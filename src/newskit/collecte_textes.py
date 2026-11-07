#!/usr/bin/env python3
"""Les textes de référence, article par article : la source de vérité d'un graphe de conformité.

Décision du porteur (07.10.2026) : « si on veut faire de ce flux une source de vérité », le graphe doit porter les
textes eux-mêmes et pas seulement leurs commentaires. Au premier banc agnostique d'un graphe de conformité, le graphe manquait
un tiers des questions de fond (l'article 25 de l'AI Act, l'article 40, le calendrier de l'article 113…) faute
d'en avoir le texte.

Chaque article, chaque annexe devient une fiche (plateforme « Référence ») : citable à l'article près, avec son
chapitre et un lien direct vers le texte officiel. Version anglaise : le corpus est surtout en anglais et ses notions
s'y nomment en anglais (« deployer », « provider »).

    python3 -m newskit.collecte_textes --graphe <graphe>            # écrit ce qui manque
    python3 -m newskit.collecte_textes --graphe <graphe> --controle # dit ce qui serait écrit

Textes déclarés ici (TEXTES), rangés sous la source du registre `<graphe>` → `cle`. Un texte déjà en fiche n'est pas
réécrit, sauf si son contenu a changé (version consolidée).
"""

from __future__ import annotations

import argparse
import html
import io
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

# Un jeu d'en-têtes de navigateur complet et cohérent, selon la méthode de l'outil d'audit de site_scraper (stealth.py) :
# le NIST refusait un agent nommé, le Conseil de l'Europe un agent seul (403, 07.10.2026) ; jamais d'agent seul en dur.
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Les textes, par graphe : une donnée de l'édition (N2), dans textes_de_reference.yaml sous la racine des données.
# `forme` : « eurlex » (HTML d'EUR-Lex, découpé par article et annexe), « pdf » (découpé par les titres de section donnés
# dans `sections`, une expression rationnelle par titre de premier niveau), « sections_html », « github_md »…
FICHIER_TEXTES = RACINE / "textes_de_reference.yaml"
TEXTES = (yaml.safe_load(FICHIER_TEXTES.read_text(encoding="utf-8")) or {}) if FICHIER_TEXTES.exists() else {}


def lire(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def texte_de(fragment: str) -> str:
    """Le texte d'un fragment HTML : paragraphes et lignes de tableau gardés, le reste aplati."""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", fragment)
    s = re.sub(r"(?i)</(p|tr|div|h\d|li|dd)>|<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(td|dt)>", " ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t ]+", " ", s)
    # Les marqueurs de modification des versions consolidées (▼M1, ▼B, ▲) ne sont pas du texte de loi.
    s = "\n".join(l.strip() for l in s.splitlines() if l.strip() and not re.fullmatch(r"[▼▲►◄][A-Z]?\d*", l.strip()))
    # Un point d'énumération seul sur sa ligne (« (a) », « 1. ») rejoint son texte.
    return re.sub(r"(?m)^(\([a-z0-9]+\)|\d+\.|[a-z]\))\n", r"\1 ", s)


def decouper_eurlex(h: str) -> list[dict]:
    """Articles et annexes d'un acte d'EUR-Lex, chacun avec son chapitre (et sa section) en sous-titre."""
    # Chaque borne commence à la balise qui porte l'identifiant, pas à l'identifiant : sinon le début de la balise
    # suivante finit dans le texte de la partie précédente.
    bornes = [
        (m.start(), m.group(1))
        for m in re.finditer(r'<[a-z]+[^<>]*\bid="((?:art_\d+|anx_[IVXLC]+|cpt_[IVXLC]+(?:\.sct_\d+)?|fnp_\d+))"', h)
    ]
    out, chapitre, section = [], "", ""
    for k, (debut, ident) in enumerate(bornes):
        fin = bornes[k + 1][0] if k + 1 < len(bornes) else len(h)
        t = texte_de(h[debut:fin])
        lignes = t.splitlines()
        if ident.startswith("cpt_"):
            titre = " — ".join(lignes[:2]) if lignes else ident
            if ".sct_" in ident:
                section = titre
            else:
                chapitre, section = titre, ""
            continue
        if ident.startswith("fnp_") or not lignes:
            continue
        if ident.startswith("art_"):
            n = int(ident[4:])
            titre = f"Article {n}" + (f": {lignes[1]}" if len(lignes) > 1 else "")
            corps = "\n\n".join(lignes[2:])
            out.append(
                {
                    "ident": ident,
                    "rang": f"art-{n:03d}",
                    "titre": titre,
                    "contexte": " · ".join(x for x in (chapitre, section) if x),
                    "corps": corps,
                }
            )
        else:
            titre = " — ".join(lignes[:2])
            out.append(
                {
                    "ident": ident,
                    "rang": f"anx-{ident[4:].lower()}",
                    "titre": titre,
                    "contexte": "Annexes",
                    "corps": "\n\n".join(lignes[2:]),
                }
            )
    return out


def decouper_fedlex(h: str) -> list[dict]:
    """Les articles d'un acte de Fedlex (un <article id="art_…"> chacun), avec leur chapitre et leur section."""
    titres = []  # (position, niveau, intitulé) des chapitres et sections
    for m in re.finditer(r'<section id="(chap_[^"/]+(?:/sec_[^"]+)?)"[^>]*>\s*<h\d[^>]*>(.*?)</h\d>', h, flags=re.S):
        titres.append((m.start(), "sec" if "/sec_" in m.group(1) else "chap", texte_de(m.group(2)).replace("\n", " ")))
    out = []
    for m in re.finditer(r'<article id="(art_[^"]+)"[^>]*>(.*?)</article>', h, flags=re.S):
        tete = re.search(r"<h6[^>]*>(.*?)</h6>", m.group(2), flags=re.S)
        titre = texte_de(tete.group(1)).replace("\n", " ") if tete else m.group(1)
        corps = texte_de(re.sub(r'(?s)<h6[^>]*>.*?</h6>|<div class="footnotes".*', "", m.group(2)))
        chap = next((x for p, n, x in reversed(titres) if p < m.start() and n == "chap"), "")
        sec = next(
            (
                x
                for p, n, x in reversed(titres)
                if p < m.start()
                and n == "sec"
                and p > max([q for q, k, _ in titres if k == "chap" and q < m.start()] or [0])
            ),
            "",
        )
        ident = m.group(1)
        n = re.match(r"art_(\d+)([a-z]*)", ident)
        rang = f"art-{int(n.group(1)):03d}{n.group(2)}" if n else ident.replace("_", "-")
        out.append(
            {
                "ident": ident,
                "rang": rang,
                "titre": titre,
                "contexte": " · ".join(x for x in (chap, sec) if x),
                "corps": "\n\n".join(corps.splitlines()),
            }
        )
    return out


def pages_depuis_index(t: dict) -> list[dict]:
    """Une fiche par page : les pages données (`pages`), ou les liens d'un index qui répondent à `motif`, lues comme la
    collecte lit un article (texte principal en markdown). Pour les livres et cours en ligne (07.10.2026 : Physically
    Based Rendering, LearnOpenGL, documentation d'Unreal Engine)."""
    import urllib.parse  # noqa: PLC0415
    from newskit import collecte_rss as cr  # noqa: PLC0415

    urls = list(t.get("pages") or [])
    if not urls:
        index = lire(t["url"]).decode("utf-8", "replace")
        for h in re.findall(r'href="([^"#?]+)"', index):
            u = urllib.parse.urljoin(t["url"], html.unescape(h))
            if re.search(t["motif"], u) and u not in urls:
                urls.append(u)
    out = []
    for k, u in enumerate(urls[: t.get("plafond", 400)]):
        n, md, mur, meta = cr.corps_de_page(u, navigateur=bool(t.get("navigateur")))
        time.sleep(0.5)
        if mur or n < 80:
            continue
        titre = (meta.get("titre") or u.rstrip("/").rsplit("/", 1)[-1]).split(" | ")[0].strip()
        out.append({"ident": u, "rang": f"p-{k + 1:03d}", "titre": titre, "contexte": "", "corps": md, "url": u})
    return out


def sections_html(h: str, niveau: str = "h3", exclure: str = "^appendix") -> list[dict]:
    """Une page longue (une spécification) découpée à chaque titre de `niveau` portant un identifiant, avec le titre
    de section supérieur pour contexte (07.10.2026 : la spécification glTF 2.0, 99 sections)."""
    marques = [
        (m.start(), m.group(1), m.group(2), m.group(3))
        for m in re.finditer(r"<(h2|" + niveau + r') id="([^"]+)"[^>]*>(.*?)</\1>', h, flags=re.S)
    ]
    out, contexte = [], ""
    for k, (debut, balise, ident, titre) in enumerate(marques):
        titre = texte_de(titre).replace("\n", " ")
        if balise == "h2":
            contexte = titre
            continue
        if re.search(exclure, ident):
            continue
        fin = marques[k + 1][0] if k + 1 < len(marques) else len(h)
        corps = texte_de(h[debut:fin])
        corps = corps.split("\n", 1)[1] if "\n" in corps else corps
        out.append({"ident": ident, "rang": f"s-{k + 1:03d}", "titre": titre, "contexte": contexte, "corps": corps})
    return out


def markdown_github(t: dict) -> list[dict]:
    """Un fichier Markdown par sous-dossier d'un dépôt GitHub (07.10.2026 : les extensions officielles de glTF,
    KhronosGroup/glTF/extensions/2.0/Khronos/<extension>/README.md)."""
    import json  # noqa: PLC0415

    liste = json.loads(lire(t["api"]).decode("utf-8"))
    out = []
    for e in liste:
        if e.get("type") != "dir":
            continue
        brut = f"https://raw.githubusercontent.com/{t['depot']}/{t.get('branche', 'main')}/{e['path']}/{t.get('fichier', 'README.md')}"
        try:
            md = lire(brut).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        titre = (re.search(r"^#\s+(.+)$", md, flags=re.M) or [None, e["name"]])[1]
        out.append(
            {
                "ident": e["name"],
                "rang": e["name"].lower(),
                "titre": titre.strip(),
                "contexte": "",
                "corps": md,
                "url": f"https://github.com/{t['depot']}/blob/{t.get('branche', 'main')}/{e['path']}",
            }
        )
    return out


def decouper_pdf_articles(donnees: bytes) -> list[dict]:
    """Un traité en PDF, découpé par article (« Article N – Titre »), avec son chapitre. Les lignes coupées par la mise en
    page sont recousues ; un renvoi en tête de ligne (« Article 3 du… ») n'ouvre pas d'article : les numéros croissent."""
    import pypdf  # noqa: PLC0415

    lignes = [
        l.strip() for p in pypdf.PdfReader(io.BytesIO(donnees)).pages for l in (p.extract_text() or "").splitlines()
    ]
    out, chapitre, courant, attendu = [], "", None, 1
    for l in lignes:
        mc = re.match(r"^Chapter\s+[IVXLC]+\s+[–-]\s+(.+)$", l)
        ma = re.match(r"^Article\s+(\d+)\s+[–-]\s+(.+)$", l)
        if mc:
            chapitre = l
            continue
        if ma and int(ma.group(1)) == attendu:
            courant = {
                "ident": f"art_{attendu}",
                "rang": f"art-{attendu:03d}",
                "titre": f"Article {attendu}: {ma.group(2)}",
                "contexte": chapitre,
                "lignes": [],
            }
            out.append(courant)
            attendu += 1
            continue
        if courant is not None and l:
            courant["lignes"].append(l)
    for a in out:
        paras, cur = [], ""
        for l in a.pop("lignes"):
            if re.match(r"^(\d+|[a-z])\s", l) and cur and cur.rstrip()[-1:] in ".:;":
                paras.append(cur)
                cur = l
            else:
                cur = f"{cur} {l}".strip()
        a["corps"] = "\n\n".join(paras + ([cur] if cur else []))
    return out


def decouper_pdf(donnees: bytes, sections: list[str]) -> list[dict]:
    import pypdf  # noqa: PLC0415

    lecteur = pypdf.PdfReader(io.BytesIO(donnees))
    lignes = [l.strip() for p in lecteur.pages for l in (p.extract_text() or "").splitlines()]
    reperes = []
    for motif in sections:
        rx = re.compile(motif)
        # La première occurrence après la table des matières : on prend la dernière qui soit suivie de texte courant.
        pos = [i for i, l in enumerate(lignes) if rx.search(l)]
        if pos:
            reperes.append((pos[-1] if len(pos) > 1 else pos[0], lignes[pos[-1] if len(pos) > 1 else pos[0]]))
    reperes.sort()
    out = []
    for k, (i, titre) in enumerate(reperes):
        fin = reperes[k + 1][0] if k + 1 < len(reperes) else len(lignes)
        corps = re.sub(r"(?<![.:;])\n(?=[a-z(])", " ", "\n".join(lignes[i + 1 : fin]))
        out.append(
            {"ident": f"sec_{k + 1}", "rang": f"sec-{k + 1:02d}", "titre": titre, "contexte": "", "corps": corps}
        )
    return out


def collecter(graphe: str, controle: bool = False) -> int:
    textes = TEXTES.get(graphe) or []
    if not textes:
        print(f"  {graphe} : aucun texte de référence déclaré")
        return 0
    sources = {s.cle: s for s in cfg.toutes()}
    aujourdhui = time.strftime("%Y-%m-%d")
    ecrites = gardees = 0
    for t in textes:
        s = sources.get(t["cle"])
        if s is None:
            print(f"  {t['cle']} : source absente du registre — rien n'est écrit", file=sys.stderr)
            return 1
        dossier = RACINE / "sources" / s.dossier / t["court"]
        donnees = b""
        if t["forme"] not in ("pages", "github_md"):
            try:
                donnees = lire(t["url"])
            except Exception as e:  # noqa: BLE001
                print(f"  {t['nom']} : lecture impossible ({str(e)[:80]})", file=sys.stderr)
                continue
        if t["forme"] == "pages":
            parties = pages_depuis_index(t)
        elif t["forme"] == "github_md":
            parties = markdown_github(t)
        elif t["forme"] == "eurlex":
            parties = decouper_eurlex(donnees.decode("utf-8", "replace"))
        elif t["forme"] == "fedlex":
            parties = decouper_fedlex(donnees.decode("utf-8", "replace"))
        elif t["forme"] == "pdf_articles":
            parties = decouper_pdf_articles(donnees)
        elif t["forme"] == "sections_html":
            parties = sections_html(donnees.decode("utf-8", "replace"))
        else:
            parties = decouper_pdf(donnees, t["sections"])
        print(f"  {t['nom']} : {len(parties)} partie(s)")
        for p in parties:
            chemin = dossier / f"{t['date']}_{t['court']}-{p['rang']}.md"
            titre = f"{t['nom']} — {p['titre']}"
            corps = f"# {titre}\n\n" + (f"*{p['contexte']}*\n\n" if p["contexte"] else "") + p["corps"] + "\n"
            if chemin.exists() and fiche.lire_fichier(chemin)[1].strip() == corps.strip():
                gardees += 1
                continue
            if controle:
                print(f"    écrirait {chemin.relative_to(RACINE)}  ({len(p['corps'].split())} mots)")
                ecrites += 1
                continue
            champs = {
                "title": titre,
                "subtitle": p["contexte"],
                "author": t["auteur"],
                "date": t["date"],
                "url": p.get("url")
                or (
                    (t.get("lien") or t["url"])
                    + (f"#{p['ident']}" if t["forme"] in ("eurlex", "fedlex", "sections_html") else "")
                ),
                # Un PDF n'a pas d'ancre par article : l'adresse est celle du document, le titre dit l'article.
                "plateforme": "Référence",
                "publication": t["publication"],
                "complet": True,
                "collected": aujourdhui,
                "source": f"texte de référence, officiel, découpé par partie ({t['court']})",
            }
            try:
                fiche.ecrire(chemin, champs, corps, strict=False)
            except fiche.IdentiteDejaVue as deja:  # une autre fiche du graphe porte déjà cette partie
                print(f"  déjà vue : {deja}")
                continue
            ecrites += 1
    print(f"  {graphe} : {ecrites} fiche(s) {'à écrire' if controle else 'écrite(s)'}, {gardees} déjà à jour")
    # La version et le statut de chaque fiche (en vigueur, remplacée) dans son en-tête, d'après les familles
    # déclarées ci-dessus (diachronie.py, 09.10.2026) ; l'en-tête seul change, rien n'est réextrait.
    from newskit import diachronie  # noqa: PLC0415

    c = diachronie.annoter_fiches(graphe, controle)
    print(
        f"  versions : {c['ecrites']} en-tête(s) {'à écrire' if controle else 'écrit(s)'}, {c[diachronie.EN_VIGUEUR]} fiche(s) "
        f"en vigueur, {c[diachronie.REMPLACEE]} remplacée(s)"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--controle", action="store_true")
    ap.add_argument(
        "--versions", action="store_true", help="veille seule : versions nouvelles en ligne (diachronie.py --versions)"
    )
    a = ap.parse_args()
    if a.versions:
        from newskit import diachronie  # noqa: PLC0415

        signaux = diachronie.veille(cfg.nom_actuel(a.graphe))
        for s in signaux:
            print(f"  À FAIRE : {s}")
        return 0
    return collecter(cfg.nom_actuel(a.graphe), a.controle)


if __name__ == "__main__":
    sys.exit(main())

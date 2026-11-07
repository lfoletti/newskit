#!/usr/bin/env python3
"""Collecte Medium par script, avec la session du compte.

Medium n'expose pas d'API de lecture, et sa protection Cloudflare refuse les
requêtes de script (403, page de défi) tant qu'elles n'apportent pas le
laissez-passer `cf_clearance` qu'un navigateur obtient en passant le défi.
Depuis le 27.09.2026, la session déposée au trousseau contient ce
laissez-passer, et `config_sources.py verifier` a reçu l'historique de lecture
en entier. Ce collecteur en fait l'usage : l'historique, filtré par mots-clés,
puis chaque article, écrit par le contrat de fiche.

Le laissez-passer expire et est lié à l'adresse publique du réseau. Quand il
ne passe plus, Medium rend de nouveau la page de défi : c'est un **refus**,
compté et rendu par un code non nul, jamais une semaine sans lecture.

Étape 1, la sonde — rien n'est écrit dans sources/ :
    python3 -m newskit.collecte_medium --sonder
Elle présente la session, rapporte ce que Medium rend (code, taille, page de
défi ou non, état Apollo présent ou non, nombre d'articles repérés) et garde
les pages reçues dans cache/medium/ (hors dépôt) pour écrire l'analyseur sur
du réel plutôt que sur une supposition.

Étape 2, la collecte :
    python3 -m newskit.collecte_medium --update            # l'historique, filtré, puis les articles
    python3 -m newskit.collecte_medium --update --tout     # sans le filtre par mots-clés

Ce que la sonde a établi le 27.09.2026 : la page d'historique embarque son
état Apollo (les 14 à 15 dernières lectures, avec un curseur de pagination) ;
un article demandé à `medium.com/p/<id>` est servi entier, même réservé aux
membres et même quand medium.com renvoie vers le domaine propre de la
publication — la redirection porte l'identité. Le texte est dans l'état Apollo
de la page d'article, paragraphe par paragraphe, typé (titre, code, liste,
citation, image), avec ses enrichissements (liens, gras, code en ligne) en
positions de caractères. C'est de là que la fiche est écrite, pas du HTML.

Le filtre par mots-clés est déclaré au registre (`mots_cles`) : l'historique
mêle la veille et les lectures d'un dimanche, et seul ce qui relève du domaine
entre au corpus. Ce qui est écarté est nommé dans la sortie, pour que le
filtre se corrige au registre plutôt que par oubli.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
CACHE = RACINE / "cache" / "medium"
HISTORIQUE = "https://medium.com/me/lists/reading-history"

from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

# Le même User-Agent que le vérificateur de session : le laissez-passer
# Cloudflare peut être lié à celui qui a passé le défi, et c'est avec
# celui-ci que l'historique a été reçu.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 12_6) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

ECHECS: list[str] = []


class Refus(Exception):
    """Medium a rendu sa page de défi ou un code d'erreur : ce n'est pas une absence."""


def get(url: str, cookie: str) -> tuple[int, str, str]:
    """(code, url finale, corps). Lève Refus sur une page de défi ou une erreur HTTP."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en", "Cookie": cookie,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            corps = r.read().decode("utf-8", "replace")
            code, finale = r.status, r.url
    except urllib.error.HTTPError as e:
        corps = e.read(20000).decode("utf-8", "replace")
        if "challenge-platform" in corps or e.code == 403:
            raise Refus(f"{url} : HTTP {e.code}, page de défi Cloudflare — le laissez-passer "
                        f"cf_clearance ne passe plus ; redéposer la session") from None
        raise Refus(f"{url} : HTTP {e.code} ({e.reason})") from None
    if "challenge-platform" in corps[:20000] and "cdn-cgi" in corps[:20000]:
        raise Refus(f"{url} : page de défi Cloudflare rendue avec un code {code}")
    if "/m/signin" in finale:
        raise Refus(f"{url} : redirigé vers la connexion — la session n'est plus reconnue")
    return code, finale, corps


def etat_apollo(html: str) -> dict | None:
    """L'état Apollo embarqué dans la page, lu par un décodeur brut.

    Le premier essai cherchait `{…}</script>` : l'objet est suivi d'autres
    instructions dans le même <script>, et une expression régulière ne sait
    pas où un JSON s'arrête. Le décodeur, si.
    """
    i = html.find("window.__APOLLO_STATE__")
    if i < 0:
        return None
    j = html.find("{", i)
    try:
        st, _ = json.JSONDecoder().raw_decode(html[j:])
        return st
    except (json.JSONDecodeError, ValueError):
        return None


def liens_d_articles(html: str) -> list[str]:
    """Les adresses d'articles Medium repérables dans une page, absolues ou
    relatives à medium.com : chemin fini par un identifiant hexadécimal."""
    vus, out = set(), []
    for h in re.findall(r'href="((?:https?://[^"/]+)?/[^"?#]+?-[0-9a-f]{8,12})(?:[?#][^"]*)?"', html):
        if h.startswith("/"):
            h = "https://medium.com" + h
        if h not in vus:
            vus.add(h); out.append(h)
    return out


def posts_de_l_historique(st: dict) -> list[dict]:
    """Les articles de l'historique, dans l'ordre de la page, avec ce qu'il faut pour décider."""
    out = []
    for k, v in st.items():
        if not k.startswith("Post:") or not isinstance(v, dict):
            continue
        cr = v.get("creator") or {}
        co = v.get("collection") or {}
        auteur = st.get(cr.get("__ref", ""), {}) if isinstance(cr, dict) else {}
        coll = st.get(co.get("__ref", ""), {}) if isinstance(co, dict) else {}
        out.append({
            "id": v.get("id") or k.split(":", 1)[1],
            "titre": v.get("title") or "",
            "url": v.get("mediumUrl") or "",
            "slug": v.get("uniqueSlug") or "",
            "publie_ms": v.get("firstPublishedAt") or 0,
            "verrouille": bool(v.get("isLocked")),
            "lecture_min": v.get("readingTime") or 0,
            "auteur": auteur.get("name") or "",
            "publication": coll.get("name") or "",
            "domaine_publication": coll.get("domain") or "",
        })
    return out


# --- De l'état Apollo d'un article à une fiche ------------------------------

def identifiant(url: str) -> str:
    """L'identifiant d'un article Medium, les 8 à 12 hexadécimaux qui finissent son chemin."""
    m = re.search(r"-([0-9a-f]{8,12})(?:[?#].*)?$", url.strip().strip('"'))
    return m.group(1) if m else ""


def _utf16(t: str) -> bytes:
    return t.encode("utf-16-le")


def appliquer_markups(texte: str, markups: list[dict]) -> str:
    """Rend en markdown les enrichissements de Medium, donnés en positions UTF-16.

    Les positions sont celles de JavaScript, en unités UTF-16 : un émoji compte
    deux. On travaille donc sur l'encodage UTF-16 et on découpe là.
    """
    if not markups:
        return texte
    u = _utf16(texte)
    n = len(u) // 2
    # Chaque markup devient une paire (ouvrant, fermant) à insérer ; on insère de
    # la fin vers le début pour ne pas décaler les positions restantes.
    insertions: list[tuple[int, int, str]] = []   # (position, priorité, texte)
    for m in markups:
        d, f = int(m.get("start") or 0), int(m.get("end") or 0)
        if not (0 <= d < f <= n):
            continue
        t = m.get("type")
        if t == "A" and m.get("href"):
            insertions.append((d, 1, "[")); insertions.append((f, 0, f"]({m['href']})"))
        elif t == "STRONG":
            insertions.append((d, 2, "**")); insertions.append((f, 0, "**"))
        elif t == "EM":
            insertions.append((d, 3, "*")); insertions.append((f, 0, "*"))
        elif t == "CODE":
            insertions.append((d, 4, "`")); insertions.append((f, 0, "`"))
    if not insertions:
        return texte
    morceaux = []
    pos = 0
    for position, _, ins in sorted(insertions, key=lambda x: (x[0], x[1])):
        morceaux.append(u[2 * pos:2 * position].decode("utf-16-le", "replace"))
        morceaux.append(ins)
        pos = position
    morceaux.append(u[2 * pos:].decode("utf-16-le", "replace"))
    rendu = "".join(morceaux)
    # Un enrichissement qui englobe l'espace finale (« **Logs: **When ») ne se
    # rend pas en markdown : la marque fermante passe avant l'espace.
    rendu = re.sub(r"(\*\*|\*|`)(\S(?:.*?\S)?)( +)\1", r"\1\2\1\3", rendu)
    return rendu


def _plat(t: str) -> str:
    """Pour comparer un titre : espaces fines et insécables ramenées à l'espace, casse ignorée."""
    return re.sub(r"\s+", " ", t.replace("\u200a", " ").replace("\u00a0", " ")).strip("# ").strip().lower()


def paragraphes_en_markdown(paragraphes: list[dict], titre: str = "", sous_titre: str = "") -> str:
    """Le corps d'un article, depuis ses paragraphes typés, dans l'ordre reçu.

    Le titre et le sous-titre ouvrent presque toujours le corps, en H3 puis
    H4 : ils sont déjà en tête de fiche, on ne les répète pas.
    """
    out: list[str] = []
    liste_prec = None
    deja = {_plat(titre), _plat(sous_titre)} - {""}
    for p in paragraphes:
        t = p.get("type") or "P"
        texte = (p.get("text") or "").replace("\u200a", " ").strip()
        if t not in ("PRE",):
            texte = appliquer_markups(texte, p.get("markups") or [])
        if t in ("OLI", "ULI"):
            if liste_prec and liste_prec != t:
                out.append("")
            out.append(("1. " if t == "OLI" else "- ") + texte)
            liste_prec = t
            continue
        if liste_prec:
            out.append(""); liste_prec = None
        if t in ("H1", "H2", "H3", "H4"):
            if _plat(texte) in deja and not [l for l in out if l and not l.startswith("#")]:
                continue                      # titre ou sous-titre, déjà en tête de fiche
            out += [("## " if t != "H4" else "### ") + texte, ""]
        elif t == "PRE":
            lang = ((p.get("codeBlockMetadata") or {}).get("lang") or "")
            out += [f"```{lang}", (p.get("text") or "").rstrip(), "```", ""]
        elif t in ("BQ", "PQ"):
            out += ["> " + texte.replace("\n", "\n> "), ""]
        elif t == "IMG":
            if texte:
                out += [f"*[image : {texte}]*", ""]
        elif t in ("IFRAME", "MIXTAPE_EMBED"):
            href = (p.get("mixtapeMetadata") or {}).get("href") or p.get("href") or ""
            if href:
                out += [f"[{texte or href}]({href})", ""]
        else:
            if texte:
                out += [texte, ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def article_depuis_etat(st: dict, post_id: str) -> dict | None:
    """Ce qu'une fiche demande, lu dans l'état Apollo d'une page d'article."""
    p = st.get(f"Post:{post_id}")
    if not isinstance(p, dict):
        return None
    contenu = next((v for k, v in p.items() if k.startswith("content(") and isinstance(v, dict)), {})
    refs = ((contenu.get("bodyModel") or {}).get("paragraphs") or [])
    paragraphes = [st.get(r.get("__ref", ""), {}) for r in refs if isinstance(r, dict)]
    cr = p.get("creator") or {}
    co = p.get("collection") or {}
    auteur = st.get(cr.get("__ref", ""), {}) if isinstance(cr, dict) else {}
    coll = st.get(co.get("__ref", ""), {}) if isinstance(co, dict) else {}
    ms = p.get("firstPublishedAt") or 0
    return {
        "id": post_id,
        "titre": (p.get("title") or "").strip(),
        "sous_titre": ((p.get("previewContent") or {}).get("subtitle") or "").strip(),
        "auteur": (auteur.get("name") or "").strip(),
        "publication": (coll.get("name") or "").strip(),
        "date": time.strftime("%Y-%m-%d", time.gmtime(ms / 1000)) if ms else "",
        "url": (p.get("canonicalUrl") or p.get("mediumUrl") or "").split("?")[0],
        "reserve": bool(p.get("isLocked")),
        "apercu_seul": bool(contenu.get("isLockedPreviewOnly")),
        "mots_annonces": int(p.get("wordCount") or 0),
        "tags": [str(t.get("__ref", "")).removeprefix("Tag:") for t in (p.get("tags") or []) if isinstance(t, dict)],
        "paragraphes": paragraphes,
    }


def retenu(post: dict, mots_cles: list[str]) -> bool:
    """Le filtre du registre, sur le titre, la publication et les étiquettes."""
    if not mots_cles:
        return True
    champ = " ".join([post.get("titre") or "", post.get("publication") or "",
                      " ".join(post.get("tags") or [])]).lower()
    for mc in mots_cles:
        mc = mc.lower().strip()
        if not mc:
            continue
        if len(mc) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(mc)}(?![a-z0-9])", champ):
                return True
        elif mc in champ:
            return True
    return False


def ecrire_fiche(src, art: dict) -> tuple[str, bool, int]:
    corps = paragraphes_en_markdown(art["paragraphes"], art["titre"], art["sous_titre"])
    mots = len(corps.split())
    complet = not art["apercu_seul"] and (art["mots_annonces"] == 0 or mots >= 0.8 * art["mots_annonces"])
    slug = re.sub(r"^https?://[^/]+/", "", art["url"]).strip("/").replace("@", "").replace("/", "__")[:90]
    chemin = src.chemin / f"{art['date'] or '0000-00-00'}_{slug}.md"
    champs = {"title": art["titre"], "subtitle": art["sous_titre"], "author": art["auteur"],
              "date": art["date"], "url": art["url"], "plateforme": "Medium",
              "publication": art["publication"], "source": "Medium (abonnement)",
              "wordcount_annonce": art["mots_annonces"], "words": mots, "complet": complet,
              "collected": time.strftime("%Y-%m-%d")}
    tete = "# " + art["titre"] + "\n\n" + (f"*{art['sous_titre']}*\n\n" if art["sous_titre"] else "")
    fiche.ecrire(chemin, champs, tete + corps)
    return chemin.name, complet, mots


def manifeste_initial(src) -> dict:
    """Les fiches Medium écrites avant ce collecteur, indexées par identifiant, pour ne pas les redemander."""
    m = {}
    for f in sorted(src.chemin.glob("*.md")):
        if f.name.startswith("_"):
            continue
        try:
            champs, _ = fiche.lire_fichier(f)
        except Exception:
            continue
        pid = identifiant(champs.get("url") or "")
        if pid:
            m[pid] = {"file": f.name, "title": champs.get("title", ""), "date": champs.get("date", ""),
                      "url": champs.get("url", ""), "complet": bool(champs.get("complet", True)),
                      "words": int(champs.get("words") or 0)}
    return m


def collecter(src, cookie: str, tout: bool) -> int:
    src.chemin.mkdir(parents=True, exist_ok=True)
    mf = src.manifeste
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else manifeste_initial(src)
    if not mf.exists():
        mf.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{src.nom}: manifeste initial, {len(manifest)} fiches existantes indexées")
    mots_cles = [] if tout else list(getattr(src, "mots_cles", []) or [])
    try:
        _, _, html = get(HISTORIQUE, cookie)
    except Refus as e:
        ECHECS.append(str(e)); print(f"  !! {e}", file=sys.stderr)
        return 1
    posts = posts_de_l_historique(etat_apollo(html) or {})
    if not posts:
        ECHECS.append("historique : page reçue mais sans article dans son état ; la page a changé de forme")
        print(f"  !! {ECHECS[-1]}", file=sys.stderr)
        return 1
    neufs = [p for p in posts if p["id"] not in manifest]
    ecartes = [p for p in neufs if not retenu(p, mots_cles)]
    a_lire = [p for p in neufs if retenu(p, mots_cles)]
    print(f"{src.nom}: {len(posts)} lectures dans l'historique, {len(neufs)} nouvelles, "
          f"{len(a_lire)} retenues, {len(ecartes)} hors mots-clés")
    for p in ecartes:
        print(f"    écarté : {p['titre'][:70]!r} — {p['publication'] or p['auteur']}")
    if neufs and len(neufs) == len(posts):
        print("  à voir : toute la première page de l'historique est nouvelle ; des lectures plus "
              "anciennes ont pu passer entre deux collectes (la page n'en montre que 15).")
    anomalies = 0
    for p in a_lire:
        time.sleep(2)
        try:
            _, finale, html = get(f"https://medium.com/p/{p['id']}", cookie)
        except Refus as e:
            ECHECS.append(str(e)); print(f"  !! {e}", file=sys.stderr)
            continue
        art = article_depuis_etat(etat_apollo(html) or {}, p["id"])
        if not art or not art["paragraphes"]:
            ECHECS.append(f"{p['url']} : page reçue ({len(html)} octets) mais sans article dans son état")
            print(f"  !! {ECHECS[-1]}", file=sys.stderr)
            continue
        if not art["titre"]:
            art["titre"] = p["titre"]
        if not art["url"]:
            art["url"] = p["url"] or finale.split("?")[0]
        try:
            nom, complet, mots = ecrire_fiche(src, art)
        except fiche.IdentiteDejaVue as deja:          # le même article, déjà venu par une autre source du graphe
            print(f"  déjà vue : {deja}")
            continue
        manifest[p["id"]] = {"file": nom, "title": art["titre"], "date": art["date"], "url": art["url"],
                             "complet": complet, "words": mots, "reserve": art["reserve"]}
        mf.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        etat = "complet" if complet else "APERÇU"
        print(f"  {etat:<8} {nom}  {mots} mots" + (f" / {art['mots_annonces']} annoncés" if art["mots_annonces"] else ""))
        if not complet:
            anomalies += 1
    if anomalies:
        print(f"  ALERTE : {anomalies} article(s) restent en aperçu malgré la session — session expirée "
              f"ou abonnement qui ne couvre pas la publication. Éprouver : config_sources.py verifier",
              file=sys.stderr)
    print(f"  -> {len(manifest)} fiches Medium au manifeste")
    return 1 if (anomalies or ECHECS) else 0


def sonder(cookie: str) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"Sonde Medium — session présentée, pages gardées dans {CACHE.relative_to(RACINE)}/")
    try:
        code, finale, html = get(HISTORIQUE, cookie)
    except Refus as e:
        print(f"  REFUS  {e}")
        return 1
    (CACHE / "historique.html").write_text(html, encoding="utf-8")
    st = etat_apollo(html) or {}
    posts = posts_de_l_historique(st)
    liens = liens_d_articles(html)
    print(f"  ok     historique : HTTP {code}, {len(html)} octets"
          + (f", redirigé vers {finale[:80]}" if finale != HISTORIQUE else ""))
    print(f"         état Apollo : {'présent, ' + str(len(st)) + ' entrées' if st else 'absent'}"
          f" ; articles dans l'état : {len(posts)} ({sum(p['verrouille'] for p in posts)} réservés) ; "
          f"liens d'articles : {len(liens)}")
    for p in posts[:4]:
        print(f"           {p['titre'][:60]!r} — {p['publication'] or p['auteur']} — "
              f"{'réservé' if p['verrouille'] else 'libre'} — {p['url'][:70]}")
    if not posts:
        print("  à voir : aucun article dans l'état de la page ; le fichier gardé le dira.")
        return 1

    # Deux articles : un réservé aux membres et un libre. Le réservé est celui
    # qui compte — c'est pour lui que la session existe — et on le demande à
    # medium.com plutôt qu'au domaine propre de sa publication, où les cookies
    # de medium.com ne sont pas envoyés.
    def sur_medium(p: dict) -> str:
        u = p["url"]
        if "://medium.com/" in u:
            return u
        return f"https://medium.com/p/{p['id']}"

    choix = []
    reserve = next((p for p in posts if p["verrouille"]), None)
    libre = next((p for p in posts if not p["verrouille"]), None)
    for etiquette, p in (("réservé", reserve), ("libre", libre)):
        if p is None:
            continue
        time.sleep(1.5)
        cible = sur_medium(p)
        try:
            code, finale, html = get(cible, cookie)
        except Refus as e:
            print(f"  REFUS  article {etiquette} : {e}")
            return 1
        nom = f"article_{etiquette}.html"
        (CACHE / nom).write_text(html, encoding="utf-8")
        sta = etat_apollo(html) or {}
        paras = [v for k, v in sta.items() if k.startswith("Paragraph:")]
        post = next((v for k, v in sta.items() if k.startswith("Post:")), {})
        mots = sum(len(str(v.get("text") or "").split()) for v in paras)
        print(f"  ok     article {etiquette} : {cible[:80]}")
        print(f"         HTTP {code}, {len(html)} octets"
              + (f", fini sur {finale[:70]}" if finale != cible else "")
              + f" ; état Apollo : {'présent' if sta else 'absent'} ; paragraphes : {len(paras)} "
              f"({mots} mots) ; annoncé : {p['lecture_min']:.0f} min ; <article> : "
              f"{'oui' if '<article' in html else 'non'} ; gardé dans {nom}")
    print("\n  Prochaine étape : l'analyseur s'écrit sur ces fichiers.")
    return 0


def main() -> int:
    src = next((s for s in cfg.toutes("Medium")), None)
    if src is None:
        print("  aucune source Medium au registre", file=sys.stderr)
        return 1
    if not src.session:
        print("  la source Medium ne déclare pas de session", file=sys.stderr)
        return 1
    etat = cfg.etat_session(src.session)
    if etat != "deposee":
        print(f"  session « {src.session} » : {etat} — rien ne peut être collecté", file=sys.stderr)
        return 1
    cookie = cfg.session(src.session) or ""
    if "--sonder" in sys.argv:
        return sonder(cookie)
    if "--update" in sys.argv:
        code = collecter(src, cookie, tout="--tout" in sys.argv)
        if ECHECS:
            print(f"  ALERTE : {len(ECHECS)} requête(s) n'ont pas abouti ; ce ne sont pas des lectures "
                  f"absentes.", file=sys.stderr)
            for e in ECHECS[:5]:
                print(f"    {e}", file=sys.stderr)
        return code
    print("  usage : --sonder | --update [--tout]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Collecte d'un dossier local : des documents Markdown découpés par titre en fiches.

Pourquoi. Le graphe financier (27.09.2026) porte la base scientifique de
dollar_fountain : cent études et leurs verdicts, la bibliographie, le protocole,
les décisions. Ces documents vivent dans un autre dépôt, sous une forme qui
n'est pas une fiche — RECHERCHE.md fait 390 Ko. Ce collecteur les découpe sur
leurs titres, une fiche par section, chacune avec sa provenance exacte
(fichier, lignes), et les relit à chaque passage : une section qui change est
réécrite, une section disparue retire sa fiche, pour que le contrôle de
fraîcheur reste vrai.

Une fiche par section plutôt qu'une par document : c'est la granularité à
laquelle la couche 2 extrait des concepts sans dépasser un appel, et celle à
laquelle on cherche à retrouver quelque chose (« Étude 58 », « Volet E »).

Registre (sources.yaml), plateforme « Dossier local » :
    racine     chemin absolu du dossier source
    fichiers   liste de « chemin » ou « chemin:niveau », relatifs à la racine ;
               le niveau est celui des titres qui bornent une fiche (défaut 2)
    niveau     niveau par défaut pour la liste

Une section trop longue (plus de 30 000 caractères) est redécoupée sur le
niveau de titre suivant, puis sur des paragraphes, avec un suffixe (1/3).

Usage :
    python3 -m newskit.collecte_dossier_local [--source <cle>] [--graphe <nom>] [--controle]
Sortie : sources/<dossier>/<fichier>__<section>.md + _manifest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

TAILLE_MAX = 30_000       # caractères par fiche avant redécoupage
TAILLE_MORCEAU = 22_000   # taille visée d'un morceau sans titre
RE_TITRE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
RE_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


def slug(t: str, n: int = 70) -> str:
    """Nom de fichier : accents translittérés (« décorrélation » → decorrelation), pas effacés."""
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t[:n].rstrip("-") or "section"


def sections(lignes: list[str], niveau: int) -> list[tuple[str, int, int]]:
    """[(titre, début, fin)] : les sections bornées par les titres de niveau ≤ niveau.

    Le titre de niveau 1 est celui du document : ce qui le suit avant le premier
    titre de section devient la section « Préambule ».
    """
    bornes: list[tuple[str, int]] = []
    for i, l in enumerate(lignes):
        m = RE_TITRE.match(l)
        if m and 2 <= len(m.group(1)) <= niveau:
            bornes.append((m.group(2), i))
    if not bornes:
        return [("", 0, len(lignes))]
    out: list[tuple[str, int, int]] = []
    if bornes[0][1] > 0:
        out.append(("Préambule", 0, bornes[0][1]))
    for k, (t, i) in enumerate(bornes):
        fin = bornes[k + 1][1] if k + 1 < len(bornes) else len(lignes)
        out.append((t, i, fin))
    return out


def redecouper(lignes: list[str], titre: str, debut: int, fin: int, niveau: int) -> list[tuple[str, int, int]]:
    """Une section trop longue : sur le niveau suivant, puis par paragraphes."""
    taille = sum(len(l) + 1 for l in lignes[debut:fin])
    if taille <= TAILLE_MAX:
        return [(titre, debut, fin)]
    if niveau < 6:
        sous = [(t, a, b) for t, a, b in sections(lignes[debut:fin], niveau + 1)]
        if len(sous) > 1:
            out = []
            for t, a, b in sous:
                t2 = titre if t == "Préambule" else f"{titre} — {t}"
                out += redecouper(lignes, t2, debut + a, debut + b, niveau + 1)
            return out
    # Pas de sous-titre : des morceaux de paragraphes, bornés sur une ligne vide.
    morceaux, a, taille = [], debut, 0
    for i in range(debut, fin):
        taille += len(lignes[i]) + 1
        if taille >= TAILLE_MORCEAU and not lignes[i].strip():
            morceaux.append((a, i)); a, taille = i, 0
    morceaux.append((a, fin))
    n = len(morceaux)
    return [(f"{titre} ({k + 1}/{n})" if n > 1 else titre, a, b) for k, (a, b) in enumerate(morceaux)]


def date_de(texte: str, repli: str) -> str:
    """La date la plus récente écrite JJ.MM.AAAA dans la section, sinon le repli."""
    dates = []
    aujourdhui = dt.date.today()
    for jj, mm, aaaa in RE_DATE.findall(texte):
        try:
            d = dt.date(int(aaaa), int(mm), int(jj))
        except ValueError:
            continue
        if d <= aujourdhui:                 # une échéance à venir n'est pas la date du texte
            dates.append(d)
    return max(dates).isoformat() if dates else repli


def traiter_source(src, controle: bool, echecs: list[str]) -> dict:
    compte = {"documents": 0, "sections": 0, "neuves": 0, "modifiees": 0, "retirees": 0, "inchangees": 0}
    racine = Path(src.racine).expanduser()          # « ~/… » au registre
    src.chemin.mkdir(parents=True, exist_ok=True)
    manifeste = json.loads(src.manifeste.read_text(encoding="utf-8")) if src.manifeste.exists() else {}
    vus: set[str] = set()
    niveau_defaut = int(getattr(src, "niveau", 2) or 2)
    aujourdhui = time.strftime("%Y-%m-%d")
    for spec in src.fichiers:
        chemin_rel, _, niv = str(spec).partition(":")
        niveau = int(niv) if niv else niveau_defaut
        f = racine / chemin_rel
        if not f.exists():
            echecs.append(f"{src.nom} : {chemin_rel} introuvable")
            continue
        compte["documents"] += 1
        texte = f.read_text(encoding="utf-8", errors="replace")
        lignes = texte.split("\n")
        titre_doc = next((RE_TITRE.match(l).group(2) for l in lignes if RE_TITRE.match(l) and RE_TITRE.match(l).group(1) == "#"), f.stem)
        mtime = dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
        parts: list[tuple[str, int, int]] = []
        for t, a, b in sections(lignes, niveau):
            parts += redecouper(lignes, t or titre_doc, a, b, niveau)
        noms_vus: dict[str, int] = {}
        for t, a, b in parts:
            corps_lignes = [l for l in lignes[a:b]]
            # le titre de la section est repris en tête de fiche, pas en double
            if corps_lignes and RE_TITRE.match(corps_lignes[0]):
                corps_lignes = corps_lignes[1:]
            corps = "\n".join(corps_lignes).strip("\n")
            if len(corps.split()) < 8:
                continue
            compte["sections"] += 1
            base = f"{slug(f.stem, 30)}__{slug(t)}"
            noms_vus[base] = noms_vus.get(base, 0) + 1
            nom = base + (f"-{noms_vus[base]}" if noms_vus[base] > 1 else "") + ".md"
            vus.add(nom)
            empreinte = hashlib.sha256(corps.encode("utf-8")).hexdigest()[:16]
            ancien = manifeste.get(nom)
            titre = f"{titre_doc} — {t}" if t and t != titre_doc else titre_doc
            date_sec = date_de(corps, mtime)
            if (ancien and ancien.get("empreinte") == empreinte and ancien.get("date") == date_sec
                    and (src.chemin / nom).exists()):
                compte["inchangees"] += 1
                continue
            champs = {
                "title": titre[:200], "subtitle": "", "author": src.nom, "date": date_sec,
                "url": "", "plateforme": "Dossier local", "publication": src.nom, "complet": True,
                "collected": ancien.get("collected", aujourdhui) if ancien else aujourdhui,
                "source": f"dossier local {chemin_rel}", "fichier": chemin_rel,
                "lignes": f"L{a + 1}-L{b}", "section": t[:160],
            }
            if not controle:
                try:
                    fiche.ecrire(src.chemin / nom, champs, f"# {titre}\n\n{corps}\n")
                except fiche.IdentiteDejaVue as deja:   # une autre fiche du graphe porte déjà ce document
                    print(f"  déjà vue : {deja}")
                    continue
                except ValueError as e:
                    echecs.append(f"{src.nom} : {nom} : {str(e)[:100]}")
                    continue
            manifeste[nom] = {"file": nom, "title": titre, "date": champs["date"], "empreinte": empreinte,
                              "fichier": chemin_rel, "lignes": champs["lignes"], "collected": champs["collected"]}
            compte["modifiees" if ancien else "neuves"] += 1
    # Les sections disparues retirent leur fiche : le graphe ne doit pas garder
    # un nœud sans texte derrière lui.
    for nom in sorted(set(manifeste) - vus):
        compte["retirees"] += 1
        if not controle:
            (src.chemin / nom).unlink(missing_ok=True)
            del manifeste[nom]
    if not controle:
        src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
    return compte


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    ap.add_argument("--controle", action="store_true", help="compte, n'écrit rien")
    args = ap.parse_args()
    sources = [s for s in cfg.toutes("Dossier local", graphe=args.graphe) if not args.source or s.cle == args.source]
    if not sources:
        print("  aucune source « Dossier local » au registre, rien à collecter.")
        return 0
    echecs: list[str] = []
    for src in sources:
        c = traiter_source(src, args.controle, echecs)
        print(f"  {src.nom} : {c['documents']} document(s), {c['sections']} section(s) → "
              f"{c['neuves']} fiche(s) neuve(s), {c['modifiees']} réécrite(s), {c['inchangees']} inchangée(s), "
              f"{c['retirees']} retirée(s)" + ("  (contrôle, rien d'écrit)" if args.controle else ""))
    for e in echecs:
        print(f"  !! {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

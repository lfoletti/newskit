#!/usr/bin/env python3
"""Les désignations d'un article de loi, versées au graphe avec son texte (mainteneur, 06.11.2026 : « je n'aimerais pas
que le site fonctionne mieux que le mcp » ; l'amélioration va au graphe, et sert le site comme le MCP).

Les textes de loi sont en anglais (collecte_textes.py) ; une question en français (« Quelles amendes l'AI Act
prévoit-il ? ») ne trouvait pas l'article 99. Chaque partie reçoit, en tête du texte versé, une ligne faite sans
lexique écrit à la main :
- ses formes usuelles (« article 99 de l'AI Act », « art. 99 RIA », « article 99 du règlement (UE) 2024/1689 ») ;
- son titre officiel français (version française de l'acte, CELLAR de l'Office des publications : EUR-Lex répond aux
  robots par un défi AWS) ;
- les termes définis à l'article 3 qu'elle emploie, en français et en anglais, s'ils sont rares (un terme employé
  partout, « système d'IA », ne désigne rien) ;
- ses mots propres : les mots de son texte français les plus rares ailleurs dans l'acte.
La fiche elle-même ne change pas ; la ligne vit dans `_designations.json`, à côté des fiches, et puits_cortexdb la
place en tête du texte versé.

    python3 -m newskit.designations --court aiact [--controle]
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

from newskit.commun import RACINE  # noqa: E402

FICHIER = "_designations.json"
RARE = 8                 # un terme défini employé par plus de RARE parties ne désigne rien
MOTS = 8                 # mots propres par partie
CELLAR = "http://publications.europa.eu/resource/celex/{}"

# court : (nom usuel, sigles, acte). Les seuls mots écrits à la main : comment on appelle la loi.
NOMS = {
    "aiact": ("AI Act", ["RIA", "AIA"], "règlement (UE) 2024/1689"),
    "aiact-2024": ("AI Act", ["RIA", "AIA"], "règlement (UE) 2024/1689"),
}
VIDES = set("""aux avec cette ceux celle celles dans des elle elles entre être leur leurs lors même mêmes notamment
ou par parmi pour qui que quel quelle quels quelles sans selon son ses sont sous sur tout tous toute toutes une
afin ainsi alors article articles autre autres avant chaque comme compte conformément dont été font lorsque paragraphe
paragraphes point points présent présente peut peuvent premier première règlement visé visés visée visées cas date
fins mesure mesures prévu prévus prévue prévues application applicable compris doivent ledit ladite lesdits lesdites
concerné concernés concernée concernées égard ensemble également toutefois plus moins jusqu partir""".split())
DEF_FR = re.compile(r"(\d+)\)\s*«\s*([^»]+?)\s*»")
DEF_EN = re.compile(r"\((\d+)\)\s*‘([^’]+)’")
MOT = re.compile(r"[a-zà-ÿœæ]{5,}")


def _rang_lisible(rang: str) -> tuple[str, str]:
    genre, n = rang.split("-", 1)
    return genre, (str(int(n)) if genre == "art" else n.upper())


def _titre_fr(partie: dict) -> str:
    t = partie.get("titre", "")
    return t.split(": ", 1)[1] if ": " in t else t.split(" — ", 1)[-1]


def _mots(texte: str) -> list[str]:
    return [m for m in MOT.findall(texte.lower().replace("’", " ").replace("'", " ")) if m not in VIDES]


def construire(court: str, html_fr: str, en: dict[str, str], rare: int = RARE, mots: int = MOTS) -> dict[str, str]:
    """{rang : ligne de désignations}. `en` : le texte anglais de chaque partie, par rang (« art-099 »)."""
    nom, sigles, acte = NOMS[court]
    from newskit import collecte_textes  # noqa: PLC0415
    fr = {p["rang"]: p for p in collecte_textes.decouper_eurlex(html_fr)}
    definis = {}
    termes_fr = dict(DEF_FR.findall(fr.get("art-003", {}).get("corps", "")))
    for n, terme_en in DEF_EN.findall(en.get("art-003", "")):
        if n in termes_fr:
            definis[n] = (termes_fr[n], terme_en)
    emplois = {n: [r for r, t in en.items() if r != "art-003" and re.search(rf"\b{re.escape(e)}s?\b", t, re.I)]
               for n, (_, e) in definis.items()}
    sacs = {r: Counter(_mots(p.get("corps", ""))) for r, p in fr.items()}
    df = Counter(m for s in sacs.values() for m in s)
    sortie = {}
    for rang in sorted(set(en) | set(fr)):
        genre, n = _rang_lisible(rang)
        if genre == "art":
            formes = [f"article {n} de l'{nom}", *(f"art. {n} {s}" for s in sigles), f"{nom} Art. {n}",
                      f"article {n} du {acte}"]
        else:
            formes = [f"annexe {n} de l'{nom}", f"{nom} Annex {n}", f"annexe {n} du {acte}"]
        parts = [f"Désignations : {', '.join(formes)}."]
        if rang in fr:
            parts.append(f"Titre français : {_titre_fr(fr[rang])}.")
        termes = [f"{f} ({e})" for k, (f, e) in sorted(definis.items(), key=lambda x: int(x[0]))
                  if rang in emplois[k] and len(emplois[k]) <= rare]
        if termes:
            parts.append(f"Termes définis : {', '.join(termes)}.")
        if sacs.get(rang):
            score = {m: c * math.log(len(sacs) / df[m]) for m, c in sacs[rang].items()}
            propres = [m for m, s in sorted(score.items(), key=lambda x: (-x[1], x[0])) if s > 0][:mots]
            if propres:
                parts.append(f"Mots propres : {', '.join(propres)}.")
        sortie[rang] = " ".join(parts)
    return sortie


@functools.lru_cache(maxsize=64)
def lire(dossier: str) -> dict:
    try:
        return json.loads((RACINE / dossier / FICHIER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


RANG = re.compile(r"-((?:art|anx)-[a-z0-9]+)\.md$")


def ligne(chemin: str) -> str:
    """La ligne à placer en tête du texte versé d'une fiche (chemin relatif au dépôt), ou rien."""
    m = RANG.search(chemin)
    if not m or "/textes/" not in chemin:
        return ""
    l = lire(str(Path(chemin).parent)).get(m.group(1))
    return f"{l}\n\n" if l else ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--court", required=True, choices=sorted(NOMS))
    p.add_argument("--controle", action="store_true", help="dit ce qui serait écrit, n'écrit rien")
    a = p.parse_args(argv)
    from newskit import collecte_textes  # noqa: PLC0415
    from newskit import fiche  # noqa: PLC0415
    decl = next(t for liste in collecte_textes.TEXTES.values() for t in liste if t.get("court") == a.court)
    celex = "3" + re.search(r"CELEX:[03](\d{4}R\d{4})", decl["url"]).group(1)
    req = urllib.request.Request(CELLAR.format(celex), headers={**collecte_textes.UA, "Accept-Language": "fra",
                                                                 "Accept": "application/xhtml+xml, text/html;q=0.8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        html_fr = r.read().decode("utf-8", "replace")
    (dossier,) = {f.parent for f in RACINE.glob(f"sources/*/textes/{a.court}/*.md")}
    en = {}
    for f in sorted(dossier.glob("*.md")):
        m = RANG.search(f.name)
        if m:
            en[m.group(1)] = fiche.lire_fichier(f)[1]
    d = construire(a.court, html_fr, en)
    print(f"{a.court} : {len(d)} parties, {sum('Titre français' in x for x in d.values())} avec leur titre français")
    if not a.controle:
        (dossier / FICHIER).write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"écrit : {(dossier / FICHIER).relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

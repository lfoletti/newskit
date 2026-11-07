#!/usr/bin/env python3
"""Déplacer des sources d'un graphe vers un autre, avec ce qui a déjà été extrait de leurs fiches.

Écrit pour le partage d'un graphe d'actualité (porteur, 07.10.2026 : « trop généraliste », ses sujets vont à un graphe de trading quantitatif, un graphe d'ingénierie
et un graphe de conformité), mais générique. Pour chaque source nommée :

    1. son champ `graphe` change au registre (sources.yaml) ;
    2. les lots de couche 2 de ses fiches (graphe/<de>/couche2/fiches/<identifiant>.json) sont recopiés dans le graphe
       d'accueil, avec leurs empreintes : rien n'est réextrait. Un lot extrait sous le domaine d'un autre graphe reste
       valable — l'empreinte suit le texte de la fiche, pas le domaine ;
    3. rien n'est effacé dans le graphe de départ : il garde ses lots, son historique, et ce qu'on ne déplace pas.

La couche 1, le contrôle de fraîcheur, la composition et la pyramide du graphe d'accueil se refont à sa routine suivante
(ou à la main : newskit.routine_hebdo --graphe <vers> --sans-collecte).

    python3 -m newskit.deplacer_sources --de <graphe> --vers <graphe> --sources <cle1>,<cle2>            # à blanc
    python3 -m newskit.deplacer_sources --de <graphe> --vers <graphe> --sources <cle1>,<cle2> --appliquer
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import extraire_concepts as ec  # noqa: E402
from newskit import fiche  # noqa: E402


def bloc_de(texte: str, cle: str) -> tuple[int, int]:
    """Début et fin du bloc d'une source au registre (de « - cle: x » à la source suivante ou à la clé de tête suivante)."""
    m = re.search(rf"^  - cle: {re.escape(cle)}[ \t]*$", texte, flags=re.M)
    if not m:
        raise SystemExit(f"source introuvable au registre : {cle}")
    suite = re.search(r"^(  - cle: |\S)", texte[m.end():], flags=re.M)
    return m.start(), (m.end() + suite.start()) if suite else len(texte)


def deplacer(de: str, vers: str, cles: list[str], appliquer: bool) -> int:
    sources = {s.cle: s for s in cfg.toutes()}
    c_de, c_vers = cfg.chemins_du_graphe(de)["couche2"], cfg.chemins_du_graphe(vers)["couche2"]
    emp_de = json.loads((c_de / "empreintes.json").read_text(encoding="utf-8")) if (c_de / "empreintes.json").exists() else {}
    emp_vers = json.loads((c_vers / "empreintes.json").read_text(encoding="utf-8")) if (c_vers / "empreintes.json").exists() else {}
    registre = cfg.FICHIER.read_text(encoding="utf-8")
    total_f = total_l = 0
    for cle in cles:
        s = sources.get(cle)
        if s is None:
            raise SystemExit(f"source inconnue : {cle}")
        if s.graphe != de:
            print(f"  {cle} : rattachée à {s.graphe}, pas à {de} — ignorée")
            continue
        dossier = RACINE / s.dossier if not str(s.dossier).startswith("sources") else RACINE / s.dossier
        if not dossier.exists():
            dossier = RACINE / "sources" / s.dossier
        fiches = [f for f in dossier.rglob("*.md") if not f.name.startswith("_")] if dossier.exists() else []
        lots = 0
        for f in fiches:
            champs, _ = fiche.lire_fichier(f)
            ident = ec.identifiant_de_fiche(f, champs)
            src, dst = c_de / "fiches" / f"{ident}.json", c_vers / "fiches" / f"{ident}.json"
            rel = str(f.relative_to(RACINE))
            if src.exists():
                lots += 1
                if appliquer and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                if rel in emp_de:
                    emp_vers.setdefault(rel, emp_de[rel])
        debut, fin = bloc_de(registre, cle)
        bloc = registre[debut:fin]
        neuf, n = re.subn(rf"^    graphe: {re.escape(de)}[ \t]*$", f"    graphe: {vers}", bloc, flags=re.M)
        if n != 1:
            raise SystemExit(f"{cle} : champ « graphe: {de} » introuvable dans son bloc")
        registre = registre[:debut] + neuf + registre[fin:]
        print(f"  {cle:30} {len(fiches):5} fiche(s), {lots:5} lot(s) de couche 2 recopiés → {vers}")
        total_f, total_l = total_f + len(fiches), total_l + lots
    print(f"  {de} → {vers} : {len(cles)} source(s), {total_f} fiche(s), {total_l} lot(s) de couche 2"
          + ("" if appliquer else " — à blanc, rien n'est écrit (--appliquer)"))
    if appliquer:
        cfg.FICHIER.write_text(registre, encoding="utf-8")
        c_vers.mkdir(parents=True, exist_ok=True)
        (c_vers / "empreintes.json").write_text(json.dumps(emp_vers, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        cfg.charger.cache_clear() if hasattr(cfg.charger, "cache_clear") else None
        cfg.toutes()                                       # le registre modifié doit rester valide
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--de", required=True)
    ap.add_argument("--vers", required=True)
    ap.add_argument("--sources", required=True, help="clés des sources, séparées par des virgules")
    ap.add_argument("--appliquer", action="store_true")
    a = ap.parse_args()
    return deplacer(cfg.nom_actuel(a.de), cfg.nom_actuel(a.vers), [c.strip() for c in a.sources.split(",") if c.strip()],
                    a.appliquer)


if __name__ == "__main__":
    sys.exit(main())

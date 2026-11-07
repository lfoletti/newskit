#!/usr/bin/env python3
"""Vérifie le corpus contre le contrat de fiche défini dans `fiche.py`.

Ce script n'a pas de schéma à lui : il applique celui que les importateurs
utilisent pour écrire. Il ne peut donc pas diverger d'eux.

À lancer après toute collecte, et avant toute construction du graphe.

Usage :
    python3 -m newskit.valider_fiches            # résumé
    python3 -m newskit.valider_fiches --detail   # liste chaque manquement
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from newskit import fiche  # noqa: E402

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
DOSSIERS = ["sources", "recherche"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detail", action="store_true", help="liste chaque manquement")
    args = ap.parse_args()

    chemins = sorted(
        c for d in DOSSIERS for c in (RACINE / d).rglob("*.md") if not c.name.startswith("_")
    )
    total_err = total_avert = 0
    par_type: Counter = Counter()

    for chemin in chemins:
        champs, corps = fiche.lire_fichier(chemin)
        rel = chemin.relative_to(RACINE)
        if not champs:
            total_err += 1
            par_type["frontmatter absent (erreur)"] += 1
            if args.detail:
                print(f"  ERREUR {rel} : frontmatter absent")
            continue
        erreurs, avertissements = fiche.valider(champs)
        e2, a2 = fiche.verifier_corps(corps, champs)
        erreurs += e2; avertissements += a2
        total_err += len(erreurs)
        total_avert += len(avertissements)
        for e in erreurs:
            par_type[f"{e.split(' : ')[0]} (erreur)"] += 1
        for a in avertissements:
            par_type[f"{a.split(' : ')[0]} (avert.)"] += 1
        if args.detail:
            for e in erreurs:
                print(f"  ERREUR {rel} : {e}")
            for a in avertissements:
                print(f"  avert. {rel} : {a}")

    print(f"\n{len(chemins)} fiches vérifiées contre le contrat de fiche.py")
    print(f"  erreurs        : {total_err}")
    print(f"  avertissements : {total_avert}")
    if par_type:
        print("\n  répartition :")
        for cle, n in par_type.most_common():
            print(f"    {cle:<36} {n:>5}")
    if total_err:
        print("\n  Corpus non conforme. Lancer : python3 -m newskit.migrer_frontmatter")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())

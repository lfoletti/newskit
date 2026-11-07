#!/usr/bin/env python3
"""Met le corpus existant en conformité avec le contrat de `fiche.py`.

Migration additive : aucun champ existant n'est écrasé ni supprimé, seuls les
manques sont comblés. Concrètement, elle ajoute `plateforme` partout, complète
`publication` là où la source ne la donnait pas (les 50 fiches Medium), calcule
`words` pour LinkedIn et pose `complet` hors Substack.

Elle applique exactement la fonction de normalisation que les importateurs
utilisent, `fiche.normaliser`. Il n'existe donc pas deux définitions du schéma
susceptibles de diverger : c'est tout l'intérêt d'avoir sorti le contrat dans un
module séparé.

Idempotente : la relancer sur un corpus déjà migré ne change rien.

Usage :
    python3 -m newskit.migrer_frontmatter --essai   # montre ce qui changerait
    python3 -m newskit.migrer_frontmatter           # applique
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
    ap.add_argument("--essai", action="store_true", help="montre les changements sans écrire")
    args = ap.parse_args()

    chemins = sorted(
        c for d in DOSSIERS for c in (RACINE / d).rglob("*.md") if not c.name.startswith("_")
    )
    ajouts: Counter = Counter()
    modifiees = 0
    bloquantes: list[str] = []

    for chemin in chemins:
        champs, corps = fiche.lire_fichier(chemin)
        if not champs:
            bloquantes.append(f"{chemin.relative_to(RACINE)} : frontmatter absent")
            continue
        normalises = fiche.normaliser(champs, corps)
        diff = {k: v for k, v in normalises.items() if champs.get(k) != v}
        if not diff:
            continue
        modifiees += 1
        for cle in diff:
            ajouts[cle] += 1
        erreurs, _ = fiche.valider(normalises)
        if erreurs:
            bloquantes.append(f"{chemin.relative_to(RACINE)} : " + " ; ".join(erreurs))
            continue
        if not args.essai:
            chemin.write_text(fiche.rendre(normalises, corps), encoding="utf-8")

    verbe = "seraient modifiées" if args.essai else "modifiées"
    print(f"\n{len(chemins)} fiches examinées, {modifiees} {verbe}")
    if ajouts:
        print("\n  champs complétés :")
        for cle, n in ajouts.most_common():
            print(f"    {cle:<20} {n:>5}")
    if bloquantes:
        print(f"\n  {len(bloquantes)} fiche(s) restent non conformes :")
        for b in bloquantes[:12]:
            print(f"    - {b}")
        if len(bloquantes) > 12:
            print(f"    … et {len(bloquantes) - 12} de plus")
    return 0


if __name__ == "__main__":
    sys.exit(main())

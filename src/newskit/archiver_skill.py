#!/usr/bin/env python3
"""Archive le skill /kg dans le dépôt, à l'identique de l'installation.

Le skill vit dans `~/.claude/skills/kg/`, et c'est là sa place : son registre
`graphes.json` est global par nature, il accueillera d'autres graphes que celui
de ce dépôt. Mais rien ne le versionnait, et son dossier tombe dans un dépôt git
dont la racine est le répertoire personnel — un `git init` fait par accident,
qui ne suit que les 109 fichiers d'un projet sans rapport. Y committer le skill
reviendrait à s'en servir comme d'un dépôt de configuration.

D'où ce script : l'installation reste la source, le dépôt en garde l'archive, et
la copie est refaite par la routine hebdomadaire. Une archive qu'on copierait à
la main divergerait en silence, ce qui est précisément le défaut que ce projet
corrige partout ailleurs.

Le sens de la copie est donc **installation vers dépôt**, jamais l'inverse : le
dépôt n'écrase pas ce qui est installé.

Usage :
    python3 -m newskit.archiver_skill              # recopie, dit ce qui a changé
    python3 -m newskit.archiver_skill --controle    # dit seulement, n'écrit rien
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
INSTALLE = Path(os.environ.get("SE_SKILL_KG", Path.home() / ".claude" / "skills" / "kg"))
ARCHIVE = RACINE / "skill" / "kg"     # sous le dossier du média, pas sous le code (07.11.2026)


def contenu(f: Path) -> bytes:
    """Le fichier tel qu'archivé : la maison s'écrit « ~ » (24.10.2026, aucun chemin de la machine dans le dépôt)."""
    b = f.read_bytes()
    try:
        return b.decode("utf-8").replace(str(Path.home()), "~").encode("utf-8")
    except UnicodeDecodeError:
        return b


def main() -> int:
    controle = "--controle" in sys.argv
    if not INSTALLE.is_dir():
        print(f"  skill absent de {INSTALLE} : rien à archiver.")
        return 0

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    vivants = sorted(f for f in INSTALLE.iterdir()
                     if f.is_file() and not f.name.startswith("."))
    changes, retires = [], []

    for f in vivants:
        cible = ARCHIVE / f.name
        if not cible.exists() or cible.read_bytes() != contenu(f):
            changes.append(f.name)
            if not controle:
                cible.write_bytes(contenu(f))
                shutil.copystat(f, cible)

    noms = {f.name for f in vivants}
    for f in sorted(ARCHIVE.iterdir()):
        if f.is_file() and f.name not in noms:
            retires.append(f.name)
            if not controle:
                f.unlink()

    if not changes and not retires:
        print(f"  skill /kg : archive conforme à {INSTALLE} ({len(vivants)} fichiers).")
        return 0
    verbe = "à recopier" if controle else "recopié(s)"
    if changes:
        print(f"  skill /kg : {len(changes)} fichier(s) {verbe} — {', '.join(changes)}")
    if retires:
        print(f"  skill /kg : {len(retires)} fichier(s) disparu(s) de l'installation "
              f"{'à retirer' if controle else 'retiré(s)'} — {', '.join(retires)}")
    if controle:
        print("  (--controle : rien n'a été écrit ; lancer sans l'option pour archiver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

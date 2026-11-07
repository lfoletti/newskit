#!/usr/bin/env python3
"""Les anciens noms quittent nginx (mainteneur, 06.11.2026 : « on va trainer cette casserole encore longtemps derrière
nous? », « on s'en fout qu'ils cassent ou pas. ils n'ont pas été diffusés »).

Un site qui ne sert que des noms retirés disparaît, avec son lien dans sites-enabled. Un site qui sert aussi un nom
gardé perd les noms retirés de ses lignes server_name et leurs redirections « if ($host = …) » posées par certbot.
Chaque fichier touché est d'abord copié dans --copie. installer_vps.sh lance ensuite nginx -t et remet les copies si
nginx refuse.

    python3 -m newskit.retirer_domaines --dossier /etc/nginx/sites-available --actifs /etc/nginx/sites-enabled \\
        --copie /root/nginx-avant-retrait --noms <nom>,<nom>
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

LIGNE = re.compile(r"^(\s*server_name\s+)([^;]*);", re.M)


def retirer(texte: str, noms: list[str]) -> str | None:
    """Le texte du site sans les noms retirés ; None si le site ne sert plus aucun nom."""
    retires = set(noms)
    gardes = {n for m in LIGNE.finditer(texte) for n in m.group(2).split() if n not in retires}
    tous = {n for m in LIGNE.finditer(texte) for n in m.group(2).split()}
    if not tous & retires:
        return texte
    if not gardes:
        return None
    texte = LIGNE.sub(lambda m: m.group(1) + " ".join(n for n in m.group(2).split() if n not in retires) + ";", texte)
    for n in retires:
        texte = re.sub(rf"^[ \t]*if \(\$host = {re.escape(n)}\) \{{\n(?:.*\n)*?[ \t]*\}}[^\n]*\n", "", texte, flags=re.M)
    return texte


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier", type=Path, required=True)
    ap.add_argument("--actifs", type=Path, required=True)
    ap.add_argument("--copie", type=Path, required=True)
    ap.add_argument("--noms", required=True)
    a = ap.parse_args(argv)
    noms = [n.strip() for n in a.noms.split(",") if n.strip()]
    for site in sorted(p for p in a.dossier.iterdir() if p.is_file()):
        avant = site.read_text(encoding="utf-8")
        apres = retirer(avant, noms)
        if apres == avant:
            continue
        a.copie.mkdir(parents=True, exist_ok=True)
        shutil.copy2(site, a.copie / site.name)
        if apres is None:
            for lien in a.actifs.iterdir():
                if lien.is_symlink() and lien.resolve() == site.resolve():
                    lien.unlink()
            site.unlink()
            print(f"site retiré : {site.name}")
        else:
            site.write_text(apres, encoding="utf-8")
            print(f"noms retirés de : {site.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Le contrôle de la production contre les versions attendues (chantier A5 du projet d'alignement, 25.10.2026).

`deploiement/versions.yaml` est la seule source de ce qui doit tourner. Ce script lit ce que la production dit
d'elle-même (GET /v1/version du gateway Thalamus ; `cortexdb-grpc -version` sur le serveur, par ssh) et compare.
Sortie 0 si tout concorde, 1 au moindre écart, imprimé ligne par ligne. Aucun geste : il lit seulement.

    python3 -m newskit.controle_production [--sans-ssh]
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
VERSIONS = RACINE / "deploiement" / "versions.yaml"
COMPOSANTS = ("th-gateway", "cortexdb-grpc")


def attendues(chemin: Path = VERSIONS) -> dict:
    """Le fichier des versions, vérifié : chaque composant a sa version."""
    d = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    for nom in COMPOSANTS:
        if not (d.get(nom) or {}).get("version"):
            raise ValueError(f"{chemin.name} : {nom}.version manque")
    return d


def ecarts(attendu: dict, obtenu: dict[str, str]) -> list[str]:
    """Pur : une ligne par composant dont la version en production n'est pas celle attendue."""
    return [f"{nom} : attendu {v['version']}, en production {obtenu.get(nom) or 'non lu'}"
            for nom, v in attendu.items() if obtenu.get(nom) != v["version"]]


def rapport(attendu: dict, obtenu: dict[str, str]) -> str:
    """Pur : une ligne par composant, puis le verdict."""
    lignes = [f"{nom:14} attendu {v['version']:12} production {obtenu.get(nom) or 'non lu':30} "
              f"{'ok' if obtenu.get(nom) == v['version'] else 'ÉCART'}" for nom, v in attendu.items()]
    e = ecarts(attendu, obtenu)
    return "\n".join(lignes + [f"{len(e)} écart(s)" if e else "production conforme"])


def version_gateway(adresse: str, delai: float = 10, ouvrir=urllib.request.urlopen) -> str:
    """Ce que le gateway dit de lui : GET <adresse>/v1/version. Une route absente (gateway d'avant la route) ou un
    gateway injoignable se lisent comme une version, pour que l'écart s'imprime au lieu d'arrêter le contrôle."""
    try:
        with ouvrir(adresse.rstrip("/") + "/v1/version", timeout=delai) as r:
            return str(json.load(r).get("version") or "vide")
    except urllib.error.HTTPError as e:
        return f"sans /v1/version (HTTP {e.code})"
    except Exception as e:  # noqa: BLE001 — un contrôle ne lève pas, il dit
        return f"injoignable ({type(e).__name__})"


def ssh(acces: dict) -> list[str]:
    return ["ssh", "-i", str(Path(acces["VPS_CLE"]).expanduser()), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}"]


def version_cortexdb(acces: dict, clone: str, run=subprocess.run) -> str:
    """Ce que le binaire du serveur dit de lui : `cortexdb-grpc -version` imprime « cortexdb-grpc v2.139.0 »."""
    try:
        r = run([*ssh(acces), "sudo " + shlex.quote(f"{clone}/cache/cortexdb/bin/cortexdb-grpc") + " -version"],
                capture_output=True, text=True, timeout=60, check=True)
        return r.stdout.split()[-1] if r.stdout.split() else "vide"
    except Exception as e:  # noqa: BLE001
        return f"injoignable ({type(e).__name__})"


def lire(attendu: dict, acces: dict | None, sans_ssh: bool = False) -> dict[str, str]:
    """Une lecture par composant ; sans accès ssh, cortexdb-grpc reste « non lu »."""
    obtenu = {"th-gateway": version_gateway(attendu["th-gateway"]["adresse"])}
    if acces and not sans_ssh:
        pass
        from newskit import publier_index as pi  # noqa: PLC0415
        obtenu["cortexdb-grpc"] = version_cortexdb(acces, acces.get("VPS_CLONE", pi.CLONE_DEFAUT).rstrip("/"))
    return obtenu


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sans-ssh", action="store_true", help="ne lit que le gateway (pas d'accès au serveur)")
    a = ap.parse_args()
    from newskit import publier_index as pi  # noqa: PLC0415
    attendu = attendues()
    obtenu = lire(attendu, pi.lire_acces(), a.sans_ssh)
    print(rapport(attendu, obtenu))
    return 1 if ecarts(attendu, obtenu) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Envoi au serveur de ce que son serveur CortexDB permanent (serveur_cortexdb.py) exige (23.10.2026).

Le dépôt ne porte ni les cerveaux (cache/, .gitignore) ni CortexDB. Ce script envoie par rsync, dans le clone du
serveur :

  cache/pilote_cortexdb/vps/cortexdb_client-*.whl  → cache/cortexdb/client/             (l'installateur l'installe)
  cache/cortexdb/<g>.db et <g>.json                → cache/cortexdb/<g>.envoi.db, <g>.envoi.json

Le cerveau arrive dans une copie de travail gardée d'un envoi à l'autre : rsync n'envoie que ce qui change. Côté
serveur, un cerveau dont le bilan a changé est copié en `<g>.pret.db` (renommé d'un coup, jamais lu à moitié), puis
le bilan ; le gardien du graphe le met en place. Accès : celui de publier_index (~/.secrets/vps_kg.env).

La roue du client se construit ici (cortexdb-grpc, lui, vient de sa release : newskit.deployer_cortexdb) :
    cd cache/pilote_cortexdb/src-v2.138.0/clients/python && ../../../venv/bin/python -m pip wheel --no-deps . -w ../../../vps

    python3 -m newskit.publier_cerveau                       # les graphes au cerveau engagé (cortexdb: true)
    python3 -m newskit.publier_cerveau --graphe <graphe> --lister
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import publier_index as pi  # noqa: E402

VPS = RACINE / "cache" / "pilote_cortexdb" / "vps"
# th-gateway ne part plus par ici (A5, 25.10.2026) : il se déploie par sa release, newskit.deployer_gateway.
# cortexdb-grpc non plus (C1, 27.10.2026) : sa release amont, newskit.deployer_cortexdb, qui pose aussi celui du Mac.
DOSSIER = RACINE / "cache" / "cortexdb"


def graphes_engages() -> list[str]:
    from newskit import config_sources as cfg  # noqa: PLC0415
    return sorted(g for g, v in (cfg.charger().get("graphes") or {}).items()
                  if (v or {}).get("cortexdb") and tenu_au_serveur(g))


def tenu_au_serveur(graphe: str) -> bool:
    """Faux pour un cerveau gardé au Mac (`cortexdb_publie: false`) : construit et mesuré avant d'être servi (GV7,
    2 novembre 2026). Il ne part ni lors d'un passage complet, ni par un envoi qui le nomme."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    return ((cfg.charger().get("graphes") or {}).get(graphe) or {}).get("cortexdb_publie", True) is not False


def _publie(graphe: str) -> bool:
    try:
        return bool(json.loads((DOSSIER / f"{graphe}.json").read_text(encoding="utf-8")).get("publie"))
    except (OSError, ValueError):
        return False


def a_envoyer(graphes: list[str]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for roue in sorted(VPS.glob("cortexdb_client-*.whl")):
        out.append((roue, f"cache/cortexdb/client/{roue.name}"))
    for g in graphes:
        if (DOSSIER / f"{g}.db").exists() and _publie(g) and tenu_au_serveur(g):
            out.append((DOSSIER / f"{g}.db", f"cache/cortexdb/{g}.envoi.db"))
            out.append((DOSSIER / f"{g}.json", f"cache/cortexdb/{g}.envoi.json"))
    return out


def options_rsync(rel: str) -> list[str]:
    """Un cerveau s'écrit en place : rsync n'envoie que ce qui change d'une copie de travail de plusieurs Go. Un
    binaire part dans un fichier temporaire que rsync renomme : écrit en place, un binaire en service est refusé
    (« Text file busy », th-gateway le 25.10.2026), et le processus qui tourne garde l'ancien jusqu'au redémarrage."""
    return ["-t"] if "/bin/" in f"/{rel}" else ["-t", "--inplace"]


def commande_distante(clone: str, graphes: list[str]) -> str:
    """Côté serveur : un cerveau dont le bilan a changé devient prêt, puis le bilan ; tout revient à kg."""
    etapes = [f"cd {shlex.quote(clone + '/cache/cortexdb')}"]
    for g in graphes:
        etapes.append(f"if [ -f {g}.envoi.json ] && ! cmp -s {g}.envoi.json {g}.json; then "
                      f"cp {g}.envoi.db {g}.pret.tmp && mv {g}.pret.tmp {g}.pret.db && cp {g}.envoi.json {g}.json "
                      f"&& echo '{g} : cerveau prêt'; fi")
    etapes += ["chmod 755 bin/*", "chown -R kg:kg ."]
    return " && ".join(etapes)


def publier(graphes: list[str] | None = None, verbeux: bool = True) -> list[str]:
    """Envoie binaire, client et cerveaux ; rend les lignes du compte rendu. Ne lève jamais."""
    lignes: list[str] = []
    acces = pi.lire_acces()
    if not acces:
        lignes.append("Cerveaux en ligne : accès au serveur non configuré, rien d'envoyé.")
        return pi._dire(lignes, verbeux)
    graphes = graphes if graphes is not None else graphes_engages()
    envois = a_envoyer(graphes)
    if not envois:
        lignes.append("Cerveaux en ligne : rien à envoyer.")
        return pi._dire(lignes, verbeux)
    cle = str(Path(acces["VPS_CLE"]).expanduser())
    hote = f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}"
    clone = acces.get("VPS_CLONE", pi.CLONE_DEFAUT).rstrip("/")
    ssh = ["ssh", "-i", cle, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    try:
        dossiers = sorted({str(Path(rel).parent) for _, rel in envois})
        subprocess.run([*ssh, hote, "sudo mkdir -p " + " ".join(f"{clone}/{d}" for d in dossiers)],
                       capture_output=True, text=True, timeout=60, check=True)
        for src, rel in envois:
            r = subprocess.run(["rsync", *options_rsync(rel), "--rsync-path=sudo rsync", "-e", " ".join(ssh), str(src),
                                f"{hote}:{clone}/{rel}"], capture_output=True, text=True, timeout=7200)
            if r.returncode != 0:
                raise RuntimeError(f"rsync {rel} : {r.stderr.strip()[:160]}")
        envoyes = sorted({rel.split("/")[-1].split(".envoi")[0] for _, rel in envois if ".envoi." in rel})
        r = subprocess.run([*ssh, hote, "sudo bash -c " + shlex.quote(commande_distante(clone, envoyes))],
                           capture_output=True, text=True, timeout=600, check=True)
        prets = [l for l in r.stdout.splitlines() if l.strip()]
        taille = sum(src.stat().st_size for src, _ in envois) / 1e6
        lignes.append(f"Cerveaux en ligne : {', '.join(envoyes) or 'aucun'} — {len(envois)} fichier(s), {taille:.0f} Mo "
                      f"au plus (rsync n'envoie que ce qui change) ; {'; '.join(prets) or 'aucun cerveau neuf'}.")
    except subprocess.CalledProcessError as e:
        lignes.append(f"Cerveaux en ligne : envoi interrompu (ssh code {e.returncode} : {(e.stderr or e.stdout or '').strip()[:160]}).")
    except Exception as e:  # noqa: BLE001 — le serveur garde le cerveau qu'il a
        lignes.append(f"Cerveaux en ligne : envoi interrompu ({type(e).__name__} {str(e)[:120]}).")
    return pi._dire(lignes, verbeux)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", action="append", help="graphe à envoyer (répétable) ; ceux au cerveau engagé sinon")
    ap.add_argument("--lister", action="store_true", help="dire ce qui partirait, sans rien envoyer")
    a = ap.parse_args()
    if a.lister:
        for src, rel in a_envoyer(a.graphe or graphes_engages()):
            print(f"  {rel}  ←  {src.relative_to(RACINE)}  ({src.stat().st_size / 1e6:.0f} Mo)")
        return 0
    lignes = publier(a.graphe)
    return 0 if lignes and "interrompu" not in lignes[-1] else 1


if __name__ == "__main__":
    sys.exit(main())

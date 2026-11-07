#!/usr/bin/env python3
"""Envoi des graphes au serveur hors de git (24.10.2026).

Décision du porteur : seul un graphe de test reste dans le dépôt. Le serveur recevait les graphes par le `git pull`
de kg-mcp-maj ; ce script les envoie par rsync, dans le clone du serveur, à la fin de chaque passage :

  les fichiers de graphe/<g>/ (graphe/ pour le graphe principal, sans les dossiers des autres graphes)
  que git ne suit pas

Avant la purge, git les suit tous : rien ne part, le pull garde la main (un fichier suivi réécrit sous le pull
l'arrêterait). Après, tout part sauf le graphe de test, que git apporte. Jamais de --delete : un fichier propre au
serveur reste. rsync écrit à côté puis renomme : un graphe n'est jamais lu à moitié, et le serveur le recharge à chaud
(date du fichier). La marque graphe/.envoi dit à kg-mcp-maj de régénérer les pages sans commit nouveau.
Accès : celui de publier_index (~/.secrets/vps_kg.env).

    python3 -m newskit.publier_graphes                     # tous les graphes du registre
    python3 -m newskit.publier_graphes --graphe <graphe> --lister
"""

from __future__ import annotations

import argparse
import fnmatch
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import publier_index as pi  # noqa: E402

MARQUE = "graphe/.envoi"
# Ce que .gitignore écarte de graphe/ hors de la règle générale de la purge : formes en clair du graphe principal (les
# archives .json.gz suffisent au serveur), index des paragraphes (publier_index les envoie avec leur encodeur),
# renvois de la dernière réponse locale. Tenu égal aux lignes de .gitignore par un test.
EXCLUS = (
    "graphe/squelette.json",
    "graphe/graphe.json",
    "graphe/*/synthese/.derniers_renvois.json",
    "graphe/synthese/.derniers_renvois.json",
    "graphe/*/synthese/vecteurs.npy",
    "graphe/*/synthese/paragraphes.json",
    "graphe/*/synthese/encodeur.txt",
    "graphe/*/synthese/empreintes_index.json",
    "graphe/synthese/vecteurs.npy",
    "graphe/synthese/paragraphes.json",
    "graphe/synthese/encodeur.txt",
    "graphe/synthese/empreintes_index.json",
)


def exclu(rel: str) -> bool:
    return any(fnmatch.fnmatchcase(rel, m) for m in EXCLUS)


def suivis() -> set[str]:
    """Les fichiers de graphe/ que git suit : ils arrivent au serveur par le pull."""
    r = subprocess.run(["git", "ls-files", "-z", "graphe"], cwd=RACINE, capture_output=True, text=True, check=True)
    return {p for p in r.stdout.split("\0") if p}


def fichiers(graphe: str, deja: set[str] | None = None) -> list[str]:
    """Les fichiers du graphe à envoyer, relatifs au dépôt, sans ceux des autres graphes ni ceux que git suit."""
    base = cfg.chemins_du_graphe(graphe)["base"]
    autres = [
        b
        for g in cfg.charger().get("graphes", {})
        if g != graphe
        for b in [cfg.chemins_du_graphe(g)["base"]]
        if b != base and base in b.parents
    ]
    deja = suivis() if deja is None else deja
    out = []
    for p in base.rglob("*"):
        if not p.is_file() or any(a in p.parents for a in autres):
            continue
        rel = p.relative_to(RACINE).as_posix()
        if rel not in deja and rel != MARQUE and not exclu(rel):
            out.append(rel)
    return sorted(out)


def commande_distante(clone: str) -> str:
    """Côté serveur : tout revient à kg, puis la marque de l'envoi."""
    g = shlex.quote(f"{clone}/graphe")
    return f"chown -R kg:kg {g} && runuser -u kg -- touch {shlex.quote(f'{clone}/{MARQUE}')}"


def publier(graphes: list[str] | None = None, verbeux: bool = True) -> list[str]:
    """Envoie les graphes ; rend les lignes du compte rendu. Ne lève jamais."""
    lignes: list[str] = []
    graphes = graphes if graphes is not None else sorted(cfg.charger().get("graphes", {}))
    try:
        deja = suivis()
        liste = sorted({f for g in graphes for f in fichiers(g, deja)})
    except Exception as e:  # noqa: BLE001
        lignes.append(f"Graphes en ligne : liste impossible ({type(e).__name__} {str(e)[:120]}), rien d'envoyé.")
        return pi._dire(lignes, verbeux)
    if not liste:
        lignes.append("Graphes en ligne : rien à envoyer (fichiers suivis par git, le serveur les tire).")
        return pi._dire(lignes, verbeux)
    acces = pi.lire_acces()
    if not acces:
        lignes.append("Graphes en ligne : accès au serveur non configuré, rien d'envoyé.")
        return pi._dire(lignes, verbeux)
    cle = str(Path(acces["VPS_CLE"]).expanduser())
    hote = f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}"
    clone = acces.get("VPS_CLONE", pi.CLONE_DEFAUT).rstrip("/")
    ssh = ["ssh", "-i", cle, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("\n".join(liste) + "\n")
        r = subprocess.run(
            [
                "rsync",
                "-t",
                "--files-from",
                f.name,
                "--rsync-path=sudo rsync",
                "-e",
                " ".join(ssh),
                str(RACINE) + "/",
                f"{hote}:{clone}/",
            ],
            capture_output=True,
            text=True,
            timeout=7200,
        )
        Path(f.name).unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError(f"rsync : {r.stderr.strip()[:160]}")
        subprocess.run(
            [*ssh, hote, "sudo bash -c " + shlex.quote(commande_distante(clone))],
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        taille = sum((RACINE / p).stat().st_size for p in liste) / 1e6
        lignes.append(
            f"Graphes en ligne : {', '.join(graphes)} — {len(liste)} fichier(s), {taille:.0f} Mo au plus "
            f"(rsync n'envoie que ce qui change)."
        )
    except subprocess.CalledProcessError as e:
        lignes.append(
            f"Graphes en ligne : envoi interrompu (ssh code {e.returncode} : {(e.stderr or e.stdout or '').strip()[:160]})."
        )
    except Exception as e:  # noqa: BLE001 — le serveur garde les graphes qu'il a
        lignes.append(f"Graphes en ligne : envoi interrompu ({type(e).__name__} {str(e)[:120]}).")
    return pi._dire(lignes, verbeux)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", action="append", help="un graphe (répétable) ; par défaut tous ceux du registre")
    ap.add_argument("--lister", action="store_true", help="dire ce qui partirait, sans rien envoyer")
    a = ap.parse_args()
    if a.lister:
        deja = suivis()
        for g in a.graphe or sorted(cfg.charger().get("graphes", {})):
            f = fichiers(g, deja)
            print(f"{g} : {len(f)} fichier(s) à envoyer")
        return 0
    lignes = publier(a.graphe)
    return 1 if any("interrompu" in l for l in lignes) else 0


if __name__ == "__main__":
    sys.exit(main())

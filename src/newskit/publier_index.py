#!/usr/bin/env python3
"""Envoi au serveur de ce qui vit hors dépôt mais que la recherche hybride exige (10.10.2026).

Le dépôt ne porte ni l'index des paragraphes (31 Mo par graphe, .gitignore) ni les modèles ; le serveur
tire le dépôt à l'heure mais ne calcule rien. Ce script envoie par rsync, dans le clone du
serveur, exactement ce que synthese.py y lit :

  graphe/<g>/synthese/{paragraphes.json, vecteurs.npy, encodeur.txt}   l'index, calculé ici (synthese.py indexer)
  cache/encodeur/<g>/retenu et cache/encodeur/<g>/<modele>/onnx/        l'encodeur affiné exporté (exporter_onnx.py)
  cache/reclasseur/<nom>/onnx/                                          le reclasseur exporté

L'index et son encodeur partent ensemble : un index calculé avec un modèle se lit avec le même (encodeur.txt).
Accès : celui de depots.py (~/.secrets/vps_kg.env : VPS_HOTE, VPS_UTILISATEUR, VPS_CLE ; VPS_CLONE facultatif,
`edition.clone` du registre par défaut). rsync tourne en sudo côté serveur, les fichiers reviennent à kg:kg.
Le serveur recharge un index changé sans redémarrer (synthese.index lit la date de vecteurs.npy) ; les modèles,
chargés une fois, demandent un redémarrage du service (le service de mise à jour du serveur, media.service_maj, le fait au prochain changement de script).

    python3 -m newskit.publier_index --graphe <graphe>            # index + modèles du graphe
    python3 -m newskit.publier_index                                   # tous les graphes qui ont un index
    python3 -m newskit.publier_index --graphe <graphe> --sans-modeles

Appelé par la routine (routine_hebdo.publier_en_ligne) ; jamais bloquant : sans accès, rien n'est envoyé et la
recherche du serveur reste lexicale.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import synthese as sy  # noqa: E402

ACCES = Path.home() / ".secrets" / "vps_kg.env"
CLONE_DEFAUT = cfg.edition()["clone"]  # le clone du serveur de l'édition (bloc `edition:`, N2)


def lire_acces() -> dict | None:
    if not ACCES.exists():
        return None
    acces = {
        k.strip(): v.strip().strip("\"'")
        for k, v in (
            l.split("=", 1)
            for l in ACCES.read_text(encoding="utf-8").splitlines()
            if "=" in l and not l.lstrip().startswith("#")
        )
    }
    return acces if {"VPS_HOTE", "VPS_UTILISATEUR", "VPS_CLE"} <= set(acces) else None


def synthese_de(graphe: str) -> Path:
    """Le dossier de l'index d'après le registre : le graphe principal (un graphe d'ingénierie) vit à la racine de graphe/
    (24.10.2026 : graphe/<g>/synthese/ écrit à la main ne l'aurait jamais envoyé)."""
    return cfg.chemins_du_graphe(graphe)["base"] / "synthese"


def a_un_index(graphe: str) -> bool:
    return (synthese_de(graphe) / "vecteurs.npy").exists()


def graphes_indexes() -> list[str]:
    """Les graphes du registre qui ont un index calculé ici."""
    return sorted(g for g in cfg.charger().get("graphes", {}) if a_un_index(g))


def a_envoyer(graphe: str, modeles: bool = True) -> list[tuple[Path, str]]:
    """(source locale, chemin relatif au clone) de ce qui existe ici pour ce graphe."""
    out: list[tuple[Path, str]] = []
    synth = synthese_de(graphe)
    for nom in ("paragraphes.json", "vecteurs.npy", "encodeur.txt"):
        if (synth / nom).exists():
            out.append((synth / nom, (synth / nom).relative_to(RACINE).as_posix()))
    if not modeles:
        return out
    affine = sy.modele_affine(graphe)
    if affine and (affine / "onnx" / "model.onnx").exists():
        out.append((affine.parent / "retenu", f"cache/encodeur/{graphe}/retenu"))
        out.append((affine / "onnx", f"cache/encodeur/{graphe}/{affine.name}/onnx"))
    rc = RACINE / "cache" / "reclasseur" / sy.RECLASSEUR.split("/")[-1] / "onnx"
    if (rc / "model.onnx").exists():
        out.append((rc, f"cache/reclasseur/{rc.parent.name}/onnx"))
    return out


def publier(graphes: list[str] | None = None, modeles: bool = True, verbeux: bool = True) -> list[str]:
    """Envoie index et modèles ; rend les lignes du compte rendu. Ne lève jamais."""
    lignes: list[str] = []
    acces = lire_acces()
    if not acces:
        lignes.append("Index en ligne : accès au serveur non configuré, rien d'envoyé.")
        return _dire(lignes, verbeux)
    if graphes is None:
        graphes = graphes_indexes()
    cle = str(Path(acces["VPS_CLE"]).expanduser())
    hote = f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}"
    clone = acces.get("VPS_CLONE", CLONE_DEFAUT).rstrip("/")
    ssh = ["ssh", "-i", cle, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    envois: list[tuple[Path, str]] = []
    for g in graphes:
        envois += a_envoyer(g, modeles)
    vus: set[str] = set()
    envois = [e for e in envois if not (e[1] in vus or vus.add(e[1]))]
    if not envois:
        lignes.append("Index en ligne : rien à envoyer (pas d'index calculé).")
        return _dire(lignes, verbeux)
    sans_index = [g for g in graphes if not a_un_index(g)]
    if sans_index:
        lignes.append(
            f"Index en ligne : pas d'index calculé pour {', '.join(sans_index)} — modèles communs seuls envoyés."
        )
    try:
        dossiers = sorted({str(Path(rel).parent) if src.is_file() else rel for src, rel in envois})
        subprocess.run(
            [*ssh, hote, "sudo mkdir -p " + " ".join(f"{clone}/{d}" for d in dossiers)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        for src, rel in envois:
            cible = f"{hote}:{clone}/{rel}" + ("/" if src.is_dir() else "")
            r = subprocess.run(
                [
                    "rsync",
                    "-rlt",
                    "--rsync-path=sudo rsync",
                    "-e",
                    " ".join(ssh),  # (pas de --chmod : rsync 2.6.9 du Mac)
                    str(src) + ("/" if src.is_dir() else ""),
                    cible,
                ],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if r.returncode != 0:
                raise RuntimeError(f"rsync {rel} : {r.stderr.strip()[:160]}")
        # Seuls les dossiers créés ci-dessus changent de propriétaire : le 11 et le 12.10.2026, l'envoi d'un graphe financier (sans
        # index local, reclasseur seul) échouait sur le chown de graphe/<nom>/synthese, absent du serveur.
        cibles = " ".join(f"{clone}/{d}" for d in dossiers)
        subprocess.run(
            [*ssh, hote, f"sudo chown -R kg:kg {cibles} && sudo chmod -R u+rwX,go+rX {cibles}"],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        taille = (
            sum(
                (sum(f.stat().st_size for f in src.rglob("*") if f.is_file()) if src.is_dir() else src.stat().st_size)
                for src, _ in envois
            )
            / 1e6
        )
        lignes.append(
            f"Index en ligne : {', '.join(graphes)} — {len(envois)} élément(s), {taille:.0f} Mo au plus (rsync n'envoie que ce qui change)."
        )
    except subprocess.CalledProcessError as e:  # la commande distante dit pourquoi, pas sa ligne de commande
        lignes.append(
            f"Index en ligne : envoi interrompu (ssh code {e.returncode} : {(e.stderr or e.stdout or '').strip()[:160]})."
        )
    except Exception as e:  # noqa: BLE001 — la recherche du serveur reste lexicale
        lignes.append(f"Index en ligne : envoi interrompu ({type(e).__name__} {str(e)[:120]}).")
    return _dire(lignes, verbeux)


def _dire(lignes: list[str], verbeux: bool) -> list[str]:
    if verbeux:
        for l in lignes:
            print(l, flush=True)
    return lignes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", action="append", help="graphe à publier (répétable) ; tous ceux qui ont un index sinon")
    ap.add_argument("--sans-modeles", action="store_true", help="l'index seul")
    ap.add_argument("--lister", action="store_true", help="dire ce qui partirait, sans rien envoyer")
    a = ap.parse_args()
    if a.lister:
        graphes = a.graphe or sorted(p.parent.parent.name for p in (RACINE / "graphe").glob("*/synthese/vecteurs.npy"))
        for g in graphes:
            for src, rel in a_envoyer(g, not a.sans_modeles):
                print(f"  {rel}  ←  {src.relative_to(RACINE)}")
        return 0
    lignes = publier(a.graphe, not a.sans_modeles)
    return 0 if lignes and lignes[-1].startswith("Index en ligne : ") and "interrompu" not in lignes[-1] else 1


if __name__ == "__main__":
    sys.exit(main())

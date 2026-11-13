#!/usr/bin/env python3
"""La collecte horaire des flux RSS (mainteneur, 03.11.2026 : « la collecte des flux pourrait être faite toutes les heures »).

Les flux des graphes à routine sont relus chaque heure, sous condition (collecte_rss : ETag, Last-Modified) : les
fiches neuves sont sur le disque dès leur publication, sans appel au modèle, et la routine du matin les verse au
graphe. Une routine en cours tient le verrou : la collecte saute son tour, sans attendre.

Après chaque collecte, le fil de la page d'accueil (fil.py) rédige les billets des fiches neuves. Puis la fraîcheur du
fil se surveille (mainteneur, 03.11.2026 : « il faut surveiller la fraîcheur des nouvelles ») : le passage
alerte par paliers, du blocage à la pénurie (alertes_fil, mainteneur, 11.11.2026).
Le fil du graphe du site part ensuite sur le VPS (publier_fil.py), où la page d'accueil le relit.
Enfin le passage relève les inscriptions du site et envoie les courriels de confirmation (diffusion.relever, que porte l'édition).
Le daemon de la collecte continue lance ce script toutes les 5 minutes (mainteneur, 03.11.2026 : « 1 mise à
jour toutes les 5 minutes me semblerait bien »).

Usage :
    python3 -m newskit.collecte_continue [--graphe <graphe> ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)
from newskit import diffusion  # noqa: E402 — la liste des abonnés de l'édition (config.yaml)
from newskit import media  # noqa: E402
from newskit import fil  # noqa: E402
from newskit import notifier_courriel as nc  # noqa: E402
from newskit import alertes_fil  # noqa: E402
from newskit import publier_fil  # noqa: E402
from newskit import pages_fiches  # noqa: E402 — une page par fiche (13.11.2026)
from newskit import routine_hebdo as rh  # noqa: E402

chemin_fil = fil.chemin_fil


def surveiller(graphe: str, code_fil: int, publication: str | None) -> None:
    """Les alertes du fil par paliers (alertes_fil) ; un échec de la surveillance se dit et n'arrête pas le passage."""
    try:
        alertes_fil.surveiller(graphe, code_fil, publication)
    except Exception as e:  # noqa: BLE001
        print(
            time.strftime("%Y-%m-%d %H:%M"), graphe, f"alertes du fil : {type(e).__name__}: {str(e)[:120]}", flush=True
        )


def inscriptions() -> str:
    """La ligne de journal de la relève des inscriptions du site (diffusion.relever, que porte l'édition) ; vide s'il n'y a rien. Un échec se
    dit et n'arrête pas le passage."""
    try:
        ligne = diffusion.relever()
    except Exception as e:  # noqa: BLE001
        ligne = f"échec ({type(e).__name__}: {str(e)[:120]})"
    return f"{time.strftime('%Y-%m-%d %H:%M')} inscriptions : {ligne}" if ligne else ""


def apres_redaction(graphe: str) -> str:
    """Le pas de l'édition après le fil du graphe (config.yaml, `apres_redaction` : un module qui porte
    `apres_redaction(graphe) -> str` ; mainteneur, 13.11.2026 : « la récupération d'adresse devrait faire partie du
    pipeline standard et accompagner la rédaction »). Sa ligne de journal ; vide sans déclaration ni rien à dire. Une
    panne se dit et n'arrête pas le passage."""
    nom = media.courant().get("apres_redaction")
    if not nom:
        return ""
    try:
        ligne = diffusion.charger(nom).apres_redaction(graphe)
    except Exception as e:  # noqa: BLE001
        ligne = f"échec ({type(e).__name__}: {str(e)[:120]})"
    return f"{time.strftime('%Y-%m-%d %H:%M')} {graphe} après la rédaction : {ligne}" if ligne else ""


def lignes_d_echec(code: int, stderr: str) -> list[str]:
    """Les lignes de la collecte à garder au journal : les réparations des sources (« ~~ sap », sante_sources.py),
    toujours et en tête ; puis, si le code dit un échec, ses causes, huit au plus."""
    lignes = [x.strip() for x in stderr.splitlines() if x.strip() and "ALERTE" not in x]
    sap = [x for x in lignes if x.startswith("~~")]
    return sap + ([x for x in lignes if not x.startswith("~~")][:8] if code else [])


def graphes(demandes: list[str] | None) -> list[str]:
    """Les graphes de la collecte : ceux demandés, sinon le graphe du site (cfg.graphe_du_site)."""
    return list(demandes or [rh.cfg.graphe_du_site()])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", action="append", help="un graphe (plusieurs possibles) ; par défaut le graphe du site")
    a = ap.parse_args()
    # La routine d'un autre graphe n'arrête rien ; celle du graphe (ou du principal) suspend la seule collecte des flux,
    # qu'elle fait elle-même : le fil continue (mainteneur, 05.11.2026 : « le panneau devrait bouger en continu »).
    # Son propre verrou d'abord, puis la lecture du verrou des routines : une routine qui démarre attend la fin des flux.
    import fcntl  # noqa: PLC0415

    rh.VERROU_COLLECTE.parent.mkdir(parents=True, exist_ok=True)
    for g in graphes(a.graphe):
        t0 = time.time()
        with open(rh.VERROU_COLLECTE, "a+", encoding="utf-8") as v:
            fcntl.flock(v, fcntl.LOCK_EX)
            occupe = rh.graphe_en_routine()
            if occupe in (g, rh.cfg.GRAPHE_PRINCIPAL):
                print(
                    time.strftime("%Y-%m-%d %H:%M"),
                    g,
                    f"routine de {occupe} en cours : flux non relus, fil rédigé",
                    flush=True,
                )
            else:
                r = subprocess.run(
                    [sys.executable, str(CODE / "collecte_rss.py"), "--graphe", g],
                    cwd=RACINE,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                print(
                    time.strftime("%Y-%m-%d %H:%M"),
                    g,
                    f"code {r.returncode}",
                    f"{time.time() - t0:.0f} s",
                    (r.stdout.strip().splitlines() or [""])[-1][:160],
                    flush=True,
                )
                for l in lignes_d_echec(r.returncode, r.stderr or ""):
                    print(
                        time.strftime("%Y-%m-%d %H:%M"),
                        g,
                        "  sap :" if l.startswith("~~") else "  échec :",
                        l[:200],
                        flush=True,
                    )
        r = subprocess.run(
            [sys.executable, str(CODE / "fil.py"), "--graphe", g],
            cwd=RACINE,
            capture_output=True,
            text=True,
            check=False,
        )
        print(
            time.strftime("%Y-%m-%d %H:%M"),
            g,
            "fil",
            f"code {r.returncode}",
            (r.stdout.strip().splitlines() or [""])[-1][:160],
            flush=True,
        )
        publication = None
        if g == rh.cfg.graphe_du_site():  # le graphe de la page d'accueil : son fil part sur le VPS (04.11.2026)
            publication = publier_fil.publier(chemin_fil(g).parent)
            print(time.strftime("%Y-%m-%d %H:%M"), g, "fil en ligne :", publication, flush=True)
            try:  # une page par fiche ; sa panne n'arrête jamais le fil
                pages = pages_fiches.publier(g)
            except Exception as e:  # noqa: BLE001
                pages = type(e).__name__
            print(time.strftime("%Y-%m-%d %H:%M"), g, "pages des fiches :", pages, flush=True)
        surveiller(g, r.returncode, publication)
        ligne = apres_redaction(g)
        if ligne:
            print(ligne, flush=True)
    ligne = inscriptions()
    if ligne:
        print(ligne, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

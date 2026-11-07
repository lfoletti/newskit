#!/usr/bin/env python3
"""Mesure à chaud de la recherche hybride, sur le serveur comme ici (10.10.2026) : temps de chargement de chaque pièce
(graphe, encodeur, index, reclasseur), temps de `passages_hybrides` sur trois questions une fois tout en mémoire, et
mémoire de pointe du processus. Aucun appel au modèle, rien d'écrit.

    sudo runuser -u <compte> -- <venv>/bin/python newskit.mesure_serveur --graphe <graphe>   (serveur)
    python3 -m newskit.mesure_serveur --graphe <graphe>                                              (ici)

Repères du 10.10.2026 sur le serveur (4 vCPU), modèles fp32 : chargement 13 s, 5 à 6 s par question, RSS 2,4 Go ;
attendus avec plongements int8 et BM25 inversé : ~1,2 Go, 4 à 5 s."""
from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

QUESTIONS = [
    "Quel est le délai de notification d'une violation de données à l'autorité de contrôle selon le RGPD ?",
    "Quelles sanctions le DSA prévoit-il pour les très grandes plateformes ?",
    "Que prévoit la nLPD suisse pour le transfert de données à l'étranger ?",
]


def rss() -> int:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r // (1024 if sys.platform != "darwin" else 1024 ** 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    a = ap.parse_args()
    from newskit import synthese as sy  # noqa: PLC0415
    g = a.graphe
    for nom, f in (("graphe", lambda: sy.charger_graphe(g)), ("encodeur", lambda: sy.encodeur(g)),
                   ("index", lambda: sy.index(g)), ("reclasseur", sy.reclasseur)):
        t = time.time(); x = f()
        print(f"  {nom:10s} {type(x).__name__ if x is not None else 'absent':16s} {time.time() - t:5.1f} s   RSS {rss()} Mo", flush=True)
    for q in QUESTIONS:
        t = time.time(); p = sy.passages_hybrides(g, q, 3)
        print(f"  question : {time.time() - t:4.1f} s, {len(p or [])} passage(s)" + ("" if p else " (recherche hybride indisponible)"), flush=True)
    print(f"  RSS de pointe {rss()} Mo")
    return 0


if __name__ == "__main__":
    sys.exit(main())

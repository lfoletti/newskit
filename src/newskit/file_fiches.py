#!/usr/bin/env python3
"""La file des fiches d'un graphe continu : qui écrit, change ou retire une fiche le signale ici.

Un graphe continu (`cerveau_continu: true`) reçoit ses fiches dans son cerveau au fil de la journée. Le passage continu
ne parcourt pas toutes les fiches : il lit cette file. Un événement est une ligne JSON de `<g>.file.jsonl`, dans le
dossier des cerveaux :

    {"chemin": "sources/…/2026-11-18_titre.md", "sorte": "parue", "quand": "2026-11-18 19:20:01", "par": ""}

Les sortes : `parue` (fiche neuve), `modifiee` (texte changé), `retiree` (fichier effacé ou déplacé), `droits` (le
document versé change sans que la fiche change : un masque, un refus).

La file n'existe que sur la machine où le passage continu tourne : il pose la marque `<g>.continu` (`ouvrir`). Ailleurs,
`signaler` n'écrit rien, et aucun fichier ne grossit sans lecteur. Un événement manqué n'est pas perdu : le contrôle
quotidien compare tous les fichiers au cerveau.

`signaler` ne lève jamais : une file illisible n'empêche pas l'écriture d'une fiche.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import time
from pathlib import Path

SORTES = ("parue", "modifiee", "retiree", "droits")


def _dossier() -> Path:
    from newskit import cerveau_cortexdb as cc  # noqa: PLC0415 — le dossier se lit à l'appel : un banc le déplace

    return cc.DOSSIER


def chemin(graphe: str) -> Path:
    return _dossier() / f"{graphe}.file.jsonl"


def marque(graphe: str) -> Path:
    return _dossier() / f"{graphe}.continu"


def ouvrir(graphe: str) -> None:
    """Le passage continu tourne sur cette machine : dès maintenant, les événements du graphe s'écrivent."""
    _dossier().mkdir(parents=True, exist_ok=True)
    marque(graphe).touch()


def ouverte(graphe: str) -> bool:
    """Vrai si les événements du graphe s'écrivent ici : graphe continu au registre et passage continu déjà venu."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    return cfg.cerveau_continu(graphe) and marque(graphe).exists()


@contextlib.contextmanager
def _verrou(graphe: str):
    """Un seul écrivain à la fois : un ajout ne tombe pas dans une file en cours de réécriture."""
    with open(_dossier() / f"{graphe}.file.verrou", "a", encoding="utf-8") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        yield


def signaler(fichier: Path | str, sorte: str, par: str = "") -> None:
    """Ajoute l'événement à la file de chaque graphe continu qui lit ce chemin. Ne lève jamais."""
    try:
        from newskit import fiche  # noqa: PLC0415 — fiche appelle ce module

        if sorte not in SORTES:
            raise ValueError(f"sorte d'événement inconnue : {sorte}")
        racine = fiche.racine_des_donnees().resolve()
        absolu = Path(fichier) if Path(fichier).is_absolute() else racine / fichier
        graphes = [g for g in fiche.graphes_du_chemin(absolu) or [] if ouverte(g)]
        if not graphes:
            return
        ligne = json.dumps(
            {
                "chemin": absolu.resolve().relative_to(racine).as_posix(),
                "sorte": sorte,
                "quand": time.strftime("%Y-%m-%d %H:%M:%S"),
                "par": par,
            },
            ensure_ascii=False,
        )
        for g in graphes:
            with _verrou(g), open(chemin(g), "a", encoding="utf-8") as f:
                f.write(ligne + "\n")
    except Exception as e:  # noqa: BLE001 — la fiche prime ; le contrôle quotidien rattrape un événement manqué
        import sys  # noqa: PLC0415

        print(f"  file des fiches : {type(e).__name__} {str(e)[:120]}", file=sys.stderr)


def _lignes(graphe: str) -> list[str]:
    try:
        return [l for l in chemin(graphe).read_text(encoding="utf-8").splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def lire(graphe: str) -> list[dict]:
    """Les événements en attente, dans l'ordre d'arrivée. Une ligne illisible rend un événement vide : elle garde sa
    place, pour qu'`acquitter` compte les mêmes lignes."""
    out = []
    for l in _lignes(graphe):
        try:
            d = json.loads(l)
        except ValueError:
            d = {}
        out.append(d if isinstance(d, dict) else {})
    return out


def acquitter(graphe: str, n: int) -> None:
    """Retire les `n` premiers événements, ceux que `lire` a rendus ; un événement arrivé depuis reste."""
    if n <= 0:
        return
    with _verrou(graphe):
        reste = _lignes(graphe)[n:]
        tmp = chemin(graphe).with_name(chemin(graphe).name + ".tmp")
        tmp.write_text("".join(l + "\n" for l in reste), encoding="utf-8")
        tmp.replace(chemin(graphe))

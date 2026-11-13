#!/usr/bin/env python3
"""La diffusion : le point d'extension vers la liste des abonnés de l'édition (NEWSKIT.md, N5, 07.11.2026).

Le moteur ne connaît pas les abonnés. Le `config.yaml` du média nomme un module de l'édition, relatif au dossier
(`diffusion: newskit.abonnes`), qui porte trois fonctions :

    envoyer(lettre: dict) -> int    envoyer une lettre à ses abonnés (0 : partie)
    lire() -> list[dict]            les abonnés inscrits ({"adresse": …}), pour vérifier un destinataire
    relever() -> str                relever les inscriptions du site ; une ligne de journal, vide s'il n'y a rien

Sans diffusion déclarée, rien n'est lu ni relevé, et une lettre ne part pas : envoyer rend 1 et le dit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from newskit.commun import RACINE  # noqa: E402
from newskit import media  # noqa: E402

_charges: dict[str, ModuleType] = {}


def charger(nom: str) -> ModuleType:
    """Le module de l'édition `nom` (un .py relatif au dossier du média), chargé une fois. Sert aussi au pas
    `apres_redaction` du passage (collecte_continue)."""
    chemin = RACINE / nom
    if str(chemin) in _charges:
        return _charges[str(chemin)]
    if not chemin.is_file():
        raise FileNotFoundError(f"config.yaml : {nom} introuvable sous {RACINE}")
    deja = sys.modules.get(chemin.stem)
    if deja is not None and Path(getattr(deja, "__file__", "") or "").resolve() == chemin.resolve():
        _charges[str(chemin)] = deja  # le même fichier, déjà importé sous son nom : une seule instance
        return deja
    if str(chemin.parent) not in sys.path:
        sys.path.insert(0, str(chemin.parent))  # le module de l'édition importe ses voisins
    spec = importlib.util.spec_from_file_location(chemin.stem, chemin)
    m = importlib.util.module_from_spec(spec)
    sys.modules[chemin.stem] = m
    spec.loader.exec_module(m)
    _charges[str(chemin)] = m
    return m


def module() -> ModuleType | None:
    """Le module de diffusion que nomme config.yaml, chargé une fois ; None sans déclaration."""
    nom = media.courant().get("diffusion")
    return charger(nom) if nom else None


def envoyer(lettre: dict) -> int:
    m = module()
    if m is None:
        print("aucune diffusion déclarée (config.yaml : diffusion) : la lettre ne part pas", file=sys.stderr)
        return 1
    return m.envoyer(lettre)


def lire() -> list[dict]:
    m = module()
    return m.lire() if m is not None else []


def relever() -> str:
    m = module()
    return m.relever() if m is not None else ""

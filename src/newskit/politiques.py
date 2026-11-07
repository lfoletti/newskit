#!/usr/bin/env python3
"""Les politiques de publication, lues dans deploiement/politiques.yaml (A3, 25.10.2026). Une valeur, un seul endroit :
la borne des extraits et l'adresse des registres de masques. Ce module ne fait que lire et vérifier."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
FICHIER = RACINE / "deploiement" / "politiques.yaml"


def charger(chemin: Path = FICHIER) -> dict:
    """Le fichier, vérifié : `extrait_mots` est un entier strictement positif, les deux registres de masques sont nommés."""
    d = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    n = d.get("extrait_mots")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise ValueError(f"{chemin.name} : extrait_mots doit être un entier > 0, pas {n!r}")
    m = d.get("masques") or {}
    for cle in ("source", "auteur"):
        if not m.get(cle):
            raise ValueError(f"{chemin.name} : masques.{cle} manque")
    return d


def extrait_mots(chemin: Path = FICHIER) -> int:
    return charger(chemin)["extrait_mots"]


def demarchage(chemin: Path = FICHIER) -> dict:
    """Ce que promet la page « how we use content » (J4.3, 31.10.2026). `contact` et `delai_refus_jours` valent null
    tant que le mainteneur ne les a pas décidés ; une valeur présente doit être juste. Rend le bloc `demarchage`, vérifié."""
    d = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    m = d.get("demarchage") or {}
    for cle in ("delai_refus_jours", "conservation_sans_reponse_mois"):
        n = m.get(cle)
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n <= 0):
            raise ValueError(f"{chemin.name} : demarchage.{cle} doit être un entier > 0 ou null, pas {n!r}")
    c = m.get("contact")
    if c is not None and (not isinstance(c, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c)):
        raise ValueError(f"{chemin.name} : demarchage.contact doit être une adresse de courriel ou null, pas {c!r}")
    return m


def demarchage_manquant(m: dict) -> list[str]:
    """Les décisions qui manquent pour publier la page : une page publique ne promet rien qui ne soit décidé."""
    return [cle for cle in ("responsable", "contact", "delai_refus_jours", "conservation_sans_reponse_mois")
            if m.get(cle) in (None, "")]


def registre_sources(chemin: Path = FICHIER) -> Path:
    """Le registre des sources : celui que nomme le `config.yaml` du média (clé `sources`, media.py), sinon celui que
    nomment les politiques (masques.source, relatif à la racine des données), sinon sources.yaml (N3, NEWSKIT.md :
    l'édition écrit les valeurs, le moteur les lit). Les deux noms, s'ils sont donnés, doivent dire le même fichier."""
    from newskit import media  # noqa: PLC0415 — media lit config_sources à la vérification, qui lit ce module à l'import
    du_media = media.fichier_des_sources()
    des_politiques = RACINE / charger(chemin)["masques"]["source"] if chemin.exists() else None
    if du_media and des_politiques and du_media.resolve() != des_politiques.resolve():
        raise ValueError(f"config.yaml nomme {du_media.name}, {chemin.name} nomme {des_politiques.name} : un seul registre")
    return du_media or des_politiques or RACINE / "sources.yaml"


def registre_auteurs(chemin: Path = FICHIER) -> Path:
    """Le registre des refus d'auteurs que nomment les politiques (masques.auteur, relatif à la racine des données) ;
    sans fichier des politiques, droits_auteurs.yaml (N3, NEWSKIT.md : l'édition écrit les valeurs, le moteur les lit)."""
    if not chemin.exists():
        return RACINE / "droits_auteurs.yaml"
    return RACINE / charger(chemin)["masques"]["auteur"]

"""Les réglages des fiches à éléments sont des champs admis d'un graphe (08.11.2026 : refusés, ils bloquaient toute la
configuration)."""

from __future__ import annotations

import inspect

from newskit import config_sources as cfg


def test_les_reglages_des_fiches_sont_admis():
    source = inspect.getsource(cfg._valider)
    for champ in ("arbre_fiches", "fiche_modele", "fiche_usd_jour"):
        assert f'"{champ}"' in source

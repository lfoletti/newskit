"""Les réglages des fiches à éléments sont des champs admis d'un graphe (08.11.2026 : refusés, ils bloquaient toute la
configuration)."""

from __future__ import annotations

import inspect

from newskit import alertes_fil, fil, fil_anglais
from newskit import config_sources as cfg


def test_les_reglages_des_fiches_sont_admis():
    source = inspect.getsource(cfg._valider)
    for champ in ("arbre_fiches", "fiche_modele", "fiche_usd_jour", "fil_usd_jour"):
        assert f'"{champ}"' in source


def test_le_plafond_du_fil_se_declare_par_graphe(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"fil_usd_jour": 50})
    assert fil.plafond("2026-11-08", "g") == 50.0


def test_sans_reglage_le_plafond_du_fil_reste_le_defaut(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert fil.plafond("2026-11-08", "g") == fil.FIL_USD_JOUR == 35.0


def test_un_jour_leve_reste_sans_plafond(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"fil_usd_jour": 50})
    monkeypatch.setattr(fil, "JOURS_SANS_PLAFOND", {"2026-11-08"})
    assert fil.plafond("2026-11-08", "g") == float("inf")


def test_les_appelants_passent_le_graphe():
    assert "plafond(jour, graphe)" in inspect.getsource(fil)
    assert "fil.plafond(jour, a.graphe)" in inspect.getsource(fil_anglais)
    assert "fil.plafond(jour, graphe)" in inspect.getsource(alertes_fil)

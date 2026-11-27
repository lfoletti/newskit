"""Les lecteurs du graphe le chargent par le nom du graphe, dans son cerveau (K3, étape 2, 27.11.2026) : plus de
chemin de graphe.json."""

from __future__ import annotations

import sys

import pytest

from newskit import angle_critique, reporter, synthese
from newskit import interroger_graphe as ig


class Charge(Exception):
    pass


@pytest.fixture
def charges(monkeypatch):
    vus = []

    def charger(graphe):
        vus.append(graphe)
        raise Charge

    monkeypatch.setattr(ig, "charger", charger)
    return vus


def test_la_synthese_charge_le_graphe_par_son_nom(charges):
    with pytest.raises(Charge):
        synthese.charger_graphe("g")
    assert charges == ["g"]


def test_l_angle_critique_charge_le_graphe_par_son_nom(charges):
    with pytest.raises(Charge):
        angle_critique.graphe_du("g")
    assert charges == ["g"]


def test_le_reporter_charge_le_graphe_par_son_nom(charges, monkeypatch):
    monkeypatch.setattr(reporter, "sujets_meilleurs", lambda g: [{"graphe": g, "titre": "t"}])
    monkeypatch.setattr(sys, "argv", ["reporter", "--meilleurs", "--graphe", "g"])
    with pytest.raises(Charge):
        reporter.main()
    assert charges == ["g"]

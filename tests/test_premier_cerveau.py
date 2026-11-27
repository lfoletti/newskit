"""Les lecteurs de la nuit lisent le cerveau tel qu'il est (K3, 27.11.2026 : le cerveau est le graphe) : celui du
passage continu, ou celui de la nuit d'avant. Le cerveau se reconstruit toujours en fin de nuit, après la pyramide et
les cartes qu'il verse (02.11.2026). Un graphe engagé qui n'a pas encore de cerveau, à sa première nuit, le reçoit
avant ses lecteurs : sans lui, tendances, carte et index n'auraient rien à lire."""

from __future__ import annotations

import inspect

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import routine_hebdo as rh


@pytest.fixture
def construits(monkeypatch, tmp_path):
    vus = []
    monkeypatch.setattr(rh, "cerveau_cortexdb", lambda j, g, etiquette, court=None: vus.append((g, etiquette)))
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / f"{g}.db")
    monkeypatch.setattr(cfg, "verse_cortexdb", lambda g: g != "sans_cerveau_engage")
    (tmp_path / "deja.db").write_text("", encoding="utf-8")
    return vus


def test_un_graphe_sans_cerveau_le_recoit_avant_ses_lecteurs(construits):
    rh.premier_cerveau(rh.Journal(), "neuf", " (neuf)")
    assert construits == [("neuf", " (neuf), premier passage")]


@pytest.mark.parametrize("graphe", ["deja", "sans_cerveau_engage"])
def test_un_cerveau_present_ou_un_graphe_non_engage_n_est_pas_construit_d_avance(construits, graphe):
    rh.premier_cerveau(rh.Journal(), graphe, "")
    assert construits == []


def test_le_premier_cerveau_vient_avant_tous_les_lecteurs_sans_composition(construits, monkeypatch):
    ordre = []
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: ordre.append(args[0]) or (True, ""))
    monkeypatch.setattr(rh, "cerveau_cortexdb", lambda j, g, etiquette, court=None: ordre.append("cerveau"))
    monkeypatch.setattr(rh, "tendances", lambda j, g, etiquette: ordre.append("tendances"))
    j = rh.Journal()
    rh.premiers_lecteurs(j, "neuf", "")
    assert ordre == [
        "cerveau",
        "tendances",
        "scripts/passe_partout.py",
        "scripts/digerer_graphe.py",
    ]


def test_la_nuit_lit_par_premiers_lecteurs():
    assert "premiers_lecteurs(j, g, etiquette)" in inspect.getsource(rh.main)

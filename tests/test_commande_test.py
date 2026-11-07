"""`newskit test` : l'essai du moteur sur le graphe d'exemple, sans frais (07.11.2026). Il remplace dans la
documentation `newskit temoin nuit`, qui reste accepté (le daemon de 0 h 30 de la première édition l'appelle). Le mot
« nuit » devient « passage » (07.11.2026) : `newskit temoin passage`, `nuit` restant un alias."""

from __future__ import annotations

import runpy

import pytest

from newskit import cli
from newskit import temoin


def test_newskit_test_lance_le_temoin_passage(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    assert cli.main(["test"]) == 0
    assert vus == [("newskit.temoin", ["passage"])]


def test_les_options_suivent(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    cli.main(["test", "--garder"])
    assert vus == [("newskit.temoin", ["passage", "--garder"])]


def test_l_ancien_nom_reste_accepte(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    cli.main(["temoin", "nuit"])
    assert vus == [("newskit.temoin", ["nuit"])]


@pytest.mark.parametrize("nom", ["passage", "nuit"])
def test_le_temoin_accepte_passage_et_nuit(monkeypatch, nom):
    vus = []
    monkeypatch.setattr(temoin, "nuit", lambda *a, **k: vus.append(nom) or 0)
    assert temoin.main([nom]) == 0 and vus == [nom]

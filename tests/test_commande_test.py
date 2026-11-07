"""`newskit test` : l'essai du moteur sur le graphe d'exemple, sans frais (07.11.2026). Il remplace dans la
documentation `newskit temoin nuit`, qui reste accepté (le daemon de 0 h 30 de la première édition l'appelle)."""

from __future__ import annotations

import runpy

from newskit import cli


def test_newskit_test_lance_le_temoin_nuit(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    assert cli.main(["test"]) == 0
    assert vus == [("newskit.temoin", ["nuit"])]


def test_les_options_suivent(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    cli.main(["test", "--garder"])
    assert vus == [("newskit.temoin", ["nuit", "--garder"])]


def test_l_ancien_nom_reste_accepte(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    cli.main(["temoin", "nuit"])
    assert vus == [("newskit.temoin", ["nuit"])]

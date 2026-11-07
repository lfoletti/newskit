"""Les commandes de `newskit` en anglais (07.11.2026) : noms, sous-commandes, options et valeurs. Les noms français
restent acceptés : la première édition et ses daemons les appellent."""

from __future__ import annotations

import runpy

import pytest

from newskit import cli


@pytest.fixture
def lance(monkeypatch):
    vus = []
    monkeypatch.setattr(runpy, "run_module", lambda nom, **k: vus.append((nom, list(cli.sys.argv[1:]))))
    return vus


@pytest.mark.parametrize("argv, attendu", [
    (["run", "--graph", "g", "--no-commit", "--no-email"],
     ("newskit.routine_hebdo", ["--graphe", "g", "--sans-commit", "--sans-courriel"])),
    (["new-graph", "ruchers", "--label", "Ruchers", "--domain", "beekeeping", "--subjects", "a ; b", "--check"],
     ("newskit.nouveau_graphe", ["ruchers", "--libelle", "Ruchers", "--domaine", "beekeeping", "--sujets", "a ; b",
                                 "--controle"])),
    (["query", "--graph", "g", "search", "straw hive", "--limit", "5"],
     ("newskit.interroger_graphe", ["--graphe", "g", "chercher", "straw hive", "--limite", "5"])),
    (["costs", "--by", "graph", "--since", "2026-11-01"],
     ("newskit.couts", ["--par", "graphe", "--depuis", "2026-11-01"])),
    (["costs", "--by=model"], ("newskit.couts", ["--par=modele"])),
])
def test_les_commandes_anglaises(lance, argv, attendu):
    assert cli.main(argv) == 0
    assert lance == [attendu]


@pytest.mark.parametrize("argv, attendu", [
    (["routine", "--graphe", "g"], ("newskit.routine_hebdo", ["--graphe", "g"])),
    (["nouveau-graphe", "x", "--controle"], ("newskit.nouveau_graphe", ["x", "--controle"])),
    (["graphe", "--graphe", "g", "chercher", "x"], ("newskit.interroger_graphe", ["--graphe", "g", "chercher", "x"])),
    (["couts", "--par", "graphe"], ("newskit.couts", ["--par", "graphe"])),
])
def test_les_noms_francais_restent(lance, argv, attendu):
    assert cli.main(argv) == 0
    assert lance == [attendu]


def test_un_terme_cherche_n_est_pas_traduit(lance):
    cli.main(["query", "--graph", "graph", "search", "search", "path", "--limit", "3"])
    assert lance == [("newskit.interroger_graphe", ["--graphe", "graph", "chercher", "search", "path", "--limite", "3"])]

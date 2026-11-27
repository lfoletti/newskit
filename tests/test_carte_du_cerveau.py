"""La carte du domaine (`digerer_graphe`) se lit du graphe du cerveau, sous ses noms (K3, étape 2, 27.11.2026)."""

from __future__ import annotations

import sys

import networkx as nx
import pytest

from newskit import digerer_graphe as dg
from newskit import graphe_cerveau as gc


def _graphe():
    G = nx.Graph()
    G.add_node("entity:p", name="Un papier", node_type="papier", _source="sources/arxiv/p.md")
    G.add_node("entity:auteur", name="Ada", node_type="auteur")
    G.add_node("entity:m", name="Complément", node_type="concept", _source="sources/modele/m.md")
    for i, nom in enumerate(["Memory", "Graph", "Agent", "Retrieval"]):
        G.add_node(f"entity:c{i}", name=nom, node_type="concept", _source="sources/arxiv/p.md")
        G.add_edge("entity:p", f"entity:c{i}", edge_type="introduces", confidence="EXTRACTED")
    G.add_edge(
        "entity:c0", "entity:c1", edge_type="semantically_similar_to", confidence="INFERRED", confidence_score="0.9"
    )
    G.add_edge(
        "entity:c2", "entity:c3", edge_type="semantically_similar_to", confidence="INFERRED", confidence_score="0.85"
    )
    G.add_edge(
        "entity:c1",
        "entity:c2",
        edge_type="semantically_similar_to",
        confidence="INFERRED",
        confidence_score="pas un nombre",
    )
    G.add_edge("entity:c0", "entity:m", edge_type="relates_to", confidence="AMBIGUOUS", _source="sources/modele/m.md")
    G.add_edge("entity:auteur", "entity:p", edge_type="wrote", confidence="EXTRACTED")
    return G


def test_les_noms_et_genres_de_la_carte_sont_ceux_du_cerveau():
    G = _graphe()
    assert (dg._label(G, "entity:c0"), dg._type(G, "entity:c0")) == ("Memory", "concept")
    assert dg._label(G, "entity:m") == "Complément ·modèle"
    G.add_node("entity:sans")
    assert (dg._type(G, "entity:sans"), dg._label(G, "entity:sans")) == ("?", "entity:sans")


def test_les_communautes_ne_prennent_que_les_notions():
    G = _graphe()
    membres = {m for _, ms, _ in dg.communautes(G, 5) for m in ms}
    assert membres and membres <= {n for n in G if G.nodes[n]["node_type"] == "concept"}


def _carte(monkeypatch, tmp_path, charger, *args):
    monkeypatch.setattr(gc, "charger", charger)
    monkeypatch.setattr("newskit.config_sources.chemins_du_graphe", lambda g: {"carte": tmp_path / f"{g}.md"})
    monkeypatch.setattr("newskit.config_sources.GRAPHE_PRINCIPAL", "principal")
    monkeypatch.setattr(sys, "argv", ["digerer_graphe", *args])
    monkeypatch.setattr(dg, "RACINE", tmp_path)
    return dg.main()


def test_la_carte_se_lit_du_cerveau_du_graphe(monkeypatch, tmp_path):
    vus = []
    assert _carte(monkeypatch, tmp_path, lambda g: vus.append(g) or _graphe(), "--graphe", "g") == 0
    carte = (tmp_path / "g.md").read_text(encoding="utf-8")
    assert vus == ["g"] and "# Carte du domaine — g" in carte
    assert "| semantically_similar_to | 3 |" in carte and "| introduces | 4 |" in carte
    assert "1 nœuds et 1 arêtes viennent de la source « Modèle »" in carte
    # les rapprochements, du plus fort au plus faible, le score lu du texte du cerveau
    assert carte.index("| Memory | Graph | 0.90 |") < carte.index("| Agent | Retrieval | 0.85 |")
    assert "| Graph | Agent | 0.00 |" in carte


def test_sans_nom_la_carte_du_graphe_principal(monkeypatch, tmp_path):
    vus = []
    assert _carte(monkeypatch, tmp_path, lambda g: vus.append(g) or _graphe()) == 0
    assert vus == ["principal"] and (tmp_path / "principal.md").exists()


def test_sans_cerveau_pas_de_carte(monkeypatch, tmp_path, capsys):
    def absent(g):
        raise gc.CerveauAbsent("cerveau absent : g.db")

    assert _carte(monkeypatch, tmp_path, absent, "--graphe", "g") == 1
    assert "cerveau absent" in capsys.readouterr().err and not (tmp_path / "g.md").exists()


@pytest.mark.parametrize(("score", "lu"), [("0.7", 0.7), (None, 0.0), ("x", 0.0), (0.5, 0.5)])
def test_le_score_d_une_relation_se_lit_du_texte(score, lu):
    assert dg._score({"confidence_score": score}) == lu

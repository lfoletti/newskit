"""Le banc et le reporter lisent le graphe du cerveau, sous ses noms (K3, étape 2, 27.11.2026)."""

from __future__ import annotations

import networkx as nx

from newskit import banc
from newskit import interroger_graphe as ig
from newskit import reporter as rp


def _graphe():
    G = nx.Graph()
    G.add_node("entity:mecanisme", name="Mécanisme", node_type="rationale", _source="sources/a/f1.md")
    G.add_node("entity:principe", name="Principe", node_type="rationale")
    G.add_node("entity:a", name="Notion A", node_type="concept")
    G.add_node("entity:b", name="Notion B", node_type="concept")
    G.add_node("entity:fiche", name="Fiche", node_type="article", _source="sources/a/2026-11-01_billet.md")
    G.add_edge("entity:mecanisme", "entity:a", _source="sources/a/f2.md")
    G.add_edge("entity:principe", "entity:b", _source="sources/a/f2.md")
    G.add_edge("entity:a", "entity:b", edge_type="implements", confidence="EXTRACTED", _source="sources/a/f3.md")
    G.add_edge("entity:fiche", "entity:a", edge_type="implements", confidence="EXTRACTED", _source="sources/a/f3.md")
    G.add_edge("entity:b", "entity:fiche", edge_type="mentions", confidence="EXTRACTED", _source="sources/a/f3.md")
    return G


def test_le_banc_pese_les_fiches_par_leurs_principes():
    assert banc.poids_des_fiches_techniques(_graphe()) == {"sources/a/f1.md": 1, "sources/a/f2.md": 2}
    assert banc.poids_des_fiches_techniques(None) == {}


def test_le_banc_tire_les_liens_explicites_entre_deux_notions():
    assert banc.liens_explicites(_graphe()) == [("Notion A", "Notion B", "sources/a/f3.md")]
    assert banc.liens_explicites(None) == []


def test_le_reporter_part_de_la_fiche_du_sujet(monkeypatch):
    vus = []
    monkeypatch.setattr(ig, "cmd_voisinage", lambda G, a: vus.append(a.noeud) or print("voisins"))
    monkeypatch.setattr(ig, "cmd_chercher", lambda G, a: None)
    monkeypatch.setattr(ig, "cmd_passages", lambda G, a: None)
    rp.graphe_d_abord(_graphe(), {"titre": "Un billet", "chemin": "x/2026-11-01_billet.md"}, "g")
    assert vus == [["entity:fiche"]]

"""Le chargeur fond les entités qui portent le même ancien identifiant (K3, étape 2, 27.11.2026).

Un lot de couche 2 nomme une notion par un identifiant et un libellé ; deux lots donnent parfois le même identifiant
sous deux libellés (« National competent authorities », « Autorités nationales »). La composition les fondait par
l'identifiant ; le cerveau ne fond que par le nom, et les garde à part (2 882 identifiants partagés au cerveau
d'ai-governance servi, le 27.11.2026). Lus séparés, ils doublaient la file de la pyramide (2 093 notions de plus à
rédiger, des doublons en tête) et changeaient l'empreinte de synthèses faites. Le chargeur les fond donc en une
entité : la plus reliée garde son nom, les autres noms vont sous `aliases`, les relations se rejoignent.
"""

from __future__ import annotations

import sqlite3

import networkx as nx

from newskit import graphe_cerveau as gc
from newskit import interroger_graphe as ig


def _graphe():
    G = nx.Graph()
    G.add_node(
        "entity:nca",
        name="National competent authorities",
        node_type="concept",
        newskit_id="c_nca",
        aliases=["NCA"],
        _source="a.md",
    )
    G.add_node(
        "entity:autorites",
        name="Autorités nationales",
        node_type="concept",
        newskit_id="c_nca",
        description="Les autorités.",
        _source="b.md",
    )
    G.add_node("entity:ai_act", name="AI Act", node_type="concept", newskit_id="c_ai_act")
    G.add_node("entity:loi", name="Loi", node_type="article")
    G.add_node("entity:seul", name="Seul", node_type="concept")
    G.add_node("entity:gdpr", name="GDPR", node_type="concept")
    G.add_edge("entity:nca", "entity:gdpr", edge_type="relates_to")
    G.add_edge("entity:nca", "entity:ai_act", edge_type="relates_to", _source="a.md")
    G.add_edge("entity:nca", "entity:loi", edge_type="mentions", _source="a.md")
    G.add_edge("entity:autorites", "entity:loi", edge_type="defines", _source="b.md")
    G.add_edge("entity:autorites", "entity:seul", edge_type="relates_to", _source="b.md")
    G.add_edge("entity:autorites", "entity:nca", edge_type="same_as")
    return G


def test_les_entites_d_un_meme_identifiant_n_en_font_qu_une():
    G = _graphe()
    assert gc.fondre_par_identifiant(G) == 1
    assert "entity:autorites" not in G and sorted(G) == [
        "entity:ai_act",
        "entity:gdpr",
        "entity:loi",
        "entity:nca",
        "entity:seul",
    ]
    d = G.nodes["entity:nca"]
    assert d["name"] == "National competent authorities" and d["_source"] == "a.md"  # la plus reliée garde le sien
    assert d["aliases"] == ["NCA", "Autorités nationales"] and d["description"] == "Les autorités."
    assert G.has_edge("entity:nca", "entity:seul") and not G.has_edge("entity:nca", "entity:nca")
    assert G.edges["entity:nca", "entity:loi"]["edge_type"] == "mentions"  # une relation déjà là le reste
    assert "Autorités nationales" in ig.autres_noms(G, "entity:nca")


def test_a_egalite_de_liens_le_premier_identifiant():
    G = nx.Graph()
    G.add_node("entity:b", name="B", node_type="concept", newskit_id="x")
    G.add_node("entity:a", name="A", node_type="concept", newskit_id="x", aliases=["A2"])
    G.add_node("entity:c", name="A", node_type="concept", newskit_id="x", aliases=["A2"])  # rien deux fois
    assert gc.fondre_par_identifiant(G) == 2
    assert list(G) == ["entity:a"] and G.nodes["entity:a"]["aliases"] == ["A2", "B"]


def test_un_identifiant_seul_ne_change_rien():
    G = _graphe()
    G.remove_node("entity:autorites")
    avant = nx.node_link_data(G, edges="links")
    assert gc.fondre_par_identifiant(G) == 0 and nx.node_link_data(G, edges="links") == avant


def test_le_cerveau_se_lit_fondu(tmp_path, monkeypatch):
    vus = []
    monkeypatch.setattr(gc, "fondre_par_identifiant", lambda G: vus.append(G) or 0)
    c = sqlite3.connect(tmp_path / "g.db")
    for sql in gc.SCHEMA_CONNU.values():
        c.execute(sql)
    c.execute("insert into graph_nodes (id, vector, node_type, properties) values ('entity:x', x'', 'concept', '{}')")
    c.commit()
    c.close()
    G = gc.lire(tmp_path / "g.db")
    assert vus == [G]

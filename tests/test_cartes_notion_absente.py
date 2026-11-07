"""Une notion de la pyramide absente du graphe ne fait pas tomber la carte d'un sujet (CHANTIER_GENERIQUE.md, défaut 11).

La garde `if n in G` de `cartes.notions_du_sujet` venait après l'itérable `G.nodes[n].get("aussi")`, déjà évalué :
une notion synthétisée sans nœud dans le graphe (graphe reconstruit après la pyramide) levait KeyError.
"""

from __future__ import annotations

import networkx as nx

from newskit import cartes


def test_une_notion_hors_du_graphe_garde_son_libelle(monkeypatch):
    monkeypatch.setattr(cartes, "canon_du_sujet", lambda graphe, i: [])
    G = nx.Graph()
    G.add_node("n1", aussi=["agent autonome"])
    notions = {
        "n1": {"label": "Agents", "phrases": [{"texte": "les agents et leurs risques"}]},
        "n2": {"label": "Risques des agents", "phrases": [{"texte": "risques"}]},  # absente de G
    }
    choisies = cartes.notions_du_sujet("essai", G, notions, 0, "les risques des agents", None, combien=5)
    assert set(choisies) == {"n1", "n2"}


def test_l_alias_d_une_notion_presente_compte_toujours(monkeypatch):
    """La garde ne doit pas jeter les alias des notions présentes : le canon se résout par un alias."""
    monkeypatch.setattr(cartes, "canon_du_sujet", lambda graphe, i: ["agent autonome"])
    G = nx.Graph()
    G.add_node("n1", aussi=["agent autonome"])
    notions = {
        "n1": {"label": "Agents", "phrases": [{"texte": "x"}]},
        "n2": {"label": "Autre sujet", "phrases": [{"texte": "autre sujet"}]},
    }
    G.add_node("n2")
    # n2 partage les mots du sujet : sans l'alias, elle passerait devant n1.
    choisies = cartes.notions_du_sujet("essai", G, notions, 0, "autre sujet", None, combien=5)
    assert choisies[0] == "n1"

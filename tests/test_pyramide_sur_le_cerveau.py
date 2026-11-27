"""La pyramide lit le graphe du cerveau sous les clés de ses synthèses (K3, étape 2, 27.11.2026).

`synthese/notions.json` range chaque synthèse sous l'ancien identifiant de sa notion, et le versement la rattache au
cerveau par lui : les clés changeront avec le versement, à l'étape 3, pour que les synthèses ne se reversent pas deux
fois. D'ici là, `synthese.charger_graphe` rend le graphe du cerveau où chaque entité qui porte un ancien identifiant est
rangée sous lui (11 352 synthèses sur 11 353 d'ai-governance la retrouvent, le 27.11.2026 ; la dernière n'a plus
d'entité). Une notion neuve reçoit donc une clé de la même forme que les synthèses faites.
"""

from __future__ import annotations

import networkx as nx

from newskit import graphe_cerveau as gc
from newskit import synthese as sy


def _graphe():
    G = nx.Graph()
    G.add_node("entity:gpai", name="GPAI models", node_type="concept", newskit_id="concept_gpai")
    G.add_node("entity:ai_act", name="AI Act", node_type="concept", newskit_id="concept_ai_act")
    G.add_node("entity:neuve", name="Neuve", node_type="concept")
    G.add_edge("entity:gpai", "entity:ai_act")
    return G


def test_chaque_entite_d_un_ancien_identifiant_est_rangee_sous_lui():
    G = gc.sous_anciennes_cles(_graphe())
    assert sorted(G) == ["concept_ai_act", "concept_gpai", "entity:neuve"]
    assert G.nodes["concept_gpai"]["name"] == "GPAI models" and G.has_edge("concept_gpai", "concept_ai_act")


def test_la_synthese_charge_le_cerveau_sous_les_anciennes_cles(monkeypatch):
    vus = []
    monkeypatch.setattr(sy.ig, "charger", lambda g: vus.append(g) or _graphe())
    assert "concept_gpai" in sy.charger_graphe("g") and vus == ["g"]

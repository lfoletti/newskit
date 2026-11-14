"""La composition du graphe, la validation des extractions et les communautés de la carte sans graphify (chantier E2,
14.11.2026). Les attendus sont figés dans fixtures/sans_graphify/, produits par graphify 0.9.20 sur des entrées qui
passent par chaque chemin que les graphes réels empruntent (mesuré sur les dix graphes du registre) : nœuds, arêtes,
attributs et ordre identiques, mêmes erreurs de schéma, mêmes communautés."""

import copy
import json
from pathlib import Path

import networkx as nx
import pytest
from networkx.readwrite import json_graph

from newskit import communautes, composition

FIXTURES = Path(__file__).parent / "fixtures" / "sans_graphify"
CAS = json.loads((FIXTURES / "composition.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("nom", sorted(CAS["cas"]))
def test_la_composition_est_celle_de_graphify(nom):
    cas = CAS["cas"][nom]
    G = composition.composer(copy.deepcopy(cas["extractions"]), racine=CAS["racine"])
    obtenu = json_graph.node_link_data(G, edges="links")
    assert json.dumps(obtenu, ensure_ascii=False, indent=1) == json.dumps(cas["attendu"], ensure_ascii=False, indent=1)


def test_la_composition_dit_combien_de_noeuds_elle_fond(capsys):
    composition.composer(copy.deepcopy(CAS["cas"]["deduplication"]["extractions"]), racine=CAS["racine"])
    assert "4 nœud(s) fondus à la composition (1 exact(s), 3 proche(s))" in capsys.readouterr().out


@pytest.mark.parametrize("cas", json.loads((FIXTURES / "validation.json").read_text(encoding="utf-8")))
def test_la_validation_rend_les_erreurs_de_graphify(cas):
    # L'ordre des champs manquants d'un même nœud suit l'itération d'un ensemble, comme chez graphify : il dépend de
    # PYTHONHASHSEED (fixé à 0 par la routine) ; la liste se compare donc triée.
    assert sorted(composition.valider_extraction(copy.deepcopy(cas["extraction"]))) == sorted(cas["erreurs"])


def test_les_communautes_sont_celles_de_graphify():
    f = json.loads((FIXTURES / "communautes.json").read_text(encoding="utf-8"))
    H = json_graph.node_link_graph(f["graphe"], edges="links")
    comms = communautes.decouper(H)
    assert {str(k): v for k, v in comms.items()} == f["communautes"]
    assert {str(k): v for k, v in communautes.etiqueter(H, comms).items()} == f["etiquettes"]
    assert {str(k): v for k, v in communautes.cohesions(H, comms).items()} == f["cohesions"]


def test_une_communaute_sans_arete_se_scinde_en_singletons():
    H = nx.Graph()
    H.add_nodes_from(["b", "a", "c"])
    assert communautes.scinder(H, ["b", "a", "c"]) == [["a"], ["b"], ["c"]]


def test_la_cohesion_d_un_singleton_est_pleine():
    assert communautes.cohesion(nx.Graph(), ["seul"]) == 1.0


def test_une_communaute_sans_membre_dans_le_graphe_garde_un_nom():
    assert communautes.etiqueter(nx.Graph(), {3: ["absent"]}) == {3: "Community 3"}

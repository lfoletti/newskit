"""Sur une même paire de nœuds, l'arête d'un autre producteur écrite en dernier remplace la précédente (15.11.2026).

graphify 0.9.20, et la composition reprise de lui, ajoutaient les champs de la seconde arête à la première : une arête
du modèle posée sur une arête de structure gardait `_origin: "ast"` et gagnait la citation (`evidence`) et le
contributeur du modèle. Le graphe de gouvernance du 14.11.2026 en comptait 29 ; le puits les versait comme arêtes
déterministes et perdait leur citation. Entre deux arêtes du même producteur, la fusion reste : sur les extractions
du 14.11.2026, la remplacer aurait ôté sa citation à 778 arêtes du modèle proposées deux fois."""

import copy

from newskit import composition

RACINE = "/racine/essai"


def _noeud(i):
    return {"id": f"concept_{i}", "label": f"Notion {i}", "file_type": "concept", "source_file": "sources/a.md"}


def _composer(relation_modele):
    structure = {
        "nodes": [_noeud("alpha"), _noeud("beta")],
        "edges": [
            {
                "source": "concept_alpha",
                "target": "concept_beta",
                "relation": "contains",
                "confidence": "EXTRACTED",
                "source_file": "sources/a.md",
                "source_location": "L1",
                "_origin": "ast",
            }
        ],
    }
    modele = {
        "nodes": [_noeud("alpha"), _noeud("beta")],
        "edges": [
            {
                "source": "concept_alpha",
                "target": "concept_beta",
                "relation": relation_modele,
                "confidence": "EXTRACTED",
                "source_file": "sources/a.md",
                "evidence": "une phrase de la fiche",
                "contributor": "Claude",
            }
        ],
    }
    G = composition.composer(copy.deepcopy([structure, modele]), racine=RACINE)
    (arete,) = [d for _, _, d in G.edges(data=True)]
    return arete


def test_l_arete_du_modele_ecrite_en_dernier_ne_garde_rien_de_la_structure():
    arete = _composer("references")  # « references » vient après « contains » : le modèle gagne
    assert arete["relation"] == "references"
    assert arete["evidence"] == "une phrase de la fiche" and arete["contributor"] == "Claude"
    assert "_origin" not in arete and "source_location" not in arete


def test_l_arete_de_structure_ecrite_en_dernier_ne_garde_rien_du_modele():
    arete = _composer("cites")  # « cites » vient avant « contains » : la structure gagne
    assert arete["relation"] == "contains" and arete["_origin"] == "ast"
    assert "evidence" not in arete and "contributor" not in arete


def test_deux_aretes_du_modele_sur_la_meme_paire_gardent_la_citation_de_la_premiere():
    arete = {
        "source": "concept_alpha",
        "target": "concept_beta",
        "relation": "references",
        "confidence": "EXTRACTED",
        "source_file": "sources/a.md",
        "contributor": "Claude",
    }
    lot = {"nodes": [_noeud("alpha"), _noeud("beta")], "edges": [{**arete, "evidence": "une phrase de la fiche"}]}
    copie = {"nodes": [_noeud("alpha"), _noeud("beta")], "edges": [arete]}
    G = composition.composer(copy.deepcopy([lot, copie]), racine=RACINE)
    (gardee,) = [d for _, _, d in G.edges(data=True)]
    assert gardee["evidence"] == "une phrase de la fiche"

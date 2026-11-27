"""Les preuves d'une synthèse se lisent du graphe du cerveau, sous ses noms (K3, étape 2, 27.11.2026) : les fiches
par la source (`_source`) des relations et de la notion, la notion nommée par son nom ou un nom fondu (`aliases`), la
ligne « pourquoi » par sa description, le lot d'un texte de référence par l'ancien identifiant de la fiche."""

from __future__ import annotations

import json

import networkx as nx
import pytest

from newskit import synthese as sy

BILLET = "sources/b/2026-11-01_billet.md"
AUTRE = "sources/b/2026-11-02_autre.md"
LOI = "sources/textes/loi/2024-01-01_art.md"


@pytest.fixture
def G(tmp_path, monkeypatch):
    for f, texte in {
        BILLET: "# Titre\n\nLe droit de l'essaim dit qui garde la ruche.\n\nRien ici.\n",
        AUTRE: "Swarm law, again.\n",
        LOI: "# Art. 1\n\nPremier alinéa sans le nom.\n\nSecond alinéa.\n",
    }.items():
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(texte, encoding="utf-8")
    lots = tmp_path / "couche2" / "fiches"
    lots.mkdir(parents=True)
    (lots / "loi_ancienne.json").write_text(
        json.dumps({"nodes": [{"id": "concept_swarm", "rationale": "Le lot le dit."}]}), encoding="utf-8"
    )
    monkeypatch.setattr(sy, "RACINE", tmp_path)
    monkeypatch.setattr(sy.cfg, "chemins_du_graphe", lambda g: {"couche2": tmp_path / "couche2"})
    monkeypatch.setattr(sy.cfg, "graphes_inclus", lambda g: [])
    monkeypatch.setattr(sy.cfg, "dossiers_de_moindre_autorite", lambda: [])
    monkeypatch.setattr(sy.cfg, "dossiers_propres_au_domaine", lambda g: ["sources/b"])
    sy._MOINDRE.clear()
    sy._INDEX_FICHES.clear()
    G = nx.Graph()
    G.add_node(
        "concept_swarm",
        name="Swarm law",
        aliases=["Droit de l'essaim"],
        node_type="concept",
        description="Who owns a swarm.",
        _source=BILLET,
        actualite={"evolue": True},
    )
    G.add_node("concept_seule", name="Lonely", node_type="concept")
    G.add_node("entity:billet", name="Billet", node_type="article", _source=BILLET, date="2026-11-01")
    G.add_node("entity:autre", name="Autre", node_type="article", _source=AUTRE, date="2026-11-02")
    G.add_node(
        "entity:loi",
        name="Art. 1",
        node_type="article",
        _source=LOI,
        statut="en vigueur",
        texte="loi",
        newskit_id="loi_ancienne",
    )
    G.add_edge("concept_swarm", "entity:billet", _source=BILLET)
    G.add_edge("concept_swarm", "entity:autre", _source=AUTRE)
    G.add_edge("concept_swarm", "entity:loi", _source=LOI)
    G.add_edge("concept_seule", "entity:loi", _source=LOI)
    return G


def test_les_preuves_viennent_des_sources_et_des_noms_du_cerveau(G):
    pr = sy.preuves_de(G, "concept_swarm", "g")
    vus = {(p["fiche"], p["ligne"], p["texte"][:24], bool(p.get("pourquoi"))) for p in pr}
    assert (BILLET, 3, "Le droit de l'essaim dit", False) in vus  # nommée par un nom fondu
    assert (AUTRE, 1, "Swarm law, again.", False) in vus
    assert (LOI, 0, "Le lot le dit.", True) in vus  # le lot de la fiche, par son ancien identifiant
    assert (BILLET, 0, "Who owns a swarm.", True) in vus  # la description, rattachée à la fiche de la notion
    assert sy.nombre_de_fiches(G, "concept_swarm") == 3
    assert "Droit de l'essaim" in sy.matiere_de(G, "concept_swarm", pr)


def test_la_file_rare_lit_les_sources_du_cerveau(G):
    # concept_seule : deux fiches du domaine, déjà couvertes par la synthèse de concept_swarm ; concept_neuve : une
    # fiche que rien ne couvre, la plus rare, en tête de la file rare malgré son degré.
    G.add_edge("concept_seule", "entity:billet", _source=BILLET)
    G.add_node("concept_neuve", name="New", node_type="concept")
    G.add_edge("concept_neuve", "entity:x", _source="sources/b/neuve.md")
    neuves = ["concept_seule", "concept_neuve"]
    assert sy.entrelacer_rares(G, {"concept_swarm": {}}, neuves, "g")[0] == "concept_neuve"


def test_le_rattrapage_par_expression_lit_le_nom(G, monkeypatch, capsys, tmp_path):
    (tmp_path / "notions.json").write_text(json.dumps({"notions": {}, "lecture": sy.LECTURE}), encoding="utf-8")
    monkeypatch.setattr(sy, "dossier", lambda g: tmp_path)
    monkeypatch.setattr(sy, "charger_graphe", lambda g: G)
    monkeypatch.setattr(sy, "notions_eligibles", lambda G: ["concept_swarm", "concept_seule"])
    monkeypatch.setattr(sy.couts, "en_limite", lambda: True)
    sy.mettre_a_jour("g", 10, "modele", seulement="law")
    assert "1 notion(s) éligible(s) retenue(s) — Swarm law" in capsys.readouterr().out


def test_les_passages_lisent_les_fiches_du_cerveau(G, monkeypatch):
    monkeypatch.setattr(sy.cfg, "lit_cortexdb", lambda g: False)
    monkeypatch.setattr(sy, "passages_hybrides", lambda g, q, c: None)
    lus = sy.passages_pour("g", "Que dit le Swarm law ?", 3, G=G)
    assert (AUTRE, 1) in {(f, ligne) for f, ligne, _ in lus}

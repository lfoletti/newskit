"""Le graphe d'un graphe actif se lit dans son cerveau, sous ses noms (K3, étape 1).

`graphe_cerveau.charger` ouvre le fichier du cerveau en lecture seule et rend un graphe networkx dont les nœuds sont
les entités du cerveau et les arêtes ses relations, sans traduction des noms. Les documents, les morceaux et leurs liens
restent hors du graphe, comme les faits retirés ou clos. Un schéma inconnu est refusé : une montée de CortexDB échoue
en clair plutôt que de rendre un graphe faux."""

from __future__ import annotations

import json
import sqlite3

import pytest

from newskit import graphe_cerveau as gc

RETIRE = "2026-11-20 10:00:00 +0000 UTC"


def cerveau(chemin, noeuds=(), aretes=(), schema=None):
    c = sqlite3.connect(chemin)
    for sql in schema or gc.SCHEMA_CONNU.values():
        c.execute(sql)
    for i, ty, props, *retrait in noeuds:
        c.execute(
            "insert into graph_nodes (id, vector, content, node_type, properties, retracted_at) values (?, ?, ?, ?, ?, ?)",
            (i, b"", props.get("name"), ty, json.dumps(props), retrait[0] if retrait else None),
        )
    for k, (de, a, ty, poids, props, *clos) in enumerate(aretes):
        c.execute(
            "insert into graph_edges (id, from_node_id, to_node_id, edge_type, weight, properties, valid_to) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (f"e{k}", de, a, ty, poids, json.dumps(props), clos[0] if clos else None),
        )
    c.commit()
    c.close()
    return chemin


FACENET = {
    "name": "FaceNet",
    "_source": "sources/a.md",
    "description": "Une perte triplet.",
    "newskit_id": "concept_facenet",
}
NOEUDS = [
    ("doc:abc", "document", {"name": "Une fiche", "_source": "sources/a.md"}),
    ("chunk:abc:000", "chunk", {}),
    ("entity:facenet", "concept", FACENET),
    ("entity:un_papier", "papier", {"name": "Un papier", "url": "https://arxiv.org/abs/1"}),
    ("entity:une_page", "document", {"name": "Une page"}),
    ("entity:sans_proprietes", "concept", {}),
    ("entity:retiree", "concept", {"name": "Retirée"}, RETIRE),
]
ARETES = [
    ("doc:abc", "chunk:abc:000", "has_chunk", 1.0, {}),
    ("chunk:abc:000", "entity:facenet", "mentions", 1.0, {}),
    ("entity:un_papier", "entity:facenet", "implements", 0.5, {"_source": "sources/a.md", "confidence": "INFERRED"}),
    ("entity:un_papier", "entity:une_page", "references", 1.0, {}),
    ("entity:facenet", "entity:une_page", "references", 1.0, {}, RETIRE),
    ("entity:facenet", "entity:retiree", "references", 1.0, {}),
    ("entity:facenet", "doc:abc", "cite", 1.0, {}),
]


@pytest.fixture
def plein(tmp_path):
    return cerveau(tmp_path / "g.db", NOEUDS, ARETES)


def test_le_graphe_porte_les_entites_et_les_relations_sous_les_noms_du_cerveau(plein):
    G = gc.lire(plein)
    assert G.nodes["entity:facenet"] == {"node_type": "concept", **FACENET}
    assert G.nodes["entity:sans_proprietes"] == {"node_type": "concept"}
    assert G.edges["entity:un_papier", "entity:facenet"] == {
        "edge_type": "implements",
        "weight": 0.5,
        "from_node_id": "entity:un_papier",
        "to_node_id": "entity:facenet",
        "_source": "sources/a.md",
        "confidence": "INFERRED",
    }
    assert not G.is_directed()


def test_le_texte_et_les_faits_retires_restent_hors_du_graphe(plein):
    G = gc.lire(plein)
    # une entité typée document par une extraction reste une entité ; seuls les documents des fiches sortent
    assert sorted(G.nodes) == ["entity:facenet", "entity:sans_proprietes", "entity:un_papier", "entity:une_page"]
    assert sorted(map(sorted, G.edges)) == [
        ["entity:facenet", "entity:un_papier"],
        ["entity:un_papier", "entity:une_page"],
    ]


def test_les_colonnes_du_cerveau_l_emportent_sur_les_proprietes_du_meme_nom(tmp_path):
    """Le versement répète le poids en propriété, en texte ; un nœud peut porter une propriété `node_type`."""
    p = cerveau(
        tmp_path / "g.db",
        [("entity:a", "concept", {"node_type": "autre"}), ("entity:b", "concept", {})],
        [("entity:a", "entity:b", "references", 0.5, {"weight": "1.0", "edge_type": "x", "from_node_id": "entity:b"})],
    )
    G = gc.lire(p)
    assert G.nodes["entity:a"]["node_type"] == "concept"
    assert {k: G.edges["entity:a", "entity:b"][k] for k in ("weight", "edge_type", "from_node_id")} == {
        "weight": 0.5,
        "edge_type": "references",
        "from_node_id": "entity:a",
    }


def test_deux_relations_entre_les_memes_entites_gardent_la_derniere_versee(tmp_path):
    """Le graphe en mémoire n'a qu'une arête par paire, comme le graphe composé : la dernière par identifiant."""
    p = cerveau(
        tmp_path / "g.db",
        [("entity:a", "concept", {}), ("entity:b", "concept", {})],
        [("entity:a", "entity:b", "references", 1.0, {}), ("entity:b", "entity:a", "implements", 1.0, {})],
    )
    assert gc.lire(p).edges["entity:a", "entity:b"]["edge_type"] == "implements"


def test_la_lecture_n_ecrit_pas_dans_le_cerveau(plein):
    with gc._ouvrir(plein) as c, pytest.raises(sqlite3.OperationalError, match="readonly"):
        c.execute("create table essai (a)")


def test_un_cerveau_absent_leve_une_erreur_nommee(tmp_path):
    with pytest.raises(gc.CerveauAbsent, match="g.db"):
        gc.lire(tmp_path / "g.db")
    assert not (tmp_path / "g.db").exists()


@pytest.mark.parametrize(
    "schema",
    [
        [
            gc.SCHEMA_CONNU["graph_nodes"].replace("retracted_at TIMESTAMP)", "retracted_at TIMESTAMP, rang INT)"),
            gc.SCHEMA_CONNU["graph_edges"],
        ],
        [gc.SCHEMA_CONNU["graph_nodes"]],
    ],
    ids=["colonne-en-plus", "table-absente"],
)
def test_un_schema_inconnu_est_refuse(tmp_path, schema):
    p = cerveau(tmp_path / "g.db", schema=schema)
    with pytest.raises(gc.SchemaInconnu, match="graph_"):
        gc.lire(p)


def test_le_schema_connu_est_celui_de_cortexdb():
    """Les colonnes lues sont celles de CortexDB 2.160.0, dans l'ordre (relevées sur un cerveau du 31.10.2026)."""
    assert gc.COLONNES == {
        "graph_nodes": "id vector content node_type properties created_at updated_at valid_from valid_to recorded_at "
        "retracted_at".split(),
        "graph_edges": "id from_node_id to_node_id edge_type weight properties vector created_at valid_from valid_to "
        "recorded_at retracted_at".split(),
    }


def test_charger_lit_le_cerveau_du_graphe(plein, monkeypatch):
    monkeypatch.setattr(gc.cc, "chemin_cerveau", lambda g: plein if g == "g" else None)
    assert "entity:facenet" in gc.charger("g")

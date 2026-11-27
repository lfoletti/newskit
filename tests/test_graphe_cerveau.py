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
    """Le graphe en mémoire n'a qu'une arête par paire, comme le graphe composé : la dernière par identifiant, sauf
    une relation déterministe."""
    p = cerveau(
        tmp_path / "g.db",
        [("entity:a", "concept", {}), ("entity:b", "concept", {})],
        [("entity:a", "entity:b", "references", 1.0, {}), ("entity:b", "entity:a", "implements", 1.0, {})],
    )
    assert gc.lire(p).edges["entity:a", "entity:b"]["edge_type"] == "implements"


def test_une_relation_deterministe_n_est_jamais_remplacee_par_une_relation_du_modele(tmp_path):
    """Mesuré sur ai-governance (27.11.2026) : 1 137 `ecrit_par` de la couche 1 étaient écrasés par une `references`
    du modèle sur la même paire ; l'index du site perdait 644 auteurs. Une relation du modèle cède à une relation
    déterministe, dans les deux ordres ; entre deux relations du même genre, la dernière reste."""
    sq = {"_producer": "x couche 1 (squelette, déterministe)"}
    mo = {"_producer": "x couche 2 (claude -p)"}
    p = cerveau(
        tmp_path / "g.db",
        [
            ("entity:a", "concept", {}),
            ("entity:b", "concept", {}),
            ("entity:c", "concept", {}),
            ("entity:d", "concept", {}),
        ],
        [
            ("entity:a", "entity:b", "ecrit_par", 1.0, sq),
            ("entity:a", "entity:b", "references", 1.0, mo),
            ("entity:c", "entity:a", "references", 1.0, mo),
            ("entity:c", "entity:a", "contains", 1.0, sq),
            ("entity:b", "entity:c", "references", 1.0, mo),
            ("entity:b", "entity:c", "implements", 1.0, mo),
            ("entity:d", "entity:a", "ecrit_par", 1.0, sq),
            ("entity:d", "entity:a", "contains", 1.0, sq),
        ],
    )
    G = gc.lire(p)
    assert G.edges["entity:a", "entity:b"]["edge_type"] == "ecrit_par"
    assert G.edges["entity:a", "entity:c"]["edge_type"] == "contains"
    assert G.edges["entity:b", "entity:c"]["edge_type"] == "implements"
    assert G.edges["entity:a", "entity:d"]["edge_type"] == "contains"


SQ = {"_producer": "x couche 1 (squelette, déterministe)", "_source": "sources/a.md"}
MO = {"_producer": "x couche 2 (claude -p)"}


def test_une_notion_qui_signe_une_oeuvre_a_un_jumeau_auteur(tmp_path):
    """Le cerveau nomme une entité par son nom : l'auteur « Bruce Schneier » et la notion du même nom sont un seul
    nœud, du genre du dernier producteur versé ; l'index du site perdait 640 auteurs (ai-governance, 27.11.2026).
    Le jumeau auteur reprend les `ecrit_par` de la couche 1, dans les deux sens ; la notion garde le reste."""
    p = cerveau(
        tmp_path / "g.db",
        [
            (
                "entity:bruce",
                "concept",
                {
                    "name": "Bruce",
                    "newskit_id": "concept_bruce",
                    "description": "Un essayiste.",
                    "_source": "sources/b.md",
                },
            ),
            ("entity:essai", "article", {"name": "Un essai"}),
            ("entity:papier", "papier", {"name": "Un papier"}),
            ("entity:ia", "concept", {"name": "IA"}),
            ("entity:x", "concept", {"name": "X"}),
            ("entity:carla", "concept", {"name": "Carla"}),
        ],
        [
            ("entity:essai", "entity:bruce", "ecrit_par", 1.0, SQ),
            ("entity:bruce", "entity:papier", "ecrit_par", 1.0, SQ),
            ("entity:bruce", "entity:ia", "conceptually_related_to", 1.0, MO),
            ("entity:x", "entity:ia", "ecrit_par", 1.0, SQ),
            ("entity:ia", "entity:essai", "ecrit_par", 1.0, MO),
            ("entity:essai", "entity:bruce", "references", 1.0, MO),
            ("entity:essai", "entity:papier", "ecrit_par", 1.0, SQ),
            ("entity:papier", "entity:carla", "ecrit_par", 1.0, SQ),
        ],
    )
    G = gc.lire(p)
    j = "entity:bruce#auteur"
    assert G.nodes[j] == {
        "name": "Bruce",
        "node_type": "auteur",
        "newskit_id": "auteur_bruce",
        "_source": "sources/b.md",
    }
    assert sorted(G[j]) == ["entity:essai", "entity:papier"]
    assert (G.edges[j, "entity:essai"]["from_node_id"], G.edges[j, "entity:essai"]["to_node_id"]) == ("entity:essai", j)
    assert (G.edges[j, "entity:papier"]["from_node_id"], G.edges[j, "entity:papier"]["to_node_id"]) == (
        j,
        "entity:papier",
    )
    assert G.nodes["entity:bruce"]["node_type"] == "concept" and sorted(G["entity:bruce"]) == [
        "entity:essai",
        "entity:ia",
    ]
    assert G.edges["entity:bruce", "entity:essai"]["edge_type"] == "references"  # écartée par la couche 1, rendue
    assert "entity:x#auteur" not in G and "entity:ia#auteur" not in G  # sans œuvre au bout, ou lien du modèle
    assert "entity:essai#auteur" not in G and "entity:papier#auteur" not in G  # une œuvre à chaque bout
    assert list(G["entity:carla#auteur"]) == ["entity:papier"]  # l'auteur au second bout


def test_un_auteur_ou_une_publication_que_le_modele_relie_a_un_jumeau_notion(tmp_path):
    """L'inverse : « ChinaTalk », publication de la couche 1, était aussi une notion (125 liens) avant le cerveau. Le
    jumeau notion reprend les relations du modèle et ses textes, même celles que la couche 1 avait écartées."""
    p = cerveau(
        tmp_path / "g.db",
        [
            (
                "entity:chinatalk",
                "publication",
                {
                    "name": "ChinaTalk",
                    "newskit_id": "publication_chinatalk",
                    "description": "Une lettre.",
                    "name_fr": "ChinaTalk",
                    "_source": "s/c.md",
                },
            ),
            ("entity:ada", "auteur", {"name": "Ada", "newskit_id": "auteur_ada"}),
            ("entity:seul", "auteur", {"name": "Seul"}),
            ("entity:ida", "publication", {"name": "Ida"}),
            ("entity:bob", "auteur", {"name": "Bob"}),
            ("entity:dan", "auteur", {"name": "Dan"}),
            ("entity:fay", "auteur", {"name": "Fay"}),
            ("entity:gus", "auteur", {"name": "Gus"}),
            ("entity:essai", "article", {"name": "Un essai"}),
            ("entity:chine", "concept", {"name": "Chine"}),
            ("entity:eve", "auteur", {"name": "Eve"}),  # après « Chine » : le signataire au second bout du lien
        ],
        [
            ("entity:essai", "entity:chinatalk", "publie_dans", 1.0, SQ),
            ("entity:chinatalk", "entity:chine", "references", 1.0, MO),
            ("entity:essai", "entity:ada", "ecrit_par", 1.0, SQ),
            ("entity:seul", "entity:ida", "references", 1.0, MO),
            ("entity:essai", "entity:chinatalk", "references", 1.0, MO),
            ("entity:essai", "entity:ada", "references", 1.0, MO),
            ("entity:essai", "entity:bob", "ecrit_par", 1.0, SQ),
            ("entity:bob", "entity:essai", "ecrit_par", 1.0, MO),
            ("entity:essai", "entity:dan", "ecrit_par", 1.0, MO),
            ("entity:chine", "entity:eve", "references", 1.0, MO),
            ("entity:fay", "entity:chine", "wrote", 1.0, {}),  # sans producteur : pas du modèle
            ("entity:essai", "entity:gus", "ecrit_par", 1.0, SQ),
            ("entity:essai", "entity:gus", "references", 1.0, {}),
        ],
    )
    G = gc.lire(p)
    assert sorted(G["entity:seul#notion"]) + sorted(G["entity:ida"]) == ["entity:ida", "entity:seul#notion"]
    j = "entity:chinatalk#notion"
    assert G.nodes[j] == {
        "name": "ChinaTalk",
        "node_type": "concept",
        "newskit_id": "concept_chinatalk",
        "description": "Une lettre.",
        "name_fr": "ChinaTalk",
        "_source": "s/c.md",
    }
    assert sorted(G[j]) == ["entity:chine", "entity:essai"] and G.edges[j, "entity:chine"]["from_node_id"] == j
    assert (G.edges[j, "entity:essai"]["edge_type"], G.edges[j, "entity:essai"]["to_node_id"]) == ("references", j)
    assert G.nodes["entity:ada#notion"]["newskit_id"] == "concept_ada" and list(G["entity:ada#notion"]) == [
        "entity:essai"
    ]
    assert list(G["entity:chinatalk"]) == ["entity:essai"] and G.nodes["entity:chinatalk"]["node_type"] == "publication"
    assert G.nodes["entity:seul#notion"]["newskit_id"] == "concept_seul"
    assert list(G["entity:eve#notion"]) == ["entity:chine"]  # le signataire au second bout
    assert "entity:fay#notion" not in G and "entity:gus#notion" not in G
    assert (
        "entity:dan#notion" not in G
        and "entity:bob#notion" not in G
        and G.edges["entity:bob", "entity:essai"]["_producer"] == SQ["_producer"]
    )


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

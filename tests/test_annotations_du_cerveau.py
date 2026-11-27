"""Le graphe chargé du cerveau porte ce que la composition posait (K3, étape 2, 27.11.2026).

- Une fiche (article, papier) reçoit l'en-tête de son document (`date`, `plateforme`, `statut`, `version`…) : le
  versement le range dans le document, l'entité de la fiche ne le porte pas.
- La table anglaise d'`alias.json` donne son nom anglais à une notion au nom étranger ; l'original va sous `aussi`.
- La table française (`francais.json`) pose `name_fr` et `description_fr` quand l'original n'a pas changé.
- L'actualité de chaque notion (`diachronie.annoter_graphe`) se calcule sur les noms du cerveau.

Les tables nomment une entité par son identifiant au cerveau ou par l'ancien (`newskit_id`) : la composition les
écrit encore sous l'ancien jusqu'à son retrait (étape 3).
"""

from __future__ import annotations

import json
import sqlite3

from newskit import diachronie, francais
from newskit import graphe_cerveau as gc

LOI = "sources/textes/aiact/2024-07-12_aiact-art-005.md"
LOI_NEUVE = "sources/textes/aiact/2026-07-27_aiact-art-005.md"
BILLET = "sources/blogs/billet.md"


def _cerveau(chemin, noeuds, aretes=()):
    c = sqlite3.connect(chemin)
    for sql in gc.SCHEMA_CONNU.values():
        c.execute(sql)
    for i, ty, props in noeuds:
        c.execute(
            "insert into graph_nodes (id, vector, node_type, properties) values (?, ?, ?, ?)",
            (i, b"", ty, json.dumps(props)),
        )
    for k, (de, a, ty, props) in enumerate(aretes):
        c.execute(
            "insert into graph_edges (id, from_node_id, to_node_id, edge_type, properties) values (?, ?, ?, ?, ?)",
            (f"e{k}", de, a, ty, json.dumps(props)),
        )
    c.commit()
    c.close()
    return chemin


NOEUDS = [
    (
        "doc:loi",
        "document",
        {
            "_source": LOI,
            "date": "2024-07-12",
            "texte": "aiact",
            "version": "2024-07-12",
            "statut": "remplacée",
            "remplacee_par": LOI_NEUVE,
            "partie": "art-005",
            "_content_sha256": "x",
            "url": "https://eur-lex.europa.eu/x",
            "title": "Art. 5",
        },
    ),
    ("doc:billet", "document", {"_source": BILLET, "date": "2026-11-01", "plateforme": "Substack"}),
    ("entity:art_5", "article", {"name": "Art. 5", "_source": LOI, "newskit_id": "art5", "url": "à garder"}),
    ("entity:billet", "article", {"name": "Billet", "_source": BILLET, "newskit_id": "billet"}),
    ("entity:sans_document", "papier", {"name": "Papier", "_source": "sources/arxiv/p.md"}),
    (
        "entity:deployeur",
        "concept",
        {"name": "Betreiber", "description": "Who deploys.", "newskit_id": "concept_b", "_source": LOI},
    ),
    ("entity:fournisseur", "concept", {"name": "Provider", "description": "Who provides.", "_source": BILLET}),
    ("entity:titre", "document", {"name": "Un titre", "_source": BILLET}),
]
ARETES = [
    ("entity:art_5", "entity:deployeur", "defines", {"_source": LOI}),
    ("entity:billet", "entity:fournisseur", "discusses", {"_source": BILLET}),
    ("entity:billet", "entity:titre", "contains", {"_source": BILLET}),
]


def test_une_fiche_recoit_l_en_tete_de_son_document(tmp_path):
    G = gc.lire(_cerveau(tmp_path / "g.db", NOEUDS, ARETES))
    loi = G.nodes["entity:art_5"]
    assert {k: loi.get(k) for k in ("statut", "version", "remplacee_par", "texte", "partie")} == {
        "statut": "remplacée",
        "version": "2024-07-12",
        "remplacee_par": LOI_NEUVE,
        "texte": "aiact",
        "partie": "art-005",
    }
    assert loi["url"] == "à garder"  # l'entité garde ce qu'elle porte
    assert "_content_sha256" not in loi and "title" not in loi  # seul l'en-tête de la fiche passe
    assert G.nodes["entity:billet"]["plateforme"] == "Substack"
    assert "date" not in G.nodes["entity:fournisseur"]  # une notion ne reçoit rien
    assert "date" not in G.nodes["entity:titre"]


def _base(tmp_path, alias=None, fr=None, nom="base"):
    base = tmp_path / nom
    base.mkdir()
    if alias is not None:
        (base / "alias.json").write_text(json.dumps(alias), encoding="utf-8")
    if fr is not None:
        (base / "francais.json").write_text(json.dumps(fr), encoding="utf-8")
    return base


def test_la_table_anglaise_renomme_par_l_ancien_identifiant_ou_le_nouveau(tmp_path):
    G = gc.lire(_cerveau(tmp_path / "g.db", NOEUDS, ARETES))
    table = {"anglais": {"concept_b": "Deployer", "entity:fournisseur": "Provider", "inconnu": "X"}}
    assert gc.appliquer_anglais(G, table["anglais"]) == 1
    assert G.nodes["entity:deployeur"]["name"] == "Deployer"
    assert G.nodes["entity:deployeur"]["aussi"] == ["Betreiber"]


def test_le_francais_se_pose_quand_l_original_n_a_pas_change(tmp_path):
    G = gc.lire(_cerveau(tmp_path / "g.db", NOEUDS, ARETES))
    table = {
        "libelles": {
            "concept_b": ["Déployeur", francais.empreinte("Betreiber")],
            "entity:fournisseur": ["Fournisseur", francais.empreinte("Ancien nom")],
        },
        "textes": {"entity:fournisseur": ["Qui fournit.", francais.empreinte("Who provides.")]},
    }
    assert gc.appliquer_francais(G, table) == 2
    assert G.nodes["entity:deployeur"]["name_fr"] == "Déployeur"
    assert "name_fr" not in G.nodes["entity:fournisseur"]  # traduction d'un autre original
    assert G.nodes["entity:fournisseur"]["description_fr"] == "Qui fournit."


def test_l_actualite_se_lit_des_fiches_du_cerveau(tmp_path, monkeypatch):
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    G = gc.lire(_cerveau(tmp_path / "g.db", NOEUDS, ARETES))
    c = diachronie.annoter_graphe(G)
    a = G.nodes["entity:deployeur"]["actualite"]
    assert (a["attestations"], a["premiere"], a["textes"], a["en_vigueur"]) == (
        1,
        "2024-07-12",
        {"aiact": ["2024-07-12"]},
        False,
    )
    assert (
        a["remplacee_seulement"] is False
    )  # la version en vigueur n'est pas dans le graphe : l'extraction est en retard
    assert G.nodes["entity:fournisseur"]["actualite"]["derniere"] == "2026-11-01"
    assert "actualite" not in G.nodes["entity:titre"]
    assert c["notions"] == 2 and c["reference"] == 1


def test_une_notion_est_attestee_par_la_source_d_une_arete_ou_par_sa_fiche_voisine(tmp_path, monkeypatch):
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    noeuds = [
        *NOEUDS,
        ("entity:ruche", "concept", {"name": "Ruche"}),
        ("entity:essaim", "concept", {"name": "Essaim"}),
    ]
    aretes = [
        *ARETES,
        ("entity:ruche", "entity:fournisseur", "relates_to", {"_source": LOI}),  # l'arête seule nomme la fiche
        ("entity:essaim", "entity:billet", "mentions_in", {}),  # la fiche voisine seule
    ]
    G = gc.lire(_cerveau(tmp_path / "g.db", noeuds, aretes))
    diachronie.annoter_graphe(G)
    assert G.nodes["entity:ruche"]["actualite"]["premiere"] == "2024-07-12"
    assert G.nodes["entity:essaim"]["actualite"]["premiere"] == "2026-11-01"


def _remplacee(tmp_path, texte_en_vigueur):
    """La notion n'est attestée que par la version remplacée ; la version en vigueur est extraite (une notion)."""
    (tmp_path / LOI_NEUVE).parent.mkdir(parents=True)
    (tmp_path / LOI_NEUVE).write_text(texte_en_vigueur, encoding="utf-8")
    noeuds = [
        *NOEUDS,
        ("doc:neuve", "document", {"_source": LOI_NEUVE, "date": "2026-07-27", "statut": "en vigueur"}),
        ("entity:art_5_neuf", "article", {"name": "Art. 5 (2026)", "_source": LOI_NEUVE}),
        ("entity:autre", "concept", {"name": "Autre", "_source": LOI_NEUVE}),
    ]
    aretes = [*ARETES, ("entity:art_5_neuf", "entity:autre", "defines", {"_source": LOI_NEUVE})]
    G = gc.lire(_cerveau(tmp_path / "g.db", noeuds, aretes))
    G.nodes["entity:deployeur"]["aussi"] = ["Betreiberin"]
    diachronie.annoter_graphe(G)
    return G.nodes["entity:deployeur"]["actualite"]


def test_une_notion_que_seule_une_version_remplacee_atteste_est_perimee(tmp_path, monkeypatch):
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    a = _remplacee(tmp_path, "Le fournisseur seul.")
    assert (a["en_vigueur"], a["remplacee_seulement"]) == (False, True)


def test_une_notion_que_le_texte_en_vigueur_nomme_par_un_autre_nom_reste_en_vigueur(tmp_path, monkeypatch):
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    a = _remplacee(tmp_path, "La Betreiberin tient le registre.")
    assert (a["en_vigueur"], a.get("attestee_textuellement"), a["remplacee_seulement"]) == (True, True, False)


def test_charger_rend_le_graphe_annote(tmp_path, monkeypatch):
    p = _cerveau(tmp_path / "g.db", NOEUDS, ARETES)
    base = _base(
        tmp_path,
        alias={"anglais": {"concept_b": "Deployer"}},
        fr={"libelles": {"concept_b": ["Déployeur", francais.empreinte("Deployer")]}, "textes": {}},
    )
    inclus = _base(tmp_path, alias={"anglais": {"entity:fournisseur": "Supplier"}}, nom="inclus")
    monkeypatch.setattr(gc.cc, "chemin_cerveau", lambda g: p)
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    monkeypatch.setattr("newskit.config_sources.chemins_du_graphe", lambda g: {"base": base if g == "g" else inclus})
    monkeypatch.setattr("newskit.config_sources.graphes_inclus", lambda g: ["inclus"])
    G = gc.charger("g")
    d = G.nodes["entity:deployeur"]
    assert (d["name"], d["name_fr"], d["aussi"]) == ("Deployer", "Déployeur", ["Betreiber"])
    assert G.nodes["entity:fournisseur"]["name"] == "Supplier"
    assert "actualite" in d


def test_sans_table_le_graphe_reste_tel_que_le_cerveau_le_porte(tmp_path, monkeypatch):
    p = _cerveau(tmp_path / "g.db", NOEUDS, ARETES)
    base = _base(tmp_path)
    monkeypatch.setattr(gc.cc, "chemin_cerveau", lambda g: p)
    monkeypatch.setattr(diachronie, "RACINE", tmp_path)
    monkeypatch.setattr("newskit.config_sources.chemins_du_graphe", lambda g: {"base": base})
    monkeypatch.setattr("newskit.config_sources.graphes_inclus", lambda g: [])
    G = gc.charger("g")
    assert G.nodes["entity:deployeur"]["name"] == "Betreiber" and "name_fr" not in G.nodes["entity:deployeur"]


def test_une_entite_dont_la_fiche_est_retiree_prend_la_fiche_qui_l_atteste_encore(tmp_path):
    noeuds = [
        *NOEUDS,
        ("entity:sou", "concept", {"name": "Sou", "_source": "sources/blogs/retiree.md"}),
        ("entity:seule", "concept", {"name": "Seule", "_source": "sources/blogs/retiree.md"}),
    ]
    aretes = [*ARETES, ("entity:sou", "entity:fournisseur", "relates_to", {"_source": BILLET})]
    G = gc.lire(_cerveau(tmp_path / "g.db", noeuds, aretes))
    assert G.nodes["entity:sou"]["_source"] == BILLET
    assert G.nodes["entity:seule"]["_source"] == "sources/blogs/retiree.md"  # rien d'autre ne l'atteste
    assert G.nodes["entity:fournisseur"]["_source"] == BILLET  # une fiche présente reste


def test_entre_plusieurs_fiches_qui_attestent_encore_la_premiere_presente(tmp_path):
    noeuds = [*NOEUDS, ("entity:sou", "concept", {"name": "Sou", "_source": "sources/blogs/retiree.md"})]
    aretes = [
        *ARETES,
        ("entity:sou", "entity:titre", "relates_to", {"_source": "sources/aaa/absente.md"}),
        ("entity:sou", "entity:fournisseur", "relates_to", {"_source": LOI}),
        ("entity:sou", "entity:art_5", "relates_to", {"_source": BILLET}),
    ]
    G = gc.lire(_cerveau(tmp_path / "g.db", noeuds, aretes))
    assert G.nodes["entity:sou"]["_source"] == BILLET

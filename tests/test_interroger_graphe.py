"""L'interrogation du graphe lit le cerveau, sous ses noms (K3, étape 2, 27.11.2026).

Le graphe vient de `graphe_cerveau.charger` : une entité porte `name`, `node_type`, `_source`, `description`, et les
noms des nœuds fondus en elle (`aliases`) ; une relation porte `edge_type` et un score de confiance que le cerveau garde
en texte. Chaque sous-commande lit ces noms, et un cerveau absent arrête la lecture, jamais un graphe vide.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import pytest

from newskit import graphe_cerveau as gc
from newskit import interroger_graphe as ig


def _cerveau(chemin, noeuds, aretes):
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


FICHE = "sources/ruchers/dossier/valmiel.md"
NOEUDS = [
    (
        "entity:swarm_law",
        "concept",
        {
            "name": "Swarm law",
            "_source": FICHE,
            "description": "Who owns a swarm that leaves its hive.",
            "aliases": ["Droit de l'essaim", "Swarm law"],
            "aussi": ["Loi des essaims"],
        },
    ),
    ("entity:valmiel", "article", {"name": "Valmiel dispute", "_source": FICHE, "date": "1398-05-01"}),
    ("entity:bell_tower", "concept", {"name": "Bell tower", "_source": FICHE}),
    ("entity:wax_tithe", "concept", {"name": "Wax tithe", "_source": "sources/modele/x.md"}),
    ("entity:brother_anselm", "auteur", {"name": "Brother Anselm"}),
]
ARETES = [
    ("entity:valmiel", "entity:swarm_law", "discusses", {"_source": FICHE, "source_location": "L12"}),
    ("entity:swarm_law", "entity:bell_tower", "applies_to", {"confidence": "INFERRED", "confidence_score": "0.7"}),
    ("entity:swarm_law", "entity:wax_tithe", "relates_to", {"confidence": "AMBIGUOUS", "confidence_score": "?"}),
    ("entity:brother_anselm", "entity:valmiel", "wrote", {}),
]


@pytest.fixture
def G(tmp_path):
    return gc.lire(_cerveau(tmp_path / "g.db", NOEUDS, ARETES))


def _sortie(capsys, fn, G, **kw):
    fn(G, argparse.Namespace(**kw))
    return capsys.readouterr().out


def test_les_noms_et_genres_viennent_du_cerveau(G):
    assert (ig._label(G, "entity:swarm_law"), ig._type(G, "entity:swarm_law")) == ("Swarm law", "concept")
    assert ig.autres_noms(G, "entity:swarm_law") == ["Loi des essaims", "Droit de l'essaim"]
    assert ig.est_du_modele(G, "entity:wax_tithe") and not ig.est_du_modele(G, "entity:bell_tower")


def test_le_score_garde_en_texte_se_lit_comme_un_nombre(G):
    assert ig._conf(G, "entity:swarm_law", "entity:bell_tower") == " ·inferred 0.70"
    assert ig._conf(G, "entity:swarm_law", "entity:wax_tithe") == " ·ambiguous"
    assert ig._provenance(G, "entity:valmiel", "entity:swarm_law") == "valmiel.md L12"


@pytest.mark.parametrize("nom", ["Droit de l'essaim", "Loi des essaims", "swarm law"])
def test_un_nom_fondu_mene_a_son_entite(G, nom):
    assert ig.resoudre(G, nom) == "entity:swarm_law"


def test_chercher_lit_les_noms_fondus(G, capsys):
    out = _sortie(capsys, ig.cmd_chercher, G, termes=["droit"], type=None, limite=5)
    assert "1 nœud(s)" in out and "entity:swarm_law" in out


def test_voisinage_et_chemin_nomment_les_relations_du_cerveau(G, capsys):
    out = _sortie(capsys, ig.cmd_voisinage, G, noeud=["Swarm law"], limite=5)
    assert "--applies_to-->" in out and "Bell tower" in out and "·inferred 0.70" in out
    out = _sortie(capsys, ig.cmd_chemin, G, depuis="Valmiel dispute", vers="Bell tower")
    assert "--discusses-->" in out and "--applies_to--> [INFERRED]" in out


def test_expliquer_et_sources_lisent_description_et_source(G, capsys):
    out = _sortie(capsys, ig.cmd_expliquer, G, noeud=["Swarm law"])
    assert "pourquoi : Who owns a swarm" in out and "applies_to ×1" in out and FICHE in out


def test_fiche_trouve_un_article_par_son_genre(G, capsys):
    out = _sortie(capsys, ig.cmd_fiche, G, chemin="valmiel")
    assert f"{FICHE}  |  1398-05-01" in out and "discusses" in out and "wrote" in out


def test_passages_lit_les_fiches_des_articles(G, capsys, monkeypatch, tmp_path):
    (tmp_path / FICHE).parent.mkdir(parents=True)
    (tmp_path / FICHE).write_text("---\nt: x\n---\n\nThe swarm settled in the bell tower.\n", encoding="utf-8")
    monkeypatch.setattr(ig, "RACINE", tmp_path)
    out = _sortie(capsys, ig.cmd_passages, G, termes=["swarm", "tower"], limite=3, longueur=200)
    assert "Valmiel dispute" in out and "L5" in out


def test_relations_compte_les_genres_et_les_relations_du_cerveau(G, capsys):
    out = _sortie(capsys, ig.cmd_relations, G)
    assert "5 nœuds, 4 arêtes" in out and "applies_to" in out and "auteur" in out


def test_charger_lit_le_cerveau_et_le_passe_partout_du_graphe(G, monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "passe_partout.json").write_text(json.dumps({"empreintes": {"abc": 1}}), encoding="utf-8")
    vus = []
    monkeypatch.setattr(gc, "charger", lambda nom: vus.append(nom) or G)
    monkeypatch.setattr("newskit.config_sources.chemins_du_graphe", lambda nom: {"base": base})
    monkeypatch.setattr("newskit.config_sources.nom_actuel", lambda nom: {"ancien": "neuf"}.get(nom, nom))
    assert ig.charger("ancien") is G and vus == ["neuf"] and ig.PASSE_PARTOUT == {"abc"}


def test_sans_nom_le_graphe_principal(monkeypatch):
    monkeypatch.setattr("newskit.config_sources.GRAPHE_PRINCIPAL", "principal")
    assert ig.nom_du_graphe(None) == "principal"


def test_un_cerveau_absent_arrete_la_lecture(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gc.cc, "chemin_cerveau", lambda nom: tmp_path / f"{nom}.db")
    with pytest.raises(SystemExit) as e:
        ig.charger("g")
    assert e.value.code == 1 and "cerveau absent" in capsys.readouterr().err


@pytest.mark.parametrize(("graphe", "note"), [("g", "g"), ("hors", None)])
def test_main_lit_le_graphe_nomme_et_range_ses_lacunes(G, monkeypatch, capsys, graphe, note):
    monkeypatch.setattr("newskit.config_sources.charger", lambda: {"graphes": {"g": {}}})
    monkeypatch.setattr("newskit.config_sources.nom_actuel", lambda nom: nom)
    monkeypatch.setattr(ig, "charger", lambda nom: G)
    notes = []
    monkeypatch.setattr("newskit.lacunes.noter_sans_bruit", lambda nom, *a: notes.append(nom))
    monkeypatch.delenv("KG_SANS_LACUNE", raising=False)
    monkeypatch.setattr(sys, "argv", ["ig", "--graphe", graphe, "chercher", "rien du tout"])
    monkeypatch.setattr(ig, "NOTER_LACUNE", None)
    assert ig.main() == 0
    assert "0 nœud(s)" in capsys.readouterr().out and notes == ([note] if note else [])

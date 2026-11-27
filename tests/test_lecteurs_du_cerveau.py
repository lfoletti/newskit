"""Les lecteurs des thèmes, de l'avance et du canon lisent le graphe du cerveau, sous ses noms (K3, étape 2,
27.11.2026) : `name`, `node_type`, `_source`. Sans cerveau, ils n'ont pas de graphe, jamais un graphe vide."""

from __future__ import annotations

import networkx as nx
import pytest

from newskit import combler_canon as cc_
from newskit import graphe_cerveau as gc
from newskit import lacunes
from newskit import mesurer_avance as ma
from newskit import themes_emergents as te


def _graphe():
    G = nx.Graph()
    G.add_node("entity:ancienne", name="Old notion", node_type="concept", _source="sources/a/2024-01-01_x.md")
    G.add_node("entity:neuve", name="New notion", node_type="concept", _source="sources/b/2026-11-20_y.md")
    G.add_node("entity:canon", name="Canon notion", node_type="rationale", _source="sources/canon/2026-11-21_c.md")
    G.add_node("entity:ada", name="Ada Lovelace", node_type="auteur")
    G.add_node("entity:p", name="Paper", node_type="papier")
    G.add_edge("entity:neuve", "entity:p", _source="sources/b/2026-11-22_z.md")
    G.add_edge("entity:ancienne", "entity:p", _source="sources/a/2026-11-22_w.md")
    return G


def test_les_themes_lisent_le_cerveau_et_rien_sans_cerveau(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "charger", lambda g: f"cerveau de {g}")
    assert te.charger("g") == "cerveau de g"
    monkeypatch.undo()
    monkeypatch.setattr(gc.cc, "chemin_cerveau", lambda g: tmp_path / "absent.db")
    assert te.charger("g") is None


def test_les_notions_du_graphe_sont_les_noms_des_notions():
    assert sorted(te.notions_du_graphe(_graphe())) == ["Canon notion", "New notion", "Old notion"]


def test_une_notion_posterieure_n_a_que_des_fiches_datees_apres_l_horizon():
    noms = {"sources/b": "Blog B"}
    assert ma.notions_posterieures(_graphe(), "2026-01-01", noms) == [("New notion", "Blog B")]


def test_une_relation_plus_ancienne_que_l_horizon_retire_la_notion_des_posterieures():
    G = _graphe()
    G.add_node("entity:mixte", name="Mixed", node_type="concept", _source="sources/b/2026-11-25_m.md")
    G.add_edge("entity:mixte", "entity:p", _source="sources/b/2024-05-01_vieux.md")
    assert "Mixed" not in [n for n, _ in ma.notions_posterieures(G, "2026-01-01", {})]


class Arret(Exception):
    pass


def test_l_avance_compare_aux_noms_du_cerveau_hors_auteurs(monkeypatch):
    vus = []
    monkeypatch.setattr(te, "charger", lambda g: _graphe())
    monkeypatch.setattr(te, "sujets_declares", lambda g: ["s"])

    def mots(e, n):
        vus.append(e)
        raise Arret

    monkeypatch.setattr(te, "mots", mots)
    with pytest.raises(Arret):
        ma.mesurer("g", "2026-01-01")
    assert vus == ["Old notion"]  # le premier nom, dans l'ordre du graphe


def test_le_canon_se_compare_aux_noms_du_cerveau_hors_auteurs(monkeypatch):
    vus = []
    monkeypatch.setattr(cc_, "lire_etat", lambda g: {})
    monkeypatch.setattr(cc_, "a_traiter", lambda g, etat, plafond: ["terme"])
    monkeypatch.setattr(te, "charger", lambda g: _graphe())

    def candidats(t, etiquettes):
        vus.extend(etiquettes)
        raise Arret

    monkeypatch.setattr(cc_, "candidats", candidats)
    with pytest.raises(Arret):
        cc_.passage("g")
    assert sorted(vus) == ["Canon notion", "New notion", "Old notion", "Paper"]


@pytest.mark.parametrize(("termes", "repond"), [("new", True), ("lovelace", False), ("absent", False)])
def test_une_lacune_est_comblee_par_un_nom_du_cerveau_hors_auteurs(termes, repond):
    assert lacunes.repond(_graphe(), termes) is repond

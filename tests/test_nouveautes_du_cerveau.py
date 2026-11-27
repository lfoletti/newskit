"""Les nouveautés de la lettre se lisent du cerveau et de la date de collecte des fiches (K3, étape 2 ; Lauro,
27.11.2026 : « 1, date de collecte »).

Elles étaient la différence de `graphe.json` avant et après la composition. Une entité du cerveau est neuve quand
toutes les fiches qui l'attestent ont été collectées depuis le passage précédent (`versees_du_graphe`, par git : plus
juste que le jour `collected`, que 56 % des fiches d'ai-governance ne portent pas). Une entité qu'une fiche plus
ancienne atteste existait déjà, renommée ou fondue. Rien ne se garde d'une nuit à l'autre, et une reconstruction du
cerveau, qui réécrit les dates de ses lignes, n'y change rien. Les identifiants sont les anciens tant que la pyramide
les garde (`gc.sous_anciennes_cles`, étape 3).
"""

from __future__ import annotations

import inspect
import json

import networkx as nx
import pytest

from newskit import graphe_cerveau as gc
from newskit import routine_hebdo as rh

BILLET = "sources/b/2026-11-27_billet.md"
VIEUX = "sources/b/2024-01-01_vieux.md"


def _cerveau():
    G = nx.Graph()
    G.add_node("entity:ancienne", name="Old notion", node_type="concept", newskit_id="concept_old", _source=VIEUX)
    G.add_node(
        "entity:neuve",
        name="New notion",
        name_fr="Notion neuve",
        node_type="concept",
        newskit_id="concept_new",
        description="Why it matters.",
        aliases=["Novel notion"],
        _source=BILLET,
    )
    # fondue par une table d'alias : sa fiche d'origine est ancienne, une fiche du jour l'atteste aussi
    G.add_node("entity:renommee", name="Renamed", aliases=["Old notion"], node_type="concept", _source=VIEUX)
    G.add_node("entity:vieille", name="Stale", node_type="concept", _source=VIEUX)
    G.add_node("entity:billet", name="Billet", node_type="article", _source=BILLET)
    G.add_edge("entity:neuve", "entity:billet", _source=BILLET)
    G.add_edge("entity:neuve", "entity:ancienne", _source=BILLET)
    G.add_edge("entity:renommee", "entity:billet", _source=BILLET)
    G.add_edge("entity:vieille", "entity:ancienne", _source=VIEUX)
    return G


@pytest.fixture
def graphe(tmp_path, monkeypatch):
    base = tmp_path / "g"
    base.mkdir()
    ch = {"base": base, "graphe": base / "graphe.json", "nouveautes": base / "nouveautes.json"}
    monkeypatch.setattr(rh.cfg, "chemins_du_graphe", lambda g: ch)
    monkeypatch.setattr(rh.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(gc, "charger", lambda g: _cerveau())
    return ch


def test_une_entite_neuve_n_est_attestee_que_par_des_fiches_du_passage():
    G = gc.sous_anciennes_cles(_cerveau())
    # « Renamed » et « Old notion » : une fiche ancienne les atteste ; « Stale » aussi
    assert sorted(rh.entites_neuves(G, {BILLET})) == ["concept_new", "entity:billet"]
    assert rh.entites_neuves(G, set()) == []
    assert rh.fiches_de(G, "concept_new") == {BILLET}
    # un chemin absolu (fiches du canon, 27.11.2026) se lit relatif au dépôt
    G.add_node("concept_canon", name="Canon", node_type="concept", _source=f"{rh.RACINE}/{BILLET}")
    assert rh.fiches_de(G, "concept_canon") == {BILLET} and "concept_canon" in rh.entites_neuves(G, {BILLET})


def test_les_nouveautes_portent_les_noms_du_cerveau(graphe):
    j = rh.Journal()
    r = rh.nouveautes_du_graphe(j, "g", {BILLET})
    neuves = {x["id"]: x for x in r["notions"]}
    assert list(neuves) == ["concept_new"]
    assert r["identifiants"] == 3  # New, Renamed et Old notion : une fiche du passage les atteste
    x = neuves["concept_new"]
    assert (x["label"], x["label_fr"], x["kind"], x["source"], x["degre"]) == (
        "New notion",
        "Notion neuve",
        "concept",
        BILLET,
        2,
    )
    assert (x["fiches"], x["aussi"], x["rationale"]) == ([BILLET], ["Novel notion"], "Why it matters.")
    hist = json.loads(graphe["nouveautes"].read_text(encoding="utf-8"))["passages"][0]
    assert (hist["notions_neuves"], hist["identifiants_neufs"], hist["fiches_versees"]) == (1, 3, [BILLET])
    assert [n["id"] for n in hist["nouveaux"]] == ["entity:billet", "concept_new"]  # du plus relié au moins relié
    assert any("1 notion(s) neuve(s)" in ligne for ligne in j.lignes)


def test_sans_cerveau_pas_de_nouveautes(graphe, monkeypatch):
    def absent(g):
        raise gc.CerveauAbsent("cerveau absent : g.db")

    monkeypatch.setattr(gc, "charger", absent)
    j = rh.Journal()
    assert rh.nouveautes_du_graphe(j, "g", {BILLET}) is None
    assert any("cerveau absent" in a for a in j.alertes) and not graphe["nouveautes"].exists()


def test_le_theme_d_une_notion_hors_communaute_vient_de_ses_voisines():
    G = nx.Graph()
    G.add_node("a", name="A", node_type="concept")
    G.add_node("b", name="B", node_type="concept")
    G.add_node("auteur", name="Ada", node_type="auteur")
    G.add_edge("a", "b")
    G.add_edge("auteur", "b")
    themes = rh.heriter_themes(G, {"b": "Thème B"})
    assert themes == {"b": "Thème B", "a": "Thème B"}  # un auteur n'hérite pas


def test_la_taille_du_graphe_est_celle_du_cerveau(graphe, monkeypatch):
    assert rh.taille_du_graphe("g") == (5, 4)

    def absent(g):
        raise gc.CerveauAbsent("x")

    monkeypatch.setattr(gc, "charger", absent)
    assert rh.taille_du_graphe("g") is None


def test_les_nouveautes_viennent_apres_la_reconstruction_du_cerveau():
    src = inspect.getsource(rh.main)
    assert src.index('cerveau_cortexdb(j, g, "" if g == cfg.GRAPHE_PRINCIPAL') < src.index("nouveautes_du_graphe(")
    assert "taille_du_graphe(" in src and '["graphe"].read_text' not in src and "etat_du_graphe" not in src

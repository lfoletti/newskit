"""Les doublons de notions et la traduction française lisent le graphe du cerveau (K3, étape 2, 27.11.2026), sous
les anciens identifiants que leurs tables (`alias.json`, `francais.json`) gardent jusqu'à l'étape 3 : une paire
tranchée ou une traduction faite reste trouvée par sa clé."""

from __future__ import annotations

import networkx as nx

from newskit import banc_decision as bd
from newskit import fondre_doublons as fd
from newskit import graphe_cerveau as gc


def _cerveau():
    G = nx.Graph()
    G.add_node("entity:a", name="Alpha", node_type="concept", newskit_id="concept_a")
    G.add_node("entity:b", name="Beta", node_type="concept", newskit_id="concept_b")
    G.add_node("entity:c", name="Gamma", node_type="concept")
    G.add_edge("entity:a", "entity:b")
    G.add_edge("entity:a", "entity:c")
    return G


def test_les_doublons_lisent_le_cerveau_sous_les_anciennes_cles(monkeypatch):
    monkeypatch.setattr(gc, "charger", lambda g: _cerveau())
    G = fd.charger_graphe("g")
    assert sorted(G) == ["concept_a", "concept_b", "entity:c"]
    label, vois, deg = fd.contexte(G)
    assert label["concept_a"] == "Alpha" and sorted(vois["concept_a"]) == ["Beta", "Gamma"] and deg["concept_a"] == 2


def test_sans_cerveau_rien_a_fondre(monkeypatch, capsys):
    def absent(g):
        raise gc.CerveauAbsent("x")

    monkeypatch.setattr(gc, "charger", absent)
    assert fd.charger_graphe("g") is None
    assert fd.passage("g", controle=True, plafond=1) == 1 and "pas de cerveau" in capsys.readouterr().err


def test_le_jeu_des_doublons_nomme_les_notions_du_cerveau(monkeypatch):
    monkeypatch.setattr(gc, "charger", lambda g: _cerveau())
    monkeypatch.setattr(fd, "lire_alias", lambda g: {"alias": {"concept_b": "concept_a"}, "refuses": {}})
    monkeypatch.setattr(fd, "contexte_des_lots", lambda g: ({}, {}))
    paires, label, vois = bd.jeu_doublons("g")
    assert label["concept_a"] == "Alpha" and paires[0]["a"] == "concept_b"


def test_le_jeu_des_doublons_sans_cerveau_part_des_lots(monkeypatch):
    def absent(g):
        raise gc.CerveauAbsent("x")

    monkeypatch.setattr(gc, "charger", absent)
    monkeypatch.setattr(fd, "lire_alias", lambda g: {"alias": {}, "refuses": {}, "libelles": {"concept_z": "Zeta"}})
    monkeypatch.setattr(fd, "contexte_des_lots", lambda g: ({"concept_y": "Ypsilon"}, {}))
    _, label, _ = bd.jeu_doublons("g")
    assert label == {"concept_y": "Ypsilon", "concept_z": "Zeta"}


def test_les_doublons_de_la_nuit_se_cherchent_quand_le_cerveau_existe(monkeypatch, tmp_path):
    from newskit import cerveau_cortexdb as cc
    from newskit import routine_hebdo as rh

    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / f"{g}.db")
    assert rh.doublons_a_lire("g") is False
    (tmp_path / "g.db").write_bytes(b"")
    assert rh.doublons_a_lire("g") is True


def test_le_francais_traduit_le_nom_et_la_description_du_cerveau():
    from newskit import francais as fr

    n = {"id": "concept_a", "name": "Swarm", "description": "A cluster of bees.", "node_type": "concept"}
    assert fr.a_traduire([n], {"libelles": {}, "textes": {}}) == [
        ("concept_a", "l", "Swarm"),
        ("concept_a", "t", "A cluster of bees."),
    ]


def _traduits(monkeypatch, G):
    from newskit import couts

    vus = []
    monkeypatch.setattr(fd, "vocabulaire", lambda: {"bee"})
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr("newskit.config_sources.graphe_declare", lambda g: {"libelle": "Ruchers"})
    monkeypatch.setattr(fd.te, "appeler_claude", lambda prompt, *a, **k: vus.append(prompt) or {"libelles": []})
    fd.traduire("ruchers", G, {"alias": {}, "anglais": {}})
    return "\n".join(vus)


def test_le_vocabulaire_anglais_se_lit_des_noms_du_cerveau(monkeypatch):
    G = nx.Graph()
    G.add_node("concept_a", name="Pursuit of the swarm", node_type="concept")
    G.add_node("concept_b", name="swarm pursuit", node_type="concept")
    G.add_node("concept_c", name="ruche paille", node_type="concept")
    p = _traduits(monkeypatch, G)
    assert "ruche paille" in p and "swarm pursuit" not in p


def test_seule_une_notion_sous_un_ancien_identifiant_se_traduit(monkeypatch):
    G = nx.Graph()
    G.add_node("concept_a", name="ruche paille", node_type="concept")
    G.add_node("entity:b", name="ruche osier", node_type="concept")
    p = _traduits(monkeypatch, G)
    assert "ruche paille" in p and "ruche osier" not in p


def test_les_paires_les_plus_reliees_se_tranchent_d_abord(monkeypatch):
    from newskit import synthese

    def sans_encodeur():
        raise RuntimeError("pas d'encodeur dans le test")

    monkeypatch.setattr(synthese, "encodeur", sans_encodeur)
    G = nx.Graph()
    for i, l in (("concept_a", "Deepfake"), ("concept_b", "Deepfakes"), ("concept_c", "Trilogue")):
        G.add_node(i, name=l, node_type="concept")
    G.add_node("concept_d", name="Trilogues", node_type="concept")
    for k in range(3):
        G.add_node(f"concept_v{k}", name=f"Voisin {k}", node_type="concept")
        G.add_edge("concept_c", f"concept_v{k}")
    paires = fd.candidats("g", G, {"alias": {}, "refuses": []})
    assert [sorted(p[:2]) for p in paires][:2] == [["concept_c", "concept_d"], ["concept_a", "concept_b"]]


def test_garder_le_francais_prend_l_original_francais_d_un_nom_traduit():
    from newskit import francais as fr

    n = {"id": "concept_a", "name": "Beekeeping law", "aussi": ["Droit des ruchers"], "node_type": "concept"}
    table = {"libelles": {}, "textes": {}}
    assert fr.garder_le_francais([n], table) == 1
    assert table["libelles"]["concept_a"] == ["Droit des ruchers", fr.empreinte("Beekeeping law")]

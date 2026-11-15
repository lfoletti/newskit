"""Les tendances d'un graphe (mainteneur, 15.11.2026 : « accueil sur "trending topics" »).

Un graphe qui déclare `tendances: {jours: 30, max: 8}` garde dans `tendances.json` les notions que plusieurs sources
indépendantes ont attestées pendant la fenêtre. Aucun appel au modèle : le compte se lit du graphe composé."""

from __future__ import annotations

import copy
import json

import networkx as nx
import pytest

from newskit import config_sources as cfg
from newskit import tendances as td

JUSQU = "2026-11-15"


def graphe(*fiches, noms=()):
    """Un graphe composé : (chemin de la fiche, genre, date, notions attestées) par fiche ; `noms` : (libellé, genre)
    des nœuds d'auteur, d'entité ou de publication."""
    G = nx.Graph()
    for chemin, genre, date, notions in fiches:
        G.add_node(chemin, kind=genre, source_file=chemin, date=date, label=chemin, file_type="document")
        for n in notions:
            if n not in G:
                G.add_node(n, label=n, file_type="concept", source_file=chemin)
            G.add_edge(chemin, n, source_file=chemin)
    for i, (libelle, genre) in enumerate(noms):
        G.add_node(f"nom{i}", label=libelle, kind=genre, file_type="concept")
    return G


def noms_de(G, **k):
    return [t["notion"] for t in td.compter(G, JUSQU, **k)]


# --- le compte


def test_une_notion_attestee_par_deux_sources_dans_la_fenetre_est_une_tendance():
    G = graphe(
        ("sources/a/2026-11-01_x.md", "article", "2026-11-01", ["RLVR"]),
        ("sources/b/2026-11-10_y.md", "article", "2026-11-10", ["RLVR"]),
        ("sources/b/2026-11-12_z.md", "article", "2026-11-12", ["RLVR"]),
    )
    assert td.compter(G, JUSQU) == [{"notion": "RLVR", "sources": 2, "fiches": 3}]


def test_une_seule_source_ne_fait_pas_une_tendance():
    G = graphe(*[(f"sources/a/2026-11-{j:02d}_x.md", "article", f"2026-11-{j:02d}", ["Asta"]) for j in range(1, 9)])
    assert td.compter(G, JUSQU) == []
    assert noms_de(G, min_sources=1) == ["Asta"]


def test_chaque_papier_compte_pour_une_source_un_dossier_d_articles_pour_une_seule():
    G = graphe(
        ("sources/arxiv/2026-11-01_1.md", "papier", "2026-11-01", ["GRPO"]),
        ("sources/arxiv/2026-11-02_2.md", "papier", "2026-11-02", ["GRPO"]),
        ("sources/blog/2026-11-01_1.md", "article", "2026-11-01", ["Docker"]),
        ("sources/blog/2026-11-02_2.md", "article", "2026-11-02", ["Docker"]),
    )
    assert td.compter(G, JUSQU) == [{"notion": "GRPO", "sources": 2, "fiches": 2}]


@pytest.mark.parametrize(
    "date, tenue",
    [("2026-10-16", False), ("2026-10-17", True), ("2026-11-15", True), ("2026-11-16", False), ("", False)],
)
def test_la_fenetre_compte_les_jours_jusqu_au_jour_de_reference_compris(date, tenue):
    G = graphe(
        ("sources/a/x.md", "article", date, ["LoRA"]),
        ("sources/b/y.md", "article", "2026-11-10", ["LoRA"]),
    )
    assert bool(td.compter(G, JUSQU, jours=30)) is tenue


def test_la_fenetre_se_regle_en_jours():
    G = graphe(
        ("sources/a/x.md", "article", "2026-11-01", ["LoRA"]),
        ("sources/b/y.md", "article", "2026-11-14", ["LoRA"]),
    )
    assert noms_de(G, jours=30) == ["LoRA"] and noms_de(G, jours=7) == []


def test_les_sources_passent_avant_les_fiches_puis_le_libelle():
    G = graphe(
        ("sources/a/1.md", "article", "2026-11-01", ["RAG", "DPO", "BM25"]),
        ("sources/a/2.md", "article", "2026-11-02", ["RAG"]),
        ("sources/a/3.md", "article", "2026-11-03", ["RAG"]),
        ("sources/b/1.md", "article", "2026-11-01", ["RAG", "DPO", "BM25", "JEV"]),
        ("sources/c/1.md", "article", "2026-11-01", ["JEV"]),
        ("sources/d/1.md", "article", "2026-11-01", ["JEV"]),
    )
    assert noms_de(G) == ["JEV", "RAG", "BM25", "DPO"]


def test_la_liste_s_arrete_a_la_borne():
    G = graphe(
        ("sources/a/1.md", "article", "2026-11-01", ["A", "B", "C"]),
        ("sources/b/1.md", "article", "2026-11-01", ["A", "B", "C"]),
    )
    assert noms_de(G, maximum=2) == ["A", "B"]


@pytest.mark.parametrize("genre", ["auteur", "entite", "publication"])
def test_un_nom_d_auteur_d_entite_ou_de_publication_n_est_pas_une_tendance(genre):
    fiches = (
        ("sources/a/1.md", "article", "2026-11-01", ["Eugene Yan", "RAG"]),
        ("sources/b/1.md", "article", "2026-11-01", ["Eugene Yan", "RAG"]),
    )
    assert noms_de(graphe(*fiches)) == ["Eugene Yan", "RAG"]
    assert noms_de(graphe(*fiches, noms=[("eugene  YAN", genre)])) == ["RAG"]
    assert noms_de(graphe(*fiches, noms=[("Eugene Yan", "depot")])) == ["Eugene Yan", "RAG"]


def test_seules_les_notions_comptent():
    G = graphe(
        ("sources/a/1.md", "article", "2026-11-01", ["RAG", "Ada", "Section"]),
        ("sources/b/1.md", "article", "2026-11-01", ["RAG", "Ada", "Section"]),
    )
    G.nodes["Ada"]["kind"] = "depot"
    G.nodes["Section"]["file_type"] = "document"
    assert noms_de(G) == ["RAG"]


def test_le_media_ecarte_les_noms_qu_il_declare():
    G = graphe(
        ("sources/a/1.md", "article", "2026-11-01", ["Google", "RAG"]),
        ("sources/b/1.md", "article", "2026-11-01", ["Google", "RAG"]),
    )
    assert noms_de(G, hors=[" google "]) == ["RAG"]


# --- la déclaration


def test_la_declaration_du_graphe_est_validee():
    assert td.declaration(None) is None
    assert td.declaration({}) == {"jours": 30, "max": 8, "hors": []}
    assert td.declaration({"jours": 7, "max": 5, "hors": ["Google"]}) == {"jours": 7, "max": 5, "hors": ["Google"]}
    for mauvaise in (
        True,
        [],
        {"jour": 7},
        {"jours": 0},
        {"jours": 366},
        {"jours": True},
        {"max": 0},
        {"max": 51},
        {"max": "8"},
        {"hors": "Google"},
        {"hors": [1]},
    ):
        with pytest.raises(ValueError):
            td.declaration(mauvaise)


def test_le_chargement_du_registre_valide_la_declaration():
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    d["graphes"][g]["tendances"] = {"jours": 30, "max": 8}
    assert cfg._valider(d, cfg.FICHIER)
    d["graphes"][g]["tendances"] = {"jours": 0}
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.tendances : jours"):
        cfg._valider(d, cfg.FICHIER)


# --- le fichier et la commande


def test_un_fichier_absent_ou_illisible_se_lit_sans_tendance(tmp_path):
    assert td.lire(tmp_path / "absent.json") == []
    for contenu in ("pas du json", "[]", '{"tendances": 3}'):
        (tmp_path / "t.json").write_text(contenu, encoding="utf-8")
        assert td.lire(tmp_path / "t.json") == []


def test_la_commande_ecrit_les_tendances_du_graphe(tmp_path, monkeypatch, capsys):
    G = graphe(
        ("sources/a/1.md", "article", "2026-11-01", ["RAG", "Google", "Zed"]),
        ("sources/b/1.md", "article", "2026-11-01", ["RAG", "Google", "Zed"]),
        ("sources/a/3.md", "article", "2026-10-20", ["Ancienne"]),
        ("sources/b/3.md", "article", "2026-10-20", ["Ancienne"]),
        ("sources/a/2.md", "article", "2026-09-01", ["DPO"]),
        ("sources/b/2.md", "article", "2026-09-01", ["DPO"]),
    )
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"tendances": {"hors": ["Google"], "jours": 20, "max": 1}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    monkeypatch.setattr(td, "charger", lambda g: G)
    assert td.main(["--graphe", "g", "--jusqu", JUSQU]) == 0
    assert "1 tendance(s) : RAG (2 sources)" in capsys.readouterr().out
    ecrit = json.loads((tmp_path / "tendances.json").read_text(encoding="utf-8"))
    assert ecrit == {"jusqu": JUSQU, "jours": 20, "tendances": [{"notion": "RAG", "sources": 2, "fiches": 2}]}
    assert td.lire(tmp_path / "tendances.json") == ecrit["tendances"]
    assert td.main(["--graphe", "g", "--jusqu", "2026-09-10"]) == 0
    assert td.lire(tmp_path / "tendances.json") == [{"notion": "DPO", "sources": 2, "fiches": 2}]


def test_sans_jour_donne_la_fenetre_finit_aujourd_hui(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"tendances": {}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    monkeypatch.setattr(td, "charger", lambda g: nx.Graph())
    monkeypatch.setattr(td, "aujourd_hui", lambda: "2026-12-01")
    assert td.main(["--graphe", "g"]) == 0
    assert json.loads((tmp_path / "tendances.json").read_text(encoding="utf-8"))["jusqu"] == "2026-12-01"


def test_la_commande_ne_fait_rien_sans_declaration_ni_sans_graphe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    monkeypatch.setattr(td, "charger", lambda g: pytest.fail("aucune lecture attendue"))
    assert td.main(["--graphe", "g"]) == 0
    assert "pas de tendances" in capsys.readouterr().out
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"tendances": {}})
    monkeypatch.setattr(td, "charger", lambda g: None)
    assert td.main(["--graphe", "g"]) == 0
    assert "graphe absent" in capsys.readouterr().out and not (tmp_path / "tendances.json").exists()


def test_le_graphe_se_charge_du_dossier_du_graphe(monkeypatch):
    from newskit import themes_emergents as te

    monkeypatch.setattr(te, "charger", lambda g: f"graphe de {g}")
    assert td.charger("g") == "graphe de g"
    assert len(td.aujourd_hui()) == 10


# --- la routine


def test_la_routine_lance_l_etape_apres_la_composition_pour_un_graphe_qui_le_declare(monkeypatch):
    from newskit import routine_hebdo as rh

    lances = []
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: lances.append((libelle, args, k)) or (True, ""))
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"tendances": {}})
    assert rh.tendances(None, "g", " (g)") is True
    assert lances == [("tendances (g)", ["scripts/tendances.py", "--graphe", "g"], {"garder": 1})]
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert rh.tendances(None, "g") is False
    assert len(lances) == 1

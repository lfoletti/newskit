"""Les mentions sans modèle d'un procédé d'ingestion (P4b du portage du procédé des papiers, mainteneur, 15 et
19.11.2026 : « il n'y a que 2 papiers liés à xavier initialization? »).

L'extraction ne garde pas toutes les notions qu'un texte nomme. Pour chaque fiche d'un type qui déclare
`mentions: true`, le moteur pose dans son lot : `mentions` du document vers une notion canonique que son corps nomme
et que le lot n'a pas ; `uses` d'une étape vers une notion canonique que son texte nomme. Une notion canonique : un
nœud d'un type canonique de la convention, propre à aucun document, ni alias d'une autre, ni mot vide ; son libellé
se cherche à partir de deux mots, ses variantes (`variantes` d'alias.json) toujours."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from newskit import fiche, procedes
from newskit import extraire_concepts as ec
from newskit import procedes_mentions as pm
from newskit import procedes_passes as pp

CONVENTION = {
    "nom": "essai-1",
    "noeuds": {"canoniques": [{"id": "notion"}, {"id": "dataset"}], "propres": [{"id": "procedure"}, {"id": "step"}, {"id": "variant"}]},
    "relations": [{"id": "presents"}, {"id": "has_step"}, {"id": "precedes, follows"}, {"id": "mentions"}, {"id": "uses"}],
}  # fmt: skip


def n(i, label, nature="notion", **k):
    return {"id": i, "label": label, "nature": nature, "file_type": "concept", **k}


XAVIER = n("concept_xavier_initialization", "Xavier Initialization")
IMAGENET = n("concept_imagenet", "ImageNet", "dataset")
DROPOUT = n("concept_dropout_rate", "Dropout Rate")


@pytest.fixture
def media(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "procedes").mkdir()
    (tmp_path / "procedes" / "lecture.txt").write_text("Lis.\n", encoding="utf-8")
    (tmp_path / "convention.yaml").write_text(yaml.safe_dump(CONVENTION), encoding="utf-8")
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(pp, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "LOTS", tmp_path / "graphe" / "couche2" / "fiches")
    monkeypatch.setattr(ec, "viser", lambda graphe: {})
    monkeypatch.setattr(pm, "_alias", lambda graphe: {})
    ec.LOTS.mkdir(parents=True)
    return tmp_path


def declaration(media: Path, mentions: bool = True) -> dict:
    decl = {"papier": {"contenu": {"champ": "arxiv_id"}, "convention": "convention.yaml", "mentions": mentions,
                       "passes": {"lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps"}}}}  # fmt: skip
    return procedes.declaration(decl, media)["papier"]


def papier(media: Path, aid: str, corps: str, lot_nodes: list[dict], **champs) -> tuple[Path, Path]:
    p = media / "sources" / "arxiv" / f"2018-10-11_{aid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    en_tete = {
        "title": f"Papier {aid}",
        "author": "A",
        "date": "2018-10-11",
        "url": f"https://x/{aid}",
        "collected": "2026-11-01",
    }
    fiche.ecrire(p, {**en_tete, "arxiv_id": aid, **champs}, corps, strict=False, hors_graphe=True)
    lot = ec.LOTS / f"{ec.identifiant_de_fiche(p, fiche.lire_fichier(p)[0])}.json"
    lot.write_text(json.dumps({"nodes": lot_nodes, "edges": [], "hyperedges": []}), encoding="utf-8")
    return p, lot


def lancer(media, monkeypatch, decl=None, graphe="essai") -> dict:
    d = decl or declaration(media)
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda g: {"papier": d})
    monkeypatch.setattr(ec, "fiches_lues", lambda g=None: sorted((media / "sources").rglob("*.md")))
    return pm.passer(graphe)


def lu(lot: Path) -> dict:
    return json.loads(lot.read_text(encoding="utf-8"))


def fid(p: Path) -> str:
    return ec.identifiant_de_fiche(p, fiche.lire_fichier(p)[0])


# --- les notions canoniques


def test_une_notion_canonique_est_d_un_type_canonique_sans_document_ni_alias_ni_mot_vide():
    lots = {"a": {"nodes": [XAVIER, IMAGENET, n("concept_p1__proc_1_x", "Pre training", "notion"),
                            n("concept_s", "Some Step", "step"), n("concept_ns", "not stated"),
                            n("concept_kaiming", "Kaiming Init"), {"id": "concept_sans", "label": "Sans Nature"}]}}  # fmt: skip
    canon = pm.canoniques(lots, {"concept_kaiming": "concept_he"}, {"notion", "dataset"})
    assert set(canon) == {"concept_xavier_initialization", "concept_imagenet"}


def test_un_libelle_d_un_mot_ne_se_cherche_pas_ses_variantes_si():
    canon = {"concept_imagenet": IMAGENET, "concept_xavier_initialization": XAVIER}
    m = pm.motifs(canon, {"concept_imagenet": ["ILSVRC 2012"], "concept_xavier_initialization": ["Glorot and Bengio"]})
    t = pm.trouver("Trained on ImageNet (ILSVRC 2012) with the init of Glorot  and\nBengio.", m)
    assert set(t) == {"concept_imagenet", "concept_xavier_initialization"}
    assert "ILSVRC 2012" in t["concept_imagenet"]
    assert pm.trouver("Trained on ImageNet.", m) == {}


def test_la_recherche_ignore_la_casse_et_respecte_les_bornes_de_mot():
    m = pm.motifs({"concept_xavier_initialization": XAVIER}, {})
    assert pm.trouver("uses XAVIER initialization here", m)
    assert not pm.trouver("uses pre-xavier initialization", m)
    assert not pm.trouver("uses xavier initializations", m)


def test_sans_libelle_cherchable_rien_ne_se_trouve():
    assert pm.trouver("ImageNet", pm.motifs({"concept_imagenet": IMAGENET}, {})) == {}


def test_un_extrait_entoure_la_premiere_mention():
    m = pm.motifs({"concept_xavier_initialization": XAVIER}, {})
    texte = "a " * 100 + "Xavier Initialization" + " b" * 100 + " Xavier Initialization"
    e = pm.trouver(texte, m)["concept_xavier_initialization"]
    assert e.startswith("a a") and e.endswith("b b") and len(e) <= 2 * pm.AUTOUR + 25


def test_deux_notions_au_meme_libelle_se_trouvent_toutes_deux():
    autre = n("concept_xavier_init", "Xavier Initialization")
    m = pm.motifs({"concept_xavier_initialization": XAVIER, "concept_xavier_init": autre}, {})
    assert set(pm.trouver("Xavier Initialization", m)) == {"concept_xavier_initialization", "concept_xavier_init"}


# --- le passage sur les lots


def test_le_document_mentionne_une_notion_que_son_corps_nomme_et_que_son_lot_n_a_pas(media, monkeypatch):
    lancer_ = papier(media, "1", "# T\n\nWe use Xavier Initialization for all layers.\n", [])
    papier(media, "2", "# T\n\nAutre.\n", [XAVIER])
    lancer(media, monkeypatch)
    p, lot = lancer_
    l = lu(lot)
    assert "concept_xavier_initialization" in [x["id"] for x in l["nodes"]]
    e = [x for x in l["edges"] if x["relation"] == "mentions"]
    assert [(x["source"], x["target"]) for x in e] == [(fid(p), "concept_xavier_initialization")]
    assert "We use Xavier Initialization" in e[0]["evidence"] and e[0]["contributor"] == pm.CONTRIBUTEUR
    noeud = next(x for x in l["nodes"] if x["id"] == "concept_xavier_initialization")
    assert noeud["contributor"] == pm.CONTRIBUTEUR and noeud["label"] == "Xavier Initialization"


def test_une_notion_deja_dans_le_lot_n_est_pas_mentionnee(media, monkeypatch):
    _, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [XAVIER])
    lancer(media, monkeypatch)
    assert not [x for x in lu(lot)["edges"] if x["relation"] == "mentions"]


def test_une_etape_emploie_une_notion_que_son_texte_nomme(media, monkeypatch):
    etape = n(
        "concept_p1__proc_1_x__step_1",
        "Step 1 of X: Initialize weights",
        "step",
        rationale="Xavier Initialization, then Dropout Rate 0.1",
    )
    _, lot = papier(media, "1", "# T\n\nRien.\n", [etape, XAVIER, DROPOUT])
    lancer(media, monkeypatch)
    rel = {(x["source"], x["relation"], x["target"]) for x in lu(lot)["edges"]}
    assert rel == {
        (etape["id"], "uses", "concept_xavier_initialization"),
        (etape["id"], "uses", "concept_dropout_rate"),
    }
    assert len(lu(lot)["nodes"]) == 3


def test_un_emploi_deja_relie_ne_se_double_pas(media, monkeypatch):
    etape = n("concept_p1__proc_1_x__step_1", "Step 1", "step", rationale="Xavier Initialization")
    _, lot = papier(media, "1", "# T\n\nRien.\n", [etape, XAVIER])
    l = lu(lot)
    l["edges"] = [
        {"source": etape["id"], "target": XAVIER["id"], "relation": "uses", "contributor": "procédé papier:procede"}
    ]
    lot.write_text(json.dumps(l), encoding="utf-8")
    lancer(media, monkeypatch)
    assert len(lu(lot)["edges"]) == 1


def test_un_nouveau_passage_ne_double_rien(media, monkeypatch):
    etape = n("concept_p1__proc_1_x__step_1", "Step 1", "step", rationale="Dropout Rate")
    _, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [etape, DROPOUT])
    papier(media, "2", "# T\n\n.\n", [XAVIER])
    lancer(media, monkeypatch)
    un = lu(lot)
    lancer(media, monkeypatch)
    assert lu(lot) == un


def test_une_mention_qui_disparait_du_corps_se_retire(media, monkeypatch):
    p, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [])
    papier(media, "2", "# T\n\n.\n", [XAVIER])
    lancer(media, monkeypatch)
    champs, _ = fiche.lire_fichier(p)
    fiche.ecrire(p, champs, "# T\n\nAutre chose.\n", strict=False, hors_graphe=True)
    lancer(media, monkeypatch)
    assert lu(lot)["nodes"] == [] and lu(lot)["edges"] == []


def test_un_lot_inchange_ne_se_reecrit_pas(media, monkeypatch):
    _, lot = papier(media, "1", "# T\n\nRien.\n", [XAVIER])
    avant = lot.stat().st_mtime_ns
    r = lancer(media, monkeypatch)
    assert lot.stat().st_mtime_ns == avant and r["lots"] == 0


def test_le_bilan_compte_mentions_emplois_et_lots(media, monkeypatch):
    etape = n("concept_p1__proc_1_x__step_1", "Step 1", "step", rationale="Dropout Rate")
    papier(media, "1", "# T\n\nXavier Initialization.\n", [etape, DROPOUT])
    papier(media, "2", "# T\n\n.\n", [XAVIER])
    assert lancer(media, monkeypatch) == {"notions": 2, "mentions": 1, "uses": 1, "lots": 1}


def test_une_fiche_sans_lot_attend(media, monkeypatch):
    p, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [])
    lot.unlink()
    papier(media, "2", "# T\n\n.\n", [XAVIER])
    assert lancer(media, monkeypatch)["lots"] == 0
    assert not lot.exists()


def test_un_type_sans_mentions_ne_pose_rien(media, monkeypatch):
    _, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [])
    papier(media, "2", "# T\n\n.\n", [XAVIER])
    assert lancer(media, monkeypatch, declaration(media, mentions=False))["lots"] == 0
    assert lu(lot)["edges"] == []


def test_une_fiche_hors_type_ne_recoit_rien_mais_ses_notions_comptent(media, monkeypatch):
    p = media / "sources" / "web" / "2026-01-01_billet.md"
    p.parent.mkdir(parents=True)
    fiche.ecrire(p, {"title": "B", "author": "X", "date": "2026-01-01", "url": "https://x/b", "collected": "2026-11-01"},
                 "Xavier Initialization.\n", strict=False, hors_graphe=True)  # fmt: skip
    hors = ec.LOTS / f"{fid(p)}.json"
    hors.write_text(json.dumps({"nodes": [XAVIER], "edges": []}), encoding="utf-8")
    _, lot = papier(media, "1", "# T\n\nXavier Initialization.\n", [])
    lancer(media, monkeypatch)
    assert lu(hors) == {"nodes": [XAVIER], "edges": []}
    assert [x["target"] for x in lu(lot)["edges"]] == ["concept_xavier_initialization"]


def test_les_alias_et_les_variantes_du_graphe_comptent(media, monkeypatch):
    monkeypatch.setattr(pm, "_alias", lambda g: {"alias": {"concept_xavier_initialization": "concept_glorot"},
                                                 "variantes": {"concept_dropout_rate": ["drop prob"]}})  # fmt: skip
    _, lot = papier(media, "1", "# T\n\nXavier Initialization, drop prob 0.1.\n", [])
    papier(media, "2", "# T\n\n.\n", [XAVIER, DROPOUT])
    lancer(media, monkeypatch)
    assert [x["target"] for x in lu(lot)["edges"]] == ["concept_dropout_rate"]


def test_alias_json_se_lit_dans_le_dossier_du_graphe(tmp_path, monkeypatch):
    monkeypatch.setattr(pm.cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    assert pm._alias("essai") == {}
    (tmp_path / "alias.json").write_text(json.dumps({"alias": {"a": "b"}}), encoding="utf-8")
    assert pm._alias("essai") == {"alias": {"a": "b"}}


def test_le_passage_vise_le_graphe(media, monkeypatch):
    vises = []
    monkeypatch.setattr(ec, "viser", lambda g: vises.append(g) or {})
    lancer(media, monkeypatch)
    assert vises == ["essai"]


def test_la_commande_rend_compte(media, monkeypatch, capsys):
    monkeypatch.setattr(pm, "passer", lambda g: {"notions": 3, "mentions": 2, "uses": 1, "lots": 1})
    assert pm.main(["--graphe", "essai"]) == 0
    assert "essai : 3 notions canoniques, 2 mentions, 1 emplois d'étape, 1 lots réécrits" in capsys.readouterr().out

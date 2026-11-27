"""Une version ne change pas l'empreinte d'un versement inchangé (mainteneur, 27.11.2026).

Règle du 16.11.2026 : un document du cerveau se réécrit seulement si le contenu de sa fiche change. La version 0.1.85 a
changé la forme d'un versement (une fiche se verse sous les noms de son lot) : l'empreinte de 3 307 documents sur 23 017
d'un graphe de gouvernance a changé, 3 288 d'entre eux avaient le même texte, les mêmes entités et les mêmes relations.
Le passage a choisi la reconstruction complète, 3 h 15, arrêtée à la main ; le relevé a été migré à la main.

Deux gardes. Les empreintes de versements de référence sont écrites dans `empreintes_de_reference.json` : une version
qui les change fait tomber le test, et dit quoi livrer. Un changement voulu se livre avec la forme précédente du
versement (`puits_cortexdb.FORMES_PRECEDENTES`) : le passage reconnaît alors l'ancienne empreinte, met le relevé à jour
et n'écrit rien au cerveau."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from newskit import config_sources as cfg
from newskit import puits_cortexdb as pc

REFERENCE = Path(__file__).with_name("empreintes_de_reference.json")
FICHE = "sources/a/2026-11-18_un-article.md"
ID = "sources_a_2026_11_18_un_article"
SQUELETTE = {
    "nodes": [
        {"id": ID, "label": "Un article", "file_type": "document", "kind": "article", "source_file": FICHE},
        {"id": "auteur_a", "label": "Ada Auteur", "file_type": "concept", "kind": "auteur", "source_file": FICHE},
    ],
    "edges": [
        {"source": ID, "target": "auteur_a", "relation": "ecrit_par", "confidence": "EXTRACTED", "source_file": FICHE,
         "source_location": "L1", "weight": 1.0, "_origin": "squelette"},
    ],
}  # fmt: skip
STRUCTURE = {
    "nodes": [
        {"id": ID, "label": "2026-11-18_un-article", "file_type": "document", "source_file": FICHE, "_origin": "ast"},
        {"id": ID + "_total", "label": "**Total des amendes**", "file_type": "document", "source_file": FICHE,
         "source_location": "L12", "_origin": "ast"},
    ],
    "edges": [
        {"source": ID, "target": ID + "_total", "relation": "contains", "confidence": "EXTRACTED", "source_file": FICHE,
         "source_location": "L12", "weight": 1.0, "_origin": "ast"},
    ],
}  # fmt: skip
LOT = {
    "nodes": [
        {"id": ID, "label": "Un article", "file_type": "document", "source_file": FICHE},
        {"id": "minimisation", "label": "Minimisation", "file_type": "concept", "source_file": FICHE},
        {"id": "data_minimization", "label": "Data minimization", "file_type": "concept", "source_file": FICHE},
    ],
    "edges": [
        {"source": ID, "target": "minimisation", "relation": "mentions", "confidence": "EXTRACTED",
         "confidence_score": 1.0, "source_file": FICHE, "weight": 1.0},
        {"source": "minimisation", "target": "data_minimization", "relation": "related_to", "confidence": "INFERRED",
         "confidence_score": 0.6, "source_file": FICHE, "weight": 1.0},
    ],
    "hyperedges": [],
}  # fmt: skip
NOTION = {"label": "Sobriété des données", "phrases": [{"texte": "Une phrase.", "refs": [{"fiche": FICHE}]}]}


@pytest.fixture
def versements(tmp_path, monkeypatch) -> dict[str, dict]:
    """Les versements de référence : une fiche avec lot et couche 1, la même sans lot, une synthèse."""
    p = tmp_path / FICHE
    p.parent.mkdir(parents=True)
    p.write_text(
        '---\ntitle: "Un article"\nauthor: "Ada Auteur"\ndate: "2026-11-18"\nurl: "https://e.org/1"\n---\n\n'
        "# Un article\n\nLe texte de l'article.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "source_du_chemin", lambda s: None)
    monkeypatch.setattr(pc, "corps_versable", lambda src, entete, corps, droits=None, chemin="": corps)
    c1 = pc.couche1_d_une_fiche(SQUELETTE, STRUCTURE, FICHE, tmp_path)
    canon = pc.canon_squelette({"nodes": SQUELETTE["nodes"]})
    return {
        "fiche avec lot et couche 1": pc.versement_d_une_fiche(FICHE, LOT, c1, canon, None, tmp_path),
        "fiche sans lot": pc.versement_d_une_fiche(FICHE, None, None, None, None, tmp_path),
        "synthèse": pc.synthese_vers_cortexdb("minimisation", NOTION),
    }


class SansServeur:
    """Un client qu'aucun appel ne doit toucher."""

    def __getattr__(self, nom):
        raise AssertionError(f"le passage a parlé au serveur ({nom})")


def test_une_version_ne_change_pas_l_empreinte_d_un_versement_inchange(versements):
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    obtenu = {nom: pc.empreinte(v) for nom, v in versements.items()}
    assert obtenu == reference, (
        "L'empreinte d'un versement inchangé a changé : au premier passage, chaque document de chaque cerveau serait "
        "réécrit. Si le changement n'est pas voulu, le défaire. S'il l'est, ajouter à "
        "puits_cortexdb.FORMES_PRECEDENTES la fonction qui rend la forme d'avant d'un versement, avec son test, "
        "écrire au corps de la PR ce que l'installation réécrit, puis mettre tests/empreintes_de_reference.json à jour."
    )


def test_la_reference_couvre_des_entites_des_relations_et_un_texte(versements):
    v = versements["fiche avec lot et couche 1"]
    assert len(v["entites"]) >= 4 and len(v["relations"]) >= 3 and "Le texte de l'article." in v["document"]["content"]
    assert versements["synthèse"]["entites"] and versements["fiche sans lot"]["entites"] == []


def avant_le_changement(v: dict) -> dict:
    """Une forme précédente d'essai : les entités portaient leur nom en capitales."""
    return {**v, "entites": [{**e, "name": e["name"].upper()} for e in v["entites"]]}


@pytest.mark.parametrize("compter", [True, False])
def test_une_forme_precedente_declaree_met_le_releve_a_jour_sans_rien_ecrire(versements, monkeypatch, compter):
    v = versements["fiche avec lot et couche 1"]
    ident = v["document"]["document_id"]
    monkeypatch.setattr(pc, "FORMES_PRECEDENTES", [avant_le_changement])
    bilan: dict = {}
    ep = pc.EnPlace(SansServeur(), {ident: pc.empreinte(avant_le_changement(v))}, bilan, compter=compter)
    vus: dict = {}
    assert ep.verser(v, vus) is None
    assert (bilan["migres"], bilan["remplaces"], bilan["inchanges"], bilan["ajoutes"]) == (1, 0, 0, 0)
    assert ep.apres == {ident: pc.empreinte(v)}  # le relevé du passage porte l'empreinte d'aujourd'hui
    assert vus == {ident: v["origine"]}  # le document compte parmi ceux du passage


def test_sans_forme_precedente_une_empreinte_changee_remplace_le_document(versements):
    v = versements["fiche avec lot et couche 1"]
    ident = v["document"]["document_id"]
    assert pc.FORMES_PRECEDENTES == []  # aucune migration en cours : le relevé de chaque cerveau est à la forme du jour
    bilan: dict = {}
    ep = pc.EnPlace(SansServeur(), {ident: pc.empreinte(avant_le_changement(v))}, bilan, compter=True)
    ep.verser(v, {})
    assert (bilan["migres"], bilan["remplaces"]) == (0, 1)


def test_un_contenu_change_ne_passe_pas_pour_une_forme_precedente(versements, monkeypatch):
    v = versements["fiche avec lot et couche 1"]
    ident = v["document"]["document_id"]
    monkeypatch.setattr(pc, "FORMES_PRECEDENTES", [avant_le_changement])
    bilan: dict = {}
    ep = pc.EnPlace(SansServeur(), {ident: "empreinte d'un autre texte"}, bilan, compter=True)
    ep.verser(v, {})
    assert (bilan["migres"], bilan["remplaces"]) == (0, 1)

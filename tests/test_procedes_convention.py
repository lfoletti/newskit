"""La couche 2 d'une fiche d'un type de contenu déclaré suit la convention du média (P3 du portage du procédé des
papiers, mainteneur, 15 et 19.11.2026).

Le type de chaque nœud devient un champ de l'extraction (`nature`), parmi les types de la convention ; les relations
de la convention s'ajoutent à celles du moteur ; la consigne de volume du média remplace celle du moteur. Une fiche hors
de tout type déclaré garde la consigne et le schéma du moteur."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from newskit import couts
from newskit import extraire_concepts as ec

CONVENTION = {
    "nom": "essai-1",
    "noeuds": {
        "canoniques": [{"id": "notion", "definition": "A concept the field names."}, {"id": "dataset"}],
        "propres": [{"id": "result", "definition": "A measured value."}],
    },
    "relations": [{"id": "uses", "liaison": "step → notion"}, {"id": "precedes, follows"}],
}
VOLUME = "Keep 20 to 60 nodes. Paper ids: concept_p{cle_id}__<slug>, labels end with ' ({court})'. {inconnu} {title}."
CHAMPS = {
    "title": "Un papier",
    "author": "Yann LeCun, Randall Balestriero",
    "date": "2026-10-01",
    "arxiv_id": "2610.39302",
}


@pytest.fixture
def media(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "procedes").mkdir()
    (tmp_path / "procedes" / "lecture.txt").write_text("Lis.\n", encoding="utf-8")
    (tmp_path / "procedes" / "volume.txt").write_text(VOLUME + "\n", encoding="utf-8")
    (tmp_path / "convention.yaml").write_text(yaml.safe_dump(CONVENTION), encoding="utf-8")
    decl = {
        "papier": {
            "contenu": {"champ": "arxiv_id"},
            "convention": "convention.yaml",
            "passes": {"lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps"}},
            "extraction": {"volume": "procedes/volume.txt"},
        }
    }
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(ec.cfg, "graphe_declare", lambda nom: {"domaine": "essai", "procedes": decl})
    ec._procedes.clear()
    yield tmp_path
    ec._procedes.clear()


def sans_procede(monkeypatch):
    monkeypatch.setattr(ec.cfg, "graphe_declare", lambda nom: {"domaine": "essai"})
    ec._procedes.clear()


# --- la consigne


def test_une_fiche_hors_type_garde_la_consigne_et_le_schema_du_moteur(media):
    champs = {"title": "Un billet", "author": "X", "date": "2026-10-01"}
    invite = ec.prompt_pour(media / "a.md", champs, "texte")
    assert "Keep 8 to 25 nodes" in invite and "nature" not in invite
    assert ec.schema_pour(champs) is ec.SCHEMA


def test_un_graphe_sans_procede_garde_la_consigne_et_le_schema_du_moteur(media, monkeypatch):
    sans_procede(monkeypatch)
    invite = ec.prompt_pour(media / "a.md", CHAMPS, "texte")
    assert "only the ABSTRACT" in invite and "nature" not in invite
    assert ec.schema_pour(CHAMPS) is ec.SCHEMA


def test_la_consigne_de_volume_du_media_remplace_celle_du_moteur(media):
    invite = ec.prompt_pour(media / "a.md", CHAMPS, "texte")
    assert "only the ABSTRACT" not in invite and "Keep 8 to 25 nodes" not in invite
    assert "Paper ids: concept_p2610_39302__<slug>, labels end with ' (LeCun 2026)'. {inconnu} Un papier." in invite


def test_sans_consigne_de_volume_le_moteur_garde_la_sienne(media, monkeypatch):
    decl = ec.cfg.graphe_declare("x")["procedes"]
    del decl["papier"]["extraction"]
    monkeypatch.setattr(ec.cfg, "graphe_declare", lambda nom: {"domaine": "essai", "procedes": decl})
    ec._procedes.clear()
    assert "only the ABSTRACT" in ec.prompt_pour(media / "a.md", CHAMPS, "texte")


def test_la_consigne_nomme_les_types_et_les_relations_de_la_convention(media):
    invite = ec.prompt_pour(media / "a.md", CHAMPS, "texte")
    assert "`nature`" in invite
    assert "- notion: A concept the field names." in invite
    assert "- result: A measured value." in invite
    assert "- dataset" in invite
    assert "- uses: step → notion" in invite and "- precedes" in invite and "- follows" in invite
    assert "essai-1" in invite


@pytest.mark.parametrize(
    "auteur, court",
    [("Yann LeCun, Randall Balestriero", "LeCun 2026"), ("", "Anonymous 2026"), ("Vaswani", "Vaswani 2026")],
)
def test_le_nom_court_est_le_nom_du_premier_auteur_et_l_annee(auteur, court):
    assert ec.valeurs_de_consigne({"author": auteur, "date": "2026-10-01"}, "x")["court"] == court


# --- le schéma


def test_le_schema_d_une_fiche_du_type_demande_la_nature_parmi_les_types_de_la_convention(media):
    s = ec.schema_pour(CHAMPS)
    noeud = s["properties"]["nodes"]["items"]
    assert noeud["properties"]["nature"]["enum"] == ["dataset", "notion", "result"]
    assert "nature" in noeud["required"]
    rel = s["properties"]["edges"]["items"]["properties"]["relation"]["enum"]
    assert {"uses", "precedes", "follows"} <= set(rel) and set(ec.RELATIONS) <= set(rel)
    assert ec.SCHEMA["properties"]["nodes"]["items"]["properties"].get("nature") is None  # le schéma du moteur intact


def test_l_appel_passe_le_schema_donne(media, monkeypatch):
    vus = []

    def run(args, **k):
        vus.append(json.loads(args[args.index("--json-schema") + 1]))
        env = {"type": "result", "result": "", "structured_output": {"nodes": [], "edges": []}, "is_error": False}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(env), stderr="")

    monkeypatch.setattr(ec.subprocess, "run", run)
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr(couts, "enregistrer", lambda *a, **k: json.loads(a[3].stdout))
    ec.appeler_claude("invite", ec.schema_pour(CHAMPS))
    ec.appeler_claude("invite")
    assert "nature" in vus[0]["properties"]["nodes"]["items"]["properties"]
    assert vus[1] == ec.SCHEMA


def test_l_extraction_d_une_fiche_envoie_le_schema_de_son_type(media, monkeypatch):
    f = media / "sources" / "arxiv" / "2026-10-01_p.md"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    schemas = []
    monkeypatch.setattr(ec, "PARALLELE", 1)
    monkeypatch.setattr(ec, "refus_connus", lambda: {})
    monkeypatch.setattr(ec, "prompt_de", lambda f: (dict(CHAMPS), "invite"))
    monkeypatch.setattr(ec, "appeler_claude", lambda invite, schema=None: schemas.append(schema) or (None, "échec"))
    ec.extraire([(f, "h")])
    assert "nature" in schemas[0]["properties"]["nodes"]["items"]["properties"]


# --- la normalisation


def ext(nature="notion", relation="uses"):
    return {
        "nodes": [
            {"id": "concept_a", "label": "A", "file_type": "concept", "nature": nature},
            {"id": "concept_b", "label": "B", "file_type": "concept", "nature": "dataset"},
        ],
        "edges": [
            {
                "source": "concept_a",
                "target": "concept_b",
                "relation": relation,
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "evidence": "a uses b in the text",
            }  # fmt: skip
        ],
    }


def test_la_normalisation_garde_la_nature_et_les_relations_de_la_convention(media):
    propre, _ = ec.normaliser_extraction(ext(), media / "a.md", CHAMPS, corps="here a uses b in the text, really")
    natures = {n["id"]: n.get("nature") for n in propre["nodes"]}
    assert natures == {"concept_a": "notion", "concept_b": "dataset"}
    assert propre["edges"][0]["relation"] == "uses"


def test_une_nature_hors_convention_se_retire(media):
    propre, ecartes = ec.normaliser_extraction(
        ext(nature="person"), media / "a.md", CHAMPS, corps="a uses b in the text"
    )
    assert "nature" not in next(n for n in propre["nodes"] if n["id"] == "concept_a")
    assert any("person" in e for e in ecartes)


def test_une_relation_hors_convention_et_hors_moteur_devient_references(media):
    propre, _ = ec.normaliser_extraction(ext(relation="likes"), media / "a.md", CHAMPS, corps="a uses b in the text")
    assert propre["edges"][0]["relation"] == "references"


def test_hors_type_ni_nature_ni_relation_de_convention(media):
    champs = {"title": "Un billet", "author": "X", "date": "2026-10-01"}
    propre, _ = ec.normaliser_extraction(ext(), media / "a.md", champs, corps="a uses b in the text")
    assert all("nature" not in n for n in propre["nodes"])
    assert propre["edges"][0]["relation"] == "references"


def test_la_declaration_se_lit_une_fois_par_graphe(media, monkeypatch):
    lus = []
    decl = ec.cfg.graphe_declare("x")
    monkeypatch.setattr(ec.cfg, "graphe_declare", lambda nom: lus.append(nom) or decl)
    ec._procedes.clear()
    ec.schema_pour(CHAMPS)
    ec.schema_pour(CHAMPS)
    assert len(lus) == 1


@pytest.mark.parametrize("valeur, cle", [("2610.39302", "2610_39302"), ("LeCun-1998.LeNet", "lecun_1998_lenet")])
def test_la_cle_d_identifiant_est_en_minuscules_chiffres_et_soulignes(valeur, cle):
    assert ec.valeurs_de_consigne({}, valeur)["cle_id"] == cle

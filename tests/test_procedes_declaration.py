"""Les procédés d'ingestion se déclarent par graphe et par type de contenu (mainteneur, 15 et 19.11.2026).

« newskit prend en variable une fiche d'extracteur, une convention, une ontologie... tout ce qui spécialise le média » ;
« ce procédé est lié au type de contenu à extraire et au média qui l'accueille » ; « refus à la création » d'un graphe
sans convention. Le moteur lit et vérifie la déclaration ; les consignes et la convention restent dans le dossier du
média."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from newskit import config_sources as cfg
from newskit import procedes

CONVENTION = {
    "nom": "essai-1",
    "noeuds": {
        "canoniques": [{"id": "notion"}, {"id": "dataset"}, {"id": "model"}],
        "propres": [{"id": "procedure"}, {"id": "step"}, {"id": "variant"}, {"id": "component"}],
    },
    "relations": [
        {"id": "presents"},
        {"id": "has_step"},
        {"id": "precedes, follows"},
        {"id": "has_component"},
        {"id": "feeds"},
        {"id": "skip"},
        {"id": "instance_of"},
        {"id": "mentions"},
        {"id": "uses"},
    ],
}


@pytest.fixture
def media(tmp_path: Path) -> Path:
    (tmp_path / "procedes").mkdir()
    for f in ("lecture.txt", "etapes.txt", "composants.txt", "tri.txt", "volume.txt"):
        (tmp_path / "procedes" / f).write_text("consigne\n", encoding="utf-8")
    (tmp_path / "convention.yaml").write_text(yaml.safe_dump(CONVENTION), encoding="utf-8")
    return tmp_path


def complet() -> dict:
    return {
        "papier": {
            "contenu": {"champ": "arxiv_id"},
            "texte": {"integral": True, "mots_max": 45000},
            "convention": "convention.yaml",
            "passes": {
                "lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps"},
                "procede": {"consigne": "procedes/etapes.txt", "forme": "etapes"},
                "architecture": {
                    "consigne": "procedes/composants.txt",
                    "forme": "composants",
                    "tri": "procedes/tri.txt",
                },
            },
            "extraction": {"volume": "procedes/volume.txt"},
            "mentions": True,
            "appel": "lots",
        }
    }


def refuse(decl, media: Path, motif: str) -> None:
    with pytest.raises(ValueError, match=motif):
        procedes.declaration(decl, media)


# --- ce qui se lit


def test_sans_declaration_rien():
    assert procedes.declaration(None, Path("/nulle/part")) is None


def test_une_declaration_complete_se_lit_dans_l_ordre_des_passes(media):
    d = procedes.declaration(complet(), media)["papier"]
    assert d["contenu"] == {"champ": "arxiv_id"}
    assert d["texte"] == {"integral": True, "mots_max": 45000}
    assert [p["nom"] for p in d["passes"]] == ["lecture", "procede", "architecture"]
    assert d["passes"][0] == {
        "nom": "lecture",
        "consigne": media / "procedes/lecture.txt",
        "sortie": "corps",
        "forme": None,
        "tri": None,
    }
    assert d["passes"][2]["tri"] == media / "procedes/tri.txt"
    assert d["passes"][1]["forme"] == "etapes" and d["passes"][1]["sortie"] is None
    assert d["extraction"] == {"volume": media / "procedes/volume.txt"}
    assert d["mentions"] is True and d["appel"] == "lots"
    assert d["convention"]["nom"] == "essai-1"


def test_les_valeurs_par_defaut(media):
    decl = complet()
    for k in ("texte", "extraction", "mentions", "appel"):
        del decl["papier"][k]
    d = procedes.declaration(decl, media)["papier"]
    assert d["texte"] == {"integral": True, "mots_max": procedes.MOTS_MAX}
    assert d["extraction"] == {"volume": None}
    assert d["mentions"] is False and d["appel"] == "claude_p"


def test_la_convention_lit_types_et_relations_une_ligne_peut_en_nommer_deux(media):
    c = procedes.convention(media / "convention.yaml")
    assert c["canoniques"] == {"notion", "dataset", "model"}
    assert c["propres"] == {"procedure", "step", "variant", "component"}
    assert {"precedes", "follows", "presents"} <= c["relations"]
    assert "precedes, follows" not in c["relations"]


# --- ce qui se refuse


@pytest.mark.parametrize("decl", [[], "papier", {}])
def test_un_dictionnaire_non_vide_de_types_de_contenu(media, decl):
    refuse(decl, media, "type de contenu")


@pytest.mark.parametrize("nom", ["Papier", "1papier", "pa pier", ""])
def test_le_nom_d_un_type_de_contenu(media, nom):
    refuse({nom: complet()["papier"]}, media, "nom")


def test_un_champ_inconnu_se_refuse(media):
    decl = complet()
    decl["papier"]["modele"] = "x"
    refuse(decl, media, "champs inconnus")


@pytest.mark.parametrize("contenu", [None, {}, {"champ": ""}, {"champ": 3}, {"champ": "a", "autre": 1}])
def test_le_contenu_nomme_le_champ_d_en_tete_qui_le_reconnait(media, contenu):
    decl = complet()
    decl["papier"]["contenu"] = contenu
    refuse(decl, media, "contenu")


def test_sans_convention_le_procede_se_refuse(media):
    decl = complet()
    del decl["papier"]["convention"]
    refuse(decl, media, "convention")


def test_une_convention_absente_du_dossier_se_refuse(media):
    decl = complet()
    decl["papier"]["convention"] = "ailleurs.yaml"
    refuse(decl, media, "convention")


@pytest.mark.parametrize(
    "casse, motif",
    [
        (lambda c: c.pop("nom"), "nom"),
        (lambda c: c.update(nom=""), "nom"),
        (lambda c: c.pop("noeuds"), "noeuds"),
        (lambda c: c.update(noeuds=["notion"]), "noeuds"),
        (lambda c: c["noeuds"].update(canoniques=[]), "canoniques : une liste non vide"),
        (lambda c: c["noeuds"]["propres"].append({"nom": "sans id"}), "id"),
        (lambda c: c["noeuds"]["propres"].append({"id": "notion"}), "deux fois"),
        (lambda c: c.update(relations=[]), "relations : une liste non vide"),
        (lambda c: c["relations"].append({"id": "Has Step"}), "Has Step"),
        (lambda c: c["relations"].append("presents"), "id"),
    ],
)
def test_une_convention_mal_formee_se_refuse(media, casse, motif):
    c = copy.deepcopy(CONVENTION)
    casse(c)
    (media / "convention.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    refuse(complet(), media, f"convention.*{motif}")


def test_une_convention_illisible_se_refuse(media):
    (media / "convention.yaml").write_text("nom: [\n", encoding="utf-8")
    refuse(complet(), media, "convention")


@pytest.mark.parametrize("chemin", ["/etc/passwd", "../dehors.txt", "procedes/absente.txt", "", 3])
def test_une_consigne_est_un_fichier_du_dossier_du_media(media, chemin):
    (media.parent / "dehors.txt").write_text("hors du média\n", encoding="utf-8")
    decl = complet()
    decl["papier"]["passes"]["lecture"]["consigne"] = chemin
    refuse(decl, media, "consigne")


@pytest.mark.parametrize("passes", [None, {}, []])
def test_au_moins_une_passe(media, passes):
    decl = complet()
    decl["papier"]["passes"] = passes
    refuse(decl, media, "passes")


@pytest.mark.parametrize(
    "passe",
    [
        {"consigne": "procedes/lecture.txt"},
        {"consigne": "procedes/lecture.txt", "sortie": "corps", "forme": "etapes"},
        {"consigne": "procedes/lecture.txt", "sortie": "titre"},
        {"consigne": "procedes/lecture.txt", "forme": "tableau"},
        {"consigne": "procedes/lecture.txt", "sortie": "corps", "inconnu": 1},
        "procedes/lecture.txt",
    ],
)
def test_une_passe_ecrit_la_fiche_ou_suit_une_forme(media, passe):
    decl = complet()
    decl["papier"]["passes"]["lecture"] = passe
    refuse(decl, media, "passes.lecture")


@pytest.mark.parametrize("nom", ["Lecture", "", "deux mots"])
def test_le_nom_d_une_passe(media, nom):
    decl = complet()
    decl["papier"]["passes"] = {nom: decl["papier"]["passes"]["lecture"]}
    refuse(decl, media, "passe")


def test_une_seule_passe_ecrit_le_corps_de_la_fiche(media):
    decl = complet()
    decl["papier"]["passes"]["seconde"] = {"consigne": "procedes/lecture.txt", "sortie": "corps"}
    refuse(decl, media, "corps")


def test_le_tri_d_une_passe_est_un_fichier_du_dossier(media):
    decl = complet()
    decl["papier"]["passes"]["architecture"]["tri"] = "procedes/absent.txt"
    refuse(decl, media, "tri")


@pytest.mark.parametrize(
    "forme, retire",
    [
        ("etapes", "step"),
        ("etapes", "has_step"),
        ("etapes", "follows"),
        ("composants", "component"),
        ("composants", "feeds"),
    ],
)
def test_une_forme_demande_ses_types_et_ses_relations_a_la_convention(media, forme, retire):
    c = copy.deepcopy(CONVENTION)
    c["noeuds"]["propres"] = [x for x in c["noeuds"]["propres"] if x["id"] != retire]
    c["relations"] = [{"id": ", ".join(i for i in r["id"].split(", ") if i != retire)} for r in c["relations"]]
    c["relations"] = [r for r in c["relations"] if r["id"]]
    (media / "convention.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    decl = complet()
    decl["papier"]["passes"] = {"seule": {"consigne": "procedes/lecture.txt", "forme": forme}}
    refuse(decl, media, retire)


def test_les_mentions_demandent_leurs_relations_a_la_convention(media):
    c = copy.deepcopy(CONVENTION)
    c["relations"] = [r for r in c["relations"] if r["id"] != "mentions"]
    (media / "convention.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    decl = complet()
    decl["papier"]["passes"] = {"lecture": decl["papier"]["passes"]["lecture"]}
    refuse(decl, media, "mentions")
    decl["papier"]["mentions"] = False
    assert procedes.declaration(decl, media)["papier"]["mentions"] is False


@pytest.mark.parametrize("mentions", ["oui", 1])
def test_mentions_vrai_ou_faux(media, mentions):
    decl = complet()
    decl["papier"]["mentions"] = mentions
    refuse(decl, media, "mentions")


@pytest.mark.parametrize("appel", ["api", "", None])
def test_l_appel_par_claude_p_ou_par_lots(media, appel):
    decl = complet()
    decl["papier"]["appel"] = appel
    refuse(decl, media, "appel")


@pytest.mark.parametrize(
    "texte",
    [{"integral": "oui"}, {"mots_max": 999}, {"mots_max": 200001}, {"mots_max": True}, {"pdf": 1}, "entier"],
)
def test_le_texte_lu(media, texte):
    decl = complet()
    decl["papier"]["texte"] = texte
    refuse(decl, media, "texte")


@pytest.mark.parametrize("extraction", [{"volume": "procedes/absent.txt"}, {"autre": 1}, "volume.txt"])
def test_la_consigne_de_volume_de_l_extraction(media, extraction):
    decl = complet()
    decl["papier"]["extraction"] = extraction
    refuse(decl, media, "extraction")


# --- au registre


def registre(tmp_path: Path, procedes_: dict) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": [
                    {
                        "cle": "blog",
                        "plateforme": "Flux RSS",
                        "nom": "Blog",
                        "dossier": "rss/blog",
                        "abonnement": "gratuit",
                        "flux": "https://exemple.org/flux",
                    }
                ],
                "graphes": {"essai": {"libelle": "Essai", "domaine": "essai", "procedes": procedes_}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return p


def test_le_registre_accepte_les_procedes_d_un_graphe(media, monkeypatch):
    monkeypatch.setattr(cfg, "RACINE", media)
    conf = cfg.charger(registre(media, complet()))
    assert conf["graphes"]["essai"]["procedes"]["papier"]["contenu"] == {"champ": "arxiv_id"}


def test_le_registre_nomme_le_graphe_d_un_procede_refuse(media, monkeypatch):
    monkeypatch.setattr(cfg, "RACINE", media)
    decl = complet()
    del decl["papier"]["convention"]
    with pytest.raises(cfg.ConfigInvalide, match=r"graphes\.essai\.procedes.*convention"):
        cfg.charger(registre(media, decl))

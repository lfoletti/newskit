"""Le rédacteur lit la source jusqu'à son apport, et le billet en prend l'angle (mainteneur, 18.11.2026 : « l'angle
pris par le rédacteur manque parfois la cible », « le bon angle aurait été les mesures à prendre, pas le fait divers » ;
« la longueur de troncature devrait [être] une variable média-spécifique et par défaut être réglée sur "toute la
longueur" »).

Les rédacteurs et les relecteurs lisaient une source tronquée, et pas à la même longueur : 1 200 caractères pour le
rédacteur français, 3 000 pour le relecteur et pour la version anglaise. Un article qui ouvre sur une anecdote ne
montrait au rédacteur français que l'anecdote, et les deux langues racontaient deux histoires. Les adresses des liens
Markdown prenaient une part de ces caractères sans rien dire.
"""

from __future__ import annotations

import pytest

from newskit import config_sources as cfg
from newskit import fil
from newskit import fil_anglais as fa
from newskit import fil_fiche as ff
from newskit import lettre as lt

LIEN = "[lien](https://exemple.org/" + "x" * 60 + ")"  # 4 caractères lus, 90 écrits
DECLARATIONS = [({}, None), ({"source_max": 5000}, 5000)]


def _source() -> str:
    """Une source dont l'apport (VU) tombe après 5 000 caractères écrits, mais avant 5 000 caractères filtrés ; LOIN
    tombe après 5 000 caractères filtrés."""
    mots = " ".join(["mot"] * 1000)  # 3 999 caractères
    liens = " ".join([LIEN] * 50)  # 4 549 caractères écrits, 249 lus
    return f"---\ntitle: S\n---\n# Titre de la source\n\n{mots} {liens} VU {'z ' * 400}LOIN\n"


@pytest.fixture
def billet(tmp_path, monkeypatch):
    monkeypatch.setattr(fil, "RACINE", tmp_path)
    (tmp_path / "s.md").write_text(_source(), encoding="utf-8")
    return {"chemin": "s.md", "titre": "S", "descriptif": "D", "url": "u", "date": "2026-11-18"}


@pytest.fixture
def longueurs(monkeypatch):
    """Les longueurs demandées à fil.extrait, dans l'ordre des appels."""
    vu: list = []

    def extrait(b, n=None):
        vu.append(n)
        return "source"

    monkeypatch.setattr(fil, "extrait", extrait)
    return vu


def _declarer(monkeypatch, d: dict) -> None:
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"libelle": "IA", **d})


# Le filtrage et le compte


def test_un_lien_garde_son_texte_sans_son_adresse(tmp_path, monkeypatch):
    monkeypatch.setattr(fil, "RACINE", tmp_path)
    (tmp_path / "a.md").write_text(
        "# Titre\n\nVoir [le rapport](https://a.b/c) et ![photo](https://i.b/p.png) <b>gras</b> "
        "https://nue.example/x [![logo](https://i.b/l.png)](https://a.b) fin",
        encoding="utf-8",
    )
    assert fil.extrait({"chemin": "a.md"}) == "Titre Voir le rapport et gras fin"


def test_le_compte_se_fait_apres_le_filtrage(billet):
    s = fil.extrait(billet, 5000)
    assert len(s) <= 5000 and "VU" in s and "LOIN" not in s and "https" not in s


def test_sans_longueur_la_source_est_lue_entiere(billet):
    assert fil.extrait(billet).endswith("LOIN")


# La longueur est celle du média


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_le_media_declare_sa_longueur(monkeypatch, declaration, attendu):
    _declarer(monkeypatch, declaration)
    assert fil.source_max("g") == attendu


@pytest.mark.parametrize("valeur", [5000, 1])
def test_une_longueur_entiere_positive_est_acceptee(valeur):
    assert cfg.valider_source_max(valeur, "g") == valeur


@pytest.mark.parametrize("valeur", [0, -5, "5000", 5000.0, True, None])
def test_une_autre_longueur_est_refusee(valeur):
    with pytest.raises(cfg.ConfigInvalide, match="source_max"):
        cfg.valider_source_max(valeur, "g")


def test_le_chargement_du_registre_valide_source_max():
    import copy

    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    d["graphes"][g]["source_max"] = 5000
    assert cfg._valider(d, cfg.FICHIER)
    d["graphes"][g]["source_max"] = 0
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.source_max"):
        cfg._valider(d, cfg.FICHIER)


# Chaque lecture de la source prend la longueur du média


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_le_redacteur_francais_lit_la_longueur_du_media(monkeypatch, longueurs, declaration, attendu):
    _declarer(monkeypatch, declaration)
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil.lt, "regles", lambda g=None: "")
    monkeypatch.setattr(fil.rf, "consigne", lambda g, maintenant=None: "")
    monkeypatch.setattr(fil, "demander", lambda *a, **k: ({"billets": []}, 0.0))
    fil.rediger([{"titre": "T", "chemin": "s.md"}], [], "g")
    assert longueurs == [attendu]


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_le_relecteur_francais_lit_la_longueur_du_media(monkeypatch, longueurs, declaration, attendu):
    _declarer(monkeypatch, declaration)
    monkeypatch.setattr(fil, "doutes", lambda t, x, f: [])
    monkeypatch.setattr(fil, "demander", lambda *a, **k: ({"billets": []}, 0.0))
    fil.juger([("T", "X", {"chemin": "s.md"}, {})], "g")
    assert longueurs == [attendu]


def test_le_relecteur_d_une_fiche_lit_la_source_entiere_quel_que_soit_le_media(monkeypatch, longueurs):
    _declarer(monkeypatch, {"source_max": 5000})
    monkeypatch.setattr(fil, "doutes", lambda t, x, f: [])
    monkeypatch.setattr(fil, "demander", lambda *a, **k: ({"billets": []}, 0.0))
    fil.juger([("T", "X", {"chemin": "s.md"}, {})], "g", source=None)
    assert longueurs == [None]


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_la_reecriture_francaise_lit_la_longueur_du_media(monkeypatch, longueurs, declaration, attendu):
    _declarer(monkeypatch, declaration)
    b = {"titre": "T", "descriptif": "D", "chemin": "s.md"}
    monkeypatch.setattr(fil.lt, "charger_fiches", lambda cs: {c: {"chemin": c} for c in cs})
    monkeypatch.setattr(fil.lt, "regles", lambda g=None: "")
    monkeypatch.setattr(fil, "forme", lambda t, x, f: [])
    monkeypatch.setattr(fil, "journaliser_refus", lambda g, r: None)
    monkeypatch.setattr(fil, "juger", lambda p, g: ({0: {"absents": [], "clair": False, "motif": "m"}}, 0.0))
    notes = []

    def demander(inv, schema, graphe, note, **k):
        notes.append(note)
        return {"billets": []}, 0.0

    monkeypatch.setattr(fil, "demander", demander)
    fil.relire([b], "g")
    assert notes and longueurs and set(longueurs) == {attendu}


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_le_redacteur_le_relecteur_et_la_reecriture_anglais_lisent_la_longueur_du_media(
    monkeypatch, longueurs, declaration, attendu
):
    _declarer(monkeypatch, declaration)
    monkeypatch.setattr(fa.lt, "regles", lambda g=None, langue="fr": "")
    monkeypatch.setattr(fa.fil, "doutes", lambda t, x, f: [])
    monkeypatch.setattr(fa, "forme", lambda t, x: [])
    relus = []

    def demander(inv, schema, graphe, note, modele=None):
        if note == "relecteur anglais":
            relus.append(note)
            return {"billets": [{"n": 0, "absents": [], "clair": len(relus) > 1, "motif": "m"}]}, 0.0
        return {"billets": [{"n": 0, "titre": "T", "texte": "X"}]}, 0.0

    monkeypatch.setattr(fa.fil, "demander", demander)
    fa.ecrire([({"chemin": "s.md", "titre": "S"}, {"titre": "S"})], "g")
    assert len(longueurs) == 4 and set(longueurs) == {attendu}


@pytest.mark.parametrize(("declaration", "attendu"), DECLARATIONS)
def test_le_redacteur_d_un_billet_sans_arbre_lit_la_longueur_du_media(monkeypatch, longueurs, declaration, attendu):
    _declarer(monkeypatch, declaration)
    monkeypatch.setattr(ff, "a_ecrire", lambda F: F["billets"])
    monkeypatch.setattr(ff.lt, "charger_fiches", lambda cs: {c: {} for c in cs})
    monkeypatch.setattr(ff.lt, "regles", lambda g=None: "")
    monkeypatch.setattr(fil, "demander", lambda *a, **k: ({"billets": []}, 0.0))
    ff._etoffer({"billets": [{"titre": "T", "chemin": "s.md"}]}, "g")
    assert longueurs == [attendu]


# L'angle


def test_la_redaction_francaise_prend_l_angle_de_l_apport():
    assert "accrocher le lecteur" in lt.REGLES
    assert "accrocher le lecteur" in lt.regles(None)


def test_la_redaction_anglaise_prend_l_angle_de_l_apport():
    assert "hook the reader" in fa.REGLES
    assert "hook the reader" in lt.regles(None, "en")


def test_le_relecteur_francais_refuse_l_angle_de_l_accroche():
    assert "accrocher le lecteur" in fil.RELECTEUR


def test_le_relecteur_anglais_refuse_l_angle_de_l_accroche():
    assert "hook the reader" in fa.RELECTURE

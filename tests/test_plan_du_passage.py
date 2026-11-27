"""Le passage du cerveau choisit sa voie par son coût prévu, et le dit (mainteneur, 27.11.2026).

« Une reconstruction complète n'est en réalité presque jamais nécessaire et elle est coûteuse en temps. » Le passage
choisissait la reconstruction complète au-delà d'un nombre fixe d'effacements (3 000), calé le 16.11.2026 sur une
reconstruction de 27 minutes. Le 27.11.2026, 3 307 documents à remplacer sur 23 017 ont lancé une reconstruction de
3 h 15, quand le passage en place en demandait moins de 40 minutes.

Le passage dresse un plan : la durée prévue de chaque voie, d'après ce que le comptage annonce et ce que les passages
précédents du graphe ont mesuré. Il prend la voie la moins coûteuse. La reconstruction complète reste aux cas nommés
(aucun cerveau précédent, relevé absent, couche 1 changée, binaire trop ancien, demande) et au cas mesuré où elle
coûte moins que le passage en place. Le bilan garde le plan, la durée faite et l'écart : au-delà de 15 %, le passage le
signale (règle du 02.11.2026)."""

from __future__ import annotations

import json
import types

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg

INDEXES = cc.INDEX_PROVENANCE_DES
ANCIEN = (2, 153, 9)
DU_27_11 = {"inchanges": 19710, "remplaces": 3307, "ajoutes": 0, "retires": 0}


def test_les_couts_de_depart_sont_ceux_qui_ont_ete_mesures():
    assert (cc.S_PAR_DOCUMENT, cc.S_PAR_EFFACEMENT_INDEXES, cc.S_PAR_EFFACEMENT) == (0.5, 0.19, 4.0)
    assert (cc.DEPASSEMENT, cc.PART_REECRITE) == (0.15, 0.1)


def test_le_plan_chiffre_les_deux_voies():
    assert cc.plan_du_passage(DU_27_11, {}, INDEXES) == {
        "documents": 23017,
        "ecrits": 3307,
        "effacements": 3307,
        "en_place_s": 2282,  # 3 307 × 0,5 s d'écriture + 3 307 × 0,19 s d'effacement
        "complet_s": 11508,  # 23 017 × 0,5 s
    }


def test_un_retrait_compte_deux_effacements_et_un_document_migre_ne_coute_rien():
    plan = cc.plan_du_passage({"inchanges": 10, "migres": 80, "ajoutes": 4, "retires": 6}, {}, INDEXES)
    assert (plan["documents"], plan["ecrits"], plan["effacements"]) == (94, 4, 12)


def test_les_mesures_du_graphe_remplacent_les_couts_de_depart():
    plan = cc.plan_du_passage(DU_27_11, {"s_par_document": 0.1, "fixe_en_place_s": 600}, INDEXES)
    assert (plan["en_place_s"], plan["complet_s"]) == (600 + 331 + 628, 2302)


def test_un_binaire_sans_index_de_provenance_efface_plus_cher():
    assert cc.plan_du_passage(DU_27_11, {}, ANCIEN)["en_place_s"] == round(3307 * 0.5 + 3307 * 4.0)


@pytest.mark.parametrize(
    "compte, version, force, attendu",
    [
        (DU_27_11, INDEXES, None, ("en_place", "")),  # le cas du 27.11.2026 : en place, plus de reconstruction
        ({"remplaces": 23017}, INDEXES, None, ("complet", "en place prévu en 15882 s, reconstruction en 11508 s")),
        (DU_27_11, ANCIEN, None, ("complet", "en place prévu en 14882 s, reconstruction en 11508 s")),
        ({"remplaces": 23017}, INDEXES, "en_place", ("en_place", "")),  # forcé : le plan ne décide pas
        ({"inchanges": 5}, INDEXES, None, ("en_place", "")),  # rien à écrire : jamais de reconstruction
    ],
)
def test_le_passage_prend_la_voie_la_moins_couteuse(compte, version, force, attendu):
    plan = cc.plan_du_passage(compte, {}, version)
    assert cc.mode_selon_le_plan("en_place", "", plan, force) == attendu


def test_a_cout_egal_le_passage_reste_en_place():
    assert cc.mode_selon_le_plan("en_place", "", {"en_place_s": 100, "complet_s": 100}, None) == ("en_place", "")


def test_une_reconstruction_deja_decidee_garde_sa_raison():
    plan = cc.plan_du_passage(DU_27_11, {}, INDEXES)
    assert cc.mode_selon_le_plan("complet", "la couche 1 a changé", plan, None) == ("complet", "la couche 1 a changé")


@pytest.mark.parametrize(
    "compte, alerte",
    [
        (DU_27_11, True),  # 14 % du cerveau à réécrire : une version a peut-être changé l'empreinte
        ({"inchanges": 22998, "remplaces": 19}, False),
        ({"ajoutes": 50}, False),  # un cerveau qui grandit n'est pas réécrit
    ],
)
def test_une_grande_part_de_documents_a_remplacer_se_signale(compte, alerte):
    texte = cc.alerte_du_plan(cc.plan_du_passage(compte, {}, INDEXES))
    assert bool(texte) is alerte
    if alerte:
        assert "3307 documents sur 23017" in texte and "FORMES_PRECEDENTES" in texte


@pytest.mark.parametrize(
    "prevu, fait, attendu",
    [(100, 115, 0.15), (100, 116, 0.16), (100, 60, -0.4), (0, 50, None), (None, 50, None)],
)
def test_l_ecart_au_plan(prevu, fait, attendu):
    assert cc.ecart_au_plan(prevu, fait) == attendu


def test_une_reconstruction_complete_mesure_le_cout_d_un_document():
    m = cc.mesures_apres({"fixe_en_place_s": 40}, "complet", {"ajoutes": 2000}, 500.0, INDEXES)
    assert m == {"fixe_en_place_s": 40, "s_par_document": 0.25}


def test_un_passage_en_place_mesure_sa_part_fixe():
    m = cc.mesures_apres({"s_par_document": 0.25}, "en_place", {"remplaces": 100, "retires": 5}, 300.0, INDEXES)
    assert m == {"s_par_document": 0.25, "fixe_en_place_s": 273.1}  # 300 − 100 × 0,25 − 110 × 0,19
    court = cc.mesures_apres({}, "en_place", {"remplaces": 100}, 10.0, INDEXES)
    assert court == {"fixe_en_place_s": 0.0}  # jamais négative


def test_une_reconstruction_vide_ne_mesure_rien():
    assert cc.mesures_apres({"s_par_document": 0.3}, "complet", {}, 12.0, INDEXES) == {"s_par_document": 0.3}


# --- la construction entière : le plan s'écrit avant, le bilan le compare au fait

Faux = type(
    "Faux",
    (),
    {
        "__init__": lambda self, *a, **k: None,
        "adresse": "a",
        "jeton": "j",
        "url": "u",
        "dim": 4,
        "nom": "n",
        "__enter__": lambda self: self,
        "__exit__": lambda self, *a: None,
    },
)


@pytest.fixture
def poste(tmp_path, monkeypatch):
    """Un graphe dont la construction va au bout sans serveur ; `poste.compte` est ce que rend le comptage."""
    p = types.SimpleNamespace(compte={}, appels=[], clones=[], horloge=[1000.0], dossier=tmp_path)

    def lancer(args, **_):
        p.appels.append(args)
        if "--compter" in args:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(p.compte) + "\n", stderr="")
        (tmp_path / "g.neuf.empreintes.json").write_text("{}", encoding="utf-8")
        (tmp_path / "g.neuf.db").write_bytes(b"neuf")
        p.horloge[0] += p.duree
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(p.compte) + "\n", stderr="")

    p.duree = 0.0
    (tmp_path / "g.db").write_bytes(b"ancien")
    (tmp_path / "g.empreintes.json").write_text("{}", encoding="utf-8")
    (tmp_path / "g.json").write_text(json.dumps({"encodeur": "n", "publie": True}), encoding="utf-8")
    monkeypatch.setattr(cc.time, "time", lambda: p.horloge[0])
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / "g.db")
    monkeypatch.setattr(cc, "version_binaire", lambda *a: INDEXES)
    monkeypatch.setattr(cc, "engage_couche1", lambda g: False)
    monkeypatch.setattr(cc, "servi", lambda g: False)
    monkeypatch.setattr(cc, "place_libre", lambda d: 10**12)
    monkeypatch.setattr(cc, "cloner", lambda s, d: p.clones.append(d))
    monkeypatch.setattr(cc, "Serveur", Faux)
    monkeypatch.setattr(cc, "Encodeur", Faux)
    monkeypatch.setattr(cc, "controle", lambda c, octets: {"lignes": 3, "a_encoder": 0, "sans_document": 0})
    monkeypatch.setattr(cc, "publiable", lambda fin: True)
    monkeypatch.setattr(cc, "elaguer_journal", lambda c: 0)
    monkeypatch.setattr(cc, "elaguer_historique", lambda c: 0)
    monkeypatch.setattr(cc, "reprendre_vecteurs", lambda *a: 0)
    monkeypatch.setattr(cc, "_publier_en_place_du_graphe", lambda g, neuf, cible: False)
    monkeypatch.setattr(cc, "_publier_du_graphe", lambda g, neuf, cible: None)
    monkeypatch.setattr(cc, "lancer_client", lancer)
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: False)
    monkeypatch.setattr(
        cfg,
        "chemins_du_graphe",
        lambda g: {"base": tmp_path / "base", "graphe": tmp_path / "absent.json", "carte": tmp_path / "absente.md"},
    )
    return p


def test_le_cas_du_27_11_passe_en_place_et_le_bilan_garde_le_plan(poste, capsys):
    poste.compte, poste.duree = DU_27_11, 2300.0
    bilan = cc.construire("g")
    assert bilan["mode"] == "en_place" and poste.clones
    assert bilan["plan"] == cc.plan_du_passage(DU_27_11, {}, INDEXES)
    assert (bilan["prevu_s"], bilan["duree_s"], bilan["ecart_au_plan"]) == (2282, 2300.0, 0.01)
    assert bilan["mesures"] == {"fixe_en_place_s": 18.2}
    err = capsys.readouterr().err
    assert "en place, prévu en 2282 s (reconstruction complète : 11508 s)" in err
    assert "3307 documents sur 23017" in err  # l'alerte : pourquoi tant d'empreintes changent
    assert "contrôle" not in err
    assert json.loads((poste.dossier / "g.json").read_text(encoding="utf-8"))["plan"] == bilan["plan"]


def test_un_depassement_de_plus_de_15_pour_cent_se_signale(poste, capsys):
    poste.compte, poste.duree = {"inchanges": 900, "remplaces": 100}, 80.0
    bilan = cc.construire("g")
    assert (bilan["prevu_s"], bilan["ecart_au_plan"]) == (69, 0.16)
    assert "contrôle : 80 s pour 69 s prévues (+16 %)" in capsys.readouterr().err


def test_les_mesures_d_un_passage_servent_au_suivant(poste):
    poste.compte, poste.duree = {"inchanges": 900, "remplaces": 100}, 669.0
    assert cc.construire("g")["mesures"] == {"fixe_en_place_s": 600.0}
    poste.duree = 0.0
    assert cc.construire("g")["plan"]["en_place_s"] == 669


def test_une_reconstruction_plus_courte_que_le_passage_en_place_se_fait_et_dit_pourquoi(poste):
    poste.compte, poste.duree = {"remplaces": 1000}, 400.0
    bilan = cc.construire("g")
    assert bilan["mode"] == "complet" and not poste.clones
    assert bilan["raison"] == "en place prévu en 690 s, reconstruction en 500 s"
    assert (bilan["prevu_s"], bilan["ecart_au_plan"]) == (500, -0.2)
    assert bilan["mesures"] == {"s_par_document": 0.4}  # 400 s pour 1 000 documents


def test_une_reconstruction_demandee_prevoit_sa_duree_d_apres_le_bilan_precedent(poste, capsys):
    (poste.dossier / "g.json").write_text(
        json.dumps({"encodeur": "n", "inchanges": 1500, "ajoutes": 500, "mesures": {"s_par_document": 0.2}}),
        encoding="utf-8",
    )
    poste.compte, poste.duree = {"ajoutes": 2000}, 400.0
    bilan = cc.construire("g", force="complet")
    assert bilan["plan"] == {"documents": 2000, "complet_s": 400} and bilan["prevu_s"] == 400
    assert "reconstruction complète (demandé), prévue en 400 s" in capsys.readouterr().err


def test_un_premier_cerveau_n_a_pas_de_plan(poste):
    (poste.dossier / "g.db").unlink()
    (poste.dossier / "g.json").unlink()
    poste.compte, poste.duree = {"ajoutes": 10}, 5.0
    bilan = cc.construire("g")
    assert bilan["mode"] == "complet" and bilan["plan"] == {} and bilan["prevu_s"] is None
    assert bilan["ecart_au_plan"] is None and bilan["mesures"] == {"s_par_document": 0.5}

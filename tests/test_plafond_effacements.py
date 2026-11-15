"""Un versement en place qui effacerait trop se fait en reconstruction complète (mainteneur, 14.11.2026).

La routine d'un graphe de gouvernance, 14.11.2026 au soir : le versement en place a passé plus d'une heure dans
`delete_document_graph` (972 appels, 4,0 s en moyenne) et `knowledge_delete` (6,0 s), quand la reconstruction
complète du matin avait pris 32 minutes. CortexDB lit tout le graphe pour chaque document qu'il efface. Le puits
compte donc d'abord, sans serveur, ce qu'un passage en place effacerait, et au-delà de EFFACEMENTS_MAX le cerveau se
reconstruit en entier."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import puits_cortexdb as pc


def versement(ident: str, texte: str) -> dict:
    return {"document": {"document_id": ident, "content": texte}, "entites": [], "relations": [], "origine": ident}


class SansServeur:
    """Un client qu'aucun appel ne doit toucher."""

    def __getattr__(self, nom):
        raise AssertionError(f"le comptage a parlé au serveur ({nom})")


def test_compter_classe_chaque_versement_sans_parler_au_serveur():
    garde, change, neuf = versement("garde", "a"), versement("change", "b"), versement("neuf", "c")
    avant = {"garde": pc.empreinte(garde), "change": "ancienne", "parti": "x"}
    bilan: dict = {}
    ep = pc.EnPlace(SansServeur(), avant, bilan, compter=True)
    assert [ep.verser(v, {}) for v in (garde, change, neuf)] == [None, None, None]
    ep.retirer_absents()
    assert bilan == {"inchanges": 1, "remplaces": 1, "ajoutes": 1, "retires": 1}


def _main(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["puits_cortexdb.py", "--graphe", "g", *args])
    pc.main()


def test_compter_demande_les_empreintes_du_cerveau_precedent(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _main(monkeypatch, "--compter")
    assert e.value.code == 2


def test_compter_se_passe_du_client_et_n_ecrit_pas_les_empreintes(tmp_path, monkeypatch, capsys):
    # cortexdb_client n'est pas installé dans l'environnement des tests : l'importer ferait tomber main().
    assert "cortexdb_client" not in sys.modules
    avant, apres = tmp_path / "avant.json", tmp_path / "apres.json"
    avant.write_text(json.dumps({"parti": "x", "autre": "y"}), encoding="utf-8")
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path, "graphe": tmp_path / "absent.json"})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: {})
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [])
    _main(monkeypatch, "--compter", "--empreintes-avant", str(avant), "--empreintes-apres", str(apres))
    bilan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (bilan["retires"], bilan["remplaces"], bilan["ajoutes"]) == (2, 0, 0)
    assert not apres.exists()


@pytest.mark.parametrize(
    "mode, compte, force, attendu",
    [
        ("en_place", {"remplaces": 100, "retires": 100}, None, ("en_place", "")),
        (
            "en_place",
            {"remplaces": 101, "retires": 100},
            None,
            ("complet", f"301 effacements en place > {cc.EFFACEMENTS_MAX}"),
        ),
        (
            "en_place",
            {"remplaces": 0, "retires": 151},
            None,
            ("complet", f"302 effacements en place > {cc.EFFACEMENTS_MAX}"),
        ),
        ("en_place", {"remplaces": 5000}, "en_place", ("en_place", "")),  # forcé : le seuil ne s'applique pas
    ],
)
def test_le_mode_selon_les_effacements(mode, compte, force, attendu):
    assert cc.EFFACEMENTS_MAX == 300
    assert cc.mode_selon_effacements(mode, "", compte, force) == attendu


def test_un_passage_complet_ne_compte_pas():
    assert cc.mode_selon_effacements("complet", "demandé", None, None) == ("complet", "demandé")


class Arret(Exception):
    pass


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
    """Un graphe qui peut passer en place ; `poste.compte` est ce que rend le comptage, et le versement s'arrête."""
    p = types.SimpleNamespace(appels=[], clones=[], compte={}, neuf=tmp_path / "g.neuf.db")

    def lancer(args, **_):
        p.appels.append(args)
        if "--compter" in args:
            assert not p.neuf.exists() and not p.clones  # rien n'est cloné avant le compte
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(p.compte) + "\n", stderr="")
        raise Arret

    (tmp_path / "g.db").write_bytes(b"")
    (tmp_path / "g.empreintes.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / "g.db")
    monkeypatch.setattr(cc, "version_binaire", lambda *a: cc.EN_PLACE_DES)
    monkeypatch.setattr(cc, "engage_couche1", lambda g: False)
    monkeypatch.setattr(cc, "cloner", lambda s, d: p.clones.append(d))
    monkeypatch.setattr(cc, "Serveur", Faux)
    monkeypatch.setattr(cc, "Encodeur", Faux)
    monkeypatch.setattr(cc, "lancer_client", lancer)
    monkeypatch.setattr(
        cfg,
        "chemins_du_graphe",
        lambda g: {"base": tmp_path / "base", "graphe": tmp_path / "absent.json", "carte": tmp_path / "absente.md"},
    )
    return p


@pytest.mark.parametrize("effacements, en_place", [(10, True), (cc.EFFACEMENTS_MAX + 1, False)])
def test_construire_compte_avant_de_cloner(poste, effacements, en_place):
    poste.compte = {"remplaces": effacements, "retires": 0, "ajoutes": 0, "inchanges": 7}
    with pytest.raises(Arret):
        cc.construire("g")
    compte, verse = poste.appels
    assert "--compter" in compte and "--empreintes-avant" in compte and "--empreintes-apres" not in compte
    assert ("--empreintes-avant" in verse) is en_place and bool(poste.clones) is en_place
    assert "--compter" not in verse


def test_un_en_place_force_ne_compte_pas(poste):
    with pytest.raises(Arret):
        cc.construire("g", force="en_place")
    assert len(poste.appels) == 1 and "--compter" not in poste.appels[0] and poste.clones


def test_un_comptage_qui_echoue_arrete_le_passage(poste, monkeypatch):
    monkeypatch.setattr(
        cc, "lancer_client", lambda args, **_: types.SimpleNamespace(returncode=1, stdout="", stderr="lot illisible")
    )
    with pytest.raises(RuntimeError, match="comptage : lot illisible"):
        cc.construire("g")
    assert not poste.clones


def test_hors_comptage_un_remplace_s_efface_avant_d_etre_verse_et_un_absent_se_retire(monkeypatch):
    appels = []
    monkeypatch.setattr(pc.EnPlace, "appel", lambda self, nom, **a: appels.append((nom, a)) or {})
    monkeypatch.setattr(
        pc, "verser", lambda client, v, vus: appels.append(("verser", v["document"]["document_id"])) or {}
    )
    ep = pc.EnPlace(object(), {"change": "ancienne", "parti": "x"}, {})
    ep.verser(versement("change", "b"), {})
    ep.retirer_absents()
    assert appels == [
        ("delete_document_graph", {"document_id": "change"}),
        ("verser", "change"),
        ("delete_document_graph", {"document_id": "parti"}),
        ("knowledge_delete", {"knowledge_id": "parti"}),
    ]


def test_verser_demande_le_jeton(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _main(monkeypatch)
    assert e.value.code == 2


def test_hors_comptage_le_puits_se_connecte_au_serveur(tmp_path, monkeypatch, capsys):
    import contextlib

    connexions = []
    client = types.ModuleType("cortexdb_client")
    client.CortexClient = types.SimpleNamespace(
        connect=lambda adresse, token: connexions.append((adresse, token)) or contextlib.nullcontext(object())
    )
    monkeypatch.setitem(sys.modules, "cortexdb_client", client)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path, "graphe": tmp_path / "absent.json"})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: {})
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [])
    _main(monkeypatch, "--adresse", "127.0.0.1:9", "--jeton", "j")
    assert connexions == [("127.0.0.1:9", "j")]
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["verses"] == 0


def test_les_vues_s_ecrivent_avant_le_comptage_qui_les_lit(poste, tmp_path, monkeypatch):
    ordre = []
    vues = tmp_path / "vues.json"
    monkeypatch.setattr(cc, "options_du_versement", lambda g, base, couche1: ["--vues", str(vues)])
    monkeypatch.setattr(cc, "ecrire_vues", lambda g, chemin: ordre.append(("vues", chemin)))
    lancer = cc.lancer_client
    monkeypatch.setattr(
        cc, "lancer_client", lambda args, **k: ordre.append(("client", "--compter" in args)) or lancer(args)
    )
    poste.compte = {"remplaces": 1}
    with pytest.raises(Arret):
        cc.construire("g")
    assert ordre == [("vues", vues), ("client", True), ("client", False)]


# Une reconstruction complète garde l'ancien cerveau jusqu'à la publication et écrit le neuf à côté. Le 15.11.2026, la
# passe du matin d'un graphe de gouvernance (1 096 effacements, donc reconstruction complète) a rempli le disque : le neuf
# faisait 4,2 Go, l'ancien 4,0, le versement n'était pas fini. Elle se mesure donc d'abord à la place libre.


@pytest.mark.parametrize(
    "mode, possible, ancien, libre, attendu",
    [
        ("complet", "en_place", 100, 200, ("complet", "r")),  # assez : exactement PLACE_COMPLETE fois l'ancien
        ("complet", "en_place", 100, 199, "en_place"),  # trop peu, en place possible : en place
        ("complet", "complet", 100, 199, SystemExit),  # trop peu, en place impossible : arrêt, l'ancien reste
        ("en_place", "en_place", 100, 0, ("en_place", "r")),  # un passage en place ne se mesure pas
        ("complet", "complet", None, 0, ("complet", "r")),  # aucun ancien cerveau : rien à comparer
    ],
)
def test_le_mode_selon_la_place(tmp_path, mode, possible, ancien, libre, attendu):
    assert cc.PLACE_COMPLETE == 2
    cible = tmp_path / "g.db"
    if ancien is not None:
        cible.write_bytes(b"x" * ancien)
    if attendu is SystemExit:
        with pytest.raises(SystemExit, match="l'ancien cerveau est gardé"):
            cc.mode_selon_place(mode, "r", possible, cible, libre)
    elif attendu == "en_place":
        m, raison = cc.mode_selon_place(mode, "r", possible, cible, libre)
        assert m == "en_place" and "reconstruction complète" in raison and "r" in raison
    else:
        assert cc.mode_selon_place(mode, "r", possible, cible, libre) == attendu


def test_la_place_de_l_ancien_cerveau_compte_son_journal(tmp_path):
    cible = tmp_path / "g.db"
    cible.write_bytes(b"x" * 100)
    (tmp_path / "g.db-wal").write_bytes(b"x" * 50)
    assert cc.mode_selon_place("complet", "r", "en_place", cible, 299)[0] == "en_place"


def test_trop_d_effacements_sans_place_passe_en_place(poste, monkeypatch):
    (poste.neuf.parent / "g.db").write_bytes(b"x" * 100)
    monkeypatch.setattr(cc, "place_libre", lambda d: 150)
    poste.compte = {"remplaces": cc.EFFACEMENTS_MAX + 1, "retires": 0, "ajoutes": 0, "inchanges": 7}
    with pytest.raises(Arret):
        cc.construire("g")
    _, verse = poste.appels
    assert "--empreintes-avant" in verse and poste.clones


def test_trop_d_effacements_avec_place_reconstruit(poste, monkeypatch):
    (poste.neuf.parent / "g.db").write_bytes(b"x" * 100)
    monkeypatch.setattr(cc, "place_libre", lambda d: 200)
    poste.compte = {"remplaces": cc.EFFACEMENTS_MAX + 1, "retires": 0, "ajoutes": 0, "inchanges": 7}
    with pytest.raises(Arret):
        cc.construire("g")
    _, verse = poste.appels
    assert "--empreintes-avant" not in verse and not poste.clones


def test_une_reconstruction_imposee_sans_place_s_arrete_avant_d_ecrire(poste, monkeypatch):
    (poste.neuf.parent / "g.db").write_bytes(b"x" * 100)
    (poste.neuf.parent / "g.json").write_text(json.dumps({"couche1": True}), encoding="utf-8")  # la couche 1 a changé
    monkeypatch.setattr(cc, "place_libre", lambda d: 150)
    with pytest.raises(SystemExit, match="l'ancien cerveau est gardé"):
        cc.construire("g")
    assert not poste.appels and not poste.clones and not poste.neuf.exists()


def test_la_place_libre_est_celle_du_dossier_des_cerveaux(tmp_path):
    import shutil

    assert cc.place_libre(tmp_path) == shutil.disk_usage(tmp_path).free

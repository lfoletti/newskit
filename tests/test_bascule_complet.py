"""Le versement en place bascule en reconstruction complète quand trop de documents changent (Lauro, 14.11.2026 :
« propose la bascule automatique dans newskit »).

Incident du 14.11.2026 (graphe ai-governance) : un changement du code de versement, le 7.11, a changé l'empreinte de
19 147 versements sur 19 752. Le passage en place supprimait puis reversait chaque document un par un, à plusieurs
secondes chacun ; il dépassait les 6 heures du client chaque nuit, et chaque nuit repartait du cerveau du 7.11. Le
cerveau servi n'a plus changé pendant une semaine.

Avant de cloner l'ancien cerveau, le passage compte donc ses versements (empreintes seules, sans serveur) ; si la part
de l'ancien cerveau à reprendre (remplacés et retirés) passe le seuil, il reconstruit en entier.
"""

from __future__ import annotations

import json

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import puits_cortexdb as pc


def v(ident: str, texte: str) -> dict:
    return {"document": {"document_id": ident, "content": texte}, "entites": [], "relations": [], "origine": ident}


class SansClient:
    """Un client qui casse le test à tout appel : compter n'écrit rien."""

    def __getattr__(self, nom):
        raise AssertionError(f"compter a appelé le client ({nom})")


def test_compter_classe_les_versements_sans_appeler_le_serveur():
    avant = {"a": pc.empreinte(v("a", "x")), "b": pc.empreinte(v("b", "x")), "c": pc.empreinte(v("c", "x"))}
    bilan: dict = {}
    ep = pc.EnPlace(SansClient(), avant, bilan, compter=True)
    for x in (v("a", "x"), v("b", "changé"), v("d", "neuf")):
        assert ep.verser(x, {}) is None
    ep.retirer_absents()
    assert {k: bilan[k] for k in ("inchanges", "remplaces", "ajoutes", "retires")} == {
        "inchanges": 1,
        "remplaces": 1,
        "ajoutes": 1,
        "retires": 1,
    }


def test_la_part_a_reprendre_est_celle_de_l_ancien_cerveau():
    """Un ajout coûte autant dans les deux modes ; un remplacement ou un retrait coûte une suppression en place."""
    assert cc.part_a_reprendre({"inchanges": 6, "remplaces": 3, "ajoutes": 50, "retires": 1}) == pytest.approx(0.4)
    assert cc.part_a_reprendre({"inchanges": 0, "remplaces": 0, "ajoutes": 5, "retires": 0}) == 0


@pytest.mark.parametrize(
    ("compte", "attendu"),
    [
        ({"inchanges": 2, "remplaces": 19147, "ajoutes": 1405, "retires": 603}, "complet"),  # 14.11.2026
        ({"inchanges": 19129, "remplaces": 589, "ajoutes": 34, "retires": 50}, "en_place"),  # 07.11.2026
    ],
)
def test_le_mode_suit_la_part_a_reprendre(compte, attendu):
    mode, raison = cc.mode_apres_compte("en_place", "", compte)
    assert mode == attendu
    if attendu == "complet":
        assert raison == "19750 versements sur 19752 à reprendre"


def test_un_en_place_force_ne_bascule_pas():
    compte = {"inchanges": 0, "remplaces": 10, "ajoutes": 0, "retires": 0}
    assert cc.mode_apres_compte("en_place", "", compte, force="en_place") == ("en_place", "")
    assert cc.mode_apres_compte("complet", "demandé", compte) == ("complet", "demandé")


def test_construire_compte_avant_de_cloner(monkeypatch, tmp_path):
    """Le compte passe par le client (--compter), avant tout clone : en place, rien ne s'écrit avant la décision."""
    appels: list = []
    monkeypatch.setattr(cc, "lancer_client", lambda args: appels.append(args) or _fini(compte_trop()))
    bilan = cc.compter_versements("g", ["--pyramide", "--empreintes-avant", "e.json"])
    assert bilan == compte_trop()
    (args,) = appels
    assert "--compter" in args and "--empreintes-avant" in args and "--adresse" not in args


def compte_trop() -> dict:
    return {"inchanges": 1, "remplaces": 9, "ajoutes": 0, "retires": 0}


def _fini(bilan: dict):
    return type("R", (), {"returncode": 0, "stdout": "  1/2\n" + json.dumps(bilan), "stderr": ""})()


class Arret(Exception):
    pass


@pytest.mark.parametrize(
    ("compte", "attendu"), [(compte_trop(), "complet"), ({"inchanges": 9, "remplaces": 1}, "en_place")]
)
def test_construire_decide_sur_le_compte_avant_de_cloner(monkeypatch, tmp_path, compte, attendu):
    ordre: list = []
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "attendre_encodeur", lambda g: 0)
    monkeypatch.setattr(cc, "version_binaire", lambda *a: cc.EN_PLACE_DES)
    monkeypatch.setattr(cc, "engage_couche1", lambda g: False)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / "g.db")
    monkeypatch.setattr(cc, "options_du_versement", lambda *a: ["--pyramide", "--vues", "v.json"])
    monkeypatch.setattr(cc, "ecrire_vues", lambda g, p: ordre.append("vues"))
    monkeypatch.setattr(cc, "compter_versements", lambda g, o: ordre.append(("compte", o)) or compte)
    monkeypatch.setattr(cc, "effacer", lambda p: ordre.append("effacer"))
    monkeypatch.setattr(cc, "cloner", lambda a, b: ordre.append("cloner"))

    class Serveur:
        def __init__(self, *a, **k):
            raise Arret

    monkeypatch.setattr(cc, "Serveur", Serveur)
    monkeypatch.setattr(cc, "Encodeur", lambda *a, **k: Serveur())
    (tmp_path / "g.db").write_bytes(b"")
    (tmp_path / "g.empreintes.json").write_text("{}")
    (tmp_path / "g.json").write_text('{"couche1": false}')
    with pytest.raises(Arret):
        cc.construire("g")
    # les vues se calculent avant le compte : il les lit
    assert ordre[0] == "vues" and ordre[1][0] == "compte" and "--empreintes-avant" in ordre[1][1]
    assert ordre[2:] == (["effacer", "cloner"] if attendu == "en_place" else ["effacer"])


def _graphe_vide(monkeypatch, tmp_path):
    from newskit import config_sources as cfg

    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path, "graphe": tmp_path / "absent.json"})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: {})
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [])


def test_le_puits_compte_sans_se_connecter(monkeypatch, tmp_path, capsys):
    _graphe_vide(monkeypatch, tmp_path)
    monkeypatch.setitem(__import__("sys").modules, "cortexdb_client", None)  # tout import du client casse
    avant, apres = tmp_path / "avant.json", tmp_path / "apres.json"
    avant.write_text(json.dumps({"vieux": "h"}))
    monkeypatch.setattr(
        "sys.argv",
        ["puits", "--graphe", "g", "--compter", "--empreintes-avant", str(avant), "--empreintes-apres", str(apres)],
    )
    assert pc.main() == 0
    bilan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert bilan["retires"] == 1 and not apres.exists()


@pytest.mark.parametrize(
    ("argv", "message"),
    [(["--compter"], "--compter demande --empreintes-avant"), ([], "--jeton est requis pour verser")],
)
def test_le_puits_refuse_un_compte_sans_empreintes_et_un_versement_sans_jeton(monkeypatch, capsys, argv, message):
    monkeypatch.setattr("sys.argv", ["puits", "--graphe", "g", *argv])
    with pytest.raises(SystemExit):
        pc.main()
    assert message in capsys.readouterr().err


def test_un_compte_qui_echoue_arrete_le_passage(monkeypatch):
    echec = type("R", (), {"returncode": 1, "stdout": "", "stderr": "fiche illisible"})()
    monkeypatch.setattr(cc, "lancer_client", lambda args: echec)
    with pytest.raises(RuntimeError, match="compte : fiche illisible"):
        cc.compter_versements("g", [])

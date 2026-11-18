"""Une clé d'écriture pour l'édition.

Avec un fichier de clés, CortexDB n'admet une écriture que d'une clé `read-write` ; la clé de l'édition est `read-only`.
Un graphe continu reçoit ses fiches dans le cerveau servi : l'édition y a une seconde clé, d'écriture, publiée à part
dans le fichier d'adresse. Qui lit garde la clé de lecture ; un graphe sans cerveau continu n'a pas de clé d'écriture."""

from __future__ import annotations

import json
import stat
import subprocess
import sys

import pytest

from newskit import aiguilleur_cortexdb as ag
from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import lecture_cortexdb as lc
from newskit import serveur_cortexdb as sc
from newskit import synthese as sy


@pytest.fixture(autouse=True)
def edition(monkeypatch):
    monkeypatch.setattr(cfg, "edition", lambda: {"nom": "media"})


def cles_de(p) -> dict:
    return {k["id"]: k for k in json.loads(p.read_text(encoding="utf-8"))["keys"]}


# --- la clé


def test_la_cle_d_ecriture_est_creee_une_fois_en_read_write(tmp_path):
    p = tmp_path / "g.json"
    secret = sc.assurer_cle(p, "g", ecriture=True)
    assert cles_de(p) == {"media/g/ecriture": {"id": "media/g/ecriture", "secret": secret, "clearance": "read-write"}}
    assert sc.assurer_cle(p, "g", ecriture=True) == secret
    assert len(cles_de(p)) == 1
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_la_cle_de_lecture_reste_read_only(tmp_path):
    p = tmp_path / "g.json"
    ecriture = sc.assurer_cle(p, "g", ecriture=True)
    lecture = sc.assurer_cle(p, "g")
    assert lecture != ecriture
    assert cles_de(p)["media/g"] == {"id": "media/g", "secret": lecture, "clearance": "read-only"}
    assert sc.assurer_cle(p, "g") == lecture and sc.assurer_cle(p, "g", ecriture=True) == ecriture


# --- le fichier d'adresse


def test_l_adresse_publie_la_cle_d_ecriture_a_part(tmp_path):
    p = tmp_path / "g.adresse"
    sc.ecrire_adresse(p, "127.0.0.1:4", "lecture", pid=7, jeton_ecriture="ecriture")
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "adresse": "127.0.0.1:4",
        "jeton": "lecture",
        "pid": 7,
        "jeton_ecriture": "ecriture",
    }
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    sc.ecrire_adresse(p, "127.0.0.1:4", "lecture", pid=7)
    assert json.loads(p.read_text(encoding="utf-8")) == {"adresse": "127.0.0.1:4", "jeton": "lecture", "pid": 7}


def test_lire_adresse_rend_toujours_la_cle_de_lecture(tmp_path):
    p = tmp_path / "g.adresse"
    sc.ecrire_adresse(p, "127.0.0.1:4", "lecture", jeton_ecriture="ecriture")
    assert lc.lire_adresse(p) == ("127.0.0.1:4", "lecture")
    assert lc.lire_adresse_ecriture(p) == ("127.0.0.1:4", "ecriture")


def test_sans_cle_d_ecriture_lire_adresse_ecriture_rend_rien(tmp_path):
    p = tmp_path / "g.adresse"
    assert lc.lire_adresse_ecriture(p) is None  # pas de serveur
    sc.ecrire_adresse(p, "127.0.0.1:4", "lecture")
    assert lc.lire_adresse_ecriture(p) is None
    p.write_text(json.dumps({"adresse": "127.0.0.1:4", "jeton": "lecture", "jeton_ecriture": ""}), encoding="utf-8")
    assert lc.lire_adresse_ecriture(p) is None
    p.write_text("illisible", encoding="utf-8")
    assert lc.lire_adresse_ecriture(p) is None


# --- qui reçoit une clé d'écriture


@pytest.mark.parametrize(
    "continu, cles, attendu",
    [
        (True, True, "la clé media/g/ecriture"),
        (True, False, "le jeton du serveur"),  # sans fichier de clés, le jeton écrit déjà
        (False, True, None),
        (False, False, None),
    ],
)
def test_seul_un_graphe_continu_recoit_une_cle_d_ecriture(tmp_path, monkeypatch, continu, cles, attendu):
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: continu)
    p = tmp_path / "g.json"
    recu = sc.cle_d_ecriture(p if cles else None, "g", "jeton du serveur")
    if attendu is None:
        assert recu is None and not p.exists()
    elif cles:
        assert recu == cles_de(p)["media/g/ecriture"]["secret"]
    else:
        assert recu == "jeton du serveur"


class Encodeur:
    url, dim, nom = "http://127.0.0.1:1/v1", 384, "n"

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Processus:
    def __init__(self, *a, **k):
        pass

    def poll(self):
        return 0  # arrêté : le service finit son tour et sort


def service(tmp_path, monkeypatch, continu: bool) -> list[dict]:
    """Les deux services sans cortexdb : rend les fichiers d'adresse qu'ils écrivent, celui de l'aiguilleur d'abord."""
    ecrits = []
    vraie = sc.ecrire_adresse

    def ecrire(p, *a, **k):
        vraie(p, *a, **k)
        ecrits.append(json.loads(p.read_text(encoding="utf-8")))

    class Aiguilleur:
        def __init__(self, ports, lancer, politique, a_relancer=None):
            self.ports, self.lancer, self.fin = ports, lancer, None

        def servir(self):
            for g, port in self.ports.items():
                self.lancer(g, port)

        def arreter(self):
            pass

    (tmp_path / "g.db").write_bytes(b"cerveau")
    (tmp_path / "cles").mkdir()
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: continu)
    monkeypatch.setattr(cfg, "port_cortexdb", lambda g: 40999)
    monkeypatch.setattr(sc, "ecrire_adresse", ecrire)
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    monkeypatch.setattr(sc.signal, "signal", lambda *a: None)
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "Encodeur", Encodeur)
    monkeypatch.setattr(cc, "attendre_port", lambda port, delai: None)
    monkeypatch.setattr(ag, "partages_declares", lambda: {})
    monkeypatch.setattr(ag, "Aiguilleur", Aiguilleur)
    monkeypatch.setattr(sy, "modele_affine", lambda g: None)
    monkeypatch.setattr(sy, "memoire_bornee", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", Processus)
    monkeypatch.setenv("KG_CORTEXDB_BINAIRE", "cortexdb-grpc")
    monkeypatch.setenv("KG_CORTEXDB_CLES_DOSSIER", str(tmp_path / "cles"))
    monkeypatch.setattr(sys, "argv", ["aiguilleur_cortexdb", "--graphes", "g"])
    assert ag.main() == 0
    monkeypatch.setattr(sys, "argv", ["serveur_cortexdb", "--graphe", "g", "--cles", str(tmp_path / "cles" / "g.json")])
    assert sc.main() == 1  # le faux cortexdb est arrêté : le gardien le dit et sort
    return ecrits


def test_les_services_publient_la_cle_d_ecriture_d_un_graphe_continu(tmp_path, monkeypatch):
    aiguilleur, gardien = service(tmp_path, monkeypatch, continu=True)
    cles = cles_de(tmp_path / "cles" / "g.json")
    assert set(cles) == {"media/g", "media/g/ecriture"}
    for adresse in (aiguilleur, gardien):
        assert adresse["jeton"] == cles["media/g"]["secret"]
        assert adresse["jeton_ecriture"] == cles["media/g/ecriture"]["secret"]


def test_les_services_ne_creent_pas_de_cle_d_ecriture_pour_un_autre_graphe(tmp_path, monkeypatch):
    aiguilleur, gardien = service(tmp_path, monkeypatch, continu=False)
    assert set(cles_de(tmp_path / "cles" / "g.json")) == {"media/g"}
    assert "jeton_ecriture" not in aiguilleur and "jeton_ecriture" not in gardien


def test_un_lancement_recree_la_cle_d_ecriture_retiree_du_fichier(tmp_path, monkeypatch):
    """Le fichier de clés est réécrit par un tiers pendant que le service tourne : la clé d'écriture y revient au
    lancement suivant du serveur, et l'adresse publiée la nomme."""
    ecrits = service(tmp_path, monkeypatch, continu=True)
    p = tmp_path / "cles" / "g.json"
    lecture = cles_de(p)["media/g"]
    del ecrits[:]
    vraie, appels = sc.cle_d_ecriture, []

    def cle(*a):
        secret = vraie(*a)
        appels.append(secret)
        if len(appels) == 1:  # après le démarrage du service, avant le lancement du serveur
            p.write_text(json.dumps({"keys": [lecture]}), encoding="utf-8")
        return secret

    monkeypatch.setattr(sc, "cle_d_ecriture", cle)
    monkeypatch.setattr(sys, "argv", ["aiguilleur_cortexdb", "--graphes", "g"])
    assert ag.main() == 0
    au_demarrage, au_lancement = appels
    assert au_lancement != au_demarrage and cles_de(p)["media/g/ecriture"]["secret"] == au_lancement
    assert [e["jeton_ecriture"] for e in ecrits] == [au_demarrage, au_lancement]
    assert {e["jeton"] for e in ecrits} == {lecture["secret"]}


def test_un_lancement_sans_cle_neuve_ne_reecrit_pas_l_adresse(tmp_path, monkeypatch):
    ecrits = service(tmp_path, monkeypatch, continu=True)
    assert len(ecrits) == 2  # une adresse par service

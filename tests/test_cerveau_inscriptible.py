"""Le cerveau mis en place accepte une écriture.

`retirer_cjk` retire les index CJK d'un cerveau publié. CortexDB les recrée vides à l'ouverture et ne les remplit pas :
la première réécriture d'un document échoue (« database disk image is malformed »). Un cerveau continu reçoit des
écritures là où il est servi : il garde donc ses index, et la copie de travail d'un passage ne les fait remplir de
nouveau que s'ils sont vides. La mise en place d'un cerveau prêt est une seule fonction, pour le gardien d'un graphe et
pour l'aiguilleur."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import types

from newskit import aiguilleur_cortexdb as ag
from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import lecture_cortexdb as lc
from newskit import serveur_cortexdb as sc
from newskit import synthese as sy


def cerveau(chemin, lignes: int = 3, version: int = 1, cjk: bool = True):
    """Un cerveau réduit à ce que lisent ces règles : un index CJK (table virtuelle, déclencheur) et sa version."""
    c = sqlite3.connect(chemin)
    c.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, content TEXT)")
    if cjk:
        c.execute("CREATE VIRTUAL TABLE chunks_fts_cjk USING fts5(content, tokenize='trigram')")
        c.execute("CREATE VIRTUAL TABLE messages_fts_cjk USING fts5(content, tokenize='trigram')")
        c.execute(
            "CREATE TRIGGER chunks_cjk_ai AFTER INSERT ON chunks BEGIN "
            "INSERT INTO chunks_fts_cjk(rowid, content) VALUES (new.id, new.content); END"
        )
    for i in range(lignes):
        c.execute("INSERT INTO chunks (content) VALUES (?)", (f"morceau {i}",))
    c.execute(f"PRAGMA user_version = {version}")
    c.commit()
    c.close()
    return chemin


def lire(chemin, requete: str):
    c = sqlite3.connect(chemin)
    try:
        return c.execute(requete).fetchall()
    finally:
        c.close()


def objets_cjk(chemin) -> int:
    return lire(chemin, "SELECT count(*) FROM sqlite_master WHERE name IN ('chunks_fts_cjk', 'chunks_cjk_ai')")[0][0]


# --- la mise en place


def test_la_mise_en_place_efface_le_journal_de_l_ancien_et_renomme(tmp_path):
    servi = tmp_path / "g.db"
    servi.write_bytes(b"ancien")
    for suffixe in ("-wal", "-shm"):
        (tmp_path / f"g.db{suffixe}").write_bytes(b"journal de l'ancien")
    assert sc.mettre_en_place(servi) is False  # rien de prêt : l'ancien et son journal restent
    assert servi.read_bytes() == b"ancien" and (tmp_path / "g.db-wal").exists()
    sc.pret(servi).write_bytes(b"neuf")
    assert sc.mettre_en_place(servi) is True
    assert servi.read_bytes() == b"neuf"
    assert not sc.pret(servi).exists()
    assert not (tmp_path / "g.db-wal").exists() and not (tmp_path / "g.db-shm").exists()


def test_le_gardien_passe_par_la_mise_en_place(tmp_path, monkeypatch):
    vus = []
    monkeypatch.setattr(sc, "mettre_en_place", lambda c: vus.append(c) or True)
    servi = tmp_path / "g.db"
    sc.pret(servi).write_bytes(b"neuf")

    class Processus:
        def poll(self):
            return 0  # déjà arrêté : le gardien n'a rien à terminer

    g = sc.Gardien(servi, Processus)
    g.demarrer()
    assert vus == [servi] and isinstance(g.p, Processus)
    g.p.poll = lambda: None  # le serveur tourne ; un cerveau prêt arrive
    g.arreter = lambda: vus.append("arrêt")
    assert g.tour() is None
    assert vus == [servi, "arrêt", servi]


def test_l_aiguilleur_passe_par_la_meme_mise_en_place(tmp_path, monkeypatch):
    """L'aiguilleur met en place un cerveau prêt avant de lancer son serveur, par la fonction du gardien."""
    servi = tmp_path / "g.db"
    servi.write_bytes(b"ancien")
    (tmp_path / "g.db-wal").write_bytes(b"journal de l'ancien")
    sc.pret(servi).write_bytes(b"neuf")
    vus, lances = [], []
    vraie = sc.mettre_en_place
    monkeypatch.setattr(sc, "mettre_en_place", lambda c: vus.append(c) or vraie(c))

    class Encodeur:
        url, dim = "http://127.0.0.1:1/v1", 384

        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Aiguilleur:
        def __init__(self, ports, lancer, politique, a_relancer=None):
            self.ports, self.lancer, self.fin = ports, lancer, None

        def servir(self):
            for g, port in self.ports.items():
                lances.append(self.lancer(g, port))

        def arreter(self):
            pass

    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "Encodeur", Encodeur)
    monkeypatch.setattr(cc, "attendre_port", lambda port, delai: None)
    monkeypatch.setattr(cfg, "port_cortexdb", lambda g: 40999)
    monkeypatch.setattr(ag, "partages_declares", lambda: {})
    monkeypatch.setattr(ag, "Aiguilleur", Aiguilleur)
    monkeypatch.setattr(ag.signal, "signal", lambda *a: None)
    monkeypatch.setattr(sy, "modele_affine", lambda g: None)
    monkeypatch.setattr(sy, "memoire_bornee", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: "processus")
    monkeypatch.delenv("KG_CORTEXDB_CLES_DOSSIER", raising=False)
    monkeypatch.setenv("KG_CORTEXDB_BINAIRE", "cortexdb-grpc")
    monkeypatch.setattr(sys, "argv", ["aiguilleur_cortexdb", "--graphes", "g"])
    assert ag.main() == 0
    assert lances == ["processus"] and vus == [servi]
    assert servi.read_bytes() == b"neuf" and not (tmp_path / "g.db-wal").exists()
    assert not lc.fichier_adresse("g").exists()  # l'adresse se retire à l'arrêt


# --- les index CJK


def test_les_index_cjk_vides_se_reconnaissent(tmp_path):
    assert cc.index_cjk_vides(cerveau(tmp_path / "pleins.db")) is False
    assert cc.index_cjk_vides(cerveau(tmp_path / "sans.db", cjk=False)) is True
    vides = cerveau(tmp_path / "vides.db")
    cc.retirer_cjk(vides)
    c = sqlite3.connect(vides)  # CortexDB les recrée vides à l'ouverture
    c.execute("CREATE VIRTUAL TABLE chunks_fts_cjk USING fts5(content, tokenize='trigram')")
    c.commit()
    c.close()
    assert cc.index_cjk_vides(vides) is True


def test_un_cerveau_continu_publie_garde_ses_index_cjk(tmp_path):
    for publier in (cc.publier, cc.publier_en_place):
        neuf, cible = cerveau(tmp_path / "g.neuf.db"), tmp_path / "g.db"
        publier(neuf, cible, garder_cjk=True)
        assert objets_cjk(cible) == 2
        assert lire(cible, "SELECT count(*) FROM chunks_fts_cjk")[0][0] == 3
        assert lire(cible, "PRAGMA user_version")[0][0] == 1
        assert not neuf.exists()
        cc.effacer(cible)


def test_un_cerveau_continu_compacte_garde_ses_index_cjk(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "pages_libres", lambda c: 1.0)
    neuf, cible = cerveau(tmp_path / "g.neuf.db"), tmp_path / "g.db"
    assert cc.publier_en_place(neuf, cible, garder_cjk=True) is True
    assert objets_cjk(cible) == 2 and lire(cible, "PRAGMA user_version")[0][0] == 1


def test_un_cerveau_non_continu_les_perd_comme_avant(tmp_path):
    for publier in (cc.publier, cc.publier_en_place):
        neuf, cible = cerveau(tmp_path / "g.neuf.db"), tmp_path / "g.db"
        publier(neuf, cible)
        assert objets_cjk(cible) == 0
        cc.effacer(cible)


def test_la_construction_garde_les_index_du_seul_graphe_continu(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: g == "continu")
    monkeypatch.setattr(cc, "servi", lambda g: False)
    for graphe, attendu in (("continu", 2), ("autre", 0)):
        for publier in (cc._publier_du_graphe, cc._publier_en_place_du_graphe):
            neuf, cible = cerveau(tmp_path / f"{graphe}.neuf.db"), tmp_path / f"{graphe}.db"
            publier(graphe, neuf, cible)
            assert objets_cjk(cible) == attendu, (graphe, publier.__name__)
            cc.effacer(cible)


def test_le_clone_ne_repart_de_zero_que_si_les_index_sont_vides(tmp_path):
    pleins, sans = cerveau(tmp_path / "pleins.db", version=3), cerveau(tmp_path / "sans.db", version=3, cjk=False)
    cc.cloner(pleins, tmp_path / "pleins.neuf.db")
    cc.cloner(sans, tmp_path / "sans.neuf.db")
    assert lire(tmp_path / "pleins.neuf.db", "PRAGMA user_version")[0][0] == 3
    assert lire(tmp_path / "sans.neuf.db", "PRAGMA user_version")[0][0] == 0


def test_un_passage_en_place_publie_par_la_regle_du_graphe(tmp_path, monkeypatch):
    """`construire` publie le clone par la règle du graphe : celui d'un graphe continu garde ses index."""
    faux = type(
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
    cerveau(tmp_path / "g.db")
    (tmp_path / "g.empreintes.json").write_text("{}", encoding="utf-8")
    (tmp_path / "g.json").write_text(json.dumps({"encodeur": "n", "publie": True}), encoding="utf-8")
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / "g.db")
    monkeypatch.setattr(cc, "version_binaire", lambda *a: cc.EN_PLACE_DES)
    monkeypatch.setattr(cc, "engage_couche1", lambda g: False)
    monkeypatch.setattr(cc, "servi", lambda g: False)
    monkeypatch.setattr(cc, "Serveur", faux)
    monkeypatch.setattr(cc, "Encodeur", faux)
    monkeypatch.setattr(cc, "controle", lambda c, octets: {"lignes": 3, "a_encoder": 0, "sans_document": 0})
    monkeypatch.setattr(cc, "publiable", lambda fin: True)

    def versement(args, **_):
        (tmp_path / "g.neuf.empreintes.json").write_text("{}", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"inchanges": 3}) + "\n", stderr="")

    monkeypatch.setattr(cc, "lancer_client", versement)
    monkeypatch.setattr(
        cfg,
        "chemins_du_graphe",
        lambda g: {"base": tmp_path / "base", "graphe": tmp_path / "absent.json", "carte": tmp_path / "absente.md"},
    )
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: True)
    monkeypatch.setattr(cc, "servi", lambda g: True)  # un cerveau continu ne se construit que servi
    (tmp_path / "g.continu.jour.json").write_text(json.dumps({"x": "h"}), encoding="utf-8")
    bilan = cc.construire("g", force="en_place")
    assert bilan["mode"] == "en_place" and bilan["publie"] is True and bilan["compacte"] is False
    assert (bilan["remplaces_du_jour"], bilan["verses_du_jour"]) == (1, 1)  # le relevé neuf n'a pas « x »
    assert objets_cjk(sc.pret(tmp_path / "g.db")) == 2  # servi : publié à côté, en attente de mise en place
    sc.pret(tmp_path / "g.db").unlink()
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: False)
    monkeypatch.setattr(cc, "servi", lambda g: False)
    cc.construire("g", force="en_place")
    assert objets_cjk(tmp_path / "g.db") == 0

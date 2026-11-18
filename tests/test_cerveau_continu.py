"""Le passage continu : la file des fiches entre dans le cerveau que le serveur tient ouvert.

Chaque test lance un passage contre un client factice qui note les appels. Le squelette est réduit à un nœud par fiche
du disque ; le relevé des empreintes et la file sont les vrais fichiers."""

from __future__ import annotations

import json
import os
import sys
import types
from datetime import datetime

import pytest

from newskit import cerveau_continu as ct
from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import extraire_concepts as ec
from newskit import file_fiches
from newskit import lecture_cortexdb as lc
from newskit import puits_cortexdb as pc
from newskit import serveur_cortexdb as sc

A, B, C = "sources/a/2026-11-18_a.md", "sources/a/2026-11-18_b.md", "sources/a/2026-11-18_c.md"
MIDI = lambda: datetime(2026, 11, 18, 12)  # noqa: E731


class Atelier:
    def __init__(self, tmp):
        self.tmp, self.appels, self.lots, self.retard, self.fil = tmp, [], {}, [], set()
        self.faites, self.budget, self.extraites, self.hors_du_graphe = 0, 0, [], set()

    def fiche(self, chemin: str, texte: str = "Le texte.") -> None:
        p = self.tmp / chemin
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{p.stem}"\nurl: "https://e.org/{p.stem}"\n---\n\n{texte}\n', encoding="utf-8")

    def evenement(self, chemin: str, sorte: str = "parue") -> None:
        with open(file_fiches.chemin("g"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"chemin": chemin, "sorte": sorte}) + "\n")

    def squelette(self, graphe: str) -> dict:
        fiches = sorted(p.relative_to(self.tmp).as_posix() for p in (self.tmp / "sources").rglob("*.md"))
        return {
            "nodes": [
                {"id": c.replace("/", "_"), "label": c.rsplit("/", 1)[1][:-3], "kind": "article", "source_file": c}
                for c in fiches
                if c not in self.hors_du_graphe
            ],
            "edges": [],
        }

    def extraire(self, liste) -> dict:
        for f, _ in liste:
            c = f.relative_to(self.tmp).as_posix()
            self.extraites.append(c)
            self.lots[c] = lot(c)
        return {"faites": len(liste), "echecs": 0, "limites": 0}

    def passer(self) -> dict:
        return ct.passer("g", client=object(), horloge=MIDI)

    def noms(self) -> list[str]:
        return [n for n, _ in self.appels]

    def releve(self) -> dict:
        return ct.lire_releve("g")


def lot(chemin: str) -> dict:
    return {
        "nodes": [
            {"id": "fiche", "label": chemin.rsplit("/", 1)[1][:-3], "file_type": "document", "source_file": chemin},
            {"id": "concept", "label": "Concept", "file_type": "concept", "source_file": chemin},
        ],
        "edges": [
            {"source": "fiche", "target": "concept", "relation": "mentions", "confidence": "EXTRACTED",
             "confidence_score": 1.0, "source_file": chemin, "weight": 1.0},
        ],
        "hyperedges": [],
    }  # fmt: skip


@pytest.fixture
def at(tmp_path, monkeypatch):
    a = Atelier(tmp_path)
    dossier = tmp_path / "cerveaux"
    dossier.mkdir()
    monkeypatch.setattr(cc, "DOSSIER", dossier)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: dossier / f"{g}.db")
    monkeypatch.setattr(pc, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: g == "g")
    monkeypatch.setattr(cfg, "graphes_inclus", lambda g: [])
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path / "graphe" / g})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: None)
    monkeypatch.setattr(cfg, "source_du_chemin", lambda s: None)
    monkeypatch.setattr(pc, "corps_versable", lambda src, entete, corps, droits=None, chemin="": corps)
    monkeypatch.setattr(pc, "appeler_outil", lambda client, nom, args: a.appels.append((nom, args)) or {})
    monkeypatch.setattr(ct, "routine_en_cours", lambda: None)
    monkeypatch.setattr(ct, "squelette_du_graphe", a.squelette)
    monkeypatch.setattr(ct, "structure_de", lambda source: None)
    monkeypatch.setattr(ct, "lot_de", lambda source: a.lots.get(source))
    monkeypatch.setattr(ct, "au_fil", lambda g: a.fil)
    monkeypatch.setattr(ec, "viser", lambda g: {"domaine": "d", "couche2_continu_jour": a.budget})
    monkeypatch.setattr(ec, "faites_du_jour", lambda g, jour: a.faites)
    monkeypatch.setattr(ec, "fiches_en_retard", lambda: [(tmp_path / c, "h") for c in a.retard])
    monkeypatch.setattr(ec, "extraire", a.extraire)
    sc.ecrire_adresse(lc.fichier_adresse("g"), "127.0.0.1:4", "lecture", pid=os.getpid(), jeton_ecriture="ecriture")
    for c in (A, B):
        a.fiche(c)
    return a


def ident(chemin: str) -> str:
    return pc.court(chemin)


# --- 1 à 9 : ce qu'un passage écrit


def test_sans_evenement_aucun_appel_au_serveur(at, monkeypatch):
    monkeypatch.setattr(ct, "squelette_du_graphe", lambda g: pytest.fail("le squelette ne se calcule pas pour rien"))
    bilan = at.passer()
    assert at.appels == [] and bilan == {"graphe": "g", "evenements": 0}
    assert file_fiches.marque("g").exists()  # dès le premier passage, les événements s'écrivent


def test_une_fiche_parue_s_ajoute_et_entre_au_releve(at):
    at.evenement(A)
    bilan = at.passer()
    assert at.noms() == ["ingest_document"]
    assert at.appels[0][1]["document_id"] == ident(A)
    assert set(at.releve()) == {ident(A)} and at.releve()[ident(A)] != ct.EN_COURS
    assert (bilan["evenements"], bilan["ajoutes"], bilan["remplaces"]) == (1, 1, 0)
    assert file_fiches.lire("g") == []  # l'événement est acquitté


def test_une_fiche_modifiee_s_efface_puis_se_verse(at):
    at.evenement(A)
    at.passer()
    avant = at.releve()[ident(A)]
    del at.appels[:]
    at.fiche(A, "Un autre texte.")
    at.evenement(A, "modifiee")
    bilan = at.passer()
    assert at.noms() == ["delete_document_graph", "ingest_document"]
    assert at.releve()[ident(A)] not in (avant, ct.EN_COURS)
    assert (bilan["remplaces"], bilan["ajoutes"]) == (1, 0)


def test_une_fiche_inchangee_ne_s_ecrit_pas(at, monkeypatch):
    at.evenement(A)
    at.passer()
    del at.appels[:]
    notes = []
    monkeypatch.setattr(ct, "noter", lambda *a: notes.append(a))
    at.evenement(A, "modifiee")
    bilan = at.passer()
    assert at.appels == [] and notes == [] and bilan["inchanges"] == 1
    assert file_fiches.lire("g") == []


def test_une_fiche_retiree_quitte_le_cerveau_et_le_releve(at):
    at.evenement(A)
    at.evenement(B)
    at.passer()
    del at.appels[:]
    (at.tmp / A).unlink()
    at.evenement(A, "retiree")
    bilan = at.passer()
    assert at.appels == [
        ("delete_document_graph", {"document_id": ident(A)}),
        ("knowledge_delete", {"knowledge_id": ident(A)}),
    ]
    assert set(at.releve()) == {ident(B)} and bilan["retires"] == 1


def test_le_passage_tient_la_liste_des_documents_du_jour(at):
    at.evenement(A)
    at.evenement(B)
    at.passer()
    assert json.loads(ct.du_jour("g").read_text(encoding="utf-8")) == at.releve()
    (at.tmp / A).unlink()
    at.evenement(A, "retiree")
    at.passer()
    assert set(json.loads(ct.du_jour("g").read_text(encoding="utf-8"))) == {ident(B)}


def test_une_fiche_retiree_jamais_versee_n_appelle_rien(at):
    at.evenement(C, "retiree")
    assert at.passer()["retires"] == 0 and at.appels == []


def test_une_fiche_hors_du_graphe_ne_se_verse_pas(at):
    at.hors_du_graphe.add(A)  # le partage ne la retient pas : le squelette ne la connaît pas
    at.evenement(A)
    at.passer()
    assert at.appels == [] and at.releve() == {}
    assert ct.a_traiter([{"chemin": A, "sorte": "parue"}], set()) == {A: "retirer"}
    assert ct.a_traiter([{"chemin": A, "sorte": "parue"}], {A}) == {A: "verser"}


def test_une_fiche_absente_du_disque_se_retire(at):
    assert ct.a_traiter([{"chemin": C, "sorte": "parue"}], {C}) == {C: "retirer"}
    assert ct.a_traiter([{}, {"chemin": ""}], set()) == {}  # une ligne illisible ne compte pas


def test_plusieurs_evenements_d_une_fiche_font_un_seul_versement(at):
    for sorte in ("parue", "modifiee", "droits"):
        at.evenement(A, sorte)
    bilan = at.passer()
    assert at.noms() == ["ingest_document"] and bilan["evenements"] == 3
    assert ct.a_traiter(
        [{"chemin": A, "sorte": "retiree"}, {"chemin": B, "sorte": "parue"}, {"chemin": A, "sorte": "parue"}], {A, B}
    ) == {B: "verser", A: "verser"}  # le dernier événement décide


def test_un_versement_en_echec_reste_dans_la_file_et_n_est_pas_verse(at, monkeypatch):
    at.evenement(A)
    at.evenement(B)

    def panne(client, nom, args):
        at.appels.append((nom, args))
        if args.get("document_id") == ident(B):
            raise RuntimeError("le serveur s'arrête")
        return {}

    monkeypatch.setattr(pc, "appeler_outil", panne)
    bilan = at.passer()
    assert bilan["erreur"] == "RuntimeError : le serveur s'arrête"
    assert len(file_fiches.lire("g")) == 2  # rien n'est acquitté
    assert at.releve()[ident(B)] == ct.EN_COURS and at.releve()[ident(A)] != ct.EN_COURS
    assert ct.est_verse("g", A) and not ct.est_verse("g", B)
    monkeypatch.setattr(ct, "passer", lambda g: bilan)
    assert ct.main(["--graphe", "g"]) == 1  # la commande dit l'erreur à qui la lance


def test_un_versement_interrompu_se_rejoue_par_un_remplacement(at):
    ct.noter(ct.releve("g"), ident(A), ct.EN_COURS)  # l'écriture avait commencé ; l'événement est resté dans la file
    at.evenement(A)
    bilan = at.passer()
    assert at.noms() == ["delete_document_graph", "ingest_document"] and bilan["remplaces"] == 1
    assert ct.est_verse("g", A)


def test_une_ecriture_interrompue_sans_evenement_se_rejoue_aussi(at):
    ct.noter(ct.releve("g"), ident(A), ct.EN_COURS)  # une fiche du retard, extraite puis interrompue
    ct.noter(ct.releve("g"), ident(C), ct.EN_COURS)  # sa fiche a disparu depuis
    at.passer()
    assert at.noms() == ["delete_document_graph", "ingest_document", "delete_document_graph", "knowledge_delete"]
    assert set(at.releve()) == {ident(A)} and ct.est_verse("g", A)


def test_un_lot_invalide_se_compte_et_ne_bloque_pas_la_file(at, capsys):
    at.lots[A] = {"nodes": [*lot(A)["nodes"], {"id": "x", "label": "X", "source_file": B}], "edges": []}
    at.evenement(A)
    at.evenement(B)
    bilan = at.passer()
    assert bilan["invalides"] == 1 and "erreur" not in bilan
    assert at.noms() == ["ingest_document"] and set(at.releve()) == {ident(B)}
    assert file_fiches.lire("g") == [] and f"!! {A}" in capsys.readouterr().err


# --- 10 à 14 : quand le passage attend


def attend(at, raison: str) -> None:
    at.evenement(A)
    bilan = at.passer()
    assert bilan == {"graphe": "g", "attente": raison}
    assert at.appels == [] and len(file_fiches.lire("g")) == 1  # l'événement attend dans la file


def test_le_passage_attend_pendant_la_routine_du_graphe(at, monkeypatch):
    monkeypatch.setattr(ct, "routine_en_cours", lambda: "autre")
    assert ct.peut_passer("g") == ""  # la routine d'un autre graphe n'arrête rien
    monkeypatch.setattr(ct, "routine_en_cours", lambda: "g")
    attend(at, "la routine du graphe est en cours")


def test_le_passage_attend_pendant_une_construction(at):
    with cc.verrou_du_cerveau("g") as pris:
        assert pris is True
        attend(at, ct.CONSTRUCTION)
    assert "attente" not in at.passer()  # le verrou rendu, le passage écrit


def test_le_passage_attend_tant_qu_un_cerveau_pret_n_est_pas_en_place(at):
    sc.pret(cc.chemin_cerveau("g")).write_bytes(b"neuf")
    attend(at, "un cerveau prêt attend sa mise en place")


def test_sans_serveur_vivant_ou_sans_cle_d_ecriture_rien(at):
    sc.ecrire_adresse(lc.fichier_adresse("g"), "127.0.0.1:4", "lecture", pid=os.getpid())
    attend(at, "le serveur ne publie pas de clé d'écriture")
    lc.fichier_adresse("g").unlink()
    assert at.passer()["attente"] == "aucun serveur ne tient le cerveau ouvert"


def test_la_pause_des_routines_arrete_le_passage(at):
    (at.tmp / "cache").mkdir()
    (at.tmp / "cache" / "routines.pause").touch()
    attend(at, "les routines sont en pause")


@pytest.mark.parametrize(
    "texte, pause",
    [
        ("Pause demandée.\n", True),
        ("Pause demandée.\nsauf: autre, g\n", False),
        ("Pause demandée.\nsauf : inclus\n", False),  # un graphe que « g » inclut
        ("Pause demandée.\nsauf: autre\n", True),
    ],
)
def test_la_pause_epargne_les_graphes_qu_elle_nomme(at, monkeypatch, texte, pause):
    monkeypatch.setattr(cfg, "graphes_inclus", lambda g: ["inclus"])
    (at.tmp / "cache").mkdir()
    (at.tmp / "cache" / "routines.pause").write_text(texte, encoding="utf-8")
    assert ct.en_pause("g") is pause
    assert ct.peut_passer("g") == ("les routines sont en pause" if pause else "")


def test_un_retrait_rejoue_ne_bloque_pas_la_file(at, monkeypatch):
    """Le passage s'était arrêté après l'effacement, avant de l'écrire au relevé : le serveur ne connaît plus le
    document. Toute autre erreur arrête encore le passage."""
    at.evenement(A)
    at.passer()
    (at.tmp / A).unlink()
    erreur = ["status = StatusCode.NOT_FOUND"]

    def absent(client, nom, args):
        if nom == "knowledge_delete":
            raise RuntimeError(erreur[0])
        return {}

    monkeypatch.setattr(pc, "appeler_outil", absent)
    at.evenement(A, "retiree")
    erreur[0] = "status = StatusCode.UNAVAILABLE"
    assert "erreur" in at.passer() and set(at.releve()) == {ident(A)}
    erreur[0] = "status = StatusCode.NOT_FOUND"
    bilan = at.passer()
    assert "erreur" not in bilan and at.releve() == {} and file_fiches.lire("g") == []


def test_un_graphe_sans_cerveau_continu_ne_passe_pas(at):
    assert ct.passer("autre", client=object()) == {"graphe": "autre", "attente": "le graphe n'a pas de cerveau continu"}
    assert not file_fiches.marque("autre").exists()


# --- 15 à 17 : l'ordre et le budget de la couche 2


def test_le_fil_passe_avant_les_autres_puis_le_retard(at):
    assert ct.ordonner(["a", "b", "c"], {"c", "r2"}, ["r1", "r2", "b"]) == ["c", "r2", "a", "b", "r1"]
    at.fiche(C)
    at.retard, at.fil, at.budget = [C, A, B], {B}, 200  # à midi, la moitié du budget : large
    at.evenement(A)
    at.passer()
    assert at.extraites == [B, A, C]  # le fil, l'événement, le retard
    assert [x["document_id"] for n, x in at.appels if n == "ingest_document"] == [ident(B), ident(A), ident(C)]


def test_le_fil_prend_le_reste_du_budget_les_autres_la_part_ecoulee(at):
    at.fiche(C)
    at.retard, at.fil, at.budget, at.faites = [A, B, C], {C}, 200, 100  # à midi : 100 déjà faites, la part est prise
    at.passer()
    assert at.extraites == [C]


def test_budget_epuise_la_fiche_dense_se_verse_sans_lot(at, monkeypatch):
    at.retard, at.budget, at.faites = [A], 10, 10
    monkeypatch.setattr(ec, "fiches_en_retard", lambda: pytest.fail("budget épuisé : le retard ne se relit pas"))
    at.evenement(A)
    bilan = at.passer()
    assert at.extraites == [] and "extraites" not in bilan
    assert at.noms() == ["ingest_document"] and ct.est_verse("g", A)


def test_le_lot_extrait_dans_le_passage_se_verse_dans_le_passage(at):
    at.retard, at.budget = [A], 200
    at.evenement(A)
    bilan = at.passer()
    assert at.noms() == ["ingest_document", "upsert_entities", "upsert_relations"]
    assert (bilan["extraites"], bilan["echecs_d_extraction"]) == (1, 0)


def test_une_fiche_du_retard_hors_du_graphe_ne_se_verse_pas(at):
    at.retard, at.budget = [A], 200
    at.hors_du_graphe.add(A)
    at.passer()
    assert at.extraites == [A] and at.appels == []


def test_sans_evenement_ni_part_du_jour_rien_ne_se_calcule(at, monkeypatch):
    at.retard, at.budget, at.faites = [A], 200, 150  # en avance sur la journée
    monkeypatch.setattr(ct, "squelette_du_graphe", lambda g: pytest.fail("rien à verser"))
    assert at.passer() == {"graphe": "g", "evenements": 0} and at.extraites == []


def test_un_passage_traite_au_plus_un_lot_d_evenements(at, monkeypatch):
    monkeypatch.setattr(ct, "LOT_MAX", 2)
    for _ in range(3):
        at.evenement(A)
    assert at.passer()["evenements"] == 2
    assert len(file_fiches.lire("g")) == 1


# --- 18, 19 : la construction et le relevé


def test_la_construction_attend_la_fin_du_passage_continu(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DOSSIER", tmp_path / "cerveaux")
    monkeypatch.setattr(cc, "attendre_encodeur", lambda g: 0)
    vus = []

    def corps(graphe, reprendre_de, couche1, force):
        with cc.verrou_du_cerveau(graphe, attendre=False) as pris:
            vus.append(pris)  # un passage continu qui viendrait maintenant attendrait
        return {"graphe": graphe}

    monkeypatch.setattr(cc, "_construire", corps)
    assert cc.construire("g") == {"graphe": "g"} and vus == [False]
    with cc.verrou_du_cerveau("g", attendre=False) as pris:
        assert pris is True  # la construction finie, le verrou est rendu


def test_est_verse_lit_le_releve_et_refuse_la_marque_en_cours(at):
    assert ct.est_verse("g", A) is False  # pas de relevé
    ct.noter(ct.releve("g"), ident(A), ct.EN_COURS)
    assert ct.est_verse("g", A) is False
    ct.noter(ct.releve("g"), ident(A), "abc")
    assert ct.est_verse("g", A) is True and ct.est_verse("g", B) is False
    ct.noter(ct.releve("g"), ident(A), None)
    assert ct.lire_releve("g") == {} and not ct.releve("g").with_name("g.empreintes.json.tmp").exists()


# --- les pièces lues ailleurs


def test_les_fiches_du_fil_sont_celles_des_billets_et_de_la_pile(monkeypatch):
    from newskit import fil

    monkeypatch.setattr(
        fil, "lire", lambda g: {"billets": [{"chemin": "sources/a/x"}, {"chemin": B}, {}], "pile": [{"chemin": A}]}
    )
    assert ct.au_fil("g") == {"sources/a/x.md", A, B}
    monkeypatch.setattr(fil, "lire", lambda g: {})
    assert ct.au_fil("g") == set()


def test_le_squelette_la_structure_le_lot_et_la_routine_se_lisent_aux_modules(tmp_path, monkeypatch):
    from newskit import extraire_squelette as ex
    from newskit import routine_hebdo as rh

    monkeypatch.setattr(ex, "construire", lambda g: ({"nodes": [g]}, {}, []))
    assert ct.squelette_du_graphe("g") == {"nodes": ["g"]}
    monkeypatch.setattr(rh, "graphe_en_routine", lambda: "g")
    assert ct.routine_en_cours() == "g"
    monkeypatch.setattr(pc, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "LOTS", tmp_path / "lots")
    p = tmp_path / A
    p.parent.mkdir(parents=True)
    p.write_text('---\ntitle: "A"\n---\n\n# A\n\n## Une partie\n\nLe texte.\n', encoding="utf-8")
    assert "Une partie" in {n["label"] for n in ct.structure_de(A)["nodes"]}
    assert ct.lot_de(A) is None
    (tmp_path / "lots").mkdir()
    (tmp_path / "lots" / f"{ec.identifiant_de_fiche(p)}.json").write_text('{"nodes": []}', encoding="utf-8")
    assert ct.lot_de(A) == {"nodes": []}


def test_les_alias_du_graphe_se_lisent_a_sa_base(at):
    assert ct._alias("g") == {}
    base = at.tmp / "graphe" / "g"
    base.mkdir(parents=True)
    (base / "alias.json").write_text("{}", encoding="utf-8")
    assert ct._alias("g") == {}


def test_sans_client_le_passage_se_connecte_avec_la_cle_d_ecriture(at, monkeypatch, capsys):
    connexions = []

    class Client:
        @classmethod
        def connect(cls, adresse, token):
            connexions.append((adresse, token))
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "cortexdb_client", types.SimpleNamespace(CortexClient=Client))
    at.evenement(A)
    assert ct.main(["--graphe", "g"]) == 0
    assert connexions == [("127.0.0.1:4", "ecriture")] and at.noms() == ["ingest_document"]
    ligne = capsys.readouterr().out.strip()
    assert json.loads(ligne[20:])["ajoutes"] == 1  # une ligne de journal par passage, datée


def test_le_passage_se_lance_en_ligne_de_commande(at, monkeypatch, capsys):
    import runpy

    monkeypatch.setattr(sys, "argv", ["cerveau_continu", "--graphe", "autre"])
    monkeypatch.delitem(sys.modules, "newskit.cerveau_continu")
    with pytest.raises(SystemExit) as fin:
        runpy.run_module("newskit.cerveau_continu", run_name="__main__")
    assert fin.value.code == 0 and "le graphe n'a pas de cerveau continu" in capsys.readouterr().out

"""La nuit devient le contrôle quotidien d'un cerveau continu.

Le passage continu écrit le cerveau au fil de la journée ; la routine de la nuit compare encore tous les fichiers au
cerveau. Un passage court, lancé en journée par un lien déposé, ne reconstruit plus le cerveau d'un graphe continu. Un
cerveau continu que personne ne sert ne se reconstruit pas. Le bilan compte les documents versés le jour que le
contrôle a dû remplacer."""

from __future__ import annotations

import json
import sys

import pytest

from newskit import cerveau_continu as ct
from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import routine_hebdo as rh


@pytest.fixture
def routine(tmp_path, monkeypatch):
    lancees = []
    monkeypatch.setattr(sys, "argv", ["routine_hebdo"])
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: lancees.append(args) or (True, ""))
    monkeypatch.setattr(cfg, "verse_cortexdb", lambda g: True)
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: g == "continu")
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    return lancees


def etape(g: str) -> list[str]:
    return ["scripts/cerveau_cortexdb.py", "--graphe", g]


def test_un_passage_court_ne_reconstruit_pas_un_cerveau_continu(routine):
    j = rh.Journal()
    rh.cerveau_cortexdb(j, "continu", " (continu)", court=True)
    assert routine == []
    assert any("passage court" in l for l in j.lignes)


def test_le_passage_du_jour_le_controle_toujours(routine, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["routine_hebdo", "--graphe", "continu"])
    rh.cerveau_cortexdb(rh.Journal(), "continu", "")
    rh.cerveau_cortexdb(rh.Journal(), "continu", "", court=False)
    assert routine == [etape("continu"), etape("continu")]


def test_un_graphe_non_continu_garde_son_etape_au_passage_court(routine):
    rh.cerveau_cortexdb(rh.Journal(), "autre", "", court=True)
    assert routine == [etape("autre")]


@pytest.mark.parametrize(
    "argv, court",
    [
        (["--graphe", "continu", "--sans-collecte", "--plafond", "3"], True),
        (["--sans-collecte", "--plafond", "0"], False),
        (["--sans-collecte"], False),
        (["--plafond", "3"], False),
        ([], False),
    ],
)
def test_un_passage_court_est_un_passage_sans_collecte_sous_plafond(routine, monkeypatch, argv, court):
    assert rh.passage_court(argv) is court
    monkeypatch.setattr(sys, "argv", ["routine_hebdo", *argv])  # la routine le lit à ses propres arguments
    assert rh.passage_court() is court
    rh.cerveau_cortexdb(rh.Journal(), "continu", "")
    assert routine == ([] if court else [etape("continu")])


def test_un_graphe_sans_cerveau_n_a_pas_d_etape(routine, monkeypatch):
    monkeypatch.setattr(cfg, "verse_cortexdb", lambda g: False)
    rh.cerveau_cortexdb(rh.Journal(), "continu", "")
    assert routine == []


def test_le_rapport_compte_les_remplacements_du_jour(routine, tmp_path):
    (tmp_path / "continu.json").write_text(json.dumps({"remplaces_du_jour": 3, "verses_du_jour": 40}))
    j = rh.Journal()
    rh.cerveau_cortexdb(j, "continu", " (continu)")
    assert "  cerveau continu (continu) : 3 document(s) sur 40 versés le jour, remplacés par le contrôle" in j.lignes
    (tmp_path / "autre.json").write_text(json.dumps({"remplaces_du_jour": 3, "verses_du_jour": 40}))
    j = rh.Journal()
    rh.cerveau_cortexdb(j, "autre", "")
    assert j.lignes == []  # un graphe non continu n'a pas de versement du jour


def test_le_rapport_se_tait_sans_bilan_ou_sans_compte(routine, tmp_path, monkeypatch):
    j = rh.Journal()
    (tmp_path / "continu.json").write_text(json.dumps({"remplaces_du_jour": 3, "verses_du_jour": 40}))
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: (False, ""))
    rh.cerveau_cortexdb(j, "continu", "")  # la construction a échoué : ce bilan est celui d'une autre nuit
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: (True, ""))
    (tmp_path / "continu.json").unlink()
    rh.cerveau_cortexdb(j, "continu", "")
    (tmp_path / "continu.json").write_text("{}")
    rh.cerveau_cortexdb(j, "continu", "")
    (tmp_path / "continu.json").write_text("illisible")
    rh.cerveau_cortexdb(j, "continu", "")
    assert j.lignes == []


# --- le compte des versements du jour


def test_le_passage_continu_tient_la_liste_du_jour(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    assert ct.du_jour("g") == tmp_path / "g.continu.jour.json"
    ct.noter_du_jour("g", "a", "h1")
    ct.noter_du_jour("g", "b", "h2")
    ct.noter_du_jour("g", "a", "h3")
    ct.noter_du_jour("g", "b", None)  # retirée dans la journée
    assert json.loads(ct.du_jour("g").read_text(encoding="utf-8")) == {"a": "h3"}


def test_le_controle_compte_ce_qu_il_remplace_et_vide_la_liste(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    assert cc.remplaces_du_jour("g", {"a": "h"}) == (0, 0)  # aucun versement du jour
    for ident, h in (("a", "h1"), ("b", "h2"), ("c", "h3")):
        ct.noter_du_jour("g", ident, h)
    # a : même empreinte la nuit ; b : remplacé ; c : retiré par le contrôle
    assert cc.remplaces_du_jour("g", {"a": "h1", "b": "autre", "z": "h9"}) == (2, 3)
    assert not ct.du_jour("g").exists()


# --- un cerveau continu que personne ne sert


def construction(tmp_path, monkeypatch, continu: bool, servi: bool, existe: bool = True):
    monkeypatch.setattr(cc, "DOSSIER", tmp_path)
    monkeypatch.setattr(cc, "attendre_encodeur", lambda g: 0)
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / f"{g}.db")
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: continu)
    monkeypatch.setattr(cc, "servi", lambda g: servi)
    monkeypatch.setattr(cc, "_construire", lambda *a: {"graphe": a[0]})
    if existe:
        (tmp_path / "g.db").write_bytes(b"cerveau")
    return cc.construire("g")


def test_un_cerveau_continu_non_servi_arrete_la_construction(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="cerveau continu de g : aucun serveur ne le tient ouvert"):
        construction(tmp_path, monkeypatch, continu=True, servi=False)


@pytest.mark.parametrize("continu, servi, existe", [(True, True, True), (False, False, True), (True, False, False)])
def test_les_autres_constructions_continuent(tmp_path, monkeypatch, continu, servi, existe):
    """Servi, il se construit ; un graphe non continu aussi ; un premier cerveau n'a pas encore de serveur."""
    assert construction(tmp_path, monkeypatch, continu, servi, existe) == {"graphe": "g"}

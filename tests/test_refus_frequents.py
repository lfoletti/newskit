"""Les refus fréquents de la relecture, portés en consigne de la première rédaction (mainteneur, 17.11.2026)."""

from __future__ import annotations

import json
from datetime import datetime

from newskit import fil
from newskit import refus_frequents as rf

MAINTENANT = datetime(2026, 11, 17, 22, 0)


def test_la_cle_garde_la_tete_du_reproche_et_reduit_les_nombres():
    assert rf.cle("absent de la source : Genians est sud-coréenne") == rf.cle("absent de la source : le Brésil")
    assert rf.cle("phrase de 31 mots") == rf.cle("phrase de 28  mots")
    assert rf.cle("« agents » seul : on ne sait pas") != rf.cle("« présomption » non expliquée : de quoi ?")


def test_un_reproche_sur_un_terme_cite_a_ce_terme_pour_motif():
    # 17.11.2026 : 22 reproches sur « agents », écrits de 8 façons par le relecteur.
    agents = [
        "« agents » seul : on ne sait pas qu'il s'agit d'agents d'IA.",
        "« Agents » sans « IA » est ambigu.",
        "« agents » ne dit pas qu'il s'agit de programmes d'IA ; « rappel » reste obscur.",
    ]
    assert len({rf.cle(r) for r in agents}) == 1
    assert rf.cle(agents[0]) != rf.cle("« jetons » seul : unité jamais expliquée")


def test_les_premiers_mots_font_la_forme_d_un_reproche_libre():
    assert rf.cle("le titre ne dit pas qu'il s'agit d'une loi.") == rf.cle(
        "le titre ne dit pas qu'il s'agit d'une banque."
    )


def test_les_plus_frequents_dans_la_fenetre_avec_leur_dernier_exemple():
    entrees = [
        ("2026-11-15 10:00", "phrase de 31 mots"),  # le moins fréquent vient d'abord : le tri le remet après
        ("2026-11-16 10:00", "absent de la source : A"),
        ("2026-11-17 09:00", "absent de la source : B"),
        ("2026-11-17 08:00", "absent de la source : C"),
        ("2026-11-16 10:00", "phrase de 29 mots"),
        ("2026-11-16 10:00", "sigle non développé : MCP"),  # vu une fois : sous le minimum
        ("2026-10-01 10:00", "mot anglais : la tech"),  # hors de la fenêtre
        ("2026-10-02 10:00", "mot anglais : la tech"),
    ]
    assert rf.frequents(entrees, MAINTENANT) == [(3, "absent de la source : B"), (2, "phrase de 29 mots")]


def test_le_nombre_de_motifs_est_borne(monkeypatch):
    monkeypatch.setattr(rf, "MAX", 2)
    entrees = [("2026-11-17 10:00", f"motif {c} : x") for c in "abc" for _ in range(2)]
    assert len(rf.frequents(entrees, MAINTENANT)) == 2


def test_une_date_illisible_ne_compte_pas():
    assert rf.frequents([("", "a : x"), ("", "a : x"), ("demain", "a : x"), ("demain", "a : y")], MAINTENANT) == []


def _banc(tmp_path, monkeypatch, journal, billets):
    j = tmp_path / "fil_refus.jsonl"
    j.write_text(journal, encoding="utf-8")
    r = tmp_path / "fil.json"
    r.write_text(json.dumps({"billets": billets, "pile": []}), encoding="utf-8")
    monkeypatch.setattr(rf, "journal", lambda: j)
    monkeypatch.setattr(fil, "chemin_fil", lambda g: r)


def test_la_consigne_reunit_le_journal_des_billets_et_les_fiches_refusees(tmp_path, monkeypatch):
    lignes = [
        {"quand": "2026-11-17 10:00", "graphe": "g", "essais": [{"reproches": ["« agents » seul : a"]}]},
        {"quand": "2026-11-17 11:00", "graphe": "autre", "essais": [{"reproches": ["« agents » seul : b"]}]},
    ]
    journal = "\n".join(json.dumps(x, ensure_ascii=False) for x in lignes) + "\npas du json\n\n"
    billets = [
        {"ajoute": "2026-11-17 12:00", "fiche": {"tenu": False, "reproches": ["« agents » seul : c"]}},
        {"ajoute": "2026-11-17 12:00", "fiche": {"tenu": True, "resume": "r"}},
        {"ajoute": "2026-11-17 12:00"},
    ]
    _banc(tmp_path, monkeypatch, journal, billets)
    texte = rf.consigne("g", MAINTENANT)
    assert texte.endswith("\n") and "14 derniers jours" in texte
    assert "- 2 fois : « agents » seul : c\n" in texte
    assert "b" not in texte.split("\n", 1)[1]  # l'autre graphe ne compte pas


def test_sans_refus_frequent_la_consigne_est_vide(tmp_path, monkeypatch):
    _banc(tmp_path, monkeypatch, "", [])
    assert rf.consigne("g", MAINTENANT) == ""


def test_un_journal_absent_ou_un_registre_illisible_ne_bloque_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "journal", lambda: tmp_path / "absent.jsonl")
    (tmp_path / "fil.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(fil, "chemin_fil", lambda g: tmp_path / "fil.json")
    assert rf.consigne("g", MAINTENANT) == ""


def test_le_journal_est_celui_du_fil():
    assert rf.journal() == fil.JOURNAL_REFUS


def test_la_redaction_des_billets_porte_la_consigne(monkeypatch):
    vu = {}
    monkeypatch.setattr(rf, "consigne", lambda g, maintenant=None: "REFUS FREQUENTS\n")
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"libelle": "IA"})
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil, "extrait", lambda f, n=None: "source")

    def demander(inv, schema, graphe, note, **k):
        vu["invite"] = inv
        return {"billets": []}, 0.0

    monkeypatch.setattr(fil, "demander", demander)
    fil.rediger([{"titre": "T"}], [], "g")
    assert "REFUS FREQUENTS\nRends l'objet" in vu["invite"]


def test_sans_journal_les_fiches_refusees_comptent(tmp_path, monkeypatch):
    billets = [{"ajoute": "2026-11-17 12:00", "fiche": {"tenu": False, "reproches": ["sigle non développé : MCP"]}}] * 2
    r = tmp_path / "fil.json"
    r.write_text(json.dumps({"billets": billets}), encoding="utf-8")
    monkeypatch.setattr(rf, "journal", lambda: tmp_path / "absent.jsonl")
    monkeypatch.setattr(fil, "chemin_fil", lambda g: r)
    assert "- 2 fois : sigle non développé : MCP\n" in rf.consigne("g", MAINTENANT)

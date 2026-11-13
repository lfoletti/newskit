"""La version anglaise d'une suite garde son angle (mainteneur, 13.11.2026 : « un fait nouveau peut tout à fait justifier
une nouvelle fiche, à condition d'en faire l'angle de la fiche, et de la titrer adéquatement »).

Le 13.11.2026, le billet Fortune sur Nvidia, titré en français sur son fait neuf (« Colette Kress et Harvey Jones
dirigent Nvidia après la démission de Jensen Huang »), a reçu une version anglaise écrite d'après la source seule :
« Nvidia CEO Jensen Huang resigns, and the AI industry is unsure what comes next ». Le rédacteur anglais ne connaissait
pas le fait neuf, et rien ne le contrôlait.
"""

from __future__ import annotations

import json

import pytest

from newskit import fil_anglais as fa

DEV = "Colette Kress et Harvey Jones dirigent Nvidia."
ANGLE = ("Harvey Jones and Colette Kress run Nvidia", "Harvey Jones chairs the Nvidia board. Huang left on Thursday.")
SANS_ANGLE = ("Nvidia CEO Jensen Huang resigns", "Jensen Huang resigned. Harvey Jones chairs the board.")


def suite(**k):
    return {
        "titre": "T",
        "descriptif": "D",
        "chemin": "c.md",
        "url": "u",
        "date": "2026-11-12",
        "suite_de": "s/a.md",
        "developpement": DEV,
        "neuf": ["harvey", "jones"],
        **k,
    }


@pytest.fixture
def banc(monkeypatch):
    etat = {"ecrits": [], "invites": []}
    monkeypatch.setattr(fa.lt, "regles", lambda g=None, langue="fr": "")
    monkeypatch.setattr(fa.fil, "extrait", lambda b, n=1200: "Harvey Jones chairs Nvidia. Jensen Huang resigned.")
    monkeypatch.setattr(fa.fil, "doutes", lambda t, x, f: [])
    monkeypatch.setattr(fa.cfg, "graphe_declare", lambda g: {"libelle": "AI governance"})
    monkeypatch.setattr(fa, "forme", lambda t, x: [])

    def demander(inv, schema, graphe, note, modele=None):
        etat["invites"].append((note, inv))
        lignes = [json.loads(x) for x in inv.split("\n") if x.startswith('{"n"')]
        if note == "relecteur anglais":
            return {"billets": [{"n": x["n"], "absents": [], "clair": True} for x in lignes]}, 0.01
        t, x_ = etat["ecrits"].pop(0)
        return {"billets": [{"n": x["n"], "titre": t, "texte": x_} for x in lignes]}, 0.02

    monkeypatch.setattr(fa.fil, "demander", demander)
    return etat


def _items(etat, note):
    return [json.loads(x) for n, inv in etat["invites"] if n == note for x in inv.split("\n") if x.startswith('{"n"')]


def test_la_version_anglaise_d_une_suite_qui_garde_l_angle_est_tenue(banc):
    banc["ecrits"] = [ANGLE]
    (en,) = fa.ecrire([(suite(), {"titre": "S"})], "g")[0].values()
    assert en == {"titre": ANGLE[0], "texte": ANGLE[1], "tenu": True}


def test_la_version_anglaise_qui_perd_l_angle_est_reecrite_sur_le_fait_neuf(banc):
    banc["ecrits"] = [SANS_ANGLE, ANGLE]
    (en,) = fa.ecrire([(suite(), {"titre": "S"})], "g")[0].values()
    assert en["titre"] == ANGLE[0] and en["tenu"] is True
    (x,) = _items(banc, "réécriture anglaise")
    assert x["developpement"] == DEV and any("new fact" in r for r in x["reproches"])


def test_le_titre_seul_qui_perd_l_angle_fait_reecrire(banc):
    banc["ecrits"] = [(SANS_ANGLE[0], ANGLE[1]), ANGLE]
    (en,) = fa.ecrire([(suite(), {"titre": "S"})], "g")[0].values()
    assert en["titre"] == ANGLE[0]


def test_la_premiere_phrase_qui_perd_l_angle_fait_reecrire(banc):
    banc["ecrits"] = [(ANGLE[0], SANS_ANGLE[1]), ANGLE]
    (en,) = fa.ecrire([(suite(), {"titre": "S"})], "g")[0].values()
    assert en["texte"] == ANGLE[1]


def test_une_version_anglaise_jamais_sur_l_angle_n_est_pas_tenue(banc):
    banc["ecrits"] = [SANS_ANGLE] * (fa.REECRITURES + 1)
    (en,) = fa.ecrire([(suite(), {"titre": "S"})], "g")[0].values()
    assert en["tenu"] is False and any("new fact" in r for r in en["reproches"])


def test_un_billet_qui_n_est_pas_une_suite_n_a_pas_d_angle_a_tenir(banc):
    banc["ecrits"] = [SANS_ANGLE]
    (en,) = fa.ecrire([(suite(suite_de=""), {"titre": "S"})], "g")[0].values()
    assert en["titre"] == SANS_ANGLE[0] and en["tenu"] is True
    assert "developpement" not in _items(banc, "anglais")[0]


def test_la_redaction_anglaise_recoit_le_fait_neuf_d_une_suite(banc):
    banc["ecrits"] = [ANGLE]
    fa.ecrire([(suite(), {"titre": "S"})], "g")
    assert _items(banc, "anglais")[0]["developpement"] == DEV


def test_les_consignes_anglaises_demandent_l_angle_d_une_suite():
    for consigne in (fa.REDACTION, fa.REECRITURE):
        assert 'an item that carries "developpement" follows a news item already published' in consigne
        assert "this new fact is its angle" in consigne

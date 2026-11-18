"""Aucun caractère chinois, japonais ou coréen dans une traduction française (governance.news, 14.11.2026 : « fais un
contrôle général des titres - je vois encore souvent des caractères asiatiques ») : « AI情報通：10月29日15時の情報 »
devenait « AI情報通 : informations du 29 octobre, 15 h », le nom gardé comme nom propre."""

from __future__ import annotations

from newskit import couts  # noqa: E402
from newskit import francais as fr  # noqa: E402


def _lot(monkeypatch, reponse):
    vus = []
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr(fr.te, "appeler_claude", lambda prompt, *a, **k: vus.append(prompt) or reponse)
    lot = [("d1", "l", "AI情報通：10月29日15時の情報"), ("d2", "l", "OpenAI")]
    return fr._lot("g", "l", lot), vus


def test_la_consigne_demande_de_translitterer():
    assert "se translittère" in fr._consigne("l") and "« AI情報通 » → « AI Jōhōtsū »" in fr._consigne("l")


def test_un_nom_propre_en_ecriture_asiatique_ne_reste_pas_tel_quel(monkeypatch):
    out, _ = _lot(
        monkeypatch,
        {
            "textes": [
                {"i": 0, "fr": "AI Jōhōtsū : informations du 29 octobre", "nom_propre": True},
                {"i": 1, "fr": "OpenAI", "nom_propre": True},
            ]
        },
    )
    assert [o[2] for o in out] == ["AI Jōhōtsū : informations du 29 octobre", "OpenAI"]


def test_une_traduction_qui_garde_des_caracteres_asiatiques_est_refusee(monkeypatch):
    out, _ = _lot(
        monkeypatch, {"textes": [{"i": 0, "fr": "AI情報通 : informations du 29 octobre", "nom_propre": False}]}
    )
    assert out == []


def test_une_traduction_en_table_qui_garde_des_caracteres_asiatiques_se_refait():
    n = {"id": "d1", "label": "AI情報通：10月29日15時の情報", "kind": "article"}
    table = {"libelles": {"d1": ["AI情報通 : informations du 29 octobre", fr.empreinte(n["label"])]}}
    assert fr.a_traduire([n], table) == [("d1", "l", n["label"])]

"""Une longue réponse réussie n'est pas un refus d'usage.

Le 15.11.2026, sur 78 lectures de papiers d'un média d'ingénierie, 6 réponses réussies de plusieurs milliers de mots
ont été comptées comme des limites d'usage : leur texte disait « rate limit », « quota » ou « overloaded », des mots du
sujet. Trois de suite posent la marque et arrêtent les boucles trois heures. Un refus d'usage est un message court.
"""

from __future__ import annotations

import json
import types

import pytest

from newskit import couts

LECTURE = "## Concepts\n\n### Rate limiting\nThe gateway applies a rate limit and a quota per key; an overloaded worker sheds load.\n" + "The paper measures latency. " * 80  # fmt: skip
REFUS = "You've hit your limit · resets at 5pm (Europe/Zurich)"


def env(resultat: str, **k) -> dict:
    return {"result": resultat, "is_error": False, "duration_api_ms": 41000, "total_cost_usd": 0.7, **k}


def test_une_longue_reponse_qui_parle_de_quota_n_est_pas_une_limite():
    assert len(LECTURE) > couts.REFUS_MAX and couts.RE_LIMITE.search(LECTURE)
    assert couts.est_une_limite(env(LECTURE), "", 0) is False


@pytest.mark.parametrize(
    "message", [REFUS, "Claude AI usage limit reached", "API Error: 529 overloaded_error", "rate limit"]
)
def test_un_message_court_de_refus_reste_une_limite(message):
    assert couts.est_une_limite(env(message, is_error=True), "", 1) is True
    assert couts.est_une_limite({}, message, 1) is True


def test_le_seuil_est_le_nombre_de_caracteres_du_message():
    juste = (REFUS + " " * couts.REFUS_MAX)[: couts.REFUS_MAX]
    assert couts.est_une_limite(env(juste), "", 0) is True
    assert couts.est_une_limite(env(juste + "."), "", 0) is False
    assert couts.est_une_limite({}, juste, 1) is True and couts.est_une_limite({}, juste + ".", 1) is False


def test_un_appel_que_l_api_n_a_pas_recu_reste_une_limite_quelle_que_soit_sa_longueur():
    assert couts.est_une_limite(env(LECTURE, is_error=True, duration_api_ms=0), "", 1) is True


def test_trois_longues_reponses_reussies_ne_posent_pas_la_marque(tmp_path, monkeypatch):
    monkeypatch.setattr(couts, "REGISTRE", tmp_path / "couts.jsonl")
    monkeypatch.setattr(couts, "MARQUE", tmp_path / "limite_atteinte")
    monkeypatch.setattr(couts, "_consecutives", 0)
    monkeypatch.setattr(couts, "sentinelle", lambda *a, **k: None)
    r = types.SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(env(LECTURE)))
    for _ in range(couts.SEUIL):
        couts.enregistrer("extraction", "papiers", "m", r, 1.0)
    lignes = [json.loads(x) for x in (tmp_path / "couts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(x["ok"], x["limite"]) for x in lignes] == [(True, False)] * couts.SEUIL
    assert not (tmp_path / "limite_atteinte").exists() and not couts.en_limite()

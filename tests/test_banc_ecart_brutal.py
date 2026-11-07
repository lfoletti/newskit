"""Un écart brutal arrête le banc (CLAUDE.md, Bancs ; CHANTIER_GENERIQUE.md § 1, écart 16 ; anticorps
test_plafonds_et_chute_du_banc, volet banc).

`banc.reposer_recentes` publiait la repose quelle que soit la chute. Une part « juste » du graphe qui perd
ECART_BRUTAL ou plus d'un coup n'est pas publiée : banc.json reste tel quel (son état de pose aussi, la nuit suivante
repose : « une perte se repose avant d'être crue »), la mesure est gardée à part, la sortie le dit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from newskit import banc  # noqa: E402

N = 10


def _banc(base: Path) -> Path:
    det = [
        {
            "i": i,
            "type": "ag_recent",
            "question": f"q{i}",
            **{c: {"reponse": "r", "verdict": "juste"} for c in banc.CONDITIONS},
        }
        for i in range(N)
    ]
    r = banc.recompter({"methode": "agnostique", "details": det, "graphe_repose_etat": "0"})
    p = base / "banc.json"
    p.write_text(json.dumps(r), encoding="utf-8")
    return p


@pytest.fixture
def pose(tmp_path, monkeypatch):
    p = _banc(tmp_path)
    monkeypatch.setattr(banc.cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    monkeypatch.setattr(banc.cfg, "banc", lambda: {"delai": 1})
    monkeypatch.setattr(banc, "etat_du_graphe", lambda g: "1")
    monkeypatch.setattr(banc, "methode_du_graphe", lambda g: "m")
    monkeypatch.setattr(banc, "interrompu", lambda *a: False)
    monkeypatch.setattr(banc, "repondre", lambda g, c, qs, b: {d["i"]: {"reponse": "r2"} for d in qs})

    def juste(n_justes):
        monkeypatch.setattr(
            banc, "juger", lambda qs, rep, delai: {d["i"]: ("juste" if d["i"] < n_justes else "faux") for d in qs}
        )

    return tmp_path, p, juste


def test_une_chute_de_100_a_60_n_est_pas_publiee(pose, capsys):
    base, p, juste = pose
    avant = p.read_text(encoding="utf-8")
    juste(6)
    assert banc.reposer_recentes("g") is None
    assert p.read_text(encoding="utf-8") == avant  # banc.json et son état de pose intacts
    assert not (base / "banc_historique.jsonl").exists()
    (garde,) = (base / "bancs").glob("*_ecart_brutal.json")
    assert json.loads(garde.read_text(encoding="utf-8"))["conditions"]["graphe"]["juste"] == 0.6
    assert "écart brutal" in capsys.readouterr().err


def test_une_baisse_moderee_est_publiee(pose):
    base, p, juste = pose
    juste(8)
    r = banc.reposer_recentes("g")
    assert r["conditions"]["graphe"]["juste"] == 0.8
    assert json.loads(p.read_text(encoding="utf-8"))["graphe_repose_etat"] == "1"
    assert (base / "banc_historique.jsonl").exists()

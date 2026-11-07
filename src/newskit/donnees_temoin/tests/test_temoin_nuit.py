"""Le témoin de la nuit (CHANTIER_GENERIQUE.md § 4.3 ; NEWSKIT.md, N4) : à 00:30, `temoin.py nuit` passe le témoin
entier et pose `cache/temoin_vert` (date, commit, verdict) ; la routine refuse de partir sur un témoin rouge, d'un
autre jour ou d'un autre commit.

Deux rouges se distinguent : « d'enregistrement » (une consigne changée, code 98 : les manquantes se paient sous
plafond, puis le témoin repasse une fois) et « d'infrastructure » (fuite, exception, anticorps tombé : la nuit ne part
pas). Sans marque du tout (le daemon du témoin pas encore installé), la routine part et le dit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from newskit import routine_hebdo as rh  # noqa: E402
from newskit import temoin  # noqa: E402

JOUR, COMMIT = "2026-11-07", "a" * 40


def _marque(racine: Path, **m) -> None:
    p = racine / "cache" / temoin.MARQUE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"date": JOUR, "commit": COMMIT, "vert": True, "detail": "55 passed", **m}), encoding="utf-8"
    )


# --- Le verdict lu par la routine --------------------------------------------


def test_sans_marque_la_routine_part_et_le_dit(tmp_path):
    ok, dit = temoin.verdict(tmp_path, COMMIT, JOUR)
    assert ok and "jamais passé" in dit


def test_un_temoin_vert_du_jour_et_du_commit_laisse_partir(tmp_path):
    _marque(tmp_path)
    assert temoin.verdict(tmp_path, COMMIT, JOUR) == (True, "")


@pytest.mark.parametrize(
    "marque,motif",
    [
        ({"vert": False, "detail": "1 failed, 54 passed"}, "rouge"),
        ({"date": "2026-11-06"}, "2026-11-06"),
        ({"commit": "b" * 40}, "bbbbbbbbb"),
    ],
)
def test_un_temoin_rouge_ancien_ou_d_un_autre_commit_arrete(tmp_path, marque, motif):
    _marque(tmp_path, **marque)
    ok, dit = temoin.verdict(tmp_path, COMMIT, JOUR)
    assert not ok and motif in dit


# --- La nuit -------------------------------------------------------------------


def _nuit(tmp_path, essais, manquantes=0, payes=0):
    """`essais` : les (code, dernière ligne) rendus par chaque passage du témoin, dans l'ordre."""
    passes, payees = [], []

    def essai():
        passes.append(1)
        return essais[len(passes) - 1]

    def rejeu(dossier):
        t = temoin.Temoin(dossier, dossier / "reponses")
        t.manquantes.mkdir(parents=True, exist_ok=True)
        for i in range(manquantes):
            (t.manquantes / f"{i}.json").write_text("{}", encoding="utf-8")
        return t

    def payer(t, plafond):
        payees.append(plafond)
        return {"payes": payes, "erreurs": 0, "restantes": manquantes - payes, "cout_usd": 0.1 * payes}

    code = temoin.nuit(tmp_path, essai=essai, rejeu=rejeu, payer=payer, commit=COMMIT, jour=JOUR)
    m = json.loads((tmp_path / "cache" / temoin.MARQUE).read_text(encoding="utf-8"))
    return code, m, len(passes), payees


def test_une_nuit_verte_pose_la_marque_du_jour_et_du_commit(tmp_path):
    code, m, passes, payees = _nuit(tmp_path, [(0, "55 passed in 53.15s")])
    assert (code, m["vert"], m["date"], m["commit"], passes, payees) == (0, True, JOUR, COMMIT, 1, [])


def test_un_rouge_d_infrastructure_pose_une_marque_rouge_sans_rien_payer(tmp_path):
    code, m, passes, payees = _nuit(tmp_path, [(1, "1 failed, 54 passed")])
    assert (code, m["vert"], m["detail"], passes, payees) == (1, False, "1 failed, 54 passed", 1, [])


def test_un_rouge_d_enregistrement_paie_sous_plafond_et_repasse_une_fois(tmp_path):
    code, m, passes, payees = _nuit(tmp_path, [(1, "1 failed"), (0, "55 passed")], manquantes=2, payes=2)
    assert (code, m["vert"], m["reponses_neuves"], passes, payees) == (0, True, 2, 2, [temoin.PLAFOND])


def test_un_reenregistrement_qui_ne_paie_rien_ne_repasse_pas(tmp_path):
    code, m, passes, _ = _nuit(tmp_path, [(1, "1 failed")], manquantes=2, payes=0)
    assert (code, m["vert"], passes) == (1, False, 1)


# --- La routine ----------------------------------------------------------------


def test_la_routine_refuse_un_temoin_rouge(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TEMOIN_REEL", raising=False)
    monkeypatch.setattr(rh, "RACINE", tmp_path)
    monkeypatch.setattr(temoin, "commit_du_code", lambda: COMMIT)
    monkeypatch.setattr(rh.time, "strftime", lambda f, *a: JOUR if f == "%Y-%m-%d" else "x")
    _marque(tmp_path, vert=False)
    assert rh.exiger_le_temoin() == 2
    assert "témoin" in capsys.readouterr().err
    _marque(tmp_path)
    assert rh.exiger_le_temoin() is None


def test_dans_le_temoin_la_routine_ne_s_exige_pas_elle_meme(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMOIN_REEL", str(tmp_path))
    monkeypatch.setattr(rh, "RACINE", tmp_path)
    _marque(tmp_path, vert=False)
    assert rh.exiger_le_temoin() is None


def test_la_routine_exige_le_temoin_apres_la_pause_et_avant_le_verrou():
    src = Path(rh.__file__).read_text(encoding="utf-8")
    main = src[src.index("def main() -> int:") :]
    pause, exige, verrou = (
        main.index(x)
        for x in ('pause = RACINE / "cache" / "routines.pause"', "exiger_le_temoin()", "verrou = prendre_le_verrou(")
    )
    assert pause < exige < verrou

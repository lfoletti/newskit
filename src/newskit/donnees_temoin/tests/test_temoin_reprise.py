"""Le témoin absurde, anticorps 3 sur 10 : un résultat payé est écrit dès qu'il existe (CHANTIER_GENERIQUE.md § 4.3,
test_resultat_ecrit_des_qu_il_existe).

Mode TEMOIN_TUER_APRES=10 : le faux claude tue net (SIGKILL) l'étape qui l'appelle au dixième appel, un appel à la
fois (SE_PARALLELE=1). Les neuf réponses servies avant sont déjà écrites ; relancée, la routine ne redemande aucune
d'elles et finit la couche 2. Incident : des résultats tenus en mémoire jusqu'à la fin d'une boucle, perdus et repayés
quand l'étape tombait (recueil, Consommation) ; le banc ne garde plus de banc.json global.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import temoin  # noqa: E402

TUER = 10


def _passe(t, extra):
    env = temoin.environnement(t)
    env.update({"SE_PARALLELE": "1", **extra})
    return subprocess.run([sys.executable, str(t.code / "newskit" / "routine_hebdo.py"), "--graphe", temoin.GRAPHE,
                           "--sans-commit", "--sans-courriel"], env=env, cwd=t.donnees,
                          capture_output=True, text=True, timeout=900)


def _appels(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@pytest.fixture(scope="module")
def deux_passes(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("reprise") / "t")
    r1 = _passe(t, {"TEMOIN_TUER_APRES": str(TUER)})
    lots_1 = len(list((t.donnees / "graphe" / "couche2" / "fiches").glob("*.json")))
    (t.dossier / "appels.jsonl").rename(t.dossier / "appels_1.jsonl")
    r2 = _passe(t, {})
    return t, r1, r2, _appels(t.dossier / "appels_1.jsonl"), _appels(t.dossier / "appels.jsonl"), lots_1


def test_l_etape_tuee_a_ecrit_ses_resultats_servis(deux_passes):
    t, r1, _, a1, _, lots_1 = deux_passes
    assert len(a1) == TUER and "couche 2 incrémentale : code -9" in r1.stdout, r1.stdout[-1500:]
    assert lots_1 == TUER - 1


def test_relancee_elle_ne_redemande_rien_de_servi(deux_passes):
    t, _, r2, a1, a2, _ = deux_passes
    servis = {a["cle"] for a in a1[:TUER - 1]}
    assert [a["cle"] for a in a2 if a["cle"] in servis] == []
    assert a1[TUER - 1]["cle"] in {a["cle"] for a in a2}          # l'appel tué, lui, est repris
    assert "ok      couche 2 incrémentale" in r2.stdout and not t.manquantes.exists()


def test_pas_de_banc_json_global(deux_passes):
    t, *_ = deux_passes
    assert list(t.donnees.rglob("banc.json")) == []

"""Le témoin absurde, anticorps 2 sur 10 : le coupe-circuit de la limite d'usage, isolé (CHANTIER_GENERIQUE.md § 4.3,
test_coupe_circuit_limite_isolee).

Mode TEMOIN_LIMITE=3 : le faux claude répond aux trois premiers appels, puis la fenêtre d'usage se ferme et tout est
refusé. Trois refus d'affilée posent la marque de la copie ; les étapes au modèle qui suivent sont « sautées », jamais
« finies » ; la vraie marque n'est pas touchée. Défaut trouvé le 06.11.2026 : l'extraction tourne en parallèle, un
appel lancé avant la limite finissait après les trois refus, son succès retirait la marque, et la nuit continuait
comme si la fenêtre était rouverte.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import commun  # noqa: E402
from newskit import temoin  # noqa: E402


@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    avant = temoin.photo_du_reel()
    t = temoin.preparer(tmp_path_factory.mktemp("limite") / "t")
    env = temoin.environnement(t)
    env["TEMOIN_LIMITE"] = "3"
    r = subprocess.run(
        [
            sys.executable,
            str(t.code / "newskit" / "routine_hebdo.py"),
            "--graphe",
            temoin.GRAPHE,
            "--sans-commit",
            "--sans-courriel",
        ],
        env=env,
        cwd=t.donnees,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return t, r, avant


def test_trois_refus_posent_la_marque_de_la_copie(passage):
    t, r, avant = passage
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    assert (t.donnees / "cache" / "limite_atteinte").exists(), r.stdout[-2500:]
    assert temoin.fuites_vers_le_reel(avant) == []
    assert not t.journal.exists() or t.journal.read_text(encoding="utf-8") == ""


def test_les_etapes_au_modele_suivantes_sont_sautees(passage):
    t, r, _ = passage
    assert "limite d'usage atteinte" in r.stdout
    for etape in ("thèmes émergents",):
        assert f"sauté   {etape} : limite d'usage atteinte" in r.stdout, etape
    assert "ok      couche 2 incrémentale" not in r.stdout

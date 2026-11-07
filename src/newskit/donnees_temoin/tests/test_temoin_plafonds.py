"""Le témoin absurde, anticorps 7 sur 10, volet plafonds (CHANTIER_GENERIQUE.md § 4.3, test_plafonds_et_chute_du_banc ;
volet banc : test_banc_ecart_brutal.py).

Le registre du témoin déclare les plafonds de la couche 2 (`plafond_couche2`, `plafond_couche2_max`) : le validateur
les accepte, et l'extraction s'arrête au plafond même quand les 36 fiches sont arrivées le jour même. Incident :
377 articles collectés un soir auraient été extraits la nuit même, une centaine de dollars d'un coup (09.10.2026).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from newskit import temoin  # noqa: E402

PLAFOND = 10


@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("plafonds") / "t")
    reg = t.donnees / "config.yaml"  # les graphes et leurs bornes vivent au config.yaml du média
    d = yaml.safe_load(reg.read_text(encoding="utf-8"))
    d["graphes"][temoin.GRAPHE].update({"plafond_couche2": 4, "plafond_couche2_max": PLAFOND})
    reg.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            str(t.code / "newskit" / "routine_hebdo.py"),
            "--graphe",
            temoin.GRAPHE,
            "--sans-commit",
            "--sans-courriel",
        ],
        env=temoin.environnement(t),
        cwd=t.donnees,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return t, r


def test_le_registre_aux_plafonds_est_accepte(passage):
    _, r = passage
    assert r.returncode == 0 and "ConfigInvalide" not in r.stdout + r.stderr, r.stdout[-1500:]


def test_l_extraction_s_arrete_au_plafond_du_registre(passage):
    t, r = passage
    lignes = [json.loads(l) for l in (t.donnees / "cache" / "couts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(l["poste"] == "extraction" for l in lignes) == PLAFOND
    assert len(list((t.donnees / "graphe" / "couche2" / "fiches").glob("*.json"))) == PLAFOND

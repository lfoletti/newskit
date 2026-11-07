"""Le crochet du plugin : une garde hors du modèle.

Quand la fenêtre d'usage est fermée (la marque `cache/limite_atteinte` du média, posée par newskit.couts), un appel au
modèle ne sert à rien : le crochet refuse, avant qu'elle parte, toute commande Bash qui lance `claude -p` ou une nuit
(`newskit routine`). Il ne lit que la marque ; il n'importe pas le moteur, que le Python du crochet peut ne pas avoir.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from newskit import couts

ICI = Path(__file__).resolve().parents[2]
CROCHET = ICI / "hooks" / "garde_limite.py"


def _media(d: Path, marque_age_s: float | None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text("version: 1\nmedia:\n  nom: essai\n", encoding="utf-8")
    if marque_age_s is not None:
        m = d / "cache" / "limite_atteinte"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("limite (essai)", encoding="utf-8")
        t = time.time() - marque_age_s
        os.utime(m, (t, t))
    return d


def _appel(cwd: Path, commande: str, env_donnees: Path | None = None) -> dict | None:
    env = {k: v for k, v in os.environ.items() if k != "NEWSKIT_DONNEES"}
    if env_donnees is not None:
        env["NEWSKIT_DONNEES"] = str(env_donnees)
    entree = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd),
              "tool_input": {"command": commande}}
    r = subprocess.run([sys.executable, "-I", str(CROCHET)], input=json.dumps(entree), capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def _refus(sortie: dict | None) -> bool:
    return bool(sortie) and sortie["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("commande", ["newskit routine --graphe g", "echo x | claude -p --model m",
                                      "cd /x && newskit routine"])
def test_marque_fraiche_l_appel_au_modele_est_refuse(tmp_path, commande):
    sortie = _appel(_media(tmp_path / "m", 60), commande)
    assert _refus(sortie)
    assert "limite" in sortie["hookSpecificOutput"]["permissionDecisionReason"]


def test_marque_ancienne_rien_n_est_refuse(tmp_path):
    assert _appel(_media(tmp_path / "m", couts.MARQUE_VALIDE_S + 60), "newskit routine --graphe g") is None


def test_sans_marque_rien_n_est_refuse(tmp_path):
    assert _appel(_media(tmp_path / "m", None), "claude -p x") is None


def test_une_commande_sans_modele_passe_meme_en_limite(tmp_path):
    assert _appel(_media(tmp_path / "m", 60), "newskit media; ls cache") is None


def test_la_variable_des_donnees_designe_le_media(tmp_path):
    m = _media(tmp_path / "m", 60)
    assert _refus(_appel(tmp_path, "newskit routine", env_donnees=m))


def test_un_sous_dossier_du_media_est_dans_le_media(tmp_path):
    m = _media(tmp_path / "m", 60)
    (m / "sources" / "x").mkdir(parents=True)
    assert _refus(_appel(m / "sources" / "x", "newskit routine"))


def test_hors_d_un_media_rien_n_est_refuse(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "limite_atteinte").write_text("x", encoding="utf-8")
    assert _appel(tmp_path, "claude -p x") is None


def test_une_entree_illisible_ne_bloque_rien(tmp_path):
    r = subprocess.run([sys.executable, "-I", str(CROCHET)], input="pas du json", capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_la_duree_de_la_marque_est_celle_du_moteur():
    m = re.search(r"^MARQUE_VALIDE_S = (\d+)", CROCHET.read_text(encoding="utf-8"), re.MULTILINE)
    assert m and int(m.group(1)) == couts.MARQUE_VALIDE_S


def test_le_crochet_est_declare_sur_bash():
    h = json.loads((ICI / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entree,) = h["hooks"]["PreToolUse"]
    assert entree["matcher"] == "Bash"
    assert "garde_limite.py" in entree["hooks"][0]["command"]

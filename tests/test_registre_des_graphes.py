"""Le registre des graphes d'un média (N5, 07.11.2026) : libellé, résumé et sujets déclarés de chaque graphe, que les
thèmes émergents réécrivent chaque nuit. Il était fixé à `~/.claude/skills/kg/graphes.json`, le registre du skill
/kg de la première édition : un média sans ce skill n'avait pas de sujets. Il vit dans le dossier du média
(`graphes.json`) ; `registre_des_graphes:` de `config.yaml` le place ailleurs (`~` développé, sinon relatif au média).
`config.yaml` reste écrit à la main : les sujets, réécrits par le moteur, n'y entrent pas.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from newskit import config_sources as cfg  # noqa: E402
from newskit import depots  # noqa: E402
from newskit import media  # noqa: E402
from newskit import routine_hebdo as rh  # noqa: E402


def _media(d: Path, **plus) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(
        yaml.safe_dump({"version": 1, "media": {"nom": "apiculture"}, **plus}), encoding="utf-8"
    )
    return d


def test_sans_cle_le_registre_est_dans_le_dossier_du_media(tmp_path):
    c = media.charger(_media(tmp_path) / "config.yaml")
    assert media.registre_des_graphes(c, tmp_path) == tmp_path / "graphes.json"


def test_la_cle_developpe_le_tilde_ou_part_du_media(tmp_path):
    c = media.charger(_media(tmp_path, registre_des_graphes="~/.claude/skills/kg/graphes.json") / "config.yaml")
    assert media.registre_des_graphes(c, tmp_path) == Path.home() / ".claude" / "skills" / "kg" / "graphes.json"
    c = media.charger(_media(tmp_path, registre_des_graphes="etat/graphes.json") / "config.yaml")
    assert media.registre_des_graphes(c, tmp_path) == tmp_path / "etat" / "graphes.json"


@pytest.mark.parametrize("v", ["", 3, ["a"]])
def test_une_cle_qui_n_est_pas_un_chemin_est_refusee(tmp_path, v):
    with pytest.raises(media.ConfigMediaInvalide, match="registre_des_graphes"):
        media.charger(_media(tmp_path, registre_des_graphes=v) / "config.yaml")


def test_les_modules_lisent_le_registre_du_media(tmp_path):
    d = _media(tmp_path / "m", registre_des_graphes="etat/graphes.json")
    code = "from newskit import themes_emergents as te, nouveau_graphe as ng; print(te.REGISTRE_SKILL); print(ng.SKILL)"
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEWSKIT_")} | {"NEWSKIT_DONNEES": str(d)}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.split() == [str(d.resolve() / "etat" / "graphes.json")] * 2


def test_les_depots_prennent_les_sujets_du_registre_du_media(tmp_path, monkeypatch):
    reg = tmp_path / "graphes.json"
    reg.write_text(json.dumps({"ruchers": {"resume": "Ruchers", "sujets": ["ruche en paille"]}}), encoding="utf-8")
    monkeypatch.setattr(media, "registre_des_graphes", lambda *a, **k: reg)

    class S:
        graphe = "ruchers"

    monkeypatch.setattr(cfg, "toutes", lambda *a, **k: [S()])
    monkeypatch.setattr(cfg, "graphe_declare", lambda nom: {"libelle": "Ruchers"})
    assert depots.graphes_candidats() == {"ruchers": "Ruchers ; ruche en paille"}


def test_la_routine_lit_le_registre_du_media(tmp_path, monkeypatch):
    reg = tmp_path / "graphes.json"
    reg.write_text(json.dumps({"ruchers": {"racine": str(rh.RACINE)}}), encoding="utf-8")
    monkeypatch.setattr(media, "registre_des_graphes", lambda *a, **k: reg)
    assert rh.skill_a_synchroniser(["ruchers"]) is True

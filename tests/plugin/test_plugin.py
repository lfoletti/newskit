"""La forme du plugin : son manifeste, sa place de marché et son skill disent la même chose que le paquet."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import newskit
from newskit import cli, media

ICI = Path(__file__).resolve().parents[2]
SKILL = ICI / "skills" / "newskit" / "SKILL.md"


def _json(rel: str) -> dict:
    return json.loads((ICI / rel).read_text(encoding="utf-8"))


def test_une_version_partout():
    projet = tomllib.loads((ICI / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "version" not in projet and "version" in projet["dynamic"]          # lue dans __init__.py par hatch
    assert _json(".claude-plugin/plugin.json")["version"] == newskit.__version__
    assert f"## [{newskit.__version__}]" in (ICI / "CHANGELOG.md").read_text(encoding="utf-8")


def test_la_place_de_marche_sert_le_plugin_de_la_racine():
    m = _json(".claude-plugin/marketplace.json")
    (p,) = m["plugins"]
    assert p["name"] == _json(".claude-plugin/plugin.json")["name"] == "newskit"
    assert p["source"] == "./"


def test_le_skill_a_son_en_tete():
    t = SKILL.read_text(encoding="utf-8")
    m = re.match(r"^---\nname: newskit\ndescription: (.+?)\n---\n", t, re.DOTALL)
    assert m and 50 < len(m.group(1)) < 1024


def test_chaque_commande_citee_par_le_skill_existe():
    t = SKILL.read_text(encoding="utf-8")
    citees = set(re.findall(r"`newskit ([a-z][a-z_-]*)", t))
    assert citees, "le skill ne cite aucune commande"
    connues = set(cli.commandes()) | set(cli.ALIAS)
    assert {c for c in citees if cli.ALIAS.get(c, c.replace("-", "_")) not in connues and c not in connues} == set()


def test_le_skill_nomme_chaque_role_de_modele_et_chaque_champ_du_media():
    t = SKILL.read_text(encoding="utf-8")
    for role in media.ROLES:
        assert f"`{role}`" in t, role
    for champ in ("media.langue", "media.traductions", "media.frequence", "sources", "diffusion", "graphes"):
        assert f"`{champ}`" in t, champ


def test_le_moteur_ne_nomme_personne_dans_le_plugin():
    motif = re.compile(r"Lauro|Foletti|/Users/|/mnt/dat[a]")
    for p in [SKILL, ICI / "hooks" / "garde_limite.py", ICI / "hooks" / "hooks.json", ICI / "README.md", ICI / "README.fr.md"]:
        assert not motif.search(p.read_text(encoding="utf-8")), p.name


def test_le_temoin_part_d_une_installation_simple():
    """Une nuit ne part pas sans témoin vert : ce qu'il lui faut (pytest) s'installe avec le paquet, sans extra. Le
    démarrage rapide du README (pip install, puis newskit temoin nuit) tombait sur « No module named pytest »."""
    projet = tomllib.loads((ICI / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(re.match(r"pytest\b", d) for d in projet["dependencies"])
    assert "temoin" not in projet.get("optional-dependencies", {})
    assert "[temoin]" not in (ICI / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

"""`newskit nouveau-graphe` dans un média quelconque (N5, 07.11.2026).

La tâche planifiée d'un graphe lance le moteur installé (`<python> -m newskit.routine_hebdo`) sur le dossier du média
(NEWSKIT_DONNEES), avec l'interpréteur qui porte le paquet ; elle n'appelle plus `/opt/miniconda3/bin/python3
scripts/routine_hebdo.py`. Un média sans installateur des daemons reçoit une tâche prête à poser (sous `taches/`) ;
un média qui en a un (la première édition) garde ses champs à remplir sous `scripts/`. Les pièces de ses installateurs
(daemons, serveur) ne sont demandées qu'au média qui les porte.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from newskit import config_sources as cfg  # noqa: E402
from newskit import nouveau_graphe as ng  # noqa: E402


@pytest.fixture
def media(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(ng, "RACINE", tmp_path)
    monkeypatch.setattr(ng, "INSTALLATEUR", tmp_path / "deploiement" / "installer_daemons_mac.sh")
    monkeypatch.setattr(ng, "INSTALLATEUR_VPS", tmp_path / "deploiement" / "installer_vps.sh")
    monkeypatch.setattr(ng, "SKILL", tmp_path / "skill" / "graphes.json")
    monkeypatch.setattr(cfg, "edition", lambda: {"launchd": "com.exemple", "nom": "apiculture"})
    monkeypatch.setattr(cfg, "charger", lambda: {"graphes": {}, "sources": []})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda nom: {"base": tmp_path / "graphe" / nom})
    monkeypatch.setattr(cfg, "toutes", lambda graphe=None: [])
    return tmp_path


def test_sans_installateur_la_tache_est_prete_a_poser(media):
    p = ng.ecrire_plist("ruchers", "Ruchers", [(5, 30)])
    assert p.parent == media / "taches" and p.name == "com.exemple.apiculture-ruchers.plist"
    texte = p.read_text(encoding="utf-8")
    assert "__" not in texte
    d = plistlib.loads(texte.encode("utf-8"))
    assert d["Label"] == "com.exemple.apiculture-ruchers"
    assert d["ProgramArguments"] == [
        sys.executable,
        "-u",
        "-m",
        "newskit.routine_hebdo",
        "--graphe",
        "ruchers",
        "--planifie",
    ]
    assert d["WorkingDirectory"] == str(media)
    assert d["EnvironmentVariables"]["NEWSKIT_DONNEES"] == str(media)
    assert d["EnvironmentVariables"]["HOME"] == str(Path.home())
    assert d["StartCalendarInterval"] == [{"Hour": 5, "Minute": 30}]
    assert Path(d["StandardOutPath"]).parent.is_dir()  # launchd n'y crée pas le dossier du journal


def test_avec_installateur_la_tache_garde_ses_champs_sous_scripts(media):
    ng.INSTALLATEUR.parent.mkdir(parents=True)
    ng.INSTALLATEUR.write_text("TACHES=(\n)\n", encoding="utf-8")
    p = ng.ecrire_plist("ruchers", "Ruchers", [(5, 30)])
    assert p == media / "scripts" / "launchd_ruchers.plist.exemple"
    texte = p.read_text(encoding="utf-8")
    assert "__DEPOT__" in texte and "__MAISON__" in texte
    assert f"<string>{sys.executable}</string>" in texte and "<string>newskit.routine_hebdo</string>" in texte
    assert (
        "scripts/routine_hebdo.py" not in texte and "/opt/miniconda3/bin/python3</string>\n    <string>-u" not in texte
    )


def test_le_modele_ne_nomme_ni_un_interpreteur_ni_un_chemin_du_depot():
    t = ng.MODELE_PLIST.read_text(encoding="utf-8")
    assert "/opt/miniconda3/bin/python3</string>" not in t and "scripts/routine_hebdo.py" not in t


def test_sans_installateur_aucune_piece_des_installateurs(media):
    pieces = [p for _, p, _ in ng.pieces("ruchers")]
    assert not any("installer_daemons_mac" in p or "installer_vps" in p or "GRAPHES" in p for p in pieces), pieces
    assert any(p.startswith("tâche planifiée") for p in pieces), pieces


def test_avec_installateurs_leurs_pieces_sont_demandees(media):
    ng.INSTALLATEUR.parent.mkdir(parents=True)
    ng.INSTALLATEUR.write_text("TACHES=(\n)\n", encoding="utf-8")
    ng.INSTALLATEUR_VPS.write_text("GRAPHES=a,b\n", encoding="utf-8")
    pieces = [p for _, p, _ in ng.pieces("ruchers")]
    assert any("installer_daemons_mac.sh" in p for p in pieces) and any("installer_vps.sh" in p for p in pieces)

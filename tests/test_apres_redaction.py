"""Un pas de l'édition après la rédaction du fil (mainteneur, 13.11.2026 : « la récupération d'adresse devrait faire
partie du pipeline standard et accompagner la rédaction »). Le `config.yaml` du média nomme un module de l'édition
(`apres_redaction: scripts/contacts.py`) qui porte `apres_redaction(graphe) -> str` ; le passage l'appelle après le
fil de chaque graphe. Sa panne se dit et n'arrête jamais le passage."""

from __future__ import annotations

import pytest
import yaml

from newskit import collecte_continue as cc
from newskit import diffusion, media


@pytest.fixture
def edition(tmp_path, monkeypatch):
    def poser(code, nom="scripts/pas.py"):
        (tmp_path / nom).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / nom).write_text(code, encoding="utf-8")
        monkeypatch.setattr(diffusion, "RACINE", tmp_path)
        monkeypatch.setattr(media, "courant", lambda: {"apres_redaction": nom})
        monkeypatch.setattr(diffusion, "_charges", {})

    return poser


def test_le_pas_de_l_edition_suit_le_fil_du_graphe(edition):
    edition("def apres_redaction(g):\n    return f'4 article(s) de {g}'\n")
    ligne = cc.apres_redaction("ai-governance")
    assert ligne.endswith("ai-governance après la rédaction : 4 article(s) de ai-governance")


def test_un_pas_muet_ne_s_ecrit_pas(edition):
    edition("def apres_redaction(g):\n    return ''\n")
    assert cc.apres_redaction("g") == ""


def test_sans_declaration_rien(monkeypatch):
    monkeypatch.setattr(media, "courant", lambda: {})
    assert cc.apres_redaction("g") == ""


def test_une_panne_se_dit_et_n_arrete_rien(edition):
    edition("def apres_redaction(g):\n    raise OSError('réseau')\n")
    assert "après la rédaction : échec (OSError: réseau)" in cc.apres_redaction("g")


def test_le_passage_l_appelle_apres_le_fil(monkeypatch, tmp_path, capsys):
    import subprocess

    monkeypatch.setattr(cc.rh, "VERROU", tmp_path / "routine.verrou")
    monkeypatch.setattr(cc.rh, "VERROU_COLLECTE", tmp_path / "collecte.verrou")
    monkeypatch.setattr(cc.rh, "graphe_en_routine", lambda *a: None)
    monkeypatch.setattr(cc.rh.cfg, "graphe_du_site", lambda: "autre")
    ordre = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: ordre.append(cmd[1].rsplit("/", 1)[1]) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(cc, "surveiller", lambda *a: None)
    monkeypatch.setattr(cc, "inscriptions", lambda: "")
    monkeypatch.setattr(cc, "apres_redaction", lambda g: ordre.append(f"après {g}") or f"{g} après la rédaction : 2")
    monkeypatch.setattr("sys.argv", ["collecte_continue.py", "--graphe", "g"])
    assert cc.main() == 0
    assert ordre == ["collecte_rss.py", "fil.py", "après g"]
    assert "g après la rédaction : 2" in capsys.readouterr().out


def _config(tmp_path, valeur):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "media": {"nom": "media"}, "apres_redaction": valeur}), encoding="utf-8")
    return p


@pytest.mark.parametrize("valeur", ["/abs/pas.py", "../pas.py", "pas.txt", 3])
def test_la_declaration_est_un_module_du_media(tmp_path, valeur):
    with pytest.raises(media.ConfigMediaInvalide, match="apres_redaction : un module"):
        media.charger(_config(tmp_path, valeur))


def test_le_config_admet_la_declaration(tmp_path):
    assert media.charger(_config(tmp_path, "scripts/pas.py"))["apres_redaction"] == "scripts/pas.py"


def test_un_module_de_l_edition_se_charge_une_fois(edition, tmp_path):
    edition("N = []\ndef apres_redaction(g):\n    N.append(g)\n    return ''\n")
    m = diffusion.charger("scripts/pas.py")
    assert diffusion.charger("scripts/pas.py") is m
    with pytest.raises(FileNotFoundError, match="absent.py introuvable"):
        diffusion.charger("scripts/absent.py")


def test_un_module_deja_importe_sous_son_nom_reste_le_meme(edition, tmp_path, monkeypatch):
    import importlib.util
    import sys

    edition("X = 1\n", nom="scripts/voisin.py")
    spec = importlib.util.spec_from_file_location("voisin", tmp_path / "scripts" / "voisin.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setitem(sys.modules, "voisin", m)
    assert diffusion.charger("scripts/voisin.py") is m


def test_la_diffusion_declaree_passe_par_le_meme_chargeur(tmp_path, monkeypatch):
    (tmp_path / "abonnes.py").write_text("def lire():\n    return [{'adresse': 'a@b.c'}]\n", encoding="utf-8")
    monkeypatch.setattr(diffusion, "RACINE", tmp_path)
    monkeypatch.setattr(diffusion, "_charges", {})
    monkeypatch.setattr(media, "courant", lambda: {"diffusion": "abonnes.py"})
    assert diffusion.lire() == [{"adresse": "a@b.c"}]
    monkeypatch.setattr(media, "courant", lambda: {})
    assert diffusion.module() is None

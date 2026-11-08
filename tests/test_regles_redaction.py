"""Les règles de rédaction se lisent dans un fichier du média, éditable sans version (Lauro, 8.11.2026)."""

import pytest

from newskit import config_sources as cfg
from newskit import fil, fil_anglais as fa, fil_fiche as ff
from newskit import lettre as lt

TEXTE = "# fr\nRègle française {sans format}.\n\n# en\nEnglish rule.\n"


@pytest.fixture
def media(tmp_path, monkeypatch):
    (tmp_path / "regles.md").write_text(TEXTE, encoding="utf-8")
    monkeypatch.setattr(lt, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"regles_redaction": "regles.md"})


def test_le_fichier_donne_les_regles_de_chaque_langue(media):
    assert lt.regles("g") == "Règle française {sans format}.\n"
    assert lt.regles("g", "en") == "English rule.\n"


def test_sans_fichier_declare_les_regles_du_paquet(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert lt.regles("g") == lt.REGLES and lt.regles("g", "en") == fa.REGLES


def test_une_section_absente_garde_les_regles_du_paquet(tmp_path, monkeypatch):
    (tmp_path / "regles.md").write_text("# fr\nSeule.\n", encoding="utf-8")
    monkeypatch.setattr(lt, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"regles_redaction": "regles.md"})
    assert lt.regles("g") == "Seule.\n" and lt.regles("g", "en") == fa.REGLES


def test_un_fichier_declare_absent_arrete_la_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr(lt, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"regles_redaction": "absent.md"})
    with pytest.raises(FileNotFoundError):
        lt.regles("g")


@pytest.mark.parametrize("gabarit", [fa.REDACTION, fa.REECRITURE])
def test_les_gabarits_anglais_recoivent_les_regles(gabarit):
    assert "{regles}" in gabarit and fa.REGLES not in gabarit


@pytest.mark.parametrize("module", [fil, ff, fa])
def test_chaque_redacteur_lit_les_regles_du_graphe(module):
    src = open(module.__file__, encoding="utf-8").read()
    assert "regles=lt.regles(graphe" in src and "regles=lt.REGLES" not in src


def test_la_configuration_admet_le_champ():
    assert "regles_redaction" in open(cfg.__file__, encoding="utf-8").read()

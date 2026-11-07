"""Le conftest de Newskit.

La suite tourne dans un média jetable (newskit.donnees_temoin.media_de_test), sauf si NEWSKIT_DONNEES est posée.
"""

from __future__ import annotations

from newskit.donnees_temoin import media_de_test

media_de_test.accrocher(seulement_sans_media=False)


# --- les fixtures du moteur

"""Amorce commune des tests.

Les scripts vivent dans `scripts/` et s'importent par leur nom nu, comme ils
le font entre eux. On rend ce dossier importable une fois pour toutes, ici.

Aucun test ne touche au réseau ni au corpus réel : tout ce qui est éprouvé
l'est sur des fixtures, des fichiers temporaires ou des graphes construits à
la main. Ce qui demande le réseau est justement ce que les tests ne peuvent pas
garantir, et ce que les contrôles d'exécution de la routine surveillent.
"""


import sys
from pathlib import Path

import pytest

# prototype de la lettre, tests repris à la fin
collect_ignore = [
    "test_lettre.py",
    "test_lettre_edition.py",
    "test_lettre_liens.py",
    "test_lettre_nom_lisible.py",
    "test_lettre_rubriques.py",
    "test_lettre_une_minimum.py",
    "test_angle_critique.py",
    "test_angle_apporte_un_fait.py",
    "test_angle_formes.py",
    "test_chapeau_court.py",
    "test_titre_nomme_personne.py",
    "test_titre_une_ligne.py",
    "test_objet_concret.py",
    "test_revendique.py",
    "test_breves_dans_les_rubriques.py",
    "test_fiches_d_incident.py",
    "test_actualite_rubriques.py",
    "test_non_actualite.py",
    "test_eclairages_site.py",
    "test_eclairages_vps.py",
    "test_lettres_vps.py",
    "test_reporter.py",
]

RACINE = Path(__file__).resolve().parent

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def verrou_du_fil_isole(tmp_path_factory, monkeypatch):
    """Le verrou du fil vit dans un dossier temporaire : un test ne doit ni écrire dans `cache/`, ni attendre la fin
    d'un passage du fil que la collecte continue fait toutes les 5 minutes dans la copie de travail."""
    fil = sys.modules.get("newskit.fil")
    if fil is not None and hasattr(fil, "VERROUS"):
        monkeypatch.setattr(fil, "VERROUS", tmp_path_factory.mktemp("verrous"))


@pytest.fixture(autouse=True)
def liaison_du_fil_neutre(monkeypatch):
    """Un passage du fil ne lie pas les nouvelles dans un test qui ne l'éprouve pas : la liaison appelle le modèle.
    tests/test_fil_liaison.py reprend la vraie fonction."""
    fil = sys.modules.get("newskit.fil")
    if fil is not None and hasattr(fil, "a_lier"):
        monkeypatch.setattr(fil, "a_lier", lambda F: [])


@pytest.fixture(autouse=True)
def registre_des_couts_isole(tmp_path_factory, monkeypatch):
    """Le registre des coûts, la marque de limite et le résumé vivent dans un dossier temporaire, et le compteur de
    limites repart de zéro à chaque test : un test ne doit jamais écrire `cache/couts.jsonl` ni poser
    `cache/limite_atteinte`, qui arrêterait toutes les boucles de la nuit trois heures (audit du 22.10.2026)."""
    from newskit import couts
    d = tmp_path_factory.mktemp("couts")
    monkeypatch.setattr(couts, "REGISTRE", d / "couts.jsonl")
    monkeypatch.setattr(couts, "MARQUE", d / "limite_atteinte")
    monkeypatch.setattr(couts, "RESUME", d / "couts.json")
    monkeypatch.setattr(couts, "_consecutives", 0)
    monkeypatch.setattr(couts, "_medianes", {})
    monkeypatch.setattr(couts, "_courants", {})
    monkeypatch.setattr(couts, "_alertes", set())


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def fiche_texte() -> str:
    """Une fiche minimale, conforme au contrat, telle qu'on la trouve sur disque."""
    return (
        '---\n'
        'title: "Un titre"\n'
        'subtitle: "Un sous-titre"\n'
        'author: "Ada Lovelace, Charles Babbage"\n'
        'date: "2026-01-02"\n'
        'url: "https://example.org/p/un-titre"\n'
        'plateforme: "Substack"\n'
        'publication: "Une lettre"\n'
        'words: 7\n'
        'complet: true\n'
        'collected: "2026-01-03"\n'
        '---\n'
        '\n'
        '# Un titre\n'
        '\n'
        'Sept mots dans ce corps de fiche.\n'
    )


@pytest.fixture(autouse=True)
def selection_sans_modele(monkeypatch):
    """La WIP envoyée par un test n'appelle jamais le Sélectionneur réel (un appel au modèle) ; ses tests
    (test_selectionneur.py) remettent la vraie fonction."""
    from newskit import notifier_courriel
    monkeypatch.setattr(notifier_courriel, "selection", lambda L, graphe: [])


@pytest.fixture(autouse=True)
def corrections_isolees(tmp_path_factory, monkeypatch):
    """Le corpus des corrections du rédacteur en chef (docs/corrections_eclairages.jsonl) n'est jamais écrit par un
    test : chaque test a le sien, vide."""
    try:
        from newskit import exemples_redaction
    except ImportError:
        return
    monkeypatch.setattr(exemples_redaction, "FICHIER", tmp_path_factory.mktemp("corrections") / "corrections.jsonl")


@pytest.fixture(autouse=True)
def journal_des_types_isole(tmp_path_factory, monkeypatch):
    """Le journal des types de correction du relecteur n'est jamais écrit par un test."""
    from newskit import reporter
    monkeypatch.setattr(reporter, "JOURNAL_TYPES", tmp_path_factory.mktemp("types") / "relecture_types.jsonl",
                        raising=False)



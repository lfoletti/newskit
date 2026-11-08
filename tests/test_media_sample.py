"""Le dossier media_sample est un média complet, lu par le moteur (Lauro, 8.11.2026)."""

import json
import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SAMPLE = RACINE / "media_sample"
LIRE = """
import json
from newskit import config_sources as cfg, fil_fiche as ff, lettre as lt
g = "potagers-urbains"
print(json.dumps({
    "sources": len(cfg.charger()["sources"]),
    "graphe": cfg.graphe_declare(g)["libelle"],
    "fr": lt.regles(g),
    "en": lt.regles(g, "en"),
    "feuilles": sorted(ff.arbre(g)),
}))
"""


def lire():
    env = {**os.environ, "NEWSKIT_DONNEES": str(SAMPLE)}
    return json.loads(
        subprocess.run([sys.executable, "-c", LIRE], env=env, capture_output=True, text=True, check=True).stdout
    )


def test_le_moteur_lit_le_media_d_exemple():
    lu = lire()
    assert lu["sources"] == 1 and lu["graphe"] == "Potagers urbains"


def test_les_regles_du_fichier_servent_dans_les_deux_langues():
    lu = lire()
    assert "développe le fait du titre" in lu["fr"] and "develops the fact of the title" in lu["en"]


def test_l_arbre_des_categories_se_lit():
    assert "pratiques:sols" in lire()["feuilles"]


def test_l_exemple_entre_dans_l_archive_source():
    assert '"media_sample"' in (RACINE / "pyproject.toml").read_text(encoding="utf-8")

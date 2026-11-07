"""Le média des tests : un dossier neutre, posé avant tout import du moteur.

Les modules du moteur lisent leur registre à l'import, sous la racine des données (newskit.commun.RACINE). Les tests
tournent donc dans un média jetable : un graphe de potagers urbains et les fiches du témoin. Son graphe n'est pas
celui du témoin : pour le témoin, le média des tests est le réel.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import yaml

ICI = Path(__file__).resolve().parent
GRAPHE = "potagers-urbains"


def poser_le_media(d: Path) -> Path:
    fichiers = sorted(p.name for p in (ICI / "dossier").glob("*.md"))
    shutil.copytree(ICI / "dossier", d / "dossier_local", dirs_exist_ok=True)
    (d / "cache").mkdir(parents=True, exist_ok=True)
    config = {"version": 1, "sources": "sources.yaml",
              "media": {"nom": "banc-d-essai", "principal": GRAPHE, "graphe_du_site": GRAPHE,
                        "domaine_principal": "urban gardening", "launchd": "local.banc-d-essai", "langue": "fr"},
              "graphes": {GRAPHE: {"libelle": "Potagers urbains",
                                   "domaine": "urban gardening: allotments, soil, seed exchange"}}}
    sources = {"version": 1, "sources": [{"cle": "potagers_dossier", "plateforme": "Dossier local",
                                          "nom": "Carnets des potagers", "dossier": "potagers/dossier",
                                          "abonnement": "interne", "dense": True, "graphe": GRAPHE,
                                          "racine": "dossier_local", "fichiers": fichiers}]}
    for nom, v in (("config.yaml", config), ("sources.yaml", sources)):
        (d / nom).write_text(yaml.safe_dump(v, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return d


def accrocher(seulement_sans_media: bool) -> None:
    """Pose NEWSKIT_DONNEES sur un média jetable, sauf si elle l'est déjà ; avec `seulement_sans_media`, aussi sauf si
    le dossier courant est un média (le témoin lancé dans un média vrai le prend pour le réel)."""
    if os.environ.get("NEWSKIT_DONNEES"):
        return
    if seulement_sans_media and any((Path.cwd() / f).is_file() for f in ("config.yaml", "sources.yaml")):
        return
    os.environ["NEWSKIT_DONNEES"] = str(poser_le_media(Path(tempfile.mkdtemp(prefix="newskit-media-"))))

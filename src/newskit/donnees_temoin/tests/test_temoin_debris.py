"""Le témoin absurde, anticorps 4 sur 10 : le graphe composé sans débris (CHANTIER_GENERIQUE.md § 4.3,
test_graphe_compose_sans_debris).

Un premier passage rejoue la routine entière. Puis deux débris sont semés : une fiche retirée (son original et sa
copie collectée ; son lot de couche 2 reste), un intertitre en gras ajouté à une autre fiche. Le second passage,
marque de limite posée (rien ne redemande le modèle), recompose : aucune arête ne cite une fiche absente, rien ne
nomme la fiche retirée, aucun libellé ne garde son balisage. Défaut trouvé le 06.11.2026 : le lot d'une fiche retirée
revenait dans le graphe (fusionner_graphe.lot_orphelin ne lisait pas les chemins absolus, voir test_lot_orphelin.py).
Reste hors du témoin : soumettre les paires « X (…) » / « X » au juge (un appel au modèle).
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from newskit import temoin  # noqa: E402

RETIREE = "vol-de-ruche"


def _passe(t):
    return subprocess.run(
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


@pytest.fixture(scope="module")
def recompose(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("debris") / "t")
    r1 = _passe(t)
    assert r1.returncode == 0, r1.stdout[-1500:]
    retirees = list(t.donnees.rglob(f"{RETIREE}*.md"))
    assert len(retirees) == 2  # l'original du dossier et sa copie collectée
    for p in retirees:
        p.unlink()
    y = next((t.donnees / "sources" / "ruchers" / "dossier").glob("marque-des-ruches*.md"))
    y.write_text(
        y.read_text(encoding="utf-8") + "\n## **Le registre des marques**\n\nIl compte 63 marques.\n", encoding="utf-8"
    )
    (t.donnees / "cache" / "limite_atteinte").write_text(
        time.strftime("%Y-%m-%d %H:%M:%S") + " témoin\n", encoding="utf-8"
    )
    r2 = _passe(t)
    g = json.load(gzip.open(t.donnees / "graphe" / "graphe.json.gz"))
    return t, r2, g


def test_aucune_arete_ne_cite_une_fiche_absente(recompose):
    t, r, g = recompose
    assert r.returncode == 0, r.stdout[-1500:]
    absentes = [
        l["source_file"] for l in g["links"] if l.get("source_file") and not (t.donnees / l["source_file"]).exists()
    ]
    assert absentes == []


def test_rien_ne_nomme_la_fiche_retiree(recompose):
    _, _, g = recompose
    assert [x.get("label") for x in g["nodes"] if RETIREE in json.dumps(x, ensure_ascii=False)] == []


def test_aucun_libelle_de_balisage(recompose):
    _, _, g = recompose
    balises = [x["label"] for x in g["nodes"] if re.search(r"\*\*|__|`|^\s*#|^\W*$", x.get("label") or "")]
    assert balises == []
    assert any(x.get("label") == "Le registre des marques" for x in g["nodes"])  # nettoyé, pas perdu

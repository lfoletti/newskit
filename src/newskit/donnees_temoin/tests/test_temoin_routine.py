"""Le témoin absurde, tranche 2 : la routine entière dans le témoin (NEWSKIT.md, N4).

La marque de limite posée dans les données du témoin, toutes les étapes au modèle sont sautées : la routine collecte
le dossier local, écrit la couche 1, compose le graphe, sa carte et son bilan, sans un appel au modèle, sans une
écriture vers le réel (canari vide), sans toucher au vrai registre des coûts ni à la vraie marque. Premier défaut
trouvé le 06.11.2026 : sur des données neuves, la routine mourait en écrivant graphe/sources_muettes.json, faute du
dossier de son graphe.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from newskit import commun  # noqa: E402
from newskit import temoin  # noqa: E402



@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    avant = temoin.photo_du_reel()
    t = temoin.preparer(tmp_path_factory.mktemp("temoin") / "t")
    (t.donnees / "cache" / "limite_atteinte").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " témoin\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(t.code / "newskit" / "routine_hebdo.py"), "--graphe", temoin.GRAPHE,
                        "--sans-commit", "--sans-courriel"], env=temoin.environnement(t), cwd=t.donnees,
                       capture_output=True, text=True, timeout=900)
    return t, r, avant


def test_la_routine_passe_sans_erreur(passage):
    t, r, _ = passage
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])


def test_la_routine_n_ecrit_pas_vers_le_reel(passage):
    t, r, avant = passage
    journal = t.journal.read_text(encoding="utf-8").splitlines() if t.journal.exists() else []
    assert [json.loads(l) for l in journal] == []
    assert temoin.fuites_vers_le_reel(avant) == []


def test_la_routine_collecte_compose_et_dresse_la_carte(passage):
    t, r, _ = passage
    sortie = r.stdout
    import re
    n = len(list(temoin.TEMOIN.joinpath("dossier").glob("*.md")))
    m = re.search(r"Carnets des ruchers : (\d+) document\(s\), (\d+) section\(s\) → (\d+) fiche\(s\) neuve\(s\)", sortie)
    assert m and int(m[1]) == n and int(m[2]) == int(m[3]) >= n, sortie[-1500:]
    for etape in ("couche 1 du graphe", "structure des titres", "composition", "carte du domaine"):
        assert f"ok      {etape}" in sortie, etape
    assert (t.donnees / "graphe" / "graphe.json.gz").exists() and (t.donnees / "graphe" / "carte.md").exists()
    assert len(list((t.donnees / "sources" / "ruchers" / "dossier").glob("*.md"))) == int(m[3])


def test_les_etapes_au_modele_sont_sautees_et_rien_n_est_paye(passage):
    t, r, _ = passage
    for etape in ("canon", "éclaireur", "couche 2 incrémentale", "thèmes émergents"):
        assert f"sauté   {etape} : limite d'usage atteinte" in r.stdout, etape
    assert "coût du passage : aucun appel au modèle consigné" in r.stdout
    assert not t.manquantes.exists()                    # le faux claude n'a pas été appelé

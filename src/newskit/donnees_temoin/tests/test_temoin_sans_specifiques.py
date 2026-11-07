"""Le témoin absurde, anticorps 5 sur 10 : aucun nom réel ni spécifique (CHANTIER_GENERIQUE.md § 4.3,
test_aucun_nom_reel_ni_specifique), et la seconde passe du témoin.

Les modules propres à l'édition (collecte_textes, collecte_arxiv, depots) sont retirés de la copie du moteur.
diachronie reste au moteur : la datation des attestations et l'actualité d'une notion sont génériques, seule sa
veille des versions lit collecte_textes. Sans eux, la routine rejouée reste verte : une étape dont le module manque est « sautée », pas en échec, et la couche 2
s'écrit. Rien de ce que le témoin écrit (données, maison) ne nomme un graphe réel, l'AI Act, EUR-Lex, Fedlex, arxiv.org
ni l'édition. Défaut trouvé le 06.11.2026 : la routine importait collecte_textes sans condition et mourait.
"""

from __future__ import annotations

import gzip
import re
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import commun  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import temoin  # noqa: E402

SPECIFIQUES = ("collecte_textes", "collecte_arxiv", "depots")


@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("sans") / "t")
    for m in SPECIFIQUES:
        (t.code / "newskit" / f"{m}.py").unlink()
    r = subprocess.run(
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
    return t, r


def test_la_routine_reste_verte_sans_les_modules_specifiques(passage):
    t, r = passage
    assert r.returncode == 0 and "Traceback" not in r.stdout + r.stderr, (r.stdout[-1500:], r.stderr[-1500:])
    assert "ok      couche 2 incrémentale" in r.stdout and not t.manquantes.exists()


def test_une_etape_sans_module_est_sautee_pas_en_echec(passage):
    _, r = passage
    for etape in ("liens déposés", "arXiv, papiers cités"):
        assert f"sauté   {etape} : module absent" in r.stdout, etape
        assert f"{etape} : code" not in r.stdout, etape


def test_rien_d_ecrit_ne_nomme_le_reel(passage):
    t, _ = passage
    reels = sorted(cfg.charger(Path(commun.RACINE) / "sources.yaml")["graphes"])
    mots = [*reels, "AI Act", "EUR-Lex", "eur-lex", "Fedlex", "fedlex", "arxiv.org", cfg.edition()["nom"]]
    motif = re.compile("|".join(re.escape(m) for m in mots))
    trouves = []
    for racine in (t.donnees, t.maison):
        for p in racine.rglob("*"):
            if p.is_file() and "cortexdb" not in p.parts:
                b = gzip.open(p).read() if p.suffix == ".gz" else p.read_bytes()
                trouves += [
                    (str(p.relative_to(t.dossier)), m.group()) for m in motif.finditer(b.decode("utf-8", "ignore"))
                ]
    assert trouves == []

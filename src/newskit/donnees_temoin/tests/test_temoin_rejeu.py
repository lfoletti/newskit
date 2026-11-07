"""Le témoin absurde, tranche 3 : la routine rejouée sans marque de limite (NEWSKIT.md, N4).

Sans la marque, les étapes au modèle tournent ; le faux claude rejoue les réponses enregistrées le 06.11.2026
(36 extractions de la couche 2, 2,86 $). Aucune consigne ne manque, la couche 2 est écrite, rien ne fuit vers le réel.
Une consigne changée (code ou fiche) fait tomber ce test : `temoin.py reenregistrer` paie les seules manquantes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import commun  # noqa: E402
from newskit import temoin  # noqa: E402


@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    avant = temoin.photo_du_reel()
    t = temoin.preparer(tmp_path_factory.mktemp("rejeu") / "t")
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
    return t, r, avant


def test_aucune_consigne_ne_manque(passage):
    t, r, _ = passage
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    manquantes = sorted(t.manquantes.glob("*.json")) if t.manquantes.exists() else []
    assert [json.loads(m.read_text(encoding="utf-8"))["consigne"][:80] for m in manquantes] == []


def test_la_couche_2_est_ecrite_depuis_les_reponses(passage):
    t, r, _ = passage
    assert "ok      couche 2 incrémentale" in r.stdout
    assert "ok      couche 2 à jour sur les fiches denses" in r.stdout


def test_le_rejeu_ne_touche_pas_au_reel(passage):
    t, r, avant = passage
    assert not t.journal.exists() or t.journal.read_text(encoding="utf-8") == ""
    assert temoin.fuites_vers_le_reel(avant) == []


# Anticorps 2 (CHANTIER_GENERIQUE.md § 4.3) : `claude -p` a un point de passage unique et un contexte borné.
# Incidents : 75 259 jetons de contexte pour « ok » quand un plugin de la machine se chargeait (recueil, Consommation,
# 6 bis, 22.10.2026) ; des appels hors du registre des coûts. Le faux claude du témoin journalise chaque appel.
CONSIGNE_MAX = 20_000  # caractères ; l'extraction de la couche 2 en prend environ 4 000 par fiche


def _appels(t):
    p = t.dossier / "appels.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_claude_p_point_unique_et_contexte_borne(passage):
    t, r, _ = passage
    appels = _appels(t)
    assert appels, "le faux claude n'a rien journalisé"
    for a in appels:
        argv = a["argv"]
        assert argv[:2] == ["-p", "--settings"], argv[:2]  # couts.CLAUDE_P en tête
        reglages = json.loads(argv[2])
        assert reglages["enabledPlugins"] == {"cortexdb@cortexdb": False, "thalamus@thalamus": False}
        assert "--tools" in argv and "--model" in argv and "--no-session-persistence" in argv
        assert a["taille"] < CONSIGNE_MAX, (a["cle"], a["taille"])
    registre = t.donnees / "cache" / "couts.jsonl"
    lignes = [json.loads(l) for l in registre.read_text(encoding="utf-8").splitlines()] if registre.exists() else []
    assert len(lignes) == len(appels) and all(l.get("poste") for l in lignes)  # une ligne au registre par appel

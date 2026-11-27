"""Le témoin absurde, anticorps 8 et 9 sur 10 : les alias (CHANTIER_GENERIQUE.md § 4.3, test_alias_jamais_dans_le_vide,
test_aussi_lu_par_toute_lecture).

Après un rejeu complet, une table d'alias est semée : un alias valide (A fondu dans B), une chaîne (C → A, A étant
lui-même fondu dans B), un alias dont la cible n'existe pas. Le second passage, sous marque de limite, fond A et C
dans B au cerveau, compte et dit l'alias sans cible ; puis le nom de A, gardé parmi les autres noms de B, mène à B par
toutes les lectures du graphe (`resoudre`, qui sert voisinage, expliquer et sources, comme `chercher`). Défauts trouvés le 06.11.2026 : un
alias sans cible ou en chaîne était sauté en silence ; `chercher` ne lisait pas `aussi`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from newskit import graphe_cerveau as gc  # noqa: E402
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import temoin  # noqa: E402


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


def _graphe(t):
    """Le cerveau du témoin, sous les anciens identifiants que la table d'alias nomme (K3 : plus de graphe composé)."""
    G = gc.sous_anciennes_cles(gc.lire(t.donnees / "cache" / "cortexdb" / f"{temoin.GRAPHE}.db"))
    return [
        {"id": n, "label": d.get("name") or "", "kind": d["node_type"], "aussi": ig.autres_noms(G, n)}
        for n, d in G.nodes(data=True)
    ]


@pytest.fixture(scope="module")
def recompose(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("alias") / "t")
    assert _passe(t).returncode == 0
    noeuds = _graphe(t)
    libelles = [x["label"].casefold() for x in noeuds]
    # Des notions dont le libellé n'est contenu dans aucun autre : seul `aussi` peut mener de A à B.
    concepts = sorted(
        (
            x
            for x in noeuds
            if x["kind"] == "concept"
            and x["id"].startswith("concept_")
            and sum(x["label"].casefold() in l for l in libelles) == 1
        ),
        key=lambda x: x["id"],
    )
    a, b, c = concepts[0], concepts[1], concepts[2]
    table = {
        "alias": {
            a["id"]: b["id"],
            c["id"]: a["id"],
            "notion_qui_n_existe_pas": b["id"],
            concepts[3]["id"]: "cible_qui_n_existe_pas",
        },
        # les libellés, comme fondre_doublons les écrit : la fusion du cerveau nomme ses entités par eux
        "libelles": {x["id"]: x["label"] for x in concepts[:4]},
    }
    (t.donnees / "graphe" / "alias.json").write_text(json.dumps(table), encoding="utf-8")
    (t.donnees / "cache" / "limite_atteinte").write_text(
        time.strftime("%Y-%m-%d %H:%M:%S") + " témoin\n", encoding="utf-8"
    )
    r = _passe(t)
    return t, r, _graphe(t), a, b, c, concepts[3]


def _lire(t, *args):
    return subprocess.run(
        [sys.executable, str(t.code / "newskit" / "interroger_graphe.py"), *args],
        env=temoin.environnement(t),
        cwd=t.donnees,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_l_alias_et_la_chaine_sont_fondus_dans_la_cible(recompose):
    _, r, g, a, b, c, _ = recompose
    assert r.returncode == 0, r.stdout[-1500:]
    ids = {x["id"] for x in g}
    assert a["id"] not in ids and c["id"] not in ids and b["id"] in ids
    aussi = next(x for x in g if x["id"] == b["id"])["aussi"]
    assert a["label"] in aussi and c["label"] in aussi


def test_l_alias_sans_cible_est_compte_et_dit(recompose):
    _, r, g, *_, d = recompose
    assert d["id"] in {x["id"] for x in g}  # rien n'est fondu dans le vide
    assert "1 alias sans cible" in r.stdout, r.stdout[-2500:]


@pytest.mark.parametrize("commande", ["chercher", "voisinage", "expliquer", "sources"])
def test_le_libelle_fondu_mene_a_la_cible_par_toute_lecture(recompose, commande):
    t, _, _, a, b, *_ = recompose
    r = _lire(t, "--sans-modele", commande, a["label"])
    assert r.returncode == 0, r.stderr[-800:]
    assert b["label"][:40] in r.stdout or b["id"][:40] in r.stdout, (commande, r.stdout[:800])

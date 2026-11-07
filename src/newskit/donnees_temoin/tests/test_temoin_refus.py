"""Le témoin absurde, anticorps 6 sur 10 : un refus n'est pas une absence (CHANTIER_GENERIQUE.md § 4.3,
test_refus_n_est_pas_absence ; règle : un refus de collecte n'est pas une absence).

Le registre du témoin porte une source par collecteur réseau (flux, page de liste, plan de site, revue, OpenAlex,
GitHub, HuggingFace), sur des hôtes `.invalid` ; le canari coupe le réseau. Chaque collecte sort en échec et dit le
refus de chaque source ; aucune ne se résume par « 0 fiche neuve » seule ; le relevé de santé nomme la source
injoignable. Les pauses entre tentatives sont annulées (TEMOIN_SANS_PAUSE). Incident : des collectes refusées
comptées « 0 nouvelle », des sources jugées muettes alors qu'elles étaient injoignables.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import temoin  # noqa: E402

COLLECTES = {
    "flux RSS": ("Gazette des ruchers", "Archives de Hohenruche", "Annales de Valmiel"),
    "revues (Crossref)": ("Revue d'apiculture médiévale",),
    "OpenAlex": ("OpenAlex, essaimage",),
    "GitHub et HuggingFace": ("GitHub, ruchers", "HuggingFace, ruchers"),
}


@pytest.fixture(scope="module")
def passage(tmp_path_factory):
    t = temoin.preparer(tmp_path_factory.mktemp("refus") / "t", reseau=True)
    env = temoin.environnement(t)
    env["TEMOIN_SANS_PAUSE"] = "1"
    r = subprocess.run(
        [
            sys.executable,
            str(t.code / "newskit" / "routine_hebdo.py"),
            "--graphe",
            temoin.GRAPHE,
            "--sans-commit",
            "--sans-courriel",
        ],
        env=env,
        cwd=t.donnees,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return t, r


def _bloc(sortie: str, etape: str) -> str:
    """Les lignes de l'étape, jusqu'à l'étape suivante."""
    m = re.search(
        rf"^(?:  ok      {re.escape(etape)} |{re.escape(etape)} : code \d+).*?(?=^\S|^  ok |^  sauté |\Z)",
        sortie,
        re.M | re.S,
    )
    return m.group(0) if m else ""


@pytest.mark.parametrize("etape", COLLECTES)
def test_chaque_collecte_refusee_sort_en_echec_et_nomme_ses_sources(passage, etape):
    _, r = passage
    bloc = _bloc(r.stdout, etape)
    assert bloc.startswith(f"{etape} : code "), (etape, bloc[:300], r.stdout[-2000:])
    assert "réseau coupé" in bloc or "injoignable" in bloc or "interrompue" in bloc
    for source in COLLECTES[etape]:
        assert source in bloc, (etape, source)


def test_la_sante_des_sources_nomme_l_injoignable(passage):
    _, r = passage
    bloc = _bloc(r.stdout, "santé des sources")
    assert re.search(r"Gazette des ruchers\s+NON", bloc), bloc


def test_le_dossier_local_passe_quand_meme(passage):
    _, r = passage
    assert r.returncode == 0 and "ok      dossier local" in r.stdout

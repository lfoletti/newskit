"""Les doublons d'identité déjà sur le disque (CHANTIER_GENERIQUE.md § 4.2) : `doublons_d_identite.py --etat` les
compte, `--fondre` garde une fiche par document et retire les autres.

La fiche gardée est lue par tous les graphes qui lisaient les autres (sinon un graphe perdrait le document), puis
déjà extraite (son lot de couche 2 reste valable), puis la plus ancienne. Un groupe sans telle fiche n'est pas fondu :
il est dit, à trancher. Sur l'arbre réel, aucun graphe ne garde deux fiches d'une même clé (rouge le 06.11.2026 avant
la fusion : 227 clés).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from newskit import doublons_d_identite as dd  # noqa: E402
from newskit import fiche  # noqa: E402

GRAPHES = {"a": ["sources/a"], "large": ["sources/large", "sources/a"], "b": ["sources/b"]}


@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: GRAPHES)
    extraites: set[str] = set()
    monkeypatch.setattr(dd, "extraites", lambda: extraites)
    fiche.oublier_index()
    yield tmp_path, extraites
    fiche.oublier_index()


def _poser(racine: Path, rel: str, **champs) -> Path:
    c = {"title": "T", "subtitle": "", "author": "", "date": "2024-01-01", "url": "", "publication": "p",
         "collected": "2026-11-06", **champs}
    p = racine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fiche.rendre(fiche.normaliser(c, "# T\n\ncorps"), "# T\n\ncorps"), encoding="utf-8")
    return p


def test_la_plus_ancienne_est_gardee(racine):
    r, _ = racine
    _poser(r, "sources/a/x.md", url="https://e.org/1", date="2024-05-01")
    _poser(r, "sources/a/y.md", url="https://e.org/1/", date="2024-02-01")
    (g,) = dd.etat()
    assert (g.garde, g.retirees) == ("sources/a/y.md", ["sources/a/x.md"])


def test_une_fiche_extraite_l_emporte_sur_une_plus_ancienne(racine):
    r, extraites = racine
    _poser(r, "sources/a/x.md", url="https://e.org/1", date="2024-05-01")
    _poser(r, "sources/a/y.md", url="https://e.org/1", date="2024-02-01")
    extraites.add("sources/a/x.md")
    (g,) = dd.etat()
    assert g.garde == "sources/a/x.md"


def test_la_gardee_couvre_tous_les_graphes(racine):
    """y (sources/a) est lue par a et large ; x (sources/large) par large seul : retirer y ôterait le document à a."""
    r, extraites = racine
    _poser(r, "sources/large/x.md", doi="10.1/X", date="2020-01-01")
    _poser(r, "sources/a/y.md", url="https://doi.org/10.1/x", date="2024-01-01")
    extraites.add("sources/large/x.md")
    (g,) = dd.etat()
    assert (g.garde, g.retirees) == ("sources/a/y.md", ["sources/large/x.md"])


def test_un_groupe_sans_fiche_qui_couvre_tout_est_a_trancher(racine, monkeypatch):
    r, _ = racine
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: {"a": ["sources/a"], "b": ["sources/b"],
                                                               "ab": ["sources/a", "sources/b"]})
    fiche.oublier_index()
    _poser(r, "sources/a/x.md", url="https://e.org/1")
    _poser(r, "sources/b/y.md", url="https://e.org/1")
    (g,) = dd.etat()
    assert g.garde is None and g.retirees == []
    assert dd.fondre() == ([], [g]) and (r / "sources/a/x.md").exists() and (r / "sources/b/y.md").exists()


def test_deux_graphes_sans_lien_ne_font_pas_un_doublon(racine):
    r, _ = racine
    _poser(r, "sources/a/x.md", url="https://e.org/1")
    _poser(r, "sources/b/y.md", url="https://e.org/1")
    assert dd.etat() == []


def test_un_groupe_se_forme_par_des_cles_differentes(racine):
    """x et y partagent l'adresse, y et z le DOI : un seul document, une seule fiche gardée."""
    r, _ = racine
    _poser(r, "sources/a/x.md", url="https://e.org/1", date="2024-03-01")
    _poser(r, "sources/a/y.md", url="https://e.org/1", doi="10.1/a", date="2024-01-01")
    _poser(r, "sources/a/z.md", doi="10.1/A", date="2024-02-01")
    (g,) = dd.etat()
    assert (g.garde, sorted(g.retirees)) == ("sources/a/y.md", ["sources/a/x.md", "sources/a/z.md"])


def test_les_parties_d_un_texte_ne_sont_pas_des_doublons(racine):
    r, _ = racine
    _poser(r, "sources/a/art-1.md", url="https://e.org/loi.pdf", partie="art-001")
    _poser(r, "sources/a/art-2.md", url="https://e.org/loi.pdf", partie="art-002")
    assert dd.etat() == []


def test_fondre_retire_les_doublons_et_l_etat_devient_vide(racine):
    r, _ = racine
    _poser(r, "sources/a/x.md", url="https://e.org/1", date="2024-05-01")
    _poser(r, "sources/a/y.md", url="https://e.org/1", date="2024-02-01")
    retirees, a_trancher = dd.fondre()
    assert (retirees, a_trancher) == (["sources/a/x.md"], [])
    assert not (r / "sources/a/x.md").exists() and (r / "sources/a/y.md").exists()
    fiche.oublier_index()
    assert dd.etat() == []


@pytest.mark.skipif(not (Path(fiche.racine_des_donnees()) / "sources").is_dir(), reason="pas de données")
def test_aval_aucun_graphe_reel_n_a_deux_fiches_d_un_meme_document():
    fiche.oublier_index()
    groupes = dd.etat()
    assert not groupes, f"{len(groupes)} groupe(s) de doublons, dont : " + "; ".join(
        f"{g.garde or 'à trancher'} = {', '.join(g.retirees or g.fiches)}" for g in groupes[:3])

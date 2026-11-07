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


def test_deux_versions_de_langue_gardent_la_francaise_sinon_l_anglaise(racine):
    # Mainteneur, 07.11.2026 : deux versions de langues différentes du même article ne s'extraient pas en parallèle ni ne
    # font deux clusters. La langue l'emporte sur l'extraction faite et sur l'ancienneté.
    r, extraites = racine
    _poser(r, "sources/a/ko.md", url="https://blog.x/blog/ramp/ko/", date="2024-01-01")
    _poser(r, "sources/a/en.md", url="https://blog.x/blog/ramp/en/", date="2024-05-01")
    extraites.add("sources/a/ko.md")
    (g,) = dd.etat()
    assert (g.garde, g.retirees) == ("sources/a/en.md", ["sources/a/ko.md"])
    _poser(r, "sources/a/fr.md", url="https://blog.x/blog/ramp/fr/", date="2024-06-01")
    fiche.oublier_index()
    (g,) = dd.etat()
    assert g.garde == "sources/a/fr.md"


def test_la_version_par_defaut_passe_avant_une_autre_langue(racine):
    r, extraites = racine
    _poser(r, "sources/a/zh.md", url="https://metr.org/zh-Hans/blog/x", date="2024-01-01")
    _poser(r, "sources/a/defaut.md", url="https://metr.org/blog/x", date="2024-05-01")
    extraites.add("sources/a/zh.md")
    (g,) = dd.etat()
    assert (g.garde, g.retirees) == ("sources/a/defaut.md", ["sources/a/zh.md"])


def test_un_texte_de_reference_dans_deux_langues_n_est_pas_un_doublon(racine):
    r, _ = racine
    _poser(r, "sources/a/textes/loi-en/art_1.md", url="https://lex.x/eli/1/en#art_1")
    _poser(r, "sources/a/textes/loi-fr/art_1.md", url="https://lex.x/eli/1/fr#art_1")
    assert dd.etat() == []


def test_le_manifeste_d_un_flux_perd_les_entrees_dont_la_fiche_porte_un_autre_article(racine):
    # 07.11.2026 : trois articles anglais du même jour s'écrivaient dans 2026-11-07_en.md, chacun écrasant le précédent.
    import json
    r, _ = racine
    d = r / "sources" / "a" / "rss" / "blog"
    _poser(r, "sources/a/rss/blog/2026-11-07_en.md", url="https://blog.x/blog/ramp/en/")
    man = {"https://blog.x/blog/frontier/en/": {"file": "2026-11-07_en.md"},
           "https://blog.x/blog/ramp/en/": {"file": "2026-11-07_en.md"},
           "https://blog.x/blog/parti/en/": {"file": "2026-11-06_en.md"},
           "https://blog.x/blog/parti-aussi/en/": {"file": "2026-11-06_en.md"},     # fiche partie, partagée : restent
           "https://blog.x/vieux-nom": {"file": "2026-11-05_renomme.md"},         # seule sur sa fiche : une redirection
           "http://blog.x//blog/double/": {"file": "2026-11-04_double.md"},         # la même adresse, autrement écrite
           "https://blog.x/blog/double-bis": {"file": "2026-11-04_double.md"},
           "https://noyb.x/en/plan-ue": {"file": "2026-09-21_plan-ue.md"},              # /index.php/en/… : la même page
           "https://ancien.x/posts/rwa": {"file": "2026-09-21_plan-ue.md"},             # domaine changé : un autre article ici
           "https://noyb.x/de/plan-ue": {"file": "2026-09-21_plan-ue.md"}}              # une autre langue : version
    _poser(r, "sources/a/rss/blog/2026-11-05_renomme.md", url="https://blog.x/nouveau-nom")
    _poser(r, "sources/a/rss/blog/2026-11-04_double.md", url="https://blog.x/blog/double")
    _poser(r, "sources/a/rss/blog/2026-09-21_plan-ue.md", url="https://noyb.x/index.php/en/plan-ue")
    (d / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    _poser(r, "sources/a/bibliotheque/module.md", url="https://cours.x/module")     # hors flux : un fichier, plusieurs adresses
    (r / "sources/a/bibliotheque/_manifest.json").write_text(json.dumps({"https://cours.x/module/p2": {"file": "module.md"},
                                                                         "https://cours.x/module/p3": {"file": "module.md"}}),
                                                             encoding="utf-8")
    assert dd.reparer_manifestes(r / "sources", plan=True) == 4
    assert len(json.loads((d / "_manifest.json").read_text(encoding="utf-8"))) == 10           # le plan ne change rien
    assert dd.reparer_manifestes(r / "sources") == 4
    assert sorted(json.loads((d / "_manifest.json").read_text(encoding="utf-8"))) == [
        "http://blog.x//blog/double/", "https://blog.x/blog/parti-aussi/en/", "https://blog.x/blog/parti/en/", "https://blog.x/blog/ramp/en/",
        "https://blog.x/vieux-nom", "https://noyb.x/en/plan-ue"]                  # une fiche partie n'est pas recollectée
    assert list(json.loads((r / "sources/a/bibliotheque/_manifest.json").read_text(encoding="utf-8"))) == [
        "https://cours.x/module/p2", "https://cours.x/module/p3"]

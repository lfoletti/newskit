"""Une source prolixe déclare sa série au registre : `serie: {theme, heure}`. Ses fiches du thème ne font plus chacune
un billet ; le fil en écrit un seul par jour, à l'heure dite. Ici, la clé du registre seule."""

import copy

import pytest

from newskit import config_sources as cfg

SERIE = {"theme": "commentaire d'un règlement, article par article", "heure": "15:00"}


def _registre(serie=None):
    """Le registre du média d'essai, avec un flux RSS qui déclare (ou non) une série."""
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = d["sources"][0]["graphe"]
    s = {
        "cle": "gazette",
        "plateforme": "Flux RSS",
        "nom": "Gazette",
        "dossier": "rss/gazette",
        "abonnement": "gratuit",
        "graphe": g,
        "flux": "https://gazette.example/rss",
    }
    if serie is not None:
        s["serie"] = serie
    d["sources"].append(s)
    return d


def _gazette(d):
    return next(s for s in cfg._valider(d, cfg.FICHIER)["sources"] if s.cle == "gazette")


def test_une_source_n_a_pas_de_serie_sauf_mention():
    assert _gazette(_registre()).serie == {}
    assert cfg.Source(cle="c", plateforme="Flux RSS", nom="c", dossier="rss/c", abonnement="gratuit").serie == {}


def test_le_registre_admet_une_serie():
    assert _gazette(_registre(SERIE)).serie == SERIE


@pytest.mark.parametrize(
    ("serie", "motif"),
    [
        ("commentaire", "bloc"),
        ({"heure": "15:00"}, "theme"),
        ({"theme": "  ", "heure": "15:00"}, "theme"),
        ({"theme": 3, "heure": "15:00"}, "theme"),
        ({"theme": "t"}, "heure"),
        ({"theme": "t", "heure": 900}, "guillemets"),  # 15:00 sans guillemets : YAML lit l'entier 900
        ({"theme": "t", "heure": "25:00"}, "heure"),
        ({"theme": "t", "heure": "15h"}, "heure"),
        ({"theme": "t", "heure": "15:00", "jour": "lundi"}, "n'admet que"),
    ],
)
def test_une_serie_mal_formee_est_refusee(serie, motif):
    with pytest.raises(cfg.ConfigInvalide, match=f"serie.*{motif}"):
        cfg._valider(_registre(serie), cfg.FICHIER)


def test_la_serie_d_une_fiche_se_lit_par_son_chemin(monkeypatch):
    avec = cfg.Source(cle="a", plateforme="Flux RSS", nom="A", dossier="rss/a", abonnement="gratuit", serie=SERIE)
    sans = cfg.Source(cle="b", plateforme="Flux RSS", nom="B", dossier="rss/b", abonnement="gratuit")
    monkeypatch.setattr(cfg, "toutes", lambda *a, **k: [avec, sans])
    assert cfg.serie_du_chemin("sources/rss/a/2026-11-19_x.md") is avec
    assert cfg.serie_du_chemin("sources/rss/b/2026-11-19_x.md") is None
    assert cfg.serie_du_chemin("sources/ailleurs/x.md") is None

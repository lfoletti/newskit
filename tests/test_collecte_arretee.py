"""Une source dont la collecte est arrêtée (`collecte: false` au registre) reste déclarée : son dossier, ses fiches et
ce qui en est paru restent, plus rien de neuf n'y entre. Retirer le bloc était la seule voie ; elle défait le dossier
du graphe, et l'éclaireur redéclare le domaine quand il le retrouve."""

import copy
import datetime as dt

import pytest

from newskit import collecte_rss as rss
from newskit import config_sources as cfg
from newskit import routine_hebdo as rh


def _registre():
    """Le registre du média d'essai, avec un flux RSS en plus de sa source « Dossier local »."""
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = d["sources"][0]["graphe"]
    d["sources"].append(
        {
            "cle": "gazette",
            "plateforme": "Flux RSS",
            "nom": "Gazette",
            "dossier": "rss/gazette",
            "abonnement": "gratuit",
            "graphe": g,
            "flux": "https://gazette.example/rss",
        }
    )
    return d


def _source(d, collectee_ici: bool):
    return next(s for s in d["sources"] if (s["plateforme"] in cfg.COLLECTE_ARRETABLE) is collectee_ici)


def _charge(d):
    return {s.cle: s for s in cfg._valider(d, cfg.FICHIER)["sources"]}


def test_une_source_se_collecte_sauf_mention():
    d = _registre()
    s = _source(d, True)
    assert _charge(d)[s["cle"]].collecte is True
    nue = cfg.Source(cle="c", plateforme="Flux RSS", nom="c", dossier="rss/c", abonnement="gratuit")
    assert nue.collecte is True  # une source bâtie sans la clé (bancs, modules tiers) se collecte


def test_le_registre_admet_une_collecte_arretee():
    d = _registre()
    s = _source(d, True)
    s["collecte"] = False
    assert _charge(d)[s["cle"]].collecte is False


@pytest.mark.parametrize("valeur", ["non", 0, None, "false"])
def test_une_collecte_qui_n_est_ni_vraie_ni_fausse_est_refusee(valeur):
    d = _registre()
    _source(d, True)["collecte"] = valeur
    with pytest.raises(cfg.ConfigInvalide, match="collecte"):
        cfg._valider(d, cfg.FICHIER)


def test_une_plateforme_qui_ne_lit_pas_la_cle_la_refuse():
    d = _registre()
    s = _source(d, False)
    s["collecte"] = False
    with pytest.raises(cfg.ConfigInvalide, match=f"collecte.*{s['plateforme']}"):
        cfg._valider(d, cfg.FICHIER)


def _sources(monkeypatch, *sources):
    monkeypatch.setattr(
        cfg,
        "toutes",
        lambda plateforme=None, graphe=None: [
            s for s in sources if (not plateforme or s.plateforme == plateforme) and (not graphe or s.graphe == graphe)
        ],
    )


def _src(cle, collecte=True, plateforme="Flux RSS"):
    return cfg.Source(
        cle=cle,
        plateforme=plateforme,
        nom=cle,
        dossier=f"rss/{cle}",
        abonnement="gratuit",
        graphe="g",
        collecte=collecte,
    )


def test_le_collecteur_saute_une_source_arretee(monkeypatch):
    _sources(monkeypatch, _src("suivie"), _src("arretee", collecte=False), _src("plan", plateforme="Plan de site"))
    assert [s.cle for s in rss.sources_a_collecter("g")] == ["suivie", "plan"]


def test_le_collecteur_ne_prend_pas_une_source_arretee_meme_nommee(monkeypatch):
    _sources(monkeypatch, _src("suivie"), _src("arretee", collecte=False))
    assert rss.sources_a_collecter("g", "arretee") == []
    assert [s.cle for s in rss.sources_a_collecter("g", "suivie")] == ["suivie"]


def test_une_source_arretee_n_est_pas_une_source_muette(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "SOURCES", tmp_path)
    _sources(monkeypatch, _src("silencieuse"), _src("arretee", collecte=False))
    jadis = lambda s: dt.date(2026, 1, 1)  # noqa: E731
    assert [s.cle for s in rh.sources_muettes("g", dt.date(2026, 11, 19), declaree_le=jadis)] == ["silencieuse"]


def test_la_commande_ne_lit_pas_le_flux_d_une_source_arretee(monkeypatch, capsys):
    _sources(monkeypatch, _src("arretee", collecte=False))
    monkeypatch.setattr(rss, "traiter_source", lambda *a, **k: pytest.fail("la source arrêtée a été lue"))
    monkeypatch.setattr(rss.sys, "argv", ["collecte_rss.py", "--graphe", "g"])
    assert rss.main() == 0
    assert "rien à collecter" in capsys.readouterr().out

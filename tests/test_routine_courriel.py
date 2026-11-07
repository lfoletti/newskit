"""Les lettres reçues par courriel dans la routine (mainteneur, 03.11.2026) : le collecteur tourne comme les autres
plateformes, et la liste des sources muettes dit « boîte non configurée » d'une source dont la boîte n'a pas de mot de
passe au trousseau, sans attendre trente jours de silence."""

from __future__ import annotations

import datetime as dt

from newskit import routine_hebdo as rh


def test_la_routine_lance_le_collecteur_de_courriel_pour_un_graphe_seul_et_pour_le_principal():
    src = open(rh.__file__, encoding="utf-8").read()
    assert '("courriel", ("Courriel",), "collecte_courriel.py"' in src          # un graphe seul
    assert '["scripts/collecte_courriel.py", "--graphe", p]' in src            # le principal


def _sources(tmp_path, monkeypatch, configurees):
    from newskit import config_sources
    from newskit.config_sources import Source
    monkeypatch.setattr(config_sources, "SOURCES", tmp_path)

    def source(cle, boite, **propres):
        return Source(cle=cle, plateforme="Courriel", nom=f"Nom {cle}", dossier=cle, abonnement="gratuit", graphe="g",
                      propres={"expediteur": "presse.europarl.test", "boite": boite, **propres})
    sources = [source("pe", "veille", site="https://www.europarl.europa.eu/news"), source("autre", "prete")]
    monkeypatch.setattr(rh.cfg, "toutes", lambda *a, graphe=None, **k: sources)
    monkeypatch.setattr(rh.cfg, "boite_configuree", lambda nom: nom in configurees)


def test_une_boite_non_configuree_est_une_source_muette_de_cause_connue(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, configurees={"prete"})
    # Déclarée hier : jamais « muette » par le silence, mais la boîte manque.
    lignes = rh.liste_des_muettes("g", "", aujourd_hui=dt.date(2026, 11, 4), declaree_le=lambda s: dt.date(2026, 11, 3))
    assert [(l["cle"], l["cause"], l["adresse"]) for l in lignes] == [
        ("pe", "boîte non configurée", "https://www.europarl.europa.eu/news")]


def test_une_boite_configuree_et_silencieuse_reste_a_etablir(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, configurees={"veille", "prete"})
    lignes = rh.liste_des_muettes("g", "", aujourd_hui=dt.date(2026, 11, 4), declaree_le=lambda s: dt.date(2026, 8, 1))
    assert [(l["cle"], l["cause"]) for l in lignes] == [("pe", "à établir"), ("autre", "à établir")]
    assert all("@" not in l["adresse"] for l in lignes)                         # jamais l'expéditeur


def test_le_delai_du_courriel_n_arrete_pas_la_routine(monkeypatch, capsys):
    # 05.11.2026 : TimeoutExpired après 600 s ; la routine finissait sur une trace. courriel() n'est jamais bloquant.
    import subprocess
    from newskit import routine_hebdo as rh

    def lent(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1800)
    monkeypatch.setattr(rh, "SANS_COURRIEL", False)
    monkeypatch.setattr(rh.subprocess, "run", lent)
    lettres = []
    monkeypatch.setattr(rh, "commiter_lettres", lambda racine: lettres.append(racine))
    rh.courriel()
    assert "délai de 1800 s dépassé" in capsys.readouterr().out and lettres

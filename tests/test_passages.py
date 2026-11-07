"""Les passages d'un média (07.11.2026) : `media.passages: {tous_les: N, heures: ["05:30", "17:00"]}` remplace
`media.frequence`, qui ne disait qu'une heure. `heures` donne les départs du jour de passage ; `tous_les` l'intervalle en
jours. La tâche planifiée part chaque jour aux heures dites ; un jour hors intervalle, la routine planifiée s'arrête
d'elle-même avant le témoin. Un passage lancé à la main part toujours. `frequence` reste lu (heures d'un passage par jour).
"""

from __future__ import annotations

import datetime
import plistlib
import sys

import pytest
import yaml

from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402
from newskit import nouveau_graphe as ng  # noqa: E402
from newskit import routine_hebdo as rh  # noqa: E402


def _c(tmp_path, m: dict) -> dict:
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"version": 1, "media": {"nom": "ruchers", **m}}),
                                          encoding="utf-8")
    return media.charger(tmp_path / "config.yaml")


def test_sans_passages_un_passage_par_jour_a_5_h_30(tmp_path):
    assert media.passages(_c(tmp_path, {})) == (1, [(5, 30)])


def test_passages_lus(tmp_path):
    c = _c(tmp_path, {"passages": {"tous_les": 2, "heures": ["05:30", "17:00", "9:05"]}})
    assert media.passages(c) == (2, [(5, 30), (17, 0), (9, 5)])


def test_tous_les_seul_garde_l_heure_par_defaut(tmp_path):
    assert media.passages(_c(tmp_path, {"passages": {"tous_les": 3}})) == (3, [(5, 30)])


def test_l_ancienne_frequence_reste_lue(tmp_path):
    assert media.passages(_c(tmp_path, {"frequence": {"heure": 4, "minute": 10}})) == (1, [(4, 10)])


@pytest.mark.parametrize("p", [{"tous_les": 0}, {"tous_les": "2"}, {"tous_les": True}, {"heures": []},
                               {"heures": ["25:00"]}, {"heures": ["5h30"]}, {"heures": "05:30"},
                               {"jours": 2}, ["05:30"]])
def test_des_passages_mal_dits_sont_refuses(tmp_path, p):
    with pytest.raises(media.ConfigMediaInvalide, match="passages"):
        _c(tmp_path, {"passages": p})


def test_passages_et_frequence_ensemble_sont_refuses(tmp_path):
    with pytest.raises(media.ConfigMediaInvalide, match="passages"):
        _c(tmp_path, {"passages": {"tous_les": 1}, "frequence": {"heure": 5}})


@pytest.fixture
def media_vide(tmp_path, monkeypatch):
    monkeypatch.setattr(ng, "RACINE", tmp_path)
    monkeypatch.setattr(ng, "INSTALLATEUR", tmp_path / "deploiement" / "installer_daemons_mac.sh")
    monkeypatch.setattr(cfg, "edition", lambda: {"launchd": "com.exemple", "nom": "ruchers"})
    return tmp_path


def test_la_tache_part_a_chaque_heure_et_se_dit_planifiee(media_vide):
    p = ng.ecrire_plist("ruchers", "Ruchers", [(5, 30), (17, 0)])
    d = plistlib.loads(p.read_bytes())
    assert d["StartCalendarInterval"] == [{"Hour": 5, "Minute": 30}, {"Hour": 17, "Minute": 0}]
    assert d["ProgramArguments"] == [sys.executable, "-u", "-m", "newskit.routine_hebdo", "--graphe", "ruchers",
                                     "--planifie"]


def test_nouveau_graphe_prend_les_heures_du_media_ou_l_heure_donnee(monkeypatch):
    monkeypatch.setattr(media, "passages", lambda c=None: (2, [(5, 30), (17, 0)]))
    assert ng.heures_de(ng.arguments(["ruchers"])) == [(5, 30), (17, 0)]
    assert ng.heures_de(ng.arguments(["ruchers", "--heure", "6", "--minute", "15"])) == [(6, 15)]


@pytest.fixture
def jours(tmp_path, monkeypatch):
    monkeypatch.setattr(rh, "RACINE", tmp_path)
    monkeypatch.setattr(media, "passages", lambda c=None: (2, [(5, 30)]))
    return tmp_path


def test_hors_intervalle_la_routine_planifiee_attend(jours):
    j1 = datetime.date(2026, 11, 7)
    assert rh.jour_de_passage("ruchers", j1) is True               # aucun passage encore
    rh.noter_le_passage("ruchers", j1)
    assert rh.jour_de_passage("ruchers", j1) is True               # même jour : une autre heure du même passage
    assert rh.jour_de_passage("ruchers", j1 + datetime.timedelta(days=1)) is False
    assert rh.jour_de_passage("ruchers", j1 + datetime.timedelta(days=2)) is True
    assert rh.jour_de_passage("autre", j1 + datetime.timedelta(days=1)) is True   # un graphe, son propre intervalle


def test_un_passage_par_jour_part_chaque_jour(jours, monkeypatch):
    monkeypatch.setattr(media, "passages", lambda c=None: (1, [(5, 30)]))
    j1 = datetime.date(2026, 11, 7)
    rh.noter_le_passage("ruchers", j1)
    assert rh.jour_de_passage("ruchers", j1 + datetime.timedelta(days=1)) is True


class _Arret(Exception):
    pass


@pytest.fixture
def routine(jours, monkeypatch):
    vus = []
    monkeypatch.setattr(rh.cfg, "graphes", lambda: {"ruchers": {}})
    monkeypatch.setattr(rh.cfg, "chemins_du_graphe", lambda nom: {"rapport": jours / "ROUTINE.md"})
    monkeypatch.setattr(rh, "exiger_le_temoin", lambda: vus.append("témoin"))
    monkeypatch.setattr(rh, "prendre_le_verrou", lambda **k: vus.append("verrou"))

    def arret():
        raise _Arret
    monkeypatch.setattr(rh, "attendre_la_collecte", arret)
    return vus


def _lancer(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["routine_hebdo", "--graphe", "ruchers", *args])
    try:
        return rh.main()
    except _Arret:
        return "parti"


def test_la_routine_planifiee_hors_intervalle_s_arrete_avant_le_temoin(routine, monkeypatch):
    rh.noter_le_passage("ruchers", datetime.date.today() - datetime.timedelta(days=1))
    assert _lancer(monkeypatch, "--planifie") == 0 and routine == []
    assert _lancer(monkeypatch) == "parti"                                    # à la main : il part
    assert rh.dernier_passage("ruchers") == datetime.date.today()             # et compte comme passage


def test_un_passage_refuse_par_le_temoin_ne_compte_pas(routine, monkeypatch):
    monkeypatch.setattr(rh, "exiger_le_temoin", lambda: 2)
    assert _lancer(monkeypatch, "--planifie") == 2
    assert rh.dernier_passage("ruchers") is None

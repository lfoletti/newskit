"""Un média dont les données sont un dépôt git sans distant publie quand même (chantier K, 16.11.2026).

Au serveur, les fiches, les lots et le cerveau vivent dans un dépôt git local, jamais poussé (décision du mainteneur,
16.11.2026). La routine commite comme au poste ; sans distant, elle ne pousse rien, mais publie le fil, l'index, les
graphes et le cerveau comme après une poussée réussie. Les dépôts du navigateur ne lèvent pas d'alerte."""

from __future__ import annotations

import subprocess

import pytest

from newskit import depots
from newskit import routine_hebdo as rh


def depot(chemin, distant=None):
    chemin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=chemin, check=True)
    if distant:
        subprocess.run(["git", "remote", "add", "origin", str(distant)], cwd=chemin, check=True)
    return chemin


@pytest.fixture
def publications(monkeypatch):
    appels = []
    monkeypatch.setattr(rh, "publier_en_ligne", lambda: appels.append("publier"))
    return appels


def test_sans_distant_la_routine_publie_sans_pousser(tmp_path, monkeypatch, publications, capsys):
    monkeypatch.setattr(rh, "RACINE", depot(tmp_path / "donnees"))
    rh.pousser()
    assert publications == ["publier"]
    assert "ALERTE" not in capsys.readouterr().out


def test_avec_un_distant_injoignable_la_routine_alerte_et_ne_publie_pas(tmp_path, monkeypatch, publications, capsys):
    monkeypatch.setattr(rh, "RACINE", depot(tmp_path / "donnees", distant=tmp_path / "absent"))
    rh.pousser()
    assert publications == []
    assert "ALERTE : envoi vers origin impossible" in capsys.readouterr().out


def test_sans_distant_les_depots_ne_levent_pas_d_alerte(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(depots, "RACINE", depot(tmp_path / "donnees"))
    assert depots.pousser() == ""
    assert "ALERTE" not in capsys.readouterr().err


def test_avec_un_distant_injoignable_les_depots_alertent(tmp_path, monkeypatch):
    monkeypatch.setattr(depots, "RACINE", depot(tmp_path / "donnees", distant=tmp_path / "absent"))
    assert depots.pousser().startswith("ALERTE : push refusé")

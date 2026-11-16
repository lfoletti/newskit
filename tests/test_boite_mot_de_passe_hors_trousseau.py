"""Le mot de passe d'une boîte hors du trousseau macOS (chantier K de l'édition, 16.11.2026) : les routines passent sur un
serveur Linux, sans trousseau. `mot_de_passe_env` nomme la variable qui le porte, lue dans l'environnement puis dans le
fichier hors dépôt (commun.variable_hors_depot), comme `utilisateur_env`. Le registre ne porte jamais le secret."""

from __future__ import annotations

import subprocess

import pytest

from newskit import commun
from newskit import config_sources as cfg

BOITE = {"hote": "imap.exemple.ch", "port": 993, "utilisateur_env": "U_BOITE", "dossier": "INBOX"}


def _registre(monkeypatch, **propres):
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"boites": {"lettres": {**BOITE, **propres}}})


def _sans_trousseau(monkeypatch):
    def refuse(*a, **k):
        raise OSError("security introuvable")

    monkeypatch.setattr(cfg.subprocess, "run", refuse)


def test_le_registre_accepte_le_nom_de_la_variable():
    assert cfg.valider_boites({"lettres": {**BOITE, "mot_de_passe_env": "MDP_BOITE"}}, "sources.yaml")


@pytest.mark.parametrize("valeur", ["", "  ", 3, True])
def test_le_nom_de_la_variable_est_un_texte_non_vide(valeur):
    with pytest.raises(cfg.ConfigInvalide, match="mot_de_passe_env"):
        cfg.valider_boites({"lettres": {**BOITE, "mot_de_passe_env": valeur}}, "sources.yaml")


def test_un_autre_champ_reste_refuse():
    with pytest.raises(cfg.ConfigInvalide, match="champs inconnus"):
        cfg.valider_boites({"lettres": {**BOITE, "mot_de_passe": "secret"}}, "sources.yaml")


def test_la_variable_d_environnement_sert_sans_trousseau(monkeypatch):
    _registre(monkeypatch, mot_de_passe_env="MDP_BOITE")
    _sans_trousseau(monkeypatch)
    monkeypatch.setenv("MDP_BOITE", " s3cret ")
    assert cfg.lire_mot_de_passe_boite("lettres") == ("s3cret", "")


def test_le_fichier_hors_depot_sert_sans_trousseau(monkeypatch, tmp_path):
    _registre(monkeypatch, mot_de_passe_env="MDP_BOITE")
    _sans_trousseau(monkeypatch)
    monkeypatch.delenv("MDP_BOITE", raising=False)
    f = tmp_path / "newskit.env"
    f.write_text("AUTRE=x\nMDP_BOITE=du-fichier\n", encoding="utf-8")
    monkeypatch.setattr(commun, "FICHIER_CONTACT", f)
    assert cfg.lire_mot_de_passe_boite("lettres") == ("du-fichier", "")


def test_variable_absente_le_trousseau_reste_lu(monkeypatch, tmp_path):
    _registre(monkeypatch, mot_de_passe_env="MDP_BOITE")
    monkeypatch.delenv("MDP_BOITE", raising=False)
    monkeypatch.setattr(commun, "FICHIER_CONTACT", tmp_path / "absent.env")
    monkeypatch.setattr(cfg, "edition", lambda: {"nom": "ed"})
    monkeypatch.setattr(
        cfg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="du-trousseau\n", stderr="")
    )
    assert cfg.lire_mot_de_passe_boite("lettres") == ("du-trousseau", "")


def test_variable_absente_et_sans_trousseau_la_raison_nomme_la_variable(monkeypatch, tmp_path):
    _registre(monkeypatch, mot_de_passe_env="MDP_BOITE")
    _sans_trousseau(monkeypatch)
    monkeypatch.delenv("MDP_BOITE", raising=False)
    monkeypatch.setattr(commun, "FICHIER_CONTACT", tmp_path / "absent.env")
    mdp, raison = cfg.lire_mot_de_passe_boite("lettres")
    assert mdp is None and "MDP_BOITE" in raison and str(tmp_path / "absent.env") in raison


def test_sans_variable_declaree_rien_ne_change(monkeypatch):
    _registre(monkeypatch)
    _sans_trousseau(monkeypatch)
    monkeypatch.setenv("MDP_BOITE", "ignoree")
    assert cfg.lire_mot_de_passe_boite("lettres") == (
        None,
        "lecture du trousseau refusée (commande security introuvable)",
    )


def test_une_boite_non_declaree_lit_le_trousseau(monkeypatch):
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {})
    _sans_trousseau(monkeypatch)
    assert cfg.lire_mot_de_passe_boite("inconnue")[0] is None

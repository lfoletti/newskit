"""La daily part aux abonnés sans validation (mainteneur, 11.11.2026 : « les newsletters doivent partir à la liste sans
attendre de validation de ma part »).

Le média le déclare dans son config.yaml (`lettre_aux_abonnes: true`) : après l'envoi de la daily à l'adresse du
registre, notifier_courriel la passe à la diffusion de l'édition (diffusion.envoyer), comme la weekly. Une diffusion qui
échoue est signalée et ne retient pas le rapport de routine.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from newskit import diffusion, media
from newskit import lettre as lt
from newskit import notifier_courriel as nc

from test_rapport_routine import _lettre


def _preparer(tmp_path, monkeypatch, config: dict, envoi: int = 0):
    rapport = tmp_path / "ROUTINE.md"
    rapport.write_text("rapport", encoding="utf-8")
    monkeypatch.setattr(nc, "RAPPORT", rapport)
    monkeypatch.setattr(nc, "LETTRES", tmp_path / "lettres")
    monkeypatch.setattr(sys, "argv", ["notifier_courriel.py", "--envoyer"])
    monkeypatch.setattr(lt, "eclairages_publies", lambda g: {})
    monkeypatch.setattr(lt, "lettre_du_graphe", lambda *a, **k: {**_lettre(), "redige": True})
    monkeypatch.setattr(media, "courant", lambda: config)
    vus, diffusees = [], []
    monkeypatch.setattr(nc, "envoyer", lambda o, c, h="", vers=None, **k: vus.append(o) or envoi)
    return vus, diffusees


def test_la_daily_part_aux_abonnes_quand_le_media_le_declare(tmp_path, monkeypatch):
    vus, diffusees = _preparer(tmp_path, monkeypatch, {"lettre_aux_abonnes": True})
    monkeypatch.setattr(diffusion, "envoyer", lambda L: diffusees.append(L) or 0)
    assert nc.main() == 0
    assert len(diffusees) == 1 and diffusees[0]["faits"][0]["titre"] == "Fait de la une"
    assert len(vus) == 2 and vus[1].startswith("Rapport de routine")  # la daily au registre, puis le rapport


def test_sans_la_declaration_la_daily_ne_part_pas_aux_abonnes(tmp_path, monkeypatch):
    for config in ({}, {"lettre_aux_abonnes": False}):
        vus, diffusees = _preparer(tmp_path, monkeypatch, config)
        monkeypatch.setattr(diffusion, "envoyer", lambda L: diffusees.append(L) or 0)
        assert nc.main() == 0 and diffusees == [] and len(vus) == 2


def test_une_daily_qui_n_est_pas_partie_ne_va_pas_aux_abonnes(tmp_path, monkeypatch):
    vus, diffusees = _preparer(tmp_path, monkeypatch, {"lettre_aux_abonnes": True}, envoi=1)
    monkeypatch.setattr(diffusion, "envoyer", lambda L: diffusees.append(L) or 0)
    assert nc.main() == 1 and diffusees == []


@pytest.mark.parametrize("panne", ["code", "exception"])
def test_une_diffusion_en_echec_est_signalee_et_le_rapport_part(tmp_path, monkeypatch, capsys, panne):
    vus, _ = _preparer(tmp_path, monkeypatch, {"lettre_aux_abonnes": True})

    def diffuser(L):
        if panne == "exception":
            raise OSError("ssh refusé")
        return 1

    monkeypatch.setattr(diffusion, "envoyer", diffuser)
    assert nc.main() == 0 and len(vus) == 2
    assert "ALERTE : la daily n'est pas partie à tous les abonnés" in capsys.readouterr().err


@pytest.mark.parametrize("valeur", ["oui", 1, None])
def test_le_config_n_admet_qu_un_booleen(tmp_path, valeur):
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump({"version": 1, "media": {"nom": "media"}, "lettre_aux_abonnes": valeur}), encoding="utf-8"
    )
    with pytest.raises(media.ConfigMediaInvalide, match="lettre_aux_abonnes"):
        media.charger(p)


def test_le_config_admet_la_declaration(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("version: 1\nmedia: {nom: media}\nlettre_aux_abonnes: true\n", encoding="utf-8")
    assert media.charger(p)["lettre_aux_abonnes"] is True

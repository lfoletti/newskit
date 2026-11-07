"""Le service de mise à jour du serveur se nomme dans `config.yaml` (`media.service_maj`), pas dans le moteur
(07.11.2026).

Après un envoi, la routine et l'intégration des dépôts lançaient `sudo systemctl start kg-mcp-maj.service` : le nom
d'un service de la première édition, écrit dans le moteur. Le média le déclare ; sans lui, rien n'est lancé et le
serveur se met à jour à son heure. Le nom part dans une commande ssh : seuls lettres, chiffres et « @._- » passent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402

SRC = Path(media.__file__).parent


def _media(d: Path, **m) -> Path:
    (d / "config.yaml").write_text(
        yaml.safe_dump({"version": 1, "media": {"nom": "apiculture", **m}}), encoding="utf-8"
    )
    return d / "config.yaml"


def test_le_media_declare_son_service(tmp_path):
    c = media.charger(_media(tmp_path, service_maj="kg-maj"))
    assert c["media"]["service_maj"] == "kg-maj"


@pytest.mark.parametrize("v", ["kg-maj; reboot", "kg maj", "$(id)", "-kg", "a" * 90])
def test_un_nom_hors_des_caracteres_admis_est_refuse(tmp_path, v):
    with pytest.raises(media.ConfigMediaInvalide, match=r"media\.service_maj :"):
        media.charger(_media(tmp_path, service_maj=v))


def test_la_commande_nomme_le_service_declare(monkeypatch):
    monkeypatch.setattr(cfg, "edition", lambda: {**cfg.EDITION, "service_maj": "kg-maj"})
    assert cfg.commande_maj_serveur() == "sudo systemctl start kg-maj.service"


def test_sans_service_aucune_commande(monkeypatch):
    monkeypatch.setattr(cfg, "edition", lambda: dict(cfg.EDITION))
    assert cfg.commande_maj_serveur() is None


def test_le_moteur_ne_nomme_aucun_service_de_la_premiere_edition():
    for f in SRC.glob("*.py"):
        assert "kg-mcp-maj" not in f.read_text(encoding="utf-8"), f.name


def _publier(tmp_path, monkeypatch, service: str) -> list[list[str]]:
    import subprocess
    import sys
    import types

    from newskit import routine_hebdo as rh

    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "vps_kg.env").write_text(
        "VPS_CLE=~/.ssh/k\nVPS_UTILISATEUR=kg\nVPS_HOTE=exemple.org\n", encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for nom in ("publier_index", "publier_graphes", "publier_cerveau"):
        monkeypatch.setitem(sys.modules, f"newskit.{nom}", types.SimpleNamespace(publier=lambda *a, **k: None))
    monkeypatch.setattr(cfg, "edition", lambda: {**cfg.EDITION, "service_maj": service})
    appels: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **k: appels.append(args))
    rh.publier_en_ligne()
    return appels


def test_apres_l_envoi_la_routine_lance_le_service_declare(tmp_path, monkeypatch):
    appels = _publier(tmp_path, monkeypatch, "kg-maj")
    assert [a[-1] for a in appels if a[0] == "ssh"] == ["sudo systemctl start kg-maj.service"]


def test_sans_service_la_routine_ne_lance_rien(tmp_path, monkeypatch, capsys):
    assert [a for a in _publier(tmp_path, monkeypatch, "") if a[0] == "ssh"] == []
    assert "aucun service de mise à jour déclaré" in capsys.readouterr().out


def test_l_integration_des_depots_prend_la_commande_du_media():
    import inspect

    from newskit import depots

    s = inspect.getsource(depots.suivre)
    assert "cfg.commande_maj_serveur()" in s and "if commande is None:" in s

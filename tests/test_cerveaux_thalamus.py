"""Les noms des cerveaux de Thalamus se déclarent dans `config.yaml` (`media.cerveau_du_site`, `media.cerveau_lacunes`),
pas dans le moteur (07.11.2026).

Le moteur formait le nom du cerveau du site (`<média>/<graphe du site>`, lu par la station « Interroger le graphe ») et
écrivait en dur celui des lacunes signalées (`lacunes-signalees`). Une édition qui renomme ses cerveaux devait changer
le moteur. Vides, les deux gardent les noms d'avant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from newskit import config_sources as cfg
from newskit import media


def _media(d: Path, **m) -> Path:
    (d / "config.yaml").write_text(
        yaml.safe_dump({"version": 1, "media": {"nom": "apiculture", **m}}), encoding="utf-8"
    )
    return d / "config.yaml"


def test_le_media_declare_ses_cerveaux(tmp_path):
    c = media.charger(_media(tmp_path, cerveau_du_site="api-site", cerveau_lacunes="api-lacunes"))
    assert (c["media"]["cerveau_du_site"], c["media"]["cerveau_lacunes"]) == ("api-site", "api-lacunes")


@pytest.mark.parametrize("champ", ["cerveau_du_site", "cerveau_lacunes"])
@pytest.mark.parametrize("v", ["Api", "api site", "api;x", "-api", "a" * 70])
def test_un_nom_de_cerveau_hors_des_caracteres_admis_est_refuse(tmp_path, champ, v):
    with pytest.raises(media.ConfigMediaInvalide, match=rf"media\.{champ} :"):
        media.charger(_media(tmp_path, **{champ: v}))


def _edition(monkeypatch, **e):
    monkeypatch.setattr(cfg, "edition", lambda: {**cfg.EDITION, "nom": "apiculture", "principal": "ruches", **e})


def test_le_cerveau_du_site_est_celui_que_le_media_declare(monkeypatch):
    _edition(monkeypatch, cerveau_du_site="api-site")
    assert cfg.cerveau_thalamus("ruches") == "api-site"
    assert cfg.cerveau_thalamus("miel") == "apiculture/miel"  # un autre graphe garde le nom formé


def test_sans_declaration_le_nom_est_forme(monkeypatch):
    _edition(monkeypatch)
    assert cfg.cerveau_thalamus("ruches") == "apiculture/ruches"


def test_le_cerveau_des_lacunes_est_celui_que_le_media_declare(monkeypatch):
    _edition(monkeypatch, cerveau_lacunes="api-lacunes")
    assert cfg.cerveau_des_lacunes() == "api-lacunes"
    _edition(monkeypatch)
    assert cfg.cerveau_des_lacunes() == "lacunes-signalees"


def test_la_station_interroge_le_cerveau_du_site(monkeypatch):
    from newskit import interroger

    _edition(monkeypatch, cerveau_du_site="api-site")
    assert interroger.cerveau() == "api-site"


def test_la_releve_lit_le_cerveau_des_lacunes(monkeypatch):
    from newskit import lacunes as la

    _edition(monkeypatch, cerveau_lacunes="api-lacunes")
    vus = []

    def appeler(outil, **a):
        vus.append(a.get("brain"))
        return {"results": []}

    la.relever_lacunes_signalees(appeler)
    assert vus == ["api-lacunes"]


def test_la_carte_servie_nomme_le_cerveau_des_lacunes():
    import inspect

    from newskit import puits_cortexdb

    s = inspect.getsource(puits_cortexdb)
    assert "cfg.cerveau_des_lacunes()" in s and "la.CERVEAU_LACUNES" not in s


def test_la_carte_servie_dit_ou_signaler(monkeypatch):
    from newskit import puits_cortexdb

    _edition(monkeypatch, cerveau_lacunes="api-lacunes")
    assert "`th_add` dans le cerveau `api-lacunes`." in puits_cortexdb.carte_pour_thalamus("# Carte\n")

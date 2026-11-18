"""Le cerveau d'un graphe continu vit où il est servi.

Un graphe qui déclare `cerveau_continu: true` reçoit ses fiches dans le cerveau que le serveur tient ouvert, au fil de
la journée. Ce cerveau n'a donc qu'un fichier par machine : le fichier d'adresse du serveur se lit dans le dossier des
cerveaux, et le cerveau d'un graphe continu ne s'envoie jamais d'une machine à une autre, où il remplacerait un cerveau
plus récent."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import lecture_cortexdb as lc
from newskit import publier_cerveau as pcv


def registre(**cles) -> tuple[dict, str]:
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    for k in ("cortexdb", "cerveau_continu"):
        d["graphes"][g].pop(k, None)
    d["graphes"][g].update(cles)
    return d, g


def test_le_registre_admet_cerveau_continu(monkeypatch):
    d, g = registre(cortexdb=True, cerveau_continu=True)
    assert cfg._valider(d, cfg.FICHIER)
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": d["graphes"]})
    assert cfg.cerveau_continu(g) is True
    assert cfg.cerveau_continu("graphe-absent") is False
    d["graphes"][g]["cerveau_continu"] = False
    assert cfg.cerveau_continu(g) is False


@pytest.mark.parametrize("v", ["oui", 1, None, ["a"]])
def test_cerveau_continu_est_vrai_ou_faux(v):
    d, g = registre(cortexdb=True, cerveau_continu=v)
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.cerveau_continu : vrai ou faux"):
        cfg._valider(d, cfg.FICHIER)


@pytest.mark.parametrize("cortexdb", [{}, {"cortexdb": False}])
def test_cerveau_continu_demande_un_cerveau(cortexdb):
    d, g = registre(cerveau_continu=True, **cortexdb)
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.cerveau_continu demande cortexdb"):
        cfg._valider(d, cfg.FICHIER)
    d, g = registre(cerveau_continu=False, **cortexdb)
    assert cfg._valider(d, cfg.FICHIER)


def test_l_adresse_se_lit_dans_le_dossier_des_cerveaux(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DOSSIER", tmp_path / "cerveaux")
    assert lc.fichier_adresse("g") == tmp_path / "cerveaux" / "g.adresse"


def test_l_envoi_part_du_dossier_des_cerveaux(tmp_path):
    code = "from newskit import publier_cerveau as p, lecture_cortexdb as l; print(p.DOSSIER); print(l.fichier_adresse('g'))"
    r = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ | {"CORTEXDB_DOSSIER": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.split() == [str(tmp_path), str(tmp_path / "g.adresse")]


def cerveaux(tmp_path, monkeypatch, **graphes):
    dos = tmp_path / "cerveaux"
    dos.mkdir()
    for g in graphes:
        (dos / f"{g}.db").write_bytes(b"cerveau")
        (dos / f"{g}.json").write_text(json.dumps({"publie": True}), encoding="utf-8")
    monkeypatch.setattr(pcv, "DOSSIER", dos)
    monkeypatch.setattr(pcv, "VPS", tmp_path / "vps")
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": graphes})


def test_un_cerveau_continu_ne_s_envoie_pas(tmp_path, monkeypatch):
    cerveaux(tmp_path, monkeypatch, a={"cortexdb": True, "cerveau_continu": True})
    assert pcv.a_envoyer(["a"]) == []
    assert pcv.graphes_engages() == []


def test_un_cerveau_non_continu_part_comme_avant(tmp_path, monkeypatch):
    cerveaux(
        tmp_path,
        monkeypatch,
        a={"cortexdb": True, "cerveau_continu": True},
        b={"cortexdb": True},
        c={"cortexdb": True, "cerveau_continu": False},
    )
    assert {rel for _, rel in pcv.a_envoyer(["a", "b", "c"])} == {
        "cache/cortexdb/b.envoi.db",
        "cache/cortexdb/b.envoi.json",
        "cache/cortexdb/c.envoi.db",
        "cache/cortexdb/c.envoi.json",
    }
    assert pcv.graphes_engages() == ["b", "c"]


def test_la_liste_nomme_un_cerveau_range_hors_des_donnees(tmp_path, monkeypatch, capsys):
    cerveaux(tmp_path, monkeypatch, b={"cortexdb": True})
    monkeypatch.setattr(pcv, "RACINE", tmp_path / "donnees")
    monkeypatch.setattr(sys, "argv", ["publier_cerveau", "--lister"])
    assert pcv.main() == 0
    assert f"cache/cortexdb/b.envoi.db  ←  {tmp_path / 'cerveaux' / 'b.db'}  (0 Mo)" in capsys.readouterr().out

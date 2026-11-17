"""CortexDB fait attendre chaque écrivain son tour au plus 30 s ; son point de contrôle du WAL prend ce tour. Au VPS
(disque à 3,4 Mo/s en écritures de 4 Ko), un point de contrôle a dépassé 30 s : le 16 et le 17.11.2026, la
reconstruction complète du cerveau s'est arrêtée sur « no turn to write within 30s » après une heure. L'erreur
tombe avant toute écriture : le pilote attend, puis renvoie l'appel."""

from __future__ import annotations

import json
import sys
import types

import pytest

from newskit import puits_cortexdb as pc

TOUR = "upsert entity nodes: failed to begin transaction: cortexdb: no turn to write within 30s, another writer in this process held it: database is locked (SQLITE_BUSY)"


class ErreurRpc(Exception):
    def __init__(self, details):
        super().__init__(details)
        self._details = details

    def details(self):
        return self._details


class Outils:
    def __init__(self, erreurs):
        self.erreurs, self.appels = list(erreurs), []

    def CallTool(self, req):  # noqa: N802
        self.appels.append(req.name)
        if self.erreurs:
            raise self.erreurs.pop(0)
        return types.SimpleNamespace(result_json=json.dumps({"ok": req.name}))


@pytest.fixture
def client(monkeypatch):
    mod = types.ModuleType("cortexdb_client")
    mod.proto = types.SimpleNamespace(CallToolRequest=lambda name, args_json: types.SimpleNamespace(name=name))
    monkeypatch.setitem(sys.modules, "cortexdb_client", mod)
    attentes = []
    monkeypatch.setattr(pc.time, "sleep", attentes.append)

    def fabrique(*erreurs):
        return types.SimpleNamespace(tools=Outils(erreurs)), attentes

    return fabrique


def test_un_tour_perdu_se_rejoue(client):
    c, attentes = client(ErreurRpc(TOUR), ErreurRpc(TOUR))
    assert pc.EnPlace(c, None, {}).appel("upsert_entities", document_id="d") == {"ok": "upsert_entities"}
    assert c.tools.appels == ["upsert_entities"] * 3
    assert len(attentes) == 2


def test_le_tour_perdu_trop_souvent_s_arrete(client):
    c, _ = client(*[ErreurRpc(TOUR)] * (len(pc.ATTENTES_TOUR) + 1))
    with pytest.raises(ErreurRpc):
        pc.EnPlace(c, None, {}).appel("upsert_entities", document_id="d")
    assert len(c.tools.appels) == len(pc.ATTENTES_TOUR) + 1


def test_une_autre_erreur_ne_se_rejoue_pas(client):
    c, attentes = client(ErreurRpc("database disk image is malformed"))
    with pytest.raises(ErreurRpc):
        pc.EnPlace(c, None, {}).appel("ingest_document", document_id="d")
    assert c.tools.appels == ["ingest_document"]
    assert attentes == []


def test_le_versement_rejoue_aussi(client):
    c, _ = client(ErreurRpc(TOUR))
    v = {"document": {"document_id": "d"}, "entites": [], "relations": [], "origine": "o"}
    assert pc.verser(c, v) == {"morceaux": 0, "entites": 0, "relations": 0}
    assert c.tools.appels == ["ingest_document", "ingest_document"]

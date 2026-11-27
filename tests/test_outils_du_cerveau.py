"""Les outils à la main lisent le cerveau, pas `graphe.json` (K3, étape 2, 27.11.2026) : l'élagage et le semis de la
pyramide (`partage_graphe`), le contrôle d'un nouveau graphe (`nouveau_graphe`)."""

from __future__ import annotations

import networkx as nx

from newskit import cerveau_cortexdb as cc
from newskit import graphe_cerveau as gc
from newskit import nouveau_graphe as ng
from newskit import partage_graphe as pg


def _cerveau(racine):
    G = nx.Graph()
    G.add_node("entity:a", name="Alpha", node_type="concept", newskit_id="concept_a", _source=f"{racine}/sources/a.md")
    G.add_node("entity:b", name="Beta", node_type="concept", _source="sources/b.md")
    G.add_node("entity:c", name="Gamma", node_type="auteur")
    return G


def test_les_fiches_presentes_sont_les_sources_du_cerveau_relatives_a_la_racine(monkeypatch, tmp_path):
    monkeypatch.setattr("newskit.config_sources.RACINE", tmp_path)
    monkeypatch.setattr(gc, "charger", lambda g: _cerveau(tmp_path))
    assert pg.sources_presentes("g") == {"sources/a.md", "sources/b.md"}


def test_les_cles_presentes_sont_les_anciennes_cles_du_cerveau(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "charger", lambda g: _cerveau(tmp_path))
    assert pg.cles_presentes("g") == {"concept_a", "entity:b", "entity:c"}


def test_un_nouveau_graphe_attend_son_premier_cerveau(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / f"{g}.db")

    def premier():
        return next(ok for ok, quoi, _ in ng.pieces("ruchers") if quoi == "premier cerveau")

    assert premier() is False
    (tmp_path / "ruchers.db").write_bytes(b"")
    assert premier() is True

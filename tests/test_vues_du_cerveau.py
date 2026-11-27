"""Les vues du graphe (K4 : carte, sommets, relations, fiches `expliquer`) se calculent sur le cerveau, sous ses noms
(K3, étape 2, 27.11.2026). Sans cerveau, aucune vue."""

from __future__ import annotations

import json

import test_interroger_graphe as ti

from newskit import cerveau_cortexdb as cc


def _poser(monkeypatch, tmp_path, cerveau: bool):
    if cerveau:
        ti._cerveau(tmp_path / "g.db", ti.NOEUDS, ti.ARETES)
    (tmp_path / "carte.md").write_text("# Carte des ruchers", encoding="utf-8")
    monkeypatch.setattr(cc, "chemin_cerveau", lambda g: tmp_path / f"{g}.db")
    monkeypatch.setattr(
        "newskit.config_sources.chemins_du_graphe", lambda g: {"carte": tmp_path / "carte.md", "base": tmp_path}
    )
    monkeypatch.setattr("newskit.config_sources.graphes_inclus", lambda g: [])


def test_les_vues_se_calculent_sur_le_cerveau_sous_ses_noms(monkeypatch, tmp_path):
    _poser(monkeypatch, tmp_path, cerveau=True)
    cc.ecrire_vues("g", tmp_path / "vues.json")
    vues = json.loads((tmp_path / "vues.json").read_text(encoding="utf-8"))
    par_origine = {v["origine"]: v["document"]["content"] for v in vues}
    assert "# Carte des ruchers" in par_origine["vue/carte"]
    assert "Swarm law" in par_origine["vue/sommets"] and "applies_to" in par_origine["vue/relations"]
    assert "Who owns a swarm" in par_origine["vue/expliquer/entity:swarm_law"]


def test_sans_cerveau_aucune_vue(monkeypatch, tmp_path):
    _poser(monkeypatch, tmp_path, cerveau=False)
    cc.ecrire_vues("g", tmp_path / "vues.json")
    assert json.loads((tmp_path / "vues.json").read_text(encoding="utf-8")) == []

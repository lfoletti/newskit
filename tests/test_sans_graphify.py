"""Newskit se passe de graphify (chantier E2, décision du 07.11.2026 : « se passer totalement de graphify… on a un outil
de trop »). La structure des titres, la validation, la composition et les communautés vivent dans le paquet
(structure_titres, composition, communautes)."""

import ast
import sys
import tomllib
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parents[1]


def test_aucun_module_de_newskit_n_importe_graphify():
    """Le moteur se passe de graphify (décision du 07.11.2026 : « on a un outil de trop ») : aucun import, nulle part dans
    le paquet, et le paquet ne l'installe plus."""
    importes = []
    for p in sorted((RACINE_DEPOT / "src" / "newskit").rglob("*.py")):
        for noeud in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            noms = []
            if isinstance(noeud, ast.Import):
                noms = [a.name for a in noeud.names]
            elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                noms = [noeud.module]
            importes += [f"{p.name}: {n}" for n in noms if n.split(".")[0] == "graphify"]
    assert importes == []
    projet = tomllib.loads((RACINE_DEPOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = projet["dependencies"] + [d for v in projet.get("optional-dependencies", {}).values() for d in v]
    assert not [d for d in deps if d.lower().startswith("graphify")]


def test_la_couche_2_valide_ses_lots_sans_graphify():
    """Sans graphify, `extraire_concepts.valider` rendait une liste vide : un lot mal formé passait sans contrôle."""
    from newskit import extraire_concepts as ec

    lot = {"nodes": [{"id": "a", "label": "A", "file_type": "bizarre", "source_file": "s"}], "edges": []}
    assert ec.valider(lot) == [
        "Node 0 (id='a') has invalid file_type 'bizarre' - must be one of "
        "['code', 'concept', 'document', 'image', 'paper', 'rationale']"
    ]


def _squelette(monkeypatch, capsys, extraction):
    from newskit import extraire_squelette as sq

    monkeypatch.setattr(sq, "construire", lambda graphe=None: (extraction, {"noeuds_total": 1}, []))
    monkeypatch.setattr(sys, "argv", ["extraire_squelette", "--valider", "--stats"])
    code = sq.main()
    return code, capsys.readouterr()


def test_le_squelette_se_valide_sans_graphify(monkeypatch, capsys):
    valide = {"nodes": [{"id": "a", "label": "A", "file_type": "document", "source_file": "s"}], "edges": []}
    code, sortie = _squelette(monkeypatch, capsys, valide)
    assert code == 0 and "validation du schéma : 0 erreur(s)" in sortie.out
    invalide = {"nodes": [{"id": "a", "label": "A", "file_type": "document"}], "edges": []}
    code, sortie = _squelette(monkeypatch, capsys, invalide)
    assert code == 1 and "validation du schéma : 1 erreur(s)" in sortie.out
    assert "missing required field 'source_file'" in sortie.out

"""Un collecteur qui n'a pas pu lire une source le dit par son code de sortie (CHANTIER_GENERIQUE.md, défaut 1).

OpenAlex et les dépôts (GitHub, HuggingFace) rendaient toujours 0 : un refus de l'API paraissait « ok » dans la
routine. La règle est celle de `collecte_rss.py` : un refus n'est pas une absence, le code de sortie le porte.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from newskit import collecte_depots
from newskit import collecte_openalex


def _source(tmp_path, plateforme: str):
    chemin = tmp_path / "sources" / plateforme.lower()
    return SimpleNamespace(
        nom=f"essai {plateforme}",
        cle=f"essai_{plateforme.lower()}",
        plateforme=plateforme,
        chemin=chemin,
        manifeste=chemin / "manifeste.json",
        requetes=["agents"],
        jours=30,
        par_requete=2,
        etoiles_min=1,
    )


def _sans_attente(monkeypatch, module):
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)


def test_openalex_rend_1_quand_une_requete_echoue(tmp_path, monkeypatch):
    src = _source(tmp_path, "OpenAlex")
    monkeypatch.setattr(collecte_openalex.cfg, "toutes", lambda p, graphe=None: [src] if p == "OpenAlex" else [])
    monkeypatch.setattr(collecte_openalex, "demander", lambda url, essais=3: None)
    monkeypatch.setattr(collecte_openalex, "contact", lambda: "x@example.org")
    _sans_attente(monkeypatch, collecte_openalex)
    monkeypatch.setattr(sys, "argv", ["collecte_openalex.py"])
    assert collecte_openalex.main() == 1


def test_openalex_rend_0_sans_echec(tmp_path, monkeypatch):
    src = _source(tmp_path, "OpenAlex")
    monkeypatch.setattr(collecte_openalex.cfg, "toutes", lambda p, graphe=None: [src] if p == "OpenAlex" else [])
    monkeypatch.setattr(collecte_openalex, "demander", lambda url, essais=3: {"results": []})
    monkeypatch.setattr(collecte_openalex, "contact", lambda: "x@example.org")
    _sans_attente(monkeypatch, collecte_openalex)
    monkeypatch.setattr(sys, "argv", ["collecte_openalex.py"])
    assert collecte_openalex.main() == 0


def _refus(*_a, **_k):
    raise OSError("HTTP Error 403: rate limit exceeded")


def test_depots_rendent_1_quand_github_refuse(tmp_path, monkeypatch):
    src = _source(tmp_path, "GitHub")
    monkeypatch.setattr(collecte_depots.cfg, "toutes", lambda p, graphe=None: [src] if p == "GitHub" else [])
    monkeypatch.setattr(collecte_depots, "lire", _refus)
    _sans_attente(monkeypatch, collecte_depots)
    monkeypatch.setattr(sys, "argv", ["collecte_depots.py"])
    assert collecte_depots.main() == 1


def test_depots_rendent_1_quand_huggingface_refuse(tmp_path, monkeypatch):
    src = _source(tmp_path, "HuggingFace")
    monkeypatch.setattr(collecte_depots.cfg, "toutes", lambda p, graphe=None: [src] if p == "HuggingFace" else [])
    monkeypatch.setattr(collecte_depots, "lire", _refus)
    _sans_attente(monkeypatch, collecte_depots)
    monkeypatch.setattr(sys, "argv", ["collecte_depots.py"])
    assert collecte_depots.main() == 1


def test_depots_rendent_0_sans_echec(tmp_path, monkeypatch):
    src = _source(tmp_path, "GitHub")
    monkeypatch.setattr(collecte_depots.cfg, "toutes", lambda p, graphe=None: [src] if p == "GitHub" else [])
    monkeypatch.setattr(collecte_depots, "lire", lambda *a, **k: {"items": []})
    _sans_attente(monkeypatch, collecte_depots)
    monkeypatch.setattr(sys, "argv", ["collecte_depots.py"])
    assert collecte_depots.main() == 0

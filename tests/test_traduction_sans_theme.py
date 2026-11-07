"""La consigne de traduction des libellés nomme le graphe traduit, jamais un thème du moteur (N5, 07.11.2026).

fondre_doublons.traduire disait « un graphe de connaissances sur la gouvernance de l'IA » pour tout graphe : un graphe
d'apiculture recevait la consigne de la première édition. Le libellé du graphe déclaré la remplace.
"""

from __future__ import annotations

from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import fondre_doublons as fd  # noqa: E402


def _consigne(monkeypatch, decl: dict) -> str:
    vus = []
    monkeypatch.setattr(cfg, "graphe_declare", lambda nom: decl)
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr(fd.te, "appeler_claude", lambda prompt, *a, **k: vus.append(prompt) or {"libelles": []})
    d = {"nodes": [{"id": "concept_ruche_paille", "label": "ruche en paille du Moyen Âge"}]}
    fd.traduire("ruchers", d, {"alias": {}, "anglais": {}})
    assert len(vus) == 1
    return vus[0]


def test_la_consigne_nomme_le_libelle_du_graphe(monkeypatch):
    p = _consigne(monkeypatch, {"libelle": "Apiculture médiévale", "domaine": "medieval beekeeping"})
    assert "graphe de connaissances « Apiculture médiévale »" in p
    assert "gouvernance" not in p


def test_sans_libelle_la_consigne_nomme_le_domaine(monkeypatch):
    p = _consigne(monkeypatch, {"domaine": "medieval beekeeping"})
    assert "graphe de connaissances « medieval beekeeping »" in p

"""La première tenue de la pyramide sur le cerveau réancre les empreintes, sans réécrire une synthèse (K3, étape 2,
27.11.2026).

L'empreinte d'une synthèse porte ses preuves, dont la ligne « pourquoi » (la description de la notion) et la fiche
d'origine. Quand plusieurs lots décrivent une notion, la composition et le cerveau n'en gardent pas la même : mesuré
sur 400 synthèses d'ai-governance, l'empreinte de 216 changeait (35 avec l'ancien code, preuves changées depuis). Sans
réancrage, la nuit les aurait toutes réécrites, au modèle, sans qu'une fiche ait changé. La première tenue pose donc
l'empreinte que le cerveau donne, une fois, et le note (`lecture`) ; une synthèse sans preuve reste à réécrire.
"""

from __future__ import annotations

import json

import networkx as nx
import pytest

from newskit import synthese as sy


@pytest.fixture
def pyramide(tmp_path, monkeypatch):
    etat = {
        "notions": {
            "a": {"label": "A", "empreinte": "vieille"},
            "b": {"label": "B", "empreinte": "juste"},
            "c": {"label": "C", "empreinte": "vieille"},
        },
        "echecs": {
            "d": {"empreinte": "vieille", "controle": sy.CONTROLE},
            "e": {"controle": sy.CONTROLE},
            "absente": {"empreinte": "vieille"},
        },
    }
    (tmp_path / "notions.json").write_text(json.dumps(etat), encoding="utf-8")
    G = nx.Graph()
    G.add_nodes_from(["a", "b", "c", "d", "e"])
    monkeypatch.setattr(sy, "dossier", lambda g: tmp_path)
    monkeypatch.setattr(sy, "charger_graphe", lambda g: G)
    monkeypatch.setattr(sy, "preuves_de", lambda G, n, g=None: [] if n == "c" else [{"n": n}])
    monkeypatch.setattr(sy, "empreinte_de", lambda G, n, pr: "juste" if n == "b" else f"neuve-{n}")
    monkeypatch.setattr(sy, "notions_eligibles", lambda G: [])
    monkeypatch.setattr(sy.couts, "en_limite", lambda: True)
    monkeypatch.setattr(sy, "entrelacer_rares", lambda G, notions, neuves, graphe=None: neuves)
    return tmp_path


def test_reancrer_pose_l_empreinte_du_cerveau_et_garde_les_synthese_sans_preuve(pyramide):
    etat = json.loads((pyramide / "notions.json").read_text(encoding="utf-8"))
    assert sy.reancrer(sy.charger_graphe("g"), etat, "g") == 2
    assert {k: v["empreinte"] for k, v in etat["notions"].items()} == {"a": "neuve-a", "b": "juste", "c": "vieille"}
    assert etat["echecs"]["d"]["empreinte"] == "neuve-d" and "empreinte" not in etat["echecs"]["e"]
    assert etat["echecs"]["absente"]["empreinte"] == "vieille" and etat["lecture"] == sy.LECTURE


def test_la_premiere_tenue_reancre_une_fois_et_ne_reecrit_que_le_sans_preuve(pyramide, capsys):
    sy.mettre_a_jour("g", 10, "modele")
    out = capsys.readouterr().out
    etat = json.loads((pyramide / "notions.json").read_text(encoding="utf-8"))
    assert etat["lecture"] == sy.LECTURE and etat["notions"]["a"]["empreinte"] == "neuve-a"
    assert "2 empreinte(s) réancrée(s)" in out and "1 à réécrire" in out
    sy.mettre_a_jour("g", 10, "modele")
    assert "réancrée" not in capsys.readouterr().out

"""`encodeur.py evaluer` sur un graphe fusionné (GV7, 2 novembre 2026).

Un graphe fusionné n'a pas de banc à lui : l'évaluation lisait `banc_questions.json` du graphe et tombait. Elle lit
maintenant les bancs nommés par `--banc` (ceux des graphes d'origine), et sans banc mesure les faits rares seuls.
BM25 découpe les unités une seule fois : sur environ 100 000 unités, le découpage à chaque question coûtait des heures.
"""

from __future__ import annotations

import json
import sys

from newskit import encodeur


def test_les_bancs_nommes_sont_lus(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps([{"question": "q1", "source": "s1", "citation": "c"}, {"question": "sans source"}]))
    b.write_text(json.dumps([{"question": "q2", "source": "s2", "citation": "c"}]))
    qs = encodeur.questions_du_banc("essai", [a, b])
    assert [q["question"] for q in qs] == ["q1", "q2"]


def test_sans_banc_les_faits_rares_seuls(tmp_path, monkeypatch):
    monkeypatch.setattr(encodeur.cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    assert encodeur.questions_du_banc("essai", None) == []


def test_le_banc_du_graphe_par_defaut(tmp_path, monkeypatch):
    (tmp_path / "banc_questions.json").write_text(json.dumps([{"question": "q", "source": "s", "citation": "c"}]))
    monkeypatch.setattr(encodeur.cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    assert [q["question"] for q in encodeur.questions_du_banc("essai", None)] == ["q"]


def test_bm25_indexe_une_fois_rend_les_memes_scores():
    docs = ["le règlement sur l'IA classe les risques", "la nLPD protège les données", "risques et données de l'IA"]
    index = encodeur.Bm25(docs)
    for q in ("risques de l'IA", "données personnelles", "rien"):
        assert index.scores(q) == encodeur.bm25(q, docs)


def test_main_passe_les_bancs(monkeypatch, tmp_path):
    vus = []
    monkeypatch.setattr(encodeur, "evaluer", lambda g, bancs=None: vus.append((g, bancs)))
    monkeypatch.setattr(encodeur.cfg, "nom_actuel", lambda g: g)
    monkeypatch.setattr(sys, "argv", ["encodeur.py", "evaluer", "--graphe", "essai", "--banc", str(tmp_path / "x.json")])
    assert encodeur.main() == 0
    assert vus == [("essai", [tmp_path / "x.json"])]

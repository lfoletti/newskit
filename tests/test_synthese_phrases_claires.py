"""Une phrase de synthèse se lit sans l'original (mainteneur, 14.11.2026). Sur Palantir, gardée : « Un listing d'octobre
2026 décrit Palantir comme le maker de Maven, en partenariat ou en discussions avec une start-up de défense de Fedorov,
selon les termes « reportedly ». » Sa seule preuve était la page d'un épisode de podcast ; la consigne demandait de
recopier les noms à l'identique, et le modèle a recopié aussi des mots anglais ordinaires."""

from __future__ import annotations

import networkx as nx

from newskit import synthese as sy

PALANTIR = (
    "Un listing d'octobre 2026 décrit Palantir comme le maker de Maven, en partenariat ou en discussions avec une "
    "start-up de défense de Fedorov, selon les termes « reportedly »."
)


def test_un_mot_anglais_ordinaire_hors_citation_rend_la_phrase_illisible():
    assert sy.illisible(PALANTIR) == ["anglais : listing", "anglais : maker"]  # « reportedly » est cité
    assert sy.illisible("Le texte serait adopté, reportedly en mai.") == ["anglais : reportedly"]
    assert sy.illisible("Son CEO a décrit une « Artificial Intelligence Platform ».") == []
    assert sy.illisible("Le Department of Defense et le Council of Europe publient.") == []  # des noms
    assert sy.illisible('Il cite "the maker of Maven" et “according to X”.') == []
    assert sy.illisible("Le déréférencement (de-indexing / de-listing) retire une page.") == []  # mot composé


def test_la_consigne_ecarte_une_preuve_qui_ne_decrit_qu_un_document_et_demande_du_francais():
    c = sy.consigne_notions("Gouvernance de l'IA")
    assert "Une preuve qui décrit seulement un document" in c
    assert "Un mot anglais ordinaire se traduit" in c
    assert "seuls les noms, les sigles et les titres restent tels quels" in c


def test_une_phrase_illisible_est_retiree_et_journalisee(monkeypatch, tmp_path):
    G = nx.Graph()
    G.add_node("concept_palantir", label="Palantir")
    preuve = {
        "fiche": "sources/a.md",
        "ligne": 0,
        "texte": "Un listing d'octobre 2026 : Palantir, maker of Maven, reportedly with Fedorov. Palantir sert la défense.",
    }
    monkeypatch.setattr(sy, "preuves_de", lambda G, n, g=None: [preuve])
    monkeypatch.setattr(sy, "matiere_de", lambda G, n, pr: preuve["texte"])
    monkeypatch.setattr(sy.cfg, "graphe_declare", lambda g: {"libelle": "Gouvernance de l'IA"})
    prompts = []
    monkeypatch.setattr(sy, "journal_rejets", lambda g: tmp_path / "rejets.jsonl")
    monkeypatch.setattr(
        sy,
        "appeler",
        lambda p, *a, **k: (
            prompts.append(p)
            or {
                "notions": [
                    {
                        "id": "concept_palantir",
                        "phrases": [
                            {"texte": PALANTIR, "preuves": ["E1"]},
                            {"texte": "Palantir sert la défense.", "preuves": ["E1"]},
                        ],
                    }
                ]
            }
        ),
    )
    r = sy.rediger_notions("g", G, ["concept_palantir"], "m")
    assert [p["texte"] for p in r["notions"]["concept_palantir"]["phrases"]] == ["Palantir sert la défense."]
    assert prompts[0].startswith(sy.consigne_notions("Gouvernance de l'IA"))
    assert r["retirees"] == 1 and "anglais : maker" in (tmp_path / "rejets.jsonl").read_text(encoding="utf-8")

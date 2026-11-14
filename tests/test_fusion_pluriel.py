"""Le singulier et le pluriel d'une notion sont candidats à la fusion sans appel (mainteneur, 14.11.2026 : « pourquoi
hypertrucage est une notion différente de hypertrucages ? », puis « cette étape devrait faire partie de la routine de
fusion »). L'encodeur rapproche « Deepfake » et « Deepfakes » à 0,913, sous le seuil : la paire n'était jamais proposée.
Le modèle tranche ensuite, comme pour les autres règles."""

from __future__ import annotations

import pytest

from newskit import fondre_doublons as fd


@pytest.mark.parametrize(
    ("libelle", "forme"),
    [
        ("Deepfakes", "deepfake"),
        ("Deepfake", "deepfake"),
        ("SMEs", "sme"),
        ("LPDS", "lpds"),  # un sigle en capitales reste tel quel
        ("AI Policies", "ai policy"),
        ("Data Analysis", "data analysis"),
        ("Business Process", "business process"),
        ("Lethal Autonomous Weapon Systems", "lethal autonomous weapon system"),
        ("Bus", "bus"),
        ("Gas", "gas"),  # trois lettres au plus : tel quel
        ("GPT4s", "gpt4s"),  # un nom de produit à chiffre : tel quel
    ],
)
def test_le_singulier_d_un_libelle(libelle, forme):
    assert fd.singulier(libelle) == forme


def _graphe():
    noeuds = [
        ("concept_deepfake", "Deepfake"),
        ("concept_deepfakes", "Deepfakes"),
        ("concept_lpd", "LPD"),
        ("concept_lpds", "LPDS"),
        ("concept_ai_policy", "AI Policy"),
        ("concept_ai_policies", "AI policies"),
        ("concept_trilogue", "Trilogue"),
        ("concept_trilogues", "Trilogues"),
        ("concept_sme", "SME"),
        ("concept_smes", "SMEs"),
        ("concept_casse", "AI policy"),  # la casse seule : candidate aussi
    ]
    return {
        "nodes": [{"id": i, "label": l} for i, l in noeuds],
        "links": [{"source": "concept_deepfake", "target": "concept_ai_policy"}],
    }


def test_le_pluriel_est_candidat_sans_l_encodeur(monkeypatch):
    from newskit import synthese

    def sans_encodeur():
        raise RuntimeError("pas d'encodeur dans le test")

    monkeypatch.setattr(synthese, "encodeur", sans_encodeur)
    memo = {"alias": {"concept_sme": "concept_x"}, "refuses": ["concept_trilogue|concept_trilogues"]}
    paires = fd.candidats("g", _graphe(), memo)
    assert sorted((tuple(sorted(p[:2])), p[3]) for p in paires) == [
        (("concept_ai_policies", "concept_ai_policy"), "pluriel"),
        (("concept_ai_policies", "concept_casse"), "pluriel"),
        (("concept_ai_policy", "concept_casse"), "pluriel"),
        (("concept_deepfake", "concept_deepfakes"), "pluriel"),
    ]
    # LPD/LPDS : deux sigles ; Trilogue(s) : déjà refusée ; SME(s) : SME est déjà l'alias d'une autre notion
    assert all(p[2] == 1.0 for p in paires)

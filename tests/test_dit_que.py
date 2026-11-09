"""« dit que » est interdit, titre et texte ; « dit » reste permis devant une citation directe (signalement du 9.11.2026)."""

import pytest

from newskit import fil_anglais as fa
from newskit import lettre

TEXTE_EN = (
    "The European Commission published guidance on general-purpose AI models on 2 November 2026. "
    "It sets out how providers document training data and report serious incidents to the AI Office."
)


@pytest.mark.parametrize(
    "texte",
    [
        "La ministre dit que la loi entrera en vigueur en 2027.",
        "Les auteurs disent que le modèle se trompe une fois sur trois.",
        "Le régulateur a dit qu'il ouvrirait une enquête.",
        "Les entreprises ont dit que les règles coûtent cher.",
        "Le juge avait dit que la plainte était recevable.",
        "Les avocats disaient que l'accord tiendrait.",
        "Pour la CNIL, rien ne change. Elle DIT QUE le texte suffit.",
    ],
)
def test_dit_que_est_refuse(texte):
    assert lettre.preuve(texte) is not None
    assert "dit" in lettre.preuve(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "La ministre dit : « La loi entrera en vigueur en 2027. »",
        "Selon la ministre, la loi entrera en vigueur en 2027.",
        "D'après les auteurs, le modèle se trompe une fois sur trois.",
        "Le témoin cité rapporte : « Il dit que tout va bien. »",
        "Ce qui a été dit quelque part ne compte pas.",
        "Le contredit que la société a déposé est rejeté.",
    ],
)
def test_dit_devant_une_citation_ou_hors_que_passe(texte):
    assert lettre.preuve(texte) is None


@pytest.mark.parametrize(
    "titre,texte",
    [
        ("The Commission says that AI rules apply in 2027", TEXTE_EN),
        ("Authors sue Meta over AI training", TEXTE_EN.replace("It sets out", "It said that it sets out")),
        ("Authors sue Meta over AI training", TEXTE_EN.replace("It sets out", "Officials say that it sets out")),
    ],
)
def test_says_that_est_refuse_en_anglais(titre, texte):
    assert any("says that" in e for e in fa.forme(titre, texte))


@pytest.mark.parametrize(
    "titre,texte",
    [
        ("According to the Commission, AI rules apply in 2027", TEXTE_EN),
        ("Authors sue Meta over AI training", TEXTE_EN.replace("It sets out", 'It said: "We set out')),
        ("Authors sue Meta over AI training", TEXTE_EN.replace("It sets out", '"Officials say that" sets out')),
        ("Authors sue Meta over AI training", TEXTE_EN.replace("It sets out", "The essay that sets out")),
    ],
)
def test_said_devant_une_citation_passe_en_anglais(titre, texte):
    assert not any("says that" in e for e in fa.forme(titre, texte))

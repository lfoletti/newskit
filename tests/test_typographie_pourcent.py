"""Pas d'espace ordinaire devant « % » (mainteneur, 09.11.2026 : « pas d'espace devant %, ou alors espace insécable »)."""

from __future__ import annotations

import pytest

from newskit import lettre as lt

NB = " "


@pytest.mark.parametrize("langue", ["fr", "en"])
@pytest.mark.parametrize("espace", [" ", " ", "  "])
def test_l_espace_devant_le_pourcentage_devient_insecable(langue, espace):
    assert lt.typographie(f"11,2{espace}% des entreprises", langue) == f"11,2{NB}% des entreprises"


@pytest.mark.parametrize("texte", ["11,2% des entreprises", f"11,2{NB}% des entreprises", "un taux de % seul"])
def test_sans_espace_ordinaire_rien_ne_change(texte):
    assert lt.typographie(texte, "en") == texte


def test_la_page_publiee_passe_aussi_le_pourcentage():
    assert lt.page_typographiee("<body><p>70 % des sites</p></body>", "fr") == f"<body><p>70{NB}% des sites</p></body>"

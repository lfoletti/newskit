"""Dans un titre, l'attribution vient en tête (Lauro, 8.11.2026)."""

from newskit import fil_anglais as fa
from newskit import lettre as lt


def test_le_titre_met_l_attribution_en_tete():
    assert "Pour le scientifique en chef IA de Tencent, l'IA pourra" in lt.REGLES
    assert "jamais « X dit que … »" in lt.REGLES


def test_l_exemple_du_chercheur_suit_la_regle():
    assert "Un chercheur affirme" not in lt.REGLES and "Pour un chercheur, …" in lt.REGLES


def test_la_regle_vaut_pour_le_titre_anglais():
    for regles in (lt.REGLES, fa.REGLES):
        assert "For Tencent's chief AI scientist, AI could" in regles
        assert "never « X says … »" in regles

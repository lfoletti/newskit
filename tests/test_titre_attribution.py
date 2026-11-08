"""Dans un titre, l'attribution vient en tête (Lauro, 8.11.2026)."""

from newskit import fil_anglais as fa
from newskit import lettre as lt


def test_le_titre_met_l_attribution_en_tete():
    assert "Pour le scientifique en chef IA de Tencent, l'IA pourra" in lt.REGLES
    assert "Jamais « X dit que … »" in lt.REGLES


def test_l_exemple_du_chercheur_suit_la_regle():
    assert "Un chercheur affirme" not in lt.REGLES and "Pour un chercheur, …" in lt.REGLES


def test_la_regle_vaut_pour_le_titre_anglais():
    for regles in (lt.REGLES, fa.REGLES):
        assert "For Tencent's chief AI scientist, AI could" in regles
        assert "never « X says … »" in regles


def test_trois_formes_d_attribution_dans_le_titre():
    assert "Anthropic aurait franchi son seuil" in lt.REGLES
    assert "D'après X, …" in lt.REGLES
    assert "Anthropic may have crossed" in fa.REGLES


def test_selon_ne_sert_plus_d_exemple_de_titre():
    assert "« Selon X, … ») ;" not in lt.REGLES
    assert "« Selon Nom (Institution), … »" not in lt.REGLES and "« D'après Nom (Institution), … »" in lt.REGLES


def test_le_conditionnel_est_permis_dans_un_titre_pour_un_fait_non_etabli():
    assert "dans un titre, pour un fait avancé par une seule source et non établi" in lt.REGLES
    assert "in a title, for a fact put forward by one source and not established" in fa.REGLES

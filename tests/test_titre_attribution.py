"""Un titre ne porte pas d'attribution par défaut : il dit le fait, la source va dans la première phrase (mainteneur,
9.11.2026 : « trop de titres commencent par "d'après" ou "pour" », « dans bien des cas, c'est une information
secondaire », puis « ne rends pas l'interdiction absolue, parfois un "selon" se justifie aussi en titre ») : le conditionnel
pour un fait non établi ; « Selon X » ou « Pour X » en tête quand X fait la nouvelle."""

from newskit import fil_anglais as fa
from newskit import lettre as lt


def test_la_regle_ne_met_plus_l_attribution_dans_le_titre():
    assert "Attribution dans le titre : le plus souvent, aucune." in lt.REGLES
    assert "« Selon X, … » ou « Pour X, … » en tête seulement quand X fait la nouvelle" in lt.REGLES
    assert "jamais pour un inconnu, ni par habitude" in lt.REGLES
    assert "« D'après X, … » pour un fait ou un chiffre que X revendique" not in lt.REGLES
    assert "Anthropic aurait franchi son seuil" in lt.REGLES
    assert "Jamais « X dit que … »" in lt.REGLES


def test_la_regle_anglaise_suit():
    assert "Attribution in a title: mostly none." in fa.REGLES
    assert "at the start only when X is the news" in fa.REGLES
    assert "« According to X, … » for a fact X claims" not in fa.REGLES
    assert "Anthropic may have crossed" in fa.REGLES


def test_les_invites_ne_demandent_plus_le_nom_dans_le_titre():
    assert "it names the person the source names" not in fa.REDACTION
    assert "le titre nomme la personne que la source nomme" not in lt.REGLES

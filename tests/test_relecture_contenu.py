"""Le relecteur refuse un texte dont la première phrase ne dit que la parution ou l'intervention (Lauro, 8.11.2026)."""

from newskit import fil
from newskit import fil_anglais as fa


def test_le_relecteur_refuse_une_premiere_phrase_sans_contenu():
    assert "la première phrase du texte dit seulement" in fil.RELECTEUR
    assert "le lieu, le format ou les intervenants" in fil.RELECTEUR


def test_le_relecteur_anglais_applique_la_meme_regle():
    assert "the first sentence of the text only says" in fa.RELECTURE
    assert "the venue, the format or the speakers" in fa.RELECTURE


def test_les_consignes_restent_formatables():
    assert "la première phrase" in fil.RELECTEUR.format(nom="n", billets="b")
    assert "the first sentence" in fa.RELECTURE.format(nom="n", billets="b")

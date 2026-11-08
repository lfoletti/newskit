"""La limite de mots est la même partout et la première phrase porte le contenu (Lauro, 8.11.2026)."""

import re

from newskit import fil_anglais as fa
from newskit import fil_fiche as ff
from newskit import lettre as lt


def test_une_seule_limite_de_30_mots():
    assert lt.MOTS_PAR_PHRASE == ff.MOTS_PAR_PHRASE == fa.MOTS_PAR_PHRASE == 30


def test_les_gabarits_anglais_ne_demandent_plus_qui_ou_quoi():
    for gabarit in (fa.REDACTION, fa.REECRITURE):
        assert not re.search(r"who,\s+where,\s+what", gabarit)
        assert re.search(r"what\s+the\s+source\s+establishes\s+or\s+argues", gabarit)


def test_les_regles_du_paquet_annoncent_la_limite():
    assert "30 mots au plus par phrase" in lt.REGLES and "30 words at most per sentence" in fa.REGLES

"""Un sigle développé selon son sens établi se lit dans la source (mainteneur, 18.11.2026 : « oui ok »).

Le 18.11.2026, une fiche sur un accord politique chilien a été refusée 3 fois, et le billet retiré : la source écrivait
« PS », la fiche « Parti socialiste », et le relecteur l'a compté comme absent de la source. Le relecteur lisait déjà un
nom traduit dans la source ; il lit aussi un sigle développé selon le sens qu'il a dans le pays et le domaine de la
source.
"""

from __future__ import annotations

from newskit import fil
from newskit import fil_anglais as fa


def test_le_relecteur_francais_lit_un_sigle_developpe_dans_la_source():
    assert "Un sigle développé selon son sens établi" in fil.RELECTEUR


def test_le_relecteur_anglais_lit_un_sigle_developpe_dans_la_source():
    assert "An acronym spelled out in its established meaning" in fa.RELECTURE

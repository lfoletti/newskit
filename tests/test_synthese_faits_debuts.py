"""`synthese.faits` : un mot courant en tête de phrase n'est pas un nom propre (04.11.2026, éclairages NCSC et AI Act :
« Au-delà de 14 jours » retiré, « Seul » refusé)."""

from newskit import synthese as sy


def test_un_mot_compose_en_tete_de_phrase_n_est_pas_un_fait():
    assert "au-dela" not in sy.faits("Au-delà de 14 jours, la décision se prend à un niveau approprié [6].")


def test_un_mot_compose_en_tete_de_seconde_phrase_n_est_pas_un_fait():
    assert "peut-etre" not in sy.faits("La Commission répond. Peut-être le Conseil suivra-t-il.")


def test_seul_en_tete_de_seconde_phrase_n_est_pas_un_fait():
    assert "seul" not in sy.faits("La Commission publie ses demandes. Seul le Parlement les conteste.")
    assert "seules" not in sy.faits("La Commission publie ses demandes. Seules deux agences répondent.")


def test_un_nom_propre_en_tete_de_seconde_phrase_reste_un_fait():
    assert "odgers" in sy.faits("Le rapport paraît. Candice Odgers le conteste.")
    assert "candice" in sy.faits("Le rapport paraît. Candice Odgers le conteste.")

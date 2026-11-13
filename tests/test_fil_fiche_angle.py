"""La fiche d'une suite garde son angle (mainteneur, 13.11.2026 : « un fait nouveau peut tout à fait justifier une
nouvelle fiche, à condition d'en faire l'angle de la fiche, et de la titrer adéquatement »).

Le 13.11.2026, le billet Fortune sur Nvidia, réécrit par la relecture sur son fait neuf (« Colette Kress et Harvey Jones
dirigent Nvidia après la démission de Jensen Huang »), a reçu sa fiche : la fiche, qui ne connaissait pas le fait neuf,
a donné au billet son titre (« Nvidia confie sa direction à sa directrice financière et au président de son conseil ») et
un chapeau ouvert sur la démission. L'angle était perdu.
"""

from __future__ import annotations

import json

from newskit import fil_fiche as ff
import test_fil_fiche_elements as tfe

banc = tfe.banc  # le banc des fiches
billet, reponse = tfe.billet, tfe.reponse

DEV = "Colette Kress et Harvey Jones dirigent Nvidia."


def suite():
    return {
        **billet(),
        "titre": "Colette Kress et Harvey Jones dirigent Nvidia",
        "descriptif": "Harvey Jones préside.",
        "suite_de": "s/a.md",
        "developpement": DEV,
        "neuf": ["harvey", "jones"],
    }


ANGLE = {
    "titre": "Harvey Jones et Colette Kress dirigent Nvidia",
    "titre_en": "Harvey Jones and Colette Kress run Nvidia",
    "resume": "Harvey Jones préside le conseil de Nvidia. Huang est parti.",
    "resume_en": "Harvey Jones chairs the Nvidia board. Huang left.",
}
SANS_ANGLE = {
    "titre": "Nvidia confie sa direction à sa directrice financière",
    "titre_en": "Nvidia hands its leadership to its finance chief",
    "resume": "Jensen Huang a démissionné. Harvey Jones préside le conseil.",
    "resume_en": "Jensen Huang resigned. Harvey Jones chairs the board.",
}


def test_la_fiche_d_une_suite_qui_garde_l_angle_donne_son_titre_au_billet(banc):
    banc["reponses"] = [reponse(**ANGLE)]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is True and b["titre"] == ANGLE["titre"]


def test_la_fiche_d_une_suite_qui_perd_l_angle_est_refusee_et_le_billet_garde_son_titre(banc):
    banc["reponses"] = [reponse(**SANS_ANGLE)]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False and b["titre"] == "Colette Kress et Harvey Jones dirigent Nvidia"
    assert any("fait neuf" in r for r in b["fiche"]["reproches"])


def test_le_titre_seul_qui_perd_l_angle_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(**{**ANGLE, "titre": SANS_ANGLE["titre"]})]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False


def test_la_premiere_phrase_qui_perd_l_angle_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(**{**ANGLE, "resume": SANS_ANGLE["resume"]})]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False


def test_la_version_anglaise_qui_perd_l_angle_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(**{**ANGLE, "titre_en": SANS_ANGLE["titre_en"]})]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False
    banc["reponses"] = [reponse(**{**ANGLE, "resume_en": SANS_ANGLE["resume_en"]})]
    b = suite()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False


def test_un_billet_qui_n_est_pas_une_suite_prend_le_titre_de_sa_fiche(banc):
    banc["reponses"] = [reponse(**SANS_ANGLE)]
    b = {**suite(), "suite_de": ""}
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is True and b["titre"] == SANS_ANGLE["titre"]


def test_la_redaction_de_la_fiche_recoit_le_fait_neuf_d_une_suite(banc):
    banc["reponses"] = [reponse(**ANGLE)]
    ff.etoffer({"billets": [suite()]}, "g", 20)
    (x,) = [json.loads(ligne) for ligne in banc["invite"].split("\n") if ligne.startswith('{"n"')]
    assert x["developpement"] == DEV
    banc["reponses"] = [reponse(**ANGLE)]
    ff.etoffer({"billets": [{**billet(), "developpement": DEV}]}, "g", 20)  # sans suite_de : pas un fait neuf
    (x,) = [json.loads(ligne) for ligne in banc["invite"].split("\n") if ligne.startswith('{"n"')]
    assert "developpement" not in x

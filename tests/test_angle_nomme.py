"""Le reproche d'angle nomme ce que le titre doit dire (13.11.2026).

Le billet Fortune sur Nvidia, une suite dont le fait neuf tient en deux mots (« harvey », « jones »), a épuisé ses trois
essais de fiche et sa version anglaise : le reproche disait « le titre et la première phrase ne disent pas le fait
neuf : Colette Kress … et Harvey Jones … », sans dire lequel des noms manquait. Le rédacteur gardait Colette Kress,
déjà dite par la nouvelle d'avant. Le reproche nomme maintenant les mots attendus, dans leur forme d'origine.
"""

from __future__ import annotations

from newskit import fil, fil_anglais, fil_fiche

DEV = (
    "Colette Kress, responsable des finances, et Harvey Jones, premier président du conseil d'administration, "
    "dirigent Nvidia après la démission de Jensen Huang."
)


def suite(**k):
    return {"suite_de": "s/a.md", "developpement": DEV, "neuf": ["harvey", "jones"], **k}


def test_les_mots_du_fait_neuf_gardent_leur_forme_et_leur_suite():
    assert fil.a_nommer(suite()) == ["Harvey Jones"]


def test_des_mots_neufs_separes_se_nomment_chacun():
    b = suite(
        developpement="Colette Kress dirige Nvidia ; Harvey Jones préside, avec 13 % des voix.",
        neuf=["#13%", "jones", "kress"],
    )
    assert fil.a_nommer(b) == ["Kress", "Jones", "13 %"]


def test_un_nom_redit_se_nomme_une_fois():
    b = suite(developpement="Harvey Jones préside. Harvey Jones dirige.")
    assert fil.a_nommer(b) == ["Harvey Jones"]


def test_un_mot_neuf_absent_du_developpement_se_nomme_tel_quel():
    assert fil.a_nommer(suite(developpement="", neuf=["harvey"])) == ["harvey"]


def test_un_billet_qui_n_est_pas_une_suite_n_a_rien_a_nommer():
    assert fil.a_nommer({"developpement": DEV, "neuf": ["harvey"]}) == []


def test_le_reproche_de_la_relecture_nomme_le_fait_neuf():
    r = fil.reproche_angle(suite(), "fr")
    assert "Harvey Jones" in r.split(":")[0] and r.startswith("le titre et la première phrase ne nomment pas")


def test_le_reproche_de_la_fiche_nomme_le_fait_neuf():
    b = suite()
    f = {"titre": "Colette Kress dirige Nvidia", "resume": "Colette Kress dirige.", "en": {"titre": "x", "resume": "y"}}
    (r,) = fil_fiche.angle_perdu(b, f)
    assert r == fil.reproche_angle(b, "fr")


def test_le_reproche_anglais_nomme_le_fait_neuf_en_anglais():
    (r,) = fil_anglais._angle(suite(), "Colette Kress runs Nvidia", "Colette Kress runs Nvidia.")
    assert r == fil.reproche_angle(suite(), "en") and r.startswith("the title and the first sentence must name")
    assert "Harvey Jones" in r

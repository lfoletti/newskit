"""Un billet a la même forme en français et en anglais (mainteneur, 09.11.2026, chantier I1).

La copie publique montre la fiche développée seulement quand elle existe dans les deux langues ; sinon le billet paraît
en brève dans les deux langues. Un billet sans version anglaise tenue ne paraît pas."""

from __future__ import annotations

import pytest

from newskit import publier_fil

EN = {"titre": "TE", "texte": "DE", "tenu": True}
ELEMENTS = {
    "texte": "x",
    "tenu": True,
    "resume": "R",
    "categorie": "C",
    "elements": [{"champ": "A", "texte": "B", "citation": "c"}],
    "en": {"resume": "RE", "categorie": "CE", "elements": [{"champ": "AE", "texte": "BE"}]},
}


@pytest.fixture(autouse=True)
def sans_liens(monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})


def billet(chemin="c.md", **autre):
    return {"titre": "T", "descriptif": "D", "chemin": chemin, "rubrique": "Conformité", "url": chemin, **autre}


def publies(*billets):
    return publier_fil.public({"billets": list(billets)})["billets"]


@pytest.mark.parametrize("en", [None, {"tenu": False, "reproches": ["r"]}, {"titre": "TE", "texte": "", "tenu": True}])
def test_un_billet_sans_version_anglaise_tenue_ne_parait_pas(en):
    b = billet(fiche=ELEMENTS) if en is None else billet(fiche=ELEMENTS, en=en)
    assert publies(b) == []


def test_une_fiche_dans_les_deux_langues_parait_developpee():
    (p,) = publies(billet(fiche=ELEMENTS, en=EN))
    assert p["resume"] == "R" and p["resume_en"] == "RE" and p["fiche"] == ""
    assert p["elements"] == [{"champ": "A", "texte": "B"}] and p["elements_en"] == [{"champ": "AE", "texte": "BE"}]


def test_une_fiche_ancienne_en_francais_seul_parait_en_breve_dans_les_deux_langues():
    (p,) = publies(billet(fiche={"texte": "Texte long en français.", "tenu": True}, en=EN))
    assert p["fiche"] == "" and p["resume"] == "" and p["resume_en"] == ""
    assert (p["titre"], p["descriptif"], p["titre_en"], p["descriptif_en"]) == ("T", "D", "TE", "DE")


def test_une_fiche_sans_elements_anglais_parait_en_breve_dans_les_deux_langues():
    fiche = {**ELEMENTS, "en": {"resume": "", "categorie": "CE", "elements": []}}
    (p,) = publies(billet(fiche=fiche, en=EN))
    assert p["fiche"] == "" and p["resume"] == "" and p["elements"] == [] and p["elements_en"] == []


def test_une_fiche_refusee_parait_en_breve_dans_les_deux_langues():
    (p,) = publies(billet(fiche={"tenu": False, "reproches": ["r"], "essais": 3}, en=EN))
    assert p["fiche"] == "" and p["resume"] == "" and p["resume_en"] == ""
    assert (p["titre_en"], p["descriptif_en"]) == ("TE", "DE")


def test_les_billets_ecartes_ne_comptent_pas_dans_la_copie_publique(monkeypatch):
    monkeypatch.setattr(publier_fil, "MAX", 2)
    p = publies(billet("a.md"), billet("b.md", en=EN), billet("c.md", en=EN), billet("d.md", en=EN))
    assert [x["url"] for x in p] == ["b.md", "c.md"]

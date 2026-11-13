"""Le passage du fil révise les titres des billets neufs tenus, après la relecture et avant le complément."""

from newskit import fil, fil_fiche


def test_le_passage_revise_les_titres_apres_la_relecture(monkeypatch):
    ordre, F = [], {"billets": [], "pile": [], "vues": []}
    fiche = {"chemin": "f1", "url": "u", "titre": "t", "date": "2026-11-09"}
    rien = lambda *a, **k: []  # noqa: E731
    zero = lambda *a, **k: 0.0  # noqa: E731
    for nom, valeur in {
        "lire": lambda g: F,
        "poser_langues": rien,
        "classer": lambda b: b,
        "poser_versions": rien,
        "paraitre": lambda g, f: 0,
        "candidates": lambda g, v, j: [fiche],
        "a_relire": rien,
        "a_lier": rien,
        "a_definir": rien,
        "billets_anglais_a_ecrire": rien,
        "plafond": lambda j, g: 10.0,
        "depense_du_jour": lambda f, j: 0.0,
        "textes_du_registre": lambda f: {},
        "proposer": lambda *a: None,
        "rediger": lambda lot, r, g: ([{"n": 0, "pertinent": True, "texte": "x", "titre": "T"}], 0.0),
        "rubrique_finale": lambda f, r, rubs: "",
        "langue_source": lambda f: "fr",
        "maintenant": lambda: "now",
        "relire": lambda b, g, F=None: ordre.append("relire") or 0.0,
        "reviser_titres": lambda b, g: ordre.append(("reviser", [x["chemin"] for x in b])) or 0.5,
        "completer": lambda b, g, r: ordre.append("completer") or 0.0,
        "enrichir": lambda b: None,
        "definir": zero,
        "lier": zero,
        "fil_anglais": lambda: type("A", (), {"anglais": staticmethod(zero)}),
        "ecrire": lambda g, f: None,
    }.items():
        monkeypatch.setattr(fil, nom, valeur)
    monkeypatch.setattr(fil.nv, "rattacher", lambda f, c: [])
    monkeypatch.setattr(fil.nv, "toutes", rien)
    monkeypatch.setattr(fil.couts, "en_limite", lambda: False)
    monkeypatch.setattr(fil.cfg, "rubriques", rien)
    monkeypatch.setattr(fil.lt, "_source", lambda f: "s")
    monkeypatch.setattr(fil_fiche, "a_ecrire", rien)
    monkeypatch.setattr(fil_fiche, "etoffer", zero)
    fil.passer("g")
    assert ordre == ["relire", ("reviser", ["f1"]), "completer"]
    assert list(F["depense"].values()) == [0.5]  # le coût de la révision compte dans la dépense du jour

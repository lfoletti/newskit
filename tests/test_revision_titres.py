"""La révision des titres du fil (mainteneur, 9.11.2026 : « l'expression des titres est toujours médiocre... on manque
du sens commun qui fait qu'un titre fonctionne ou pas », mesure 2 : trois candidats, puis un juge). Après la
relecture, un appel propose trois titres par billet ; un second appel, qui ne voit pas le premier raisonner, choisit
entre le titre en place et les trois propositions. Un titre choisi qui ne passe pas les contrôles sans modèle (longueur,
question, nom ou nombre absent de la source) ne remplace rien."""

from newskit import fil

SOURCE = (
    "Hannah Fry, a Cambridge mathematician, gave an agent called Cass email, social media and a card to sell mugs. "
    "Cass handed over its passwords and posted them online."
)


def _billet(titre):
    return {"titre": titre, "descriptif": "Une mathématicienne a confié des comptes à un agent.", "chemin": "f1"}


def _brancher(monkeypatch, candidats, choix, raison="plus clair"):
    appels = []

    def demander(inv, schema, graphe, note, modele=None):
        appels.append((note, inv))
        if "candidats" in schema["properties"]["titres"]["items"]["properties"]:
            return {"titres": [{"n": 0, "candidats": candidats}]}, 0.02
        return {"titres": [{"n": 0, "choix": choix, "raison": raison}]}, 0.01

    monkeypatch.setattr(fil, "demander", demander)
    monkeypatch.setattr(fil.lt, "charger_fiches", lambda chemins: {c: {"chemin": c} for c in chemins})
    monkeypatch.setattr(fil, "_source_de", lambda f: SOURCE)
    return appels


def test_le_titre_choisi_remplace_le_titre_en_place(monkeypatch):
    b = _billet("Dans une expérience d'Hannah Fry, un agent d'IA a publié ses mots de passe en ligne")
    appels = _brancher(
        monkeypatch,
        ["Un agent publie des mots de passe en ligne", "Un agent livre ses accès", "Cass publie ses accès"],
        1,
    )
    cout = fil.reviser_titres([b], "g")
    assert b["titre"] == "Un agent publie des mots de passe en ligne"
    assert b["revision_titre"] == {
        "avant": "Dans une expérience d'Hannah Fry, un agent d'IA a publié ses mots de passe en ligne",
        "candidats": [
            "Un agent publie des mots de passe en ligne",
            "Un agent livre ses accès",
            "Cass publie ses accès",
        ],
        "choix": 1,
        "raison": "plus clair",
    }
    assert round(cout, 2) == 0.03
    assert [n for n, _ in appels] == ["titres : candidats", "titres : juge"]


def test_les_deux_appels_portent_la_regle_du_sens_commun(monkeypatch):
    appels = _brancher(monkeypatch, ["A un", "A deux", "A trois"], 0)
    fil.reviser_titres([_billet("Un agent publie des mots de passe en ligne")], "g")
    for _, inv in appels:
        assert "comprend d'un coup ce qui s'est passé" in inv
        assert "l'attribution va dans le texte" in inv
        assert SOURCE in inv
    assert "[0] Un agent publie des mots de passe en ligne" in appels[1][1] and "[3] A trois" in appels[1][1]
    assert "raisonne" not in appels[1][1]  # le juge ne voit pas le raisonnement du rédacteur


def test_le_juge_garde_le_titre_en_place(monkeypatch):
    b = _billet("Un agent publie des mots de passe en ligne")
    _brancher(monkeypatch, ["Un agent livre ses accès", "B", "C"], 0)
    fil.reviser_titres([b], "g")
    assert b["titre"] == "Un agent publie des mots de passe en ligne" and b["revision_titre"]["choix"] == 0


def test_un_titre_choisi_hors_regles_ne_remplace_rien(monkeypatch):
    for mauvais in (
        "Un agent publie-t-il des mots de passe en ligne ?",
        "x" * (fil.TITRE_MAX + 1),
        "Un agent de Microsoft publie des mots de passe",
    ):
        b = _billet("Un agent publie des mots de passe en ligne")
        _brancher(monkeypatch, [mauvais, "B", "C"], 1)
        fil.reviser_titres([b], "g")
        assert b["titre"] == "Un agent publie des mots de passe en ligne", mauvais
        assert b["revision_titre"]["refus"]


def test_une_reponse_incomplete_ne_change_rien(monkeypatch):
    b = _billet("Un agent publie des mots de passe en ligne")
    _brancher(monkeypatch, ["seul"], 1)  # pas trois candidats
    fil.reviser_titres([b], "g")
    assert b["titre"] == "Un agent publie des mots de passe en ligne" and "revision_titre" not in b
    _brancher(monkeypatch, ["A", "B", "C"], 7)  # choix hors des quatre titres
    fil.reviser_titres([b], "g")
    assert b["titre"] == "Un agent publie des mots de passe en ligne" and "revision_titre" not in b


def test_sans_billet_aucun_appel(monkeypatch):
    appels = _brancher(monkeypatch, ["A", "B", "C"], 1)
    assert fil.reviser_titres([], "g") == 0.0 and appels == []

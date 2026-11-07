"""Le collecteur Medium : de l'état Apollo d'une page à une fiche, sans réseau.

Les formes viennent des pages réelles reçues le 27.09.2026 (cache/medium/,
hors dépôt) : un Post dont le contenu référence ses paragraphes dans l'ordre,
des paragraphes typés, des enrichissements en positions UTF-16.
"""

from newskit import collecte_medium as m


def _etat():
    return {
        "Post:abc123def456": {
            "__typename": "Post", "id": "abc123def456", "title": "Jev Clearly Explained",
            "previewContent": {"subtitle": "A number instead of a paragraph"},
            "creator": {"__ref": "User:u1"}, "collection": {"__ref": "Collection:c1"},
            "firstPublishedAt": 1790015076297, "isLocked": True, "wordCount": 30,
            "mediumUrl": "https://levelup.gitconnected.com/jev-clearly-explained-abc123def456",
            "tags": [{"__ref": "Tag:ai"}, {"__ref": "Tag:data-science"}],
            'content({"postMeteringOptions":{"referrer":""}})': {
                "isLockedPreviewOnly": False,
                "bodyModel": {"paragraphs": [{"__ref": f"Paragraph:v_{i}"} for i in range(8)]},
            },
        },
        "Post:autre000000": {"__typename": "Post", "id": "autre000000", "title": "Un article voisin"},
        "User:u1": {"name": "Fareed Khan", "username": "fareedkhandev"},
        "Collection:c1": {"name": "Level Up Coding", "domain": "levelup.gitconnected.com"},
        "Paragraph:v_0": {"type": "H3", "text": "Jev Clearly Explained", "markups": []},
        "Paragraph:v_1": {"type": "P", "text": "Read this story for free: link",
                          "markups": [{"type": "A", "start": 26, "end": 30, "href": "https://x.test/free"}]},
        "Paragraph:v_2": {"type": "H3", "text": "The problem", "markups": []},
        "Paragraph:v_3": {"type": "P", "text": "Token 😀 bold here",
                          "markups": [{"type": "STRONG", "start": 9, "end": 13}]},
        "Paragraph:v_4": {"type": "PRE", "text": "x = 1\nprint(x)", "codeBlockMetadata": {"lang": "python"}, "markups": []},
        "Paragraph:v_5": {"type": "ULI", "text": "un", "markups": []},
        "Paragraph:v_6": {"type": "ULI", "text": "deux", "markups": []},
        "Paragraph:v_7": {"type": "IMG", "text": "Une légende", "markups": []},
    }


def test_l_article_est_lu_par_son_identifiant_pas_par_le_premier_post():
    art = m.article_depuis_etat(_etat(), "abc123def456")
    assert art["titre"] == "Jev Clearly Explained"
    assert art["auteur"] == "Fareed Khan" and art["publication"] == "Level Up Coding"
    assert art["date"] == "2026-09-21" and art["reserve"] and not art["apercu_seul"]
    assert art["tags"] == ["ai", "data-science"] and len(art["paragraphes"]) == 8
    assert m.article_depuis_etat(_etat(), "inconnu") is None


def test_le_corps_respecte_types_et_ordre_et_saute_le_titre_repete():
    art = m.article_depuis_etat(_etat(), "abc123def456")
    corps = m.paragraphes_en_markdown(art["paragraphes"], art["titre"])
    assert not corps.startswith("## Jev")                       # le titre est déjà en tête de fiche
    assert "Read this story for free: [link](https://x.test/free)" in corps
    assert "## The problem" in corps
    assert "Token 😀 **bold** here" in corps                    # positions UTF-16 : l'émoji compte deux
    assert "```python\nx = 1\nprint(x)\n```" in corps
    assert "- un\n- deux" in corps
    assert "*[image : Une légende]*" in corps


def test_les_enrichissements_imbriques_ne_cassent_pas_le_texte():
    t = "voir le code ici"
    out = m.appliquer_markups(t, [{"type": "A", "start": 5, "end": 16, "href": "https://x.test"},
                                  {"type": "CODE", "start": 8, "end": 12}])
    assert out == "voir [le `code` ici](https://x.test)"
    assert m.appliquer_markups(t, [{"type": "A", "start": 5, "end": 99, "href": "u"}]) == t   # hors bornes : ignoré


def test_le_filtre_lit_titre_publication_et_etiquettes():
    assert not m.retenu({"titre": "Ethereum receipts", "publication": "Coinmonks", "tags": ["ethereum"]}, ["AI", "model"])
    assert m.retenu({"titre": "Jev Clearly Explained", "publication": "Level Up Coding", "tags": ["ai"]}, ["AI"])
    assert not m.retenu({"titre": "Maintain your sanity", "publication": "", "tags": []}, ["AI"])  # « ai » dans un mot ne compte pas
    assert m.retenu({"titre": "quoi que ce soit", "publication": "", "tags": []}, [])       # sans mots-clés, tout passe


def test_l_identifiant_se_lit_en_fin_de_chemin():
    assert m.identifiant("https://medium.com/@x/how-to-abeab8b8d084") == "abeab8b8d084"
    assert m.identifiant("https://levelup.gitconnected.com/jev-abc123def456?gi=1") == "abc123def456"
    assert m.identifiant("https://medium.com/me/lists/reading-history") == ""

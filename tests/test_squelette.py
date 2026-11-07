"""Le squeletteur : les fonctions pures qui fabriquent identifiants et arêtes.

Chacune de ces fonctions décide de ce qui devient un nœud. Une régression sur
`canon_arxiv` ou `nettoyer` ne casse rien : elle change le graphe en silence,
et le contrôle de fraîcheur ne la voit pas. D'où des tests serrés.
"""

import pytest

from newskit import extraire_squelette as sq


# --- identifiants ----------------------------------------------------------


def test_normalize_id_est_idempotent_et_reproduit_graphify():
    from graphify.ids import normalize_id

    for s in ("Hello World", "é_à--x", "  Déjà vu!  ", "a__b"):
        assert sq.normalize_id(s) == normalize_id(s)
        assert sq.normalize_id(sq.normalize_id(s)) == sq.normalize_id(s)


def test_make_id_compose_et_ignore_les_vides():
    assert sq.make_id("auteur", "Ada Lovelace") == "auteur_ada_lovelace"
    assert sq.make_id("arxiv", "", "1706.03762") == "arxiv_1706_03762"


# --- liens -----------------------------------------------------------------


def test_liens_du_texte_prend_liens_markdown_et_url_nues_avec_leur_ligne():
    corps = "voir [ici](https://a.org/x) et https://b.org/y\n\nligne trois [b](https://c.org)"
    liens = list(sq.liens_du_texte(corps, 10))
    assert liens == [("https://a.org/x", 10), ("https://b.org/y", 10), ("https://c.org", 12)]


def test_liens_du_texte_ecarte_les_images_a_texte_vide():
    liens = list(sq.liens_du_texte("[](https://substackcdn.com/img.png) texte", 1))
    assert liens == []


def test_nettoyer_retire_suivi_www_ponctuation_et_slash():
    assert sq.nettoyer("https://www.Exemple.org/chemin/?utm_source=x&id=3).") == "https://exemple.org/chemin?id=3"


def test_nettoyer_deballe_les_redirections():
    assert sq.nettoyer("https://medium.com/r/?url=https%3A%2F%2Fcible.org%2Fp") == "https://cible.org/p"
    assert sq.nettoyer("https://www.google.com/url?q=https://cible.org/q&sa=D") == "https://cible.org/q"


@pytest.mark.parametrize(
    "url, exclu",
    [
        ("https://substackcdn.com/image/x", True),
        ("https://linktr.ee/qui", True),
        ("https://exemple.org/photo.PNG?x=1", True),
        ("https://exemple.org/subscribe", True),
        ("https://exemple.org/api/v1/posts", True),
        ("https://exemple.org/api/v1", True),
        ("https://exemple.org/subscribe/plus", False),
        ("https://exemple.org/article", False),
    ],
)
def test_est_exclu(url, exclu):
    assert sq.est_exclu(url) is exclu


# --- arXiv et GitHub -------------------------------------------------------


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762"),
        ("https://arxiv.org/pdf/2401.12070.pdf", "2401.12070"),
        ("https://arxiv.org/html/2401.12070v2", "2401.12070"),
        ("https://arxiv.org/abs/physics/0408067", "physics/0408067"),
        ("https://arxiv.org/abs/math.GT/0309136", "math.gt/0309136"),
        ("https://exemple.org/1706.03762", None),
    ],
)
def test_canon_arxiv(url, attendu):
    assert sq.canon_arxiv(url) == attendu


def test_canon_arxiv_rejette_un_identifiant_tronque_apres_2015():
    """Après avril 2015 les identifiants ont cinq chiffres ; `2401.1207` est
    tronqué et on ne devine pas le chiffre manquant."""
    assert sq.canon_arxiv("https://arxiv.org/abs/2401.1207") is None
    assert sq.canon_arxiv("https://arxiv.org/abs/1412.6980") == "1412.6980"  # quatre chiffres légitimes avant 2015


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("https://github.com/rasbt/LLMs-from-scratch", "rasbt/LLMs-from-scratch"),
        ("https://github.com/rasbt/LLMs-from-scratch/blob/main/x.py", "rasbt/LLMs-from-scratch"),
        ("https://github.com/rasbt/repo.", "rasbt/repo"),
        ("https://github.com/orgs/truc/x", None),
        ("https://github.com/qui/depot.git", None),
        ("https://gitlab.com/qui/depot", None),
    ],
)
def test_canon_github(url, attendu):
    assert sq.canon_github(url) == attendu


def test_slug_article_reconnait_substack_et_pulse():
    assert sq.slug_article("https://x.substack.com/p/Mon-Billet?x=1") == "mon-billet"
    assert (
        sq.slug_article("https://www.linkedin.com/pulse/titre-andr%C3%A9-lindenberg-abc")
        == "titre-andré-lindenberg-abc"
    )
    assert sq.slug_article("https://exemple.org/autre") is None


# --- auteurs ---------------------------------------------------------------


def test_normaliser_auteurs_scinde_et_retire_les_titres():
    assert sq.normaliser_auteurs("Tivadar Danka, Alejandro Piad Morffis") == ["Tivadar Danka", "Alejandro Piad Morffis"]
    assert sq.normaliser_auteurs("Sebastian Raschka, PhD") == ["Sebastian Raschka"]
    assert sq.normaliser_auteurs("Ada et Bob and Cléo & Dan; Eve") == ["Ada", "Bob", "Cléo", "Dan", "Eve"]
    assert sq.normaliser_auteurs("A, B") == []  # une lettre n'est pas un nom
    assert sq.normaliser_auteurs("") == []


# --- who-is-who ------------------------------------------------------------


def test_triplets_who_is_who_traduit_la_certitude_sans_la_promouvoir(tmp_path):
    f = tmp_path / "wiw.md"
    f.write_text(
        "## Relations\n"
        "- EPFL -> dirige -> Laboratoire X [A]\n"
        "- Alice -> travaille_chez -> EPFL [A/B]\n"
        "- Bob -> cofonde -> Startup\n"
        "## Autre\n"
        "- pas -> un -> triplet [A]\n",
        encoding="utf-8",
    )
    triplets = list(sq.triplets_who_is_who(f, "id"))
    assert triplets == [
        ("EPFL", "dirige", "Laboratoire X", "EXTRACTED", 2),
        ("Alice", "travaille_chez", "EPFL", "INFERRED", 3),  # A/B : le pire l'emporte
        ("Bob", "cofonde", "Startup", "AMBIGUOUS", 4),  # sans niveau : C
    ]


# --- index -----------------------------------------------------------------


def test_construire_index_exclut_les_fiches_arxiv():
    fiches = {
        "a": {"id": "a", "arxiv_id": "", "champs": {"url": "https://x.substack.com/p/slug-a?utm_source=z"}},
        "b": {"id": "b", "arxiv_id": "1706.03762", "champs": {"url": "https://arxiv.org/abs/1706.03762"}},
    }
    index = sq.construire_index(fiches)
    assert index["slug-a"] == "a"
    assert index["https://x.substack.com/p/slug-a"] == "a"
    assert "b" not in index.values()

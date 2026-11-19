"""Le billet groupé d'une source prolixe (fil_serie) : ses fiches du thème attendent l'heure de la série, puis font un
seul billet pour le jour ; une fiche hors du thème suit la voie ordinaire."""

import time

import pytest

from newskit import config_sources as cfg
from newskit import fil, fil_serie, pages_fiches, publier_fil
from newskit import fil_fiche as ff
from newskit import nouvelles as nv

JOUR = time.strftime("%Y-%m-%d")
SERIE = {"theme": "commentaire d'un règlement, article par article", "heure": "15:00"}
GAZETTE = cfg.Source(
    cle="gazette", plateforme="Flux RSS", nom="Gazette", dossier="rss/gazette", abonnement="gratuit", serie=SERIE
)


def fiche(n, dossier="rss/gazette", **k):
    return {
        "chemin": f"sources/{dossier}/{n}.md",
        "url": f"https://gazette.example/posts/{n}",
        "titre": f"Article {n}",
        "date": JOUR,
        "rubrique": "Conformité",
        **k,
    }


def article(n, dans=True):
    if not dans:
        return {"n": n, "dans_le_theme": False}
    return {
        "n": n,
        "dans_le_theme": True,
        "titre_court": f"Titre {n}",
        "texte": f"Une phrase. Deux phrases {n}.",
        "titre_court_en": f"Title {n}",
        "texte_en": f"One sentence. Two sentences {n}.",
    }


def reponse(*articles, **groupe):
    g = {
        "titre": "Gazette commente deux articles",
        "texte": "Le chapeau du jour.",
        "titre_en": "Gazette comments on two articles",
        "texte_en": "The lead of the day.",
        "rubrique": "Conformité",
        **groupe,
    }
    return {"groupe": g, "articles": list(articles)}


RUBS = [{"nom": "Conformité"}, {"nom": "Entreprises"}]


@pytest.fixture
def serie(monkeypatch):
    monkeypatch.setattr(cfg, "serie_du_chemin", lambda c: GAZETTE if c.startswith("sources/rss/gazette/") else None)
    monkeypatch.setattr(fil, "langue_source", lambda f: "en")


# 1. Le partage et l'heure


def test_les_fiches_d_une_serie_quittent_la_voie_ordinaire(serie):
    a, b, x = fiche("a"), fiche("b"), fiche("x", dossier="rss/autre")
    ordinaires, en_serie = fil_serie.partager([a, x, b], {})
    assert ordinaires == [x] and en_serie == {"gazette": {"source": GAZETTE, "fiches": [a, b]}}


def test_une_fiche_jugee_hors_du_theme_reste_ordinaire(serie):
    a, b = fiche("a"), fiche("b")
    F = {"series": {"gazette": {"hors": [b["chemin"]]}}}
    ordinaires, en_serie = fil_serie.partager([a, b], F)
    assert ordinaires == [b] and en_serie["gazette"]["fiches"] == [a]


def test_le_billet_groupe_est_du_a_l_heure_de_la_serie(serie):
    en_serie = {"gazette": {"source": GAZETTE, "fiches": [fiche("a")]}}
    assert fil_serie.dues(en_serie, {}, f"{JOUR} 14:59") == []
    assert fil_serie.dues(en_serie, {}, f"{JOUR} 15:00") == ["gazette"]
    assert fil_serie.dues(en_serie, {"series": {"gazette": {"jour": "2000-01-01"}}}, f"{JOUR} 16:00") == ["gazette"]


def test_un_seul_billet_groupe_par_jour(serie):
    en_serie = {"gazette": {"source": GAZETTE, "fiches": [fiche("a")]}}
    assert fil_serie.dues(en_serie, {"series": {"gazette": {"jour": JOUR}}}, f"{JOUR} 18:00") == []


def test_un_essai_sans_billet_se_reprend_une_heure_plus_tard(serie):
    en_serie = {"gazette": {"source": GAZETTE, "fiches": [fiche("a")]}}
    F = {"series": {"gazette": {"repris": f"{JOUR} 15:00"}}}
    assert fil_serie.dues(en_serie, F, f"{JOUR} 15:59") == []
    assert fil_serie.dues(en_serie, F, f"{JOUR} 16:00") == ["gazette"]


# 2. Le billet


def test_le_billet_groupe_porte_un_article_par_fiche_du_theme(serie):
    f = [fiche("a"), fiche("b"), fiche("c")]
    b, hors = fil_serie.billet(
        reponse(article(0), article(1, dans=False), article(2)), f, GAZETTE, f"{JOUR} 15:05", RUBS
    )
    assert hors == [f[1]]
    assert b["titre"] == "Gazette commente deux articles" and b["descriptif"] == "Le chapeau du jour."
    assert b["en"] == {"titre": "Gazette comments on two articles", "texte": "The lead of the day.", "tenu": True}
    assert b["articles"] == [
        {
            "chemin": f[0]["chemin"],
            "url": f[0]["url"],
            "titre": "Titre 0",
            "texte": "Une phrase. Deux phrases 0.",
            "en": {"titre": "Title 0", "texte": "One sentence. Two sentences 0."},
        },
        {
            "chemin": f[2]["chemin"],
            "url": f[2]["url"],
            "titre": "Titre 2",
            "texte": "Une phrase. Deux phrases 2.",
            "en": {"titre": "Title 2", "texte": "One sentence. Two sentences 2."},
        },
    ]
    assert b["chemin"] == f"serie/gazette/{JOUR}" and b["url"] == f"https://gazette.example/#gazette-{JOUR}"
    assert (
        b["rubrique"] == "Conformité" and b["date"] == JOUR and b["ajoute"] == f"{JOUR} 15:05" and b["langue"] == "en"
    )
    assert b["source"] == {"titre": "Gazette", "editeur": "Gazette", "date": JOUR, "url": "https://gazette.example/"}
    assert b["rapproche"] is True and b["contexte_juge"] is True and b["versions"] == {} and "tenu" not in b


def test_l_adresse_du_billet_groupe_change_avec_le_jour_et_la_source(serie):
    f = [fiche("a")]
    autre = cfg.Source(cle="b", plateforme="Flux RSS", nom="B", dossier="rss/b", abonnement="gratuit", serie=SERIE)
    u = fil_serie.adresse(GAZETTE, f, "2026-11-19")
    assert u != fil_serie.adresse(GAZETTE, f, "2026-11-20") and u != fil_serie.adresse(autre, f, "2026-11-19")
    assert pages_fiches.ident(u) != pages_fiches.ident(fil_serie.adresse(GAZETTE, f, "2026-11-20"))


def test_la_rubrique_est_celle_de_la_plupart_des_fiches(serie):
    f = [fiche("a", rubrique="Entreprises"), fiche("b", rubrique="Entreprises"), fiche("c")]
    b, _ = fil_serie.billet(reponse(article(0), article(1), article(2)), f, GAZETTE, f"{JOUR} 15:05", RUBS)
    assert b["rubrique"] == "Entreprises"


def test_sans_rubrique_aux_fiches_celle_du_modele(serie):
    f = [fiche("a", rubrique=None), fiche("b", rubrique=None)]
    b, _ = fil_serie.billet(reponse(article(0), article(1), rubrique="Entreprises"), f, GAZETTE, f"{JOUR} 15:05", RUBS)
    assert b["rubrique"] == "Entreprises"
    assert (
        fil_serie.billet(reponse(article(0), article(1), rubrique="Sports"), f, GAZETTE, f"{JOUR} 15:05", RUBS)[0]
        is None
    )
    sans = fil_serie.billet(reponse(article(0), article(1), rubrique=""), f, GAZETTE, f"{JOUR} 15:05", [])[0]
    assert sans is not None and sans["rubrique"] is None  # un graphe sans rubriques


def test_un_seul_article_du_theme_suit_la_voie_ordinaire(serie):
    f = [fiche("a"), fiche("b")]
    b, hors = fil_serie.billet(reponse(article(0), article(1, dans=False)), f, GAZETTE, f"{JOUR} 15:05", RUBS)
    assert b is None and sorted(x["chemin"] for x in hors) == [f[0]["chemin"], f[1]["chemin"]]


@pytest.mark.parametrize(
    "rep",
    [
        {},
        reponse(article(0)),  # une fiche sans réponse
        reponse(article(0), article(1), article(5)),  # un numéro hors du lot
        reponse(article(0), article(1), titre=""),
        reponse(article(0), article(1), texte_en=" "),
        reponse(article(0), article(1), titre_en=""),
        reponse(article(0), {**article(1), "titre_court_en": ""}),
        reponse(article(0), {**article(1), "texte": ""}),
        reponse(article(0), article(1), titre="T" * 91),
        reponse(article(0), article(1), titre_en="T" * 91),
        reponse(article(0), article(1), titre="Que dit la Gazette ?"),
        reponse(article(0), {**article(1), "texte": "mot " * 91}),
        reponse(article(0), {**article(1), "texte_en": "word " * 91}),
    ],
)
def test_une_reponse_incomplete_ne_fait_pas_de_billet(serie, rep):
    b, hors = fil_serie.billet(rep, [fiche("a"), fiche("b")], GAZETTE, f"{JOUR} 15:05", RUBS)
    assert b is None and hors == []


def test_un_texte_de_quatre_vingt_dix_mots_passe(serie):
    rep = reponse(article(0), {**article(1), "texte": "mot " * 90})
    assert fil_serie.billet(rep, [fiche("a"), fiche("b")], GAZETTE, f"{JOUR} 15:05", RUBS)[0] is not None


def test_l_etat_de_la_serie_retient_le_jour_ou_l_essai(serie):
    F = {}
    fil_serie.noter(F, "gazette", f"{JOUR} 15:05", None, [fiche("x")])
    assert F["series"]["gazette"] == {"hors": [fiche("x")["chemin"]], "repris": f"{JOUR} 15:05"}
    fil_serie.noter(F, "gazette", f"{JOUR} 16:05", {"articles": []}, [fiche("y")])
    assert F["series"]["gazette"] == {"hors": [fiche("x")["chemin"], fiche("y")["chemin"]], "jour": JOUR}


def test_la_liste_des_fiches_hors_theme_reste_bornee(serie, monkeypatch):
    monkeypatch.setattr(fil_serie, "HORS_GARDES", 2)
    F = {}
    fil_serie.noter(F, "gazette", f"{JOUR} 15:05", None, [fiche("a"), fiche("b"), fiche("c")])
    assert F["series"]["gazette"]["hors"] == [fiche("b")["chemin"], fiche("c")["chemin"]]


# 3. La consigne


def test_la_consigne_porte_le_theme_la_source_et_les_fiches(serie, monkeypatch):
    vu = {}
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"libelle": "la gouvernance"})
    monkeypatch.setattr(cfg, "rubriques", lambda g: RUBS)
    monkeypatch.setattr(fil.lt, "regles", lambda g: "REGLES-DU-GRAPHE")
    monkeypatch.setattr(fil, "extrait", lambda f, n: vu.setdefault("max", n) and f"corps de {f['titre']}")

    def demander(inv, schema, graphe, note):
        vu.update(inv=inv, schema=schema, graphe=graphe, note=note)
        return {"groupe": {}}, 0.4

    monkeypatch.setattr(fil, "demander", demander)
    assert fil_serie.grouper([fiche("a"), fiche("b")], GAZETTE, "g") == ({"groupe": {}}, 0.4)
    inv = vu["inv"]
    assert SERIE["theme"] in inv and "« Gazette »" in inv and "la gouvernance" in inv and "REGLES-DU-GRAPHE" in inv
    assert "Conformité, Entreprises" in inv and '"n": 1' in inv and "corps de Article b" in inv
    assert vu["schema"] is fil_serie.SCHEMA and vu["graphe"] == "g" and vu["max"] == fil_serie.ARTICLE_MAX


def test_la_consigne_sans_rubrique_ni_libelle(serie, monkeypatch):
    vu = {}
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    monkeypatch.setattr(cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil.lt, "regles", lambda g: "")
    monkeypatch.setattr(fil, "extrait", lambda f, n: "")
    monkeypatch.setattr(fil, "demander", lambda inv, *a: vu.update(inv=inv) or ({}, 0.0))
    fil_serie.grouper([fiche("a")], GAZETTE, "mon-graphe")
    assert "sur mon-graphe" in vu["inv"] and "(aucune)" in vu["inv"]


# 4. Le passage du fil


def _banc(monkeypatch, heure, cands, rep, F=None, limite=False, depense=0.0):
    F = F if F is not None else {}
    F.setdefault("billets", [])
    F.setdefault("pile", [])
    F.setdefault("vues", [])
    F.setdefault("depense", {JOUR: depense})
    vu = {"groupes": [], "rediges": [], "enrichis": []}
    for nom in ("poser_langues", "poser_versions"):
        monkeypatch.setattr(fil, nom, lambda b: 0)
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: RUBS)
    monkeypatch.setattr(fil, "lire", lambda g: F)
    monkeypatch.setattr(fil, "ecrire", lambda g, f: vu.__setitem__("ecrit", True))
    monkeypatch.setattr(fil, "maintenant", lambda: f"{JOUR} {heure}")
    monkeypatch.setattr(fil, "cadence_du_moment", lambda g, n, q: 10)
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"fil_usd_jour": 100})
    monkeypatch.setattr(fil, "candidates", lambda g, vues, jour: [f for f in cands if f["chemin"] not in vues])
    monkeypatch.setattr(
        fil, "rediger", lambda lot, recents, g: (vu["rediges"].append((list(lot), list(recents))) or [], 0.0)
    )
    monkeypatch.setattr(fil, "proposer", lambda *a: None)
    monkeypatch.setattr(fil, "textes_du_registre", lambda F: {})
    for nom in ("relire", "reviser_titres"):
        monkeypatch.setattr(fil, nom, lambda b, g: 0.0)
    monkeypatch.setattr(fil, "completer", lambda b, g, reste: 0.0)
    monkeypatch.setattr(fil, "billets_anglais_a_ecrire", lambda F: [])
    monkeypatch.setattr(fil, "rapprocher", lambda F, g: 0.0)
    monkeypatch.setattr(fil.nv, "rattacher", lambda F, c: 0)
    monkeypatch.setattr(fil, "definir", lambda F, g, reste: 0.0)
    monkeypatch.setattr(fil, "lier", lambda F, g: 0.0)
    monkeypatch.setattr(fil.fil_anglais(), "anglais", lambda F, g, reste: 0.0)
    monkeypatch.setattr(ff, "a_ecrire", lambda F: [])
    monkeypatch.setattr(ff, "etoffer", lambda F, g, reste: 0.0)
    monkeypatch.setattr(ff, "en_attente", lambda F, g, quand=None: [])
    monkeypatch.setattr(fil.couts, "en_limite", lambda: limite)
    monkeypatch.setattr(fil, "enrichir", lambda b: vu["enrichis"].append(b))
    monkeypatch.setattr(
        fil_serie, "grouper", lambda fiches, source, g: (vu["groupes"].append(list(fiches)) or rep, 0.5)
    )
    vu["reste"] = fil.passer("g")
    return F, vu


def test_avant_l_heure_les_fiches_de_la_serie_attendent(serie, monkeypatch):
    F, vu = _banc(monkeypatch, "14:00", [fiche("a"), fiche("b")], reponse(article(0), article(1)))
    assert vu["groupes"] == [] and vu["rediges"] == [] and F["vues"] == [] and F["billets"] == [] and vu["reste"] == 0


def test_a_l_heure_un_seul_billet_entre_au_fil_sans_la_pile(serie, monkeypatch):
    a, b = fiche("a"), fiche("b")
    F = {"paru_le": f"{JOUR} 15:04"}  # la pile vient de laisser sortir un billet : le suivant attendrait dix minutes
    F, vu = _banc(monkeypatch, "15:05", [a, b], reponse(article(0), article(1)), F=F)
    assert vu["groupes"] == [[a, b]] and vu["rediges"] == []
    assert [x["chemin"] for x in F["billets"]] == [f"serie/gazette/{JOUR}"] and F["pile"] == []
    assert F["billets"][0]["ajoute"] == f"{JOUR} 15:05"
    assert F["vues"] == [a["chemin"], b["chemin"]] and F["series"]["gazette"]["jour"] == JOUR
    assert F["depense"][JOUR] == 0.5 and vu["reste"] == 0 and vu["ecrit"]


def test_chaque_article_du_billet_groupe_entre_a_l_en_tete_de_sa_fiche(serie, monkeypatch):
    a, b = fiche("a"), fiche("b")
    _, vu = _banc(monkeypatch, "15:05", [a, b], reponse(article(0), article(1)))
    assert vu["enrichis"] == [
        {"chemin": a["chemin"], "titre": "Titre 0", "descriptif": "Une phrase. Deux phrases 0."},
        {"chemin": b["chemin"], "titre": "Titre 1", "descriptif": "Une phrase. Deux phrases 1."},
    ]


def test_le_billet_du_jour_ecrit_une_fiche_tardive_attend_le_lendemain(serie, monkeypatch):
    F = {"series": {"gazette": {"jour": JOUR}}}
    F, vu = _banc(monkeypatch, "18:00", [fiche("c")], reponse(article(0)), F=F)
    assert vu["groupes"] == [] and vu["rediges"] == [] and F["vues"] == []


def test_une_fiche_hors_du_theme_est_redigee_dans_le_meme_passage(serie, monkeypatch):
    a, b, c, x = fiche("a"), fiche("b"), fiche("c"), fiche("x", dossier="rss/autre")
    F, vu = _banc(monkeypatch, "15:05", [a, b, c, x], reponse(article(0), article(1, dans=False), article(2)))
    assert [f["chemin"] for f in vu["rediges"][0][0]] == [b["chemin"], x["chemin"]]
    assert F["series"]["gazette"]["hors"] == [b["chemin"]] and len(F["billets"][0]["articles"]) == 2
    assert vu["reste"] == 2  # le rédacteur n'a pas répondu : les deux fiches ordinaires restent à lire


def test_une_reponse_vide_laisse_les_fiches_a_lire(serie, monkeypatch):
    F, vu = _banc(monkeypatch, "15:05", [fiche("a"), fiche("b")], {})
    assert F["billets"] == [] and F["vues"] == [] and vu["enrichis"] == []
    assert F["series"]["gazette"] == {"hors": [], "repris": f"{JOUR} 15:05"} and F["depense"][JOUR] == 0.5


def test_au_plafond_ou_a_la_limite_d_usage_aucun_billet_groupe(serie, monkeypatch):
    for limite, depense in ((True, 0.0), (False, 100.0)):
        F, vu = _banc(
            monkeypatch,
            "15:05",
            [fiche("a"), fiche("b")],
            reponse(article(0), article(1)),
            limite=limite,
            depense=depense,
        )
        assert vu["groupes"] == [] and F["billets"] == [] and F["vues"] == []


def test_sans_modele_aucun_billet_groupe(serie, monkeypatch):
    vu = []
    monkeypatch.setattr(fil, "lire", lambda g: {"billets": [], "vues": []})
    monkeypatch.setattr(fil, "candidates", lambda g, vues, jour: [fiche("a"), fiche("x", dossier="rss/autre")])
    monkeypatch.setattr(fil, "maintenant", lambda: f"{JOUR} 15:05")
    monkeypatch.setattr(fil_serie, "grouper", lambda *a: vu.append(a) or ({}, 0.0))
    assert fil.passer("g", sans_modele=True) == 1 and vu == []


def test_un_billet_groupe_compte_au_plus_vingt_cinq_articles(serie, monkeypatch):
    monkeypatch.setattr(fil_serie, "PAR_GROUPE", 2)
    F, vu = _banc(monkeypatch, "15:05", [fiche("a"), fiche("b"), fiche("c")], reponse(article(0), article(1)))
    assert [len(g) for g in vu["groupes"]] == [2] and len(F["vues"]) == 2


# 5. Les étapes d'après ne prennent pas un billet groupé pour un billet ordinaire


def groupe():
    f = [fiche("a"), fiche("b")]
    return fil_serie.billet(reponse(article(0), article(1)), f, GAZETTE, f"{JOUR} 15:05", RUBS)[0]


def test_un_billet_groupe_n_est_ni_relu_ni_lie_ni_defini_ni_traduit_ni_etoffe(serie, monkeypatch):
    F = {"billets": [groupe()], "pile": []}
    monkeypatch.setattr(fil, "provisoire", lambda b: False)
    assert fil.a_relire(F) == [] and fil.a_lier(F) == [] and fil.a_definir(F) == []
    assert fil.fil_anglais().a_ecrire(F) == [] and ff.a_ecrire(F) == []


def test_un_billet_groupe_n_est_pas_un_billet_recent_pour_le_redacteur(serie, monkeypatch):
    x = fiche("x", dossier="rss/autre")
    F, vu = _banc(monkeypatch, "10:00", [x], {}, F={"billets": [groupe()]})
    assert vu["rediges"] == [([x], [])]


def test_les_articles_d_un_billet_groupe_ne_sont_pas_des_fiches_ecartees(serie):
    g = groupe()
    chemins = [a["chemin"] for a in g["articles"]] + ["sources/rss/autre/z.md"]
    F = {"billets": [g], "vues": chemins}
    assert nv.ecartees(F, chemins) == {"sources/rss/autre/z.md"}


def test_la_copie_publique_donne_un_element_par_article_avec_son_lien(serie, monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})
    p = publier_fil.public({"billets": [groupe()]})["billets"][0]
    assert p["titre_en"] == "Gazette comments on two articles" and p["resume"] == "" and p["meta"] == []
    assert p["elements"] == [
        {"champ": "Titre 0", "texte": "Une phrase. Deux phrases 0.", "lien": "https://gazette.example/posts/a"},
        {"champ": "Titre 1", "texte": "Une phrase. Deux phrases 1.", "lien": "https://gazette.example/posts/b"},
    ]
    assert p["elements_en"][1] == {
        "champ": "Title 1",
        "texte": "One sentence. Two sentences 1.",
        "lien": "https://gazette.example/posts/b",
    }


def test_la_page_du_billet_groupe_donne_le_lien_de_chaque_article(serie, monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})
    g = groupe()
    g["articles"][0]["url"] = "https://gazette.example/posts/a?x=1&y=<2>"
    p = pages_fiches.items({"billets": [g]})[0]
    fr, en = (
        pages_fiches.article(p, "fr", "https://exemple.news"),
        pages_fiches.article(p, "en", "https://exemple.news"),
    )
    assert "<h2>Titre 0</h2>\n<p>Une phrase. Deux phrases 0.</p>\n" in fr
    assert '<p><a href="https://gazette.example/posts/a?x=1&amp;y=&lt;2&gt;">Lire l’article</a></p>' in fr
    assert '<p><a href="https://gazette.example/posts/b">Read the article</a></p>' in en and "<h2>Title 1</h2>" in en
    assert fr.count("Lire l’article") == 2


def test_un_element_sans_lien_n_a_pas_de_lien(serie):
    p = {"titre": "T", "descriptif": "D", "date": JOUR, "url": "u", "elements": [{"champ": "Fait", "texte": "x"}]}
    assert "Lire" not in pages_fiches.article(p, "fr", "https://exemple.news")

"""Le lecteur web (`newskit.lecture_web`) : ce qu'il garde d'une page, ce qu'il met à part, ce qu'il jette.

Aucun réseau : le téléchargement est remplacé par un HTML figé (fixture ou page construite ici), et le repli
`collecte_rss.corps_de_page` est neutralisé.
"""

from __future__ import annotations

import pytest

from newskit import lecture_web as lw
from conftest import FIXTURES

MEMO = (FIXTURES / "lecture_web_memo_notes.html").read_text(encoding="utf-8")


class _Serveur:
    """Un faux serveur local : chaque chemin rend (statut, HTML) ; chaque requête reçue garde ses en-têtes."""

    def __init__(self):
        import http.server
        import threading

        self.routes: dict[str, tuple[int, str | bytes, str]] = {}
        self.recus: list[dict] = []
        moi = self

        class Gestion(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                moi.recus.append({**{k: v for k, v in self.headers.items()}, ":chemin": self.path})
                statut, corps, genre = moi.routes.get(self.path, (404, "absent", "text/html; charset=utf-8"))
                octets = corps if isinstance(corps, bytes) else corps.encode("utf-8")
                self.send_response(statut)
                self.send_header("Content-Type", genre)
                self.send_header("Content-Length", str(len(octets)))
                self.end_headers()
                self.wfile.write(octets)

            def log_message(self, *a):
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
        threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def servir(
        self, html: str | bytes, chemin: str = "/page", statut: int = 200, genre: str = "text/html; charset=utf-8"
    ) -> str:
        self.routes[chemin] = (statut, html, genre)
        return self.base + chemin


@pytest.fixture
def serveur(monkeypatch):
    from newskit import collecte_rss

    monkeypatch.setattr(collecte_rss, "corps_de_page", lambda *a, **k: (0, "", False, {}))
    s = _Serveur()
    yield s
    s.http.shutdown()


@pytest.fixture
def servir(serveur):
    """`servir(html)` : l'adresse locale où le lecteur téléchargera cette page ; le repli maison ne lit rien."""
    return serveur.servir


def page(*paragraphes: str, titre: str = "Une page de test") -> str:
    corps = "\n".join(f"<p>{p}</p>" for p in paragraphes)
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{titre}</title></head>"
        f"<body><article><h1>{titre}</h1>\n{corps}\n</article></body></html>"
    )


FOND = (
    "The regulator published its annual report on Tuesday, describing how the new supervisory framework applies to "
    "providers of general purpose models and which obligations will apply from August of next year onwards."
)
FOND2 = (
    "According to the report, forty-two providers notified their models during the first year, and the authority "
    "opened six formal investigations into transparency obligations, two of which are already closed with fines."
)


# ── 1. Notes de bas de page ────────────────────────────────────────────────────────────────────────────────────────


def test_les_notes_finales_sortent_du_texte_et_vont_dans_notes(servir):
    p = lw.lire(servir(MEMO))
    assert "The following Participating States have signed the JPA" not in p["texte"]
    assert "Tender Specifications, p. 26" not in p["texte"]
    assert "[5] The following Participating States have signed the JPA" in p["notes"]
    assert "[7] Tender Specifications, p. 26." in p["notes"]


def test_les_appels_de_note_sortent_du_corps(servir):
    p = lw.lire(servir(MEMO))
    assert "gigafactories[2]" not in p["texte"] and "[3]" not in p["texte"]
    assert "artificial intelligence gigafactories in the Member States" in p["texte"]


def test_le_fond_reste_entier_titres_numerotes_compris(servir):
    p = lw.lire(servir(MEMO))
    for morceau in (
        "1. Overview",
        "2. Financing",
        "at least EUR 4 to 5 billion for each site",
        "Tenders must be submitted electronically through the eSubmission system",
        "In the wake of the artificial intelligence boom",
    ):
        assert morceau in p["texte"], morceau


def test_une_citation_tiree_d_une_note_se_verifie_encore(servir):
    p = lw.lire(servir(MEMO))
    cit = "The following Participating States have signed the JPA: Croatia, Czechia, Denmark"
    assert lw.citation_dans(cit, p)
    assert not lw.citation_dans(cit, p["texte"])


def test_une_citation_qui_enjambe_un_appel_de_note_se_verifie(servir):
    p = lw.lire(servir(MEMO))
    assert lw.citation_dans("12 November 2026 at 16:00 Luxembourg time. Tenders must be submitted electronically", p)


def test_notes_numerotees_sous_un_titre_de_notes(servir):
    p = lw.lire(servir(page(FOND, FOND2, "Notes", "1. Annual report 2026, p. 4.", "2. Press release of 3 March 2026.")))
    assert "Annual report 2026, p. 4" not in p["texte"] and "Press release of 3 March" not in p["texte"]
    assert "1. Annual report 2026, p. 4." in p["notes"]
    assert FOND2 in p["texte"]


def test_une_liste_numerotee_finale_sans_titre_de_notes_reste_du_fond(servir):
    p = lw.lire(
        servir(
            page(
                FOND,
                FOND2,
                "1. Cut the reporting burden for small providers.",
                "2. Publish the list of notified models.",
            )
        )
    )
    assert "1. Cut the reporting burden for small providers." in p["texte"]
    assert "2. Publish the list of notified models." in p["texte"]
    assert p["notes"] == ""


def test_le_memo_perd_ses_notes_en_mots(servir):
    p = lw.lire(servir(MEMO))
    assert len(p["notes"].split()) > 90
    assert p["mots"] == len(p["texte"].split())


# ── 1. Lignes d'abonnement et de partage ──────────────────────────────────────────────────────────────────────────

PARASITES = [
    "Subscribe to stay current on S&amp;C Insights.",
    "Share this",
    "Sign up for our newsletter",
    "Abonnez-vous à notre newsletter",
    "Partager cet article",
    "Newsletter abonnieren",
    "Diesen Artikel teilen",
    "Schrijf je in voor onze nieuwsbrief",
    "Deel dit artikel",
    "Iscriviti alla newsletter",
    "Condividi questo articolo",
    "Suscríbete a nuestro boletín",
    "Compartir en redes sociales",
]


@pytest.mark.parametrize("ligne", PARASITES)
def test_une_ligne_d_abonnement_ou_de_partage_sort(servir, ligne):
    p = lw.lire(servir(page(FOND, ligne, FOND2)))
    texte_de_la_ligne = ligne.replace("&amp;", "&")
    assert texte_de_la_ligne not in p["texte"]
    assert FOND in p["texte"] and FOND2 in p["texte"]


LONGS = [
    # 25 mots ou plus : un paragraphe de fond, même s'il commence comme un appel à s'abonner.
    "Subscribe to the view of the Commission, the authors argue, and the gigafactories become a subsidy for compute that "
    "European firms could not otherwise afford to buy from American clouds.",
    # Court, mais « Teilen » y est un nom (des parties), pas un bouton.
    "Teilen der Bevölkerung fehlt der Zugang zu schnellem Internet auf dem Land.",
]


@pytest.mark.parametrize("ligne", LONGS)
def test_un_paragraphe_de_fond_n_est_jamais_retire(servir, ligne):
    p = lw.lire(servir(page(FOND, ligne, FOND2)))
    assert ligne in p["texte"]


# ── 3. Téléchargement : un profil de navigateur complet, une page refusée rend un texte vide ─────────────────────


def test_les_entetes_envoyes_forment_un_seul_profil_de_navigateur(serveur):
    import re as _re

    lw.lire(serveur.servir(MEMO))
    assert len(serveur.recus) == 1, "une seule requête, faite par le lecteur lui-même"
    e = {k.lower(): v for k, v in serveur.recus[0].items()}
    for cle in (
        "user-agent",
        "accept",
        "accept-language",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
    ):
        assert e.get(cle), cle
    ua = e["user-agent"]
    version = _re.search(r"Chrome/(\d+)\.", ua).group(1)
    assert f'"Google Chrome";v="{version}"' in e["sec-ch-ua"] and f'"Chromium";v="{version}"' in e["sec-ch-ua"]
    assert ("Macintosh" in ua) == (e["sec-ch-ua-platform"] == '"macOS"')
    assert e["sec-ch-ua-mobile"] == "?0" and "Mobile" not in ua
    assert (e["sec-fetch-dest"], e["sec-fetch-mode"], e["sec-fetch-site"], e["sec-fetch-user"]) == (
        "document",
        "navigate",
        "none",
        "?1",
    )
    assert e["upgrade-insecure-requests"] == "1" and e["accept"].startswith("text/html")
    assert "trafilatura" not in ua.lower() and "python" not in ua.lower()


def test_la_page_telechargee_passe_par_trafilatura(serveur):
    p = lw.lire(serveur.servir(MEMO))
    assert p["voie"] == "trafilatura"
    assert "at least EUR 4 to 5 billion for each site" in p["texte"]


@pytest.mark.parametrize("statut", [403, 429, 503])
def test_une_page_refusee_rend_un_texte_vide_et_sa_raison(serveur, statut):
    p = lw.lire(serveur.servir("<html><body><p>Forbidden</p></body></html>", statut=statut))
    assert p["texte"] == "" and p["mots"] == 0
    assert "refus" in p["voie"] and str(statut) in p["voie"]


MUR = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    "<div id='challenge-running'>Checking if the site connection is secure</div>"
    "<p>Enable JavaScript and cookies to continue</p>"
    "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script></body></html>"
)


def test_un_mur_anti_robot_rend_un_texte_vide_et_sa_raison(serveur):
    p = lw.lire(serveur.servir(MUR, statut=200))
    assert p["texte"] == "" and "mur anti-robot" in p["voie"]


def test_un_article_qui_parle_de_captcha_n_est_pas_un_mur(serveur):
    article = page(
        FOND,
        "Cloudflare shows a page titled Just a moment... while it checks the browser, and "
        "Enable JavaScript and cookies to continue is the sentence most users see.",
        FOND2,
    )
    p = lw.lire(serveur.servir(article))
    assert FOND2 in p["texte"] and p["voie"] == "trafilatura"


def test_une_adresse_injoignable_ne_leve_pas(serveur):
    p = lw.lire("http://127.0.0.1:9/rien")
    assert p["texte"] == "" and p["voie"].startswith("téléchargement en échec")


def test_un_article_long_avec_le_script_de_cloudflare_n_est_pas_un_mur(serveur):
    # Cloudflare injecte /cdn-cgi/challenge-platform/scripts/jsd/main.js dans des pages ordinaires.
    article = page(*([FOND, FOND2] * 6)).replace(
        "</body>", "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></body>"
    )
    p = lw.lire(serveur.servir(article))
    assert FOND2 in p["texte"] and p["voie"] == "trafilatura"


# ── 2. Tri des paragraphes pour le modèle (sans modèle) ───────────────────────────────────────────────────────────

TITRE = "Bruxelles reporte l'appel aux gigafactories"
CHAPO = (
    "La Commission a repoussé la date limite de son appel aux gigafactories d'intelligence artificielle, faute "
    "d'offres reçues selon plusieurs sources, et les consortiums nationaux se préparent encore."
)
HORS = (
    "Le reste de l'article parle du temps qu'il fait à Bruxelles, des terrasses et des cafés du quartier "
    "européen où se croisent les fonctionnaires, les lobbyistes et les journalistes chaque matin de la semaine."
)
VOISIN = (
    "Les consortiums espagnols et français ont confirmé leur candidature à l'appel, sans donner leur budget, "
    "selon les ministres chargés du numérique des deux pays et leurs services."
)
CHIFFRE = "Les offres du lot deux sont désormais attendues le 3 décembre 2026 à seize heures, heure de Luxembourg."
FORT = (
    "Les consortiums candidats à l'appel aux gigafactories, en Espagne et en France, attendent les budgets "
    "nationaux ; la candidature espagnole réunit Telefónica et la Generalitat, la française Scaleway."
)
QUESTIONS = [
    "Quels consortiums se sont déclarés candidats à l'appel aux gigafactories ?",
    "La date limite des offres a-t-elle changé ?",
]


def _texte(*paragraphes):
    return "\n".join(paragraphes)


def test_un_texte_dans_le_budget_rend_tout():
    t = _texte(TITRE, CHAPO, HORS)
    assert lw.extrait_utile(t, QUESTIONS, 1000) == t


def test_le_tri_garde_les_paragraphes_qui_recoupent_les_questions_dans_l_ordre():
    t = _texte(TITRE, CHAPO, HORS, HORS, FORT, HORS, VOISIN, HORS)
    budget = len(_texte(TITRE, CHAPO, FORT, VOISIN).split())
    ex = lw.extrait_utile(t, QUESTIONS, budget)
    assert ex.split("\n") == [TITRE, CHAPO, FORT, VOISIN]


def test_le_plus_fort_recoupement_passe_d_abord_quand_le_budget_manque():
    t = _texte(TITRE, CHAPO, VOISIN, HORS, FORT, HORS)
    budget = len(_texte(TITRE, CHAPO, FORT).split()) + 3
    ex = lw.extrait_utile(t, QUESTIONS, budget)
    assert FORT in ex and VOISIN not in ex and HORS not in ex


def test_titre_et_premier_paragraphe_restent_meme_sans_recoupement():
    t = _texte("Météo du jour", HORS, FORT, HORS, HORS)
    ex = lw.extrait_utile(t, QUESTIONS, len(_texte("Météo du jour", HORS, FORT).split()))
    assert ex.split("\n") == ["Météo du jour", HORS, FORT]


def test_un_nombre_proche_d_un_mot_des_questions_fait_entrer_le_paragraphe():
    # CHIFFRE recoupe moins de mots que VOISIN, mais sa date est à côté de « offres » : il passe devant.
    t = _texte(TITRE, CHAPO, VOISIN, HORS, CHIFFRE, HORS)
    place = max(len(VOISIN.split()), len(CHIFFRE.split()))  # la place d'un seul des deux
    assert len(VOISIN.split()) + len(CHIFFRE.split()) > place
    ex = lw.extrait_utile(t, QUESTIONS, len(_texte(TITRE, CHAPO).split()) + place)
    assert CHIFFRE in ex and VOISIN not in ex


def test_un_nombre_loin_de_tout_mot_des_questions_ne_compte_pas():
    loin = (
        "En 1998, sur les terrasses, on servait 300 cafés par matin aux fonctionnaires pressés du quartier, "
        "et le chiffre n'a guère bougé depuis, disent les patrons."
    )
    t = _texte(TITRE, CHAPO, loin, HORS)
    ex = lw.extrait_utile(t, QUESTIONS, len(_texte(TITRE, CHAPO).split()) + 40)
    assert loin not in ex
    # Un nombre loin du seul mot des questions (« appel ») ne fait pas passer le paragraphe devant un meilleur score.
    loin_appel = (
        "En 1998, sur les terrasses du quartier, on servait des cafés aux fonctionnaires pressés, aux "
        "lobbyistes et aux journalistes, et longtemps après on parlait encore de cet appel."
    )
    t = _texte(TITRE, CHAPO, loin_appel, HORS, VOISIN, HORS)
    place = max(len(VOISIN.split()), len(loin_appel.split()))
    ex = lw.extrait_utile(t, QUESTIONS, len(_texte(TITRE, CHAPO).split()) + place)
    assert VOISIN in ex and loin_appel not in ex


def test_mots_plies_et_mots_vides():
    # Casse et accents neutralisés : « CANDIDATURE », « Déclarés » ; « les », « à », « de » ne comptent pas.
    plie = "CANDIDATURE DÉPOSÉE : les CONSORTIUMS se sont DÉCLARÉS hier soir devant la presse réunie."
    vide = "Il est à la fois de la et des les une un pour par sur dans avec sans sous entre chez vers."
    t = _texte(TITRE, CHAPO, vide, HORS, plie, HORS)
    ex = lw.extrait_utile(t, QUESTIONS, len(_texte(TITRE, CHAPO, plie).split()) + 3)
    assert plie in ex and vide not in ex


def test_le_budget_est_tenu():
    t = _texte(TITRE, CHAPO, *([FORT, VOISIN, CHIFFRE, HORS] * 30))
    for budget in (100, 300, 600):
        assert len(lw.extrait_utile(t, QUESTIONS, budget).split()) <= budget


def test_le_budget_par_defaut_existe_et_le_texte_lu_reste_entier(serveur):
    assert lw.BUDGET_EXTRAIT == 4000
    p = lw.lire(serveur.servir(MEMO))
    assert "Tenders must be submitted electronically" in p["texte"]  # le tri ne touche pas au texte lu


# ── 4. Blocs que trafilatura laisse : fiches, listes de définitions, tableaux clé-valeur (03.11.2026) ─────────────
# Pages réelles figées (scripts, styles et attributs Vue retirés) : les citations des dossiers du reporter qui étaient
# dans le HTML mais pas dans le texte lu.


def _dans(citation: str, texte: str) -> bool:
    c = lw._plier(citation)
    return f" {c} " in f" {lw._plier(texte)} "


@pytest.mark.parametrize(
    "fixture,citation",
    [
        ("lecture_web_algoritmeregister_kapelle.html", "Laatst gewijzigd op 17 december 2024 om 14:09"),
        ("lecture_web_algoritmeregister_fiche.html", "Begindatum 2025-09"),
        ("lecture_web_algoritmeregister_recherche.html", "AI Chatbot Robin"),
        (
            "lecture_web_normattiva_dlgs_179_2026.html",
            "l'Agenzia per l'Italia digitale (AgID) e l'Agenzia per la cybersicurezza nazionale (ACN)",
        ),
        (
            "lecture_web_bundestag_kw13.html",
            "Jedoch nur, wenn die Bundesnetzagentur als kooperative Schaltzentrale ausreichend ausgestattet wird",
        ),
    ],
)
def test_une_citation_du_html_se_retrouve_dans_le_texte_lu(serveur, fixture, citation):
    p = lw.lire(serveur.servir((FIXTURES / fixture).read_text(encoding="utf-8")))
    assert _dans(citation, p["texte"]), citation


def test_les_credits_et_les_vignettes_ne_sont_pas_rattrapes(serveur):
    p = lw.lire(serveur.servir((FIXTURES / "lecture_web_bundestag_kw13.html").read_text(encoding="utf-8")))
    assert "© Deutscher Bundestag/ Inga Haar" not in p["texte"]
    assert "Lemke, Sonja" not in p["texte"]  # vignette de profil (teaser)


def test_liste_de_definitions_et_tableau_cle_valeur(serveur):
    html = page(FOND, FOND2).replace(
        "</article>",
        (
            "<dl><dt>Leverancier</dt><dd>SafeGPT</dd><dt>Begindatum</dt><dd>2026-07</dd></dl>"
            "<table><tr><th>Bewaartermijn</th><td>60 dagen</td></tr><tr><th>Status</th><td>In gebruik</td></tr></table>"
            "</article>"
        ),
    )
    p = lw.lire(serveur.servir(html))
    for c in ("Leverancier SafeGPT", "Begindatum 2026-07", "Bewaartermijn 60 dagen", "Status In gebruik"):
        assert _dans(c, p["texte"]), c
    assert FOND in p["texte"]


def test_une_balise_html_fermee_trop_tot_ne_coupe_pas_la_page(serveur):
    html = page(FOND).replace("</article>", "</article></div></html>\n<div><p>" + FOND2 + "</p></div>")
    p = lw.lire(serveur.servir(html))
    assert FOND2 in p["texte"]


def test_une_page_bien_lue_ne_gagne_rien(serveur):
    p = lw.lire(serveur.servir(MEMO))
    assert p["voie"] == "trafilatura"
    assert "Sign up for our newsletter" not in p["texte"]


@pytest.mark.parametrize(
    "avant,apres,intrus",
    [
        (
            "<div class='kop'><a href='/a'>Over het register</a> <a href='/b'>Contact en hulp</a></div><div>",
            "</div>",
            "Contact en hulp",
        ),  # en-tête : avant le premier bloc gardé
        (
            "<main>",
            "</main><div><div>Andere berichten over parkeren en afval in de gemeente</div></div>",
            "Andere berichten over parkeren",
        ),  # hors de <main>
        ("<article>", "<div><a href='#'>Afdrukken</a></div></article>", "Afdrukken"),  # un mot seul : un bouton
    ],
)
def test_le_rattrapage_ne_reprend_ni_en_tete_ni_hors_zone_ni_bouton(serveur, avant, apres, intrus):
    html = f"<html><body>{avant}<p>{FOND}</p><p>{FOND2}</p>{apres}</body></html>"
    p = lw.lire(serveur.servir(html))
    assert FOND2 in p["texte"] and intrus not in p["texte"]


def test_un_bloc_repris_ne_l_est_qu_une_fois(serveur):
    # Les fiches de l'algoritmeregister répètent leurs champs (vue mobile et vue large).
    lien = "<div><a href='/veld'>Bewaartermijn 60 dagen na afloop</a></div>"
    html = f"<html><body><article><p>{FOND}</p>{lien}<p>{FOND2}</p>{lien}</article></body></html>"
    p = lw.lire(serveur.servir(html))
    assert p["texte"].count("Bewaartermijn 60 dagen na afloop") == 1


# ── 5. EUR-Lex par la voie officielle, page trop courte, PDF (03.11.2026) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "url,celex,langue",
    [
        ("https://eur-lex.europa.eu/eli/reg/2024/1689/oj", "32024R1689", "eng"),
        ("https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng", "32024R1689", "eng"),
        ("https://eur-lex.europa.eu/eli/dir/2019/790/oj", "32019L0790", "eng"),
        ("https://eur-lex.europa.eu/eli/dec/2022/2481/oj", "32022D2481", "eng"),
        ("https://eur-lex.europa.eu/eli/reg_impl/2024/2690/oj", "32024R2690", "eng"),
        ("https://eur-lex.europa.eu/legal-content/FR/TXT/HTML/?uri=CELEX:32024R1689", "32024R1689", "fra"),
        ("https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX%3A32016R0679", "32016R0679", "deu"),
        ("https://eur-lex.europa.eu/eli/reg/2024/1689/oj/fra", "32024R1689", "fra"),
    ],
)
def test_une_adresse_eur_lex_donne_son_celex_et_sa_langue(url, celex, langue):
    assert lw.celex_de(url) == (celex, langue)


def test_une_adresse_hors_eur_lex_n_a_pas_de_celex():
    assert lw.celex_de("https://www.sullcrom.com/insights/memo/2026/October/x") is None


def test_eur_lex_se_lit_par_cellar_et_rend_le_texte_entier(serveur, monkeypatch):
    xhtml = (FIXTURES / "lecture_web_cellar_32024R1689_debut.xhtml").read_text(encoding="utf-8")
    serveur.servir(xhtml, chemin="/celex/32024R1689", genre="application/xhtml+xml;charset=UTF-8")
    monkeypatch.setattr(lw, "CELLAR", serveur.base + "/celex/")
    p = lw.lire("https://eur-lex.europa.eu/eli/reg/2024/1689/oj")
    assert serveur.recus and serveur.recus[0][":chemin"] == "/celex/32024R1689"
    e = {k.lower(): v for k, v in serveur.recus[0].items()}
    assert e["accept"].startswith("application/xhtml+xml") and e["accept-language"] == "eng"
    assert p["voie"].startswith("eur-lex (cellar")
    assert p["titre"].startswith("REGULATION (EU) 2024/1689 OF THE EUROPEAN PARLIAMENT")
    assert "laying down harmonised rules on artificial intelligence" in p["texte"]
    assert p["mots"] > 2000


def test_le_mur_aws_d_eur_lex_est_reconnu(serveur):
    mur = (FIXTURES / "lecture_web_eurlex_mur_aws.html").read_text(encoding="utf-8")
    p = lw.lire(serveur.servir(mur, statut=202))
    assert p["texte"] == "" and "mur anti-robot" in p["voie"]


def test_une_page_trop_courte_le_dit(serveur):
    p = lw.lire(serveur.servir(page("Un avis court, sans plus de texte que ces quelques mots ici.")))
    assert p["mots"] < 80 and p["voie"].startswith("page trop courte")
    assert lw.lire_plusieurs([serveur.servir(page("Encore une page courte."), chemin="/c")]) == []


def _pdf(*lignes: str) -> bytes:
    """Un PDF minimal d'une page, une ligne de texte par ligne (Helvetica)."""
    flux = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lignes) + " ET"
    objets = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(flux)} >>\nstream\n{flux}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, pos = "%PDF-1.4\n", []
    for i, o in enumerate(objets, 1):
        pos.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n" + "".join(f"{p:010d} 00000 n \n" for p in pos)
    out += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def test_un_pdf_se_lit_localement(serveur):
    mots = (FOND + " " + FOND2).split()
    lignes = [" ".join(mots[i : i + 12]) for i in range(0, len(mots), 12)]
    lignes *= 3
    p = lw.lire(serveur.servir(_pdf(*lignes), chemin="/memo.pdf", genre="application/pdf"))
    assert p["voie"] == "pdf (pypdf)"
    assert "supervisory framework applies" in " ".join(p["texte"].split())
    assert p["mots"] >= 80


# ── 6. Tri bilingue : les libellés anglais des notions du graphe qui touchent les questions (03.11.2026) ──────────


def _graphe(tmp_path):
    import json

    noeuds = [
        {"id": "c_ai_act", "label": "AI Act"},
        {"id": "c_msa", "label": "Market Surveillance Authority"},
        {"id": "c_notif", "label": "Notifying Authority"},
        {"id": "c_bnetza", "label": "Bundesnetzagentur"},
        {"id": "c_quantum", "label": "Quantum Computing"},
        {"id": "a_doe", "label": "Jane Doe", "kind": "auteur"},
        {"id": "f_fiche", "label": "AI Act enforcement in Germany", "kind": "article"},
    ]
    liens = [{"source": "c_ai_act", "target": t} for t in ("c_msa", "c_notif", "a_doe", "f_fiche")]
    liens += [{"source": "c_bnetza", "target": "c_msa"}, {"source": "c_quantum", "target": "a_doe"}]
    p = tmp_path / "graphe.json"
    p.write_text(json.dumps({"directed": False, "multigraph": False, "graph": {}, "nodes": noeuds, "links": liens}))
    return p


def test_les_termes_du_graphe_sont_les_notions_nommees_et_leurs_voisines(tmp_path):
    termes = lw.termes_du_graphe(
        ["Qui contrôle l'AI Act en Allemagne ?", "La Bundesnetzagentur a-t-elle des moyens ?"], _graphe(tmp_path)
    )
    assert "AI Act" in termes and "Bundesnetzagentur" in termes
    assert "Market Surveillance Authority" in termes and "Notifying Authority" in termes  # voisines
    assert "Jane Doe" not in termes  # un auteur n'est pas une notion
    assert "AI Act enforcement in Germany" not in termes  # une fiche non plus
    assert "Quantum Computing" not in termes  # ne touche pas les questions


def test_sans_nom_propre_ni_graphe_pas_de_terme(tmp_path):
    assert lw.termes_du_graphe(["Est-ce nouveau ?"], _graphe(tmp_path)) == []
    assert lw.termes_du_graphe(["Qui contrôle l'AI Act ?"], tmp_path / "absent.json") == []


def test_les_termes_font_entrer_un_paragraphe_anglais_pour_une_question_francaise():
    anglais = (
        "Each Member State shall designate at least one market surveillance authority and one notifying "
        "authority, and give them adequate technical, financial and human resources for their tasks."
    )
    t = _texte(TITRE, CHAPO, HORS, anglais, HORS, HORS)
    questions = ["Qui sont les autorités, avec quels moyens ?"]
    budget = len(_texte(TITRE, CHAPO, anglais).split()) + 3
    assert anglais not in lw.extrait_utile(t, questions, budget)
    assert anglais in lw.extrait_utile(t, questions, budget, termes=["Market Surveillance Authority"])


def test_les_pieges_du_graphe_restent_dehors(tmp_path):
    import json

    noeuds = [
        {"id": "x1", "label": "Axel Springer c. Allemagne"},  # « Allemagne », mot ordinaire : à l'identique
        {"id": "x2", "label": "Univac 90/30"},  # « 30 » seul : pas un nom
        {"id": "x3", "label": "KI-MIG Begründung des Bundesrates vom Juni 2026"},  # plus de cinq mots
        {"id": "x4", "label": "KI-MIG Entwurf"},
        {"id": "e1", "label": "EuroHPC"},
        {"id": "e2", "label": "EuroHPC JU Governing Board"},
        {"id": "e3", "label": "EuroHPC Call 2026"},
        {"id": "e4", "label": "EuroHPC Summit Week"},
        {"id": "e5", "label": "EuroHPC Mission Statement"},
        {"id": "h", "label": "Hub"},
    ]
    liens = [{"source": "h", "target": t} for t in ("e2", "e3", "e4", "e5", "x1", "x2")]
    p = tmp_path / "pieges.json"
    p.write_text(json.dumps({"directed": False, "multigraph": False, "graph": {}, "nodes": noeuds, "links": liens}))
    termes = lw.termes_du_graphe(["Le 30 juillet, en Allemagne, le KI-MIG et EuroHPC ont-ils avancé ?"], p)
    assert "Axel Springer c. Allemagne" not in termes and "Univac 90/30" not in termes
    assert "KI-MIG Entwurf" in termes and "KI-MIG Begründung des Bundesrates vom Juni 2026" not in termes
    assert "EuroHPC" in termes  # le nom exact passe avant les libellés plus reliés qui le contiennent
    assert "EuroHPC JU Governing Board" not in termes

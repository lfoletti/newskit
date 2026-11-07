"""Les collecteurs : les fonctions pures de chacun, sans réseau.

Ce qui parle au réseau n'est pas testé ici ; ce qui décide de ce qu'on garde
l'est — dates, titres, filtres, dédoublonnage, conversion HTML.
"""

import xml.etree.ElementTree as ET

import pytest

from newskit import collecte_arxiv as arxiv
from newskit import collecte_pdf_local as pdfl
from newskit import collecte_revue as revue
from newskit import collecte_rss as rss
from newskit import prospecter_source as prosp
from newskit.commun import html_to_md


# --- arXiv : lecture d'une notice OAI-PMH ----------------------------------


def test_notice_oai_lit_une_notice_reelle(fixtures):
    racine = ET.fromstring((fixtures / "oai_1706.03762.xml").read_bytes())
    meta = racine.find(".//ar:arXiv", arxiv.NS_OAI)
    n = arxiv._notice_oai(meta)
    assert n["arxiv_id"] == "1706.03762"
    assert n["title"] == "Attention Is All You Need"
    assert n["authors"][0] == "Ashish Vaswani"
    # Le champ `created` d'OAI-PMH est la date de la dernière version (2023-08-02
    # pour la septième). La date de dépôt vient de l'identifiant : juin 2017.
    assert n["published"] == "2017-06-01"
    assert n["updated"] == "2023-08-03"
    assert n["primaire"] == "cs.CL"
    assert "attention" in n["summary"].lower()


@pytest.mark.parametrize("aid, attendu", [
    ("1706.03762", "2017-06-01"),
    ("2510.04618", "2025-10-01"),
    ("physics/0408067", "2004-08-01"),
    ("math.GT/0309136", "2003-09-01"),
    ("9901.12345", "1999-01-01"),
    ("1713.00001", ""),                 # mois impossible
    ("n'importe quoi", ""),
])
def test_mois_de_depot(aid, attendu):
    assert arxiv.mois_de_depot(aid) == attendu


def test_slug_arxiv_est_court_et_sans_ponctuation():
    assert arxiv._slug("Attention Is All You Need!") == "attention-is-all-you-need"
    assert len(arxiv._slug("x " * 100)) <= 60


def test_filtrer_recolte_garde_un_papier_depose_dans_le_mois_de_la_fenetre():
    """Régression du 19.09.2026 : `published` vaut le premier du mois de dépôt,
    et un `depuis` au 11 rejetait tout papier déposé dans le mois. La fenêtre
    se juge sur la date de dernière modification."""
    recolte = [{"arxiv_id": "9", "title": "knowledge graph", "summary": "", "categories": "cs.AI",
                "published": "2026-09-01", "updated": "2026-09-15"}]
    retenus, _, _ = arxiv.filtrer_recolte(recolte, ["knowledge graph"], {"cs.AI"}, "2026-09-11", connus=set())
    assert list(retenus) == ["9"]


def test_filtrer_recolte_par_theme_distingue_muet_et_connu():
    recolte = [
        {"arxiv_id": "1", "title": "A knowledge graph study", "summary": "", "categories": "cs.AI", "published": "2026-09-10"},
        {"arxiv_id": "2", "title": "Hors catégorie", "summary": "knowledge graph", "categories": "q-bio", "published": "2026-09-10"},
        {"arxiv_id": "3", "title": "Ancien", "summary": "knowledge graph", "categories": "cs.AI", "published": "2020-01-01"},
        {"arxiv_id": "4", "title": "Déjà là", "summary": "knowledge graph", "categories": "cs.AI", "published": "2026-09-10"},
    ]
    retenus, muets, vus = arxiv.filtrer_recolte(
        recolte, ["knowledge graph", "ontology"], {"cs.AI"}, "2026-09-05", connus={"4"})
    assert [n["arxiv_id"] for n in retenus.values()] == ["1"]
    assert retenus["1"]["_theme"] == "knowledge graph"
    assert muets == ["ontology"]
    assert vus["knowledge graph"] == 3           # 1, 3 et 4 : vus, même si pas retenus


# --- RSS -------------------------------------------------------------------


def test_entrees_rss_lit_un_flux_reel(fixtures):
    items = rss.entrees((fixtures / "rss_sony_csl.xml").read_bytes())
    assert len(items) == 1
    it = items[0]
    assert it["titre"] and it["lien"].startswith("https://csl.sony.fr/")
    assert it["date"] and len(it["date"]) == 10
    assert it["complet"] is True                 # le flux porte content:encoded


def test_entrees_atom():
    atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            b'<entry><title>T</title><link href="https://x.org/a"/>'
            b'<published>2026-09-01T10:00:00Z</published><author><name>Qui</name></author>'
            b'<summary>Un r\xc3\xa9sum\xc3\xa9</summary></entry></feed>')
    items = rss.entrees(atom)
    assert items == [{"titre": "T", "lien": "https://x.org/a", "date": "2026-09-01",
                      "auteur": "Qui", "corps": "Un résumé", "complet": False}]


def test_entrees_tolerantes_survivent_a_un_attribut_duplique():
    casse = (b'<rss><channel><item><title>T</title><link>https://x.org/a</link>'
             b'<pubDate>Mon, 01 Sep 2026 10:00:00 +0000</pubDate>'
             b'<description a="1" a="2">D</description></item></channel></rss>')
    with pytest.raises(ET.ParseError):
        rss.entrees(casse)
    items = rss.entrees_tolerantes(casse)
    assert items[0]["titre"] == "T" and items[0]["date"] == "2026-09-01"


@pytest.mark.parametrize("brut, attendu", [
    ("Mon, 01 Sep 2026 10:00:00 +0000", "2026-09-01"),
    ("2026-09-01T10:00:00Z", "2026-09-01"),
    ("2026-09-01", "2026-09-01"),
    ("", ""),
    ("n'importe quoi", ""),
])
def test_date_rss(brut, attendu):
    assert rss._date(brut) == attendu


def test_retenu_par_mots_cles():
    assert rss.retenu_par_mots_cles("Gemini 3 model card", ["model"]) is True
    assert rss.retenu_par_mots_cles("3 ways to prep for your race", ["model", "AI"]) is False
    assert rss.retenu_par_mots_cles("n'importe", []) is True


def test_un_mot_cle_borne_d_espaces_retient_le_mot_en_tete_ou_en_fin_de_titre():
    # 03.11.2026 : « AI will not set its own limits in warfare. States must. » (CICR) et « Systems Thinking Is Vital in
    # the Age of AI » (IEEE Spectrum) échappaient à « AI » : le mot ouvre ou ferme le titre, sans espace autour.
    assert rss.retenu_par_mots_cles("AI will not set its own limits in warfare", [" AI "]) is True
    assert rss.retenu_par_mots_cles("Systems Thinking Is Vital in the Age of AI", [" AI "]) is True
    assert rss.retenu_par_mots_cles("Art. 6 DSG", [" DSG "]) is True
    assert rss.retenu_par_mots_cles("Paid search", [" AI "]) is False


def test_slug_rss_prend_le_dernier_segment_de_l_url():
    assert rss.slug("https://x.org/blog/Mon-Billet/", "Titre") == "mon-billet"
    assert rss.slug("", "Un Titre !") == "un-titre"


# --- PDF local -------------------------------------------------------------


def test_date_de_prend_l_identifiant_arxiv_avant_tout(tmp_path):
    p = tmp_path / "2019.x.pdf"; p.write_bytes(b"")
    assert pdfl.date_de(p, {"CreationDate": "D:20240101"}, "1706.03762") == ("2017-06-01", "identifiant arXiv (année et mois de dépôt)")


def test_date_de_puis_annee_en_tete_du_nom(tmp_path):
    p = tmp_path / "2011.SG_Urban_design.pdf"; p.write_bytes(b"")
    assert pdfl.date_de(p, {"CreationDate": "D:20240908"}, "")[0] == "2011-01-01"


def test_date_de_puis_metadonnees_puis_annee_dans_le_nom_puis_fichier(tmp_path):
    p = tmp_path / "01.ImageNet.NIPS-2012-paper.pdf"; p.write_bytes(b"")
    assert pdfl.date_de(p, {"CreationDate": "D:20240913"}, "")[0] == "2024-09-13"   # métadonnées avant l'année dans le nom
    assert pdfl.date_de(p, {}, "") == ("2012-01-01", "année trouvée dans le nom de fichier")
    q = tmp_path / "sans-annee.pdf"; q.write_bytes(b"")
    date, origine = pdfl.date_de(q, {}, "")
    assert len(date) == 10 and "faute de mieux" in origine


def test_lier_references_rend_les_arxiv_cliquables_sans_toucher_aux_liens_existants():
    texte = "voir arXiv:1706.03762 et arXiv preprint arXiv:2006.11477v2, déjà [x](https://arxiv.org/abs/1)"
    lie, n = pdfl.lier_references(texte)
    assert n == 2
    assert "[arXiv:1706.03762](https://arxiv.org/abs/1706.03762)" in lie
    assert "[arXiv:2006.11477](https://arxiv.org/abs/2006.11477)" in lie
    assert lie.count("https://arxiv.org/abs/1)") == 1


def test_titre_de_refuse_un_nom_de_revue_et_prend_la_premiere_vraie_ligne(tmp_path):
    p = tmp_path / "sustainability-12-04393-v2.pdf"
    texte = "sustainability\n\nArticle\nSelf-Organising Floor Plans in Care Homes\nAuteurs"
    assert pdfl.titre_de(p, {}, texte) == "Self-Organising Floor Plans in Care Homes"
    assert pdfl.titre_de(p, {"Title": "Microsoft Word - doc"}, texte) == "Self-Organising Floor Plans in Care Homes"
    assert pdfl.titre_de(p, {"Title": "Un vrai titre de document"}, texte) == "Un vrai titre de document"


def test_identifiant_arxiv_depuis_le_nom_ou_le_texte(tmp_path):
    p = tmp_path / "05.Clip.2103.00020v1.pdf"
    assert pdfl.identifiant_arxiv(p, "") == "2103.00020"
    q = tmp_path / "x.pdf"
    assert pdfl.identifiant_arxiv(q, "… arXiv:1706.03762 …") == "1706.03762"
    assert pdfl.identifiant_arxiv(q, "rien") == ""


# --- Substack : la seconde horloge ne bouge pas à vide ----------------------


def test_collected_a_garder_ne_change_pas_une_fiche_identique(tmp_path):
    from newskit import collecte_substack as sub
    from newskit import fiche as F
    f = tmp_path / "x.md"
    F.ecrire(f, {"title": "t", "date": "2026-01-01", "collected": "2026-01-02",
                 "url": "https://x.substack.com/p/t", "complet": False}, "# t\n\nmême corps")
    assert sub.collected_a_garder(f, "# t\n\nmême corps\n") == "2026-01-02"
    assert sub.collected_a_garder(f, "# t\n\nautre corps") is None
    assert sub.collected_a_garder(tmp_path / "absente.md", "…") is None


# --- Crossref --------------------------------------------------------------


def test_resume_crossref_retire_le_jats():
    brut = "<jats:p>Un <jats:italic>résumé</jats:italic> &amp; du texte.</jats:p><jats:title>Abstract</jats:title>"
    assert revue.resume(brut) == "Un résumé & du texte."
    assert revue.resume("") == ""


def test_date_crossref_prefere_l_impression_et_complete_les_parties():
    it = {"published-print": {"date-parts": [[2020, 5]]}, "issued": {"date-parts": [[2019, 1, 1]]}}
    assert revue.date_de(it) == "2020-05-01"
    assert revue.date_de({"created": {"date-parts": [[2021]]}}) == "2021-01-01"
    assert revue.date_de({}) == ""


def test_retenu_crossref_filtre_sur_le_titre():
    assert revue.retenu("Deep learning for BIM", ["deep learning"]) is True
    assert revue.retenu("Concrete curing", ["deep learning"]) is False
    assert revue.retenu("n'importe", []) is True


# --- prospection -----------------------------------------------------------


def test_verdict_prospection():
    assert prosp.verdict(None) == ("NON", "ni API Substack ni flux lisible")
    eteinte = {"dernier": "2020-01-01", "mots": 900, "note": "le flux porte le texte"}
    assert prosp.verdict(eteinte)[0] == "NON"
    maigre = {"dernier": "2026-09-01", "mots": 12, "note": "des entrefilets"}
    assert prosp.verdict(maigre)[0] == "NON"
    page = {"dernier": "2026-09-01", "mots": 12, "note": "flux sans texte : il faudra lire la page (page: true)"}
    assert prosp.verdict(page)[0] == "OUI"
    bonne = {"dernier": "2026-09-01", "mots": 900, "note": "le flux porte le texte"}
    assert prosp.verdict(bonne) == ("OUI", "le flux porte le texte")


def test_bloc_yaml_propose_page_true_quand_le_flux_est_maigre():
    r = {"voie": "flux", "adresse": "https://x.org/feed/", "mots": 12, "note": ""}
    bloc = prosp.bloc_yaml("x.org", r)
    assert "plateforme: Flux RSS" in bloc and "page: true" in bloc
    r2 = {"voie": "Substack", "adresse": "https://x.substack.com", "mots": 900, "note": ""}
    assert "plateforme: Substack" in prosp.bloc_yaml("x.substack.com", r2)


# --- conversion HTML -------------------------------------------------------


def test_html_to_md_titres_liens_listes_et_code():
    html = ('<h2>Titre</h2><p>Un <b>gras</b> et <a href="https://x.org/?trk=1">lien</a>.</p>'
            '<ul><li>un</li><li>deux</li></ul><pre>code<b>x</b></pre><script>rien</script>')
    md = html_to_md(html)
    assert "### Titre" in md
    assert "**gras**" in md
    assert "[lien](https://x.org/)" in md
    assert "- un" in md and "- deux" in md
    assert "```\ncodex\n```" in md
    assert "rien" not in md


def test_le_mur_membres_est_reconnu_dans_le_texte_de_page():
    from newskit import collecte_rss as rss
    assert rss.mur_membres("Daily Newsletter\n\n### This story is for members\n\nCreate a free account to continue reading.")
    assert rss.mur_membres("This Deep Dive is for members\nBecome a member")
    assert not rss.mur_membres("Members of the team shipped a new model this week, with 2 000 words of detail.")


def test_le_mur_membres_reconnait_le_palier_payant():
    from newskit import collecte_rss as rss
    assert rss.mur_membres("This Deep Dive is for paid members. Upgrade to continue.")
    assert rss.mur_membres("This issue is for premium subscribers")
    assert not rss.mur_membres("Premium GPUs are for members of the consortium's compute pool.")


SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://x.test/a/</loc><lastmod>2026-09-25T10:00:00.000Z</lastmod></url>
<url><loc>https://x.test/b/</loc><lastmod>2026-05-01T10:00:00.000Z</lastmod></url>
<url><loc>https://x.test/c/</loc><lastmod>2026-09-26T10:00:00.000Z</lastmod></url>
<url><loc>https://x.test/sans-date/</loc></url>
</urlset>"""


def test_le_plan_de_site_filtre_sur_lastmod_et_garde_les_plus_recents_en_tete():
    from newskit import collecte_rss as rss
    items = rss.plan_de_site(SITEMAP, "2026-09-01")
    assert [i["lien"] for i in items] == ["https://x.test/c/", "https://x.test/a/", "https://x.test/sans-date/"]
    assert all(i["corps"] == "" and i["titre"] == "" for i in items)      # tout viendra de la page
    assert len(rss.plan_de_site(SITEMAP, "")) == 4


PAGE_GHOST = """<html><head><title>Titre de repli</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article",
"headline":"🛎️ The Agent Era Moved Downstairs","datePublished":"2026-09-24T10:00:17.000Z",
"author":{"@type":"Person","name":"Magna"},"keywords":"Daily Rundown"}</script></head><body></body></html>"""


def test_les_metadonnees_de_page_viennent_du_ld_json_puis_du_title():
    from newskit import collecte_rss as rss
    m = rss.meta_de_page(PAGE_GHOST)
    assert m == {"titre": "🛎️ The Agent Era Moved Downstairs", "date": "2026-09-24", "auteur": "Magna", "mots_cles": "Daily Rundown",
                 "date_publication": "2026-09-24"}          # la date que la page déclare elle-même (éclaireur, 03.11.2026)
    assert rss.meta_de_page("<html><head><title>Seul le &amp; titre</title></head></html>")["titre"] == "Seul le & titre"


def test_un_theme_muet_sur_deux_fenetres_sort_du_registre_sans_toucher_aux_commentaires(tmp_path):
    from newskit import collecte_arxiv as ca
    reg = tmp_path / "sources.yaml"
    reg.write_text("veille_arxiv:\n  themes:\n    - knowledge graph\n    # un commentaire\n    - shape grammar\n    - agent memory\n", encoding="utf-8")
    etat = tmp_path / "_muets.json"
    assert ca.retirer_themes_muets(["shape grammar"], "2026-09-20", reg, etat) == []          # première fenêtre
    assert ca.retirer_themes_muets(["shape grammar"], "2026-09-22", reg, etat) == []          # trop proche
    assert ca.retirer_themes_muets(["shape grammar", "agent memory"], "2026-09-27", reg, etat) == ["shape grammar"]
    texte = reg.read_text(encoding="utf-8")
    assert "    - shape grammar" not in texte and "retiré le 2026-09-27" in texte
    assert "# un commentaire" in texte and "    - agent memory" in texte
    import json
    assert json.loads(etat.read_text()) == {"agent memory": ["2026-09-27"]}                 # shape grammar oublié, agent memory compte
    assert ca.retirer_themes_muets([], "2026-10-04", reg, etat) == []
    assert json.loads(etat.read_text()) == {}                                                # redevenu visible : compteur remis


def test_repartir_range_chaque_fiche_de_veille_dans_le_dossier_de_son_theme(tmp_path, monkeypatch, capsys):
    from newskit import collecte_arxiv as ca
    from newskit import fiche
    principal, vision = tmp_path / "sources" / "arxiv", tmp_path / "sources" / "arxiv_vision_3d"
    principal.mkdir(parents=True)

    def ecrire(nom, aid, source):
        fiche.ecrire(principal / nom, {"title": nom, "date": "2026-09-01", "url": f"https://arxiv.org/abs/{aid}",
                                       "plateforme": "arXiv", "publication": "arXiv", "complet": False,
                                       "collected": "2026-09-02", "source": source, "arxiv_id": aid}, "# t\n\ncorps\n")
    ecrire("a.md", "2609.00001", "arXiv (veille par moisson, thème « gaussian splatting »)")   # part
    ecrire("b.md", "2609.00002", "arXiv (veille par moisson, thème « gaussian splatting »)")   # cité : copié
    ecrire("c.md", "2609.00003", "arXiv (veille par moisson, thème « knowledge graph »)")      # reste
    ecrire("d.md", "2609.00004", "arXiv (cité par 3 fiche(s))")                                # sans thème : reste
    monkeypatch.setattr(ca, "RACINE", tmp_path); monkeypatch.setattr(ca, "OUT", principal)
    monkeypatch.setattr(ca, "DOSSIERS_ARXIV", [principal, vision])
    monkeypatch.setattr(ca, "VEILLES", [{"graphe": "ai-science", "dossier": "arxiv", "themes": ["knowledge graph"]},
                                        {"graphe": "vision-3d", "dossier": "arxiv_vision_3d", "themes": ["gaussian splatting"]}])
    monkeypatch.setattr(ca, "identifiants_cites", lambda: {"2609.00002": 1})

    ca.mode_repartir(controle=True)
    assert not vision.exists() and "2 fiche(s)" in capsys.readouterr().out          # --controle n'écrit rien
    ca.mode_repartir(controle=False)
    assert sorted(f.name for f in vision.glob("*.md")) == ["a.md", "b.md"]
    assert sorted(f.name for f in principal.glob("*.md")) == ["b.md", "c.md", "d.md"]
    capsys.readouterr()
    ca.mode_repartir(controle=False)                                                # rejouable : plus rien à faire
    assert "déjà dans le dossier" in capsys.readouterr().out


def test_atom_prend_le_lien_alternate_et_non_le_flux_des_commentaires():
    from newskit import collecte_rss as cr
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Billet</title>
      <link rel="alternate" type="text/html" href="https://exemple.org/2026/billet/"/>
      <published>2026-09-28T06:59:06Z</published>
      <link rel="replies" type="text/html" href="https://exemple.org/2026/billet/#comments"/>
      <link rel="replies" type="application/atom+xml" href="https://exemple.org/2026/billet/feed/atom/"/>
      <summary>Résumé.</summary></entry></feed>"""
    (item,) = cr.entrees(xml.encode("utf-8"))
    assert item["lien"] == "https://exemple.org/2026/billet/"


def _graphe_avec_un_papier_de_l_eclaireur(tmp_path, monkeypatch, aid):
    """Un graphe dont l'éclaireur a déjà la page d'un papier (avec `arxiv_id`), hors de son dossier arXiv."""
    from newskit import collecte_arxiv as ca
    from newskit import config_sources as cfg
    from newskit import fiche
    eclaireur = tmp_path / "sources" / "eclaireur" / "g"
    (eclaireur / "sous").mkdir(parents=True)
    fiche.ecrire(eclaireur / "sous" / "page.md", {"title": "page", "date": "2023-02-23",
                 "url": f"https://arxiv.org/abs/{aid}", "plateforme": "Recherche web", "publication": "arxiv.org",
                 "complet": True, "collected": "2026-10-17", "source": "trouvé par l'éclaireur",
                 "arxiv_id": aid}, "# page\n\ncorps\n")
    monkeypatch.setattr(ca, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "dossiers_du_graphe", lambda nom, propres=False: ["sources/eclaireur/g", "sources/arxiv_g"])
    # L'index d'identité de fiche (06.11.2026) lit la racine et la carte des graphes par ses propres points d'entrée.
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: {"g": ["sources/eclaireur/g", "sources/arxiv_g"]})
    fiche.oublier_index()
    return tmp_path / "sources" / "arxiv_g"


def test_un_jalon_que_l_eclaireur_a_deja_ne_recoit_pas_de_seconde_fiche(tmp_path, monkeypatch):
    # 19.10.2026 : 2302.12173, page gardée par l'éclaireur le 17, refaite en jalon dans arxiv_cyberresistance ;
    # deux fiches sous un nœud, le contrôle de fraîcheur a bloqué ai-security quatre nuits.
    from newskit import choisir_jalons as cj
    from newskit import collecte_arxiv as ca
    from newskit import config_sources as cfg
    _graphe_avec_un_papier_de_l_eclaireur(tmp_path, monkeypatch, "2302.12173")
    monkeypatch.setattr(cj, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "veilles_arxiv", lambda: [{"graphe": "g", "dossier": "arxiv_g", "themes": []}])
    monkeypatch.setattr(cj, "choisis_a_la_main", lambda graphe=None: ["2302.12173"])
    demandes = []
    monkeypatch.setattr(ca, "par_identifiants", lambda ids: (demandes.extend(ids), ([], []))[1])
    cj.collecter_les_choisis("g")
    assert demandes == []


def test_la_veille_ne_reprend_pas_un_papier_que_l_eclaireur_a_deja(tmp_path, monkeypatch):
    from newskit import collecte_arxiv as ca
    dossier = _graphe_avec_un_papier_de_l_eclaireur(tmp_path, monkeypatch, "2610.00001")
    notice = {"arxiv_id": "2610.00001", "title": "Indirect prompt injection", "summary": "", "categories": "cs.CR",
              "published": "2026-10-01", "updated": "2026-10-20"}
    monkeypatch.setattr(ca, "VEILLES", [{"graphe": "g", "dossier": "arxiv_g", "themes": ["prompt injection"],
                                         "categories": ["cs.CR"]}])
    monkeypatch.setattr(ca, "moisson_oai", lambda depuis: [notice])
    monkeypatch.setattr(ca, "retirer_themes_muets", lambda *a, **k: [])
    ecrites = []
    monkeypatch.setattr(ca, "ecrire", lambda n, **k: ecrites.append(n["arxiv_id"]))
    ca.mode_veille(30, graphe="g")
    assert ecrites == [] and not any(dossier.glob("*.md"))


def test_l_api_d_une_page_se_lit_par_son_segment_en_json_et_par_un_champ_imbrique(monkeypatch):
    # 03.11.2026 : la salle de presse de la Commission est une application Angular ; l'article se lit par
    # /api/documents?reference=IP/26/2303 (le segment « ip_26_2303 » de l'adresse, en majuscules, « _ » → « / »),
    # sous Accept JSON (sinon 406 ou corps vide), et son texte est dans docuLanguageResource.htmlContent.
    import json
    import types
    appels = []

    def faux(url, essais=3, navigateur=False, accept=None):
        appels.append((url, accept))
        return json.dumps({"docuLanguageResource": {"title": "Titre", "htmlContent": "<p>" + "mot " * 200 + "</p>"},
                           "publishDate": "2026-10-30T11:45:13.000+01:00"}).encode()
    monkeypatch.setattr(rss, "recuperer", faux)
    src = types.SimpleNamespace(
        api_page="https://ec.europa.eu/commission/presscorner/api/documents?reference={segment}&language=en",
        api_segment={"majuscules": True, "remplacer": {"_": "/"}},
        api_champs={"titre": "docuLanguageResource.title", "date": "publishDate", "corps": ["docuLanguageResource.htmlContent"]},
        propres={})
    n, md, mur, meta = rss.page_par_api(src, "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_2303")
    assert appels == [("https://ec.europa.eu/commission/presscorner/api/documents?reference=IP%2F26%2F2303&language=en",
                       "application/json")]
    assert n == 200 and meta["titre"] == "Titre" and meta["date"] == "2026-10-30"

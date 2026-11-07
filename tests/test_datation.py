"""La date d'une fiche de l'éclaireur est celle de la publication du document, jamais celle de sa collecte.

Trois cas réels faux (03.11.2026) : Gleiss Lutz daté 2024-08-01 (le 1er août 2024, entrée en vigueur de l'AI Act, lu
dans le texte ; publié le 23.02.2026) ; Heuking daté 2025-09-12 (« applicable since 12 September 2025 » dans le
texte ; la page dit 09-01-2026) ; DCD daté 2026-11-02, jour de la collecte (publié le 14.02.2025). Ordre décidé : les
métadonnées de publication de la page (article:published_time, JSON-LD datePublished, <time datetime> de l'article),
puis la date du lecteur web, puis l'adresse ; sans date, « date inconnue ». Les HTML ci-dessous reproduisent chaque cas.
"""

from __future__ import annotations

from newskit import collecte_rss as cr
from newskit import eclaireur as ec
from newskit import fiche

GLEISS = (
    '<html><head><meta property="article:published_time" content="2026-02-23T09:00:00+01:00"></head><body>'
    "<article><p>The AI Act entered into force on 1 August 2024. The Federal Government draft bill …</p></article>"
    "</body></html>"
)
HEUKING = (
    "<html><body><p>09-01-2026</p><article><h2>DATA ACT: Access by Design</h2><p>Although the Regulation has "
    "largely been applicable since 12 September 2025, another cut-off date will take effect on 12 September 2026."
    "</p></article></body></html>"
)
DCD = (
    '<html><head><script type="application/ld+json">{"@type": "NewsArticle", "headline": "EU allocates 20bn", '
    '"datePublished": "2025-02-14T10:00:00Z"}</script></head><body><p>AI gigafactories.</p></body></html>'
)
TIME = '<html><body><time datetime="2020-01-01">menu</time><article><time datetime="2026-03-05T08:00">5 mars</time></article></body></html>'


def test_la_meta_de_publication_passe_avant_une_date_du_texte():
    assert cr.date_de_publication(GLEISS) == "2026-02-23"
    assert cr.date_de_publication(DCD) == "2025-02-14"
    assert cr.date_de_publication(TIME) == "2026-03-05"


def test_une_date_lue_dans_le_texte_n_est_pas_une_date_de_publication():
    assert cr.date_de_publication(HEUKING) == ""


def test_l_ordre_meta_puis_lecteur_puis_adresse_jamais_la_collecte():
    j = "2026-11-03"
    assert ec.date_du_document("2026-02-23", "2024-08-01", "https://x.org/2023/05/01/a", j) == (
        "2026-02-23",
        "méta de publication",
    )
    assert ec.date_du_document("", "2026-09-01", "https://x.org/a", j) == ("2026-09-01", "lecteur web")
    assert ec.date_du_document("", "", "https://x.org/news/2025/02/14/a", j) == ("2025-02-14", "adresse")
    assert ec.date_du_document("", "", "https://x.org/speeches/20261102-ai", j) == ("2026-11-02", "adresse")
    assert ec.date_du_document("", "", "https://x.org/a", j) == ("", "inconnue")
    assert ec.date_du_document("2027-01-01", "", "https://x.org/a", j) == ("", "inconnue")  # à venir : pas la page


def _ingerer(tmp_path, monkeypatch, html_meta, lu_date, page_date):
    monkeypatch.setattr(ec, "DOSSIER", tmp_path)
    monkeypatch.setattr(cr, "corps_de_page", lambda url: (500, "texte " * 500, False, html_meta))
    from newskit import lecture_web

    monkeypatch.setattr(lecture_web, "lire", lambda url: {"date": lu_date, "mots": 500, "titre": "", "texte": ""})
    memoire = {"domaines": {}}
    ec.ingerer(
        "g",
        {
            "url": "https://www.datacenterdynamics.com/en/news/eu-allocates",
            "titre": "EU allocates 20bn",
            "date": page_date,
            "point": 0,
        },
        [("sujet", "Financements")],
        {"mots_min": 100},
        set(),
        memoire,
        "2026-11-02",
    )
    (f,) = list((tmp_path / "g").glob("*.md"))
    return f, fiche.lire_fichier(f)[0]


def test_sans_meta_ni_lecteur_la_fiche_dit_date_inconnue(tmp_path, monkeypatch):
    """Le cas DCD : la date que le modèle de l'éclaireur avait donnée (le jour même) et la collecte ne datent pas."""
    f, champs = _ingerer(
        tmp_path,
        monkeypatch,
        {"titre": "EU allocates 20bn", "date": "2025-09-12", "date_publication": ""},
        "",
        "2026-11-02",
    )
    assert champs["date"] == "" and champs["origine_date"] == "inconnue"
    assert champs["collected"] == "2026-11-02"
    assert fiche.valider(champs)[0] == []  # « date inconnue » est conforme au contrat de fiche


def test_la_meta_date_la_fiche(tmp_path, monkeypatch):
    _, champs = _ingerer(
        tmp_path,
        monkeypatch,
        {"titre": "EU allocates 20bn", "date": "2026-11-02", "date_publication": "2025-02-14"},
        "",
        "2026-11-02",
    )
    assert champs["date"] == "2025-02-14" and champs["origine_date"] == "méta de publication"


def test_le_lecteur_web_date_la_fiche_quand_la_page_n_a_pas_de_meta(tmp_path, monkeypatch):
    _, champs = _ingerer(
        tmp_path,
        monkeypatch,
        {"titre": "Data Act", "date": "2025-09-12", "date_publication": ""},
        "2026-09-01",
        "2025-09-12",
    )
    assert champs["date"] == "2026-09-01" and champs["origine_date"] == "lecteur web"


def test_meta_de_page_porte_la_date_de_publication():
    assert cr.meta_de_page(GLEISS)["date_publication"] == "2026-02-23"
    assert cr.meta_de_page(HEUKING)["date_publication"] == ""


def test_la_lettre_ne_reprend_pas_la_date_du_nom_de_fichier_d_une_fiche_sans_date(tmp_path, monkeypatch):
    from newskit import lettre as lt

    monkeypatch.setattr(lt, "RACINE", tmp_path)
    p = tmp_path / "sources" / "eclaireur" / "g" / "2026-11-02_dcd.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        '---\ntitle: "EU allocates 20bn"\ndate: ""\norigine_date: "inconnue"\nurl: "https://x.org/a"\n---\n\nt\n',
        encoding="utf-8",
    )
    f = lt.charger_fiches(["sources/eclaireur/g/2026-11-02_dcd.md"])["sources/eclaireur/g/2026-11-02_dcd.md"]
    assert f["date"] == "" and not lt.recente(f, "2026-11-03")


def test_une_date_corrigee_garde_l_ancienne_valeur():
    champs = {
        "title": "t",
        "subtitle": "",
        "author": "",
        "date": "2026-02-23",
        "url": "https://x.org/a",
        "plateforme": "Recherche web",
        "publication": "x.org",
        "collected": "2026-11-02",
        "date_avant_correction": "2024-08-01",
        "origine_date": "méta de publication",
    }
    assert not [e for e in fiche.valider(champs)[0] if "date_avant_correction" in e]
    assert not [a for a in fiche.valider(champs)[1] if "date_avant_correction" in a]


DCD_REEL = (
    '<html><body><article class="card"><time datetime="2026-11-02" itemprop="datePublished">Nov 2</time></article>'
    '<main><time class="article-intro__date" datetime="2025-02-14" itemprop="datePublished">Feb 14, 2025</time>'
    '<p>AI gigafactories.</p></main><article><time datetime="2026-10-30" itemprop="datePublished"></time></article>'
    "</body></html>"
)


def test_la_date_de_l_article_et_pas_celle_d_une_carte_voisine():
    """La page réelle de DCD (relue le 03.11.2026) porte une dizaine de <time datePublished> : les cartes « à lire
    aussi » (2026-11-02, 2026-10-30…) et celle de l'article, de classe article-intro__date (2025-02-14)."""
    assert cr.date_de_publication(DCD_REEL) == "2025-02-14"
    plusieurs = DCD_REEL.replace('class="article-intro__date" ', "")
    assert cr.date_de_publication(plusieurs) == ""  # plusieurs dates sans celle de l'article : aucune

"""Un agrégateur ne vaut que par les articles où il mène (mainteneur, 13.11.2026 : « leur page n'a d'intérêt que parce
qu'elle mène à une source, et c'est cette source qui devrait être intégrée », « garde la classification en
métadonnée »). Une source « Plan de site » déclarée `agregateur: true` (l'AI Incidents Monitor de l'OCDE) ne fait plus
une fiche de sa page : la fiche porte l'article d'origine, la page de l'agrégateur reste en métadonnée (`via`), avec
sa classification et les autres articles qu'elle cite. Le domaine de l'article compte comme une trouvaille de
l'éclaireur (mainteneur : « les adresses en source deviennent parfois de nouvelles sources pour la routine »)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from newskit import collecte_rss as rss
from newskit import config_sources as cfg
from newskit import depots
from newskit import eclaireur
from newskit import fiche

AGREGAT = "https://oecd.ai/en/incidents/2026-11-12-a8be"
MD = """##  Israel Unveils AI-Enabled Super Ice Cruise Missile

Rafael has unveiled the Super Ice cruise missile.[AI generated]

Harm types

Physical (death)Human or fundamental rights

### Articles about this incident or hazard

[##### Izrael pokazał najnowszą broń](https://businessinsider.com.pl/technologie/super-ice/sg82s3t)

2026-11-12

Business Insider

Why's our monitor labelling this an incident or hazard?

[##### Rafael zaprezentował nowy pocisk](https://www.wnp.pl/rynki/rafael-pocisk,1118987.html)

2026-11-11

wnp.pl

Why's our monitor labelling this an incident or hazard?

[##### Sans date](https://naszdziennik.pl/swiat/348549,pocisk.html)

Nasz Dziennik
"""
HTML = """<span class="has-text-weight-bold is-size-6">AI principles</span><div class="tags pt-3"><span class="tag is-light">
Safety</span><span class="tag is-light">Respect of human rights</span></div><hr><span class="has-text-weight-bold
is-size-6">Harm types</span><div class="tags pt-3"><span class="tag">Physical (death)</span></div><span
class="has-text-weight-bold is-size-6">AI system task:</span><div class="tags pt-3"><span class="tag has-text-danger">
Recognition/object detection</span><!----></div>"""
MOTIF = r'has-text-weight-bold[^"]*">\s*([^<]+?)\s*</span>\s*<div[^>]*class="tags[^"]*">(.*?)</div>'
TEXTE = " ".join(["mot"] * 400)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sans_pause(monkeypatch):
    monkeypatch.setattr(rss, "PAUSE_PAGE", 0)


def test_les_articles_cites_par_l_agregateur():
    assert rss.sources_de_l_agregat(MD) == [
        {
            "titre": "Izrael pokazał najnowszą broń",
            "url": "https://businessinsider.com.pl/technologie/super-ice/sg82s3t",
            "date": "2026-11-12",
            "publication": "Business Insider",
        },
        {
            "titre": "Rafael zaprezentował nowy pocisk",
            "url": "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html",
            "date": "2026-11-11",
            "publication": "wnp.pl",
        },
        {
            "titre": "Sans date",
            "url": "https://naszdziennik.pl/swiat/348549,pocisk.html",
            "date": "",
            "publication": "Nasz Dziennik",
        },
    ]
    assert rss.sources_de_l_agregat("[un lien](https://x.org) sans titre de section") == []


def test_la_classification_de_la_page():
    assert rss.classification_de(HTML, MOTIF) == (
        "AI principles: Safety, Respect of human rights ; Harm types: Physical (death) ; "
        "AI system task: Recognition/object detection"
    )
    assert rss.classification_de(HTML, "") == "" and rss.classification_de("", MOTIF) == ""


def _lecteur(pages):
    lues = []

    def lire(url):
        lues.append(url)
        return pages.get(url, (0, "", False, {}))

    return lire, lues


def test_l_article_d_origine_le_premier_publie_qui_se_lit():
    wnp = "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html"
    bi = "https://businessinsider.com.pl/technologie/super-ice/sg82s3t"
    lire, lues = _lecteur(
        {
            wnp: (400, TEXTE, False, {"titre": "Rafael : nowy pocisk", "auteur": "Jan Kowalski"}),
            bi: (400, TEXTE, False, {"titre": "BI"}),
        }
    )
    o = rss.suivre_l_agregat(AGREGAT, MD, HTML, MOTIF, lire=lire, exclu=lambda u: False)
    assert lues == [wnp]  # le plus ancien d'abord ; un seul suffit
    assert (o["lien"], o["titre"], o["auteur"], o["date"], o["publication"]) == (
        wnp,
        "Rafael : nowy pocisk",
        "Jan Kowalski",
        "2026-11-11",
        "wnp.pl",
    )
    assert o["corps"] == TEXTE and o["mots"] == 400 and o["complet"] is True
    assert o["champs"] == {
        "via": AGREGAT,
        "autres_sources": f"{bi} ; https://naszdziennik.pl/swiat/348549,pocisk.html",
        "classification": rss.classification_de(HTML, MOTIF),
    }


def test_une_source_fermee_cede_a_la_suivante():
    wnp = "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html"
    bi = "https://businessinsider.com.pl/technologie/super-ice/sg82s3t"
    nasz = "https://naszdziennik.pl/swiat/348549,pocisk.html"
    # wnp.pl derrière un mur, Business Insider exclu par robots.txt : Nasz Dziennik, sans date, est pris
    lire, lues = _lecteur(
        {
            wnp: (90, "aperçu", True, {}),
            bi: (400, TEXTE, False, {}),
            nasz: (200, TEXTE[:1000], False, {"date": "2026-11-12"}),
        }
    )
    o = rss.suivre_l_agregat(AGREGAT, MD, HTML, MOTIF, lire=lire, exclu=lambda u: "businessinsider" in u)
    assert lues == [wnp, nasz] and o["lien"] == nasz and o["complet"] is False and o["date"] == "2026-11-12"
    assert o["titre"] == "Sans date"  # sans titre de page, celui de l'agrégateur
    lire, _ = _lecteur({})
    assert rss.suivre_l_agregat(AGREGAT, MD, HTML, MOTIF, lire=lire, exclu=lambda u: False) is None


def test_un_apercu_derriere_un_mur_n_est_pas_l_article():
    wnp = "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html"
    bi = "https://businessinsider.com.pl/technologie/super-ice/sg82s3t"
    lire, lues = _lecteur({wnp: (200, TEXTE[:800], True, {}), bi: (400, TEXTE, False, {})})
    assert rss.suivre_l_agregat(AGREGAT, MD, HTML, MOTIF, lire=lire, exclu=lambda u: False)["lien"] == bi


def test_quatre_articles_essayes_au_plus():
    md = "\n\n".join(f"[##### A{i}](https://s{i}.org/a)\n\n2026-11-0{i}\n\nS{i}" for i in range(1, 7))
    lire, lues = _lecteur({})
    assert rss.suivre_l_agregat(AGREGAT, md, "", "", lire=lire, exclu=lambda u: False) is None
    assert lues == [f"https://s{i}.org/a" for i in range(1, rss.ESSAIS_AGREGAT + 1)]


def test_le_registre_admet_l_agregateur():
    oblig, fac = cfg.PROPRES["Plan de site"]
    assert {"agregateur", "classification_motif"} <= fac
    for champ in ("via", "autres_sources", "classification"):
        assert fiche.OPTIONNELS[champ] is str


def test_le_domaine_d_origine_compte_comme_une_trouvaille(monkeypatch, tmp_path):
    monkeypatch.setattr(eclaireur, "MEMOIRE", tmp_path / "_sources.json")
    etudies = []
    monkeypatch.setattr(depots, "etudier_source", lambda d, m, j: etudies.append(d))
    monkeypatch.setattr(depots, "deja_declaree", lambda d: None)
    eclaireur.noter_trouvaille("https://www.wnp.pl/a", "ai-governance", "2026-11-13")
    m = eclaireur.lire_memoire()
    assert m["domaines"]["wnp.pl"]["depots"] == 1 and m["domaines"]["wnp.pl"]["graphes"] == ["ai-governance"]
    assert etudies == []
    eclaireur.noter_trouvaille("https://wnp.pl/b", "ai-governance", "2026-11-13")
    assert eclaireur.lire_memoire()["domaines"]["wnp.pl"]["depots"] == 2 and etudies == ["wnp.pl"]


def test_la_collecte_d_un_agregateur_ecrit_l_article_d_origine(monkeypatch, tmp_path):
    wnp = "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html"
    src = SimpleNamespace(
        cle="oecd_aim",
        nom="OCDE.AI — AI Incidents Monitor",
        plateforme="Plan de site",
        chemin=tmp_path / "aim",
        manifeste=tmp_path / "aim" / "_manifest.json",
        session=None,
        mots_cles=[],
        exclure="",
        doublons_de=[],
        plan="https://oecd.ai/sitemap.xml",
        flux="",
        graphe="ai-governance",
        propres={"agregateur": True, "classification_motif": MOTIF},
    )
    monkeypatch.setattr(
        rss,
        "entrees_plan_quotidien",
        lambda s, l: [{"lien": AGREGAT, "titre": "", "corps": "", "complet": False, "date": "", "auteur": ""}],
    )
    monkeypatch.setattr(rss, "PAUSE_PAGE", 0)
    pages = {
        AGREGAT: (300, MD, False, {"titre": "Super Ice - OECD.AI", "html": HTML}),
        wnp: (400, TEXTE, False, {"titre": "Rafael : nowy pocisk", "auteur": "Jan Kowalski"}),
    }

    def corps_de_page(url, *a, garder_html=False, **k):  # le HTML seulement quand la collecte le demande
        n, md, mur, meta = pages.get(url, (0, "", False, {}))
        return n, md, mur, {c: v for c, v in meta.items() if c != "html" or garder_html}

    monkeypatch.setattr(rss, "corps_de_page", corps_de_page)
    monkeypatch.setattr(rss, "_robots_exclut_ia", lambda u: False)
    trouvees = []
    monkeypatch.setattr(eclaireur, "noter_trouvaille", lambda u, g, j: trouvees.append((u, g)))
    c = rss.traiter_source(src, 10, [])
    assert c["neuves"] == 1
    m = json.loads(src.manifeste.read_text(encoding="utf-8"))
    assert list(m) == [AGREGAT] and m[AGREGAT]["origine"] == wnp  # l'agrégat reste la clé : il n'est pas relu
    champs, corps = fiche.lire_fichier(src.chemin / m[AGREGAT]["file"])
    assert (champs["url"], champs["author"], champs["publication"], champs["date"], champs["via"]) == (
        wnp,
        "Jan Kowalski",
        "wnp.pl",
        "2026-11-11",
        AGREGAT,
    )
    assert champs["classification"].startswith("AI principles: Safety") and "AI generated" not in corps
    assert trouvees == [(wnp, "ai-governance")]


def test_sans_source_lisible_la_page_de_l_agregateur_reste(monkeypatch, tmp_path):
    src = SimpleNamespace(
        cle="oecd_aim",
        nom="OCDE.AI",
        plateforme="Plan de site",
        chemin=tmp_path / "aim",
        manifeste=tmp_path / "aim" / "_manifest.json",
        session=None,
        mots_cles=[],
        exclure="",
        doublons_de=[],
        plan="https://oecd.ai/sitemap.xml",
        flux="",
        graphe="ai-governance",
        propres={"agregateur": True, "classification_motif": MOTIF},
    )
    monkeypatch.setattr(
        rss,
        "entrees_plan_quotidien",
        lambda s, l: [{"lien": AGREGAT, "titre": "", "corps": "", "complet": False, "date": "", "auteur": ""}],
    )
    monkeypatch.setattr(rss, "PAUSE_PAGE", 0)
    monkeypatch.setattr(
        rss,
        "corps_de_page",
        lambda url, *a, **k: (300, MD, False, {"titre": "Super Ice"}) if url == AGREGAT else (0, "", False, {}),
    )
    monkeypatch.setattr(rss, "_robots_exclut_ia", lambda u: False)
    assert rss.traiter_source(src, 10, [])["neuves"] == 1
    m = json.loads(src.manifeste.read_text(encoding="utf-8"))
    champs, _ = fiche.lire_fichier(src.chemin / m[AGREGAT]["file"])
    assert champs["url"] == AGREGAT and champs["note"] == "agrégateur : aucun article d'origine lisible"


def test_la_page_garde_son_html_seulement_sur_demande(monkeypatch):
    page = b"<html><body><article>" + b"<p>texte</p>" * 50 + b"</article></body></html>"
    monkeypatch.setattr(rss.ss, "en_repos", lambda *a: None)
    monkeypatch.setattr(rss.ss, "imite", lambda h: False)
    monkeypatch.setattr(rss.ss, "succes", lambda *a: None)
    monkeypatch.setattr(rss, "_ouvrir", lambda url, req: page)
    assert "html" not in rss.corps_de_page("https://oecd.ai/x")[3]
    assert rss.corps_de_page("https://oecd.ai/x", garder_html=True)[3]["html"] == page.decode()


def test_une_memoire_des_domaines_en_panne_n_empeche_pas_la_fiche(monkeypatch, tmp_path, capsys):
    wnp = "https://www.wnp.pl/rynki/rafael-pocisk,1118987.html"
    src = SimpleNamespace(
        cle="oecd_aim",
        nom="OCDE.AI",
        plateforme="Plan de site",
        chemin=tmp_path / "aim",
        manifeste=tmp_path / "aim" / "_manifest.json",
        session=None,
        mots_cles=[],
        exclure="",
        doublons_de=[],
        plan="https://oecd.ai/sitemap.xml",
        flux="",
        graphe="ai-governance",
        propres={"agregateur": True, "classification_motif": MOTIF},
    )
    monkeypatch.setattr(
        rss,
        "entrees_plan_quotidien",
        lambda s, l: [{"lien": AGREGAT, "titre": "", "corps": "", "complet": False, "date": "", "auteur": ""}],
    )
    pages = {AGREGAT: (300, MD, False, {"titre": "Super Ice"}), wnp: (400, TEXTE, False, {})}
    monkeypatch.setattr(rss, "corps_de_page", lambda url, *a, **k: pages.get(url, (0, "", False, {})))
    monkeypatch.setattr(rss, "_robots_exclut_ia", lambda u: False)

    def panne(*a):
        raise OSError("disque plein")

    monkeypatch.setattr(eclaireur, "noter_trouvaille", panne)
    assert rss.traiter_source(src, 10, [])["neuves"] == 1
    assert "mémoire des domaines : OSError" in capsys.readouterr().err

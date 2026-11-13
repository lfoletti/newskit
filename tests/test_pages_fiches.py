"""Une page par fiche, à une adresse lisible (mainteneur, 13.11.2026 : « l'URL est à formater en langage naturel pour le
SEO avec un fonctionnement scalable. Le titre de l'URL doit être celui de la fiche, pas du site » ; « la diffusion du
lien met en avant le contenu de la fiche » ; carte d'aperçu A retenue ; « ok pour 1 et 2 » : seules les fiches
développées s'indexent).

Avant, un lien de fiche était https://<site>/#n=<identifiant> : le fragment n'atteint jamais le serveur, les robots
d'aperçu et Google ne voyaient que l'accueil, et le lien mourait quand le billet sortait du fil.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml
from PIL import Image, ImageColor

from newskit import pages_fiches as pf

SITE = "https://exemple.news"
FORTUNE = (
    "https://fortune.com/2026/11/12/a-dark-day-jensen-huangs-sex-drive-threatens-to-pop-the-ai-bubble-and-the-"
    "industry-isnt-sure-whats-next/"
)
OSIRIS = "https://www.osiris.sn/intelligence-artificielle-dans-l-enseignement-superieur-un-universitaire-alerte.html"


def item(**k):
    """Un billet de la copie publique (publier_fil), fiche développée dans les deux langues."""
    return {
        "titre": "Colette Kress et Harvey Jones dirigent Nvidia après la démission de Jensen Huang",
        "descriptif": "Colette Kress et Harvey Jones dirigent Nvidia. D'après Fortune, l'industrie hésite.",
        "rubrique": "Secteurs",
        "url": FORTUNE,
        "lien_fr": FORTUNE,
        "lien_en": FORTUNE,
        "editeur": "Fortune",
        "date": "2026-11-12",
        "titre_en": "Colette Kress and Harvey Jones run Nvidia after Jensen Huang resigns",
        "descriptif_en": "Colette Kress and Harvey Jones run Nvidia. Fortune says the industry hesitates.",
        "resume": "Harvey Jones préside le conseil.",
        "resume_en": "Harvey Jones chairs the board.",
        "elements": [{"champ": "Fait", "texte": "Huang part."}, {"champ": "Contexte", "texte": "Enquête interne."}],
        "elements_en": [{"champ": "Fact", "texte": "Huang leaves."}],
        "categorie": "Secteurs : semi-conducteurs",
        "categorie_en": "Sectors: semiconductors",
        "fiche": "",
        "meta": [],
        "meta_en": [],
        **k,
    }


def bref(**k):
    return item(resume="", resume_en="", elements=[], elements_en=[], **k)


# L'identifiant


def test_l_identifiant_est_celui_du_site():
    assert pf.ident(FORTUNE) == "110kh65"
    assert pf.ident(OSIRIS) == "1fojr0d"
    assert pf.ident("https://exemple.org/é?q=💡") == "19w9ab2"  # points de code, comme le script du site


# L'adresse lisible


def test_l_adresse_vient_du_titre_de_la_fiche_dans_sa_langue():
    assert pf.adresse(item(), "fr") == (
        "/fr/colette-kress-et-harvey-jones-dirigent-nvidia-apres-la-demission-de-jensen-huang-110kh65"
    )
    assert (
        pf.adresse(item(), "en") == "/en/colette-kress-and-harvey-jones-run-nvidia-after-jensen-huang-resigns-110kh65"
    )


def test_l_adresse_est_en_minuscules_sans_accents_ni_ponctuation():
    assert pf.slug("Au Sénégal, 52 % des étudiants voient l’IA « générative » comme un obstacle") == (
        "au-senegal-52-des-etudiants-voient-l-ia-generative-comme-un-obstacle"
    )
    assert pf.slug("Œuvres d’art : Ærø & Straße") == "oeuvres-d-art-aero-strasse"


def test_une_adresse_longue_se_coupe_entre_deux_mots():
    s = pf.slug(" ".join(["gouvernance"] * 20))
    assert len(s) <= pf.SLUG_MAX and not s.endswith("-") and s.split("-") == ["gouvernance"] * len(s.split("-"))


def test_un_titre_sans_lettre_garde_une_adresse():
    assert pf.adresse(item(titre="« ! »"), "fr") == "/fr/fiche-110kh65"


# L'en-tête de la page


def _meta(tete, cle):
    m = re.search(rf'<meta (?:property|name)="{re.escape(cle)}" content="([^"]*)">', tete)
    return m and m.group(1)


def test_l_en_tete_met_en_avant_le_contenu_de_la_fiche():
    t = pf.tete(item(), "fr", SITE)
    adr = SITE + pf.adresse(item(), "fr")
    assert "<title>Colette Kress et Harvey Jones dirigent Nvidia après la démission de Jensen Huang" in t
    assert _meta(t, "og:title") == item()["titre"]
    assert (
        _meta(t, "description")
        == _meta(t, "og:description")
        == "Colette Kress et Harvey Jones dirigent Nvidia. D&#x27;après Fortune, l&#x27;industrie hésite."
    )
    assert f'<link rel="canonical" href="{adr}">' in t and _meta(t, "og:url") == adr
    assert _meta(t, "og:image") == f"{SITE}/fiches/cartes/110kh65-fr.png"
    assert _meta(t, "og:image:width") == "1200" and _meta(t, "og:image:height") == "630"
    assert _meta(t, "twitter:card") == "summary_large_image" and _meta(t, "og:type") == "article"
    assert _meta(t, "og:locale") == "fr_FR"


def test_l_en_tete_relie_les_deux_langues():
    t = pf.tete(item(), "en", SITE)
    assert f'<link rel="alternate" hreflang="fr" href="{SITE}{pf.adresse(item(), "fr")}">' in t
    assert f'<link rel="alternate" hreflang="en" href="{SITE}{pf.adresse(item(), "en")}">' in t
    assert f'<link rel="alternate" hreflang="x-default" href="{SITE}{pf.adresse(item(), "en")}">' in t
    assert _meta(t, "og:title") == item()["titre_en"] and _meta(t, "og:locale") == "en_US"


def test_la_description_se_coupe_entre_deux_mots():
    d = _meta(pf.tete(item(descriptif="mot " * 80), "fr", SITE), "description")
    assert len(d) <= pf.DESCRIPTION_MAX and d.endswith("…") and "mot mot…" in d


def test_l_en_tete_dit_l_article_de_presse_en_donnees_structurees():
    t = pf.tete(item(), "fr", SITE)
    d = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S).group(1))
    assert d["@type"] == "NewsArticle" and d["headline"] == item()["titre"] and d["inLanguage"] == "fr"
    assert d["datePublished"] == "2026-11-12" and d["isBasedOn"] == FORTUNE
    assert d["image"] == [f"{SITE}/fiches/cartes/110kh65-fr.png"] and d["mainEntityOfPage"] == SITE + pf.adresse(
        item(), "fr"
    )


def test_les_donnees_structurees_ne_ferment_pas_le_script():
    t = pf.tete(item(titre="Un </script><b>piège"), "fr", SITE)
    assert "</script><b>" not in t


def test_seule_une_fiche_developpee_s_indexe():
    assert 'name="robots"' not in pf.tete(item(), "fr", SITE)
    assert '<meta name="robots" content="noindex, follow">' in pf.tete(bref(), "fr", SITE)
    assert pf.developpee(item(), "fr") and not pf.developpee(bref(), "fr")
    assert pf.developpee(item(elements=[], resume="R"), "fr")


# L'article, lisible sans script (lynx, robots)


def test_l_article_hierarchise_la_fiche():
    a = pf.article(item(), "fr", SITE)
    titres = re.findall(r"<(h[1-6])>(.*?)</\1>", a)
    assert titres == [("h1", item()["titre"]), ("h2", "Fait"), ("h2", "Contexte")]
    assert a.index("<h1>") < a.index("Colette Kress et Harvey Jones dirigent Nvidia. D") < a.index("<h2>")
    assert '<time datetime="2026-11-12">12 novembre 2026</time>' in a
    assert f'<a href="{FORTUNE}">Fortune</a>' in a and "Harvey Jones préside le conseil." in a


def test_l_article_anglais_est_en_anglais():
    a = pf.article(item(), "en", SITE)
    assert "<h1>Colette Kress and Harvey Jones run Nvidia" in a and "<h2>Fact</h2>" in a
    assert '<time datetime="2026-11-12">12 November 2026</time>' in a and "Harvey Jones chairs the board." in a


def test_une_date_illisible_reste_telle_quelle():
    assert pf.date_longue("bientôt", "fr") == "bientôt"


def test_l_article_echappe_le_texte():
    a = pf.article(item(titre="A <b> & B"), "fr", SITE)
    assert "<h1>A &lt;b&gt; &amp; B</h1>" in a


def test_l_article_d_un_billet_bref_n_a_que_son_chapeau():
    a = pf.article(bref(), "fr", SITE)
    assert re.findall(r"<(h[1-6])>", a) == ["h1"] and item()["descriptif"].split(".")[0] in a


# La carte d'aperçu


def test_la_carte_porte_le_titre_la_rubrique_la_date_et_la_source():
    assert pf.champs_carte(item(), "fr") == {
        "titre": item()["titre"],
        "rubrique": "Secteurs",
        "date": "12 novembre 2026",
        "source": "Fortune",
    }


def test_la_carte_anglaise_nomme_la_rubrique_en_anglais():
    c = pf.champs_carte(item(), "en", {"Secteurs": "Sectors"})
    assert c["rubrique"] == "Sectors" and c["date"] == "12 November 2026" and c["titre"] == item()["titre_en"]


# Les billets publiés


def test_les_pages_suivent_la_copie_publique_sans_sa_limite(monkeypatch):
    monkeypatch.setattr(pf.publier_fil, "MAX", 1)
    monkeypatch.setattr(pf.publier_fil, "_champs", lambda F: F["billets"])
    F = {"billets": [item(), item(url=OSIRIS), item(url="u3", titre_en="", descriptif_en="")]}
    assert [p["url"] for p in pf.items(F)] == [FORTUNE, OSIRIS]  # le billet sans version anglaise ne paraît pas


# Le dépôt


class Banc:
    def __init__(self):
        self.rendus, self.envois = [], []

    def rendre(self, champs, langue):
        self.rendus.append((champs, langue))
        return b"PNG" + json.dumps([champs, langue], ensure_ascii=False, sort_keys=True).encode()

    def envoyer(self, archive):
        with tarfile.open(fileobj=io.BytesIO(archive)) as t:
            self.envois.append({m.name: t.extractfile(m).read() for m in t.getmembers()})
        return ""


@pytest.fixture
def banc():
    return Banc()


def deposer(banc, items, cache, **k):
    return pf.deposer(items, SITE, cache, banc.rendre, banc.envoyer, **k)


def test_le_depot_porte_l_en_tete_l_article_et_la_carte_des_deux_langues(banc, tmp_path):
    ligne = deposer(banc, [item()], tmp_path / "pages.json")
    (envoi,) = banc.envois
    assert {
        "fr/110kh65.head",
        "fr/110kh65.html",
        "en/110kh65.head",
        "en/110kh65.html",
        "cartes/110kh65-fr.png",
        "cartes/110kh65-en.png",
        "plan_fiches.xml",
    } == set(envoi)
    assert envoi["fr/110kh65.head"].decode() == pf.tete(item(), "fr", SITE)
    assert envoi["cartes/110kh65-fr.png"] == banc.rendre(pf.champs_carte(item(), "fr"), "fr")
    assert envoi["cartes/110kh65-en.png"] == banc.rendre(pf.champs_carte(item(), "en"), "en")
    assert "1 fiche" in ligne


def test_une_fiche_inchangee_ne_repart_pas(banc, tmp_path):
    deposer(banc, [item()], tmp_path / "pages.json")
    assert deposer(banc, [item()], tmp_path / "pages.json") == "inchangé"
    assert len(banc.envois) == 1 and len(banc.rendus) == 2


def test_une_marque_changee_refait_les_cartes(banc, tmp_path):
    deposer(banc, [item()], tmp_path / "pages.json", empreinte_marque="a")
    assert deposer(banc, [item()], tmp_path / "pages.json", empreinte_marque="a") == "inchangé"
    deposer(banc, [item()], tmp_path / "pages.json", empreinte_marque="b")
    assert len(banc.envois) == 2 and "cartes/110kh65-fr.png" in banc.envois[1]


def test_une_fiche_changee_repart_seule(banc, tmp_path):
    deposer(banc, [item(), item(url=OSIRIS)], tmp_path / "pages.json")
    deposer(banc, [item(titre="Nouveau titre"), item(url=OSIRIS)], tmp_path / "pages.json")
    assert {n for n in banc.envois[1] if n != "plan_fiches.xml"} == {
        "fr/110kh65.head",
        "fr/110kh65.html",
        "en/110kh65.head",
        "en/110kh65.html",
        "cartes/110kh65-fr.png",
        "cartes/110kh65-en.png",
    }


def test_un_depot_manque_se_refait_au_passage_suivant(banc, tmp_path):
    banc.envoyer = lambda archive: "échec ssh 255"
    assert "échec" in deposer(banc, [item()], tmp_path / "pages.json")
    b2 = Banc()
    deposer(b2, [item()], tmp_path / "pages.json")
    assert "fr/110kh65.head" in b2.envois[0]


def test_une_carte_non_rendue_ne_marque_pas_la_fiche_deposee(banc, tmp_path):
    banc.rendre = lambda champs, langue: None
    ligne = deposer(banc, [item()], tmp_path / "pages.json")
    assert banc.envois == [] and "carte" in ligne
    b2 = Banc()
    deposer(b2, [item()], tmp_path / "pages.json")
    assert "cartes/110kh65-fr.png" in b2.envois[0]


def _plan(xml):
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    r = ElementTree.fromstring(xml)
    return [(u.find("s:loc", ns).text, u.find("s:lastmod", ns).text) for u in r.findall("s:url", ns)], r, ns


def test_le_plan_liste_les_fiches_developpees_dans_les_deux_langues(banc, tmp_path):
    deposer(banc, [item(), bref(url=OSIRIS)], tmp_path / "pages.json")
    urls, r, ns = _plan(banc.envois[0]["plan_fiches.xml"])
    assert urls == [(SITE + pf.adresse(item(), "fr"), "2026-11-12"), (SITE + pf.adresse(item(), "en"), "2026-11-12")]
    alt = r.find("s:url", ns).findall("x:link", ns)
    assert {(a.get("hreflang"), a.get("href")) for a in alt} == {
        ("fr", SITE + pf.adresse(item(), "fr")),
        ("en", SITE + pf.adresse(item(), "en")),
    }


def test_le_plan_garde_les_fiches_sorties_du_registre(banc, tmp_path):
    deposer(banc, [item()], tmp_path / "pages.json")
    deposer(banc, [item(url=OSIRIS, titre="Autre")], tmp_path / "pages.json")
    urls, _, _ = _plan(banc.envois[1]["plan_fiches.xml"])
    assert SITE + pf.adresse(item(), "fr") in [u for u, _ in urls] and len(urls) == 4


def test_le_plan_suit_le_titre_reecrit(banc, tmp_path):
    deposer(banc, [item()], tmp_path / "pages.json")
    deposer(banc, [item(titre="Harvey Jones préside Nvidia")], tmp_path / "pages.json")
    urls = [u for u, _ in _plan(banc.envois[1]["plan_fiches.xml"])[0]]
    assert SITE + "/fr/harvey-jones-preside-nvidia-110kh65" in urls and SITE + pf.adresse(item(), "fr") not in urls


# La configuration du média


def test_les_cartes_se_declarent_dans_la_configuration_du_media(tmp_path):
    from newskit import media

    base = "version: 1\nmedia: {nom: exemple}\n"
    for bon in ("pages_fiches: {marque: a/carte.yaml}\n",):
        (tmp_path / "config.yaml").write_text(base + bon, encoding="utf-8")
        media.charger(tmp_path / "config.yaml")
    for mauvais in (
        "pages_fiches: {}\n",
        "pages_fiches: {marque: /abs/carte.yaml}\n",
        "pages_fiches: {marque: ../carte.yaml}\n",
        "pages_fiches: {marque: a/carte.yaml, autre: 1}\n",
        "pages_fiches: oui\n",
    ):
        (tmp_path / "config.yaml").write_text(base + mauvais, encoding="utf-8")
        with pytest.raises(media.ConfigMediaInvalide, match="pages_fiches"):
            media.charger(tmp_path / "config.yaml")


# Le passage de la collecte


def test_chaque_passage_depose_les_pages_du_graphe_du_site_sans_dependre_d_elles(monkeypatch, tmp_path, capsys):
    import subprocess

    from newskit import alertes_fil as af
    from newskit import collecte_continue as cc

    monkeypatch.setattr(cc.rh, "VERROU", tmp_path / "routine.verrou")
    monkeypatch.setattr(cc.rh, "VERROU_COLLECTE", tmp_path / "collecte.verrou")
    monkeypatch.setattr(cc.rh, "graphe_en_routine", lambda *a: None)
    monkeypatch.setattr(cc.rh.cfg, "graphe_du_site", lambda: "g")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(cc.publier_fil, "publier", lambda base: "déposé")
    monkeypatch.setattr(cc, "inscriptions", lambda: "")
    monkeypatch.setattr(af, "surveiller", lambda *a: None)
    monkeypatch.setattr("sys.argv", ["collecte_continue.py"])
    vus = []
    monkeypatch.setattr(cc.pages_fiches, "publier", lambda g: vus.append(g) or "3 fiche(s) déposée(s)")
    assert cc.main() == 0 and vus == ["g"] and "pages des fiches : 3 fiche(s) déposée(s)" in capsys.readouterr().out
    monkeypatch.setattr(cc.pages_fiches, "publier", lambda g: 1 / 0)  # une panne des pages n'arrête pas le fil
    assert cc.main() == 0 and "pages des fiches : ZeroDivisionError" in capsys.readouterr().out


# Les entrées et sorties


def test_l_envoi_passe_par_ssh(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(pf.publier_fil, "acces", lambda env: None)
    assert "sans accès" in pf.envoyer_ssh(b"a")
    monkeypatch.setattr(pf.publier_fil, "acces", lambda env: {"VPS_CLE": "k", "VPS_UTILISATEUR": "u", "VPS_HOTE": "h"})
    vus = []
    monkeypatch.setattr(
        pf.subprocess,
        "run",
        lambda cmd, **k: vus.append((cmd, k["input"])) or subprocess.CompletedProcess(cmd, 0, b"", b""),
    )
    assert pf.envoyer_ssh(b"a") == "" and vus[0][1] == b"a" and f"tar -C {pf.DOSSIER_VPS} -xf -" in vus[0][0][-1]
    monkeypatch.setattr(pf.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 255, b"", b"refus"))
    assert pf.envoyer_ssh(b"a") == "ssh 255 : refus"


def test_les_rubriques_anglaises_viennent_de_l_arbre_des_fiches(monkeypatch, tmp_path):
    (tmp_path / "arbre.yaml").write_text("racines:\n- {cle: s, fr: Secteurs, en: Sectors}\n", encoding="utf-8")
    monkeypatch.setattr(pf, "RACINE", tmp_path)
    from newskit import config_sources as cfg

    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"arbre_fiches": "arbre.yaml"})
    assert pf.rubriques_anglaises("g") == {"Secteurs": "Sectors"}
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert pf.rubriques_anglaises("g") == {}


def test_la_publication_lit_le_media_et_le_fil(monkeypatch, tmp_path):
    from newskit import config_sources as cfg
    from newskit import fil, media

    monkeypatch.setattr(pf, "RACINE", tmp_path)
    (tmp_path / "fil.json").write_text(json.dumps({"billets": []}), encoding="utf-8")
    monkeypatch.setattr(fil, "chemin_fil", lambda g: tmp_path / "fil.json")
    monkeypatch.setattr(cfg, "edition", lambda: {"lettre_site": SITE + "/"})
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    monkeypatch.setattr(media, "courant", lambda: {})
    assert "sans pages" in pf.publier("g")
    ecrire_marque(tmp_path)
    monkeypatch.setattr(media, "courant", lambda: {"pages_fiches": {"marque": "carte.yaml"}})
    vus = []
    monkeypatch.setattr(pf, "items", lambda F: [item()])
    monkeypatch.setattr(pf, "deposer", lambda *a: vus.append(a) or "ok")
    assert pf.publier("g") == "ok"
    billets, site, cache, rendre, envoyer, rubriques, empreinte = vus[0]
    assert site == SITE and cache == tmp_path / "cache" / "pages_fiches_g.json" and billets == [item()]
    assert rendre(pf.champs_carte(item(), "fr"), "fr").startswith(PNG_SIG) and envoyer is pf.envoyer_ssh
    (tmp_path / "carte.yaml").write_text((tmp_path / "carte.yaml").read_text() + "# changée\n", encoding="utf-8")
    pf.publier("g")
    assert vus[1][6] != empreinte  # une marque changée refait toutes les cartes


def test_la_commande_publie_le_graphe_demande(monkeypatch, capsys):
    monkeypatch.setattr(pf, "publier", lambda g: f"publié {g}")
    assert pf.main(["--graphe", "g"]) == 0 and "publié g" in capsys.readouterr().out


# La carte dessinée (Pillow : Chrome sans écran échouait sous le daemon, 13.11.2026)

POLICE = Path(__file__).parent / "polices" / "JosefinSans-variable.ttf"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
COULEURS = {
    "fond": "#eef0f3",
    "titre": "#16233b",
    "marque": "#3c4a60",
    "accent": "#b4560f",
    "discret": "#56637a",
    "source": "#2c8a3c",
    "filet": "#aab2bf",
}
# Un grand point : son bord gauche (x = 10 dans la viewBox, échelle 2) tombe sur la marge ; son centre à (92, 114).
LOGO = '<svg viewBox="0 0 110 58"><circle cx="20" cy="29" r="10"/><circle cx="60.5" cy="10" r="1.5"/></svg>'


def ecrire_marque(dossier, **k):
    (dossier / "logo.svg").write_text(k.pop("svg", LOGO), encoding="utf-8")
    m = {
        "logo": "logo.svg",
        "polices": {r: [str(POLICE), 600] for r in pf.POLICES},
        "couleurs": COULEURS,
        "nom": ["exemple", ".news"],
        "textes": {lg: {"sous": "Sous", "pied": "Pied", "d_apres": "D'après"} for lg in pf.LANGUES},
        **k,
    }
    (dossier / "carte.yaml").write_text(yaml.safe_dump(m, allow_unicode=True), encoding="utf-8")
    return dossier / "carte.yaml"


@pytest.fixture
def marque(tmp_path):
    return pf.charger_marque(ecrire_marque(tmp_path))


def _image(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def _proche(px, couleur, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(px, ImageColor.getrgb(couleur)))


def _compte(im, boite, couleur):
    return sum(
        _proche(im.getpixel((x, y)), couleur, 30) for x in range(boite[0], boite[2]) for y in range(boite[1], boite[3])
    )


CHAMPS = {
    "titre": "Harvey Jones dirige Nvidia",
    "rubrique": "Secteurs",
    "date": "12 novembre 2026",
    "source": "Fortune",
}


def test_la_carte_est_une_image_de_1200_sur_630(marque):
    png = pf.dessiner(CHAMPS, "fr", marque)
    assert png.startswith(PNG_SIG) and _image(png).size == (pf.LARGEUR, pf.HAUTEUR)


def test_la_marque_s_aligne_sur_la_marge(marque):
    im = _image(pf.dessiner(CHAMPS, "fr", marque))
    assert _proche(im.getpixel((92, 114)), COULEURS["marque"])  # le centre du grand point
    assert _proche(im.getpixel((pf.MARGE + 2, 114)), COULEURS["marque"])
    assert _proche(im.getpixel((pf.MARGE - 3, 114)), COULEURS["fond"])


def test_le_nom_porte_son_suffixe_en_couleur_d_accent(marque):
    im = _image(pf.dessiner(CHAMPS, "fr", marque))
    nom = (150, 70, 600, 110)
    assert _compte(im, nom, COULEURS["accent"]) > 30 and _compte(im, nom, COULEURS["marque"]) > 30


def test_la_rubrique_et_la_date_sont_en_haut_a_droite(marque):
    im = _image(pf.dessiner(CHAMPS, "fr", marque))
    assert _compte(im, (800, 70, pf.LARGEUR - pf.MARGE, 110), COULEURS["discret"]) > 30
    vide = dict(CHAMPS, rubrique="", date="")
    assert _compte(_image(pf.dessiner(vide, "fr", marque)), (800, 70, 1120, 110), COULEURS["discret"]) < 5


def test_le_pied_porte_un_filet_et_la_source(marque):
    im = _image(pf.dessiner(CHAMPS, "fr", marque))
    assert _compte(im, (pf.MARGE, 520, pf.LARGEUR - pf.MARGE, 545), COULEURS["filet"]) > 500
    assert _compte(im, (pf.MARGE, 550, 400, 590), COULEURS["source"]) > 30


def test_le_titre_occupe_le_milieu(marque):
    im = _image(pf.dessiner(CHAMPS, "fr", marque))
    assert _compte(im, (pf.MARGE, 200, 1128, 500), COULEURS["titre"]) > 500


def test_un_long_titre_se_reduit_pour_tenir(marque):
    court = pf.titre_carte("Un titre court", marque, 360)
    long = pf.titre_carte(" ".join(["gouvernance"] * 30), marque, 360)
    assert court[0] == pf.TITRE_TAILLES[0] and len(court[1]) == 1
    assert long[0] < pf.TITRE_TAILLES[0] and len(long[1]) <= pf.TITRE_LIGNES


def test_un_titre_qui_ne_tient_jamais_garde_ses_mots(marque):
    taille, lignes = pf.titre_carte(" ".join(["gouvernance"] * 200), marque, 360)
    assert taille == pf.TITRE_TAILLES[-1] and len(lignes) == pf.TITRE_LIGNES
    assert " ".join(lignes).split() == ["gouvernance"] * 200


def test_le_titre_se_coupe_entre_deux_mots(marque):
    f = pf._police(str(POLICE), 600, 40)
    lignes = pf.couper("un deux trois quatre cinq six sept huit neuf dix", f, 200)
    assert len(lignes) > 1 and " ".join(lignes) == "un deux trois quatre cinq six sept huit neuf dix"
    assert all(f.getlength(x) <= 200 for x in lignes if " " in x)
    assert pf.couper("anticonstitutionnellement", f, 10) == ["anticonstitutionnellement"]


def test_une_police_statique_se_prend_telle_quelle(monkeypatch):
    class Statique:
        def get_variation_axes(self):
            raise OSError("pas une police variable")

        def set_variation_by_axes(self, axes):
            raise AssertionError("une police statique n'a pas d'axe")

    monkeypatch.setattr(pf.ImageFont, "truetype", lambda chemin, taille: Statique())
    pf._police.cache_clear()
    assert isinstance(pf._police("statique.ttf", 700, 10), Statique)
    pf._police.cache_clear()


def test_la_graisse_d_une_police_variable_est_appliquee():
    pf._police.cache_clear()
    maigre, grasse = pf._police(str(POLICE), 100, 40), pf._police(str(POLICE), 700, 40)
    assert maigre.getlength("MMMM") < grasse.getlength("MMMM")


@pytest.mark.parametrize(
    "faute",
    [
        {"polices": {"titre": [str(POLICE), 500]}},
        {"polices": {r: ["absente.ttf", 500] for r in pf.POLICES}},
        {"couleurs": {**COULEURS, "fond": "pas une couleur"}},
        {"couleurs": {"fond": "#fff"}},
        {"nom": ["seul"]},
        {"textes": {"fr": {"sous": "S", "pied": "P", "d_apres": "D"}}},
        {"textes": {lg: {"sous": "S"} for lg in pf.LANGUES}},
        {"logo": "absent.svg"},
        {"autre": 1},
    ],
)
def test_une_marque_fautive_est_refusee_et_nommee(tmp_path, faute):
    with pytest.raises(pf.MarqueInvalide, match="carte.yaml"):
        pf.charger_marque(ecrire_marque(tmp_path, **faute))


def test_un_logo_sans_points_est_refuse(tmp_path):
    with pytest.raises(pf.MarqueInvalide, match="logo"):
        pf.charger_marque(ecrire_marque(tmp_path, svg='<svg viewBox="0 0 10 10"></svg>'))


def test_une_marque_illisible_est_refusee(tmp_path):
    (tmp_path / "carte.yaml").write_text("a: [b", encoding="utf-8")
    with pytest.raises(pf.MarqueInvalide, match="illisible"):
        pf.charger_marque(tmp_path / "carte.yaml")
    with pytest.raises(pf.MarqueInvalide, match="illisible"):
        pf.charger_marque(tmp_path / "absente.yaml")


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")  # runpy relance le module importé
def test_le_module_se_lance_en_commande(monkeypatch, capsys):
    import runpy
    import sys

    from newskit import media

    monkeypatch.setattr(media, "courant", lambda: {})
    monkeypatch.setattr(sys, "argv", ["pages_fiches", "--graphe", "g"])
    with pytest.raises(SystemExit) as fin:
        runpy.run_module("newskit.pages_fiches", run_name="__main__")
    assert fin.value.code == 0 and "sans pages" in capsys.readouterr().out

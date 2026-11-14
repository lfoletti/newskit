"""Une source peut dire où sa date est écrite dans l'adresse (mainteneur, 14.11.2026). Le Japan AISI publie sous
/activity/activity_news/261030/ (AAMMJJ) sans aucune méta de date : la fiche « AI情報通：10月29日15時の情報 » était
datée du 26 octobre, date d'un document d'OpenAI cité dans le texte ; la page de liste dit 2026年10月30日."""

from __future__ import annotations

import pytest

from newskit import collecte_rss as cr
from newskit import config_sources as cs

AISI = "https://aisi.go.jp/activity/activity_news/261030/"
MOTIF = r"/(?P<a>\d\d)(?P<m>\d\d)(?P<j>\d\d)/?$"


def test_le_motif_de_la_source_lit_la_date_de_l_adresse():
    assert cr.date_de_l_adresse(AISI) == ""  # sans motif, six chiffres ne sont pas une date
    assert cr.date_de_l_adresse(AISI, MOTIF) == "2026-10-30"
    assert cr.date_de_l_adresse("https://x.org/2026/10/28/a", MOTIF) == "2026-10-28"  # le motif muet, la règle commune
    assert cr.date_de_l_adresse("https://aisi.go.jp/a/261399/", MOTIF) == ""  # pas un jour du calendrier
    assert (
        cr.date_de_l_adresse("https://x.org/a/2026-11-03/", r"/(?P<a>\d{4})-(?P<m>\d\d)-(?P<j>\d\d)/") == "2026-11-03"
    )


def test_l_adresse_passe_devant_une_date_lue_dans_le_texte():
    meta = {"date": "2026-10-26", "date_publication": ""}
    assert cr.date_de_l_entree("", meta, AISI, MOTIF) == "2026-10-30"
    assert cr.date_de_l_entree("", meta, AISI) == "2026-10-26"
    assert cr.date_de_l_entree("", {"date_publication": "2026-10-31"}, AISI, MOTIF) == "2026-10-31"
    assert cr.date_de_plan({}, AISI, MOTIF) == ("2026-10-30", "adresse")


def _registre(tmp_path, **en_plus):
    s = {
        "cle": "gov_japan_aisi",
        "plateforme": "Page de liste",
        "nom": "Japan AISI",
        "dossier": "rss/japan_aisi",
        "abonnement": "gratuit",
        "liste": "https://aisi.go.jp/",
        "chemins": "aisi",
        **en_plus,
    }
    import yaml

    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "sources": [s]}, allow_unicode=True), encoding="utf-8")
    return p


def test_le_registre_admet_un_motif_qui_nomme_annee_mois_et_jour(tmp_path):
    src = cs.charger(_registre(tmp_path, date_adresse=MOTIF))["sources"][0]
    assert src.propres["date_adresse"] == MOTIF
    for faux in (r"/(\d\d)(\d\d)(\d\d)/", r"/(?P<a>\d\d)(?P<m>\d\d)/", "(?P<a>"):
        with pytest.raises(cs.ConfigInvalide, match="date_adresse"):
            cs.charger(_registre(tmp_path, date_adresse=faux))


def test_la_collecte_d_une_page_de_liste_date_la_fiche_par_son_adresse(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from newskit import fiche

    src = SimpleNamespace(
        cle="gov_japan_aisi",
        nom="Japan AISI",
        plateforme="Page de liste",
        chemin=tmp_path / "aisi",
        manifeste=tmp_path / "aisi" / "_manifest.json",
        session=None,
        mots_cles=[],
        exclure="",
        doublons_de=[],
        liste="https://aisi.go.jp/",
        flux="",
        graphe="ai-governance",
        propres={"date_adresse": MOTIF},
    )
    monkeypatch.setattr(
        cr,
        "entrees_page_de_liste",
        lambda s, l: [{"lien": AISI, "titre": "AI情報通", "corps": "", "complet": False, "date": "", "auteur": ""}],
    )
    monkeypatch.setattr(cr, "PAUSE_PAGE", 0)
    texte = "OpenAI. “Addendum”, October 26, 2026. " + "評価 " * 120
    monkeypatch.setattr(cr, "corps_de_page", lambda url, *a, **k: (130, texte, False, {"date": "2026-10-26"}))
    monkeypatch.setattr(cr, "_robots_exclut_ia", lambda u: False)
    assert cr.traiter_source(src, 10, [])["neuves"] == 1
    m = json.loads(src.manifeste.read_text(encoding="utf-8"))
    assert fiche.lire_fichier(src.chemin / m[AISI]["file"])[0]["date"] == "2026-10-30"

"""Une source doit être spécialisée pour entrer au registre (mainteneur, 13.11.2026 : « il faut resserrer sur les
sources spécialisées et éviter d'ouvrir au tout venant. c'est même un critère d'admissibilité pour une source »).

Un graphe déclare son sujet (`specialisation: {motif, part}`) : la part des entrées récentes du flux d'un domaine qui
parlent du sujet doit atteindre `part`, sinon l'éclaireur ne le déclare pas. Et l'article atteint par un agrégateur ne
compte plus comme une trouvaille : l'OCDE cite surtout la presse généraliste (mainteneur : « oui, retire le
comptage »)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from newskit import collecte_rss as rss
from newskit import config_sources as cfg
from newskit import depots
from newskit import eclaireur
from newskit import prospecter_source as ps

MOTIF = r"(?<![a-z])(ai|artificial intelligence|llms?)(?![a-z])"
SPEC = {"motif": MOTIF, "part": 0.5}


def test_la_part_des_entrees_qui_parlent_du_sujet():
    textes = ["AI Act: what changes", "Football results", "LLM agents at work", "Weather", "Elections"]
    assert ps.specialisation(textes, MOTIF) == pytest.approx(0.4)
    assert ps.specialisation(["Intelligence artificielle", "Artificial Intelligence in courts"], MOTIF) == 0.5
    assert ps.specialisation(["Inteligência artificial no Brasil"], r"inteligencia artificial") == 1.0  # sans accents
    assert ps.specialisation([], MOTIF) is None and ps.specialisation(None, MOTIF) is None


def test_la_mesure_du_flux_garde_ses_textes():
    items = [{"titre": f"T{i}", "corps": "<p>" + "mot " * 200 + "</p>", "date": "2026-11-13"} for i in range(15)]
    r = ps.mesurer_flux("https://x.org/feed", items)
    assert len(r["textes"]) == 12 and r["textes"][0].startswith("T0 mot") and len(r["textes"][0]) <= 300


def test_l_etude_garde_les_textes(monkeypatch):
    r = {
        "voie": "flux",
        "adresse": "https://x.org/feed",
        "entrees": 3,
        "dernier": "2026-11-13",
        "mots": 400,
        "note": "le flux porte le texte",
        "echantillon": "",
        "textes": ["AI one", "AI two", "sport"],
    }
    monkeypatch.setattr(ps, "essai_substack", lambda d: None)
    monkeypatch.setattr(ps, "essai_flux", lambda d: r)
    m = {"domaines": {}}
    depots.etudier_source("x.org", m, "2026-11-13")
    assert m["domaines"]["x.org"]["etude"]["textes"] == ["AI one", "AI two", "sport"]


def _memoire(textes, depots_=2):
    return {
        "domaines": {
            "infobae.com": {
                "depots": depots_,
                "graphes": ["g"],
                "etudie_le": "2026-11-13",
                "etude": {
                    "verdict": "OUI",
                    "flux": "https://infobae.com/feed",
                    "mots": 600,
                    "voie": "flux",
                    "note": "",
                    "yaml": "",
                    "dernier": "2026-11-13",
                    **({"textes": textes} if textes is not None else {}),
                },
            }
        }
    }


@pytest.fixture
def registre(monkeypatch):
    poses = []
    monkeypatch.setattr(cfg, "toutes", lambda *a, **k: [])
    monkeypatch.setattr(eclaireur, "inserer_source", lambda bloc, reg: poses.append(bloc) or True)
    monkeypatch.setattr(eclaireur, "bloc_source", lambda g, dom, e, n, lex: (f"auto_{dom}", "bloc"))
    monkeypatch.setattr(ps, "JAMAIS", [])
    return poses


def test_un_generaliste_n_entre_pas(monkeypatch, registre):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"specialisation": SPEC})
    m = _memoire(["AI in Argentina", "Fútbol", "Elecciones", "Clima", "Economía"])
    lignes = eclaireur.declarer_sources("g", m)
    assert registre == [] and "declaree" not in m["domaines"]["infobae.com"]
    assert any("généraliste" in l and "20 %" in l and "50 %" in l for l in lignes), lignes
    assert m["domaines"]["infobae.com"]["ecartee"] == {
        "date": m["domaines"]["infobae.com"]["ecartee"]["date"],
        "graphe": "g",
        "part": 0.2,
    }


def test_une_source_specialisee_entre(monkeypatch, registre):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"specialisation": SPEC})
    m = _memoire(["AI Act", "LLM audits", "AI liability", "Privacy news"])
    eclaireur.declarer_sources("g", m)
    assert registre == ["bloc"] and m["domaines"]["infobae.com"]["declaree"]["cle"] == "auto_infobae.com"


def test_une_etude_sans_textes_se_refait_avant_de_juger(monkeypatch, registre):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"specialisation": SPEC})
    refaites = []

    def etudier(dom, m, jour):
        refaites.append(dom)
        m["domaines"][dom]["etude"]["textes"] = ["Fútbol", "Clima"]
        return m["domaines"][dom]

    monkeypatch.setattr(depots, "etudier_source", etudier)
    m = _memoire(None)
    eclaireur.declarer_sources("g", m)
    assert refaites == ["infobae.com"] and registre == []


def test_un_graphe_sans_sujet_declare_garde_l_ancienne_regle(monkeypatch, registre):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    eclaireur.declarer_sources("g", _memoire(["Fútbol"]))
    assert registre == ["bloc"]


def test_le_registre_valide_le_sujet_du_graphe():
    assert cfg.valider_specialisation(SPEC, "g") == SPEC
    for faux in ({"motif": MOTIF}, {"motif": "(", "part": 0.5}, {"motif": MOTIF, "part": 1.5}, "ai"):
        with pytest.raises(cfg.ConfigInvalide):
            cfg.valider_specialisation(faux, "g")


def test_l_article_d_un_agregateur_ne_compte_plus(monkeypatch, tmp_path):
    assert not hasattr(eclaireur, "noter_trouvaille")
    agregat = "https://oecd.ai/en/incidents/2026-11-12-a8be"
    md = "[##### A](https://www.infobae.com/a)\n\n2026-11-12\n\nInfobae\n"
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
        graphe="g",
        propres={"agregateur": True},
    )
    monkeypatch.setattr(
        rss,
        "entrees_plan_quotidien",
        lambda s, l: [{"lien": agregat, "titre": "", "corps": "", "complet": False, "date": "", "auteur": ""}],
    )
    monkeypatch.setattr(rss, "PAUSE_PAGE", 0)
    pages = {agregat: (300, md, False, {"titre": "A"}), "https://www.infobae.com/a": (400, "mot " * 400, False, {})}
    monkeypatch.setattr(rss, "corps_de_page", lambda url, *a, **k: pages.get(url, (0, "", False, {})))
    monkeypatch.setattr(rss, "_robots_exclut_ia", lambda u: False)
    monkeypatch.setattr(eclaireur, "MEMOIRE", tmp_path / "_sources.json")
    assert rss.traiter_source(src, 10, [])["neuves"] == 1
    assert not (tmp_path / "_sources.json").exists()
    m = json.loads(src.manifeste.read_text(encoding="utf-8"))
    assert m[agregat]["origine"] == "https://www.infobae.com/a"


def test_le_registre_refuse_un_sujet_mal_forme(tmp_path):
    base = {
        "version": 1,
        "sources": [
            {
                "cle": "a",
                "plateforme": "Flux RSS",
                "nom": "A",
                "dossier": "rss/a",
                "flux": "https://a.org/feed",
                "abonnement": "gratuit",
                "graphe": "essai-g",
            }
        ],
        "graphes": {"essai-g": {"libelle": "G", "domaine": "d", "specialisation": SPEC}},
    }
    cfg._valider(base, tmp_path / "sources.yaml")
    base["graphes"]["essai-g"]["specialisation"] = {"motif": MOTIF, "part": 2}
    with pytest.raises(cfg.ConfigInvalide, match="specialisation.part"):
        cfg._valider(base, tmp_path / "sources.yaml")

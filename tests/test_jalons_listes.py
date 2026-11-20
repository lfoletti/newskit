"""Les listes de sélection d'un graphe (mainteneur, 20.11.2026 : « oui c'est une source », sur une liste hebdomadaire de
papiers choisie par des humains). Une page publique nomme des papiers arXiv par ses liens ; le registre la déclare sous
`veille_arxiv.jalons.listes.<graphe>` ; ses papiers valent des papiers choisis à la main : jalons d'office, collectés
s'ils manquent. `depuis` (AAAA-MM) écarte les papiers soumis avant ce mois."""

from __future__ import annotations

import copy
import urllib.error
from pathlib import Path

import pytest
import yaml

from newskit import choisir_jalons as cj
from newskit import config_sources as cfg

PAGE = """# Papers of the Week

| 0) **v3** - A version. | [Paper](https://arxiv.org/abs/2611.07777v3) |

| 1) **s3** - Multi-agent RL. | [Paper](https://academy.example.org/papers/s-3-swarms-2611.08615), [Tweet](https://x.com/a/status/2119792446242718170) |
| 2) **SoMem** - Memory. | [Paper](https://arxiv.org/abs/2611.01234v2) |
| 3) **Old** - An old one. | [Paper](https://arxiv.org/pdf/2301.00001.pdf) |
| 4) **Again** - Same paper twice. | [Paper](https://arxiv.org/html/2611.08615) |
| 5) **No arXiv** - A blog. | [Post](https://blog.example.org/2026/11/post) |
Year 2026.11234 in prose is not a link.
"""


def test_les_papiers_d_une_liste_se_lisent_dans_ses_liens_une_fois_chacun():
    assert cj.ids_d_une_liste(PAGE) == ["2611.07777", "2611.08615", "2611.01234", "2301.00001"]


def test_depuis_ecarte_les_papiers_soumis_avant_le_mois():
    assert cj.ids_d_une_liste(PAGE, "2026-11") == ["2611.07777", "2611.08615", "2611.01234"]
    assert cj.ids_d_une_liste(PAGE, "2023-01") == ["2611.07777", "2611.08615", "2611.01234", "2301.00001"]
    assert cj.ids_d_une_liste(PAGE, "2023-02") == ["2611.07777", "2611.08615", "2611.01234"]


@pytest.fixture
def listes(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "RACINE", tmp_path)
    veille = {"jalons": {"listes": {"g": [{"url": "https://exemple.org/liste.md", "depuis": "2026-11"}]}}}
    monkeypatch.setattr(cfg, "veille_arxiv", lambda: veille)
    lues = []

    def lire(url):
        lues.append(url)
        return PAGE

    monkeypatch.setattr(cj, "lire_liste", lire)
    return lues


def test_les_papiers_d_une_liste_sont_choisis_a_la_main(listes, monkeypatch):
    from newskit import combler_canon

    monkeypatch.setattr(combler_canon, "papiers_du_canon", lambda nom: [])
    assert cj.choisis_a_la_main("g") == ["2611.07777", "2611.08615", "2611.01234"]
    assert listes == ["https://exemple.org/liste.md"]
    assert cj.choisis_a_la_main("autre") == []


def test_une_liste_injoignable_se_lit_dans_sa_derniere_copie(listes, monkeypatch):
    assert cj.papiers_des_listes("g") == ["2611.07777", "2611.08615", "2611.01234"]

    def panne(url):
        raise urllib.error.URLError("hors ligne")

    monkeypatch.setattr(cj, "lire_liste", panne)
    assert cj.papiers_des_listes("g") == ["2611.07777", "2611.08615", "2611.01234"]


def test_sans_reseau_la_liste_se_lit_dans_sa_copie_sans_appel(listes):
    assert cj.papiers_des_listes("g", sans_reseau=True) == []
    cj.papiers_des_listes("g")
    assert cj.papiers_des_listes("g", sans_reseau=True) == ["2611.07777", "2611.08615", "2611.01234"]
    assert len(listes) == 1


def test_une_liste_jamais_lue_et_injoignable_ne_donne_rien(listes, monkeypatch):
    def panne(url):
        raise urllib.error.URLError("hors ligne")

    monkeypatch.setattr(cj, "lire_liste", panne)
    assert cj.papiers_des_listes("g") == []


# --- le registre


REGISTRE = Path(__file__).resolve().parent.parent / "media_sample" / "sources.yaml"


def _charger(tmp_path: Path, listes) -> dict:
    d = yaml.safe_load(REGISTRE.read_text(encoding="utf-8"))
    d = copy.deepcopy(d)
    d.setdefault("veille_arxiv", {}).setdefault("jalons", {})["listes"] = listes
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    return cfg.charger(p)


def test_le_registre_accepte_une_liste_de_selection(tmp_path):
    c = _charger(
        tmp_path, {"ai-essai": [{"url": "https://exemple.org/l.md", "depuis": "2026-11"}, {"url": "http://e.org/m"}]}
    )
    assert c["veille_arxiv"]["jalons"]["listes"]["ai-essai"][1] == {"url": "http://e.org/m"}


@pytest.mark.parametrize(
    "listes",
    [
        ["https://exemple.org/l.md"],
        {"ai-essai": "https://exemple.org/l.md"},
        {"ai-essai": [{"url": "ftp://exemple.org/l.md"}]},
        {"ai-essai": [{"adresse": "https://exemple.org/l.md"}]},
        {"ai-essai": [{"url": "https://exemple.org/l.md", "autre": 1}]},
        {"ai-essai": [{"url": "exemple.org/l.md"}]},
        {"ai-essai": 5},
        {"ai-essai": [5]},
        {"ai-essai": [{"depuis": "2026-11"}]},
        {"ai-essai": [{"url": "https://exemple.org/l.md", "depuis": "2026-13"}]},
        {"ai-essai": [{"url": "https://exemple.org/l.md", "depuis": "nov. 2026"}]},
        {"G majuscule": [{"url": "https://exemple.org/l.md"}]},
    ],
)
def test_le_registre_refuse_une_liste_mal_formee(tmp_path, listes):
    with pytest.raises(cfg.ConfigInvalide, match="jalons.listes"):
        _charger(tmp_path, listes)


def test_le_compte_sans_reseau_ne_lit_pas_les_listes(listes, monkeypatch):
    from newskit import combler_canon

    monkeypatch.setattr(combler_canon, "papiers_du_canon", lambda nom: [])
    assert cj.choisis_a_la_main("g", sans_reseau=True) == []
    monkeypatch.setattr(cj, "papiers_du_graphe", lambda g: {"2611.08615": {"titre": "s3", "chemin": "x.md"}})
    monkeypatch.setattr(cj, "citations", lambda ids, sans_reseau=False: {})
    monkeypatch.setattr(cj, "fiches_citantes", lambda g: {})
    assert cj.choisir("g", ecrire=False, sans_reseau=True) == {}
    assert listes == []
    assert list(cj.choisir("g", ecrire=False)) == ["2611.08615"]
    assert listes == ["https://exemple.org/liste.md"]


def test_une_liste_se_lit_par_http_avec_le_nom_et_le_contact_de_l_edition(monkeypatch):
    vues = []

    class Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "| [Paper](https://arxiv.org/abs/2611.08615) é |".encode()

    def ouvrir(req, timeout):
        vues.append((req.full_url, req.get_header("User-agent"), timeout))
        return Reponse()

    monkeypatch.setattr(cj.urllib.request, "urlopen", ouvrir)
    monkeypatch.setattr(cj, "contact", lambda: "robot@exemple.org")
    assert cj.lire_liste("https://exemple.org/l.md").endswith("é |")
    assert vues == [("https://exemple.org/l.md", f"{cfg.edition()['nom']}/1.0 (mailto:robot@exemple.org)", 60)]

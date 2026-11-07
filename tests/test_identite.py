"""L'identité avant le corps (CHANTIER_GENERIQUE.md § 4.2, étape 2 de Newskit ; anticorps 10 du témoin,
test_fiche_ecrire_refuse_identite_en_double).

Une fiche a une identité : son adresse canonique, son identifiant arXiv, son DOI. `fiche.ecrire` refuse de créer une
fiche dont une clé est déjà portée par une autre fiche du même graphe (inclusions comprises) : `IdentiteDejaVue`, avec
la fiche existante. La réécriture d'un même chemin reste libre. Mesure du 06.11.2026 sur l'arbre : 229 clés portées
par plusieurs fiches, toutes de vrais doublons (le même billet par deux sources, un flux et le plan de son site, le
même papier par l'arXiv et par une revue), dès que `partie` entre dans l'identité d'un texte découpé.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RACINE_CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE_CODE / "src" / "newskit"))
from newskit import fiche  # noqa: E402


# --- Inventaire de classe des champs ---------------------------------------

def test_chaque_champ_du_contrat_est_classe_identite_ou_non():
    assert not fiche.IDENTITE & fiche.NON_IDENTITE
    assert set(fiche.ORDRE) == fiche.IDENTITE | fiche.NON_IDENTITE, (
        "champ non classé : " + ", ".join(sorted(set(fiche.ORDRE) - fiche.IDENTITE - fiche.NON_IDENTITE)))


def test_papier_arxiv_n_est_pas_une_identite():
    assert "papier_arxiv" in fiche.NON_IDENTITE
    assert fiche.cles_d_identite({"papier_arxiv": "2401.01234", "url": ""}) == frozenset()


# --- Les clés, en table -----------------------------------------------------

TABLE = [
    ({"url": "HTTPS://Example.ORG/a/?utm_source=x&b=1"}, {"url:https://example.org/a?b=1"}),
    ({"url": "https://example.org/a/"}, {"url:https://example.org/a"}),
    ({"url": "https://www.fedlex.admin.ch/eli/cc/2022/491/fr#art_70"}, {"url:https://www.fedlex.admin.ch/eli/cc/2022/491/fr#art_70"}),
    ({"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"},
     {"url:https://eur-lex.europa.eu/legal-content/EN/TXT?uri=CELEX%3A32016R0679"}),
    ({"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679"},
     {"url:https://eur-lex.europa.eu/legal-content/EN/TXT?uri=CELEX%3A32016R0679"}),
    ({"url": "https://www.youtube.com/watch?v=abc"}, {"url:https://www.youtube.com/watch?v=abc"}),
    ({"url": "https://arxiv.org/abs/2401.01234v2"}, {"url:https://arxiv.org/abs/2401.01234v2", "arxiv:2401.01234"}),
    ({"url": "https://arxiv.org/pdf/2401.01234"}, {"url:https://arxiv.org/pdf/2401.01234", "arxiv:2401.01234"}),
    ({"url": "", "arxiv_id": "2401.01234v3"}, {"arxiv:2401.01234"}),
    ({"url": "", "arxiv_id": "hep-th/9901001"}, {"arxiv:hep-th/9901001"}),
    ({"url": "", "doi": "10.48550/arXiv.2401.01234"}, {"doi:10.48550/arxiv.2401.01234", "arxiv:2401.01234"}),
    ({"url": "", "doi": "10.5334/TISMIR.286"}, {"doi:10.5334/tismir.286"}),
    ({"url": "", "doi": "https://doi.org/10.5334/tismir.286"}, {"doi:10.5334/tismir.286"}),
    ({"url": "https://doi.org/10.5334/tismir.286"}, {"url:https://doi.org/10.5334/tismir.286", "doi:10.5334/tismir.286"}),
    ({"url": "https://rm.coe.int/1680afae3c", "partie": "art-009"}, {"url:https://rm.coe.int/1680afae3c|partie:art-009"}),
    ({"url": ""}, set()),
    ({"url": "file:///tmp/a.md"}, set()),
    ({}, set()),
]


@pytest.mark.parametrize("champs,attendu", TABLE)
def test_cles_d_identite(champs, attendu):
    assert fiche.cles_d_identite(champs) == frozenset(attendu)


def test_deux_parties_d_un_meme_texte_sont_deux_identites():
    a = fiche.cles_d_identite({"url": "https://rm.coe.int/1680afae3c", "partie": "art-009"})
    b = fiche.cles_d_identite({"url": "https://rm.coe.int/1680afae3c", "partie": "art-010"})
    assert not a & b


# --- Inventaire des écrivains -----------------------------------------------

# Un module qui crée des fiches traite IdentiteDejaVue, ou dit ici pourquoi il n'en a pas besoin.
REECRIVAINS = {
    "diachronie": "réécrit une fiche existante (même chemin) : la réécriture est libre",
    "fil": "réécrit l'en-tête d'une fiche existante (même chemin)",
    "recuperer_tronquee": "réécrit une fiche tronquée existante (même chemin)",
    "convertir_substack_page_text": "réécrit une fiche existante (même chemin)",
}


def _ecrivains() -> set[str]:
    return {p.stem for p in (RACINE_CODE / "src" / "newskit").glob("*.py")
            if p.stem != "fiche" and re.search(r"\bfiche\.ecrire\(", p.read_text(encoding="utf-8"))}


def test_chaque_ecrivain_traite_l_identite_deja_vue_ou_dit_pourquoi():
    traite = {m for m in _ecrivains()
              if "IdentiteDejaVue" in (RACINE_CODE / "src" / "newskit" / f"{m}.py").read_text(encoding="utf-8")}
    raisons = {m for m, r in REECRIVAINS.items() if r}
    assert _ecrivains() == traite | raisons, (
        "écrivain sans traitement ni raison : " + ", ".join(sorted(_ecrivains() - traite - raisons)))
    assert not raisons - _ecrivains(), "raison pour un module qui n'écrit plus de fiche"




# --- Amont : racine temporaire, registre factice ----------------------------

GRAPHES = {"a": ["sources/a"], "inclut-a": ["sources/inclut", "sources/a"], "sans-lien": ["sources/z"]}


@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: GRAPHES)
    fiche.oublier_index()
    yield tmp_path
    fiche.oublier_index()


def _champs(**k):
    return {"title": "Un papier", "subtitle": "", "author": "X", "date": "2024-01-02", "url": "",
            "publication": "arXiv", "collected": "2026-11-06", **k}


CORPS = "# Un papier\n\n" + "Un texte de fiche assez long pour passer le contrôle du corps. " * 8


def test_amont_un_meme_papier_par_deux_voies_donne_une_seule_fiche(racine):
    a = racine / "sources" / "a" / "2024-01-02_a.md"
    fiche.ecrire(a, _champs(url="https://arxiv.org/abs/2401.01234v1"), CORPS)
    b = racine / "sources" / "a" / "2024-01-02_b.md"
    with pytest.raises(fiche.IdentiteDejaVue) as e:
        fiche.ecrire(b, _champs(url="https://example.org/b", doi="10.48550/arXiv.2401.01234"), CORPS)
    assert e.value.existante == a.resolve() and e.value.cle == "arxiv:2401.01234" and not b.exists()
    fiche.ecrire(a, _champs(url="https://arxiv.org/abs/2401.01234v1", title="Un papier, réécrit"), CORPS)   # réécriture libre
    assert "réécrit" in a.read_text(encoding="utf-8")
    c = racine / "sources" / "inclut" / "c.md"
    with pytest.raises(fiche.IdentiteDejaVue):
        fiche.ecrire(c, _champs(arxiv_id="2401.01234v2"), CORPS)                  # le graphe qui inclut voit A
    d = racine / "sources" / "z" / "d.md"
    fiche.ecrire(d, _champs(url="https://arxiv.org/abs/2401.01234v1"), CORPS)     # graphe sans lien : accepté
    assert d.exists()


def test_amont_l_index_suit_les_fiches_ecrites_dans_le_processus(racine):
    fiche.ecrire(racine / "sources" / "a" / "x.md", _champs(url="https://example.org/x"), CORPS)
    with pytest.raises(fiche.IdentiteDejaVue):
        fiche.ecrire(racine / "sources" / "inclut" / "y.md", _champs(url="https://EXAMPLE.org/x/?utm_medium=m"), CORPS)


def test_amont_l_index_lit_les_fiches_deja_sur_le_disque(racine):
    p = racine / "sources" / "inclut" / "ancienne.md"
    p.parent.mkdir(parents=True)
    p.write_text(fiche.rendre(fiche.normaliser(_champs(doi="10.5334/tismir.286"), CORPS), CORPS), encoding="utf-8")
    fiche.oublier_index()
    with pytest.raises(fiche.IdentiteDejaVue) as e:
        fiche.ecrire(racine / "sources" / "a" / "neuve.md", _champs(url="https://doi.org/10.5334/TISMIR.286"), CORPS)
    assert e.value.existante == p.resolve()


def test_amont_une_fiche_deplacee_reprend_son_identite(racine):
    ancienne = racine / "sources" / "a" / "2024-03-01_p.md"
    fiche.ecrire(ancienne, _champs(arxiv_id="2401.01234"), CORPS)
    nouvelle = racine / "sources" / "a" / "2024-01-01_p.md"
    fiche.ecrire(nouvelle, _champs(arxiv_id="2401.01234"), CORPS, remplace=ancienne)
    ancienne.unlink()
    with pytest.raises(fiche.IdentiteDejaVue) as e:
        fiche.ecrire(racine / "sources" / "a" / "autre.md", _champs(arxiv_id="2401.01234"), CORPS)
    assert e.value.existante == nouvelle.resolve()


def test_amont_une_fiche_supprimee_libere_son_identite(racine):
    p = racine / "sources" / "a" / "p.md"
    fiche.ecrire(p, _champs(url="https://example.org/p"), CORPS)
    p.unlink()
    fiche.ecrire(racine / "sources" / "a" / "q.md", _champs(url="https://example.org/p"), CORPS)


def test_amont_hors_graphe_est_une_erreur_sauf_demande(racine):
    p = racine / "sources" / "_en_attente" / "p.md"
    with pytest.raises(fiche.HorsGraphe):
        fiche.ecrire(p, _champs(url="https://example.org/p"), CORPS)
    fiche.ecrire(p, _champs(url="https://example.org/p"), CORPS, hors_graphe=True)
    assert p.exists()


def test_amont_hors_des_donnees_rien_n_est_controle(racine, tmp_path_factory):
    ailleurs = tmp_path_factory.mktemp("ailleurs")
    for n in ("p.md", "q.md"):
        fiche.ecrire(ailleurs / n, _champs(url="https://example.org/p"), CORPS)
    assert len(list(ailleurs.glob("*.md"))) == 2


def test_amont_la_reecriture_d_un_doublon_deja_sur_le_disque_reste_libre(racine):
    """fil, diachronie, la fraîcheur réécrivent l'en-tête d'une fiche existante, doublon compris (avant --fondre)."""
    for n in ("a1.md", "a2.md"):
        p = racine / "sources" / "a" / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(fiche.rendre(fiche.normaliser(_champs(url="https://example.org/d"), CORPS), CORPS), encoding="utf-8")
    fiche.oublier_index()
    for n in ("a1.md", "a2.md"):
        fiche.ecrire(racine / "sources" / "a" / n, _champs(url="https://example.org/d", title="Réécrite"), CORPS)
    assert all("Réécrite" in (racine / "sources" / "a" / n).read_text(encoding="utf-8") for n in ("a1.md", "a2.md"))

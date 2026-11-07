"""L'index unique (CHANTIER_GENERIQUE.md § 4.2, enfants) : les listes « déjà connues » de l'arXiv, de l'éclaireur et de
l'éditeur lisent l'index d'identité de `fiche`, sous-dossiers compris, avec la même canonicalisation que la garde.

Avant le 06.11.2026, chacune avait sa lecture : `adresses_connues` ne descendait pas dans les sous-dossiers et ne
retirait que la barre finale, `identifiants_du_graphe` ne lisait que le champ `arxiv_id`. Une page connue sous une
autre forme était relue (un appel au lecteur, parfois payé) avant d'être refusée à l'écriture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from newskit import collecte_arxiv as ca  # noqa: E402
from newskit import eclaireur  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import reporter  # noqa: E402

GRAPHES = {"g": ["sources/g"], "autre": ["sources/autre"]}


@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: GRAPHES)
    fiche.oublier_index()
    yield tmp_path
    fiche.oublier_index()


def _poser(racine: Path, rel: str, **champs) -> Path:
    c = {
        "title": "T",
        "subtitle": "",
        "author": "",
        "date": "2024-01-01",
        "url": "",
        "publication": "p",
        "collected": "2026-11-06",
        **champs,
    }
    p = racine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fiche.rendre(fiche.normaliser(c, "# T\n\ncorps"), "# T\n\ncorps"), encoding="utf-8")
    return p


def test_deja_vue_rend_la_fiche_qui_porte_le_document(racine):
    p = _poser(racine, "sources/g/sous/a.md", url="https://e.org/a")
    assert fiche.deja_vue("g", {"url": "HTTPS://E.org/a/?utm_source=x"}) == p.resolve()
    assert fiche.deja_vue("autre", {"url": "https://e.org/a"}) is None
    p.unlink()
    assert fiche.deja_vue("g", {"url": "https://e.org/a"}) is None


def test_les_identifiants_arxiv_du_graphe_lisent_adresse_et_doi(racine):
    _poser(racine, "sources/g/eclaireur/p.md", url="https://arxiv.org/pdf/2401.01234v2")
    _poser(racine, "sources/g/revues/q.md", url="https://journal.example/q", doi="10.48550/arXiv.2402.00001")
    _poser(racine, "sources/g/r.md", arxiv_id="2403.00002")
    assert ca.identifiants_du_graphe("g") == {"2401.01234", "2402.00001", "2403.00002"}


def test_les_adresses_connues_descendent_dans_les_sous_dossiers(racine):
    _poser(racine, "sources/g/rss/flux/a.md", url="https://e.org/a/")
    _poser(racine, "sources/g/p.md", url="https://arxiv.org/abs/2401.01234v1")
    vues = eclaireur.adresses_connues("g")
    assert "https://e.org/a" in vues and "arxiv:2401.01234" in vues


def test_l_eclaireur_ne_relit_pas_une_page_connue_sous_une_autre_forme(racine, monkeypatch):
    from newskit import collecte_rss as cr

    _poser(racine, "sources/g/rss/a.md", url="https://e.org/a")
    monkeypatch.setattr(cr, "corps_de_page", lambda u: pytest.fail(f"page relue : {u}"))
    ligne = eclaireur.ingerer(
        "g", {"url": "https://E.org/a/?utm_medium=m", "titre": "", "point": 0}, [], {}, set(), {}, "2026-11-06"
    )
    assert "déjà au graphe" in ligne


def test_l_editeur_ne_verse_pas_une_source_deja_au_graphe(racine):
    _poser(racine, "sources/g/rss/a.md", url="https://e.org/a")
    sources = {
        1: {"url": "https://e.org/a/?utm_source=lettre", "titre": "A", "editeur": "E", "genre": "web"},
        2: {"url": "https://e.org/b", "titre": "B", "editeur": "E", "genre": "web"},
        3: {"url": "https://e.org/c", "titre": "C", "editeur": "E", "genre": "graphe"},
    }
    faits = [{"id": "f1", "citations": [{"source": 1}, {"source": 2}, {"source": 3}]}]
    assert [x["url"] for x in reporter.sources_a_verser(sources, faits, "g")] == ["https://e.org/b"]


# --- La porte de la deep research (§ 4.2 : « ingerer seule porte, bilan nouvelles / déjà vues / refusées ») ---------


def test_un_lot_de_l_eclaireur_ne_lit_qu_une_fois_un_document_et_rend_son_bilan(racine, monkeypatch, capsys):
    """Un lot de recherche (la deep research = l'éclaireur sur chaque sujet) : une page neuve, la même sous une autre
    forme, une page déjà au graphe, une page trop courte. Une seule lecture par document, un bilan à trois termes."""
    from newskit import collecte_rss as cr

    _poser(racine, "sources/g/rss/connue.md", url="https://e.org/connue")
    lues = []

    def corps(u):
        lues.append(u)
        return (5, "court", False, {}) if "courte" in u else (400, "Un texte assez long. " * 60, False, {"titre": "N"})

    monkeypatch.setattr(cr, "corps_de_page", corps)
    monkeypatch.setattr(eclaireur, "DOSSIER", racine / "sources" / "g" / "eclaireur")
    monkeypatch.setattr(eclaireur, "_date_du_lecteur", lambda u: "", raising=False)
    monkeypatch.setattr(eclaireur, "MEMOIRE", racine / "memoire.json")
    from newskit import depots

    monkeypatch.setattr(depots, "deja_declaree", lambda d: True)
    pages = ["https://e.org/neuve", "https://E.org/neuve/?utm_source=x", "https://e.org/connue", "https://e.org/courte"]
    lignes = eclaireur.ingerer_lot("g", pages, [("sujet", "un sujet")], {"mots_min": 50})
    assert lues == ["https://e.org/neuve", "https://e.org/courte"]
    assert eclaireur.bilan(lignes) == {"nouvelles": 1, "deja_vues": 2, "refusees": 1}
    assert "1 nouvelle(s), 2 déjà vue(s), 1 refusée(s)" in capsys.readouterr().out

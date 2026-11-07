"""L'éditeur d'une page web est le nom que la page déclare (`og:site_name`), pas son domaine (04.11.2026 :
« thenextweb.com » corrigé à la main dans deux dossiers d'éclairage)."""
from __future__ import annotations

from newskit import lecture_web as lw
from newskit import reporter as rp

URL = "https://thenextweb.com/news/ai-act-guidelines"
CORPS = "".join(f"<p>Point {i}. The Commission published guidance on general-purpose models and their providers "
                f"under the AI Act, with obligations that apply from August.</p>" for i in range(40))


def _page(entete: str) -> bytes:
    return f"<html><head><title>AI Act guidance</title>{entete}</head><body><article>{CORPS}</article></body></html>".encode()


def _servir(monkeypatch, corps: bytes):
    monkeypatch.setattr(lw, "_obtenir", lambda url, entetes=None: (corps, "text/html", "utf-8", ""))


def test_l_editeur_est_le_nom_declare_par_la_page(monkeypatch):
    _servir(monkeypatch, _page('<meta property="og:site_name" content="The Next Web">'))
    p = lw.lire(URL)
    assert p["editeur"] == "The Next Web"
    assert rp._source_web(1, p)["editeur"] == "The Next Web"


def test_sans_nom_declare_l_editeur_reste_le_domaine(monkeypatch):
    _servir(monkeypatch, _page(""))
    p = lw.lire(URL)
    assert not p.get("editeur")
    assert rp._source_web(1, p)["editeur"] == rp.domaine(URL)


def test_une_page_lue_par_imitation_garde_son_editeur():
    p = lw._page_de_html(URL, _page('<meta property="og:site_name" content="The Next Web">').decode(), "imitation")
    assert p["editeur"] == "The Next Web"

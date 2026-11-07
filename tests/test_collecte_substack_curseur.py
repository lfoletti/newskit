"""L'archive de Substack se pagine par curseur (06.11.2026 : `offset=0` rend 400 « Invalid value » sur toutes les
publications ; la réponse est {posts, nextCursor}, la page suivante se demande avec `cursor=<nextCursor>`)."""

from __future__ import annotations

from newskit import collecte_substack as cs

PAGES = {
    "https://x.substack.com/api/v1/archive?sort=new&limit=25": {
        "posts": [{"post_date": "2026-11-02T12:00:00Z", "id": 1}, {"post_date": "2026-06-01T12:00:00Z", "id": 2}],
        "nextCursor": "c=1",
    },
    "https://x.substack.com/api/v1/archive?sort=new&limit=25&cursor=c%3D1": {
        "posts": [{"post_date": "2026-03-01T12:00:00Z", "id": 3}, {"post_date": "2025-10-01T12:00:00Z", "id": 4}],
        "nextCursor": "c=2",
    },
    "https://x.substack.com/api/v1/archive?sort=new&limit=25&cursor=c%3D2": {
        "posts": [{"post_date": "2025-01-01T12:00:00Z", "id": 5}],
        "nextCursor": None,
    },
}


def _archive(monkeypatch, depuis=""):
    vus = []
    monkeypatch.setattr(cs, "get", lambda url, cookie=None: vus.append(url) or PAGES.get(url))
    return [p["id"] for p in cs.archive("x.substack.com", depuis=depuis)], vus


def test_l_archive_suit_le_curseur_jusqu_a_la_fin(monkeypatch):
    ids, vus = _archive(monkeypatch)
    assert ids == [1, 2, 3, 4, 5] and vus == list(PAGES)


def test_l_archive_s_arrete_au_premier_billet_anterieur(monkeypatch):
    ids, vus = _archive(monkeypatch, depuis="2026-01-01")
    assert ids == [1, 2, 3] and vus == list(PAGES)[:2]


def test_une_reponse_vide_ou_refusee_arrete_l_archive_sans_planter(monkeypatch):
    monkeypatch.setattr(cs, "get", lambda url, cookie=None: None)
    assert cs.archive("x.substack.com") == []
    monkeypatch.setattr(cs, "get", lambda url, cookie=None: {"posts": [], "nextCursor": None})
    assert cs.archive("x.substack.com", depuis="2026-01-01") == []


def test_une_page_qui_revient_arrete_l_archive(monkeypatch):
    # chinatalk.media redirige vers www. et la redirection perd le curseur : la première page revenait sans fin
    # (2004 billets lus, 41 distincts, 06.11.2026).
    premiere = PAGES["https://x.substack.com/api/v1/archive?sort=new&limit=25"]
    vus = []
    monkeypatch.setattr(cs, "get", lambda url, cookie=None: vus.append(url) or premiere)
    assert [p["id"] for p in cs.archive("x.substack.com")] == [1, 2] and len(vus) == 2

"""Un ld+json dont les mots-clés mêlent texte et nombres (« keywords »: ["IA", 2026]) : la page se lit (13.11.2026, la
reprise des fiches de l'OCDE s'est arrêtée sur « sequence item 1: expected str instance, int found »)."""

from __future__ import annotations

from newskit import collecte_rss as rss


def test_des_mots_cles_numeriques_ne_font_pas_tomber_la_page():
    html = '<script type="application/ld+json">{"@type": "NewsArticle", "headline": "T", "keywords": ["IA", 2026, null]}</script>'
    assert rss.meta_de_page(html)["mots_cles"] == "IA, 2026"

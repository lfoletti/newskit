"""Lecteur web : le nom en tête d'une fiche vient de son corps, jamais d'un bloc de liens voisins (03.11.2026).

Cas réel figé : algoritmes.overheid.nl/en/algoritme/59194242, la fiche SafeGPT de Reimerswaal (gm0703), lue comme celle
de Lansingerland. Deux causes dans `rattraper` : la carte « SafeGPT » des fiches semblables ramenait l'ancre en tête
de page, et « Gemeente Reimerswaal » passait pour déjà lu, à cause de l'adresse gemeente@reimerswaal.nl."""

from __future__ import annotations

from pathlib import Path

from newskit import lecture_web as lw

FIXTURES = Path(__file__).parent / "fixtures"
CORPS = " ".join(["Le corps de la fiche décrit l'algorithme et son usage par la commune."] * 6)


def _lire(monkeypatch, html):
    monkeypatch.setattr(lw, "_obtenir", lambda url, entetes=None: (html.encode(), "text/html", "utf-8", ""))
    return lw.lire("https://algoritmes.overheid.nl/en/algoritme/59194242")


def test_la_fiche_de_reimerswaal_porte_son_nom_en_tete(monkeypatch):
    p = _lire(monkeypatch, (FIXTURES / "lecture_web_algoritmeregister_reimerswaal.html").read_text(encoding="utf-8"))
    lignes = p["texte"].split("\n")
    assert lignes[:2] == ["SafeGPT", "Gemeente Reimerswaal"]
    assert not any("Lansingerland" in l for l in lignes[:20])
    assert "Lansingerland" not in p["titre"]


def test_un_bloc_voisin_ne_ramene_pas_l_ancre_en_tete():
    html = (
        f"<html><body><main><h1>Titre</h1><p>{CORPS}</p><ul><li><a href='/autre'><h2>Titre</h2></a>"
        "<h2>Voisin de liste</h2></li></ul></main></body></html>"
    )
    texte, n = lw.rattraper(html, f"Titre\n{CORPS}")
    assert n == 1 and texte.split("\n") == ["Titre", CORPS, "Voisin de liste"]


def test_une_adresse_de_courriel_ne_vaut_pas_le_nom_lu():
    html = (
        "<html><body><main><h1>SafeGPT</h1><h2>Gemeente Reimerswaal</h2>"
        f"<p>{CORPS}</p><p>Contact gemeente@reimerswaal.nl</p></main></body></html>"
    )
    texte, _ = lw.rattraper(html, f"SafeGPT\n{CORPS}\nContact gemeente@reimerswaal.nl")
    assert texte.split("\n")[:2] == ["SafeGPT", "Gemeente Reimerswaal"]

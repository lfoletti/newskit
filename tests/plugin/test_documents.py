"""La typographie des textes publiés du dépôt : en français, l'espace devant « : », « ; », « ! », « ? » et celles des
guillemets sont insécables, hors du code ; pas de virgule devant « et »."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ICI = Path(__file__).resolve().parents[2]
DOCUMENTS = ["README.fr.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "skills/newskit/SKILL.md"]
FAUTES = [
    (re.compile(r"[ \t ](?=[:;!?](?:\s|$))"), "espace ordinaire devant un signe double"),
    (re.compile(r"(?<=[^\s (\[|*_])(?=[:;!?](?:\s|$))"), "signe double sans espace insécable"),
    (re.compile(r"«(?! )"), "« sans espace insécable"),
    (re.compile(r"(?<! )»"), "» sans espace insécable"),
    (re.compile(r",\s+et\s"), "virgule devant « et »"),
]


def prose(texte: str) -> list[tuple[int, str]]:
    """Les lignes hors des blocs de code, sans le code en ligne, les liens et l'en-tête YAML."""
    out, bloc = [], False
    lignes = texte.split("\n")
    debut = lignes.index("---", 1) + 1 if lignes and lignes[0] == "---" else 0
    for n, ligne in enumerate(lignes[debut:], debut + 1):
        if ligne.lstrip().startswith("```"):
            bloc = not bloc
            continue
        if bloc or ligne.startswith("    ") or ligne.startswith("[") or ligne.startswith("|"):
            continue
        ligne = re.sub(r"`[^`]*`", "", ligne)
        ligne = re.sub(r"\]\([^)]*\)", "]", ligne)
        ligne = re.sub(r"https?://\S+", "", ligne)
        out.append((n, ligne))
    return out


@pytest.mark.parametrize("nom", DOCUMENTS)
def test_les_espaces_sont_insecables(nom):
    fautes = [
        f"{nom}:{n} {quoi} : {ligne.strip()[:80]}"
        for n, ligne in prose((ICI / nom).read_text(encoding="utf-8"))
        for motif, quoi in FAUTES
        if motif.search(ligne)
    ]
    assert fautes == []


def test_la_garde_voit_une_faute():
    vues = {q for _, l in prose("Un mot : la suite « citée », et la fin.") for m, q in FAUTES if m.search(l)}
    assert vues == {q for _, q in FAUTES} - {"signe double sans espace insécable"}

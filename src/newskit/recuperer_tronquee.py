#!/usr/bin/env python3
"""Reconstruit une fiche complète depuis une capture navigateur non tronquée.

Pourquoi ce script existe. Cinq fiches du corpus avaient été capturées par
l'outil de lecture de page du navigateur, qui plafonne sa sortie à 50 000
caractères. Les fiches se déclaraient complètes alors qu'il manquait jusqu'à la
moitié du texte. Le plafond se relève, et une sortie volumineuse est archivée
sur disque : c'est cette archive que ce script consomme.

Procédure côté navigateur, pour un article Substack payant :

    1. naviguer sur la page de l'article ;
    2. en JavaScript, `fetch('/api/v1/posts/<slug>', {credentials:'include'})`
       — la session abonné donne le `body_html` entier, là où l'API publique ne
       rend qu'un aperçu ;
    3. sérialiser les métadonnées et le HTML en un seul objet JSON, et le poser
       dans le DOM comme unique contenu ;
    4. lire la page avec une capacité relevée ; la sortie part sur disque.

Pour un article Medium, même principe en prenant `document.querySelector('article').innerHTML`
puisqu'il n'y a pas d'API.

Le POST vers un récepteur local ne fonctionne pas depuis Substack : sa politique
de sécurité de contenu interdit les requêtes vers l'hôte local.

Usage :
    python3 -m newskit.recuperer_tronquee <archive.json> [<archive.json> ...]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from newskit import fiche  # noqa: E402
from newskit.collecte_linkedin import html_to_md  # noqa: E402

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)


def charger_archive(chemin: Path) -> dict:
    """Extrait l'objet JSON déposé dans le DOM depuis une archive d'outil."""
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    texte = brut[0]["text"] if isinstance(brut, list) else brut.get("text", "")
    if "output truncated at" in texte:
        raise ValueError(f"{chemin.name} est encore tronquée : relever la capacité de lecture")
    corps = texte.split("\n---\n", 1)[1] if "\n---\n" in texte else texte
    debut = corps.find("{")
    if debut < 0:
        raise ValueError(f"{chemin.name} ne contient pas d'objet JSON")
    return json.loads(corps[debut:])


def localiser_fiche(url: str, slug: str) -> Path | None:
    """Retrouve la fiche existante à mettre à jour, par URL puis par slug."""
    for dossier in ("sources/substack", "sources/medium"):
        for f in (RACINE / dossier).rglob("*.md"):
            if f.name.startswith("_"):
                continue
            champs, _ = fiche.lire_fichier(f)
            if champs.get("url", "").rstrip("/") == url.rstrip("/"):
                return f
    candidats = [
        f for d in ("sources/substack", "sources/medium") for f in (RACINE / d).rglob("*.md") if slug and slug in f.name
    ]
    return candidats[0] if len(candidats) == 1 else None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        try:
            d = charger_archive(Path(arg))
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            print(f"  !! {e}")
            continue

        url = d.get("canonical_url") or d.get("url") or ""
        slug = d.get("slug", "")
        cible = localiser_fiche(url, slug)
        if cible is None:
            print(f"  !! fiche introuvable pour {slug or url}")
            continue

        corps_md = html_to_md(d["body_html"])
        titre = d.get("title") or ""
        sous = d.get("subtitle") or ""
        entete = f"# {titre}\n\n" + (f"*{sous}*\n\n" if sous else "")
        corps = entete + corps_md

        champs, _ = fiche.lire_fichier(cible)
        mots_avant = champs.get("words", 0)
        champs.update(
            {
                "title": titre or champs["title"],
                "subtitle": sous or champs.get("subtitle", ""),
                "author": ", ".join(d.get("bylines") or []) or champs.get("author", ""),
                "words": len(corps.split()),
                "complet": True,
                "wordcount_annonce": d.get("wordcount") or champs.get("wordcount_annonce", 0),
                "source": (champs.get("source", "") or "").replace(
                    "texte de page navigateur", "texte de page navigateur, capture complète"
                ),
            }
        )
        champs.pop("comment", None)  # le constat de troncature n'a plus lieu d'être
        fiche.ecrire(cible, champs, corps)
        gain = champs["words"] - mots_avant
        print(f"  {cible.relative_to(RACINE)}")
        print(f"     {mots_avant} → {champs['words']} mots (+{gain}), annoncé {champs['wordcount_annonce']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

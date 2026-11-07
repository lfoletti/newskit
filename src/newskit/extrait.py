"""L'extrait d'un fait de la couche 2 (07.11.2026, repris d'OntoSmith) : une citation de la fiche, vérifiée mot pour mot.

`extraire_concepts` écarte une arête ou un rationale dont l'extrait n'est pas dans la fiche ; `puits_cortexdb` rattache
une relation au morceau qui porte son extrait. Le texte cité ne part jamais au cerveau (droits, COUCHES.md).
"""

from __future__ import annotations

import re
import unicodedata

MOTS_EXTRAIT = 3  # un extrait plus court s'atteste par hasard (« AI Act »)


def _a_plat(texte: str) -> str:
    """Le texte comparé : formes Unicode unifiées, liens Markdown réduits à leur texte, apostrophes, guillemets
    et tirets ramenés à un seul signe, marques de mise en forme retirées, espaces fondus, casse ignorée."""
    t = unicodedata.normalize("NFKC", texte)
    t = re.sub(r"\[([^\]]*)\]\([^)\s]*\)", r"\1", t)  # un lien Markdown : son texte, sans l'adresse
    t = re.sub(r"[\u2018\u2019\u02bc\u0060\u00b4]", "'", t)
    t = re.sub(r"[\u201c\u201d\u00ab\u00bb\u201e]", '"', t)
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)
    t = re.sub(r"[*_]", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def atteste(extrait: str | None, corps: str) -> bool:
    """L'extrait figure-t-il mot pour mot dans le texte donné au modèle (07.11.2026, repris d'OntoSmith) ?
    Le modèle qui extrait ne décide pas seul : un fait sans extrait retrouvé n'entre pas au graphe."""
    if not extrait or len(str(extrait).split()) < MOTS_EXTRAIT:
        return False
    return _a_plat(str(extrait)) in _a_plat(corps)

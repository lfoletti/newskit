#!/usr/bin/env python3
"""Structure des titres des fiches denses : la passe déterministe de graphify.

Le graphe composé lit trois entrées : le squelette (couche 1), les lots
sémantiques (couche 2, `graphe/couche2/`) et la structure des titres, que
graphify produit sans modèle dans `graphify-out/.graphify_ast.json`. Les deux
premières sont versionnées ou rejouées par la routine ; la troisième ne l'était
pas, et ne se rejouait qu'à la main. Le 27.09.2026, sur un clone neuf, le
graphe s'est composé sans elle : 1 500 nœuds `document` et autant d'arêtes
`contains` en moins, et aucune étape ne l'avait dit.

Ce script la rejoue, sur les fiches des dossiers `dense` du registre — le
périmètre de la couche 2, donc le même que celui de la structure d'origine.
C'est déterministe et cela tient en quelques secondes grâce au cache de
graphify (`cache/`, hors dépôt). La routine l'appelle avant de composer.

Usage :
    python3 -m newskit.extraire_structure
    python3 -m newskit.extraire_structure --graphe <graphe>   # un autre graphe, sous graphe/<nom>/structure.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
SORTIE = RACINE / "graphify-out" / ".graphify_ast.json"

from newskit import config_sources as cfg  # noqa: E402

# graphify retire de ses identifiants le préfixe de la racine courante ; un identifiant qui commence encore par le premier
# dossier d'un chemin absolu (`users_…` sur ce Mac) vient d'une autre racine : le 23.10.2026, le cache gardait celle
# d'avant le renommage du dépôt, et 19 828 nœuds sur 20 137 d'un graphe de conformité ne se recollaient plus à leurs fiches.
PREFIXE_ABSOLU = RACINE.parts[1].lower() + "_"
PART_ABSOLUE_MAX = 0.05


def identifiants_absolus(resultat: dict, prefixe: str = PREFIXE_ABSOLU) -> int:
    return sum(1 for n in resultat.get("nodes", []) if str(n.get("id", "")).startswith(prefixe))


def structure_acceptable(resultat: dict, prefixe: str = PREFIXE_ABSOLU) -> bool:
    return identifiants_absolus(resultat, prefixe) <= PART_ABSOLUE_MAX * len(resultat.get("nodes", []))


def sans_aretes_pendantes(resultat: dict) -> tuple[dict, int]:
    """Sans les arêtes dont un bout n'est pas un nœud : un lien d'une fiche vers une page qui n'en est pas une
    (`projects_md`, un chemin absolu du Mac) restait une arête `references` pendante, erreur de schéma à la composition
    (GV7, 02.11.2026 : 2 958 dans un graphe d'éthique, 4 411 dans un graphe de sécurité)."""
    ids = {n.get("id") for n in resultat.get("nodes", [])}
    gardees = [e for e in resultat.get("edges", []) if e.get("source") in ids and e.get("target") in ids]
    return {**resultat, "edges": gardees}, len(resultat.get("edges", [])) - len(gardees)


def main() -> int:
    global SORTIE
    graphe = sys.argv[sys.argv.index("--graphe") + 1] if "--graphe" in sys.argv else None
    if graphe:
        SORTIE = cfg.chemins_du_graphe(graphe)["structure"]
    try:
        from graphify.extract import extract
    except ImportError as e:
        print(f"  graphify introuvable dans {sys.executable} : {e}", file=sys.stderr)
        return 1
    fichiers = sorted(p for d in cfg.dossiers_denses(graphe)
                      for p in (RACINE / d).rglob("*.md") if not p.name.startswith("_"))
    if not fichiers:
        print("  aucune fiche dense : le registre ne déclare aucun dossier `dense`", file=sys.stderr)
        return 1
    resultat = extract(fichiers, cache_root=RACINE)
    if not resultat.get("nodes"):
        # Un résultat vide n'est pas une structure : c'est une extraction qui
        # n'a pas eu lieu, et l'écrire ferait composer un graphe amputé en silence.
        print(f"  extraction vide sur {len(fichiers)} fiches : rien n'est écrit", file=sys.stderr)
        return 1
    if not structure_acceptable(resultat):
        print(f"  {identifiants_absolus(resultat)} identifiants sur {len(resultat['nodes'])} portent un chemin absolu "
              f"({PREFIXE_ABSOLU}…) : le cache de graphify vient d'une autre racine ; rien n'est écrit", file=sys.stderr)
        return 1
    resultat, pendantes = sans_aretes_pendantes(resultat)
    if pendantes:
        print(f"  {pendantes} arête(s) vers un lien qui n'est pas une fiche, retirée(s)")
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(resultat, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(fichiers)} fiches denses : {len(resultat['nodes'])} nœuds, "
          f"{len(resultat['edges'])} arêtes → {SORTIE.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

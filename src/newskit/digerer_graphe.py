#!/usr/bin/env python3
"""Produit la carte du domaine : ce qu'un modèle doit savoir avant de creuser.

Le graphe pèse plus de vingt mégaoctets et ne peut pas entrer dans un contexte.
Mais on n'a pas besoin du graphe pour s'orienter dedans : on a besoin d'une
carte. Ce script la calcule et l'écrit en quelques kilo-octets.

La carte donne le vocabulaire, les notions centrales, les communautés du graphe
avec leurs habitants, les auteurs et les papiers qui font autorité dans ce
corpus, et les rapprochements que la couche conceptuelle a établis. Elle se lit
en deux minutes et se charge intégralement. C'est elle qui rend le graphe
utilisable comme une mémoire plutôt que comme une base à requêter à l'aveugle.

Une note sur la reproductibilité. La détection de communautés de graphify est
déterministe à l'intérieur d'un processus, mais pas d'un processus à l'autre :
elle dépend quelque part de l'ordre d'itération d'un ensemble, donc de la
randomisation du hachage des chaînes. Sur un graphe strictement identique, deux
régénérations donnaient deux découpages différents — 128 communautés puis 141 —
et la carte hebdomadaire montrait ainsi un domaine qui bouge alors que rien
n'avait changé. Le script se relance donc avec `PYTHONHASHSEED=0`, ce qui rend
la carte reproductible et le diff hebdomadaire lisible : ce qui y bouge a bougé
dans le corpus.

Sortie : graphe/carte.md

Usage :
    python3 -m newskit.digerer_graphe
    python3 -m newskit.digerer_graphe --communautes 14
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
GRAPHE = RACINE / "graphe" / "graphe.json"
SORTIE = RACINE / "graphe" / "carte.md"

TYPES_CONCEPT = ("concept", "rationale")


def _du_modele(G, n) -> bool:
    """Nœud issu d'un complément écrit par un modèle, sans source externe."""
    d = G.nodes[n]
    return d.get("origine") == "modele" or "sources/modele/" in str(d.get("source_file") or "")


def _label(G, n) -> str:
    """Étiquette, suffixée quand l'autorité du nœud n'est pas celle du corpus.

    La carte est chargée comme une référence : une notion qui n'est affirmée que
    par un modèle doit être lisible comme telle à l'endroit où elle est lue, et
    pas seulement dans le graphe.
    """
    return str(G.nodes[n].get("label") or n) + (" ·modèle" if _du_modele(G, n) else "")


def _type(G, n) -> str:
    d = G.nodes[n]
    return str(d.get("kind") or d.get("file_type") or "?")


def _tableau(entetes: list[str], lignes: list[list[str]]) -> list[str]:
    return (["| " + " | ".join(entetes) + " |", "|" + "---|" * len(entetes)]
            + ["| " + " | ".join(str(c) for c in l) + " |" for l in lignes] + [""])


def communautes(G, combien: int) -> list[tuple[str, list[str], float]]:
    """Communautés du graphe, étiquetées par leur nœud le plus central.

    On restreint au sous-graphe des concepts : sur le graphe entier, les 17 000
    auteurs et les 2 000 papiers dominent la structure et les communautés
    obtenues décrivent la bibliographie, pas le domaine.
    """
    concepts = [n for n in G if _type(G, n) in TYPES_CONCEPT]
    H = G.subgraph(concepts).copy()
    H.remove_nodes_from([n for n in list(H) if H.degree(n) == 0])
    if H.number_of_nodes() < 4:
        return []
    try:
        from graphify.cluster import cluster, label_communities_by_hub, score_all
        comms = cluster(H)
        etiquettes = label_communities_by_hub(H, comms)
        scores = score_all(H, comms)
    except Exception:                                  # repli sans graphify
        comms = {i: list(c) for i, c in enumerate(nx.community.greedy_modularity_communities(H))}
        etiquettes = {i: _label(H, max(c, key=lambda n: H.degree(n))) for i, c in comms.items()}
        scores = {i: 0.0 for i in comms}
    sortie = []
    for i, membres in comms.items():
        if len(membres) < 3:
            continue
        tri = sorted(membres, key=lambda n: -H.degree(n))
        sortie.append((str(etiquettes.get(i, _label(H, tri[0]))), tri, float(scores.get(i, 0.0))))
    sortie.sort(key=lambda t: -len(t[1]))
    return sortie[:combien]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--communautes", type=int, default=12)
    ap.add_argument("--graphe", help="nom d'un graphe du registre, ou chemin d'un graphe.json")
    ap.add_argument("--sortie", type=Path, help="où écrire la carte (défaut : à côté du graphe)")
    args = ap.parse_args()

    global SORTIE
    p = GRAPHE
    if args.graphe:
        if args.graphe.endswith(".json") or "/" in args.graphe:
            p = Path(args.graphe)
            SORTIE = p.parent / "carte.md"
        else:
            pass
            from newskit import config_sources as cfg  # noqa: PLC0415
            chemins = cfg.chemins_du_graphe(args.graphe)
            p, SORTIE = chemins["graphe"], chemins["carte"]
    if args.sortie:
        SORTIE = args.sortie
    if not p.exists():
        print(f"  Graphe absent : {p}", file=sys.stderr)
        return 1
    G = json_graph.node_link_graph(json.loads(p.read_text(encoding="utf-8")), edges="links")

    types = Counter(_type(G, n) for n in G)
    rels = Counter(d.get("relation", "?") for _, _, d in G.edges(data=True))
    confs = Counter(d.get("confidence", "?") for _, _, d in G.edges(data=True))

    L: list[str] = [
        "# Carte du domaine" + (f" — {args.graphe}" if args.graphe and "/" not in args.graphe
                                 and not args.graphe.endswith(".json") else ""),
        "",
        "*Produite par `newskit.digerer_graphe`. Ce document est fait pour être chargé "
        "intégralement : il oriente dans le graphe, il ne le remplace pas. "
        "Pour creuser, `newskit.interroger_graphe`.*",
        "",
        (f"**{G.number_of_nodes() - types['auteur']} nœuds hors auteurs, {types['auteur']} auteurs, "
         f"{G.number_of_edges()} arêtes.** " if types.get("auteur") else
         f"**{G.number_of_nodes()} nœuds, {G.number_of_edges()} arêtes.** ") +
        "Confiance : " + ", ".join(f"{n} {k.lower()}" for k, n in confs.most_common()) + ".",
        "",
        (lambda nm, am: (
            f"*{nm} nœuds et {am} arêtes viennent de la source « Modèle » : des compléments "
            f"écrits par un modèle, sans source externe, là où le corpus a une lacune "
            f"constatée. Ils sont marqués `·modèle` partout dans cette carte, et leurs "
            f"relations sont en INFERRED ou AMBIGUOUS. "
            f"`interroger_graphe.py --sans-modele` les écarte.*")
         if nm else "")(
            sum(1 for n in G if _du_modele(G, n)),
            sum(1 for _u, _v, d in G.edges(data=True) if d.get("origine") == "modele")),
        "",
        "## Ce que le graphe contient",
        "",
    ]
    L += _tableau(["type de nœud", "nombre", "ce que c'est"], [
        [t, n, {
            "auteur": "personnes signataires, newsletters et papiers confondus",
            "papier": "papiers arXiv, avec titre, auteurs, résumé et catégories",
            "article": "fiches du corpus : éditions, billets, articles",
            "rationale": "principes, mécanismes, motifs de conception",
            "concept": "notions, modèles, entreprises, normes",
            "depot": "dépôts GitHub cités",
            "externe": "ressources hors corpus citées par au moins deux fiches",
            "document": "sections de fiche, issues de la structure des titres",
            "entite": "institutions et personnes du who-is-who suisse",
            "publication": "publications d'origine",
            "code": "outils et classes nommés dans le texte",
            "paper": "papiers nommés par la couche conceptuelle",
        }.get(t, "")] for t, n in types.most_common()])

    L += ["## Le vocabulaire des relations", ""]
    L += _tableau(["relation", "nombre", "sens"], [
        [r, n, {
            "ecrit_par": "une fiche ou un papier vers ses auteurs",
            "cite_papier": "une fiche vers un papier arXiv",
            "publie_dans": "une fiche vers sa publication",
            "cite_article": "une fiche vers une autre fiche du corpus",
            "cite_externe": "une fiche vers une ressource externe adoptée",
            "cite_depot": "une fiche vers un dépôt GitHub",
            "contains": "une fiche vers ses sections",
            "references": "mention explicite, couche conceptuelle",
            "implements": "une notion met en œuvre une autre",
            "conceptually_related_to": "parenté conceptuelle établie par lecture",
            "semantically_similar_to": "rapprochement inféré, avec un score",
            "rationale_for": "une notion justifie une autre",
            "cites": "citation académique",
            "shares_data_with": "deux notions partagent une structure",
        }.get(r, "relation issue du who-is-who")] for r, n in rels.most_common(18)])

    for titre, t, n in [("Notions les plus centrales", TYPES_CONCEPT, 30),
                        ("Papiers les plus cités", ("papier",), 15),
                        ("Dépôts les plus cités", ("depot",), 15),
                        ("Auteurs les plus présents", ("auteur",), 12)]:
        noeuds = [x for x in G if _type(G, x) in t]
        noeuds.sort(key=lambda x: -G.degree(x))
        L += [f"## {titre}", ""]
        L += _tableau(["nom", "liens"], [[_label(G, x)[:78], G.degree(x)] for x in noeuds[:n]])

    comms = communautes(G, args.communautes)
    if comms:
        L += ["## Communautés du graphe conceptuel", "",
              "*Détectées sur le seul sous-graphe des notions : sur le graphe entier, "
              "les auteurs et les papiers dominent la structure et décriraient la "
              "bibliographie plutôt que le domaine.*", ""]
        for etiquette, membres, score in comms:
            tete = ", ".join(_label(G, m) for m in membres[:12])
            L += [f"**{etiquette}** — {len(membres)} notions"
                  + (f", cohésion {score:.2f}" if score else "") + f"  \n{tete}", ""]

    ponts = [(d.get("confidence_score", 0), u, v) for u, v, d in G.edges(data=True)
             if d.get("relation") == "semantically_similar_to"]
    ponts.sort(reverse=True)
    if ponts:
        L += ["## Rapprochements inférés les plus forts", "",
              "*Établis par la couche conceptuelle entre notions sans lien structurel. "
              "Ce sont des hypothèses, avec leur score.*", ""]
        L += _tableau(["notion", "notion", "score"],
                      [[_label(G, u)[:44], _label(G, v)[:44], f"{s:.2f}"] for s, u, v in ponts[:18]])

    L += [
        "## Creuser", "",
        "```bash",
        "python3 -m newskit.interroger_graphe chercher   \"agent memory\"",
        "python3 -m newskit.interroger_graphe expliquer  \"event sourcing\"",
        "python3 -m newskit.interroger_graphe voisinage  concept_knowledge_graph",
        "python3 -m newskit.interroger_graphe chemin     \"ontologie\" \"audit trail\"",
        "python3 -m newskit.interroger_graphe sources    \"bi-temporal\"",
        "python3 -m newskit.interroger_graphe fiche      audit-auditor",
        "python3 -m newskit.interroger_graphe sommets    --type papier",
        "```", "",
        "Chaque réponse porte sa provenance, fichier et ligne, et aucune n'appelle de modèle.", "",
    ]

    SORTIE.write_text("\n".join(L), encoding="utf-8")
    taille = SORTIE.stat().st_size
    print(f"  écrit : {SORTIE.relative_to(RACINE)}  ({taille // 1024} Ko, {len(comms)} communautés)")
    return 0


if __name__ == "__main__":
    # Voir la note sur la reproductibilité en tête de fichier. Fixer la graine
    # de hachage demande de relancer l'interpréteur : la variable est lue au
    # démarrage et ne peut pas être posée après coup.
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.execve(sys.executable, [sys.executable, *sys.argv],
                  {**os.environ, "PYTHONHASHSEED": "0"})
    sys.exit(main())

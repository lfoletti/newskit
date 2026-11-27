# Adapté de graphify 0.9.20 (graphify/cluster.py), sous licence MIT :
#
# Copyright (c) 2026 Safi Shamsi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""Les communautés de la carte du domaine (`digerer_graphe`), comme les découpait graphify 0.9.20 sans graspologic.

Louvain de networkx (graine 42) sur une copie du graphe aux nœuds et arêtes triés ; une communauté qui dépasse le
quart du graphe (10 nœuds au moins), ou de 50 nœuds et plus dont la cohésion est sous 0,05, est redécoupée ; les
communautés sont numérotées par taille décroissante, puis par leurs membres. Chacune prend le nom de son nœud de plus
haut degré. Le graphe reçu est non orienté, sans nœud isolé, avec au moins une arête (celui des notions de la carte).
"""

from __future__ import annotations

import json

import networkx as nx

PART_MAX = 0.25
TAILLE_MIN_DECOUPE = 10
COHESION_MIN = 0.05
TAILLE_MIN_COHESION = 50


def _partition(G: nx.Graph) -> dict:
    stable = nx.Graph()
    stable.add_nodes_from(sorted(G.nodes(), key=str))
    for u, v, d in sorted(
        G.edges(data=True),
        key=lambda r: (str(r[0]), str(r[1]), json.dumps(r[2], sort_keys=True, ensure_ascii=False, default=str)),
    ):
        stable.add_edge(u, v, **d)
    comms = nx.community.louvain_communities(stable, seed=42, threshold=1e-4, resolution=1.0, max_level=10)
    return {n: i for i, c in enumerate(comms) for n in c}


def scinder(G: nx.Graph, noeuds: list) -> list[list]:
    """Une communauté redécoupée par un second passage ; sans arête, chaque nœud seul."""
    sous = G.subgraph(noeuds)
    if sous.number_of_edges() == 0:
        return [[n] for n in sorted(noeuds)]
    parts: dict[int, list] = {}
    for n, c in _partition(sous).items():
        parts.setdefault(c, []).append(n)
    if len(parts) <= 1:
        return [sorted(noeuds)]
    return [sorted(v) for v in parts.values()]


def cohesion(G: nx.Graph, noeuds: list) -> float:
    """Arêtes internes sur arêtes possibles."""
    n = len(noeuds)
    if n <= 1:
        return 1.0
    return G.subgraph(noeuds).number_of_edges() / (n * (n - 1) / 2)


def decouper(G: nx.Graph) -> dict[int, list]:
    """{numéro: membres triés}, la plus grande communauté d'abord."""
    brut: dict[int, list] = {}
    for n, c in _partition(G).items():
        brut.setdefault(c, []).append(n)
    taille_max = max(TAILLE_MIN_DECOUPE, int(G.number_of_nodes() * PART_MAX))
    finales: list[list] = []
    for noeuds in brut.values():
        finales.extend(scinder(G, noeuds) if len(noeuds) > taille_max else [noeuds])
    secondes: list[list] = []
    for noeuds in finales:
        if len(noeuds) >= TAILLE_MIN_COHESION and cohesion(G, noeuds) < COHESION_MIN:
            parts = scinder(G, noeuds)
            secondes.extend(parts if len(parts) > 1 else [noeuds])
        else:
            secondes.append(noeuds)
    secondes.sort(key=lambda ns: (-len(ns), tuple(sorted(map(str, ns)))))
    return {i: sorted(ns) for i, ns in enumerate(secondes)}


def etiqueter(G: nx.Graph, comms: dict[int, list]) -> dict[int, str]:
    """Chaque communauté au nom de son nœud de plus haut degré (identifiant croissant pour départager), sans « () »."""
    noms: dict[int, str] = {}
    for c, membres in comms.items():
        presents = [n for n in membres if n in G]
        if not presents:
            noms[c] = f"Community {c}"
            continue
        pivot = min(presents, key=lambda n: (-G.degree(n), str(n)))
        nom = str(G.nodes[pivot].get("name") or pivot).strip()
        if nom.endswith("()"):
            nom = nom[:-2]
        noms[c] = nom or f"Community {c}"
    return noms


def cohesions(G: nx.Graph, comms: dict[int, list]) -> dict[int, float]:
    return {c: cohesion(G, noeuds) for c, noeuds in comms.items()}

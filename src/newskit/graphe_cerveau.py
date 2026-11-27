"""Le graphe d'un graphe actif, lu dans son cerveau (K3, mainteneur, 27.11.2026 : « un seul système », « voie 2 »).

Le cerveau CortexDB est le graphe : ni `graphe.json` ni composition. `charger(g)` rend le graphe en mémoire (networkx),
pour les calculs qui le lisent en entier (communautés, chemins, degrés), sous les noms du cerveau, sans traduction :
un nœud est une entité, nommée par son identifiant (`entity:…`), avec `node_type` et ses propriétés telles que le
cerveau les garde (`name`, `_source`, `description`, `newskit_id`, `aliases`…) ; une arête porte `edge_type`, `weight`,
ses deux bouts dans le sens du cerveau (`from_node_id`, `to_node_id`) et ses propriétés.

Les documents des fiches et leurs morceaux (`doc:…`, `chunk:…`), les liens de texte (`has_chunk`, `mentions`, `next`)
et les faits retirés ou clos restent hors du graphe : ils sont le texte, pas le graphe. Une entité typée `document` par
une extraction reste une entité.

La lecture ouvre le fichier du cerveau en lecture seule, sans passer par le serveur (mainteneur, 27.11.2026 : « oui,
voie 1 »). Mesuré le 27.11.2026 sur une copie du cerveau servi d'un graphe de gouvernance (164 489 entités, 400 377
arêtes du domaine) : 12 s par SQLite, 1 061 s par `graph_list_all`, dont les pages de 5 000 nœuds dépassent la limite
de 4 Mo de gRPC. Le prix : la lecture dépend des tables internes de CortexDB. `SCHEMA_CONNU` les fixe ; un schéma
différent est refusé (`SchemaInconnu`), pour qu'une montée de CortexDB échoue en clair plutôt que de rendre un graphe
faux.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from newskit import cerveau_cortexdb as cc

# Les deux tables lues, telles que CortexDB 2.160.0 les crée.
SCHEMA_CONNU = {
    "graph_nodes": "CREATE TABLE graph_nodes (id TEXT PRIMARY KEY, vector BLOB NOT NULL, content TEXT, node_type TEXT, "
    "properties TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "valid_from TIMESTAMP, valid_to TIMESTAMP, recorded_at TIMESTAMP, retracted_at TIMESTAMP)",
    "graph_edges": "CREATE TABLE graph_edges (id TEXT PRIMARY KEY, from_node_id TEXT NOT NULL, to_node_id TEXT NOT NULL, "
    "edge_type TEXT, weight REAL DEFAULT 1.0, properties TEXT, vector BLOB, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "valid_from TIMESTAMP, valid_to TIMESTAMP, recorded_at TIMESTAMP, retracted_at TIMESTAMP)",
}
TEXTE = ("doc:", "chunk:")  # les documents des fiches et leurs morceaux
LIENS_DE_TEXTE = ("has_chunk", "mentions", "next")
FICHES = ("article", "papier")  # les entités d'une fiche, qui reçoivent l'en-tête de son document


class CerveauAbsent(Exception):
    """Le cerveau du graphe manque : jamais un graphe vide à sa place."""


class SchemaInconnu(Exception):
    """Les tables du cerveau ne sont pas celles que la lecture connaît."""


def _colonnes(sql: str) -> list[str]:
    corps = sql[sql.index("(") + 1 : sql.rindex(")")]
    return [c.split()[0] for c in corps.split(",") if c.split()]


COLONNES = {t: _colonnes(sql) for t, sql in SCHEMA_CONNU.items()}


def _ouvrir(chemin: Path) -> sqlite3.Connection:
    if not chemin.exists():
        raise CerveauAbsent(f"cerveau absent : {chemin}")
    return sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)


def _verifier(c: sqlite3.Connection, chemin: Path) -> None:
    for table, attendues in COLONNES.items():
        lues = [r[1] for r in c.execute(f"pragma table_info({table})")]
        if lues != attendues:
            raise SchemaInconnu(
                f"{chemin.name} : {table} a pour colonnes {lues or 'rien'}, la lecture connaît {attendues}"
            )


def lire(chemin: Path) -> nx.Graph:
    """Le graphe du cerveau `chemin`, sous ses noms. Une fiche reçoit l'en-tête de son document (`date`,
    `plateforme`, `statut`, `version`…, `puits_cortexdb.ENTETE_VERSEE`), que le versement range dans le document ;
    ce que l'entité porte déjà l'emporte."""
    from newskit.puits_cortexdb import ENTETE_VERSEE  # noqa: PLC0415

    G = nx.Graph()
    en_tetes: dict[str, dict] = {}
    with _ouvrir(chemin) as c:
        _verifier(c, chemin)
        noeuds = c.execute(
            "select id, node_type, properties from graph_nodes where retracted_at is null and valid_to is null"
        )
        for i, ty, props in noeuds:
            d = json.loads(props or "{}")
            if i.startswith("doc:"):
                en_tetes[str(d.get("_source") or "")] = {k: d[k] for k in ENTETE_VERSEE if k in d}
            elif not i.startswith(TEXTE):
                G.add_node(i, **{**d, "node_type": ty})  # la colonne l'emporte
        aretes = c.execute(
            "select from_node_id, to_node_id, edge_type, weight, properties from graph_edges where edge_type not in "
            "(?, ?, ?) and retracted_at is null and valid_to is null order by id",
            LIENS_DE_TEXTE,
        )
        for de, a, ty, poids, props in aretes:
            if de in G and a in G:
                # les colonnes l'emportent : le versement répète le poids en propriété, en texte
                bouts = {"edge_type": ty, "weight": poids, "from_node_id": de, "to_node_id": a}
                G.add_edge(de, a, **{**json.loads(props or "{}"), **bouts})
    for n, d in G.nodes(data=True):
        if d["node_type"] in FICHES:
            for k, v in en_tetes.get(str(d.get("_source") or ""), {}).items():
                d.setdefault(k, v)
    return G


def _par_cle(G: nx.Graph) -> dict[str, str]:
    """Clé d'une table → entité : son identifiant au cerveau, ou l'ancien (`newskit_id`), sous lequel la composition
    écrit encore ses tables jusqu'à son retrait (K3, étape 3)."""
    return {**{d["newskit_id"]: n for n, d in G.nodes(data=True) if d.get("newskit_id")}, **{n: n for n in G}}


def appliquer_anglais(G: nx.Graph, anglais: dict[str, str]) -> int:
    """Le nom anglais d'une notion au nom étranger (`alias.json`, table `anglais`, fondre_doublons.traduire) ;
    l'original va sous `aussi`, pour que la recherche le retrouve. Rend le nombre de noms changés."""
    cles, changes = _par_cle(G), 0
    for cle, en in anglais.items():
        n = cles.get(cle)
        if n is None or not en or G.nodes[n].get("name") == en:
            continue
        d = G.nodes[n]
        aussi = [x for x in [*(d.get("aussi") or []), d.get("name")] if x and x != en]
        d["aussi"], d["name"] = list(dict.fromkeys(aussi)), en
        changes += 1
    return changes


def appliquer_francais(G: nx.Graph, table: dict) -> int:
    """`name_fr` et `description_fr` de la table française (francais.py), posés quand la traduction est celle de
    l'original actuel (même empreinte). Rend le nombre de champs posés."""
    from newskit import francais  # noqa: PLC0415

    cles, k = _par_cle(G), 0
    for cle_table, champ, dest in (("libelles", "name", "name_fr"), ("textes", "description", "description_fr")):
        for cle, v in (table.get(cle_table) or {}).items():
            n = cles.get(cle)
            d = G.nodes[n] if n is not None else {}
            if v and d.get(champ) and v[1] == francais.empreinte(d[champ]):
                d[dest] = v[0]
                k += 1
    return k


def annoter(G: nx.Graph, graphe: str) -> nx.Graph:
    """Ce que la composition posait sur le graphe : les noms anglais des tables d'alias du graphe et de ses graphes
    inclus, le français du graphe, puis l'actualité des notions (diachronie)."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import diachronie  # noqa: PLC0415

    for g in [graphe, *cfg.graphes_inclus(graphe)]:
        p = Path(cfg.chemins_du_graphe(g)["base"]) / "alias.json"
        if p.exists():
            appliquer_anglais(G, json.loads(p.read_text(encoding="utf-8")).get("anglais") or {})
    p = Path(cfg.chemins_du_graphe(graphe)["base"]) / "francais.json"
    if p.exists():
        appliquer_francais(G, json.loads(p.read_text(encoding="utf-8")))
    diachronie.annoter_graphe(G)
    return G


def charger(graphe: str) -> nx.Graph:
    """Le graphe du graphe `graphe`, lu dans son cerveau et annoté (`annoter`)."""
    return annoter(lire(cc.chemin_cerveau(graphe)), graphe)

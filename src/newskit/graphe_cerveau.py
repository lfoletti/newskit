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
TITRES = ("article", "externe", "auteur", "papier", "publication", "depot")  # un reste de page y reste : c'est un titre
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


def _deterministe(e: dict) -> bool:
    """Une relation posée par un producteur déterministe (couche 1, structure des titres, vues), pas par le modèle."""
    return "déterministe" in str(e.get("_producer") or "")


OEUVRES = {"article", "papier", "depot", "document"}  # le bout d'un `ecrit_par` qui n'est pas l'auteur
SIGNATAIRES = {"auteur", "publication"}  # les genres de la couche 1 qui signent une œuvre
TEXTES_DE_NOTION = ("description", "name_fr", "description_fr", "_source")


def _jumeau(G: nx.Graph, n: str, genre: str, prefixe: str, aretes: list, textes=("_source",)) -> None:
    d = G.nodes[n]
    j = f"{n}#{'auteur' if genre == 'auteur' else 'notion'}"
    cle = str(d["newskit_id"]).split("_", 1)[-1] if d.get("newskit_id") else n.split(":", 1)[-1]
    G.add_node(
        j, name=d.get("name"), node_type=genre, newskit_id=f"{prefixe}_{cle}", **{k: d[k] for k in textes if k in d}
    )
    for v, e in aretes:
        if not G.has_edge(n, v):
            continue  # entre deux signataires, la relation est partie au jumeau du premier
        G.remove_edge(n, v)
        G.add_edge(j, v, **{**e, **{k: j for k in ("from_node_id", "to_node_id") if e.get(k) == n}})


def separer_homonymes(G: nx.Graph, cedees: list[tuple[str, str, dict]] = ()) -> None:
    """Le cerveau nomme une entité par son nom : l'auteur et la notion du même nom sont un seul nœud, du genre du dernier
    producteur versé ; l'ancien graphe en gardait deux (`auteur_x`, `concept_x`). Mesuré sur ai-governance le
    27.11.2026 : 640 auteurs et 95 notions perdus à l'index du site. Une notion qui signe une œuvre par un `ecrit_par`
    de la couche 1 reçoit un jumeau auteur, qui reprend ces liens ; un auteur ou une publication que le modèle relie
    reçoit un jumeau notion, qui reprend ces relations. Une relation du modèle que la couche 1 avait écartée sur la même
    paire (`cedees`) revient à la notion, une fois la paire libre. Une notion rangée comme raisonnement ne se distingue pas ainsi
    (les vrais raisonnements portent les mêmes liens) : elle reste au versement."""
    signe: dict[str, list] = {}
    relie: dict[str, list] = {}
    for a, b, e in G.edges(data=True):
        ta, tb = G.nodes[a].get("node_type"), G.nodes[b].get("node_type")
        if e.get("edge_type") == "ecrit_par" and _deterministe(e) and (ta in OEUVRES) != (tb in OEUVRES):
            auteur, oeuvre = (b, a) if ta in OEUVRES else (a, b)
            if G.nodes[auteur].get("node_type") not in SIGNATAIRES:
                signe.setdefault(auteur, []).append((oeuvre, e))
        elif e.get("edge_type") != "ecrit_par" and e.get("_producer") and not _deterministe(e):  # du modèle
            for x, y in ((a, b), (b, a)):
                if G.nodes[x].get("node_type") in SIGNATAIRES:
                    relie.setdefault(x, []).append((y, e))
    for a, b, e in cedees:
        for x in (a, b) if e.get("edge_type") != "ecrit_par" and e.get("_producer") else ():
            if G.nodes[x].get("node_type") in SIGNATAIRES:
                relie.setdefault(x, [])
    for n, aretes in signe.items():
        _jumeau(G, n, "auteur", "auteur", aretes)
    for n, aretes in relie.items():
        _jumeau(G, n, "concept", "concept", aretes, TEXTES_DE_NOTION)
    for a, b, e in cedees:
        x, y = (f"{n}#notion" if n in relie else n for n in (a, b))
        if not G.has_edge(x, y):
            G.add_edge(x, y, **{**e, "from_node_id": x, "to_node_id": y})


def lire(chemin: Path) -> nx.Graph:
    """Le graphe du cerveau `chemin`, sous ses noms. Une fiche reçoit l'en-tête de son document (`date`,
    `plateforme`, `statut`, `version`…, `puits_cortexdb.ENTETE_VERSEE`), que le versement range dans le document ;
    ce que l'entité porte déjà l'emporte. Une notion dont le nom est un reste de navigation (« Share »…) reste dehors."""
    from newskit.partage_graphe import reste_de_page  # noqa: PLC0415 — la composition le retirait (K3)
    from newskit.puits_cortexdb import ENTETE_VERSEE  # noqa: PLC0415

    G = nx.Graph()
    en_tetes: dict[str, dict] = {}
    cedees: list[tuple[str, str, dict]] = []
    with _ouvrir(chemin) as c:
        _verifier(c, chemin)
        noeuds = c.execute(
            "select id, node_type, properties from graph_nodes where retracted_at is null and valid_to is null"
        )
        for i, ty, props in noeuds:
            d = json.loads(props or "{}")
            if i.startswith("doc:"):
                en_tetes[str(d.get("_source") or "")] = {k: d[k] for k in ENTETE_VERSEE if k in d}
            elif not i.startswith(TEXTE) and not (ty not in TITRES and reste_de_page(str(d.get("name") or ""))):
                G.add_node(i, **{**d, "node_type": ty})  # la colonne l'emporte
        aretes = c.execute(
            "select from_node_id, to_node_id, edge_type, weight, properties from graph_edges where edge_type not in "
            "(?, ?, ?) and retracted_at is null and valid_to is null order by id",
            LIENS_DE_TEXTE,
        )
        for de, a, ty, poids, props in aretes:
            if de in G and a in G:
                # les colonnes l'emportent : le versement répète le poids en propriété, en texte
                e = {**json.loads(props or "{}"), "edge_type": ty, "weight": poids, "from_node_id": de, "to_node_id": a}
                if G.has_edge(de, a) and _deterministe(G.edges[de, a]) and not _deterministe(e):
                    cedees.append((de, a, e))  # une relation du modèle ne remplace pas la couche 1 (1 137 `ecrit_par`)
                    continue
                G.add_edge(de, a, **e)
    for n, d in G.nodes(data=True):
        # Une entité attestée encore par d'autres fiches garde la première pour source, même retirée : elle prend la
        # fiche d'une relation qui l'atteste encore (témoin, anticorps des débris, 27.11.2026).
        if (src := str(d.get("_source") or "")) and src not in en_tetes:
            vivantes = sorted(f for *_, e in G.edges(n, data=True) if (f := str(e.get("_source") or "")) in en_tetes)
            if vivantes:
                d["_source"] = vivantes[0]
        if d["node_type"] in FICHES:
            for k, v in en_tetes.get(str(d.get("_source") or ""), {}).items():
                d.setdefault(k, v)
    separer_homonymes(G, cedees)
    fondre_par_identifiant(G)
    return G


def fondre_par_identifiant(G: nx.Graph) -> int:
    """Fond en une entité celles qui portent le même ancien identifiant (`newskit_id`) : deux lots donnent parfois le
    même identifiant sous deux libellés, que la composition fondait et que le cerveau garde à part. La plus reliée
    (à égalité, le premier identifiant) garde son nom et ce qu'elle porte ; les autres noms vont sous `aliases`, ce
    qu'elle n'a pas lui vient des autres, leurs relations la rejoignent. Rend le nombre d'entités fondues."""
    groupes: dict[str, list[str]] = {}
    for n, d in G.nodes(data=True):
        if d.get("newskit_id"):
            groupes.setdefault(d["newskit_id"], []).append(n)
    fondues = 0
    for ns in groupes.values():
        garde = min(ns, key=lambda n: (-G.degree(n), n))
        d = G.nodes[garde]
        noms = list(d.get("aliases") or [])
        for autre in sorted(n for n in ns if n != garde):
            a = G.nodes[autre]
            for nom in [a.get("name"), *(a.get("aliases") or [])]:
                if nom and nom != d.get("name") and nom not in noms:
                    noms.append(nom)
            for k, v in a.items():
                d.setdefault(k, v)
            for v, e in G[autre].items():
                if v != garde and not G.has_edge(garde, v):
                    G.add_edge(garde, v, **e)
            G.remove_node(autre)
            fondues += 1
        if noms:
            d["aliases"] = noms
    return fondues


def _par_cle(G: nx.Graph) -> dict[str, str]:
    """Clé d'une table → entité : son identifiant au cerveau, ou l'ancien (`newskit_id`), sous lequel la composition
    écrit encore ses tables jusqu'à son retrait (K3, étape 3)."""
    return {**{d["newskit_id"]: n for n, d in G.nodes(data=True) if d.get("newskit_id")}, **{n: n for n in G}}


def sous_anciennes_cles(G: nx.Graph) -> nx.Graph:
    """Le graphe où chaque entité qui porte un ancien identifiant (`newskit_id`) est rangée sous lui, comme la
    composition rangeait ses nœuds : la pyramide garde ces clés jusqu'à l'étape 3 de K3, où elles changent avec le
    versement. Le chargeur a fondu les entités d'un même identifiant (`fondre_par_identifiant`) : une clé, une entité."""
    return nx.relabel_nodes(G, {n: d["newskit_id"] for n, d in G.nodes(data=True) if d.get("newskit_id")}, copy=False)


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

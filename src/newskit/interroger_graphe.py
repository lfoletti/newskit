#!/usr/bin/env python3
"""Interrogation du graphe de connaissances. Aucun appel de modèle.

Le graphe pèse plus de vingt mégaoctets : il ne peut pas entrer dans le contexte
d'un modèle. Ce script est la porte d'entrée. Il répond à des questions
précises par des réponses courtes, et **chaque réponse porte sa provenance**,
fichier et ligne, puisque le graphe la porte déjà. C'est tout l'intérêt de
l'avoir construite ainsi.

Les réponses sont volontairement compactes et stables : elles sont faites pour
être lues par un modèle autant que par une personne.

Sous-commandes :
    chercher   <termes>        les nœuds dont l'étiquette correspond (hors auteurs, sauf --type auteur)
    voisinage  <nœud>          relations typées autour d'un nœud
    chemin     <nœud> <nœud>   plus court chemin, relation par relation
    expliquer  <nœud>          fiche de synthèse : type, degré, voisins, sources
    sources    <nœud>          les fiches qui attestent ce nœud, avec la ligne
    fiche      <chemin>        ce qu'une fiche apporte au graphe
    sommets    [--type T]      les nœuds les plus connectés (hors auteurs, sauf --type auteur)
    relations                  le vocabulaire des relations, avec les effectifs

Le corpus n'est pas homogène en autorité, et les réponses le disent. Un nœud
issu de la source « Modèle » — un complément écrit par un modèle, sans source
externe — est marqué `·modèle` partout où il paraît, et `--sans-modele`
l'écarte entièrement. Une relation inférée ou ambiguë est marquée de même.
Mélanger silencieusement une affirmation de modèle et un fait attesté par une
publication rendrait le graphe inutilisable pour ce à quoi il sert.

Les auteurs sont tenus à part. Ils font les deux tiers des nœuds du graphe
principal et les trois quarts ne signent qu'une fiche : mêlés aux résultats, ils
décrivent la bibliographie, pas le domaine. `chercher` et `sommets` les écartent
donc sauf `--type auteur`, et un fragment d'étiquette ne se résout vers un auteur
que si rien d'autre ne correspond. Ils restent dans le graphe : `voisinage`,
`chemin` et `expliquer` les montrent comme avant.

Un nœud se désigne par son identifiant exact ou par un fragment d'étiquette :
la résolution est faite automatiquement, et l'ambiguïté est signalée plutôt que
tranchée au hasard.

Usage :
    python3 -m newskit.interroger_graphe chercher "agent memory"
    python3 -m newskit.interroger_graphe voisinage concept_event_sourcing
    python3 -m newskit.interroger_graphe chemin "event sourcing" "audit trail"
    python3 -m newskit.interroger_graphe expliquer "bi-temporal"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

GRAPHE = RACINE / "graphe" / "graphe.json"

LARGEUR = 64
# Une recherche restée sans réponse est notée (lacunes.py) : l'éclaireur ira la chercher sur le web.
# `NOTER_LACUNE(termes, outil)` est posé par l'appelant — l'outil en ligne de commande, ou le serveur,
# qui écrit dans sa base. Sans appelant, rien n'est noté.
NOTER_LACUNE = None


def _lacune(termes: str, outil: str) -> None:
    if NOTER_LACUNE is not None:
        try:
            NOTER_LACUNE(termes, outil)
        except Exception:  # noqa: BLE001 — noter ne doit jamais faire échouer une recherche
            pass


def _graphe_ou_chemin(valeur: str) -> Path:
    """`<nom>` → graphe/<nom>/graphe.json ; un chemin est rendu tel quel."""
    if valeur.endswith(".json") or "/" in valeur:
        return Path(valeur)
    from newskit import config_sources as cfg  # noqa: PLC0415

    return cfg.chemins_du_graphe(cfg.nom_actuel(valeur))["graphe"]  # un ancien nom mène au graphe renommé


def charger(chemin: Path | None = None) -> nx.Graph:
    p = chemin or GRAPHE
    if not p.exists():
        print(f"  Graphe absent : {p}. Lancer newskit.fusionner_graphe.", file=sys.stderr)
        sys.exit(1)
    charger_passe_partout(p)
    return json_graph.node_link_graph(json.loads(p.read_text(encoding="utf-8")), edges="links")


def _label(G: nx.Graph, n: str) -> str:
    return str(G.nodes[n].get("label") or n)


def _type(G: nx.Graph, n: str) -> str:
    d = G.nodes[n]
    return str(d.get("kind") or d.get("file_type") or "?")


def est_auteur(G: nx.Graph, n: str) -> bool:
    return _type(G, n) == "auteur"


def est_du_modele(G: nx.Graph, n: str) -> bool:
    """Le nœud vient-il d'un complément écrit par un modèle ?"""
    d = G.nodes[n]
    return d.get("origine") == "modele" or "sources/modele/" in str(d.get("source_file") or "")


def _marque(G: nx.Graph, n: str) -> str:
    """Marqueur d'autorité, collé à l'étiquette. Court, pour rester lisible en colonne."""
    return " ·modèle" if est_du_modele(G, n) else ""


def _conf(G: nx.Graph, u: str, v: str) -> str:
    """Marqueur de confiance d'une arête : rien si elle est extraite, sinon le dire."""
    d = G.get_edge_data(u, v) or {}
    c = str(d.get("confidence") or "")
    if c in ("INFERRED", "AMBIGUOUS"):
        s = d.get("confidence_score")
        return f" ·{c.lower()}" + (f" {float(s):.2f}" if isinstance(s, (int, float)) else "")
    return ""


def resoudre(G: nx.Graph, requete: str, muet: bool = False) -> str | None:
    """Identifiant exact, sinon meilleure correspondance d'étiquette.

    L'ambiguïté est signalée plutôt que tranchée : deux notions voisines ne
    doivent pas être confondues parce que l'une est arrivée la première.
    """
    if requete in G:
        return requete
    q = requete.casefold().strip()

    def formes(n: str) -> list[str]:
        # Le libellé et ceux des nœuds fondus dans celui-ci (alias de langue ou de sigle, `aussi`, 09.10.2026) :
        # « Minimisation des données » mène au nœud « Data minimization ».
        # Le titre français d'un article du fil (titre_fr, 03.11.2026) le fait trouver en français.
        return [
            _label(G, n),
            *(G.nodes[n].get("aussi") or []),
            *([G.nodes[n]["titre_fr"]] if G.nodes[n].get("titre_fr") else []),
        ]

    exacts = [n for n in G if any(f.casefold() == q for f in formes(n))]
    if len(exacts) == 1:
        return exacts[0]
    partiels = [n for n in G if any(q in f.casefold() for f in formes(n))]
    if not partiels:
        if not muet:
            print(f"  Aucun nœud ne correspond à « {requete} ».")
            _lacune(requete, "noeud")
        return None
    # Un auteur ne l'emporte sur une notion ou une fiche que s'il est seul à correspondre.
    partiels = [n for n in partiels if not est_auteur(G, n)] or partiels
    partiels.sort(key=lambda n: (len(_label(G, n)), -G.degree(n)))
    if len(partiels) > 1 and not muet:
        tete = partiels[:6]
        print(f"  « {requete} » correspond à {len(partiels)} nœuds ; le plus proche est retenu.")
        for n in tete:
            print(f"    {_label(G, n)[:LARGEUR]:<{LARGEUR}} [{_type(G, n)}]{_marque(G, n)} degré {G.degree(n)}")
        print()
    return partiels[0]


def _provenance(G: nx.Graph, u: str, v: str) -> str:
    d = G.get_edge_data(u, v) or {}
    f, l = d.get("source_file") or "", d.get("source_location") or ""
    court = str(f).split("/")[-1]
    ligne = f" {l}" if l and str(l).lower() != "none" else ""
    return f"{court}{ligne}" if f else ""


# --- Sous-commandes --------------------------------------------------------


def cmd_chercher(G: nx.Graph, args) -> None:
    q = " ".join(args.termes).casefold()
    # Le libellé et ceux des nœuds fondus (`aussi`) : chercher lit ce que resoudre lit (06.11.2026, le témoin).
    trouves = [n for n in G if any(q in f.casefold() for f in (_label(G, n), *(G.nodes[n].get("aussi") or [])))]
    auteurs = 0
    if args.type:
        trouves = [n for n in trouves if _type(G, n) == args.type]
    else:
        hors = [n for n in trouves if not est_auteur(G, n)]
        auteurs, trouves = len(trouves) - len(hors), hors
    trouves.sort(key=lambda n: -G.degree(n))
    if not trouves and not args.type:
        _lacune(" ".join(args.termes), "chercher")
    print(f"  {len(trouves)} nœud(s) pour « {q} »" + (f", type {args.type}" if args.type else ""))
    for n in trouves[: args.limite]:
        print(f"    {G.degree(n):>4}  {_label(G, n)[:LARGEUR]:<{LARGEUR}} [{_type(G, n)}]{_marque(G, n)}  {n[:46]}")
    if len(trouves) > args.limite:
        print(f"    … {len(trouves) - args.limite} de plus")
    if auteurs:
        print(f"    ({auteurs} auteur(s) écarté(s) : --type auteur pour les voir)")


def cmd_voisinage(G: nx.Graph, args) -> None:
    n = resoudre(G, " ".join(args.noeud))
    if not n:
        return
    print(f"  {_label(G, n)}  [{_type(G, n)}]  degré {G.degree(n)}")
    par_rel: dict[str, list[str]] = {}
    for v in G.neighbors(n):
        rel = (G.get_edge_data(n, v) or {}).get("relation", "?")
        par_rel.setdefault(rel, []).append(v)
    for rel in sorted(par_rel, key=lambda r: -len(par_rel[r])):
        voisins = sorted(par_rel[rel], key=lambda v: -G.degree(v))
        print(f"\n    --{rel}-->  ({len(voisins)})")
        for v in voisins[: args.limite]:
            prov = _provenance(G, n, v)
            print(
                f"      {_label(G, v)[:LARGEUR]:<{LARGEUR}} [{_type(G, v)}]{_marque(G, v)}{_conf(G, n, v)}"
                + (f"  ← {prov}" if prov else "")
            )
        if len(voisins) > args.limite:
            print(f"      … {len(voisins) - args.limite} de plus")


def cmd_chemin(G: nx.Graph, args) -> None:
    a, b = resoudre(G, args.depuis), resoudre(G, args.vers)
    if not a or not b:
        return
    try:
        p = nx.shortest_path(G, a, b)
    except nx.NetworkXNoPath:
        print(f"  Aucun chemin entre « {_label(G, a)} » et « {_label(G, b)} ».")
        return
    print(f"  {len(p) - 1} pas de « {_label(G, a)} » à « {_label(G, b)} »\n")
    for i in range(len(p) - 1):
        d = G.get_edge_data(p[i], p[i + 1]) or {}
        prov = _provenance(G, p[i], p[i + 1])
        print(f"    {_label(G, p[i])[:52]}")
        print(f"        --{d.get('relation', '?')}--> [{d.get('confidence', '?')}]" + (f"  ← {prov}" if prov else ""))
    print(f"    {_label(G, p[-1])[:52]}")


def cmd_expliquer(G: nx.Graph, args) -> None:
    n = resoudre(G, " ".join(args.noeud))
    if not n:
        return
    d = G.nodes[n]
    print(f"  {_label(G, n)}{_marque(G, n)}")
    print(f"    identifiant  {n}")
    print(f"    type         {_type(G, n)}    degré {G.degree(n)}")
    if est_du_modele(G, n):
        print(
            "    autorité     complément écrit par un modèle, sans source externe ; relations en INFERRED ou AMBIGUOUS"
        )
    for cle in (
        "date",
        "collected",
        "plateforme",
        "publication",
        "origine",
        "contributor",
        "verifie",
        "categories",
        "url",
        "complet",
        "words",
    ):
        if d.get(cle) not in (None, "", []):
            print(f"    {cle:<12} {str(d[cle])[:72]}")
    if d.get("rationale"):
        print(f"\n    pourquoi : {str(d['rationale'])[:400]}")
    rels = Counter((G.get_edge_data(n, v) or {}).get("relation", "?") for v in G.neighbors(n))
    print("\n    relations : " + ", ".join(f"{r} ×{c}" for r, c in rels.most_common()))
    voisins = sorted(G.neighbors(n), key=lambda v: -G.degree(v))[:10]
    print("\n    voisins les plus centraux :")
    for v in voisins:
        print(
            f"      {_label(G, v)[:LARGEUR]:<{LARGEUR}} [{_type(G, v)}]{_marque(G, v)}"
            f"{_conf(G, n, v)} degré {G.degree(v)}"
        )
    cmd_sources(G, argparse.Namespace(noeud=[n], limite=6), entete=False)


def cmd_sources(G: nx.Graph, args, entete: bool = True) -> None:
    n = resoudre(G, " ".join(args.noeud), muet=not entete)
    if not n:
        return
    if entete:
        print(f"  Sources attestant « {_label(G, n)} »")
    else:
        print("\n    attesté par :")
    vues: dict[str, str] = {}
    for v in G.neighbors(n):
        d = G.get_edge_data(n, v) or {}
        f = str(d.get("source_file") or "")
        if f and f not in vues:
            vues[f] = str(d.get("source_location") or "")
    prefixe = "    " if entete else "      "
    for f, l in list(vues.items())[: args.limite]:
        ligne = f" {l}" if l and str(l).lower() != "none" else ""
        print(f"{prefixe}{f}{ligne}")
    if len(vues) > args.limite:
        print(f"{prefixe}… {len(vues) - args.limite} autre(s) fiche(s)")


def cmd_fiche(G: nx.Graph, args) -> None:
    q = args.chemin.casefold()
    cands = [
        n
        for n, d in G.nodes(data=True)
        if d.get("kind") in ("article", "papier") and (q in str(n).casefold() or q in _label(G, n).casefold())
    ]
    if not cands:
        print(f"  Aucune fiche ne correspond à « {args.chemin} ».")
        return
    cands.sort(key=lambda n: -G.degree(n))
    n = cands[0]
    d = G.nodes[n]
    print(f"  {_label(G, n)}")
    print(
        f"    {d.get('source_file', '?')}  |  {d.get('date', '?')}  |  {d.get('words', '?')} mots"
        f"  |  complet={d.get('complet')}"
    )
    par_rel: dict[str, list[str]] = {}
    for v in G.neighbors(n):
        rel = (G.get_edge_data(n, v) or {}).get("relation", "?")
        par_rel.setdefault(rel, []).append(v)
    print(f"    apporte {G.degree(n)} arête(s) au graphe :")
    for rel in sorted(par_rel, key=lambda r: -len(par_rel[r])):
        noms = [_label(G, v) for v in sorted(par_rel[rel], key=lambda v: -G.degree(v))[:5]]
        suite = f" … +{len(par_rel[rel]) - 5}" if len(par_rel[rel]) > 5 else ""
        print(f"      {rel:<26} {len(par_rel[rel]):>4}  {', '.join(s[:34] for s in noms)}{suite}")


# --- Passages : le texte des fiches, là où sont les chiffres, les versions et les dates ---------------
# Le banc du 04.10.2026 l'a montré : le graphe répondait à 11 % des questions précises, quand le LLM avec
# recherche web en trouvait 78 %. Les réponses étaient dans les fiches ; les notions extraites ne les
# gardaient pas. `passages` cherche dans le texte des fiches du graphe, et rend le paragraphe avec sa ligne.
_PARAGRAPHES: dict[str, tuple[float, list[tuple[int, str]]]] = {}
RE_GABARIT = re.compile(r"appeared first on|^The post \[|^Source : http|^Dépôt : http", re.I)
VIDES_PASSAGE = {
    "the",
    "and",
    "for",
    "with",
    "les",
    "des",
    "une",
    "dans",
    "pour",
    "est",
    "que",
    "qui",
    "what",
    "which",
    "how",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "sur",
    "par",
    "from",
    "this",
    "that",
}


def _plier(texte: str) -> str:
    import unicodedata  # noqa: PLC0415

    t = unicodedata.normalize("NFKD", texte.casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


PASSE_PARTOUT: set[str] = (
    set()
)  # empreintes des paragraphes répétés d'une source (passe_partout.py), chargées avec le graphe
RE_TAGS = re.compile(r"^\s*(?:Tags?|Étiquettes?|Catégories?|Categories|Filed under)\s*:", re.I)


def charger_passe_partout(chemin_graphe: Path) -> int:
    """Le passe-partout calculé à côté du graphe (passe_partout.json) : les paragraphes que la lecture saute. Rend leur
    nombre. La mémoire des paragraphes est vidée, puisqu'elle dépend du filtre."""
    p = Path(chemin_graphe).parent / "passe_partout.json"
    PASSE_PARTOUT.clear()
    _PARAGRAPHES.clear()
    if p.exists():
        try:
            PASSE_PARTOUT.update(json.loads(p.read_text(encoding="utf-8")).get("empreintes", {}).keys())
        except (OSError, json.JSONDecodeError):
            pass
    return len(PASSE_PARTOUT)


def _bruit(texte: str) -> bool:
    """Un paragraphe qui n'est pas de la substance (10.10.2026) : passe-partout de sa source, ligne d'étiquettes, liste de
    liens sans texte. Les fiches restent entières ; c'est la lecture qui saute."""
    if RE_TAGS.match(texte):
        return True
    if len(re.findall(r"\]\(", texte)) >= 2 and len(re.sub(r"\[[^\]]*\]\([^)]*\)", "", texte).split()) < 12:
        return True
    if (
        PASSE_PARTOUT
        and hashlib.sha1(re.sub(r"\s+", " ", texte.strip()).encode("utf-8")).hexdigest()[:16] in PASSE_PARTOUT
    ):
        return True
    return False


def _paragraphes(chemin: Path) -> list[tuple[int, str]]:
    """(ligne de début, texte) des paragraphes du corps d'une fiche, gardés en mémoire tant qu'elle ne change pas ; sans
    le bruit (_bruit) quand un passe-partout est chargé."""
    cle = str(chemin)
    try:
        mtime = chemin.stat().st_mtime
    except OSError:
        return []
    garde = _PARAGRAPHES.get(cle)
    if garde and garde[0] == mtime:
        return garde[1]
    lignes = chemin.read_text(encoding="utf-8", errors="replace").splitlines()
    debut_corps = 0
    if lignes and lignes[0].strip() == "---":  # en-tête de la fiche : pas un passage
        debut_corps = next((i + 1 for i in range(1, len(lignes)) if lignes[i].strip() == "---"), 0)
    out, courant, debut = [], [], None
    for i in range(debut_corps, len(lignes) + 1):
        l = lignes[i] if i < len(lignes) else ""
        if l.strip():
            if debut is None:
                debut = i + 1
            courant.append(l.strip())
        elif courant:
            texte = " ".join(courant)
            if not RE_GABARIT.search(texte) and not _bruit(
                texte
            ):  # ni le pied de flux, ni le passe-partout, ni les étiquettes
                out.append((debut, texte))
            courant, debut = [], None
    _PARAGRAPHES[cle] = (mtime, out)
    return out


def cmd_passages(G: nx.Graph, args) -> None:
    termes = [
        m for m in re.findall(r"[\w.+-]+", _plier(" ".join(args.termes))) if len(m) >= 2 and m not in VIDES_PASSAGE
    ]
    if not termes:
        print("  Termes trop courts ou trop communs.")
        return
    reserve = getattr(args, "reserve", None)  # posé par le serveur : réservées ou non publiées
    fiches = {}
    for n, d in G.nodes(data=True):
        if d.get("kind") in ("article", "papier") and d.get("source_file"):
            p = Path(str(d["source_file"]))
            p = p if p.is_absolute() else RACINE / p
            fiches[str(p)] = (n, str(d.get("date") or ""))
    trouves = []
    for chemin, (n, date) in fiches.items():
        for ligne, texte in _paragraphes(Path(chemin)):
            plie = _plier(texte)
            presents = [t for t in termes if t in plie]
            if len(presents) * 2 >= len(termes) and presents:  # au moins la moitié des termes
                trouves.append(
                    (len(set(presents)), sum(plie.count(t) for t in presents), date, chemin, ligne, texte, n)
                )
    trouves.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    if not trouves:
        print(f"  Aucun passage ne contient ces termes : {' '.join(termes)}")
        _lacune(" ".join(args.termes), "passages")
        return
    print(f"  {len(trouves)} passage(s) pour « {' '.join(termes)} », les plus complets d'abord")
    for k, (nb, _, date, chemin, ligne, texte, n) in enumerate(trouves[: args.limite]):
        rel = str(Path(chemin).relative_to(RACINE)) if Path(chemin).is_relative_to(RACINE) else chemin
        print(f"\n  {_label(G, n)[:90]}  ({date[:10]})\n    {rel} L{ligne}  — {nb}/{len(termes)} termes")
        if reserve and reserve(Path(chemin)):
            print(
                "    texte non rendu ici (abonnement, document privé ou refus de l'auteur) : lire l'original à son adresse"
            )
            continue
        print("    " + (texte if len(texte) <= args.longueur else texte[: args.longueur] + " […]"))


def cmd_sommets(G: nx.Graph, args) -> None:
    noeuds = [n for n in G if (_type(G, n) == args.type if args.type else not est_auteur(G, n))]
    noeuds.sort(key=lambda n: -G.degree(n))
    print("  nœuds les plus connectés" + (f", type {args.type}" if args.type else ", hors auteurs"))
    for n in noeuds[: args.limite]:
        print(f"    {G.degree(n):>5}  {_label(G, n)[:LARGEUR]:<{LARGEUR}} [{_type(G, n)}]{_marque(G, n)}")


def cmd_relations(G: nx.Graph, args) -> None:
    rels = Counter(d.get("relation", "?") for _, _, d in G.edges(data=True))
    confs = Counter(d.get("confidence", "?") for _, _, d in G.edges(data=True))
    types = Counter(_type(G, n) for n in G)
    print(f"  {G.number_of_nodes()} nœuds, {G.number_of_edges()} arêtes\n")
    print("  types de nœuds")
    for t, c in types.most_common():
        print(f"    {t:<16} {c:>7}")
    print("\n  relations")
    for r, c in rels.most_common():
        print(f"    {r:<28} {c:>7}")
    print("\n  confiance")
    for k, c in confs.most_common():
        print(f"    {k:<16} {c:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", type=_graphe_ou_chemin, help="nom d'un graphe du registre ou chemin d'un graphe.json")
    ap.add_argument(
        "--sans-modele", action="store_true", help="écarter les nœuds issus des compléments écrits par un modèle"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("chercher")
    p.add_argument("termes", nargs="+")
    p.add_argument("--type")
    p.add_argument("--limite", type=int, default=20)
    p.set_defaults(fn=cmd_chercher)

    p = sub.add_parser("voisinage")
    p.add_argument("noeud", nargs="+")
    p.add_argument("--limite", type=int, default=8)
    p.set_defaults(fn=cmd_voisinage)

    p = sub.add_parser("chemin")
    p.add_argument("depuis")
    p.add_argument("vers")
    p.set_defaults(fn=cmd_chemin)

    p = sub.add_parser("expliquer")
    p.add_argument("noeud", nargs="+")
    p.set_defaults(fn=cmd_expliquer)

    p = sub.add_parser("sources")
    p.add_argument("noeud", nargs="+")
    p.add_argument("--limite", type=int, default=12)
    p.set_defaults(fn=cmd_sources)

    p = sub.add_parser("fiche")
    p.add_argument("chemin")
    p.set_defaults(fn=cmd_fiche)

    p = sub.add_parser("sommets")
    p.add_argument("--type")
    p.add_argument("--limite", type=int, default=25)
    p.set_defaults(fn=cmd_sommets)

    p = sub.add_parser("relations")
    p.set_defaults(fn=cmd_relations)

    p = sub.add_parser("passages")
    p.add_argument("termes", nargs="+")
    p.add_argument("--limite", type=int, default=6)
    p.add_argument("--longueur", type=int, default=700)
    p.set_defaults(fn=cmd_passages)

    # La question entière que le graphe n'a pas su traiter, enregistrée par le skill /kg.
    p = sub.add_parser("lacune")
    p.add_argument("question", nargs="+")
    p.set_defaults(fn=None)

    args = ap.parse_args()
    # Le nom du graphe interrogé, pour ranger ses lacunes : le principal, ou graphe/<nom>/graphe.json.
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import lacunes  # noqa: PLC0415

    chemin = Path(args.graphe).resolve() if args.graphe else GRAPHE
    nom = (
        cfg.GRAPHE_PRINCIPAL
        if chemin == GRAPHE.resolve()
        else chemin.parent.name
        if chemin.parent.parent == (RACINE / "graphe").resolve()
        else None
    )
    if args.cmd == "lacune":
        question = " ".join(args.question)
        if nom and lacunes.noter(nom, question, "skill", "skill", question=question):
            print(f"  lacune notée pour {nom} : l'éclaireur la cherchera au prochain passage")
        else:
            print("  lacune non notée (graphe inconnu, question vide, ou déjà écartée)")
        return 0
    if nom and not os.environ.get("KG_SANS_LACUNE"):  # le banc de questions interroge sans créer de lacunes
        global NOTER_LACUNE
        NOTER_LACUNE = lambda termes, outil: lacunes.noter_sans_bruit(nom, termes, outil, "local")  # noqa: E731
    G = charger(args.graphe)
    # Ce qu'une session lit du graphe vient du web : encadré comme donnée (01.11.2026, lecture critique de sécurité).
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415
    from newskit.commun import encadrer_donnees  # noqa: PLC0415

    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        if args.sans_modele:
            retires = [n for n in list(G) if est_du_modele(G, n)]
            G.remove_nodes_from(retires)
            print(f"  ({len(retires)} nœud(s) de source « Modèle » écartés)\n")
        args.fn(G, args)
    print(encadrer_donnees(tampon.getvalue().rstrip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

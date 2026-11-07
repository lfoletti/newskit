#!/usr/bin/env python3
"""Compose les couches du graphe en un seul graphe interrogeable.

Trois entrées, une sortie. La composition passe par `graphify.build.build`,
qui déduplique nœuds et arêtes, ce qui permet aux couches de désigner la même
fiche par le même identifiant et de fusionner au lieu de se dupliquer.

Entrées :
    graphe/squelette.json                     couche 1, déterministe
    graphify-out/.graphify_ast.json           structure des titres, déterministe
    graphify-out/.graphify_chunk_*.json       couche 2, extraction sémantique

Sortie :
    graphe/graphe.json      graphe complet, format node-link de networkx
    graphe/graphe_stats.md  relevé chiffré, lisible

Usage :
    python3 -m newskit.fusionner_graphe
    python3 -m newskit.fusionner_graphe --sans-ast   # ignore la hiérarchie des titres
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

from networkx.readwrite import json_graph

from graphify.build import build, validate_extraction  # noqa: E402
from newskit.commun import archiver_gz, normaliser_etiquette  # noqa: E402

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

SQUELETTE = RACINE / "graphe" / "squelette.json"
AST = RACINE / "graphify-out" / ".graphify_ast.json"
# Les lots sémantiques sont des artefacts coûteux : treize exécutions de
# sous-agents. Leur domicile durable est `graphe/couche2/`, qui nous appartient.
# `graphify-out/` reste lu parce qu'une extraction fraîche y écrit d'abord, mais
# graphify peut réécrire ce dossier, donc rien d'irremplaçable n'y séjourne.
LOTS_DURABLES = str(RACINE / "graphe" / "couche2" / "chunk_*.json")
LOTS_FRAIS = str(RACINE / "graphify-out" / ".graphify_chunk_*.json")
SORTIE = RACINE / "graphe" / "graphe.json"
STATS = RACINE / "graphe" / "graphe_stats.md"


RE_PARENTHESE_FINALE = re.compile(r"\s*\([^()]*\)\s*$")


# Les sous-agents suffixent parfois le titre d'une fiche par sa publication et
# sa date, « … (Ahead of AI, 2024-09-21) » : la variante sans ce suffixe est
# essayée aussi, voir RE_PARENTHESE_FINALE.
_norm_label = normaliser_etiquette


RE_BALISAGE = re.compile(r"^[\s*_`#>]+|[\s*_`:]+$")


def lot_orphelin(d: dict) -> bool:
    """Un lot par fiche dont aucune fiche source n'existe plus sur le disque (mainteneur, 03.11.2026 : les publicités retirées
    revenaient par leur lot). Un lot sans source_file n'est pas jugé. L'extraction écrit des chemins absolus : ils se
    lisent depuis leur segment `sources/`, d'une machine à l'autre (06.11.2026, le témoin : la garde ne voyait aucun
    lot réel)."""
    srcs = {str(n.get("source_file") or "") for n in d.get("nodes") or []} - {""}
    srcs = {"sources/" + x.split("/sources/", 1)[1] if x.startswith("/") and "/sources/" in x else x for x in srcs}
    srcs = {x for x in srcs if x.startswith("sources/")}
    return bool(srcs) and not any((RACINE / x).exists() for x in srcs)


def nettoyer_titres(extraction: dict) -> int:
    """Retire le balisage markdown des libellés des nœuds de titre de l'AST : une ligne en gras lue comme un titre
    donnait un nœud « **Total Fines** » (12.10.2026 : 651 libellés d'un graphe de conformité, dont « **Real-time remote biometric
    identification…** » que rien ne retrouvait). Rend le nombre de libellés changés."""
    n = 0
    for x in extraction.get("nodes", []):
        l = x.get("label")
        if not isinstance(l, str):
            continue
        propre = RE_BALISAGE.sub("", l.replace("**", "").replace("__", "").replace("`", "")).strip()
        propre = re.sub(r"\s+", " ", propre)
        if propre and propre != l:
            x["label"] = propre
            n += 1
    return n


def fondre_doublons_de_fiche(
    extraction: dict, ids_squelette: set[str], labels_squelette: dict[str, str], nom: str
) -> tuple[int, int]:
    """Retire d'une extraction les nœuds qui redécrivent une fiche du squelette.

    Pourquoi. Le squelette décrit chaque fiche avec son titre et son typage ; la
    passe AST et les lots sémantiques la redécrivent au passage, avec une
    étiquette parfois moins bonne et sans notre typage. La déduplication de
    graphify tranche alors par ressemblance d'étiquette, et conserve
    arbitrairement l'un ou l'autre : vingt-neuf fiches perdaient ainsi leur
    typage, et une sur trois son titre.

    On préfère donc une règle explicite : sur une fiche, le squelette fait foi.
    Les arêtes de l'extraction sont réécrites vers son identifiant, ce qui ne
    perd aucune relation.
    """
    remap: dict[str, str] = {}
    gardes = []
    for n in extraction["nodes"]:
        nid, label = n["id"], n.get("label", "")
        if nid in ids_squelette:
            # Même identifiant que le squelette : la redéclaration est retirée
            # de l'extraction, pour que les attributs du squelette prévalent.
            # Les arêtes gardent l'identifiant, que le squelette fournit.
            continue
        if n.get("file_type") in ("document", "paper"):
            cle = _norm_label(label)
            cible = labels_squelette.get(cle) or labels_squelette.get(_norm_label(RE_PARENTHESE_FINALE.sub("", label)))
            if cible:
                remap[nid] = cible
                continue
        gardes.append(n)
    extraction["nodes"] = gardes

    aretes = []
    for e in extraction["edges"]:
        e["source"] = remap.get(e["source"], e["source"])
        e["target"] = remap.get(e["target"], e["target"])
        if e["source"] != e["target"]:  # la fusion peut créer des boucles
            aretes.append(e)
    boucles = len(extraction["edges"]) - len(aretes)
    extraction["edges"] = aretes
    for h in extraction.get("hyperedges", []):
        h["nodes"] = list(dict.fromkeys(remap.get(x, x) for x in h.get("nodes", [])))
    return len(remap), boucles


TYPES_CONCEPT = ("concept", "rationale")


def consolider_concepts(G) -> int:
    """Fond les nœuds de concept qui portent exactement la même étiquette.

    Les sous-agents travaillent par lots indépendants et produisent parfois deux
    orthographes d'identifiant pour une même notion, `concept_inference_scaling`
    et `concept_inference_time_scaling`. L'étiquette, elle, est identique.

    On fusionne donc sur l'égalité stricte d'étiquette normalisée, jamais sur une
    ressemblance : c'est le même critère que pour les auteurs, et il respecte la
    règle du corpus qui préfère scinder que fusionner. Le survivant est le nœud
    de plus haut degré, l'identifiant le plus court tranchant les égalités, pour
    que le résultat ne dépende pas de l'ordre de lecture.
    """
    from collections import defaultdict

    groupes = defaultdict(list)
    for n, d in G.nodes(data=True):
        if (d.get("kind") or d.get("file_type")) in TYPES_CONCEPT:
            groupes[_norm_label(d.get("label", ""))].append(n)

    fondus = 0
    for label, membres in groupes.items():
        if not label or len(membres) < 2:
            continue
        membres.sort(key=lambda n: (-G.degree(n), len(str(n)), str(n)))
        garde, autres = membres[0], membres[1:]
        for perdu in autres:
            for voisin in list(G.neighbors(perdu)):
                if voisin == garde:
                    continue
                attrs = dict(G.get_edge_data(perdu, voisin) or {})
                if not G.has_edge(garde, voisin):
                    G.add_edge(garde, voisin, **attrs)
            for cle, val in G.nodes[perdu].items():  # ne rien perdre des attributs
                G.nodes[garde].setdefault(cle, val)
            G.remove_node(perdu)
            fondus += 1
    return fondus


def cible_finale(alias: dict[str, str], n: str) -> str:
    """La cible d'un alias au bout de sa chaîne (C → A, A → B : B) ; une boucle s'arrête au dernier nœud neuf."""
    vus = {n}
    while n in alias and alias[n] not in vus:
        n = alias[n]
        vus.add(n)
    return n


def appliquer_alias(G, alias: dict[str, str], vides: list | None = None) -> int:
    """Fond chaque nœud secondaire de la table dans son canonique : les arêtes passent au canonique (une arête déjà
    là n'est pas doublée), ses attributs manquants aussi, et le libellé du secondaire est gardé sous `aussi`, pour que
    la recherche le retrouve. Une chaîne se suit jusqu'au bout ; un alias dont la cible manque n'est pas appliqué et
    va dans `vides` (06.11.2026, le témoin : sauté en silence). Rend le nombre de nœuds fondus."""
    fondus = 0
    for perdu in list(alias):
        garde = cible_finale(alias, perdu)
        if perdu not in G or perdu == garde:
            continue
        if garde not in G:
            if vides is not None:
                vides.append((perdu, alias[perdu]))
            continue
        for voisin in list(G.neighbors(perdu)):
            if voisin == garde:
                continue
            attrs = dict(G.get_edge_data(perdu, voisin) or {})
            if not G.has_edge(garde, voisin):
                G.add_edge(garde, voisin, **attrs)
        for cle, val in G.nodes[perdu].items():
            G.nodes[garde].setdefault(cle, val)
        aussi = list(G.nodes[garde].get("aussi") or [])
        for l in [G.nodes[perdu].get("label"), *(G.nodes[perdu].get("aussi") or [])]:
            if l and l not in aussi and l != G.nodes[garde].get("label"):
                aussi.append(l)
        if aussi:
            G.nodes[garde]["aussi"] = aussi
        G.remove_node(perdu)
        fondus += 1
    return fondus


def appliquer_anglais(G, anglais: dict[str, str]) -> int:
    """Donne à chaque notion au libellé étranger son nom anglais (fondre_doublons.traduire, 03.11.2026) ; l'original
    reste sous `aussi`, pour que la recherche le retrouve. Rend le nombre de libellés changés."""
    changes = 0
    for n, en in anglais.items():
        if n not in G or not en or G.nodes[n].get("label") == en:
            continue
        aussi = list(G.nodes[n].get("aussi") or [])
        ancien = G.nodes[n].get("label")
        if ancien and ancien not in aussi:
            aussi.append(ancien)
        G.nodes[n]["aussi"] = [x for x in aussi if x != en]
        G.nodes[n]["label"] = en
        changes += 1
    return changes


def appliquer_francais(G, table: dict) -> int:
    """Pose `label_fr` et `rationale_fr` sur chaque nœud dont la table (francais.py, 03.11.2026) a une traduction de
    l'original actuel ; une traduction d'un autre original (le libellé a changé) n'est pas posée. Rend le nombre de
    champs posés."""
    from newskit import francais  # noqa: PLC0415

    k = 0
    for n, d in G.nodes(data=True):
        for cle, champ, dest in (("libelles", "label", "label_fr"), ("textes", "rationale", "rationale_fr")):
            v = table.get(cle, {}).get(n)
            if v and d.get(champ) and v[1] == francais.empreinte(d[champ]):
                d[dest] = v[0]
                k += 1
    return k


def appliquer_tables(G, graphes: list[str]) -> tuple[int, int]:
    """Les tables `alias.json` des graphes donnés : les alias d'abord (fondus), puis les noms anglais des notions
    restées seules. Rend (nœuds fondus, libellés renommés)."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    fondus = renommes = 0
    tables = []
    for g in graphes:
        p = cfg.chemins_du_graphe(g)["base"] / "alias.json"
        if p.exists():
            tables.append(json.loads(p.read_text(encoding="utf-8")))
    vides: list = []
    for t in tables:
        fondus += appliquer_alias(G, t.get("alias", {}), vides)
    if vides:
        print(
            f"    {len(vides)} alias sans cible (non appliqué(s)) : "
            + ", ".join(f"{a} → {b}" for a, b in vides[:5])
            + (" …" if len(vides) > 5 else "")
        )
    for t in tables:
        renommes += appliquer_anglais(G, t.get("anglais", {}))
    return fondus, renommes


def _charger(chemin: Path, nom: str, avertissements: list[str]) -> dict | None:
    if not chemin.exists():
        avertissements.append(f"{nom} absent : {chemin.relative_to(RACINE)}")
        return None
    try:
        d = json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        avertissements.append(f"{nom} illisible ({e})")
        return None
    d.setdefault("nodes", [])
    d.setdefault("edges", [])
    d.setdefault("hyperedges", [])
    erreurs = validate_extraction(d)
    if erreurs:
        avertissements.append(f"{nom} : {len(erreurs)} erreur(s) de schéma, ex. {erreurs[0][:90]}")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sans-ast", action="store_true", help="ignore la hiérarchie des titres")
    ap.add_argument("--graphe", help="nom d'un graphe du registre (défaut : le principal)")
    args = ap.parse_args()

    from newskit import config_sources as cfg  # noqa: PLC0415

    nom_graphe = args.graphe or cfg.GRAPHE_PRINCIPAL
    chemins = cfg.chemins_du_graphe(nom_graphe)
    global SQUELETTE, SORTIE, STATS, LOTS_DURABLES, AST
    SQUELETTE, SORTIE, STATS = chemins["squelette"], chemins["graphe"], chemins["stats"]
    LOTS_DURABLES = str(chemins["couche2"] / "chunk_*.json")
    # Depuis le 27.09.2026, couche 2 et structure des titres existent par graphe
    # (graphe financier) ; un graphe sans fiche dense n'a simplement pas ces fichiers.
    AST = chemins["structure"]
    if nom_graphe != cfg.GRAPHE_PRINCIPAL and not AST.exists():
        args.sans_ast = True

    avertissements: list[str] = []
    extractions, origines = [], []

    sq = _charger(SQUELETTE, "squelette", avertissements)
    if sq is None:
        print("  Le squelette est indispensable. Lancer d'abord extraire_squelette.py.", file=sys.stderr)
        return 1
    extractions.append(sq)
    origines.append(("couche 1 — squelette", sq))
    # Référentiels de fiche : sur une fiche, le squelette fait foi.
    fiches_sq = [n for n in sq["nodes"] if n.get("kind") in ("article", "papier")]
    ids_sq = {n["id"] for n in sq["nodes"]}
    labels_sq: dict[str, str] = {}
    for n in fiches_sq:
        labels_sq.setdefault(_norm_label(n.get("label", "")), n["id"])
    fondus_total = boucles_total = 0

    if not args.sans_ast:
        ast = _charger(AST, "AST", avertissements)
        if ast:
            # L'extraction AST redéclare chaque fiche comme nœud, mais étiquetée
            # par son nom de fichier, là où le squelette l'étiquette par son
            # titre. À la déduplication, l'étiquette gagnante est arbitraire, et
            # une fiche sur trois perdait son titre au profit de son nom de
            # fichier. On retire donc ces doublons de l'AST et on garde ses
            # arêtes : elles désignent le même identifiant, donc elles se
            # rattachent au nœud du squelette, titre compris.
            avant = len(ast["nodes"])
            nettoyes = nettoyer_titres(ast)
            f, b = fondre_doublons_de_fiche(ast, ids_sq, labels_sq, "AST")
            fondus_total += f
            boucles_total += b
            # L'AST produit quelques identifiants dérivés de chemins absolus,
            # dont les arêtes ne mènent nulle part.
            connus = ids_sq | {n["id"] for n in ast["nodes"]}
            ast["edges"] = [e for e in ast["edges"] if e["source"] in connus and e["target"] in connus]
            print(
                f"    AST : {avant - len(ast['nodes'])} nœuds de fiche fondus dans le squelette, {nettoyes} titre(s) débarrassé(s) de leur balisage"
            )
            extractions.append(ast)
            origines.append(("structure des titres", ast))

    # Un graphe qui en inclut d'autres (un graphe financier inclut un graphe de trading quantitatif, 04.10.2026) reprend leur structure
    # des titres et leurs lots de couche 2 : rien n'est extrait deux fois.
    inclus = [cfg.chemins_du_graphe(g) for g in cfg.graphes_inclus(nom_graphe)]
    for g, ch in zip(cfg.graphes_inclus(nom_graphe), inclus):
        ast_inclus = _charger(ch["structure"], f"AST de {g}", avertissements) if ch["structure"].exists() else None
        if ast_inclus:
            nettoyer_titres(ast_inclus)
            f, b = fondre_doublons_de_fiche(ast_inclus, ids_sq, labels_sq, f"AST de {g}")
            fondus_total += f
            boucles_total += b
            connus = ids_sq | {n["id"] for n in ast_inclus["nodes"]}
            ast_inclus["edges"] = [e for e in ast_inclus["edges"] if e["source"] in connus and e["target"] in connus]
            extractions.append(ast_inclus)
            origines.append((f"structure des titres — {g}", ast_inclus))

    # Un lot présent aux deux endroits n'est lu qu'une fois, la version durable
    # faisant foi.
    lots: dict[str, str] = {}
    lots_frais = sorted(glob.glob(LOTS_FRAIS)) if nom_graphe == cfg.GRAPHE_PRINCIPAL else []
    # Les lots par fiche de l'extraction incrémentale (extraire_concepts.py),
    # à côté des douze lots historiques : un fichier par fiche dense, remplacé
    # quand la fiche change.
    lots_par_fiche = sorted(glob.glob(str(chemins["couche2"] / "fiches" / "*.json")))
    for ch in inclus:
        lots_par_fiche += sorted(glob.glob(str(ch["couche2"] / "fiches" / "*.json")))
    # Un graphe à partage fiche par fiche (partage_graphe.py, GV4) ne lit que les lots de ses fiches retenues, et reprend
    # ceux des fiches d'autres graphes rangées chez lui (cherchés dans les lots de tous les graphes).
    from newskit import partage_graphe  # noqa: PLC0415

    retenues_d = partage_graphe.fiches_du_graphe(nom_graphe)
    retenues = set(retenues_d) if retenues_d is not None else None
    if retenues is not None:
        decl = partage_graphe.declaration(nom_graphe)
        from newskit import extraire_concepts as ec  # noqa: PLC0415
        from newskit import fiche as fiche_mod  # noqa: PLC0415

        for r in partage_graphe.ajouts(decl[0], decl[1], cfg.dossiers_du_graphe(nom_graphe)):
            if r not in retenues:
                continue
            ident = ec.identifiant_de_fiche(RACINE / r, fiche_mod.lire_fichier(RACINE / r)[0])
            for g in cfg.graphes():
                p = cfg.chemins_du_graphe(g)["couche2"] / "fiches" / f"{ident}.json"
                if p.exists():
                    lots_par_fiche.append(str(p))
                    break
    for chemin in sorted(glob.glob(LOTS_DURABLES)) + lots_frais:
        lots.setdefault(Path(chemin).stem[-2:], chemin)  # chunk_NN : les deux chiffres
    # Un lot par fiche est identifié par son nom entier : sur les deux derniers
    # caractères, trente lots se réduisaient à quatre (27.09.2026, graphe financier).
    for chemin in lots_par_fiche:
        lots.setdefault("fiche " + Path(chemin).stem[-40:], chemin)
    orphelins = 0
    for nom, chemin in sorted(lots.items()):
        d = _charger(Path(chemin), f"lot {nom}", avertissements)
        if (
            d
            and retenues is not None
            and nom.startswith("fiche ")
            and not partage_graphe.lot_retenu(d, retenues, RACINE)
        ):
            continue
        if d and nom.startswith("fiche ") and lot_orphelin(d):
            orphelins += 1  # fiche supprimée (publicité, retrait) : ses notions ne reviennent pas
            continue
        if d:
            f, b = fondre_doublons_de_fiche(d, ids_sq, labels_sq, nom)
            fondus_total += f
            boucles_total += b
            extractions.append(d)
            origines.append((f"couche 2 — {nom}", d))
    if not lots:
        avertissements.append("aucun lot d'extraction sémantique : le graphe n'aura pas de couche conceptuelle")

    if orphelins:
        print(f"  {orphelins} lot(s) de fiche supprimée écarté(s)")
    print("  entrées composées :")
    for nom, d in origines:
        print(f"    {nom:<28} {len(d['nodes']):>6} nœuds, {len(d['edges']):>6} arêtes")

    print(
        f"    {fondus_total} nœud(s) de fiche fondus dans le squelette au total, {boucles_total} boucle(s) écartée(s)"
    )

    G = build(extractions, root=str(RACINE))
    if retenues is not None:
        n, a = partage_graphe.retirer_fiches(G, partage_graphe.exclues(G, retenues, RACINE), RACINE)
        r = partage_graphe.retirer_restes_de_page(G)
        print(f"    partage : {n} nœud(s) et {a} arête(s) des fiches non retenues retirés, {r} reste(s) de page")
    homonymes = consolider_concepts(G)
    if homonymes:
        print(f"    {homonymes} concept(s) homonyme(s) fondus après composition")
    # La table d'alias du graphe (fondre_doublons.py, 09.10.2026) : la même notion sous plusieurs libellés —
    # traduction, sigle, variante — tranchée par un modèle et gardée ; appliquée à chaque composition, pour que les
    # graphes inclus et le graphe hôte la partagent.
    # Puis les notions au libellé étranger restées seules prennent leur nom anglais (03.11.2026).
    fondus_alias, renommes = appliquer_tables(G, [nom_graphe, *cfg.graphes_inclus(nom_graphe)])
    if fondus_alias:
        print(f"    {fondus_alias} nœud(s) fondus par la table d'alias (doublons de langue, de sigle)")
    if renommes:
        print(f"    {renommes} libellé(s) étranger(s) remplacé(s) par leur nom anglais")
    # L'affichage en français (francais.py, 03.11.2026) : label_fr et rationale_fr, de la table du graphe.
    from newskit import francais  # noqa: PLC0415

    p_fr = francais.chemin_table(nom_graphe)
    if p_fr.exists():
        poses = appliquer_francais(G, json.loads(p_fr.read_text(encoding="utf-8")))
        reste = len(francais.manquants([{"id": n, **d} for n, d in G.nodes(data=True)]))
        print(f"    français : {poses} champ(s) posé(s), {reste} élément(s) affiché(s) sans français")
    # L'actualité de chaque notion, lue de ses attestations datées (diachronie.py, 09.10.2026) : première et dernière,
    # versions des textes de référence qui la nomment, en vigueur ou remplacée seulement, évoluant entre deux versions.
    from newskit import diachronie  # noqa: PLC0415

    c_act = diachronie.annoter_graphe(G)
    if c_act["reference"]:
        print(
            f"    actualité : {c_act['notions']} notion(s) datée(s), {c_act['reference']} attestée(s) par un texte de référence "
            f"dont {c_act['en_vigueur']} en vigueur, {c_act['remplacee_seulement']} remplacée(s) seulement, {c_act['evolue']} évoluant"
        )
    donnees = json_graph.node_link_data(G, edges="links")
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    gz = archiver_gz(SORTIE)

    kinds = Counter(d.get("kind") or d.get("file_type") or "?" for _, d in G.nodes(data=True))
    rels = Counter(d.get("relation", "?") for _, _, d in G.edges(data=True))
    confs = Counter(d.get("confidence", "?") for _, _, d in G.edges(data=True))

    lignes = [
        "# Relevé du graphe composé",
        "",
        f"Produit par `newskit.fusionner_graphe` à partir de {len(extractions)} extractions"
        + (f" (graphe {nom_graphe})." if nom_graphe != cfg.GRAPHE_PRINCIPAL else "."),
        "",
        f"- **{G.number_of_nodes()} nœuds**",
        f"- **{G.number_of_edges()} arêtes**",
        "",
        "## Types de nœuds",
        "",
        "| type | nombre |",
        "|---|---|",
        *[f"| {k} | {n} |" for k, n in kinds.most_common()],
        "",
        "## Relations",
        "",
        "| relation | nombre |",
        "|---|---|",
        *[f"| {r} | {n} |" for r, n in rels.most_common(25)],
        "",
        "## Confiance",
        "",
        "| niveau | nombre |",
        "|---|---|",
        *[f"| {c} | {n} |" for c, n in confs.most_common()],
        "",
    ]
    if avertissements:
        lignes += ["## Avertissements", ""] + [f"- {a}" for a in avertissements] + [""]
    STATS.write_text("\n".join(lignes), encoding="utf-8")

    print(f"\n  graphe composé : {G.number_of_nodes()} nœuds, {G.number_of_edges()} arêtes")
    print(f"  écrit : {SORTIE.relative_to(RACINE)} et {STATS.relative_to(RACINE)}")
    print(f"  archivé : {gz.relative_to(RACINE)}  ({gz.stat().st_size / 1e6:.1f} Mo, versionné)")
    if avertissements:
        print(f"\n  {len(avertissements)} avertissement(s) :")
        for a in avertissements[:10]:
            print(f"    - {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

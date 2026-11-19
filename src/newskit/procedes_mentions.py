#!/usr/bin/env python3
"""Les mentions sans modèle d'un procédé d'ingestion (procedes.py ; mainteneur, 15 et 19.11.2026 : « il n'y a que 2
papiers liés à xavier initialization? »).

L'extraction ne garde pas toutes les notions qu'un texte nomme. Pour chaque fiche d'un type de contenu qui déclare
`mentions: true`, ce passage pose dans le lot de couche 2 de la fiche :
- `mentions` du document vers une notion canonique que le corps de la fiche nomme et que le lot n'a pas ;
- `uses` d'une étape (nature `step`) vers une notion canonique que son libellé ou son texte nomme.

Une notion canonique : un nœud d'un lot du graphe, d'un type canonique de la convention, sans « __ » dans son
identifiant (propre à aucun document), qui n'est pas l'alias d'une autre (`alias` d'alias.json) ni un mot vide
(« not stated »). Son libellé se cherche à partir de deux mots ; les variantes que le média déclare sous `variantes`
d'alias.json se cherchent toujours. Chaque passage retire ce que le passage d'avant avait posé : il se relance sans
rien doubler. Une fiche réextraite perd ses mentions jusqu'au passage suivant.

Usage :
    python3 -m newskit.procedes_mentions --graphe ai-engineering
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from newskit import config_sources as cfg
from newskit import extraire_concepts as ec
from newskit import fiche
from newskit import procedes_passes as pp

CONTRIBUTEUR = "mentions (sans modèle)"
VIDES = re.compile(r"^(not stated|not specified|not reported|unknown|none|n/?a|other)$", re.I)
AUTOUR = 60  # signes de part et d'autre de la mention, dans l'extrait


def _alias(graphe: str) -> dict:
    p = cfg.chemins_du_graphe(graphe)["base"] / "alias.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def canoniques(lots: dict[str, dict], alias: dict[str, str], types: set[str]) -> dict[str, dict]:
    canon: dict[str, dict] = {}
    for lot in lots.values():
        for x in lot["nodes"]:
            if (
                x.get("nature") in types
                and "__" not in x["id"]
                and x["id"] not in alias
                and not VIDES.match(str(x.get("label", "")).strip())
            ):
                canon.setdefault(x["id"], {"id": x["id"], "label": str(x["label"]), "nature": x["nature"]})
    return canon


def _norme(s: str) -> str:
    return " ".join(s.casefold().split())


def motifs(canon: dict[str, dict], variantes: dict[str, list[str]]) -> tuple[re.Pattern | None, dict[str, list[str]]]:
    """Une expression pour tous les libellés cherchés, et pour chaque libellé normalisé ses notions."""
    noms: dict[str, list[str]] = {}
    for i, x in canon.items():
        for l in ([x["label"]] if len(x["label"].split()) >= 2 else []) + list(variantes.get(i, [])):
            ids = noms.setdefault(_norme(l), [])
            if i not in ids:
                ids.append(i)
    if not noms:
        return None, noms
    alternatives = sorted({r"\s+".join(map(re.escape, l.split())) for l in noms}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(alternatives) + r")(?![\w-])", re.I), noms


def trouver(texte: str, m: tuple[re.Pattern | None, dict[str, list[str]]]) -> dict[str, str]:
    """Les notions que le texte nomme, chacune avec l'extrait de sa première mention."""
    expr, noms = m
    out: dict[str, str] = {}
    if expr is None:
        return out
    for x in expr.finditer(texte):
        for i in noms[_norme(x.group(1))]:
            if i not in out:
                out[i] = " ".join(texte[max(0, x.start() - AUTOUR) : x.end() + AUTOUR].split())
    return out


def _arete(source: str, cible: str, relation: str, extrait: str) -> dict:
    return {
        "source": source,
        "target": cible,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "evidence": extrait,
        "weight": 1.0,
        "contributor": CONTRIBUTEUR,
    }


def poser(lot: dict, document: str, corps: str, m, canon: dict[str, dict]) -> dict[str, int]:
    """Les mentions et les emplois d'un lot, ceux du passage d'avant retirés."""
    lot["nodes"] = [x for x in lot["nodes"] if x.get("contributor") != CONTRIBUTEUR]
    lot["edges"] = [x for x in lot["edges"] if x.get("contributor") != CONTRIBUTEUR]
    presents = {x["id"] for x in lot["nodes"]}
    aretes = {(e["source"], e["target"]) for e in lot["edges"]}
    etapes = [x for x in lot["nodes"] if x.get("nature") == "step"]

    def noeud(i: str) -> None:
        if i not in presents:
            lot["nodes"].append({**canon[i], "file_type": "concept", "contributor": CONTRIBUTEUR})
            presents.add(i)

    compte = {"mentions": 0, "uses": 0}
    for i, extrait in trouver(corps, m).items():
        if i not in presents:
            noeud(i)
            lot["edges"].append(_arete(document, i, "mentions", extrait))
            compte["mentions"] += 1
    for e in etapes:
        for i, extrait in trouver(f"{e.get('label', '')}\n{e.get('rationale', '')}", m).items():
            if (e["id"], i) not in aretes:
                noeud(i)
                lot["edges"].append(_arete(e["id"], i, "uses", extrait))
                compte["uses"] += 1
    return compte


def passer(graphe: str) -> dict[str, int]:
    """Un passage sur les fiches des types à mentions du graphe ; le bilan."""
    ec.viser(graphe)
    decls = [d for d in pp.declaration_du_graphe(graphe).values() if d["mentions"]]
    bilan = {"notions": 0, "mentions": 0, "uses": 0, "lots": 0}
    if not decls:
        return bilan
    lots = {p: json.loads(p.read_text(encoding="utf-8")) for p in sorted(ec.LOTS.glob("*.json"))}
    tables = _alias(graphe)
    types = set().union(*(d["convention"]["canoniques"] for d in decls))
    canon = canoniques(lots, tables.get("alias") or {}, types)
    m = motifs(canon, tables.get("variantes") or {})
    bilan["notions"] = len(canon)
    for d in decls:
        for chemin, champs, _ in pp.fiches(graphe, d):
            document = ec.identifiant_de_fiche(chemin, champs)
            p = ec.LOTS / f"{document}.json"
            if p not in lots:
                continue
            avant = json.dumps(lots[p], sort_keys=True)
            compte = poser(lots[p], document, fiche.lire_fichier(chemin)[1], m, canon)
            bilan["mentions"] += compte["mentions"]
            bilan["uses"] += compte["uses"]
            if json.dumps(lots[p], sort_keys=True) != avant:
                p.write_text(json.dumps(lots[p], ensure_ascii=False, indent=1), encoding="utf-8")
                bilan["lots"] += 1
    return bilan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graphe", required=True)
    a = ap.parse_args(argv)
    b = passer(a.graphe)
    print(
        f"{a.graphe} : {b['notions']} notions canoniques, {b['mentions']} mentions, {b['uses']} emplois d'étape, "
        f"{b['lots']} lots réécrits"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

#!/usr/bin/env python3
"""Le partage fiche par fiche d'un graphe qui en reprend d'autres.

Un graphe déclaré avec `inclut` lit les dossiers et les lots d'extraction des graphes qu'il nomme (config_sources). Avec
`partage: {fichier: <chemin relatif à la racine>, garder: <destination>}` au registre, il ne garde, de ces fiches, que
celles que le rangement (newskit.partage_sources) destine à `garder`, plus les fiches d'autres graphes rangées chez lui ;
une fiche collectée après le rangement reste au graphe de sa source. Rien n'est déplacé ni réextrait : les graphes
inclus gardent leurs fiches, leurs lots et leurs routines.

Écrit pour la fusion de GV4 (CHANTIER_GOUVERNANCE.md, 1er novembre 2026), mais générique : aucun nom de graphe ici.

Ce module porte aussi les trois dédoublonnages de la fusion qui se font sans modèle :
- deux fiches d'une même adresse normalisée, ou d'un même corps, n'en font qu'une, la plus ancienne ;
- les tables d'alias des graphes inclus se fondent en une, chaînes résolues, désaccords listés ;
- les restes de page devenus notions (« Read Next », « Authors ») sortent du graphe composé.

    python3 -m newskit.partage_graphe --graphe <nom> --bilan     # destinations, doublons, alias : rien d'écrit
    python3 -m newskit.partage_graphe --graphe <nom> --alias     # écrit graphe/<nom>/alias.json (table fondue)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CORPS_MIN = 200      # caractères : sous ce seuil, deux corps égaux sont des pages vides ou des renvois, pas des doublons

# Les intertitres et les liens de service que l'extraction a pris pour des notions (mesuré le 1er novembre 2026 sur
# les nuages des pages par thème). Une liste fermée : un libellé qui n'y est pas reste, même s'il ressemble à un titre.
NAVIGATION = {"latest commit", "folders and files", "repository files navigation", "share", "read next", "topics", "similar posts", "take action", "this week’s overview", "this week's overview",
              "bibtex formatted citation", "access paper", "featured articles", "filed under", "analyses", "related",
              "subscribe", "support our work through substack", "but first, our call-to-action this week", "comments",
              "newsletter", "tags", "categories", "read more", "about", "home", "menu", "search", "contact",
              "about the author", "about the authors", "authors", "articles", "events", "research", "references",
              "conclusion", "background", "background information", "recommendations", "key takeaways",
              "executive summary", "origin of the case", "summary of the decision", "decision", "latest news",
              "downloadable documents", "introduction", "summary", "overview", "related stories", "content types",
              "explore", "dates", "contributors", "guest authors", "bookmark", "submission history",
              "references & citations", "current browse context", "related posts", "read next"}
PARAMETRES_DE_SUIVI = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|source$)")


def retenue(partage: dict[str, str], garder: str, chemin: str, propre: bool) -> bool:
    """Une fiche reste au graphe si le rangement la lui destine ; sans rangement (collectée après), si elle vient d'un
    de ses dossiers (`propre`)."""
    d = partage.get(chemin)
    if d is None:
        return propre
    return d.split(":", 1)[0] == garder


def ajouts(partage: dict[str, str], garder: str, dossiers: list[str]) -> list[str]:
    """Les fiches rangées chez le graphe qui ne sont dans aucun de ses dossiers (une source d'un autre graphe)."""
    pref = tuple(d.rstrip("/") + "/" for d in dossiers)
    return sorted(c for c, d in partage.items() if d.split(":", 1)[0] == garder and not c.startswith(pref))


def url_normalisee(url: str) -> str:
    """Schéma et « www. » ignorés, hôte en minuscules, sans paramètres de suivi ni barre finale ; le fragment reste."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    hote = (p.hostname or "").lower().removeprefix("www.")
    q = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not PARAMETRES_DE_SUIVI.match(k)))
    return urlunsplit(("", hote, p.path.rstrip("/"), q, p.fragment))     # le fragment désigne l'article d'une loi


def _empreinte(corps: str) -> str | None:
    t = " ".join((corps or "").split()).lower()
    return hashlib.sha256(t.encode()).hexdigest() if len(t) >= CORPS_MIN else None


def _mots(texte: str) -> set[str]:
    return set(re.findall(r"\w+", (texte or "").lower()))


CORPS_PROCHES = 0.9      # part des mots communs à deux corps (Jaccard). Mesuré le 01.11.2026 : vrais doublons 1,0 ; articles distincts de la Convention du Conseil de l'Europe jusqu'à 0,69


def _meme_texte(f: dict, g: dict) -> bool:
    return _jaccard(_mots(f.get("corps") or ""), _mots(g.get("corps") or "")) >= CORPS_PROCHES


def _jaccard(x: set, y: set) -> float:
    return len(x & y) / len(x | y) if x and y else 0.0


def doublons(fiches: list[dict]) -> set[str]:
    """Les fiches à retirer, la plus ancienne de chaque doublon restant (date, puis chemin) :
    - un même corps (au moins CORPS_MIN caractères) ;
    - une même adresse normalisée ET des corps presque identiques (CORPS_PROCHES) : un texte de référence publie souvent
      tous ses articles sous une adresse et un titre (1er novembre 2026 : la Convention du Conseil de l'Europe, le NIST
      AI RMF), et une page republiée change à peine."""
    def ordre(f):
        return (f.get("date") or "9999", f["chemin"])
    out = set()
    par_corps: dict[str, list[dict]] = defaultdict(list)
    par_url: dict[str, list[dict]] = defaultdict(list)
    for f in fiches:
        if e := _empreinte(f.get("corps") or ""):
            par_corps[e].append(f)
        if u := url_normalisee(f.get("url") or ""):
            par_url[u].append(f)
    for fs in par_corps.values():
        fs = sorted(fs, key=ordre)
        out |= {f["chemin"] for f in fs[1:]}
    # Un même papier arXiv : la couche 1 en fait un seul nœud (l'identifiant arXiv), la fiche de premier chemin
    # (05.11.2026 : 2302.12173 en fiche arXiv et en page de l'éclaireur, contrôle de fraîcheur en échec).
    par_arxiv: dict[str, list[str]] = defaultdict(list)
    for f in fiches:
        if a := str(f.get("arxiv_id") or "").strip():
            par_arxiv[a].append(f["chemin"])
    for cs in par_arxiv.values():
        out |= set(sorted(cs)[1:])
    for fs in par_url.values():
        gardees: list[dict] = []
        for f in sorted(fs, key=ordre):
            if any(_meme_texte(f, g) for g in gardees):
                out.add(f["chemin"])
            else:
                gardees.append(f)
    return out


def _relatif(source_file: str, racine: str) -> str:
    s = str(source_file or "")
    r = str(racine).rstrip("/") + "/"
    return s[len(r):] if s.startswith(r) else s


def retirer_fiches(G, exclues: set[str], racine) -> tuple[int, int]:
    """Retire du graphe composé ce qui vient des fiches exclues : les arêtes qu'elles attestent, leurs nœuds, puis les
    notions que ce retrait laisse sans arête (une notion isolée avant le retrait reste). Rend (nœuds, arêtes) retirés."""
    if not exclues:
        return 0, 0
    aretes = [(u, v) for u, v, d in G.edges(data=True) if _relatif(d.get("source_file"), racine) in exclues]
    touches = {x for e in aretes for x in e}
    G.remove_edges_from(aretes)
    noeuds = [n for n, d in G.nodes(data=True) if _relatif(d.get("source_file"), racine) in exclues]
    touches |= {v for n in noeuds for v in G.neighbors(n)}
    G.remove_nodes_from(noeuds)
    orphelins = [n for n in touches if n in G and G.degree(n) == 0]
    G.remove_nodes_from(orphelins)
    return len(noeuds) + len(orphelins), len(aretes)


def lot_retenu(lot: dict, retenues: set[str], racine) -> bool:
    """Un lot d'extraction par fiche se lit si sa fiche est retenue (ses nœuds portent son chemin)."""
    return any(_relatif(n.get("source_file"), racine) in retenues for n in lot.get("nodes") or [])


def exclues(G, retenues: set[str], racine) -> set[str]:
    """Les fiches que le graphe composé porte, par ses nœuds ou ses arêtes, et qui ne sont pas retenues."""
    vues = {_relatif(d.get("source_file"), racine) for _, d in G.nodes(data=True)}
    vues |= {_relatif(d.get("source_file"), racine) for _, _, d in G.edges(data=True)}
    return {v for v in vues if v.endswith(".md") and v not in retenues}


def fondre_tables(tables: list[dict[str, str]]) -> tuple[dict[str, str], list[list[str]]]:
    """Une table d'alias à partir de plusieurs : chaque groupe relié par des alias n'a qu'un canonique. Le canonique est
    le seul nœud du groupe qui n'est l'alias de rien ; s'il y en a plusieurs, les tables divergent : le plus désigné
    gagne (puis l'ordre alphabétique) et le groupe est listé ; s'il n'y en a aucun (une boucle), le premier par ordre
    alphabétique."""
    parent: dict[str, str] = {}

    def racine(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    designes, perdus = Counter(), set()
    for t in tables:
        for perdu, garde in t.items():
            if perdu == garde:
                continue
            parent[racine(perdu)] = racine(garde)
            designes[garde] += 1
            perdus.add(perdu)
    groupes = defaultdict(set)
    for x in list(parent):
        groupes[racine(x)].add(x)
    alias, conflits = {}, []
    for membres in groupes.values():
        terminaux = sorted(m for m in membres if m not in perdus)
        if len(terminaux) > 1:
            conflits.append(sorted(membres))
        canon = (min(terminaux, key=lambda m: (-designes[m], m)) if terminaux else min(membres))
        alias.update({m: canon for m in membres if m != canon})
    return dict(sorted(alias.items())), sorted(conflits)


def fondre_refus(tables: list[dict[str, str]]) -> dict[str, str]:
    """Les paires déjà tranchées « pas la même notion » des tables (« a|b » → date), pour que le dédoublonnage du graphe
    fusionné ne les redemande pas ; la date la plus ancienne reste."""
    out: dict[str, str] = {}
    for t in tables:
        for paire, date in t.items():
            out[paire] = min(out.get(paire, date), date)
    return dict(sorted(out.items()))


def semer_pyramide(etats: list[dict], alias: dict[str, str], present: set[str]) -> dict[str, dict]:
    """Les synthèses des graphes inclus, sous leur identifiant canonique, pour la pyramide du graphe fusionné. Une
    notion sortie du graphe fusionné n'est pas reprise ; deux synthèses d'une même notion : la plus reliée. L'empreinte
    reste : `synthese.py mettre-a-jour` réécrit alors les seules synthèses dont les preuves ont changé."""
    out: dict[str, dict] = {}
    for etat in etats:
        for n, v in etat.get("notions", {}).items():
            c = alias.get(n, n)
            if c in present and (c not in out or v.get("degre", 0) > out[c].get("degre", 0)):
                out[c] = v
    return out


def elaguer_pyramide(etat: dict, present: set[str]) -> dict:
    """Retire de chaque synthèse les phrases dont aucune référence n'est une fiche du graphe (mainteneur, 01.11.2026 : « retire
    d'abord » ; 2 511 synthèses semées citaient une fiche sortie par le partage). Une phrase sans référence reste. Une
    synthèse touchée note `elaguee` (phrases d'origine, phrases gardées) : la tenue la refait d'abord, et une
    synthèse réécrite perd la marque. Rend le bilan."""
    bilan = {"intactes": 0, "élaguées": 0, "vidées": 0, "phrases_retirees": 0}
    for v in etat.get("notions", {}).values():
        phrases = v.get("phrases") or []
        gardees = [p for p in phrases if not p.get("refs") or any(r.get("fiche") in present for r in p["refs"])]
        if len(gardees) == len(phrases):
            bilan["intactes"] += 1
            continue
        avant = (v.get("elaguee") or {}).get("avant", len(phrases))
        v["phrases"], v["elaguee"] = gardees, {"avant": avant, "apres": len(gardees)}
        bilan["vidées" if not gardees else "élaguées"] += 1
        bilan["phrases_retirees"] += len(phrases) - len(gardees)
    return bilan


def reste_de_page(label: str) -> bool:
    return " ".join((label or "").split()).lower() in NAVIGATION


def retirer_restes_de_page(G) -> int:
    """Retire les notions (nœuds sans `kind`) dont le libellé est un reste de page ; une fiche garde son titre."""
    noeuds = [n for n, d in G.nodes(data=True) if not d.get("kind") and reste_de_page(d.get("label") or "")]
    G.remove_nodes_from(noeuds)
    return len(noeuds)


def bilan(partage: dict[str, str], fiches: list[str]) -> dict:
    """Chaque fiche des graphes inclus a-t-elle une destination ? Et combien vont où ?"""
    sans = sorted(f for f in fiches if f not in partage)
    dest = Counter(partage[f].split(":", 1)[0] for f in fiches if f in partage)
    return {"sans_destination": sans, "destinations": dict(dest)}


# --- Le registre ------------------------------------------------------------------------------

def declaration(graphe: str) -> tuple[dict[str, str], str] | None:
    """Le rangement et la destination gardée, si le graphe déclare `partage` ; sinon None."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    p = ((cfg.charger().get("graphes") or {}).get(graphe) or {}).get("partage")
    if not p:
        return None
    f = cfg.RACINE / p["fichier"]
    return json.loads(f.read_text(encoding="utf-8")), p["garder"]


def fiche_lue(r: str, c: Path) -> dict:
    """Ce que `doublons` lit d'une fiche : chemin, adresse, titre, corps, date et identifiant arXiv."""
    from newskit import fiche  # noqa: PLC0415
    entete, corps = fiche.lire_fichier(c)
    return {"chemin": r, "url": str(entete.get("url") or ""), "titre": str(entete.get("title") or ""), "corps": corps,
            "date": str(entete.get("date") or ""), "arxiv_id": str(entete.get("arxiv_id") or "").strip()}


def fiches_du_graphe(graphe: str) -> dict[str, Path] | None:
    """Les fiches du graphe, par chemin relatif, rangement et doublons appliqués ; None sans `partage` (le graphe lit
    alors ses dossiers tels quels)."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    d = declaration(graphe)
    if d is None:
        return None
    partage, garder = d
    dossiers = cfg.dossiers_du_graphe(graphe)
    chemins = {str(c.relative_to(cfg.RACINE)): c for dd in dossiers for c in (cfg.RACINE / dd).rglob("*.md")
               if not c.name.startswith("_")}
    gardees = {r: c for r, c in chemins.items() if retenue(partage, garder, r, True)}
    for r in ajouts(partage, garder, dossiers):
        if (cfg.RACINE / r).exists():
            gardees[r] = cfg.RACINE / r
    lues = [fiche_lue(r, c) for r, c in gardees.items()]
    for r in doublons(lues):
        gardees.pop(r, None)
    return gardees


def main(argv: list[str] | None = None) -> int:
    from newskit import config_sources as cfg  # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--bilan", action="store_true")
    ap.add_argument("--alias", action="store_true")
    ap.add_argument("--elaguer", action="store_true",
                    help="retirer de la pyramide les phrases dont aucune référence n'est une fiche du graphe (copie gardée)")
    ap.add_argument("--pyramide", action="store_true",
                    help="semer la pyramide du graphe fusionné avec les synthèses des graphes inclus (jamais par-dessus une pyramide)")
    a = ap.parse_args(argv)
    d = declaration(a.graphe)
    if d is None:
        print(f"  {a.graphe} ne déclare pas de partage", file=sys.stderr)
        return 1
    partage, garder = d
    inclus = cfg.graphes_inclus(a.graphe)
    if a.bilan:
        toutes = [str(c.relative_to(cfg.RACINE)) for g in inclus for dd in cfg.dossiers_du_graphe(g, propres=True)
                  for c in (cfg.RACINE / dd).rglob("*.md") if not c.name.startswith("_")]
        b = bilan(partage, toutes)
        gardees = fiches_du_graphe(a.graphe) or {}
        print(f"  fiches des graphes inclus : {len(toutes)} ; destinations : {b['destinations']} ; "
              f"sans destination (collectées après le rangement) : {len(b['sans_destination'])}")
        print(f"  fiches gardées par {a.graphe}, doublons retirés : {len(gardees)}")
    if a.elaguer:
        base = cfg.chemins_du_graphe(a.graphe)["base"]
        p = base / "synthese" / "notions.json"
        etat = json.loads(p.read_text(encoding="utf-8"))
        copie = p.with_name("notions.avant_elagage.json")
        if not copie.exists():
            copie.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        present = {_relatif(n.get("source_file"), cfg.RACINE)
                   for n in json.loads((base / "graphe.json").read_text(encoding="utf-8"))["nodes"]} - {None, ""}
        b = elaguer_pyramide(etat, present)
        p.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  pyramide élaguée : {b} ; copie d'avant : {copie}")
        return 0
    if a.pyramide:
        base = cfg.chemins_du_graphe(a.graphe)["base"]
        sortie = base / "synthese" / "notions.json"
        if sortie.exists():
            print(f"  {sortie} existe : rien n'est semé", file=sys.stderr)
            return 1
        tab = json.loads((base / "alias.json").read_text(encoding="utf-8")).get("alias", {})
        present = {n["id"] for n in json.loads((base / "graphe.json").read_text(encoding="utf-8"))["nodes"]}
        etats = [json.loads(p.read_text(encoding="utf-8")) for g in inclus
                 if (p := cfg.chemins_du_graphe(g)["base"] / "synthese" / "notions.json").exists()]
        notions = semer_pyramide(etats, tab, present)
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(json.dumps({"notions": notions, "phrases": 0, "retirees": 0, "echecs": {}}, ensure_ascii=False),
                          encoding="utf-8")
        print(f"  pyramide semée : {len(notions)} synthèse(s) reprise(s) de {len(etats)} graphe(s) → {sortie}")
        return 0
    tables, refus = [], []
    for g in inclus:
        p = cfg.chemins_du_graphe(g)["base"] / "alias.json"
        if p.exists():
            x = json.loads(p.read_text(encoding="utf-8"))
            tables.append(x.get("alias", {}))
            refus.append(x.get("refuses", {}))
    alias, conflits = fondre_tables(tables)
    print(f"  alias : {sum(len(t) for t in tables)} dans {len(tables)} tables → {len(alias)} après fusion, {len(conflits)} désaccord(s)")
    for c in conflits[:20]:
        print(f"    désaccord : {', '.join(c)}")
    if a.alias:
        sortie = cfg.chemins_du_graphe(a.graphe)["base"] / "alias.json"
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(json.dumps({"alias": alias, "refuses": fondre_refus(refus), "desaccords": conflits, "depuis": inclus}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"  écrit : {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

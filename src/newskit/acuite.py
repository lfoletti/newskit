#!/usr/bin/env python3
"""L'acuité d'un graphe : ce qui rend lisible son écart avec un modèle de langage.

Le but des graphes est de dépasser un modèle en pertinence et en complétude (porteur, 04.10.2026).
Ce script réunit, par graphe, les nombres qui le disent, en une ligne et un fichier :

    banc           bonnes réponses sourcées sur un banc de questions, dans trois conditions — avec le
                   graphe, le modèle seul, le modèle doté de la recherche web (banc.py). La métrique de tête.
    canon          part de ce que le modèle tient pour essentiel et que le graphe porte (mesurer_avance.py).
    avance         part des notions récentes que le modèle dit ne pas connaître ; `utile` : celles qui sont
                   en plus dans le sujet.
    fraîcheur      date de la publication la plus récente que le graphe connaît, délai médian entre la
                   publication d'un article et sa collecte, fiches encore en attente d'extraction.
    couverture     par sujet déclaré : notions du canon portées, et manquantes.
    corroboration  part des notions attestées par au moins deux fiches, et par deux sources distinctes.
    lacunes        questions restées sans réponse : ouvertes, closes (lacunes.py).

Aucun appel à un modèle ici : le banc et le canon sont mesurés ailleurs, ce script les lit.

    python3 -m newskit.acuite --graphe <graphe>     # calcule, écrit graphe/<nom>/acuite.json, affiche
    python3 -m newskit.acuite --tous                 # tous les graphes, une ligne chacun
    python3 -m newskit.acuite --graphe <graphe> --etat   # relit sans recalculer

Chaque calcul ajoute une ligne à graphe/<nom>/acuite_historique.jsonl (une par jour) : c'est la pente
qui dit si la stratégie marche, pas le niveau. À un changement de modèle, les chiffres baissent d'un
coup — l'historique garde le modèle et son horizon pour qu'on ne le prenne pas pour une régression.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

RE_JOUR = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
# Les seuils du succès, convenus avec le porteur le 04.10.2026 : (minimum, succès). L'objectif commun aux
# graphes servis est la colonne « succès ». Avec trente questions, un écart de banc de moins de vingt à
# vingt-cinq points peut être du hasard : +25 est le plus petit écart qu'on puisse affirmer.
SEUILS = {
    "banc_ecart_modele": (0.15, 0.25),  # graphe − modèle seul, en part de réponses justes
    "banc_ecart_web": (-0.10, 0.0),  # graphe − modèle avec recherche web
    "banc_faux": (0.10, 0.05),  # réponses fausses du graphe : un plafond
    "canon": (0.80, 0.90),
    "avance_utile": (0.35, 0.50),
    "age_jours": (2, 1),  # un plafond
    "corrobore_centrales": (0.60, 0.80),  # les notions les plus reliées, attestées par deux sources
}
PLAFONDS = {"banc_faux", "age_jours"}
CENTRALES = 200  # notions les plus reliées, pour la corroboration du cœur du graphe
FENETRE = 30  # jours de collecte pris pour le délai de fraîcheur
DELAI_MAX = 14  # au-delà, la fiche est une archive remontée, pas une nouvelle


def lu(chemin: Path) -> dict:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def jour(texte: str) -> date | None:
    m = RE_JOUR.match(texte or "")
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def fraicheur(graphe: str, G, aujourdhui: date) -> dict:
    """La date la plus récente connue du graphe, et le délai entre publication et collecte des nouvelles."""
    dates = [jour(str(d.get("date") or "")) for _, d in G.nodes(data=True) if d.get("kind") in ("article", "papier")]
    dates = [d for d in dates if d and d <= aujourdhui]
    delais = []
    for dossier in cfg.dossiers_du_graphe(graphe):
        for f in (RACINE / dossier).glob("*.md"):
            publie = jour(f.name)
            m = re.search(
                r'^collected: "?(\d{4}-\d{2}-\d{2})', f.read_text(encoding="utf-8", errors="replace")[:1500], flags=re.M
            )
            collecte = jour(m.group(1)) if m else None
            if (
                publie
                and collecte
                and (aujourdhui - collecte).days <= FENETRE
                and 0 <= (collecte - publie).days <= DELAI_MAX
            ):
                delais.append((collecte - publie).days)
    r = subprocess.run(
        [sys.executable, str(CODE / "extraire_concepts.py"), "--graphe", graphe, "--etat"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        timeout=600,
    )
    m = re.search(r"(\d+) fiche\(s\) dense\(s\) en retard", r.stdout)
    return {
        "dernier": max(dates).isoformat() if dates else None,
        "age_jours": (aujourdhui - max(dates)).days if dates else None,
        "delai_median_jours": statistics.median(delais) if delais else None,
        "nouvelles_mesurees": len(delais),
        "en_attente_d_extraction": int(m.group(1)) if m else 0,
    }


def corroboration(G) -> dict:
    """Part des notions attestées par au moins deux fiches, et par au moins deux sources (dossiers) distinctes."""
    total = deux_fiches = deux_sources = 0
    par_noeud: list[tuple[int, bool]] = []
    for n in G:
        if dg._type(G, n) not in dg.TYPES_CONCEPT:
            continue
        fiches = {str(d.get("source_file") or "") for _, _, d in G.edges(n, data=True)} | {
            str(G.nodes[n].get("source_file") or "")
        }
        fiches.discard("")
        if not fiches:
            continue
        total += 1
        deux_fiches += len(fiches) >= 2
        plusieurs = len({str(Path(f).parent) for f in fiches}) >= 2
        deux_sources += plusieurs
        par_noeud.append((G.degree(n), plusieurs))
    # Le chiffre d'ensemble reste bas par nature : la longue traîne d'un domaine n'est citée qu'une fois.
    # Le cœur du graphe — ses notions les plus reliées — doit, lui, reposer sur plusieurs sources.
    centrales = sorted(par_noeud, reverse=True)[:CENTRALES]
    return {
        "notions": total,
        "deux_fiches": round(deux_fiches / total, 3) if total else None,
        "deux_sources": round(deux_sources / total, 3) if total else None,
        "centrales": round(sum(p for _, p in centrales) / len(centrales), 3) if centrales else None,
    }


def actualite(G) -> dict | None:
    """Ce que les textes de référence disent du présent des notions (diachronie.py, posé à la composition) : notions
    attestées par un texte de référence, part attestée par une version en vigueur, notions à lire comme périmées
    (version remplacée seulement), notions évoluant entre deux versions. None sans texte de référence."""
    c = {"reference": 0, "en_vigueur": 0, "remplacee_seulement": 0, "evolue": 0}
    for _, d in G.nodes(data=True):
        a = d.get("actualite")
        if a and "en_vigueur" in a:
            c["reference"] += 1
            c["en_vigueur"] += bool(a["en_vigueur"])
            c["remplacee_seulement"] += bool(a["remplacee_seulement"])
            c["evolue"] += bool(a["evolue"])
    if not c["reference"]:
        return None
    c["part_en_vigueur"] = round(c["en_vigueur"] / c["reference"], 3)
    return c


def calculer(graphe: str) -> dict | None:
    G = te.charger(graphe)
    if G is None:
        return None
    base = cfg.chemins_du_graphe(graphe)["base"]
    aujourdhui = date.today()
    avance, lac, banc = lu(base / "avance.json"), lu(base / "lacunes.json"), lu(base / "banc.json")
    a: dict = {
        "graphe": graphe,
        "date": aujourdhui.isoformat(),
        "modele": avance.get("modele") or te.MODELE,
        "horizon": avance.get("horizon") or cfg.HORIZON_MODELE,
        "fraicheur": fraicheur(graphe, G, aujourdhui),
        "corroboration": corroboration(G),
    }
    if act := actualite(G):
        a["actualite"] = act
    if avance.get("completude") is not None:
        soumises = sum(s["soumises"] for s in avance.get("sources", []))
        a["canon"] = {
            "completude": avance["completude"],
            "mesure_du": avance.get("date"),
            "sujets": [
                {"sujet": s["sujet"], "portees": s["portees"], "canon": s["canon"], "absentes": s["absentes"]}
                for s in sorted(avance.get("sujets", []), key=lambda s: s["portees"] / s["canon"])
            ],
        }
        a["avance"] = {
            "posterieures": avance.get("posterieures"),
            "notions": avance.get("notions"),
            "inconnues": round(len(avance.get("inconnues_du_modele", [])) / avance["echantillon"], 3)
            if avance.get("echantillon")
            else None,
            "utiles": round(sum(s["utiles"] for s in avance.get("sources", [])) / soumises, 3) if soumises else None,
        }
    if lac:
        semaine = sum(
            1
            for x in lac.get("closes", {}).values()
            if (j := jour(str(x.get("close_le") or ""))) and (aujourdhui - j).days <= 7
        )
        a["lacunes"] = {
            "ouvertes": len(lac.get("ouvertes", {})),
            "closes_7_jours": semaine,
            "a_trancher": len(lac.get("a_trancher", {})),
        }
    if banc.get("conditions"):
        a["banc"] = {
            "mesure_du": banc.get("date"),
            "questions": banc.get("questions"),
            "conditions": banc["conditions"],
            "methode": banc.get("methode") or "ancienne",
            "restitution": banc.get("restitution"),
            "fond": banc.get("fond"),
            "par_type": banc.get("par_type"),
        }
    a["paliers"] = paliers(a)
    a["ligne"] = ligne(a)
    return a


def valeurs(a: dict) -> dict:
    """Les valeurs que les seuils jugent, tirées de l'acuité ; None quand la mesure manque."""
    c = (a.get("banc") or {}).get("conditions") or {}
    g, m, w = (c.get(k, {}).get("juste") for k in ("graphe", "modele", "web"))
    return {
        "banc_ecart_modele": None if g is None or m is None else round(g - m, 3),
        "banc_ecart_web": None if g is None or w is None else round(g - w, 3),
        "banc_faux": c.get("graphe", {}).get("faux"),
        "canon": (a.get("canon") or {}).get("completude"),
        "avance_utile": (a.get("avance") or {}).get("utiles"),
        "age_jours": (a.get("fraicheur") or {}).get("age_jours"),
        "corrobore_centrales": (a.get("corroboration") or {}).get("centrales"),
    }


def paliers(a: dict) -> dict:
    """Pour chaque seuil : « succès », « minimum », « en dessous », ou None si la mesure manque."""
    out = {}
    for k, v in valeurs(a).items():
        mini, succes = SEUILS[k]
        if v is None:
            out[k] = None
        elif k in PLAFONDS:
            out[k] = "succès" if v <= succes else "minimum" if v <= mini else "en dessous"
        else:
            out[k] = "succès" if v >= succes else "minimum" if v >= mini else "en dessous"
    return out


def pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def ligne(a: dict) -> str:
    """La synthèse en une ligne, toujours dans le même ordre : banc, socle, avance, fraîcheur, lacunes."""
    bouts = []
    c = (a.get("banc") or {}).get("conditions") or {}
    if c:
        # Juste · faux · ne sait pas : « ne sait pas » n'est ni l'un ni l'autre, et s'écrit.
        from newskit.banc import trois_parts  # noqa: PLC0415

        jf = lambda k: trois_parts(c.get(k) or {}) if (c.get(k) or {}).get("juste") is not None else "—"  # noqa: E731
        bouts.append(f"banc {jf('graphe')} | LLM {jf('modele')} | LLM+web {jf('web')} (juste · faux · ne sait pas)")
    if a.get("canon"):
        bouts.append(f"socle {pct(a['canon']['completude'])}")
    if (a.get("avance") or {}).get("utiles") is not None:
        bouts.append(f"avance utile {pct(a['avance']['utiles'])}")
    f = a.get("fraicheur") or {}
    if f.get("age_jours") is not None:
        bouts.append(
            f"frais à J-{f['age_jours']}"
            + (f", {f['en_attente_d_extraction']} en attente" if f.get("en_attente_d_extraction") else "")
        )
    if (a.get("corroboration") or {}).get("deux_sources") is not None:
        bouts.append(f"corroboré {pct(a['corroboration']['deux_sources'])}")
    if a.get("lacunes"):
        bouts.append(f"{a['lacunes']['ouvertes']} lacune(s)")
    return " · ".join(bouts) or "pas encore mesuré"


def ecrire(graphe: str, a: dict) -> None:
    base = cfg.chemins_du_graphe(graphe)["base"]
    (base / "acuite.json").write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    # Une ligne par jour dans l'historique : celle du jour remplace la précédente du même jour.
    h = base / "acuite_historique.jsonl"
    lignes = [
        l
        for l in (h.read_text(encoding="utf-8").splitlines() if h.exists() else [])
        if f'"date": "{a["date"]}"' not in l
    ]
    c = (a.get("banc") or {}).get("conditions") or {}
    point = {
        "date": a["date"],
        "modele": a["modele"],
        "horizon": a["horizon"],
        "banc_graphe": c.get("graphe", {}).get("juste"),
        "banc_modele": c.get("modele", {}).get("juste"),
        "banc_web": c.get("web", {}).get("juste"),
        "canon": (a.get("canon") or {}).get("completude"),
        "avance_utile": (a.get("avance") or {}).get("utiles"),
        "age_jours": a["fraicheur"]["age_jours"],
        "en_attente": a["fraicheur"]["en_attente_d_extraction"],
        "corrobore": a["corroboration"]["deux_sources"],
        "lacunes": (a.get("lacunes") or {}).get("ouvertes"),
    }
    h.write_text("\n".join([*lignes, json.dumps(point, ensure_ascii=False)]) + "\n", encoding="utf-8")


def afficher(a: dict, detail: bool = True) -> None:
    print(f"  {a['graphe']} — {a['ligne']}")
    if not detail:
        return
    f, c = a["fraicheur"], a["corroboration"]
    print(
        f"    fraîcheur     dernière publication connue : {f['dernier']} ; délai publication → collecte : "
        f"{f['delai_median_jours'] if f['delai_median_jours'] is not None else '—'} j (médiane sur {f['nouvelles_mesurees']} nouvelles) ; "
        f"{f['en_attente_d_extraction']} fiche(s) en attente d'extraction"
    )
    print(
        f"    corroboration {pct(c['deux_fiches'])} des {c['notions']} notions par au moins deux fiches, {pct(c['deux_sources'])} par deux "
        f"sources ; {pct(c.get('centrales'))} des {CENTRALES} notions les plus reliées"
    )
    if a.get("actualite"):
        x = a["actualite"]
        print(
            f"    actualité     {x['reference']} notions attestées par un texte de référence, {pct(x['part_en_vigueur'])} par une version "
            f"en vigueur ; {x['remplacee_seulement']} à lire comme périmée(s) (version remplacée seulement) ; {x['evolue']} évoluant "
            f"entre deux versions"
        )
    if a.get("canon"):
        print(
            f"    socle         {pct(a['canon']['completude'])} (LLM {a['modele']}, horizon {a['horizon']}, mesure du {a['canon']['mesure_du']})"
        )
        for s in a["canon"]["sujets"]:
            print(f"                  {s['portees']:2d}/{s['canon']:2d}  {s['sujet'][:72]}")
    if a.get("avance"):
        print(
            f"    avance        {pct(a['avance']['inconnues'])} des notions récentes inconnues du LLM, {pct(a['avance']['utiles'])} utiles (dans le sujet)"
        )
    if a.get("lacunes"):
        l = a["lacunes"]
        print(
            f"    lacunes       {l['ouvertes']} ouverte(s), {l['closes_7_jours']} close(s) en sept jours, {l['a_trancher']} à trancher"
        )
    v, noms = (
        valeurs(a),
        {
            "banc_ecart_modele": "banc, écart avec le LLM seul",
            "banc_ecart_web": "banc, écart avec le LLM + web",
            "banc_faux": "banc, taux d'erreur du graphe",
            "canon": "socle",
            "avance_utile": "avance utile",
            "age_jours": "fraîcheur (jours)",
            "corrobore_centrales": "corroboration du cœur",
        },
    )
    print(
        "    seuils        "
        + "  ".join(f"[{p or 'non mesuré'}] {noms[k]}" for k, p in (a.get("paliers") or {}).items())
    )
    atteints = [p for p in (a.get("paliers") or {}).values() if p]
    if atteints:
        print(
            f"                  {atteints.count('succès')} au succès, {atteints.count('minimum')} au minimum, "
            f"{atteints.count('en dessous')} en dessous, {len(a['paliers']) - len(atteints)} non mesuré(s)"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--tous", action="store_true", help="tous les graphes, une ligne chacun")
    ap.add_argument("--etat", action="store_true", help="relire la dernière mesure, sans recalculer")
    args = ap.parse_args()
    graphes = cfg.ordre_d_affichage() if args.tous else [cfg.nom_actuel(args.graphe)]
    for g in graphes:
        if args.etat:
            a = lu(cfg.chemins_du_graphe(g)["base"] / "acuite.json")
        else:
            a = calculer(g)
            if a:
                ecrire(g, a)
        if a:
            afficher(a, detail=not args.tous)
        else:
            print(f"  {g} : pas de graphe, ou pas encore de mesure")
    return 0


if __name__ == "__main__":
    sys.exit(main())

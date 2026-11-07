#!/usr/bin/env python3
"""Les points peu couverts d'un sujet déclaré, et leur remontée (porteur, 10.10.2026 : « les points peu couverts
remontant, niveau carte ou autrement »).

Le graphe sait déjà, en quatre endroits, ce qu'il couvre mal ; chacun restait dans son coin. Ici ils se rassemblent
par sujet, sans aucun appel au modèle, et remontent là où ils servent :

Quatre origines, toutes déjà mesurées :
    canon    les notions que le canon du modèle tient pour essentielles et que le graphe ne porte pas (avance.json,
             mesurer_avance.py ; voix critiques comprises) ;
    banc     les questions du banc par thème manquées à la dernière mesure (themes_banc_historique.jsonl), tant que
             leur page n'a pas été comblée (banc_themes.py --combler) ;
    lacune   les questions restées sans réponse (lacunes.py) que la carte route vers le sujet (cartes.router) ;
    carte    les sous-questions de la carte dont moins de REFERENCES_MIN fiches portent les notions : la carte les
             pose, le corpus les étaye à peine.

Trois remontées :
    carte      chaque carte reçoit `peu_couverts` (hors empreinte : la carte ne se réécrit pas pour cela) ; `repondre`
               cite, sous la carte, ceux qui touchent la question : le lecteur sait que le graphe couvre peu ce point,
               et s'abstient au lieu de broder ;
    éclaireur  pour le sujet du jour, l'éclaireur cherche ses points peu couverts (genre [peu couvert]) au lieu d'un
               « quoi de neuf » sans cible ; un point cherché attend REPIT jours avant de revenir ;
    rapport    graphe/<g>/peu_couverts.json ; une ligne au bilan de la routine ; la colonne « Peu couverts » du tableau
               de profondeur de la page d'accueil.

    python3 -m newskit.peu_couverts --graphe <graphe>            # calcule, écrit, affiche
    python3 -m newskit.peu_couverts --graphe <graphe> --etat     # affiche ce qui est écrit, sans recalcul
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import cartes  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import lacunes  # noqa: E402
from newskit import synthese as sy  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

REFERENCES_MIN = 3          # une sous-question de carte portée par moins de fiches est « à support mince »
PAR_SUJET = 12              # points gardés par sujet, les plus parlants d'abord (banc, lacune, canon, carte)
REPIT = 14                  # jours avant qu'un point cherché par l'éclaireur revienne dans sa rotation
ORDRE = {"banc": 0, "lacune": 1, "canon": 2, "carte": 3}


def chemin(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "peu_couverts.json"


def lire(graphe: str) -> dict:
    p = chemin(graphe)
    if not p.exists():
        return {"graphe": graphe, "date": "", "sujets": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"graphe": graphe, "date": "", "sujets": []}


def cle_de(point: str) -> str:
    return lacunes.cle_de(point)


def tete(sujet: str) -> str:
    """« AI Act : classification… » → « AI Act » ; sans « : », jusqu'à la première virgule."""
    t, _, _ = sujet.partition(" : ")
    if t == sujet and ", " in t:
        t = t.partition(", ")[0]
    return t.strip()


# --- Les quatre origines ---------------------------------------------------------------------------------------

def du_canon(graphe: str, sujets: list[str]) -> dict[str, list[dict]]:
    p = cfg.chemins_du_graphe(graphe)["base"] / "avance.json"
    out: dict[str, list[dict]] = {}
    if not p.exists():
        return out
    try:
        av = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for s in av.get("sujets") or []:
        nom = s.get("sujet")
        if nom not in sujets:
            continue
        for n in s.get("absentes") or []:
            out.setdefault(nom, []).append({"point": n, "origine": "canon"})
        for n in (s.get("critique") or {}).get("absentes") or []:
            out.setdefault(nom, []).append({"point": n, "origine": "canon", "detail": "voix critique"})
    return out


def du_banc(graphe: str, sujets: list[str]) -> dict[str, list[dict]]:
    base = cfg.chemins_du_graphe(graphe)["base"]
    p_h, p_q = base / "themes_banc_historique.jsonl", base / "themes_banc.json"
    out: dict[str, list[dict]] = {}
    if not (p_h.exists() and p_q.exists()):
        return out
    try:
        m = json.loads(p_h.read_text(encoding="utf-8").splitlines()[-1])
        par_i = {q["i"]: q for q in json.loads(p_q.read_text(encoding="utf-8"))}
    except (OSError, ValueError, IndexError, KeyError):
        return out
    for d in m.get("details") or []:
        q = par_i.get(d.get("i"))
        if not q or d.get("verdict") == "juste" or q.get("comblee"):
            continue
        s = q.get("theme") or q.get("sujet")
        if s in sujets:
            out.setdefault(s, []).append({"point": " ".join(str(q["question"]).split()), "origine": "banc",
                                          "detail": f"{d.get('verdict')} au banc du {str(m.get('horodatage'))[:10]}",
                                          "attendu": str(q.get("attendu") or "")[:300]})
    return out


def des_lacunes(graphe: str, sujets: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in lacunes.lire(graphe).get("ouvertes", {}).values():
        enonce = x.get("question") or x.get("termes") or ""
        if not enonce:
            continue
        try:
            c = cartes.router(graphe, enonce)
        except Exception:  # noqa: BLE001 — sans encodeur ni carte, pas de routage
            c = None
        if c and c.get("sujet") in sujets:
            out.setdefault(c["sujet"], []).append({"point": enonce, "origine": "lacune",
                                                  "detail": f"vue {x.get('vues', 1)} fois, {', '.join(x.get('origines', []))}"})
    return out


def des_cartes(graphe: str, sujets: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s, c in cartes.lire(graphe).items():
        if s not in sujets:
            continue
        for q in c.get("questions") or []:
            refs = q.get("references") or []
            if len(refs) < REFERENCES_MIN:
                out.setdefault(s, []).append({"point": q["q"], "origine": "carte",
                                              "detail": f"{len(refs)} fiche(s) pour {len(q.get('notions') or [])} notion(s)"})
    return out


# --- Le calcul et sa remontée ----------------------------------------------------------------------------------

def couverts_depuis(graphe: str, points: list[dict]) -> list[dict]:
    """Parmi des points « banc », ceux dont les faits attendus se lisent désormais dans le contexte assemblé pour la
    question (contexte.py, sans lecteur) : des pages ingérées depuis la mesure les couvrent, le point se retire sans
    attendre le prochain banc — sinon le lecteur, prévenu que le graphe couvre peu, s'abstiendrait sur un fait présent."""
    from newskit import contexte  # noqa: PLC0415
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    G = None
    out = []
    for p in points:
        faits = contexte.faits_attendus({"attendu": p.get("attendu", ""), "citation": ""})
        if not faits:
            continue
        G = G or sy.charger_graphe(graphe)
        plie = ig._plier(contexte.contexte_de(graphe, p["point"], G, False))
        part = sum(1 for f in faits if ig._plier(f) in plie) / len(faits)
        p["couverture"] = round(part, 2)
        if part >= contexte.SEUIL:
            out.append(p)
    return out


def calculer(graphe: str) -> dict:
    sujets = te.sujets_declares(graphe)
    ancien = {cle_de(p["point"]): p for s in lire(graphe).get("sujets", []) for p in s.get("points", [])}
    par_sujet: dict[str, list[dict]] = {}
    for origine in (du_banc, des_lacunes, du_canon, des_cartes):
        for s, pts in origine(graphe, sujets).items():
            par_sujet.setdefault(s, []).extend(pts)
    couverts = couverts_depuis(graphe, [p for pts in par_sujet.values() for p in pts if p["origine"] == "banc"])
    cles_couverts = {cle_de(p["point"]) for p in couverts}
    par_sujet = {s: [p for p in pts if cle_de(p["point"]) not in cles_couverts] for s, pts in par_sujet.items()}
    out = []
    for i, s in enumerate(sujets):
        vus: set[str] = set(); pts = []
        for p in sorted(par_sujet.get(s, []), key=lambda p: ORDRE.get(p["origine"], 9)):
            k = cle_de(p["point"])
            if k in vus or not k:
                continue
            vus.add(k)
            if ancien.get(k, {}).get("cherche_le"):          # la mémoire de l'éclaireur survit au recalcul
                p["cherche_le"] = ancien[k]["cherche_le"]
            p["depuis"] = ancien.get(k, {}).get("depuis") or time.strftime("%Y-%m-%d")
            pts.append(p)
        out.append({"sujet": s, "indice": i, "tete": tete(s), "points": pts[:PAR_SUJET], "total": len(pts),
                    "par_origine": {o: sum(1 for p in pts if p["origine"] == o) for o in ORDRE}})
    return {"graphe": graphe, "date": time.strftime("%Y-%m-%d"), "sujets": out,
            "total": sum(x["total"] for x in out), "sujets_demunis": sum(1 for x in out if x["total"]),
            "couverts_depuis_le_banc": [{"point": p["point"], "couverture": p["couverture"]} for p in couverts]}


def ecrire(graphe: str, d: dict) -> None:
    chemin(graphe).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    # Remontée à la carte : hors empreinte, la carte ne se réécrit pas pour cela.
    cs = dict(cartes.lire(graphe))
    touche = False
    for x in d["sujets"]:
        if x["sujet"] in cs:
            neuf = [{"point": p["point"], "origine": p["origine"]} for p in x["points"]]
            if cs[x["sujet"]].get("peu_couverts") != neuf:
                cs[x["sujet"]]["peu_couverts"] = neuf
                touche = True
    if touche:
        cartes.ecrire(graphe, cs)


def pour_eclaireur(graphe: str, sujet: str, jour: str | None = None) -> str | None:
    """Le point peu couvert du sujet que l'éclaireur cherche aujourd'hui : le premier non cherché depuis REPIT jours,
    dans l'ordre banc, lacune, canon, carte ; None si le sujet n'en a pas (l'éclaireur cherche alors « quoi de neuf »)."""
    jour = jour or time.strftime("%Y-%m-%d")
    for x in lire(graphe).get("sujets", []):
        if x["sujet"] != sujet:
            continue
        for p in x["points"]:
            if p["origine"] in ("lacune", "canon"):
                continue                                   # déjà leurs voies : lacunes.a_chercher, combler_canon
            c = p.get("cherche_le")
            if not c or (time.mktime(time.strptime(jour, "%Y-%m-%d")) - time.mktime(time.strptime(c, "%Y-%m-%d"))) / 86400 >= REPIT:
                return p["point"]
    return None


def marquer(graphe: str, points: list[str], jour: str | None = None) -> None:
    """Après un passage de l'éclaireur : les points cherchés attendent REPIT jours."""
    jour = jour or time.strftime("%Y-%m-%d")
    d = lire(graphe)
    cles = {cle_de(p) for p in points}
    touche = False
    for x in d.get("sujets", []):
        for p in x["points"]:
            if cle_de(p["point"]) in cles:
                p["cherche_le"] = jour; p["essais"] = p.get("essais", 0) + 1; touche = True
    if touche:
        chemin(graphe).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def ligne(graphe: str, d: dict | None = None) -> str:
    """Une ligne pour le bilan de la routine."""
    d = d or lire(graphe)
    sujets = sorted((x for x in d.get("sujets", []) if x["total"]), key=lambda x: -x["total"])
    if not sujets:
        return f"  {graphe} : aucun point peu couvert"
    demunis = ", ".join(f"{x['tete']} ({x['total']})" for x in sujets[:3])
    couverts = len(d.get("couverts_depuis_le_banc") or [])
    par_o = {o: sum(x["par_origine"].get(o, 0) for x in d["sujets"]) for o in ORDRE}
    return (f"  {graphe} : {d.get('total', 0)} point(s) peu couvert(s) sur {len(sujets)} sujet(s) "
            f"(banc {par_o['banc']}, lacunes {par_o['lacune']}, canon {par_o['canon']}, cartes {par_o['carte']}) ; "
            f"les plus démunis : {demunis}" + (f" ; {couverts} question(s) du banc couverte(s) depuis" if couverts else ""))


def afficher(graphe: str, d: dict) -> None:
    for x in d["sujets"]:
        if not x["points"]:
            continue
        print(f"\n  {x['tete']} — {x['total']} point(s)")
        for p in x["points"]:
            print(f"    [{p['origine']}] {p['point'][:140]}" + (f"  ({p['detail']})" if p.get("detail") else "")
                  + (f"  cherché le {p['cherche_le']}" if p.get("cherche_le") else ""))
    print("\n" + ligne(graphe, d), flush=True)      # en dernier : la routine garde la fin de la sortie


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--etat", action="store_true", help="afficher sans recalculer")
    a = ap.parse_args()
    if a.etat:
        afficher(a.graphe, lire(a.graphe)); return 0
    d = calculer(a.graphe)
    ecrire(a.graphe, d)
    afficher(a.graphe, d)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Les cartes intermédiaires : une par sujet déclaré, entre le thème et la notion (CHANTIER_CARTES.md, 09.10.2026).

Le thème est une communauté du graphe, pas un sujet du porteur ; il se réécrit à chaque notion changée, et les
énumérations du droit (pratiques de l'article 5, rubriques de l'annexe IV, fonctions du NIST AI RMF) n'y ont pas de
lieu stable. Le passage se choisit par les mots, donc par le nombre : les textes de loi évinçaient les commentaires.
Une carte n'est PAS un niveau de prose : par sujet déclaré, les sous-questions qu'un spécialiste pose, les notions où
la réponse est, les fiches qui la fondent, les listes recopiées des synthèses (contrôlées mot pour mot) et la
chronologie du sujet. Elle est écrite par Sonnet d'après les synthèses existantes, sans lecture de fiche.

    python3 -m newskit.cartes rediger --graphe <graphe> [--plafond 20] [--seulement "<regex sur le sujet>"]
    python3 -m newskit.cartes etat --graphe <graphe>
    python3 -m newskit.cartes router --graphe <graphe> "quelles pratiques l'article 5 interdit-il ?"
    python3 -m newskit.cartes mesurer-routage --graphe <graphe>     # sur les questions des bancs au sujet connu

Sortie : graphe/<nom>/synthese/cartes.json. Les notions d'un sujet se choisissent sans appel : le canon du sujet déjà
demandé au modèle (avance_canon.json), puis l'encodeur local et les mots. La carte se réécrit quand l'empreinte des
synthèses de ses notions change ; au plus `--plafond` cartes par passage ; chaque appel au registre des coûts
(poste « cartes »).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import diachronie  # noqa: E402
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import synthese as sy  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

MODELE = sy.MODELE  # Sonnet : dresser une carte d'après des synthèses, c'est lire d'après des preuves
POSTE = "cartes"
NOTIONS_PAR_CARTE = 70  # synthèses données au rédacteur (≈ 360 caractères chacune : ≈ 8 000 tokens)
PLAFOND = 20  # cartes rédigées par passage (14 sujets pour un graphe de conformité)
COUVERTURE_LISTE = 0.6  # part des mots d'un élément de liste qui doivent être dans les synthèses citées
REFERENCES_PAR_QUESTION = 8
# Routage mesuré le 09.10.2026 sur 165 questions des bancs d'un graphe de conformité (faits de la réponse attendue retrouvés dans les
# notions rendues) : lexicales seules 0,51 ; carte seule à leur place 0,38-0,44 (elle remplace mal) ; lexicales + 3 notions
# de la carte 0,59 au seuil 0,48 (134 routées, 29 gagnées, 0 perdue), 0,60 au seuil 0,40. Un score par le contenu des
# notions (vecteurs stockés) n'ajoutait que +0,01 : le score reste celui de la sous-question seule, plus simple.
SEUIL_ROUTAGE = 0.48  # score (encodeur + mots) au-dessous duquel la question n'est pas routée ; voir mesurer-routage
POIDS_MOTS = 0.3  # part des mots communs dans le score de routage, à côté du cosinus de l'encodeur

MOIS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
MOIS_EN = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]

SCHEMA = {
    "type": "object",
    "required": ["questions", "chronologie"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["q", "notions"],
                "properties": {
                    "q": {"type": "string"},
                    "notions": {"type": "array", "items": {"type": "string"}},
                    "liste": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "chronologie": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["date", "fait", "notions"],
                "properties": {
                    "date": {"type": "string"},
                    "fait": {"type": "string"},
                    "notions": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


# --- Lire et écrire ---------------------------------------------------------------------------------------------


def chemin(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "synthese" / "cartes.json"


_CARTES: dict = {}


def lire(graphe: str) -> dict:
    """{sujet: carte}, relu quand le fichier change (le serveur vit longtemps)."""
    p = chemin(graphe)
    if not p.exists():
        return {}
    mt = p.stat().st_mtime
    if _CARTES.get(graphe, (None,))[0] != mt:
        try:
            _CARTES[graphe] = (mt, json.loads(p.read_text(encoding="utf-8")).get("cartes") or {})
        except (OSError, ValueError):
            _CARTES[graphe] = (mt, {})
    return _CARTES[graphe][1]


def ecrire(graphe: str, cartes: dict) -> None:
    p = chemin(graphe)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"cartes": cartes}, ensure_ascii=False, indent=1), encoding="utf-8")


def texte_notion(v: dict) -> str:
    return v["label"] + " " + " ".join(p["texte"] for p in v["phrases"])


# --- Choisir les notions d'un sujet, sans appel ---------------------------------------------------------------------


def canon_du_sujet(graphe: str, i: int) -> list[str]:
    """Les libellés que le modèle a dits canoniques pour ce sujet (avance_canon.json, déjà payé), canon et critique."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "avance_canon.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return []
    out: list[str] = []
    for bloc in ("canon", "critique"):
        for s in (d.get(bloc) or {}).get("sujets", []):
            if s.get("sujet") == i:
                out.extend(str(x) for x in s.get("notions", []))
    return out


def resoudre_libelle(libelle: str, formes: dict[str, list[str]]) -> str | None:
    """Le nœud synthétisé dont le libellé (ou un `aussi`) est ce libellé ; à défaut, le plus court qui le contient."""
    q = ig._plier(libelle)
    exacts = [n for n, fs in formes.items() if q in fs]
    if exacts:
        return exacts[0]
    partiels = [n for n, fs in formes.items() if any(q in f or (len(f) >= 6 and f in q) for f in fs)]
    if not partiels:
        return None
    return min(partiels, key=lambda n: len(formes[n][0]))


_VECTEURS: dict = {}


def vecteurs_des_notions(graphe: str, notions: dict):
    """{id: vecteur unitaire} des synthèses, par l'encodeur du graphe (sy.encodeur(graphe) : l'affiné retenu s'il existe,
    sinon celui de base) ; gardés dans cache/cartes/<graphe>.npz par empreinte, seuls les changés sont recalculés, tout
    est recalculé si l'encodeur change (son nom est dans le fichier). None sans encodeur.
    Le 15.10.2026, les cartes demandaient l'encodeur de base alors qu'un graphe de conformité a le sien : trois modèles chargés
    pour une question sur le serveur (615 Mo de plus), tué par l'OOM à 3 Go."""
    enc = sy.encodeur(graphe)
    if enc is None:
        return None
    import numpy as np  # noqa: PLC0415

    nom = getattr(enc, "nom", sy.ENCODEUR)
    p = RACINE / "cache" / "cartes" / f"{graphe}.npz"
    anciens: dict[str, tuple[str, object]] = {}
    if p.exists():
        try:
            z = np.load(p, allow_pickle=False)
            if "encodeur" in z and str(z["encodeur"]) == nom:
                for k, e, v in zip(z["ids"], z["empreintes"], z["E"]):
                    anciens[str(k)] = (str(e), v)
        except Exception:  # noqa: BLE001
            anciens = {}
    ids = list(notions)
    a_faire = [n for n in ids if anciens.get(n, ("",))[0] != notions[n].get("empreinte", "")]
    if a_faire:
        t0 = time.time()
        E = np.array(list(enc.embed([texte_notion(notions[n])[:1200] for n in a_faire])), dtype=np.float32)
        E /= np.linalg.norm(E, axis=1, keepdims=True)
        for n, v in zip(a_faire, E):
            anciens[n] = (notions[n].get("empreinte", ""), v.astype(np.float16))
        p.parent.mkdir(parents=True, exist_ok=True)
        gard = [n for n in ids]
        np.savez(
            p,
            ids=np.array(gard),
            empreintes=np.array([anciens[n][0] for n in gard]),
            E=np.array([anciens[n][1] for n in gard], dtype=np.float16),
            encodeur=np.array(nom),
        )
        print(f"  encodeur : {len(a_faire)} synthèse(s) vectorisée(s) en {time.time() - t0:.0f} s", flush=True)
    return {n: np.asarray(anciens[n][1], dtype=np.float32) for n in ids if n in anciens}


def vecteur(texte: str, graphe: str | None = None):
    """Le vecteur unitaire d'un texte par l'encodeur du graphe : le même que celui des vecteurs auxquels il se compare."""
    enc = sy.encodeur(graphe)
    if enc is None:
        return None
    import numpy as np  # noqa: PLC0415

    v = np.array(list(enc.embed([texte]))[0], dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def notions_du_sujet(
    graphe: str, G, notions: dict, i: int, sujet: str, vecteurs, combien: int = NOTIONS_PAR_CARTE
) -> list[str]:
    """Les notions synthétisées qui relèvent du sujet : d'abord celles du canon du sujet présentes dans la pyramide,
    puis les plus proches par l'encodeur (cosinus) et par les mots du sujet, jusqu'à `combien`."""
    # Une notion de la pyramide peut manquer au graphe (graphe reconstruit après elle) : elle garde son seul libellé.
    formes = {
        n: [ig._plier(v["label"]), *(ig._plier(a) for a in ((G.nodes[n].get("aussi") or []) if n in G else []))]
        for n, v in notions.items()
    }
    canon: list[str] = []
    for lib in canon_du_sujet(graphe, i):
        n = resoudre_libelle(lib, formes)
        if n and n not in canon:
            canon.append(n)
    ms = sy.mots(sujet)
    scores: dict[str, float] = {}
    vs = vecteur(sujet, graphe) if vecteurs else None
    for n, v in notions.items():
        s = len(ms & sy.mots(texte_notion(v))) * 0.03
        if vs is not None and n in vecteurs:
            s += float(vecteurs[n] @ vs)
        scores[n] = s
    reste = [n for n in sorted(scores, key=lambda n: -scores[n]) if n not in canon]
    return (canon + reste)[:combien]


# --- Le contrôle mécanique : une liste se recopie, une date se retrouve ---------------------------------------------


def formes_date(date: str) -> list[str]:
    """Les écritures d'une date qu'une synthèse peut porter : 2026-07-27, 27.07.2026, 27 juillet 2026, July 27, 2026…"""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", date.strip())
    if not m:
        return []
    a, mo, j = m.group(1), m.group(2), m.group(3)
    if not mo:
        return [a]
    k = int(mo) - 1
    if not 0 <= k < 12:
        return [a]
    if not j:
        return [f"{a}-{mo}", f"{MOIS_FR[k]} {a}", f"{MOIS_EN[k]} {a}", f"{mo}.{a}", f"{mo}/{a}"]
    jj, j1 = j, str(int(j))
    return [
        f"{a}-{mo}-{jj}",
        f"{jj}.{mo}.{a}",
        f"{j1}.{mo}.{a}",
        f"{j1}.{int(mo)}.{a}",
        f"{jj}/{mo}/{a}",
        f"{j1}/{int(mo)}/{a}",
        f"{j1} {MOIS_FR[k]} {a}",
        f"{j1}er {MOIS_FR[k]} {a}",
        f"{j1} {MOIS_EN[k]} {a}",
        f"{MOIS_EN[k]} {j1}, {a}",
        f"{MOIS_EN[k]} {jj}, {a}",
    ]


def couverture(texte: str, sources_pliees: str) -> float:
    m = sy.mots(texte)
    if not m:
        return 1.0
    return sum(1 for t in m if re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", sources_pliees)) / len(m)


def references_de(G, notions: dict, ids: list[str]) -> list[str]:
    """Les fiches citées par les synthèses de ces notions : les textes de référence en vigueur d'abord, puis les
    commentaires par nombre de citations ; une version remplacée n'y entre pas."""
    compte: dict[str, int] = {}
    for n in ids:
        for ph in notions[n]["phrases"]:
            for r in ph["refs"]:
                compte[r["fiche"]] = compte.get(r["fiche"], 0) + 1

    def cle(f: str):
        d = sy._fiche_de(G, f)
        return (0 if d.get("statut") == diachronie.EN_VIGUEUR else 1, -compte[f], f)

    fs = [f for f in compte if sy._fiche_de(G, f).get("statut") != diachronie.REMPLACEE]
    return sorted(fs, key=cle)[:REFERENCES_PAR_QUESTION]


def controler(rep: dict, ids: list[str], notions: dict, G) -> tuple[list[dict], list[dict], dict]:
    """Ce que la carte garde de la réponse du modèle : des sous-questions aux notions connues, des listes dont chaque
    élément est dans les synthèses citées (faits mot pour mot, et COUVERTURE_LISTE des mots), des dates qui s'y
    retrouvent. Rend (questions, chronologie, compte des retraits)."""
    connus = set(ids)
    retraits = {"questions": 0, "elements": 0, "dates": 0}
    questions: list[dict] = []
    for x in rep.get("questions", []):
        q = " ".join(str(x.get("q") or "").split())
        ns = [n for n in dict.fromkeys(x.get("notions") or []) if n in connus]
        if not q or not ns:
            retraits["questions"] += 1
            continue
        sources = " ".join(texte_notion(notions[n]) for n in ns)
        pliees = ig._plier(sources)
        liste, retires = [], 0
        for el in x.get("liste") or []:
            el = " ".join(str(el or "").split())
            if el and not sy.verifier(el, sources) and couverture(el, pliees) >= COUVERTURE_LISTE:
                liste.append(el)
            elif el:
                retires += 1
        retraits["elements"] += retires
        sq = {"q": q, "notions": ns, "references": references_de(G, notions, ns)}
        if liste:
            sq["liste"] = liste
            if retires:
                sq["liste_retires"] = retires
        questions.append(sq)
    chrono: list[dict] = []
    vues: set[tuple[str, str]] = set()
    for x in rep.get("chronologie", []):
        date = str(x.get("date") or "").strip()
        fait = " ".join(str(x.get("fait") or "").split())
        ns = [n for n in dict.fromkeys(x.get("notions") or []) if n in connus]
        formes = formes_date(date)
        if not formes or not fait or not ns:
            retraits["dates"] += 1
            continue
        sources = " ".join(texte_notion(notions[n]) for n in ns)
        pliees = ig._plier(sources)
        if not any(ig._plier(f) in pliees for f in formes) or sy.verifier(fait, sources + " " + " ".join(formes)):
            retraits["dates"] += 1
            continue
        if (date, ig._plier(fait)[:60]) in vues:
            continue
        vues.add((date, ig._plier(fait)[:60]))
        chrono.append({"date": date, "fait": fait, "notions": ns})
    chrono.sort(key=lambda e: e["date"])
    return questions, chrono, retraits


# --- Rédiger ---------------------------------------------------------------------------------------------------------


def empreinte_de(sujet: str, ids: list[str], notions: dict) -> str:
    corps = json.dumps([sujet, sorted((n, notions[n].get("empreinte", "")) for n in ids)], ensure_ascii=False)
    return hashlib.sha1(corps.encode()).hexdigest()[:16]


def prompt_de(decl: dict, sujet: str, G, notions: dict, ids: list[str]) -> str:
    blocs = []
    for n in ids:
        v = notions[n]
        a = v.get("actualite") or {}
        note = " (NOTION QUI ÉVOLUE entre deux versions du texte)" if a.get("evolue") else ""
        blocs.append(f"[{n}] « {v['label']} »{note} : " + " ".join(p["texte"] for p in v["phrases"]))
    return (
        f"Domaine : « {decl['libelle']} ». Sujet : « {sujet} ».\n"
        "Voici les synthèses des notions du graphe qui relèvent de ce sujet, chacune sous son identifiant [entre crochets]. "
        "Dresse la CARTE du sujet — sans rédiger de prose :\n"
        "1. `questions` : les 6 à 12 sous-questions qu'un juriste, un auditeur ou un responsable de conformité pose sur ce "
        "sujet ET auxquelles ces synthèses répondent — précises, en français, une par point distinct (classification, "
        "obligations, délais, sanctions, exceptions, autorités, décisions, évolutions). Chacune porte `notions` : les "
        "identifiants exacts (2 à 6) des notions où la réponse est. Ne pose pas de sous-question à laquelle les synthèses "
        "ne répondent pas.\n"
        "2. `liste` (facultatif) : quand les notions citées portent une ÉNUMÉRATION (pratiques interdites, rubriques, "
        "conditions, obligations, fonctions, catégories, étapes, délais, montants), recopie-la, un élément par entrée, "
        "avec les mots mêmes des synthèses, noms, chiffres et dates à l'identique, sans rien ajouter de ta mémoire ; "
        "sans énumération dans les synthèses, pas de liste.\n"
        "3. `chronologie` : les faits datés que ces synthèses portent (entrée en vigueur, échéance, publication, décision, "
        "version remplacée, délai reporté) — `date` au format AAAA-MM-JJ, ou AAAA-MM, ou AAAA quand la synthèse n'en dit "
        "pas plus ; `fait` en une ligne avec les mots de la synthèse ; `notions` : les identifiants qui le portent. "
        "Rien que ce que les synthèses disent.\n\n"
        + "\n".join(blocs)
        + '\n\nRéponds en JSON : {"questions": [{"q": "…", "notions": ["<id>", …], "liste": ["…", …]}], '
        '"chronologie": [{"date": "AAAA-MM-JJ", "fait": "…", "notions": ["<id>", …]}]}.'
    )


def rediger(
    graphe: str, plafond: int = PLAFOND, modele: str = MODELE, seulement: str | None = None, forcer: bool = False
) -> int:
    """Une carte par sujet déclaré dont l'empreinte a changé (ou absente), au plus `plafond` ; écrite dès qu'elle existe.
    Code 2 si la limite d'usage a interrompu le passage."""
    sujets = te.sujets_declares(graphe)
    notions, _ = sy.lire(graphe)
    if not sujets or not notions:
        print(f"  {graphe} : {'pas de sujet déclaré' if not sujets else 'pas de pyramide'}, pas de carte à dresser")
        return 0
    G = sy.charger_graphe(graphe)
    decl = cfg.graphe_declare(graphe)
    cartes = dict(lire(graphe))
    vecteurs = vecteurs_des_notions(graphe, notions)
    a_faire: list[tuple[int, str, list[str], str]] = []
    for i, s in enumerate(sujets):
        if seulement and not re.search(seulement, s, flags=re.I):
            continue
        ids = notions_du_sujet(graphe, G, notions, i, s, vecteurs)
        emp = empreinte_de(s, ids, notions)
        if not forcer and cartes.get(s, {}).get("empreinte") == emp:
            continue
        a_faire.append((i, s, ids, emp))
    print(
        f"  {graphe} : {len(sujets)} sujet(s), {len(cartes)} carte(s) en place, {len(a_faire)} à dresser"
        + (f" (plafond {plafond})" if plafond and len(a_faire) > plafond else ""),
        flush=True,
    )
    t0, faites, code = time.time(), 0, 0
    for i, s, ids, emp in a_faire[:plafond] if plafond else a_faire:
        if couts.en_limite():
            print(
                f"  ARRÊT : limite d'usage atteinte ({couts.marque_en_cours()}) ; les cartes restantes attendent",
                file=sys.stderr,
            )
            code = 2
            break
        rep = te.appeler_claude(prompt_de(decl, s, G, notions, ids), SCHEMA, modele, poste=POSTE, graphe=graphe)
        if not rep:
            print(f"  carte {i} « {s[:50]} » : pas de réponse", file=sys.stderr)
            continue
        questions, chrono, retraits = controler(rep, ids, notions, G)
        if not questions:
            print(f"  carte {i} « {s[:50]} » : aucune sous-question gardée ({retraits})", file=sys.stderr)
            continue
        cartes[s] = {
            "sujet": s,
            "indice": i,
            "notions": ids,
            "questions": questions,
            "chronologie": chrono,
            "empreinte": emp,
            "redigee": time.strftime("%Y-%m-%d %H:%M"),
            "modele": modele,
            "retraits": retraits,
        }
        ecrire(graphe, cartes)
        faites += 1
        n_l = sum(1 for q in questions if q.get("liste"))
        n_el = sum(len(q.get("liste", [])) for q in questions)
        print(
            f"  carte {i} « {s[:50]} » : {len(questions)} sous-questions, {n_l} liste(s) ({n_el} éléments, "
            f"{retraits['elements']} retiré(s)), {len(chrono)} date(s) ({retraits['dates']} retirée(s))",
            flush=True,
        )
    # Un sujet retiré du registre emporte sa carte.
    for s in [s for s in cartes if s not in sujets]:
        del cartes[s]
        ecrire(graphe, cartes)
    print(f"  fait en {time.time() - t0:.0f} s : {faites} carte(s) dressée(s), {len(cartes)} en place")
    return code


def etat(graphe: str) -> None:
    cartes = lire(graphe)
    sujets = te.sujets_declares(graphe)
    if not cartes:
        print(f"  {graphe} : pas de carte (cartes.py rediger)")
        return
    n_q = sum(len(c["questions"]) for c in cartes.values())
    n_l = sum(1 for c in cartes.values() for q in c["questions"] if q.get("liste"))
    n_d = sum(len(c["chronologie"]) for c in cartes.values())
    print(
        f"  {graphe} : {len(cartes)} carte(s) sur {len(sujets)} sujet(s) — {n_q} sous-questions, {n_l} listes, {n_d} dates"
    )
    for s in sujets:
        c = cartes.get(s)
        if not c:
            print(f"    —  {s[:70]} : pas de carte")
            continue
        print(
            f"    {c['indice']:2d} {s[:70]:70} {len(c['questions']):2d} q. {sum(1 for q in c['questions'] if q.get('liste')):2d} l. "
            f"{len(c['chronologie']):2d} d.  ({c.get('redigee', '')[:10]})"
        )


# --- Router une question -------------------------------------------------------------------------------------------

_ROUTAGE: dict = {}


def _sous_questions(graphe: str):
    """[(sujet, k, sous-question)] et leurs vecteurs, recalculés quand les cartes changent."""
    cartes = lire(graphe)
    cle = chemin(graphe).stat().st_mtime if chemin(graphe).exists() else None
    # Un graphe sans cartes a pour clé None : l'absence d'entrée doit se tester à part, sinon elle passait pour un
    # cache à jour et la lecture levait KeyError à chaque question (13.10.2026 : un graphe d'éthique, un graphe de sécurité,
    # un graphe d'ingénierie, un graphe d'image, un graphe audio).
    if graphe not in _ROUTAGE or _ROUTAGE[graphe][0] != cle:
        sqs = [(s, k, q) for s, c in cartes.items() for k, q in enumerate(c["questions"])]
        E = None
        enc = sy.encodeur(graphe) if sqs else None
        if enc is not None:
            import numpy as np  # noqa: PLC0415

            E = np.array(list(enc.embed([q["q"] for _, _, q in sqs])), dtype=np.float32)
            E /= np.linalg.norm(E, axis=1, keepdims=True)
        _ROUTAGE[graphe] = (cle, sqs, E)
    return _ROUTAGE[graphe][1], _ROUTAGE[graphe][2]


def scores_de(graphe: str, question: str) -> list[tuple[float, str, int]]:
    """(score, sujet, k) de chaque sous-question pour la question, le meilleur d'abord : cosinus de l'encodeur et part
    des mots de la question repris par la sous-question."""
    sqs, E = _sous_questions(graphe)
    if not sqs:
        return []
    mq = sy.mots(question)
    cos = None
    if E is not None:
        v = vecteur(question, graphe)
        cos = E @ v if v is not None else None
    out = []
    for j, (s, k, q) in enumerate(sqs):
        part = len(mq & sy.mots(q["q"])) / max(1, len(mq))
        # Sans encodeur (serveur sans fastembed), les mots seuls font le score : seule une sous-question qui reprend
        # au moins SEUIL_ROUTAGE des mots de la question route.
        sc = (float(cos[j]) * (1 - POIDS_MOTS) + part * POIDS_MOTS) if cos is not None else part
        out.append((sc, s, k))
    out.sort(key=lambda x: -x[0])
    return out


def peu_couverts_pour(carte: dict, question: str, combien: int = 1) -> list[dict]:
    """Le point peu couvert du sujet (peu_couverts.py, champ `peu_couverts` de la carte) qui porte sur la question
    même : au moins trois mots en commun et 40 % des mots du point. Un seul, le plus proche : le 11.10.2026, trois
    lignes sur des points voisins (autres questions du KIDS Act) ont poussé le lecteur à déduire un âge « pas écrit
    tel quel » — le signal sert à s'abstenir, pas à répondre."""
    mq = sy.mots(question)
    out = []
    for p in carte.get("peu_couverts") or []:
        mp = sy.mots(p["point"])
        commun = len(mq & mp)
        if commun >= 3 and commun >= 0.4 * len(mp):
            out.append((commun / max(1, len(mp)), p))
    return [p for _, p in sorted(out, key=lambda x: -x[0])[:combien]]


def peu_couverts_sans_carte(graphe: str, question: str, combien: int = 1) -> list[dict]:
    """Quand aucune carte ne route la question, les points peu couverts de toutes les cartes qui la touchent de près
    (au moins trois mots en commun et la moitié des mots du point) : c'est là que le signal compte le plus, une question
    mal couverte n'étant justement pas routée. Chaque point porte la tête de son sujet."""
    mq = sy.mots(question)
    out = []
    for s, c in lire(graphe).items():
        for p in c.get("peu_couverts") or []:
            mp = sy.mots(p["point"])
            commun = len(mq & mp)
            if commun >= 3 and commun >= len(mp) / 2:
                out.append((commun / max(1, len(mp)), {**p, "sujet": s}))
    return [p for _, p in sorted(out, key=lambda x: -x[0])[:combien]]


def seuil_routage(graphe: str) -> float:
    """Le seuil de routage du graphe : `seuil_routage` au registre, sinon SEUIL_ROUTAGE. Il dépend de l'encodeur qui
    sert les cartes : mesuré le 15.10.2026 sur un graphe de conformité (133 questions au sujet connu), l'encodeur de base routait
    109 questions à 0,48 dont 69 au bon sujet (63 %) ; l'encodeur affiné du graphe, dont les cosinus sont plus serrés,
    en route 47 (85 %) à 0,48 et 87 dont 64 bonnes (74 %) à 0,40 — même couverture, meilleure précision."""
    try:
        return float(cfg.graphe_declare(graphe).get("seuil_routage") or SEUIL_ROUTAGE)
    except (TypeError, ValueError):
        return SEUIL_ROUTAGE


def router(graphe: str, question: str, seuil: float | None = None) -> dict | None:
    """La sous-question d'une carte qui répond à la question, ou None quand aucune ne dépasse le seuil : la sélection
    actuelle (notions par les mots, passages lexicaux) reste la porte de repli."""
    if seuil is None:
        seuil = seuil_routage(graphe)
    sc = scores_de(graphe, question)
    if not sc or sc[0][0] < seuil:
        return None
    score, s, k = sc[0]
    c = lire(graphe)[s]
    q = c["questions"][k]
    ns = set(q["notions"])
    chrono = [e for e in c["chronologie"] if ns & set(e["notions"])][:6]
    return {
        "sujet": s,
        "indice": c["indice"],
        "score": round(score, 3),
        "q": q,
        "chronologie": chrono,
        "peu_couverts": c.get("peu_couverts") or [],
    }


def questions_au_sujet_connu(graphe: str) -> list[tuple[str, str]]:
    """(question, sujet) des bancs : le banc par sujet, et les questions de fond du banc agnostique."""
    base = cfg.chemins_du_graphe(graphe)["base"]
    out = []
    for nom, cle in (("themes_banc.json", "sujet"), ("banc_questions.json", "sujet")):
        p = base / nom
        if p.exists():
            for q in json.loads(p.read_text(encoding="utf-8")):
                if q.get("question") and q.get(cle) and not q.get("ecartee"):
                    out.append((q["question"], q[cle]))
    return out


def mesurer_routage(graphe: str) -> None:
    """Sur les questions des bancs dont on connaît le sujet : à chaque seuil, combien sont routées, et vers la carte du
    bon sujet. C'est ce qui fixe SEUIL_ROUTAGE : router peu mais juste, le repli reste la sélection actuelle."""
    qs = questions_au_sujet_connu(graphe)
    if not qs or not lire(graphe):
        print("  rien à mesurer (questions au sujet connu, ou cartes, absentes)")
        return
    res = []
    for q, s in qs:
        sc = scores_de(graphe, q)
        res.append((sc[0][0], sc[0][1] == s, sc[0][1], s) if sc else (0.0, False, "", s))
    print(
        f"  {len(qs)} questions au sujet connu ; score moyen quand la carte est la bonne "
        f"{sum(r[0] for r in res if r[1]) / max(1, sum(1 for r in res if r[1])):.3f}, "
        f"sinon {sum(r[0] for r in res if not r[1]) / max(1, sum(1 for r in res if not r[1])):.3f}"
    )
    print("  seuil   routées   bon sujet   précision")
    for seuil in [x / 100 for x in range(40, 86, 4)]:
        rt = [r for r in res if r[0] >= seuil]
        bons = sum(1 for r in rt if r[1])
        print(
            f"  {seuil:.2f}   {len(rt):4d}/{len(res)}   {bons:4d}        {bons / len(rt):.0%}"
            if rt
            else f"  {seuil:.2f}      0/{len(res)}"
        )
    seuil = seuil_routage(graphe)
    mal = [(r[0], r[2], r[3]) for r in res if not r[1] and r[0] >= seuil]
    if mal:
        print(f"  mal routées au seuil {seuil} :")
        for sc, vers, de in sorted(mal, reverse=True)[:12]:
            print(f"    {sc:.2f}  « {de[:40]} » → « {vers[:40]} »")


def afficher_routage(graphe: str, question: str) -> None:
    for sc, s, k in scores_de(graphe, question)[:5]:
        print(f"  {sc:.3f}  [{lire(graphe)[s]['indice']}] {lire(graphe)[s]['questions'][k]['q'][:100]}")
    r = router(graphe, question)
    print("  → routée" if r else f"  → non routée (seuil {seuil_routage(graphe)})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rediger")
    p.add_argument("--graphe", required=True)
    p.add_argument("--plafond", type=int, default=PLAFOND)
    p.add_argument("--modele", default=MODELE)
    p.add_argument("--seulement", help="regex sur le libellé du sujet")
    p.add_argument("--forcer", action="store_true", help="réécrire même si l'empreinte n'a pas changé")
    p = sub.add_parser("etat")
    p.add_argument("--graphe", required=True)
    p = sub.add_parser("router")
    p.add_argument("--graphe", required=True)
    p.add_argument("question", nargs="+")
    p = sub.add_parser("mesurer-routage")
    p.add_argument("--graphe", required=True)
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    if a.cmd == "rediger":
        return rediger(g, a.plafond, a.modele, a.seulement, a.forcer)
    if a.cmd == "router":
        afficher_routage(g, " ".join(a.question))
    elif a.cmd == "mesurer-routage":
        mesurer_routage(g)
    else:
        etat(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())

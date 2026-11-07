#!/usr/bin/env python3
"""Banc d'essai du modèle de décision local (`decision.py`) contre les décisions que Claude a déjà prises.

Deux jeux dont la vérité existe au dépôt, sans rien payer (`CHANTIER_DECISION.md`) :

  doublons   les paires tranchées de `graphe/<g>/alias.json` : `alias` = même notion, `refuses` = pas la même.
             Le local répond `noul` à la question de `fondre_doublons.trancher`, paire par paire, avec les mêmes
             trois voisins. `--rejouer-sonnet N` rejoue N paires à Sonnet (poste du banc) : son accord avec lui-même
             est le plancher de bruit. `--audit N` écrit N paires à relire par le porteur (vérité humaine).
  verdicts   les réponses jugées des bancs archivés (`graphe/<g>/bancs/*.json`, dernier jugement par question,
             condition et réponse) : le local répond `choice` juste / faux / ne_sait_pas avec le texte de
             `banc.juger`. `--rejuger-opus N` rejuge N enregistrements avec Opus (poste du banc).

Chaque réponse est écrite dès qu'elle existe dans `cache/decision/<jeu>_<graphe>_<modele>_en_cours.jsonl` (reprise
au redémarrage, jamais un résultat) ; le résultat va dans `graphe/<g>/bancs_decision/<horodatage>_<jeu>_<modele>.json`
(résumé et détails, jamais réécrit). Chaque lot local et chaque appel Claude ont leur ligne au registre des coûts,
postes `essai_decision_doublons` et `essai_decision_verdicts`. Les critères de décision sont dans le chantier,
écrits avant la mesure.

    python3 -m newskit.banc_decision doublons --graphe <graphe> [--modele qwen-0.5b] [--permuter] [--plafond N]
                                              [--rejouer-sonnet 400] [--audit 40]
    python3 -m newskit.banc_decision verdicts [--graphes <g1>,<g2> | tous] [--modele qwen-0.5b]
                                              [--permuter] [--max-juste 600] [--rejuger-opus 200]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import decision  # noqa: E402
from newskit import fondre_doublons as fd  # noqa: E402

CACHE = RACINE / "cache" / "decision"
VOISINS = fd.VOISINS
GRAINE = 20261009
QUESTION_DOUBLON = (
    "Do A and B denote EXACTLY the same notion, i.e. the same thing under two names: a translation "
    "(French/English/German), an acronym and its long form, a spelling or punctuation variant, singular/plural? "
    "It is NOT the same notion when one is a parent or a generalisation of the other, a sub-case, a dated or "
    "versioned variant, a document, event or decision ABOUT the notion, a distinct article of law, or when a "
    "precision in parentheses changes the meaning (another law, another context). When in doubt, answer No.")
# Options en un seul jeton (« Correct », « Wrong », « Unknown ») : avec des lettres A/B/C et des descriptions, le 0.5B
# répondait A (juste) à tout (mesuré le 09.10.2026, 184 sur 184). Les définitions passent dans la question.
OPTIONS_VERDICT = [("juste", "Correct"), ("faux", "Wrong"), ("ne_sait_pas", "Unknown")]
QUESTION_VERDICT = ("Which verdict applies to the given answer? Correct: it states the same fact as the expected answer "
                    "(wording may differ, an extra detail does no harm). Wrong: it states something else, or a fact the "
                    "citation contradicts. Unknown: it states nothing useful.")


# --- écriture au fil de l'eau ------------------------------------------------------------------------------------
def chemin_en_cours(jeu: str, graphe: str, modele: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{jeu}_{graphe}_{modele.replace(':', '-')}_en_cours.jsonl"


def lire_en_cours(p: Path) -> dict[str, dict]:
    out = {}
    if p.exists():
        for ligne in p.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(ligne)
                out[x["cle"]] = x
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def ajouter(p: Path, x: dict) -> None:
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")


def publier(graphe: str, jeu: str, modele: str, resume: dict, details: list[dict]) -> Path:
    dossier = cfg.chemins_du_graphe(graphe)["base"] / "bancs_decision"
    dossier.mkdir(parents=True, exist_ok=True)
    p = dossier / f"{time.strftime('%Y-%m-%dT%H-%M')}_{jeu}_{modele.replace(':', '-')}.json"
    p.write_text(json.dumps({"graphe": graphe, "jeu": jeu, "modele": modele, "date": time.strftime("%Y-%m-%d %H:%M"),
                             "resume": resume, "details": details}, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# --- mesures -----------------------------------------------------------------------------------------------------
def brier_ece(probas: list[float], verites: list[bool], cases: int = 10) -> tuple[float, float]:
    """Brier sur p(vrai) et erreur de calibration attendue (ECE) sur la confiance de la classe prédite."""
    n = len(probas)
    if not n:
        return 0.0, 0.0
    brier = sum((p - float(v)) ** 2 for p, v in zip(probas, verites)) / n
    conf = [(max(p, 1 - p), (p >= 0.5) == v) for p, v in zip(probas, verites)]
    ece = 0.0
    for k in range(cases):
        lo, hi = 0.5 + k * 0.5 / cases, 0.5 + (k + 1) * 0.5 / cases
        dans = [(c, j) for c, j in conf if lo <= c < hi or (k == cases - 1 and c == hi)]
        if dans:
            ece += len(dans) / n * abs(sum(j for _, j in dans) / len(dans) - sum(c for c, _ in dans) / len(dans))
    return round(brier, 4), round(ece, 4)


def temperature(probas: list[float], verites: list[bool]) -> float:
    """T qui minimise la log-vraisemblance négative sur la moitié paire du jeu (grille) ; l'autre moitié sert à la lire."""
    import math  # noqa: PLC0415
    pli = [(p, v) for k, (p, v) in enumerate(zip(probas, verites)) if k % 2 == 0]
    meilleur, nll_min = 1.0, float("inf")
    for t in [x / 20 for x in range(5, 81)]:
        nll = 0.0
        for p, v in pli:
            p = min(max(p, 1e-6), 1 - 1e-6)
            z = math.log(p / (1 - p)) / t
            q = 1 / (1 + math.exp(-z))
            nll -= math.log(q if v else 1 - q)
        if nll < nll_min:
            meilleur, nll_min = t, nll
    return meilleur


def rechauffer(p: float, t: float) -> float:
    import math  # noqa: PLC0415
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 1 / (1 + math.exp(-math.log(p / (1 - p)) / t))


def bande(probas: list[float], verites: list[bool], precision_min: float = 0.90) -> dict:
    """Seuil haut : le plus petit seuil au-dessus duquel la précision « même » tient ; seuil bas : le plus grand
    au-dessous duquel la précision « pas même » tient ; la bande est ce qui reste entre les deux."""
    grille = [x / 100 for x in range(5, 100, 5)]
    haut = next((t for t in grille if sum(v for p, v in zip(probas, verites) if p >= t) >=
                 precision_min * max(1, sum(1 for p in probas if p >= t)) and any(p >= t for p in probas)), 1.0)
    bas = next((t for t in reversed(grille) if sum(not v for p, v in zip(probas, verites) if p < t) >=
                precision_min * max(1, sum(1 for p in probas if p < t)) and any(p < t for p in probas)), 0.0)
    if bas > haut:
        bas = haut
    dedans = sum(1 for p in probas if bas <= p < haut)
    return {"seuil_bas": bas, "seuil_haut": haut, "part_bande": round(dedans / max(1, len(probas)), 3)}


def auc(probas: list[float], verites: list[bool]) -> float:
    """Aire sous la courbe ROC : la probabilité qu'une paire « même » reçoive un p plus haut qu'un refus (mesure sans
    seuil, ce que vaut le classement du modèle avant toute calibration)."""
    pos = sorted(p for p, v in zip(probas, verites) if v)
    neg = sorted(p for p, v in zip(probas, verites) if not v)
    if not pos or not neg:
        return 0.0
    import bisect  # noqa: PLC0415
    s = sum(bisect.bisect_left(neg, p) + 0.5 * (bisect.bisect_right(neg, p) - bisect.bisect_left(neg, p)) for p in pos)
    return round(s / (len(pos) * len(neg)), 4)


def deux_classes(pred: list[bool], verites: list[bool]) -> dict:
    vp = sum(1 for p, v in zip(pred, verites) if p and v)
    fp = sum(1 for p, v in zip(pred, verites) if p and not v)
    fn = sum(1 for p, v in zip(pred, verites) if not p and v)
    vn = sum(1 for p, v in zip(pred, verites) if not p and not v)
    prec = vp / max(1, vp + fp)
    rapp = vp / max(1, vp + fn)
    return {"n": len(pred), "accord": round((vp + vn) / max(1, len(pred)), 4), "precision_meme": round(prec, 4),
            "rappel_meme": round(rapp, 4), "f1_meme": round(2 * prec * rapp / max(1e-9, prec + rapp), 4),
            "matrice": {"vp": vp, "fp": fp, "fn": fn, "vn": vn}}


# --- jeu des doublons --------------------------------------------------------------------------------------------
def jeu_doublons(graphe: str) -> tuple[list[dict], dict, dict]:
    """Les paires tranchées, avec libellés et voisins ; et (label, vois) pour rejouer Sonnet."""
    memo = fd.lire_alias(graphe)
    d = fd.charger_graphe(graphe) or {"nodes": [], "links": []}
    label, vois, _ = fd.contexte(d)
    label_lots, vois_lots = fd.contexte_des_lots(graphe)
    for n, l in label_lots.items():
        label.setdefault(n, l)
    for n, l in memo.get("libelles", {}).items():
        label.setdefault(n, l)
    for n, vs in vois_lots.items():
        if not vois.get(n):
            vois[n] = vs
    paires = []
    for a, b in memo["alias"].items():
        paires.append({"cle": "|".join(sorted((a, b))), "a": a, "b": b, "verite": True})
    for k in memo["refuses"]:
        a, b = k.split("|", 1)
        paires.append({"cle": k, "a": a, "b": b, "verite": False})
    manque = [p for p in paires if p["a"] not in label or p["b"] not in label]
    for p in manque:
        print(f"  (libellé introuvable : {p['cle']})", file=sys.stderr)
    paires = [p for p in paires if p not in manque]
    for p in paires:
        p["label_a"], p["label_b"] = label[p["a"]], label[p["b"]]
        p["vois_a"], p["vois_b"] = vois.get(p["a"], [])[:VOISINS], vois.get(p["b"], [])[:VOISINS]
    random.Random(GRAINE).shuffle(paires)
    return paires, label, vois


def etat_doublon(decl: dict, p: dict) -> str:
    va = " ; ".join(p["vois_a"]) or "—"
    vb = " ; ".join(p["vois_b"]) or "—"
    return (f"Domain: \"{decl['libelle']}\". In a knowledge graph, two nodes A and B carry the labels below, with a few "
            f"neighbours for context.\nA = \"{p['label_a']}\" (neighbours: {va})\nB = \"{p['label_b']}\" (neighbours: {vb})")


def banc_doublons(args: argparse.Namespace) -> int:
    graphe = cfg.nom_actuel(args.graphe)
    decl = cfg.graphe_declare(graphe)
    paires, label, vois = jeu_doublons(graphe)
    n_meme = sum(p["verite"] for p in paires)
    print(f"  {graphe} : {len(paires)} paires tranchées ({n_meme} même, {len(paires) - n_meme} refus)")
    if args.plafond:
        paires = paires[:args.plafond]
    modele = f"local:{args.modele}" + ("-permute" if args.permuter else "")
    poste = "essai_decision_doublons"
    p_cours = chemin_en_cours("doublons", graphe, modele)
    faits = lire_en_cours(p_cours)
    restantes = [p for p in paires if p["cle"] not in faits]
    print(f"  {modele} : {len(faits)} déjà faites, {len(restantes)} à faire")
    if restantes:
        dec = decision.decideur(args.modele)
        t0 = time.time()
        for i0 in range(0, len(restantes), args.lot):
            lot = restantes[i0:i0 + args.lot]
            etats = [etat_doublon(decl, p) for p in lot]
            qs = [{"type": "noul", "question": QUESTION_DOUBLON} for _ in lot]
            reps = dec.decider(etats, qs, permuter=args.permuter, lot=args.lot, poste=poste, graphe=graphe)
            for p, r in zip(lot, reps):
                x = {"cle": p["cle"], "a": p["a"], "b": p["b"], "label_a": p["label_a"], "label_b": p["label_b"],
                     "verite": p["verite"], "p_meme": r["noul"], "ms": r["ms"]}
                faits[p["cle"]] = x
                ajouter(p_cours, x)
            fait = i0 + len(lot)
            if fait % (args.lot * 10) == 0 or fait == len(restantes):
                print(f"    {fait}/{len(restantes)} paires, {(time.time() - t0) / fait:.2f} s/paire, RSS {decision.rss_mo()} Mo", flush=True)
    details = [faits[p["cle"]] for p in paires if p["cle"] in faits]
    probas = [x["p_meme"] for x in details]
    verites = [x["verite"] for x in details]
    resume = deux_classes([p >= 0.5 for p in probas], verites)
    resume["auc"] = auc(probas, verites)
    resume["brier"], resume["ece"] = brier_ece(probas, verites)
    t = temperature(probas, verites)
    impairs = [(rechauffer(p, t), v) for k, (p, v) in enumerate(zip(probas, verites)) if k % 2 == 1]
    resume["temperature"] = t
    resume["ece_apres_temperature_autre_moitie"] = brier_ece([p for p, _ in impairs], [v for _, v in impairs])[1]
    resume.update(bande(probas, verites))
    resume["s_par_paire"] = round(sum(x["ms"] for x in details) / max(1, len(details)) / 1000, 3)
    resume["rss_mo"] = decision.rss_mo()
    print("  résumé :", json.dumps(resume, ensure_ascii=False))

    if args.rejouer_sonnet:
        rejouer_sonnet(graphe, paires, label, vois, decl, args.rejouer_sonnet, poste, resume)
    if args.audit:
        audit_doublons(graphe, details, args.audit)
    p = publier(graphe, "doublons", modele, resume, details)
    print(f"  écrit : {p.relative_to(RACINE)}")
    return 0


def rejouer_sonnet(graphe: str, paires: list[dict], label: dict, vois: dict, decl: dict, n: int, poste: str, resume: dict) -> None:
    """N paires (moitié même, moitié refus) rejouées à Sonnet avec la consigne de production : accord de Sonnet avec
    lui-même. Plafond explicite : n paires, soit n/40 appels."""
    modele = fd.MODELE
    p_cours = chemin_en_cours("doublons", graphe, modele)
    faits = lire_en_cours(p_cours)
    meme = [p for p in paires if p["verite"]][:n // 2]
    refus = [p for p in paires if not p["verite"]][:n - len(meme)]
    choisies = [p for p in meme + refus if p["cle"] not in faits]
    print(f"  rejeu Sonnet : {len(meme) + len(refus)} paires, {len(choisies)} à poser ({-(-len(choisies) // fd.LOT)} appels)")
    if choisies:
        verdicts = fd.trancher(graphe, [(p["a"], p["b"], 1.0, "banc") for p in choisies], label, vois, decl, poste=poste)
        for p in choisies:
            if p["cle"] in verdicts:
                x = {"cle": p["cle"], "a": p["a"], "b": p["b"], "label_a": p["label_a"], "label_b": p["label_b"],
                     "verite": p["verite"], "meme": verdicts[p["cle"]]}
                faits[p["cle"]] = x
                ajouter(p_cours, x)
    rejouees = [faits[p["cle"]] for p in meme + refus if p["cle"] in faits]
    if rejouees:
        r = deux_classes([x["meme"] for x in rejouees], [x["verite"] for x in rejouees])
        resume["sonnet_rejoue"] = r
        print("  Sonnet contre lui-même :", json.dumps(r, ensure_ascii=False))
        publier(graphe, "doublons", modele, r, rejouees)


def audit_doublons(graphe: str, details: list[dict], n: int) -> None:
    """N paires à relire par le porteur : accords et désaccords du local avec Sonnet, mêlés, sans dire qui a dit quoi
    (la colonne « même ? » est à remplir ; la clé permet de recoller)."""
    rng = random.Random(GRAINE)
    groupes = {
        "accord_meme": [x for x in details if x["verite"] and x["p_meme"] >= 0.5],
        "accord_refus": [x for x in details if not x["verite"] and x["p_meme"] < 0.5],
        "local_meme_sonnet_refus": [x for x in details if not x["verite"] and x["p_meme"] >= 0.5],
        "local_refus_sonnet_meme": [x for x in details if x["verite"] and x["p_meme"] < 0.5],
    }
    part = n // 4
    tirees = []
    for g, xs in groupes.items():
        rng.shuffle(xs)
        tirees += [{**x, "groupe": g} for x in xs[:part]]
    rng.shuffle(tirees)
    p = CACHE / f"audit_doublons_{graphe}_{time.strftime('%Y-%m-%d')}.md"
    lignes = [f"# Audit des doublons — {graphe}, {time.strftime('%d.%m.%Y')}", "",
              "Pour chaque paire, écrire « oui » si A et B sont EXACTEMENT la même notion (traduction, sigle, orthographe,",
              "pluriel), « non » sinon (parent, sous-cas, variante datée, document sur la notion). Les verdicts du modèle",
              "et de Sonnet ne sont pas montrés. Rendre dans `graphe/<g>/bancs_decision/audit_doublons.json`", "",
              "| n° | A | B | même ? | clé |", "|---|---|---|---|---|"]
    for i, x in enumerate(tirees, 1):
        lignes.append(f"| {i} | {x['label_a']} | {x['label_b']} |  | `{x['cle']}` |")
    p.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    (CACHE / f"audit_doublons_{graphe}_{time.strftime('%Y-%m-%d')}_cles.json").write_text(
        json.dumps([{"n": i, "cle": x["cle"], "groupe": x["groupe"]} for i, x in enumerate(tirees, 1)], ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"  audit : {len(tirees)} paires dans {p.relative_to(RACINE)}")


# --- jeu des verdicts --------------------------------------------------------------------------------------------
CONDITIONS = ("graphe", "modele", "web", "fiches")
VERDICTS = ("juste", "faux", "ne_sait_pas")


def jeu_verdicts(graphes: list[str]) -> list[dict]:
    """Le dernier jugement de chaque (graphe, question, condition, réponse non vide) dans les archives des bancs."""
    out: dict[tuple, dict] = {}
    for g in graphes:
        base = cfg.chemins_du_graphe(g)["base"]
        fichiers = sorted((base / "bancs").glob("*.json")) + ([base / "banc.json"] if (base / "banc.json").exists() else [])
        for p in fichiers:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for d in r.get("details", []):
                for c in CONDITIONS:
                    x = d.get(c)
                    if not isinstance(x, dict):
                        continue
                    rep = str(x.get("reponse") or "").strip()
                    if not rep or x.get("verdict") not in VERDICTS:
                        continue
                    cle = (g, d.get("question", ""), c, rep)
                    empreinte = hashlib.sha256((d.get("question", "") + "\n" + rep).encode("utf-8")).hexdigest()[:12]
                    out[cle] = {"cle": f"{g}|{c}|{empreinte}", "graphe": g,
                                "condition": c, "question": d.get("question", ""), "attendu": d.get("attendu", ""),
                                "citation": str(d.get("citation") or "")[:400], "reponse": rep, "verite": x["verdict"],
                                "archive": p.name}
    return list(out.values())


def etat_verdict(x: dict) -> str:
    return (f"Benchmark question (in French), with the expected answer, the source citation and the answer to judge. "
            f"Judge against the citation, not from memory.\n\nQuestion: {x['question']}\nExpected answer: {x['attendu']}\n"
            f"Citation: {x['citation']}\nGiven answer: {x['reponse']}")


def trois_classes(pred: list[str], verites: list[str]) -> dict:
    m = {v: {w: 0 for w in VERDICTS} for v in VERDICTS}
    for p, v in zip(pred, verites):
        m[v][p] += 1
    par = {}
    for c in VERDICTS:
        vp = m[c][c]
        prec = vp / max(1, sum(m[v][c] for v in VERDICTS))
        rapp = vp / max(1, sum(m[c].values()))
        par[c] = {"n": sum(m[c].values()), "precision": round(prec, 4), "rappel": round(rapp, 4)}
    n = len(pred)
    return {"n": n, "accord": round(sum(1 for p, v in zip(pred, verites) if p == v) / max(1, n), 4),
            "juste_vers_faux": round(m["juste"]["faux"] / max(1, sum(m["juste"].values())), 4),
            "par_classe": par, "matrice": m}


def banc_verdicts(args: argparse.Namespace) -> int:
    graphes = cfg.graphes() if args.graphes == "tous" else [cfg.nom_actuel(g) for g in args.graphes.split(",")]
    jeu = jeu_verdicts(graphes)
    rng = random.Random(GRAINE)
    rng.shuffle(jeu)
    par_classe = collections.Counter(x["verite"] for x in jeu)
    print(f"  {len(jeu)} réponses jugées sur {len(graphes)} graphe(s) : {dict(par_classe)}")
    ordre = [x for x in jeu if x["verite"] == "faux"] + [x for x in jeu if x["verite"] == "ne_sait_pas"] + \
            [x for x in jeu if x["verite"] == "juste"][:args.max_juste or None]
    modele = f"local:{args.modele}" + ("-permute" if args.permuter else "")
    poste = "essai_decision_verdicts"
    nom = "tous" if args.graphes == "tous" else "-".join(graphes)
    p_cours = chemin_en_cours("verdicts", nom, modele)
    faits = lire_en_cours(p_cours)
    restants = [x for x in ordre if x["cle"] not in faits]
    print(f"  {modele} : {len(faits)} déjà faits, {len(restants)} à faire (faux et ne_sait_pas d'abord)")
    if restants:
        dec = decision.decideur(args.modele)
        t0 = time.time()
        for i0 in range(0, len(restants), args.lot):
            lot = restants[i0:i0 + args.lot]
            qs = [{"type": "choice", "question": QUESTION_VERDICT, "options": [mot for _, mot in OPTIONS_VERDICT]} for _ in lot]
            reps = dec.decider([etat_verdict(x) for x in lot], qs, permuter=args.permuter, lot=args.lot, poste=poste, graphe=nom)
            for x, r in zip(lot, reps):
                probas = {c: r["probas"][desc] for c, desc in OPTIONS_VERDICT}
                pred = max(probas, key=probas.get)
                y = {**x, "pred": pred, "probas": probas, "confiance": probas[pred], "ms": r["ms"]}
                faits[x["cle"]] = y
                ajouter(p_cours, y)
            fait = i0 + len(lot)
            if fait % (args.lot * 25) == 0 or fait == len(restants):
                print(f"    {fait}/{len(restants)}, {(time.time() - t0) / fait:.2f} s/enreg., RSS {decision.rss_mo()} Mo", flush=True)
    details = [faits[x["cle"]] for x in ordre if x["cle"] in faits]
    resume = trois_classes([x["pred"] for x in details], [x["verite"] for x in details])
    # Brier multiclasse et ECE sur la confiance de la classe prédite
    n = max(1, len(details))
    resume["brier"] = round(sum(sum((x["probas"][c] - float(x["verite"] == c)) ** 2 for c in VERDICTS) for x in details) / n, 4)
    conf = [(x["confiance"], x["pred"] == x["verite"]) for x in details]
    ece = 0.0
    for k in range(10):
        lo, hi = k / 10, (k + 1) / 10
        dans = [(c, j) for c, j in conf if lo <= c < hi or (k == 9 and c == 1.0)]
        if dans:
            ece += len(dans) / n * abs(sum(j for _, j in dans) / len(dans) - sum(c for c, _ in dans) / len(dans))
    resume["ece"] = round(ece, 4)
    resume["s_par_enregistrement"] = round(sum(x["ms"] for x in details) / n / 1000, 3)
    resume["rss_mo"] = decision.rss_mo()
    resume["par_graphe"] = {g: trois_classes([x["pred"] for x in details if x["graphe"] == g],
                                             [x["verite"] for x in details if x["graphe"] == g])["accord"] for g in graphes
                            if any(x["graphe"] == g for x in details)}
    print("  résumé :", json.dumps({k: v for k, v in resume.items() if k != "matrice"}, ensure_ascii=False))
    if args.rejuger_opus:
        rejuger_opus(ordre, args.rejuger_opus, poste, nom, resume)
    p = publier(graphes[0] if len(graphes) == 1 else cfg.GRAPHE_PRINCIPAL, "verdicts", modele, resume, details)
    print(f"  écrit : {p.relative_to(RACINE)}")
    return 0


def rejuger_opus(ordre: list[dict], n: int, poste: str, nom: str, resume: dict) -> None:
    """N enregistrements (tous les faux d'abord, puis au hasard) rejugés par `banc.juger` (Opus, consigne de
    production) sous le poste du banc : accord d'Opus avec lui-même, par classe. Par lots de 10."""
    from newskit import banc  # noqa: PLC0415
    banc.CONTEXTE["poste"], banc.CONTEXTE["graphe"] = poste, nom
    p_cours = chemin_en_cours("verdicts", nom, banc.te.MODELE)
    faits = lire_en_cours(p_cours)
    faux = [x for x in ordre if x["verite"] == "faux"]
    autres = [x for x in ordre if x["verite"] != "faux"]
    choisis = (faux + autres)[:n]
    a_faire = [x for x in choisis if x["cle"] not in faits]
    print(f"  rejeu Opus : {len(choisis)} enregistrements, {len(a_faire)} à juger ({-(-len(a_faire) // 10)} appels)")
    delai = cfg.banc()["delai"]
    for i0 in range(0, len(a_faire), 10):
        lot = a_faire[i0:i0 + 10]
        qs = [{"i": k, "question": x["question"], "attendu": x["attendu"], "citation": x["citation"]} for k, x in enumerate(lot)]
        verdicts = banc.juger(qs, {k: {"reponse": x["reponse"]} for k, x in enumerate(lot)}, delai)
        for k, x in enumerate(lot):
            y = {**x, "pred": verdicts.get(k, "ne_sait_pas")}
            faits[x["cle"]] = y
            ajouter(p_cours, y)
    rejuges = [faits[x["cle"]] for x in choisis if x["cle"] in faits]
    if rejuges:
        r = trois_classes([x["pred"] for x in rejuges], [x["verite"] for x in rejuges])
        resume["opus_rejuge"] = r
        print("  Opus contre lui-même :", json.dumps({k: v for k, v in r.items() if k != 'matrice'}, ensure_ascii=False))
        publier(cfg.GRAPHE_PRINCIPAL, "verdicts", banc.te.MODELE, r, rejuges)


# --- CLI ---------------------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="jeu", required=True)
    d = sub.add_parser("doublons")
    d.add_argument("--graphe", required=True)
    d.add_argument("--modele", default=decision.DEFAUT, choices=sorted(decision.MODELES))
    d.add_argument("--permuter", action="store_true")
    d.add_argument("--lot", type=int, default=8)
    d.add_argument("--plafond", type=int, default=0, help="n premières paires seulement (fumée : 20)")
    d.add_argument("--rejouer-sonnet", type=int, default=0, metavar="N")
    d.add_argument("--audit", type=int, default=0, metavar="N")
    v = sub.add_parser("verdicts")
    v.add_argument("--graphes", default="tous", help="« tous » ou liste séparée par des virgules")
    v.add_argument("--modele", default=decision.DEFAUT, choices=sorted(decision.MODELES))
    v.add_argument("--permuter", action="store_true")
    v.add_argument("--lot", type=int, default=4)
    v.add_argument("--max-juste", type=int, default=0, help="plafond des « juste » (0 : tous)")
    v.add_argument("--rejuger-opus", type=int, default=0, metavar="N")
    args = ap.parse_args()
    return banc_doublons(args) if args.jeu == "doublons" else banc_verdicts(args)


if __name__ == "__main__":
    sys.exit(main())

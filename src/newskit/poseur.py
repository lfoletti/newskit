#!/usr/bin/env python3
"""Le poseur de questions : un agent qui interroge le graphe comme le ferait un praticien, et le pousse à se compléter.

Décision du porteur (07.10.2026) : le banc mesure la couverture, il ne la fait pas monter ; l'éclaireur cherche sur
des sujets, pas sur des questions. Le poseur ferme la boucle entre les deux, chaque jour et par graphe :

    1. tirer    des questions agnostiques, comme le banc : récentes, tirées de pages de n'importe quel site, et de
                fond, écrites sans connaître le graphe puis vérifiées sur le web, chacune avec sa réponse et sa citation ;
    2. poser    ces questions au graphe, par sa pyramide de synthèses, comme le banc (même lecteur, même consigne) ;
    3. juger    chaque réponse contre la citation (le juge du banc) ;
    4. combler  la page d'une question manquée entre au graphe par la voie de l'éclaireur : fiche, provenance
                (« question du poseur »), extraction au passage suivant ;
    5. reposer  les questions manquées des jours précédents, une fois leurs pages lues : le taux de questions
                comblées dit si la boucle marche, pas seulement si elle tourne.

Garde-fou : le poseur n'ingère jamais la page d'une question du banc (banc_questions.json), sinon le banc mesurerait
ce que le poseur a lu pour lui. Les questions du poseur ne sont pas celles du banc ; elles en partagent la méthode.

Bornes au registre (sources.yaml, clé `poseur`, avec une surcharge par graphe sous `graphes`) :
    actif       faux : le poseur ne tourne pas pour ce graphe
    questions   pages tirées, donc questions posées au plus par passage (défaut 12)
    sujets      sujets cherchés par passage (défaut 3)
    reposer     questions manquées reposées au plus par passage (défaut 12)
    delai       secondes laissées au modèle par appel (défaut 900)

    python3 -m newskit.poseur --graphe <graphe>               # un passage
    python3 -m newskit.poseur --graphe <graphe> --controle    # les sujets du jour et les bornes, sans appel
    python3 -m newskit.poseur --graphe <graphe> --etat        # le bilan du journal : posées, comblées, reposées

Journal : graphe/<nom>/poseur.jsonl, une ligne par question. Fiches : sources/eclaireur/<nom>/, comme l'éclaireur.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import banc  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import eclaireur  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

RE_URL = re.compile(r"\((https?://[^)\s]+)\)\s*$")


def journal(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "poseur.jsonl"


def lire_journal(graphe: str) -> list[dict]:
    p = journal(graphe)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def ecrire_journal(graphe: str, lignes: list[dict]) -> None:
    journal(graphe).write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes), encoding="utf-8")


def adresse_de(q: dict) -> str:
    m = RE_URL.search(q.get("citation") or "")
    return m.group(1).rstrip("/") if m else ""


def adresses_du_banc(graphe: str) -> set[str]:
    """Les pages des questions du banc : le poseur ne les ingère jamais."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc_questions.json"
    if not p.exists():
        return set()
    out = set()
    for q in json.loads(p.read_text(encoding="utf-8")):
        out |= {u.rstrip("/") for u in re.findall(r"https?://[^)\s]+", q.get("citation") or "")}
    return out


def sujets_du_jour(graphe: str, n: int, jour: int | None = None) -> list[str]:
    """Les sujets déclarés, à tour de rôle, décalés de ceux de l'éclaireur (qui part du début de la rotation)."""
    sujets = te.sujets_declares(graphe)
    if not sujets:
        return []
    jour = time.localtime().tm_yday if jour is None else jour
    debut = (jour * n + len(sujets) // 2) % len(sujets)
    tour = [sujets[(debut + k) % len(sujets)] for k in range(min(n, len(sujets)))]
    # Un sujet neuf (thème émergent entré parmi les sujets) passe en tête ses premières nuits (09.10.2026).
    neufs = [s for s in te.nouveaux_en_cours(graphe) if s in sujets][:1]
    return (neufs + [s for s in tour if s not in neufs])[:max(n, 1)]


def tirer(graphe: str, sujets: list[str], n: int, exclues: set[str], delai: int) -> list[dict]:
    """Des questions agnostiques, tirées comme celles du banc (porteur, 07.10.2026) : le poseur comble ce que le banc
    mesure. Seules les pages lisibles sont gardées, puisqu'une question manquée doit pouvoir entrer au graphe."""
    from newskit import collecte_rss as cr  # noqa: PLC0415
    mots_min = cfg.eclaireur(graphe)["mots_min"]
    tirees = banc.composer_agnostique(graphe, n + 4, delai, exclues)
    out, illisibles = [], []
    for q in tirees:
        url = adresse_de(q)
        if not url or url in exclues:
            continue
        exclues.add(url)
        # Lue comme l'ingestion la lirait : une question dont la page ne pourrait pas entrer au graphe ne sert à rien.
        n_mots, _, mur, _ = cr.corps_de_page(url)
        if mur or n_mots < mots_min:
            illisibles.append(f"{url[:80]} ({'mur' if mur else f'{n_mots} mots'})")
            continue
        out.append({**q, "url": url})
    if illisibles:
        print(f"  {len(illisibles)} page(s) illisible(s) écartée(s) avant de poser : " + " ; ".join(illisibles))
    return out[:n]


def poser(graphe: str, questions: list[dict], delai: int) -> tuple[dict[int, dict], dict[int, str], dict]:
    """Les réponses du graphe et leurs verdicts, comme au banc."""
    for i, q in enumerate(questions):
        q["i"] = i
    compte: dict = {}
    reponses = banc.repondre_par_syntheses(graphe, questions, {"delai": delai}, compte)
    return reponses, banc.juger(questions, reponses, delai), compte


def combler(graphe: str, manquees: list[dict], interdites: set[str]) -> dict[str, str]:
    """Ingère la page de chaque question manquée par la voie de l'éclaireur. Rend {adresse: ligne du rapport}."""
    bornes = cfg.eclaireur(graphe)
    aujourdhui = time.strftime("%Y-%m-%d")
    memoire, connues = eclaireur.lire_memoire(), eclaireur.adresses_connues(graphe)
    out = {}
    for q in manquees:
        if q["url"] in interdites:
            out[q["url"]] = f"  ⊘   page du banc, non ingérée : {q['url'][:90]}"
            continue
        page = {"url": q["url"], "titre": "", "point": 0}
        out[q["url"]] = eclaireur.ingerer(graphe, page, [("question du poseur", q["question"])], bornes, connues,
                                          memoire, aujourdhui)
    eclaireur.DOSSIER.mkdir(parents=True, exist_ok=True)
    eclaireur.MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def reposer(graphe: str, lignes: list[dict], n: int, delai: int) -> int:
    """Repose les questions manquées des jours précédents dont la page a été ingérée. Rend le nombre comblé."""
    aujourdhui = time.strftime("%Y-%m-%d")
    a_reposer = [l for l in lignes if l.get("verdict") not in ("juste", "en_attente") and l.get("ingeree")
                 and not l.get("reposee") and l.get("date", "") < aujourdhui][:n]
    if not a_reposer:
        return 0
    qs = [{k: l[k] for k in ("question", "attendu", "citation")} for l in a_reposer]
    _, verdicts, _ = poser(graphe, qs, delai)
    comblees = 0
    for q, l in zip(qs, a_reposer):
        l["reposee"], l["verdict_repose"] = aujourdhui, verdicts.get(q["i"], "ne_sait_pas")
        comblees += l["verdict_repose"] == "juste"
    print(f"  reposées : {len(a_reposer)} question(s) manquée(s) des jours précédents, {comblees} comblée(s)")
    return comblees


def passage(graphe: str, controle: bool = False) -> int:
    bornes = cfg.poseur(graphe)
    if not bornes["actif"]:
        print(f"  {graphe} : poseur inactif (registre)")
        return 0
    if not banc.a_une_pyramide(graphe):
        print(f"  {graphe} : pas de pyramide de synthèses, rien à poser")
        return 0
    if not cfg.toutes("Recherche web", graphe=graphe):
        print(f"  {graphe} : aucune source « Recherche web » au registre, rien ne peut être ingéré")
        return 0
    sujets = sujets_du_jour(graphe, bornes["sujets"])
    for s in sujets:
        print(f"    sujet  {s[:110]}")
    if controle:
        print(f"  (--controle : aucun appel ; bornes {bornes})")
        return 0
    lignes = lire_journal(graphe)
    reposer(graphe, lignes, bornes["reposer"], bornes["delai"])
    interdites = adresses_du_banc(graphe)
    exclues = interdites | {l["url"] for l in lignes} | eclaireur.adresses_connues(graphe)
    questions = tirer(graphe, sujets, bornes["questions"], exclues, bornes["delai"])
    if not questions:
        ecrire_journal(graphe, lignes)
        print(f"  {graphe} : aucune question tirée (le modèle n'a rien rendu)")
        return 0
    aujourdhui = time.strftime("%Y-%m-%d")
    # Les questions tirées sont écrites avant d'être posées (08.10.2026) : les tirer coûte des recherches web, et un
    # passage interrompu les perdait toutes. Verdict « en_attente » tant qu'elles ne sont pas posées.
    neuves = [{"date": aujourdhui, "question": q["question"], "attendu": q["attendu"], "citation": q["citation"],
               "url": q["url"], "verdict": "en_attente", "reponse": "", "ingeree": False} for q in questions]
    ecrire_journal(graphe, lignes + neuves)
    if couts.en_limite():
        print(f"  ARRÊT : limite d'usage atteinte, {len(questions)} question(s) tirée(s) mais non posée(s) "
              f"({couts.marque_en_cours() or 'ce processus'})", file=sys.stderr)
        return 2
    reponses, verdicts, compte = poser(graphe, questions, bornes["delai"])
    for q, l in zip(questions, neuves):
        l["verdict"] = verdicts.get(q["i"], "ne_sait_pas")
        l["reponse"] = (reponses.get(q["i"]) or {}).get("reponse", "")
    ecrire_journal(graphe, lignes + neuves)                 # posées et jugées : écrit avant l'ingestion des pages
    manquees = [q for q in questions if verdicts.get(q["i"]) != "juste"]
    rapport = combler(graphe, manquees, interdites)
    for q, l in zip(questions, neuves):
        v, r = l["verdict"], rapport.get(q["url"], "")
        l["ingeree"] = r.startswith("  ok")
        print(f"    {'ok ' if v == 'juste' else v[:3]}  {q['question'][:100]}")
        if r:
            print(f"      {r.strip()[:120]}")
    lignes += neuves
    ecrire_journal(graphe, lignes)
    justes = sum(verdicts.get(q["i"]) == "juste" for q in questions)
    ingerees = sum(r.startswith("  ok") for r in rapport.values())
    cout = float(compte.get("cout_usd") or 0)          # un compte plat (banc.compter) : les réponses du graphe
    print(f"  {graphe} : {len(questions)} question(s) posée(s), {justes} juste(s), {len(manquees)} manquée(s), "
          f"{ingerees} page(s) ingérée(s)" + (f", {cout:.2f} $ pour les réponses" if cout else ""))
    return 0


def etat(graphe: str) -> int:
    lignes = lire_journal(graphe)
    if not lignes:
        print(f"  {graphe} : le poseur n'a encore rien posé")
        return 0
    manquees = [l for l in lignes if l.get("verdict") != "juste"]
    reposees = [l for l in manquees if l.get("reposee")]
    comblees = [l for l in reposees if l.get("verdict_repose") == "juste"]
    jours = sorted({l["date"] for l in lignes})
    print(f"  {graphe} : {len(lignes)} question(s) posée(s) en {len(jours)} jour(s) ({jours[0]} → {jours[-1]}), "
          f"{len(lignes) - len(manquees)} juste(s) d'emblée, {len(manquees)} manquée(s), "
          f"{sum(bool(l.get('ingeree')) for l in manquees)} page(s) ingérée(s)")
    if reposees:
        print(f"    reposées : {len(reposees)}, comblées : {len(comblees)} ({round(100 * len(comblees) / len(reposees))} %)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--controle", action="store_true", help="dire les sujets du jour et les bornes, sans appel")
    ap.add_argument("--etat", action="store_true", help="le bilan du journal")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    banc.CONTEXTE.update(poste="poseur", graphe=g)      # registre des coûts : les appels du poseur sous son nom
    return etat(g) if a.etat else passage(g, a.controle)


if __name__ == "__main__":
    random.seed()
    sys.exit(main())

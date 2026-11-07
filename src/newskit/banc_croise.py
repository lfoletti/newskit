#!/usr/bin/env python3
"""Le banc croisé : un jeu de questions par thème, posé à plusieurs graphes, seuls et ensemble, au modèle seul et au modèle
avec la recherche web. C'est la mesure qui décide d'une fusion de graphes (GV1 de CHANTIER_GOUVERNANCE.md).

Les thèmes viennent d'un fichier (clé `themes` : `cle`, libellés `en`, `resume.en`, `sujets[].en`, `avant` : les graphes
d'où vient le corpus du thème, `etat` : « a_collecter » pour un thème sans corpus). Les graphes du banc sont ceux nommés
sous `avant`. Pour chaque thème, des questions écrites sans le graphe, comme le banc par thème (banc_themes.py) : moitié de
fond, écrites par un praticien puis vérifiées dans une page lue par nous, moitié sur l'actualité, tirées de pages récentes
de n'importe quel site ; chaque citation est vérifiée mot pour mot dans sa page. Une part des questions est ensuite
traduite en français et en allemand (LANGUES) : les fiches sont surtout en anglais, le banc mesure ce que perd une question
posée dans une autre langue.

Conditions : `modele` (de mémoire), `web` (avec la recherche web), chaque graphe seul (sa pyramide de synthèses, comme le
banc), `ensemble` (les graphes à la fois, par les mêmes outils). Les verdicts sont ceux du banc : juste, faux, ne sait pas.

Deux critères sont rendus avec le tableau : la fusion vaut si `ensemble` fait au moins aussi bien que le meilleur graphe
sur chaque thème qui a un corpus ; un thème « a_collecter » vaut d'être collecté si le modèle seul y reste sous 50 %.

    python3 -m newskit.banc_croise --themes <fichier.yaml> --composer        # le jeu, thème par thème (reprend où il s'est arrêté)
    python3 -m newskit.banc_croise --themes <fichier.yaml> --poser           # les conditions, une à une (reprend aussi)
    python3 -m newskit.banc_croise --themes <fichier.yaml> --etat            # le tableau de la dernière mesure

Le jeu, la mesure en cours, l'historique et le tableau sont écrits sous graphe/bancs_croises/<nom du fichier>/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import banc  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402

TOTAL = 100
MINIMUM = 15
LANGUES = {"en": 0.7, "fr": 0.2, "de": 0.1}
NOMS_LANGUES = {"en": "anglais", "fr": "français", "de": "allemand"}
SEUIL_COLLECTE = 0.5
TOURS = 3
SCHEMA_TRADUCTIONS = {"type": "object", "required": ["traductions"], "properties": {"traductions": {"type": "array", "items": {
    "type": "object", "required": ["i", "question"], "properties": {"i": {"type": "integer"}, "question": {"type": "string"}}}}}}


# --- Les thèmes et le jeu ----------------------------------------------------------------------

def charger_themes(f: Path) -> list[dict]:
    """Les thèmes du fichier : clé, libellé (anglais, avec son résumé), sujets en anglais, graphes d'origine."""
    d = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
    brut = d.get("themes")
    if not isinstance(brut, list) or not brut:
        raise ValueError(f"{f} : themes, une liste non vide")
    out = []
    for t in brut:
        sujets = [s["en"] for s in t.get("sujets") or [] if isinstance(s, dict) and s.get("en")]
        if not t.get("cle") or not t.get("en") or not sujets:
            raise ValueError(f"{f} : chaque thème a une clé, un libellé en anglais et des sujets")
        resume = (t.get("resume") or {}).get("en") or ""
        out.append({"cle": t["cle"], "libelle": f"{t['en']} — {resume}".strip(" —"), "sujets": sujets,
                    "graphes": list(t.get("avant") or []), "a_collecter": t.get("etat") == "a_collecter"})
    return out


def graphes_du_socle(themes: list[dict]) -> list[str]:
    return list(dict.fromkeys(g for t in themes for g in t["graphes"]))


def repartition(themes: list[dict], total: int = TOTAL, minimum: int = MINIMUM) -> dict[str, int]:
    """`minimum` questions par thème, le reste au prorata du nombre de sujets (plus forts restes)."""
    if minimum * len(themes) > total:
        raise ValueError(f"minimum de {minimum} par thème impossible : {len(themes)} thèmes pour {total} questions")
    r = {t["cle"]: minimum for t in themes}
    reste, poids = total - minimum * len(themes), sum(len(t["sujets"]) for t in themes)
    parts = {t["cle"]: reste * len(t["sujets"]) / poids for t in themes}
    for cle, p in parts.items():
        r[cle] += int(p)
    manque = total - sum(r.values())
    for cle in sorted(parts, key=lambda c: parts[c] - int(parts[c]), reverse=True)[:manque]:
        r[cle] += 1
    return r


def attribuer_langues(questions: list[dict], parts: dict[str, float] = LANGUES) -> None:
    """Pose `langue` sur chaque question, dans l'ordre du jeu : à chaque pas, la langue la plus en retard sur sa part.
    Les totaux tombent juste et chaque thème, contigu dans le jeu, reçoit sa part de chaque langue."""
    compte = {l: 0 for l in parts}
    for k, q in enumerate(questions, start=1):
        if q.get("langue") in parts:               # une question déjà posée garde sa langue (extension du 01.11.2026)
            compte[q["langue"]] += 1
            continue
        l = max(parts, key=lambda x: parts[x] * k - compte[x])
        q["langue"] = l
        compte[l] += 1


def traduire(questions: list[dict], delai: int) -> None:
    """Met chaque question dans sa langue, l'anglais compris (les questions d'actualité sortent souvent en français) ;
    l'attendu et la citation restent ceux de la source, l'original est gardé sous `question_origine`."""
    for langue in LANGUES:
        qs = [q for q in questions if q.get("langue") == langue and "question_origine" not in q]
        if not qs:
            continue
        rep = banc.claude(
            f"Mets ces questions en {NOMS_LANGUES[langue]}, comme les poserait un praticien de cette langue (une question déjà "
            "dans cette langue reste telle quelle) : les noms propres, les sigles et les titres officiels gardent leur forme "
            "d'usage dans cette langue (AI Act, RGPD/GDPR/DSGVO, nLPD/nFADP/nDSG…). Ne réponds pas aux questions.\n\n"
            + "\n".join(f"{q['i']}. {q['question']}" for q in qs)
            + '\n\nRéponds en JSON : {"traductions": [{"i": <indice>, "question": "…"}, …]}.', SCHEMA_TRADUCTIONS, None, delai)
        par_i = {x["i"]: x["question"] for x in (rep or {}).get("traductions", []) if isinstance(x.get("i"), int) and x.get("question")}
        for q in qs:
            if q["i"] in par_i:
                q["question_origine"], q["question"] = q["question"], par_i[q["i"]]
        print(f"  mise en {NOMS_LANGUES[langue]} : {len(par_i)} sur {len(qs)}", flush=True)


# --- La base des anciens bancs (mainteneur, 01.11.2026 : « on a déjà une base pour éthique, sécurité et conformité ») ---------

BASES = ("themes_banc.json", "banc_questions.json")
TYPES_DE_BASE = ("ag_fond", "ag_recent")          # écrites sans le graphe ; les autres sont tirées de ses fiches
SCHEMA_RANGEMENT = {"type": "object", "required": ["rangement"], "properties": {"rangement": {"type": "array", "items": {
    "type": "object", "required": ["i", "theme"], "properties": {"i": {"type": "integer"}, "theme": {"type": "string"},
                                                              "sujet": {"type": "integer"}}}}}}
LOT_RANGEMENT = 60


def candidats_de_base(graphes: list[str]) -> list[dict]:
    """Les questions des anciens bancs des graphes, écrites sans le graphe et sourcées. Une question « comblée » est écartée :
    le graphe a reçu ensuite la page qui y répond, il la gagnerait par construction."""
    out, vues = [], set()
    for g in graphes:
        for nom in BASES:
            p = RACINE / "graphe" / g / nom
            try:
                qs = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for q in qs:
                cle = (q.get("question") or "").strip()
                if (q.get("type") in TYPES_DE_BASE and not q.get("comblee") and cle and q.get("attendu")
                        and banc.RE_ADRESSE_FINALE.search(q.get("citation") or "") and cle not in vues):
                    vues.add(cle)
                    out.append({"question": cle, "attendu": q["attendu"], "citation": q["citation"], "type": q["type"], "base": g})
    return out


def ranger(candidats: list[dict], themes: list[dict], delai: int) -> list[dict]:
    """Range chaque candidat sous un thème et un sous-thème du fichier, ou sous aucun (l'archive, un sujet hors gouvernance)."""
    plan = "\n".join(f"{t['cle']} : {t['libelle']}\n" + "\n".join(f"    {k}. {x}" for k, x in enumerate(t["sujets"])) for t in themes)
    out = []
    for k in range(0, len(candidats), LOT_RANGEMENT):
        lot = candidats[k:k + LOT_RANGEMENT]
        rep = banc.claude(
            "Range chaque question sous le thème et le sous-thème qui la couvrent, parmi ceux-ci :\n" + plan
            + "\n\nUne question qui ne relève d'aucun (un autre sujet, même voisin) : `theme` vide. `sujet` : le numéro du "
              "sous-thème dans son thème.\n\n" + "\n".join(f"{k + j}. {q['question']}" for j, q in enumerate(lot))
            + '\n\nRéponds en JSON : {"rangement": [{"i": <numéro>, "theme": "<clé ou vide>", "sujet": <numéro>}, …]}.',
            SCHEMA_RANGEMENT, None, delai, modele=banc.MODELE_LECTURE)
        par = {x["i"]: x for x in (rep or {}).get("rangement", []) if isinstance(x.get("i"), int)}
        for j, q in enumerate(lot):
            x = par.get(k + j) or {}
            t = next((t for t in themes if t["cle"] == x.get("theme")), None)
            sj = x.get("sujet")
            out.append({**q, "theme": t["cle"] if t else "",
                        "sujet": t["sujets"][sj] if t and isinstance(sj, int) and 0 <= sj < len(t["sujets"]) else ""})
        print(f"  rangement : lot {k // LOT_RANGEMENT + 1}, {sum(1 for q in out[k:] if q['theme'])} sur {len(lot)} sous un thème", flush=True)
    return out


def reprendre(f: Path, n_par: dict[str, int]) -> int:
    """Comble les thèmes à corpus avec la base, sous-thème après sous-thème ; le tirage complète ensuite. Le rangement est
    gardé (base_rangee.json) : un second passage ne le repaie pas. Rend le nombre de questions ajoutées."""
    themes, d = charger_themes(f), dossier(f)
    d.mkdir(parents=True, exist_ok=True)
    p_jeu, p_rang, bornes = d / "jeu.json", d / "base_rangee.json", cfg.banc()
    questions = json.loads(p_jeu.read_text(encoding="utf-8")) if p_jeu.exists() else []
    if p_rang.exists():
        ranges = json.loads(p_rang.read_text(encoding="utf-8"))
    else:
        ranges = ranger(candidats_de_base(graphes_du_socle(themes)), themes, bornes["delai"])
        p_rang.write_text(json.dumps(ranges, ensure_ascii=False, indent=1), encoding="utf-8")
    pris, ajout = {q["question"] for q in questions} | {q.get("question_origine") for q in questions}, 0
    for t in themes:
        manque = n_par.get(t["cle"], 0) - sum(q["theme"] == t["cle"] for q in questions)
        if t["a_collecter"] or manque <= 0:
            continue
        par_sujet: dict[str, list[dict]] = {}
        for q in ranges:
            if q["theme"] == t["cle"] and q["question"] not in pris:
                par_sujet.setdefault(q["sujet"], []).append(q)
        tour, choix = 0, []
        while len(choix) < manque + 5 and any(len(v) > tour for v in par_sujet.values()):
            choix += [v[tour] for v in par_sujet.values() if len(v) > tour]
            tour += 1
        deja = [q for q in questions if q["theme"] == t["cle"]]
        banc.SUJETS_DOMAINE[t["libelle"]] = t["sujets"]
        neuves = [{**q, "origine": f"base de {q['base']}", "ajoutee": time.strftime("%Y-%m-%d %H:%M")} for q in choix[:manque + 5]]
        tri = banc.hors_sujet_retirees(t["libelle"], deja + neuves)
        neuves = [q for q in tri if q not in deja][:manque]
        questions += neuves
        ajout += len(neuves)
        print(f"  base, thème {t['cle']} : {len(neuves)} question(s) reprise(s) pour {manque} manquante(s)", flush=True)
    for k, q in enumerate(questions):
        q["i"] = k
    p_jeu.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    return ajout


def dossier(f: Path) -> Path:
    return RACINE / "graphe" / "bancs_croises" / Path(f).stem


def composer(f: Path, total: int = TOTAL, minimum: int = MINIMUM, base: bool = False) -> list[dict]:
    themes, d = charger_themes(f), dossier(f)
    d.mkdir(parents=True, exist_ok=True)
    p_jeu, bornes = d / "jeu.json", cfg.banc()
    questions = json.loads(p_jeu.read_text(encoding="utf-8")) if p_jeu.exists() else []
    if len(questions) >= total and all("question_origine" in q for q in questions):
        print(f"  jeu déjà composé : {len(questions)} questions ({p_jeu})")
        return questions
    n_par = repartition(themes, total, minimum)
    if base and any(not t["a_collecter"] and sum(q["theme"] == t["cle"] for q in questions) < n_par[t["cle"]] for t in themes):
        reprendre(f, n_par)
        questions = json.loads(p_jeu.read_text(encoding="utf-8"))
    vues = banc.adresses_de(questions)
    for t in themes:
        n = n_par[t["cle"]]
        gardees = [q for q in questions if q["theme"] == t["cle"]]
        if len(gardees) >= n:
            continue
        banc.SUJETS_DOMAINE[t["libelle"]] = t["sujets"]
        # Jusqu'à TOURS tours : la vérification et le tri retirent un quart des questions tirées (01.11.2026 : 88 sur 100
        # au premier tour). Un tour coupé par la limite n'est pas gardé.
        for tour in range(TOURS):
            manque = n - len(gardees)
            if manque <= 0:
                break
            if couts.en_limite():
                print("  ARRÊT : limite d'usage atteinte, les thèmes restants attendent", file=sys.stderr)
                return questions
            deja = [q for q in gardees]
            n_f = max(0, (n - n // 2) - sum(q["type"] == "ag_fond" for q in deja))
            n_r = max(0, manque - n_f)
            if tour:                               # après un tour court, chaque part peut combler l'autre
                n_f = n_r = manque
            print(f"  thème {t['cle']}, tour {tour + 1} : {manque} question(s) à tirer ({n_f} de fond, {n_r} d'actualité)", flush=True)
            fond = banc.verifier_localement(t["libelle"], banc.questions_de_fond(t["libelle"], t["sujets"], n_f + 4, bornes["delai"]), bornes["delai"]) if n_f else []
            rec = banc.questions_recentes_locales(t["libelle"], t["sujets"], n_r + 3, bornes["delai"], vues) if n_r else []
            if couts.en_limite():                      # un tour coupé n'est pas gardé : il sera retiré en entier
                print(f"  ARRÊT : limite d'usage atteinte pendant le thème {t['cle']}, il sera repris", file=sys.stderr)
                return questions
            horo = time.strftime("%Y-%m-%d %H:%M")
            neuves = [{**q, "type": "ag_fond", "origine": "question de spécialiste, de fond"} for q in fond] + \
                     [{**q, "type": "ag_recent", "origine": "question de spécialiste, actualité"} for q in rec]
            neuves = [{**q, "theme": t["cle"], "ajoutee": horo} for q in neuves]
            tri = banc.hors_sujet_retirees(t["libelle"], deja + neuves)       # le tri voit les gardées : doublons compris
            neuves = [q for q in tri if q not in deja]
            choix = [q for q in neuves if q["type"] == "ag_fond"][:n_f] + [q for q in neuves if q["type"] == "ag_recent"][:n_r]
            choix += [q for q in neuves if q not in choix][:max(0, manque - len(choix))]   # une part qui manque, l'autre la comble
            choix = choix[:manque]
            gardees += choix
            questions += choix
            vues |= banc.adresses_de(choix)
            for k, q in enumerate(questions):
                q["i"] = k
            p_jeu.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"    {t['cle']} : {len(gardees)} question(s) sur {n}", flush=True)
    attribuer_langues(questions)
    traduire(questions, bornes["delai"])
    p_jeu.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  jeu : {len(questions)} questions, " + ", ".join(f"{l} {sum(q['langue'] == l for q in questions)}" for l in LANGUES))
    return questions


# --- Les conditions ----------------------------------------------------------------------------

def consigne_ensemble(graphes: list[str], questions: list[dict]) -> tuple[str, list[str]]:
    """La consigne de la condition « ensemble » : les outils de chaque graphe, comme repondre_par_syntheses pour un seul."""
    S, I = banc.OUTIL_SYNTHESE, banc.OUTIL_GRAPHE
    commandes = "\n".join(f"  {S} repondre --graphe {g} \"<la question>\"\n  {S} descendre --graphe {g} R<n>\n"
                          f"  {I} --graphe {g} passages \"<mots précis>\"" for g in graphes)
    texte = (f"Réponds à ces questions en n'employant QUE {len(graphes)} graphes de connaissances locaux ({', '.join(graphes)}). "
             "Ta mémoire ne compte pas : si aucun graphe ne le dit, tu ne sais pas. Interroge chaque graphe dont le sujet peut "
             "répondre ; les synthèses d'abord (thème, notions, extraits avec leurs renvois R<n>), le paragraphe cité ensuite, "
             "le texte des fiches enfin pour un chiffre, un nom ou une date exacts (fiches surtout en anglais : mots-clés "
             "anglais).\n\n" + commandes + "\n\n"
             "Une commande par appel d'outil, qui commence exactement par l'outil. Le nom et la version exacts doivent "
             "correspondre ; reprends les mots de la source pour un terme exact.\n\n" + banc.consigne_de_reponse(questions))
    return texte, [f"Bash({S}:*)", f"Bash({I}:*)"]


# La recherche fixe (mainteneur, 1er novembre 2026 : « ajoute la colonne recherche fixe et mesure-la »). Au banc complet, la
# condition « ensemble » coûtait 0,073 $ par question : un agent Opus explore les graphes pendant 17 tours en moyenne
# et relit tout son contexte à chaque tour. Ici le programme interroge lui-même chaque graphe (synthese.py repondre :
# thème, notions, meilleurs passages ; aucun modèle), et le modèle reçoit en un seul appel, sans outil, la matière de
# chaque question. C'est la requête que ferait le produit ; l'agent libre reste la mesure du plafond.
CONDITIONS_FIXES = {"fixe": None, "fixe-sonnet": "claude-sonnet-5-5"}     # None : le modèle de la pyramide
BORNE_FIXE = 6000                                                         # caractères par graphe et par question
CACHE_FIXE: Path | None = None                                            # posé par poser() : <dossier>/matieres_fixes.json


def _recherche_synthese(graphe: str, question: str) -> str:
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415
    from newskit import synthese  # noqa: PLC0415
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        synthese.repondre(graphe, question, garder=False)
    return b.getvalue()


def matiere_fixe(graphes: list[str], question: str, recherche=_recherche_synthese, borne: int = BORNE_FIXE) -> str:
    """Ce que chaque graphe rend pour la question, sous son nom, borné à `borne` caractères par graphe."""
    return "\n\n".join(f"=== {g}\n{recherche(g, question)[:borne]}" for g in graphes)


def matieres_fixes(questions: list[dict], graphes: list[str], recherche=_recherche_synthese, cache: Path | None = None) -> dict[int, str]:
    """La matière de chaque question, gardée dans `cache` (les deux modèles lisent la même, et une pose coupée reprend)."""
    gardees = json.loads(cache.read_text(encoding="utf-8")) if cache and cache.exists() else {}
    for q in questions:
        if str(q["i"]) not in gardees:
            gardees[str(q["i"])] = matiere_fixe(graphes, q["question"], recherche)
            if cache:
                cache.write_text(json.dumps(gardees, ensure_ascii=False), encoding="utf-8")
    return {q["i"]: gardees[str(q["i"])] for q in questions}


def consigne_fixe(questions: list[dict], matieres: dict[int, str]) -> str:
    """Un seul appel, sans outil : chaque question suivie de ce que les graphes en disent."""
    blocs = "\n\n".join(f"### Question {q['i']} : {q['question']}\n\nCe que disent les graphes :\n{matieres.get(q['i'], '')}"
                          for q in questions)
    return ("Réponds à ces questions en n'employant QUE ce que les graphes de connaissances en disent ci-dessous (synthèses, "
            "notions, extraits de fiches, avec leurs renvois). Ta mémoire ne compte pas : si la matière ne le dit pas, tu ne "
            "sais pas. Le nom et la version exacts doivent correspondre ; reprends les mots de la source pour un terme exact. "
            "Pour `source`, nomme le graphe et la fiche ou le renvoi.\n\n" + blocs + "\n\n" + banc.consigne_de_reponse(questions))


def _reponses(rep: dict | None) -> dict[int, dict]:
    return {x["i"]: {"reponse": str(x.get("reponse") or "")[:600], "sait": bool(x.get("sait")), "source": str(x.get("source") or "")[:200]}
            for x in (rep or {}).get("reponses", []) if isinstance(x.get("i"), int)}


def repondre(condition: str, questions: list[dict], themes: list[dict], graphes: list[str], bornes: dict,
             cache: Path | None = None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if condition in CONDITIONS_FIXES:
        compte = banc.CONSO[condition] = {}
        matieres = matieres_fixes(questions, graphes, _recherche_synthese, cache or CACHE_FIXE)
        modele = CONDITIONS_FIXES[condition] or banc.modele_pyramide(graphes[0])
        for k in range(0, len(questions), banc.LOT_SYNTHESE):
            lot = questions[k:k + banc.LOT_SYNTHESE]
            rep = banc.claude(consigne_fixe(lot, matieres), banc.SCHEMA_REPONSES, None, bornes["delai"], compte, modele=modele)
            out.update(_reponses(rep))
            print(f"    {condition} : lot {k // banc.LOT_SYNTHESE + 1}, {len((rep or {}).get('reponses', []))} réponse(s)", flush=True)
        return out
    if condition in ("modele", "web"):                 # par thème : la consigne du banc nomme le domaine
        for t in themes:
            qs = [q for q in questions if q["theme"] == t["cle"]]
            if qs:
                out.update(banc.repondre(t["libelle"], condition, qs, bornes))
        return out
    if condition in graphes:
        return banc.repondre(condition, "graphe", questions, bornes)
    compte = banc.CONSO[condition] = {}
    for k in range(0, len(questions), banc.LOT_SYNTHESE):
        lot = questions[k:k + banc.LOT_SYNTHESE]
        texte, outils = consigne_ensemble(graphes, lot)
        rep = banc.claude(texte, banc.SCHEMA_REPONSES, outils, bornes["delai"], compte, charges=["Bash"],
                          modele=banc.modele_pyramide(graphes[0]), systeme=banc.SYSTEME_COURT)
        out.update(_reponses(rep))
        print(f"    ensemble : lot {k // banc.LOT_SYNTHESE + 1}, {len((rep or {}).get('reponses', []))} réponse(s)", flush=True)
    return out


def conditions(graphes: list[str]) -> list[str]:
    return ["modele", "web", *graphes, "ensemble", *CONDITIONS_FIXES]


# --- Le tableau et les critères ----------------------------------------------------------------

def _origine(q: dict) -> str:
    """« base » pour une question reprise d'un ancien banc (le graphe en a vu passer les réponses), sinon « tirage »."""
    return "base" if str(q.get("origine") or "").startswith("base") else "tirage"


def tableau(questions: list[dict], verdicts: dict[str, dict[int, str]]) -> dict:
    out = {}
    for c, v in verdicts.items():
        qs = [q for q in questions if q["i"] in v]
        par = lambda cle: {k: banc.parts([v[q["i"]] for q in qs if q.get(cle) == k]) for k in dict.fromkeys(q.get(cle) for q in qs)}  # noqa: E731
        out[c] = {"ensemble": banc.parts([v[q["i"]] for q in qs]), "themes": par("theme"), "langues": par("langue"),
                  "types": par("type"), "origines": {k: banc.parts([v[q["i"]] for q in qs if _origine(q) == k])
                                                       for k in dict.fromkeys(_origine(q) for q in qs)}}
    return out


SEUIL_GOUVERNANCE = 0.85      # GV6 : au moins 85 réponses justes sur 100


def criteres(t: dict, themes: list[dict], graphes: list[str], fusionne: str | None = None) -> dict:
    """La fusion vaut si `ensemble` tient le meilleur graphe sur chaque thème à corpus ; un thème à collecter vaut d'être
    collecté si le modèle seul y fait moins de SEUIL_COLLECTE."""
    def juste(c, cle):
        return ((t.get(c) or {}).get("themes", {}).get(cle) or {}).get("juste") or 0.0
    sous = [th["cle"] for th in themes if not th["a_collecter"]
            and juste("ensemble", th["cle"]) < max(juste(g, th["cle"]) for g in graphes)]
    out = {"fusion": {"vaut": not sous, "themes_sous_le_meilleur": sous},
           "collecte": {th["cle"]: juste("modele", th["cle"]) < SEUIL_COLLECTE for th in themes if th["a_collecter"]}}
    if fusionne:                 # GV6 : le graphe fusionné tient 85 % et, sur chaque thème, le meilleur de GV1
        tout = ((t.get(fusionne) or {}).get("ensemble") or {}).get("juste") or 0.0
        sous_gv1 = [th["cle"] for th in themes
                    if juste(fusionne, th["cle"]) < max(juste(c, th["cle"]) for c in [*graphes, "ensemble"])]
        out["gouvernance"] = {"vaut": tout >= SEUIL_GOUVERNANCE and not sous_gv1, "juste": tout, "themes_sous_gv1": sous_gv1}
    return out


def en_markdown(mesure: dict) -> str:
    t, conds = mesure["tableau"], list(mesure["tableau"])
    pc = lambda p: "—" if not p or p.get("juste") is None else f"{round(p['juste'] * 100)}"  # noqa: E731
    lignes = [f"# Banc croisé : {mesure['nom']}, {mesure['horodatage']}", "",
              f"{mesure['questions']} questions. Parts de réponses justes, en %.", "",
              "| Thème | n | " + " | ".join(conds) + " |", "|---|---|" + "---|" * len(conds)]
    for cle in mesure["themes"]:
        n = (t[conds[0]]["themes"].get(cle) or {}).get("n", 0)
        lignes.append(f"| {cle} | {n} | " + " | ".join(pc(t[c]["themes"].get(cle)) for c in conds) + " |")
    lignes.append("| **ensemble** | " + str(mesure["questions"]) + " | " + " | ".join(f"**{pc(t[c]['ensemble'])}**" for c in conds) + " |")
    lignes += ["", "| Langue | n | " + " | ".join(conds) + " |", "|---|---|" + "---|" * len(conds)]
    for l in LANGUES:
        n = (t[conds[0]]["langues"].get(l) or {}).get("n", 0)
        lignes.append(f"| {l} | {n} | " + " | ".join(pc(t[c]["langues"].get(l)) for c in conds) + " |")
    lignes += ["", "| Origine | n | " + " | ".join(conds) + " |", "|---|---|" + "---|" * len(conds)]
    for o in ("tirage", "base"):
        n = (t[conds[0]].get("origines", {}).get(o) or {}).get("n", 0)
        if n:
            lignes.append(f"| {o} | {n} | " + " | ".join(pc(t[c].get("origines", {}).get(o)) for c in conds) + " |")
    lignes += ["", "| Faux, en % | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds),
               "| tout | " + " | ".join(f"{round((t[c]['ensemble'].get('faux') or 0) * 100)}" for c in conds) + " |"]
    cr = mesure["criteres"]
    lignes += ["", f"Fusion : {'vaut' if cr['fusion']['vaut'] else 'ne vaut pas en l’état'}"
               + (f" (sous le meilleur graphe : {', '.join(cr['fusion']['themes_sous_le_meilleur'])})" if cr['fusion']['themes_sous_le_meilleur'] else ""),
               "Collecte (modèle seul sous 50 %) : " + ", ".join(f"{k} {'oui' if v else 'non'}" for k, v in cr["collecte"].items())]
    if cr.get("gouvernance"):
        g = cr["gouvernance"]
        lignes.append(f"Graphe fusionné : {'vaut' if g['vaut'] else 'ne vaut pas en l’état'} ({round(g['juste'] * 100)} % juste"
                      + (f" ; sous GV1 : {', '.join(g['themes_sous_gv1'])}" if g["themes_sous_gv1"] else "") + ")")
    if mesure.get("couts"):
        lignes += ["", "Coût par question, en $ : " + ", ".join(f"{c} {v.get('cout_usd_par_question')}" for c, v in mesure["couts"].items())]
    return "\n".join(lignes) + "\n"


def _poses(d: Path) -> dict:
    """Ce qui est déjà posé : l'état en cours, sinon la dernière mesure (ses verdicts sont dans `details`). Une extension
    du jeu (100 → 250 questions, 01.11.2026) ne repose que les questions neuves."""
    p_cours, p_hist = d / "en_cours.json", d / "historique.jsonl"
    if p_cours.exists():
        return json.loads(p_cours.read_text(encoding="utf-8"))
    vide = {"verdicts": {}, "reponses": {}, "couts": {}}
    if not p_hist.exists():
        return vide
    try:
        m = json.loads(p_hist.read_text(encoding="utf-8").splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return vide
    conds = list(m.get("couts") or m.get("reponses") or {})
    return {"verdicts": {c: {str(x["i"]): x[c] for x in m.get("details", []) if x.get(c)} for c in conds},
            "reponses": m.get("reponses") or {}, "couts": m.get("couts") or {}}


def _cumuler(avant: dict | None, conso: dict, n: int) -> dict:
    """Le coût d'une condition cumulé sur ses passes : total et nombre de questions, puis le prix d'une question."""
    avant = avant or {}
    total, q = round(float(avant.get("cout_usd") or 0) + float(conso.get("cout_usd") or 0), 4), int(avant.get("questions") or 0) + n
    return {"cout_usd": total, "questions": q, "cout_usd_par_question": round(total / q, 4) if q else None}


def poser(f: Path, etiquette: str = "", graphe: str | None = None) -> dict | None:
    """Pose les conditions du banc. Avec `graphe` (GV6) : le jeu de la mesure de référence, posé à ce graphe seul (et à
    la recherche fixe sur lui), dans `<dossier>__<graphe>` ; le modèle, le web, les graphes du socle et leur ensemble
    gardent les verdicts de la référence, pour comparer thème par thème."""
    themes, d = charger_themes(f), dossier(f)
    graphes, reference, ref = graphes_du_socle(themes), [], {}
    conds = conditions(graphes)
    if graphe:
        ref, jeu_ref = _poses(d), d / "jeu.json"
        d = d.parent / f"{d.name}__{graphe}"
        d.mkdir(parents=True, exist_ok=True)
        if jeu_ref.exists() and not (d / "jeu.json").exists():
            (d / "jeu.json").write_text(jeu_ref.read_text(encoding="utf-8"), encoding="utf-8")
        reference, graphes, conds = graphes, [graphe], [graphe, *CONDITIONS_FIXES]
    p_jeu, p_cours = d / "jeu.json", d / "en_cours.json"
    if not p_jeu.exists():
        print("  pas de jeu (--composer d'abord)", file=sys.stderr)
        return None
    questions, bornes = json.loads(p_jeu.read_text(encoding="utf-8")), cfg.banc()
    cours = _poses(d)
    for c in (x for x in conditions(reference) if x not in CONDITIONS_FIXES):
        cours["verdicts"].setdefault(c, ref.get("verdicts", {}).get(c, {}))
    global CACHE_FIXE
    CACHE_FIXE = d / "matieres_fixes.json"
    for c in conds:
        faites = cours["verdicts"].get(c, {})
        a_poser = [q for q in questions if str(q["i"]) not in faites]
        if not a_poser:
            print(f"  {c} : déjà posé, gardé", flush=True)
            continue
        if couts.en_limite():
            print(f"  ARRÊT : limite d'usage atteinte avant « {c} », état dans {p_cours}", file=sys.stderr)
            return None
        print(f"  {c} : {len(a_poser)} question(s) à poser ({len(faites)} déjà posée(s))", flush=True)
        banc.CONTEXTE["poste"] = "banc_croise"
        rep = repondre(c, a_poser, themes, graphes, bornes)
        conso = {}
        for v in banc.CONSO.values():
            for k, x in v.items():
                conso[k] = round(conso.get(k, 0) + x, 4)
        banc.CONSO.clear()
        verdicts = banc.juger(a_poser, rep, bornes["delai"])
        cours["reponses"].setdefault(c, {}).update({str(i): r for i, r in rep.items()})
        cours["verdicts"][c] = {**faites, **{str(i): v for i, v in verdicts.items()}}
        cours["couts"][c] = _cumuler(cours["couts"].get(c), conso, len(a_poser))
        p_cours.write_text(json.dumps(cours, ensure_ascii=False), encoding="utf-8")
        print(f"    {c} : {round(banc.parts(list(verdicts.values()))['juste'] * 100)} % juste sur les {len(a_poser)} posée(s)", flush=True)
    verdicts = {c: {int(i): v for i, v in vs.items()} for c, vs in cours["verdicts"].items()}
    t = tableau(questions, verdicts)
    mesure = {"nom": Path(f).stem, "horodatage": time.strftime("%Y-%m-%dT%H-%M"), "etiquette": etiquette,
              "questions": len(questions), "graphes": graphes, "themes": [th["cle"] for th in themes],
              "tableau": t, "criteres": criteres(t, themes, reference or graphes, graphe), "couts": cours["couts"],
              **({"reference": reference} if graphe else {}),
              "details": [{"i": q["i"], "theme": q["theme"], "langue": q.get("langue"), "type": q["type"],
                           "origine": q.get("origine"), **{c: cours["verdicts"][c].get(str(q["i"])) for c in cours["verdicts"]}}
                          for q in questions],
              "reponses": cours["reponses"]}
    with open(d / "historique.jsonl", "a", encoding="utf-8") as h:
        h.write(json.dumps(mesure, ensure_ascii=False) + "\n")
    (d / "tableau.md").write_text(en_markdown(mesure), encoding="utf-8")
    p_cours.unlink(missing_ok=True)
    print(en_markdown(mesure))
    return mesure


def etat(f: Path) -> int:
    p = dossier(f) / "historique.jsonl"
    if not p.exists():
        print("  aucune mesure")
        return 1
    print(en_markdown(json.loads(p.read_text(encoding="utf-8").splitlines()[-1])))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--themes", type=Path, required=True, help="le fichier des thèmes (clé themes)")
    ap.add_argument("--composer", action="store_true")
    ap.add_argument("--poser", action="store_true")
    ap.add_argument("--etat", action="store_true")
    ap.add_argument("--etiquette", default="")
    ap.add_argument("--total", type=int, default=TOTAL)
    ap.add_argument("--minimum", type=int, default=MINIMUM)
    ap.add_argument("--base", action="store_true", help="combler d'abord avec les anciens bancs des graphes")
    ap.add_argument("--graphe", default=None, help="GV6 : poser le jeu à ce graphe seul, les autres verdicts repris")
    a = ap.parse_args(argv)
    f = a.themes if a.themes.is_absolute() else RACINE / a.themes
    if a.etat:
        return etat(f)
    if a.composer and len([q for q in composer(f, a.total, a.minimum, a.base) if "question_origine" in q]) < a.total:
        return 1                                       # composition inachevée : la relancer reprend où elle s'est arrêtée
    if a.poser:
        return 0 if poser(f, a.etiquette, graphe=a.graphe) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

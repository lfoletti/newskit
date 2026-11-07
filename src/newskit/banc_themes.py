#!/usr/bin/env python3
"""Le banc par thème : chaque thème d'un graphe étudié et questionné à part, comme le ferait un spécialiste.

Décision du porteur (08.10.2026, pilote sur un graphe d'image) : un score global sur 100 questions mélangées dit que le graphe
est faible, pas où. Ici, pour chaque thème (un sujet déclaré du graphe, ou un thème donné), dix questions qu'un
spécialiste de ce thème poserait — moitié de fond, écrites sans connaître le graphe puis vérifiées sur le web, moitié
sur l'actualité du thème, tirées de pages récentes de n'importe quel site (même méthode que le banc agnostique) —
posées au graphe seul. Le résultat est une carte : où le graphe tient, où il est creux, quoi approfondir d'abord.

Un thème est « couvert en profondeur » quand le graphe répond juste à au moins 7 questions sur 10. Dix questions par
thème donnent une marge de ±15 points : assez pour classer les thèmes et voir un progrès net, pas pour suivre deux
points. Le jeu est gardé (graphe/<nom>/themes_banc.json) et reposé tel quel d'une mesure à l'autre.

    python3 -m newskit.banc_themes --graphe <graphe> --composer --themes "thème 1" "thème 2" …   # tire le jeu
    python3 -m newskit.banc_themes --graphe <graphe> --poser --etiquette "avant références"     # mesure
    python3 -m newskit.banc_themes --graphe <graphe> --etat                                     # les mesures, par thème
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import banc  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

PASSAGES_SECOND_ESSAI = 10     # extraits servis à la tentative élargie (10.10.2026)
SEUIL_PROFONDEUR = 0.7          # verdict publié : « tenu » à partir de 70 % de réponses justes
SEUIL_APPROFONDISSEMENT = 0.9   # mais on creuse tout sujet sous 90 % (09.10.2026 : « tenu » n'est pas « fini »)
SOUS_JEUX_PAR_PASSAGE = 2       # sujets déclarés sans sous-jeu composés à un passage (--poser-du-jour) ; un appel chacun


def chemins(graphe: str) -> tuple[Path, Path]:
    base = cfg.chemins_du_graphe(graphe)["base"]
    return base / "themes_banc.json", base / "themes_banc_historique.jsonl"


def composer(graphe: str, themes: list[str], par_theme: int, delai: int, p_jeu: Path | None = None, local: bool | None = None,
             completer: bool = False) -> list[dict]:
    """Pour chaque thème : la moitié de questions de fond, l'autre sur l'actualité du thème, triées comme le banc.
    Le jeu est écrit après CHAQUE thème dans `p_jeu` et un thème déjà présent n'est pas recomposé (08.10.2026 : le
    pilote d'un graphe d'image, interrompu par la pause neuf minutes après son lancement, n'avait rien écrit ; ses recherches web
    étaient perdues). En limite d'usage, les thèmes restants attendent."""
    questions: list[dict] = []
    if p_jeu and p_jeu.exists():
        questions = json.loads(p_jeu.read_text(encoding="utf-8"))
        deja = {q.get("theme") for q in questions}
        if deja & set(themes) and not completer:
            print(f"  {len(deja & set(themes))} thème(s) déjà composé(s), gardés")
            themes = [th for th in themes if th not in deja]
    vues: set[str] = banc.adresses_de(questions)
    local = banc.lecture_locale(local)
    for th in themes:
        if couts.en_limite():
            print("  ARRÊT : limite d'usage atteinte, les thèmes restants attendent", file=sys.stderr)
            break
        n_f, n_r = par_theme // 2, par_theme - par_theme // 2
        print(f"  thème : {th[:90]}", flush=True)
        if local:      # le modèle cherche, nous lisons (banc.questions_recentes_locales, verifier_localement)
            fond = banc.verifier_localement(graphe, banc.questions_de_fond(graphe, [th], n_f + 3, delai), delai)
            rec = banc.questions_recentes_locales(graphe, [th], n_r + 2, delai, vues)
        else:
            fond = banc.verifier_sur_le_web(banc.questions_de_fond(graphe, [th], n_f + 3, delai), delai)
            rec = banc.questions_recentes(graphe, [th], n_r + 2, delai, vues)
        horo = time.strftime("%Y-%m-%d %H:%M")
        lot = [{**q, "type": "ag_fond", "origine": "question de spécialiste, de fond", "source": "", "theme": th, "ajoutee": horo} for q in fond] + \
              [{**q, "type": "ag_recent", "origine": "question de spécialiste, actualité", "source": "", "theme": th, "ajoutee": horo} for q in rec]
        lot = banc.hors_sujet_retirees(graphe, lot)
        lot = [q for q in lot if q["type"] == "ag_fond"][:n_f] + [q for q in lot if q["type"] == "ag_recent"][:n_r]
        vues |= banc.adresses_de(lot)
        print(f"    {len(lot)} question(s) gardée(s)", flush=True)
        questions += lot
        for i, q in enumerate(questions):
            q["i"] = i
        if p_jeu:
            p_jeu.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    for i, q in enumerate(questions):
        q["i"] = i
    return questions


def poser(graphe: str, etiquette: str, themes: list[str] | None = None, enregistrer: bool = True) -> dict | None:
    """`themes` : les seuls thèmes à poser ; `enregistrer` faux : un essai — rien n'entre dans l'historique ni dans l'état
    des sujets, le résultat s'affiche seulement (échantillon de banc, porteur, 10.10.2026)."""
    p_jeu, p_hist = chemins(graphe)
    if not p_jeu.exists():
        print(f"  {graphe} : pas de jeu par thème (--composer d'abord)", file=sys.stderr)
        return None
    questions = [q for q in json.loads(p_jeu.read_text(encoding="utf-8")) if not q.get("comblee")]
    if themes:
        questions = [q for q in questions if any(th.casefold() in q["theme"].casefold() for th in themes)]
    if not questions:
        print(f"  {graphe} : toutes les questions du jeu sont comblées, rien à poser", file=sys.stderr)
        return None
    bornes = cfg.banc()
    p_cours = p_jeu.with_name("themes_banc_en_cours.json")
    # Thème par thème, l'état écrit après chacun : une mesure dure une heure et ne doit rien perdre.
    reponses: dict[int, dict] = {}; verdicts: dict[int, str] = {}; par_theme: dict[str, dict] = {}
    conso: dict = {}                                  # ce que coûte une réponse, cumulé sur les thèmes (09.10.2026)
    for th in dict.fromkeys(q["theme"] for q in questions):
        qs = [q for q in questions if q["theme"] == th]
        rep = banc.repondre(graphe, "graphe", qs, bornes)
        for k, v in banc.CONSO.pop("graphe", {}).items():
            conso[k] = round(conso.get(k, 0) + v, 4)
        # Second essai pour les seules questions restées sans réponse (porteur, 10.10.2026) : le lecteur varie d'une pose à
        # l'autre — ce matin, quatre « ne sait pas » sur cinq sont revenus justes à la repose, le contexte n'ayant pas
        # changé. Une réponse donnée au second essai est jugée comme les autres ; une question deux fois sans réponse
        # reste « ne sait pas ». Coûte la part des sans-réponse, retire l'essentiel du bruit du lecteur.
        sans = [q for q in qs if not (rep.get(q["i"]) or {}).get("reponse", "").strip()]
        if sans and not couts.en_limite():
            # Second essai élargi (porteur, 10.10.2026) : reposer sur le même contexte ne rendait rien (plateformes et
            # mineurs : 5 « ne sait pas » sur 5 au second essai) ; la tentative élargie sert dix extraits au lieu de trois.
            os.environ["KG_PASSAGES"] = str(PASSAGES_SECOND_ESSAI)
            try:
                rep2 = banc.repondre(graphe, "graphe", sans, bornes)
            finally:
                os.environ.pop("KG_PASSAGES", None)
            for k, v in banc.CONSO.pop("graphe", {}).items():
                conso[k] = round(conso.get(k, 0) + v, 4)
            revenues = {i: r for i, r in rep2.items() if (r or {}).get("reponse", "").strip()}
            rep.update(revenues)
            print(f"      second essai : {len(sans)} « ne sait pas », {len(revenues)} répondue(s) au second essai", flush=True)
        if couts.en_limite():
            print(f"  ARRÊT : limite d'usage atteinte au thème « {th[:60]} » — mesure non enregistrée, état dans "
                  f"{p_cours.name}", file=sys.stderr)
            p_cours.write_text(json.dumps({"reponses": reponses, "verdicts": verdicts}, ensure_ascii=False), encoding="utf-8")
            return None
        reponses.update(rep)
        verdicts.update(banc.juger(qs, rep, bornes["delai"]))
        par_theme[th] = {"ensemble": banc.parts([verdicts[q["i"]] for q in qs]),
                         "fond": banc.parts([verdicts[q["i"]] for q in qs if q["type"] == "ag_fond"]),
                         "actualite": banc.parts([verdicts[q["i"]] for q in qs if q["type"] == "ag_recent"]),
                         "profond": banc.parts([verdicts[q["i"]] for q in qs])["juste"] >= SEUIL_PROFONDEUR}
        print(f"    {th[:70]:70} {round(par_theme[th]['ensemble']['juste'] * 100):3d} % juste"
              f"{'  profond' if par_theme[th]['profond'] else ''}", flush=True)
        p_cours.write_text(json.dumps({"par_theme": par_theme, "verdicts": verdicts}, ensure_ascii=False), encoding="utf-8")
    if sum(1 for v in reponses.values() if v.get("reponse")) < len(questions) // 5:
        print(f"  {graphe} : réponses manquantes (limite d'usage ?) — mesure non enregistrée", file=sys.stderr)
        return None
    # Le « socle » comme mesure de profondeur (porteur, 08.10.2026) : un thème est tenu quand le graphe répond juste à
    # SEUIL_PROFONDEUR de ses questions, et non quand ses notions existent comme nœuds.
    profonds = sum(1 for v in par_theme.values() if v["profond"])
    mesure = {"graphe": graphe, "horodatage": time.strftime("%Y-%m-%dT%H-%M"), "etiquette": etiquette,
              "methode": banc.methode_du_graphe(graphe), "ensemble": banc.parts(list(verdicts.values())),
              "profondeur": {"themes_profonds": profonds, "themes": len(par_theme), "seuil": SEUIL_PROFONDEUR},
              "couts": banc.par_question(conso, len(questions)), "par_theme": par_theme,
              "details": [{"i": q["i"], "theme": q["theme"], "type": q["type"], "verdict": verdicts[q["i"]],
                           "reponse": reponses.get(q["i"], {}).get("reponse", "")} for q in questions]}
    if themes and enregistrer and p_hist.exists():
        # Une pose partielle complète la dernière mesure (11.10.2026) : les sujets non posés gardent leurs résultats et
        # la ligne d'historique reste entière — la page et l'acuité lisent la dernière ligne, qui montrerait sinon le
        # seul sujet posé. C'est la convention du complément du 10.10.2026, jusqu'ici assemblé à la main.
        try:
            derniere = json.loads(p_hist.read_text(encoding="utf-8").splitlines()[-1])
        except (OSError, json.JSONDecodeError, IndexError):
            derniere = {}
        if derniere.get("par_theme"):
            poses = set(par_theme)
            pt = {**derniere["par_theme"], **par_theme}
            det = [d for d in derniere.get("details", []) if d.get("theme") not in poses] + mesure["details"]
            profonds = sum(1 for v in pt.values() if v["profond"])
            mesure.update({"par_theme": pt, "details": det, "ensemble": banc.parts([d["verdict"] for d in det]),
                           "profondeur": {"themes_profonds": profonds, "themes": len(pt), "seuil": SEUIL_PROFONDEUR},
                           "etiquette": f"{etiquette} — complément : {len(poses)} sujet(s) posé(s) sur la mesure du "
                                        f"{derniere['horodatage'][:10]}", "complement_de": derniere["horodatage"]})
            par_theme = pt
    p_cours.unlink(missing_ok=True)
    if not enregistrer:
        afficher([mesure])
        print(f"  essai non enregistré : {profonds} thème(s) tenu(s) sur {len(par_theme)}")
        return mesure
    with open(p_hist, "a", encoding="utf-8") as h:
        h.write(json.dumps(mesure, ensure_ascii=False) + "\n")
    # Un sujet neuf tenu au banc par thème sort de l'approfondissement (themes_emergents.nouveaux).
    etat_t = te.lire_etat(graphe)
    tenus = [th for th, v in par_theme.items() if v["ensemble"]["juste"] >= SEUIL_APPROFONDISSEMENT
             and etat_t["nouveaux"].get(th, {}).get("statut") == "en cours"]
    for th in tenus:
        etat_t["nouveaux"][th]["statut"] = "tenu"; etat_t["nouveaux"][th]["tenu_le"] = time.strftime("%Y-%m-%d")
    # Un sujet sous SEUIL_APPROFONDISSEMENT entre en approfondissement comme un sujet neuf (09.10.2026) : tête de
    # l'éclaireur et du poseur (les plus faibles d'abord), suivi dans le rapport, jusqu'à 90 % — « tenu » (70 %) est
    # le verdict de la page, pas la fin du travail.
    faibles = [th for th, v in par_theme.items() if v["ensemble"]["juste"] < SEUIL_APPROFONDISSEMENT
               and etat_t["nouveaux"].get(th, {}).get("statut") != "en cours"]
    for th, v in par_theme.items():                 # le score à jour, pour ordonner la file
        if th in etat_t["nouveaux"]:
            etat_t["nouveaux"][th]["juste"] = round(v["ensemble"]["juste"], 2)
    for th in faibles:
        etat_t["nouveaux"][th] = {"date": time.strftime("%Y-%m-%d"), "cle": te.cle_de(th), "nuits": 0, "arxiv": [],
                                  "statut": "en cours", "notions": [], "origine": "banc par thème",
                                  "juste": round(par_theme[th]["ensemble"]["juste"], 2)}
    if tenus or faibles:
        te.ecrire_etat(graphe, etat_t)
    if tenus or faibles:
        te.ecrire_etat(graphe, etat_t)
    if tenus:
        print(f"  sujet(s) sortis de l'approfondissement (≥ {SEUIL_APPROFONDISSEMENT:.0%}) : {' ; '.join(t[:50] for t in tenus)}")
    if faibles:
        print(f"  sujet(s) sous {SEUIL_APPROFONDISSEMENT:.0%}, en approfondissement : {' ; '.join(t[:50] for t in faibles)}")
    afficher([mesure])
    print(f"  profondeur : {profonds} thème(s) tenu(s) sur {len(par_theme)} (≥ {SEUIL_PROFONDEUR:.0%} de réponses justes)")
    return mesure


def combler(graphe: str, delai: int, themes: list[str] | None = None, questions: list[int] | None = None) -> int:
    """Boucle le banc par sujet sur l'ingestion, comme banc.py --cycle : la page de chaque question manquée à la dernière
    mesure entre au graphe par la voie de l'éclaireur, la question est marquée « comblée » (hors mesure : on ne reteste
    pas une page qu'on a fait entrer), et une question neuve la remplace sur le même sujet (09.10.2026)."""
    from newskit import eclaireur  # noqa: PLC0415
    p_jeu, p_hist = chemins(graphe)
    if not p_jeu.exists() or not p_hist.exists():
        print(f"  {graphe} : pas de jeu ou pas de mesure par thème"); return 0
    jeu = json.loads(p_jeu.read_text(encoding="utf-8"))
    par_i = {q["i"]: q for q in jeu}
    m = json.loads(p_hist.read_text(encoding="utf-8").splitlines()[-1])
    manquees = [par_i[d["i"]] for d in m.get("details", []) if d.get("verdict") != "juste" and d["i"] in par_i and not par_i[d["i"]].get("comblee")]
    if themes:        # --themes : les seuls sujets à combler (porteur, 11.10.2026 : un sujet, pas tout le jeu)
        manquees = [q for q in manquees if any(th.casefold() in q["theme"].casefold() for th in themes)]
    if questions:     # --questions : les seules questions à combler (une perte à la repose se repose avant d'être crue)
        manquees = [q for q in manquees if int(q["i"]) in set(questions)]
    if not manquees:
        print(f"  {graphe} : aucune question manquée à combler"); return 0
    bornes = cfg.eclaireur(graphe)
    interdites = banc.adresses_du_banc(graphe) if hasattr(banc, "adresses_du_banc") else set()
    memoire, connues = eclaireur.lire_memoire(), eclaireur.adresses_connues(graphe)
    aujourdhui = time.strftime("%Y-%m-%d")
    comblees: dict[str, int] = {}
    for q in manquees:
        url = next(iter(banc.adresses_de([q])), "")
        if not url or url in interdites:
            continue
        ligne = eclaireur.ingerer(graphe, {"url": url, "titre": "", "point": 0}, [("banc par thème", q["question"])],
                                  bornes, connues, memoire, aujourdhui)
        if ligne.startswith("  --  écartée"):      # relue par lecture_web, comme un lien déposé (11.10.2026)
            ligne = eclaireur.relire_et_ecrire(graphe, url, "banc par thème", q["question"], bornes, connues, aujourdhui, ligne)
        print(f"    {ligne.strip()[:120]}")
        if ligne.startswith("  ok") or "déjà au graphe" in ligne:
            q["comblee"] = aujourdhui; q["ingeree"] = ligne.startswith("  ok")
            comblees[q["theme"]] = comblees.get(q["theme"], 0) + 1
    eclaireur.DOSSIER.mkdir(parents=True, exist_ok=True)
    eclaireur.MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    p_jeu.write_text(json.dumps(jeu, ensure_ascii=False, indent=1), encoding="utf-8")
    # Les remplaçantes, sujet par sujet, autant que de comblées (deux au moins pour que fond et actualité soient servis).
    for th, n in comblees.items():
        if couts.en_limite():
            print("  ARRÊT : limite d'usage atteinte, remplaçantes au passage suivant", file=sys.stderr); break
        composer(graphe, [th], max(2, n), delai, p_jeu=p_jeu, completer=True)
    print(f"  {graphe} : {sum(comblees.values())} question(s) comblée(s) sur {len(manquees)} manquée(s), "
          f"{len(comblees)} sujet(s) recomplété(s) ; la prochaine pose mesurera les remplaçantes")
    return 0


def ecarter(graphe: str, i: int, raison: str) -> int:
    """Le porteur écarte une question dont la réponse attendue est moins juste que celle du graphe (09.10.2026 : « quel
    office fédéral prépare l'avant-projet » — l'attendu nommait l'OFJ, le graphe le DFJP mandaté par le Conseil fédéral).
    La question sort du jeu avec son motif ; la dernière mesure est recomptée sans elle dans une ligne ajoutée à
    l'historique, l'originale restant en place. On ne réécrit ni une question ni un verdict."""
    p_jeu, p_hist = chemins(graphe)
    jeu = json.loads(p_jeu.read_text(encoding="utf-8"))
    q = next((x for x in jeu if x["i"] == i), None)
    if q is None:
        print(f"  pas de question n° {i}", file=sys.stderr); return 1
    q["ecartee"] = {"date": time.strftime("%Y-%m-%d"), "raison": raison}
    q["comblee"] = q.get("comblee") or time.strftime("%Y-%m-%d")       # hors mesure, comme une question comblée
    p_jeu.write_text(json.dumps(jeu, ensure_ascii=False, indent=1), encoding="utf-8")
    if p_hist.exists():
        lignes = p_hist.read_text(encoding="utf-8").splitlines()
        m = json.loads(lignes[-1])
        if any(d["i"] == i for d in m.get("details", [])):
            details = [d for d in m["details"] if d["i"] != i]
            par_theme = {}
            for th in dict.fromkeys(d["theme"] for d in details):
                ds = [d for d in details if d["theme"] == th]
                par_theme[th] = {"ensemble": banc.parts([d["verdict"] for d in ds]),
                                 "fond": banc.parts([d["verdict"] for d in ds if d["type"] == "ag_fond"]),
                                 "actualite": banc.parts([d["verdict"] for d in ds if d["type"] == "ag_recent"]),
                                 "profond": banc.parts([d["verdict"] for d in ds])["juste"] >= SEUIL_PROFONDEUR}
            corr = {**m, "etiquette": m.get("etiquette", "") + f" — corrigée : question {i} écartée par le porteur ({raison[:60]})",
                    "ensemble": banc.parts([d["verdict"] for d in details]), "par_theme": par_theme, "details": details,
                    "profondeur": {**(m.get("profondeur") or {}), "themes_profonds": sum(1 for v in par_theme.values() if v["profond"]), "themes": len(par_theme)},
                    "corrige": {"question": i, "raison": raison, "date": time.strftime("%Y-%m-%d")}}
            with open(p_hist, "a", encoding="utf-8") as h:
                h.write(json.dumps(corr, ensure_ascii=False) + "\n")
            print(f"  mesure recomptée sans la question {i} : {banc.trois_parts(corr["ensemble"])}, "
                  f"{corr['profondeur']['themes_profonds']} sujet(s) tenu(s) sur {corr['profondeur']['themes']}")
    print(f"  question {i} écartée : {q['question'][:90]}")
    return 0


def afficher(mesures: list[dict]) -> None:
    """Les mesures côte à côte, par thème : part de réponses justes (fausses entre parenthèses)."""
    if not mesures:
        return
    pc = lambda x: f"{round(x['juste'] * 100):3d} %" + (f" ({round(x['faux'] * 100)} f)" if x.get("faux") else "")  # noqa: E731
    print("  " + " | ".join(f"{m['etiquette'][:24]:>24}" for m in mesures) + " | thème")
    for th in mesures[-1]["par_theme"]:
        cellules = []
        for m in mesures:
            x = m["par_theme"].get(th)
            cellules.append(f"{pc(x['ensemble']):>24}" if x else f"{'—':>24}")
        dernier = mesures[-1]["par_theme"][th]["ensemble"]["juste"]
        marque = "  profond" if dernier >= SEUIL_PROFONDEUR else ""
        print("  " + " | ".join(cellules) + f" | {th[:70]}{marque}")
    print("  " + " | ".join(f"{pc(m['ensemble']):>24}" for m in mesures) + " | ensemble")


def etat(graphe: str) -> None:
    _, p_hist = chemins(graphe)
    mesures = [json.loads(l) for l in p_hist.read_text(encoding="utf-8").splitlines()] if p_hist.exists() else []
    afficher(mesures)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--composer", action="store_true")
    ap.add_argument("--themes", nargs="+", help="thèmes à questionner (défaut : les sujets déclarés)")
    ap.add_argument("--par-theme", type=int, default=10)
    ap.add_argument("--completer", action="store_true",
                    help="avec --composer : ajouter --par-theme questions à un sujet déjà composé (un sujet sous dix questions)")
    ap.add_argument("--poser", action="store_true")
    ap.add_argument("--etiquette", default="mesure")
    ap.add_argument("--etat", action="store_true")
    ap.add_argument("--local", dest="local", action="store_true", default=None, help="composer par lecture locale (défaut : registre banc.lecture_locale, vrai)")
    ap.add_argument("--modele-lit", dest="local", action="store_false", help="l'ancienne voie : le modèle cherche et lit lui-même (WebFetch)")
    ap.add_argument("--sortie", help="écrire le jeu composé dans ce fichier plutôt que dans themes_banc.json (essai, comparaison)")
    ap.add_argument("--ecarter", type=int, metavar="I", help="écarter la question n° I du jeu (réponse attendue moins juste que le graphe), avec --raison")
    ap.add_argument("--raison", default="réponse attendue jugée moins juste que celle du graphe par le porteur")
    ap.add_argument("--questions", type=int, nargs="+", metavar="I", help="avec --combler : les seules questions (n° I) à combler")
    ap.add_argument("--combler", action="store_true",
                    help="ingérer les pages des questions manquées à la dernière mesure, les retirer du jeu, composer leurs remplaçantes")
    ap.add_argument("--sans-cartes", action="store_true",
                    help="poser sans le routage par les cartes des sujets (KG_SANS_CARTES=1 pour synthese.repondre) : mesurer l'un contre l'autre")
    ap.add_argument("--essai", action="store_true", help="avec --poser : ne rien enregistrer (échantillon, comparaison)")
    ap.add_argument("--poser-du-jour", action="store_true",
                    help="poser si c'est dû : jamais mesuré, dernière mesure de plus de banc.renouveler_jours jours, ou thème nouveau au jeu")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    banc.CONTEXTE.update(poste="banc_themes", graphe=g)  # registre des coûts
    if a.composer:
        themes = a.themes or te.sujets_declares(g)
        p_jeu = Path(a.sortie) if a.sortie else chemins(g)[0]
        if banc.lecture_locale(a.local):
            banc.CONTEXTE["poste"] = "banc_themes_local"
        q = composer(g, themes, a.par_theme, cfg.banc()["delai"], p_jeu=p_jeu, local=a.local, completer=a.completer)
        p_jeu.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {g} : {len(q)} questions sur {len(themes)} thème(s)")
    if a.poser:
        return 0 if poser(g, a.etiquette, themes=a.themes, enregistrer=not a.essai) else 1
    if a.ecarter is not None:
        return ecarter(g, a.ecarter, a.raison)
    if a.combler:
        return combler(g, cfg.banc()["delai"], themes=a.themes, questions=a.questions)
    if a.poser_du_jour:
        p_jeu, p_hist = chemins(g)
        if not p_jeu.exists():
            print(f"  {g} : pas de jeu par thème"); return 0
        jours = cfg.banc().get("renouveler_jours", 0) or 30
        themes_jeu = {q.get("theme") for q in json.loads(p_jeu.read_text(encoding="utf-8"))}
        # Un sujet déclaré à la main (`themes_emergents.py declarer`) n'a pas de sous-jeu : la routine n'en composait que
        # pour les sujets entrés d'eux-mêmes (`auto`). Le 11.10.2026, « droit et protection des travailleurs en Suisse »,
        # déclaré le 09.10, avait sa carte et trois nuits d'éclaireur mais aucune question ni mesure : 16 sujets au
        # registre, 15 au banc. Ici, tout sujet déclaré absent du jeu reçoit son sous-jeu, au plus
        # SOUS_JEUX_PAR_PASSAGE par passage, puis compte comme thème nouveau et se pose.
        manquants = [th for th in te.sujets_declares(g) if th not in themes_jeu][:SOUS_JEUX_PAR_PASSAGE]
        if manquants:
            if banc.lecture_locale(a.local):
                banc.CONTEXTE["poste"] = "banc_themes_local"
            q = composer(g, manquants, a.par_theme, cfg.banc()["delai"], p_jeu=p_jeu, local=a.local)
            p_jeu.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
            banc.CONTEXTE["poste"] = "banc_themes"
            themes_jeu = {x.get("theme") for x in q}
            for th in manquants:
                print(f"  {g} : sous-jeu composé pour le sujet déclaré sans questions « {th[:60]} »")
        mesures = [json.loads(l) for l in p_hist.read_text(encoding="utf-8").splitlines() if l.strip()] if p_hist.exists() else []
        if mesures:
            derniere = mesures[-1]
            age = (time.time() - time.mktime(time.strptime(derniere["horodatage"], "%Y-%m-%dT%H-%M"))) / 86400
            neufs = themes_jeu - set(derniere["par_theme"])
            horo_m = derniere["horodatage"].replace("T", " ").replace("-", ":", 4).replace(":", "-", 2)
            ajoutees = sum(1 for q in json.loads(p_jeu.read_text(encoding="utf-8"))
                           if not q.get("comblee") and str(q.get("ajoutee") or "") > horo_m)
            if age < jours and not neufs and not ajoutees:
                print(f"  {g} : mesure par thème du {derniere['horodatage'][:10]}, renouvellement dans {int(jours - age)} jour(s)"); return 0
            etiquette = f"mesure du {time.strftime('%Y-%m-%d')}" + (f", {len(neufs)} thème(s) nouveau(x)" if neufs else "") \
                + (f", {ajoutees} question(s) ajoutée(s)" if ajoutees else "")
        else:
            etiquette = f"première mesure, {time.strftime('%Y-%m-%d')}"
        return 0 if poser(g, etiquette) else 1
    if a.etat:
        etat(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())

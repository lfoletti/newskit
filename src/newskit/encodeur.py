#!/usr/bin/env python3
"""Encodeur léger, à la façon de CLAP : questions et synthèses dans un même espace (pilote du 06.10.2026).

Le reranker multilingue (jina-reranker-v2) rapprochait bien « latence » de « latency », mais pesait 278 M de paramètres et
lisait chaque paire question-candidat : trop lourd pour le serveur (MemoryMax=1G, service tué le 05.10.2026), 2 à 7 s
par question. Ici, un encodeur à deux tours, petit (paraphrase-multilingual-MiniLM, 118 M), affiné par apprentissage
contrastif sur des paires question ↔ synthèse ou paragraphe tirées de la pyramide elle-même : les vecteurs du graphe se
calculent à l'avance, une question n'en demande qu'un.

    python3 -m newskit.encodeur paires    --graphe <graphe>            # questions synthétiques (modèle léger)
    python3 -m newskit.encodeur entrainer --graphe <graphe> --paires 1000
    python3 -m newskit.encodeur evaluer   --graphe <graphe>            # mots, modèle de base, modèles affinés

Paires : 4 questions par synthèse de notion, 3 par paragraphe de fiche cité par une synthèse ; négatif difficile : une
synthèse de la même communauté. Évaluation : les questions du banc tirées du graphe (40 sur 50 pour un graphe audio), jamais
vues à l'entraînement — la fiche qui porte la réponse est-elle parmi les cinq premières que la recherche ramène ?
Tout ce qui est produit va dans cache/encodeur/<graphe>/ (hors dépôt).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import synthese as sy  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

BASE = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODELE_QUESTIONS = media.modele("questions")     # écrire des questions ne demande pas un gros modèle (Haiku 5.5 depuis le 10.10.2026)
LOT = 12
PARAGRAPHES_MAX = 1200
SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["id", "questions"],
    "properties": {"id": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}}}}}}


def dossier(graphe: str) -> Path:
    d = RACINE / "cache" / "encodeur" / graphe
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Les unités indexées : synthèses de notions et paragraphes de fiches --------------------------------------

def unites(graphe: str, G=None) -> list[dict]:
    """{id, texte, fiches} : chaque synthèse de notion (avec les fiches qu'elle cite) et chaque paragraphe de fiche."""
    notions, _ = sy.lire(graphe)
    out = []
    for k, v in notions.items():
        fiches = sorted({r["fiche"] for p in v["phrases"] for r in p["refs"]})
        out.append({"id": f"N:{k}", "texte": f"{v['label']}. " + " ".join(p["texte"] for p in v["phrases"]), "fiches": fiches})
    G = G if G is not None else sy.charger_graphe(graphe)
    for f in sorted({str(d["source_file"]) for _, d in G.nodes(data=True) if d.get("kind") in ("article", "papier") and d.get("source_file")}):
        for ligne, texte in ig._paragraphes(RACINE / f):
            if len(texte) >= 120:
                out.append({"id": f"P:{f}:{ligne}", "texte": texte[:1200], "fiches": [f]})
    return out


# --- Paires question ↔ unité ---------------------------------------------------------------------------------

def fiches_du_jeu_rare(graphe: str) -> set[str]:
    """Les fiches du jeu de faits rares : l'affinage ne les questionne pas, sinon il apprendrait ce qui le mesure."""
    p = dossier(graphe) / "jeu_rare.jsonl"
    return {json.loads(l)["fiche"] for l in p.read_text(encoding="utf-8").splitlines() if l.strip()} if p.exists() else set()


def unites_equilibrees(graphe: str, notions: dict, par_strate: int, exclues: frozenset | set = frozenset()) -> list[tuple[str, str, int]]:
    """Des unités réparties entre faits populaires et faits rares (porteur, 10.10.2026 : « l'asymétrie de représentation
    entre faits populaires et faits rares devrait être prise en compte dans l'équilibrage du jeu d'affinage »). Le premier
    jeu d'un graphe de conformité avait un degré médian de 14 contre 6 pour le graphe : l'encodeur apprenait le vocabulaire des
    notions centrales, que la redondance du corpus retrouve de toute façon. Cinq strates, `par_strate` unités chacune :
    notions de degré ≤ 5, de 6 à 29, ≥ 30 ; paragraphes cités par une seule synthèse ; paragraphes de fiches citées par
    une seule synthèse (les plus isolées). `exclues` : des fiches dont aucun paragraphe n'est tiré (celles du jeu de faits
    rares, GV7, 2 novembre 2026 : les fiches citées une fois sont aussi les siennes)."""
    rng = random.Random(11)
    deg = {k: int(v.get("degre") or 0) for k, v in notions.items()}
    strates = {"bas": [k for k in notions if deg[k] <= 5], "moyen": [k for k in notions if 6 <= deg[k] <= 29], "haut": [k for k in notions if deg[k] >= 30]}
    out = []
    for nom, ks in strates.items():
        for k in rng.sample(ks, min(par_strate, len(ks))):
            out.append((f"N:{k}", f"{notions[k]['label']}. " + " ".join(p["texte"] for p in notions[k]["phrases"]), 4))
    cit = Counter((r["fiche"], r["ligne"]) for v in notions.values() for p in v["phrases"] for r in p["refs"] if r.get("ligne"))
    fiches = Counter(r["fiche"] for v in notions.values() for p in v["phrases"] for r in p["refs"])
    seuls = [fl for fl, c in cit.items() if c == 1 and fl[0] not in exclues]
    isolees = [fl for fl in cit if fiches[fl[0]] == 1 and fl[0] not in exclues]
    for nom, fls in (("paragraphe cité une fois", seuls), ("fiche citée une fois", isolees)):
        for f, l in rng.sample(fls, min(par_strate, len(fls))):
            t = next((x for l2, x in ig._paragraphes(RACINE / f) if l2 == l and len(x) >= 120), None)
            if t:
                out.append((f"P:{f}:{l}", t[:1200], 3))
    return out


def generer_paires(graphe: str, max_unites: int = 0, equilibre: bool = False) -> None:
    """`max_unites` : au plus tant d'unités questionnées, les notions de plus haut degré d'abord puis les paragraphes qu'elles
    citent (un graphe de conformité, 10.10.2026 : 4 381 synthèses et 4 289 paragraphes cités, 1 500 unités suffisent à l'affinage).
    `equilibre` : unités réparties entre faits populaires et rares (unites_equilibrees), max_unites / 5 par strate."""
    notions, _ = sy.lire(graphe)
    G = sy.charger_graphe(graphe)
    if equilibre:
        cibles = unites_equilibrees(graphe, notions, max(max_unites // 5, 20) if max_unites else 150, exclues=fiches_du_jeu_rare(graphe))
    else:
        ordre = sorted(notions, key=lambda k: -int(notions[k].get("degre") or 0))
        if max_unites:
            ordre = ordre[:max(max_unites * 2 // 3, 1)]
        cibles = [(f"N:{k}", f"{notions[k]['label']}. " + " ".join(p["texte"] for p in notions[k]["phrases"]), 4) for k in ordre]
        citees = {(r["fiche"], r["ligne"]) for k in ordre for p in notions[k]["phrases"] for r in p["refs"] if r.get("ligne")}
        paras = {(f, l): t for f, l in citees for l2, t in ig._paragraphes(RACINE / f) if l2 == l and len(t) >= 120}
        plafond_p = min(PARAGRAPHES_MAX, max_unites - len(cibles)) if max_unites else PARAGRAPHES_MAX
        for (f, l), t in list(paras.items())[:max(plafond_p, 0)]:
            cibles.append((f"P:{f}:{l}", t[:1200], 3))
    p_sortie = dossier(graphe) / "paires.jsonl"
    deja = {json.loads(x)["id"] for x in p_sortie.read_text(encoding="utf-8").splitlines()} if p_sortie.exists() else set()
    cibles = [c for c in cibles if c[0] not in deja]
    print(f"  {graphe} : {len(cibles)} unités à questionner ({len(deja)} déjà faites)", flush=True)
    decl = cfg.graphe_declare(graphe)
    ancien = te.MODELE
    te.MODELE = MODELE_QUESTIONS
    try:
        for k in range(0, len(cibles), LOT):
            lot = cibles[k:k + LOT]
            prompt = (f"Domaine : « {decl['libelle']} ». Pour chaque texte ci-dessous, écris le nombre demandé de questions EN "
                      "FRANÇAIS auxquelles ce texte répond — comme les poserait un praticien qui ne l'a pas lu : variées (une "
                      "large, une précise sur un fait, un nom ou un chiffre, une sur le fonctionnement), sans recopier les "
                      "tournures du texte, avec les noms propres et techniques tels quels.\n\n"
                      + "\n\n".join(f"### {i} ({n} questions)\n{t}" for i, t, n in lot)
                      + '\n\nRéponds en JSON : {"items": [{"id": "<id>", "questions": ["…", …]}]}.')
            rep = te.appeler_claude(prompt, SCHEMA, poste="encodeur")
            with open(p_sortie, "a", encoding="utf-8") as h:
                for x in (rep or {}).get("items", []):
                    if x.get("id") in {c[0] for c in lot}:
                        h.write(json.dumps({"id": x["id"], "questions": [q for q in x.get("questions", []) if q.strip()]},
                                           ensure_ascii=False) + "\n")
            print(f"  {min(k + LOT, len(cibles))}/{len(cibles)}", flush=True)
    finally:
        te.MODELE = ancien


def jeu_rare(graphe: str, n: int = 150) -> None:
    """Un jeu d'évaluation de faits rares (porteur, 10.10.2026) : une question par paragraphe de fiches citées par une seule
    synthèse, hors des fiches déjà questionnées pour l'affinage. La fiche et la ligne sont la vérité ; `evaluer` mesure
    si la recherche les retrouve, pour chaque modèle. Écrit cache/encodeur/<graphe>/jeu_rare.jsonl."""
    notions, _ = sy.lire(graphe)
    p_p, p_s = dossier(graphe) / "paires.jsonl", dossier(graphe) / "jeu_rare.jsonl"
    vues = {json.loads(l)["id"].split(":")[1] for l in p_p.read_text(encoding="utf-8").splitlines() if json.loads(l)["id"].startswith("P:")} if p_p.exists() else set()
    fiches = Counter(r["fiche"] for v in notions.values() for p in v["phrases"] for r in p["refs"])
    # Fiches citées par au plus deux synthèses (souvent par leur seule ligne « pourquoi ») : un paragraphe de fond de chacune.
    cand = [f for f, c in fiches.items() if c <= 2 and f not in vues and (RACINE / f).exists()]
    rng = random.Random(23); rng.shuffle(cand)
    unites = []
    for f in cand:
        paras = [(l2, x) for l2, x in ig._paragraphes(RACINE / f) if len(x) >= 200 and not x.lstrip().startswith("#")]
        if paras:
            l, t = max(paras, key=lambda lx: len(lx[1]))
            unites.append((f"P:{f}:{l}", t[:1200], 1))
        if len(unites) >= n:
            break
    decl = cfg.graphe_declare(graphe)
    ancien = te.MODELE; te.MODELE = MODELE_QUESTIONS
    ecrits = 0
    try:
        with open(p_s, "w", encoding="utf-8") as h:
            for k in range(0, len(unites), LOT):
                lot = unites[k:k + LOT]
                prompt = (f"Domaine : « {decl['libelle']} ». Pour chaque texte ci-dessous, écris UNE question EN FRANÇAIS, précise, "
                          "portant sur un fait que seul ce texte apporte (un chiffre, une date, un nom, une décision), comme la "
                          "poserait un praticien qui ne l'a pas lu, sans recopier ses tournures, avec les noms propres tels quels.\n\n"
                          + "\n\n".join(f"### {i} (1 question)\n{t}" for i, t, _ in lot)
                          + '\n\nRéponds en JSON : {"items": [{"id": "<id>", "questions": ["…"]}]}.')
                rep = te.appeler_claude(prompt, SCHEMA, poste="encodeur")
                for x in (rep or {}).get("items", []):
                    if x.get("id") in {u[0] for u in lot} and x.get("questions"):
                        _, f, l = x["id"].split(":", 2)
                        h.write(json.dumps({"question": x["questions"][0], "fiche": f, "ligne": int(l)}, ensure_ascii=False) + "\n"); ecrits += 1
    finally:
        te.MODELE = ancien
    print(f"  {graphe} : jeu de faits rares, {ecrits} questions sur {len(unites)} paragraphes de fiches isolées → {p_s}")


# --- Entraînement contrastif -----------------------------------------------------------------------------------

def choisir_negatifs(Q, E, pool: list[str], positifs: list[str], fiches: dict, saut: int = 2, profondeur: int = 50) -> list:
    """Pour chaque question (ligne de Q), une unité du vivier (lignes de E) proche selon le modèle courant : ni le positif ni
    une unité d'une de ses fiches (un faux négatif probable), après les `saut` plus proches qui restent (les autres faux
    négatifs se logent en tête). None quand aucune ne convient. R-GPL (arXiv 2501.14434) : extraits à chaque époque."""
    import numpy as np  # noqa: PLC0415
    S = np.asarray(Q) @ np.asarray(E).T
    out = []
    for j, pos in enumerate(positifs):
        f_pos = set(fiches.get(pos, ()))
        ok = [pool[i] for i in np.argsort(-S[j])[:profondeur + saut + 1]
              if pool[i] != pos and not f_pos & set(fiches.get(pool[i], ()))]
        out.append(ok[saut] if len(ok) > saut else (ok[-1] if ok else None))
    return out


VIVIER = 20000   # unités hors positifs dans le vivier des négatifs réextraits (un réencodage par époque : environ 3 min)


def entrainer(graphe: str, n_paires: int, epoques: int = 1, verbeux: bool = False, nom: str = "",
              reextraire: bool = False, saut: int = 2) -> Path:
    from sentence_transformers import InputExample, SentenceTransformer, losses  # noqa: PLC0415
    from torch.utils.data import DataLoader  # noqa: PLC0415
    us = unites(graphe)
    textes = {u["id"]: u["texte"] for u in us}
    G = sy.charger_graphe(graphe)
    communaute = {}
    for etiquette, membres, _ in dg.communautes(G, 60):
        for n in membres:
            communaute[f"N:{n}"] = etiquette
    par_comm = defaultdict(list)
    for k, c in communaute.items():
        if k in textes:
            par_comm[c].append(k)
    paires = []
    for l in (dossier(graphe) / "paires.jsonl").read_text(encoding="utf-8").splitlines():
        x = json.loads(l)
        if x["id"] in textes:
            paires += [(q, x["id"]) for q in x["questions"]]
    random.Random(7).shuffle(paires)
    paires = paires[:n_paires]
    exemples = []
    for q, i in paires:
        voisins = [v for v in par_comm.get(communaute.get(i), []) if v != i]
        if voisins:                                          # négatif difficile : une notion de la même communauté
            exemples.append(InputExample(texts=[q, textes[i], textes[random.Random(hash(q)).choice(voisins)]]))
        else:
            exemples.append(InputExample(texts=[q, textes[i]]))
    m = SentenceTransformer(BASE)
    if reextraire:
        return _entrainer_reextrait(m, graphe, us, paires, epoques, verbeux, nom, n_paires, saut)
    triplets = [e for e in exemples if len(e.texts) == 3]
    doublets = [e for e in exemples if len(e.texts) == 2]
    objectifs = []
    for ex in (triplets, doublets):
        if ex:
            objectifs.append((DataLoader(ex, shuffle=True, batch_size=32), losses.MultipleNegativesRankingLoss(m)))
    t0 = time.time()
    print(f"  {len(paires)} paires ({len(triplets)} triplets, {len(doublets)} doublets), {epoques} époque(s), lots de 32, "
          f"{sum(math.ceil(len(ex) / 32) for ex in (triplets, doublets) if ex)} pas par époque", flush=True)
    m.fit(train_objectives=objectifs, epochs=epoques, warmup_steps=20, show_progress_bar=verbeux)
    sortie = dossier(graphe) / (f"modele_{n_paires}" + (f"_{nom}" if nom else ""))
    m.save(str(sortie))
    print(f"  entraîné sur {len(paires)} paires ({len(triplets)} avec négatif difficile) en {time.time() - t0:.0f} s → {sortie}")
    return sortie


def _entrainer_reextrait(m, graphe, us, paires, epoques, verbeux, nom, n_paires, saut) -> Path:
    """Une époque à la fois : le modèle courant encode le vivier et les questions, choisit les négatifs, puis s'entraîne."""
    from sentence_transformers import InputExample, losses  # noqa: PLC0415
    from torch.utils.data import DataLoader  # noqa: PLC0415
    textes = {u["id"]: u["texte"] for u in us}
    fiches = {u["id"]: u["fiches"] for u in us}
    positifs = sorted({i for _, i in paires})
    autres = [u["id"] for u in us if u["id"] not in set(positifs)]
    pool = positifs + random.Random(5).sample(autres, min(VIVIER, len(autres)))
    t0 = time.time()
    for e in range(epoques):
        E = m.encode([textes[i] for i in pool], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        Q = m.encode([q for q, _ in paires], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        negs = choisir_negatifs(Q, E, pool, [i for _, i in paires], fiches, saut=saut)
        exemples = [InputExample(texts=[q, textes[i], textes[n]] if n else [q, textes[i]]) for (q, i), n in zip(paires, negs)]
        triplets = [x for x in exemples if len(x.texts) == 3]
        doublets = [x for x in exemples if len(x.texts) == 2]
        objectifs = [(DataLoader(x, shuffle=True, batch_size=32), losses.MultipleNegativesRankingLoss(m)) for x in (triplets, doublets) if x]
        print(f"  époque {e + 1}/{epoques} : {len(triplets)} négatifs réextraits sur {len(paires)} paires, vivier {len(pool)}", flush=True)
        m.fit(train_objectives=objectifs, epochs=1, warmup_steps=20 if e == 0 else 0, show_progress_bar=verbeux)
    sortie = dossier(graphe) / (f"modele_{n_paires}" + (f"_{nom}" if nom else ""))
    m.save(str(sortie))
    print(f"  entraîné sur {len(paires)} paires, négatifs réextraits à chaque époque, en {time.time() - t0:.0f} s → {sortie}")
    return sortie


# --- Évaluation ------------------------------------------------------------------------------------------------

class Bm25:
    """BM25 sur des unités découpées une seule fois : `scores(q)` rend ce que rend `bm25(q, docs)`. Sur un graphe fusionné
    (environ 100 000 unités, GV7, 2 novembre 2026), redécouper les unités à chaque question coûtait des heures."""

    def __init__(self, docs: list[str]):
        self.tok = [list(sy.mots(d)) for d in docs]
        self.compte = [Counter(d) for d in self.tok]
        self.N = len(self.tok)
        self.moy = sum(map(len, self.tok)) / max(self.N, 1)
        self.df = Counter(t for d in self.tok for t in set(d))

    def scores(self, requete: str) -> list[float]:
        q = sy.mots(requete)
        out = []
        for d, c in zip(self.tok, self.compte):
            s = 0.0
            for t in q:
                if c[t]:
                    idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * len(d) / self.moy))
            out.append(s)
        return out


def questions_du_banc(graphe: str, bancs: list[Path] | None) -> list[dict]:
    """Les questions de banc qui nomment leur fiche source : celles des bancs nommés (un graphe fusionné n'a pas de banc à
    lui, on lui passe ceux des graphes d'origine), sinon celles du banc du graphe ; aucune s'il n'en a pas (faits rares seuls)."""
    if not bancs:
        p = cfg.chemins_du_graphe(graphe)["base"] / "banc_questions.json"
        bancs = [p] if p.exists() else []
    return [q for b in bancs for q in json.loads(Path(b).read_text(encoding="utf-8")) if q.get("source")]


def bm25(requete: str, docs: list[str]) -> list[float]:
    """Le classement par les mots, pour comparer : BM25 simple sur les unités."""
    tok = [list(sy.mots(d)) for d in docs]
    N = len(tok); moy = sum(map(len, tok)) / max(N, 1)
    df = Counter(t for d in tok for t in set(d))
    q = sy.mots(requete)
    out = []
    for d in tok:
        c = Counter(d); s = 0.0
        for t in q:
            if c[t]:
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * len(d) / moy))
        out.append(s)
    return out


def rappel(classement: list[int], us: list[dict], source: str, k: int = 5) -> bool:
    vues = []
    for i in classement:
        for f in us[i]["fiches"]:
            if f not in vues:
                vues.append(f)
        if len(vues) >= k:
            break
    return source in vues[:k]


def contient_citation(u: dict, citation: str) -> bool:
    """L'unité porte-t-elle le passage que cite le banc ? (un fragment de 50 caractères de la citation)."""
    c = re.sub(r"\s+", " ", ig._plier(citation)).strip()
    t = re.sub(r"\s+", " ", ig._plier(u["texte"]))
    morceaux = [c[i:i + 50] for i in range(0, max(len(c) - 50, 1), 25)] or [c]
    return any(m in t for m in morceaux if len(m) >= 30)


def mesures(classement: list[int], us: list[dict], q: dict) -> tuple[bool, bool, bool]:
    """(fiche dans les 5 premières, paragraphe cité en tête, paragraphe cité dans les 3 premiers)."""
    cites = [i for i in classement[:3] if contient_citation(us[i], q["citation"])]
    return rappel(classement, us, q["source"]), bool(cites) and cites[0] == classement[0], bool(cites)


def evaluer(graphe: str, bancs: list[Path] | None = None) -> None:
    import numpy as np  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    us = unites(graphe)
    qs = questions_du_banc(graphe, bancs)
    print(f"  {graphe} : {len(us)} unités indexées, {len(qs)} questions du banc avec leur fiche source")
    textes = [u["texte"] for u in us]
    res = {}
    t0 = time.time()
    p_rare = dossier(graphe) / "jeu_rare.jsonl"
    rares = [json.loads(l) for l in p_rare.read_text(encoding="utf-8").splitlines() if l.strip()] if p_rare.exists() else []
    idx_rare = {f"P:{r['fiche']}:{r['ligne']}": i for i, u in enumerate(us) for r in rares if u["id"] == f"P:{r['fiche']}:{r['ligne']}"}
    def bilan(nom, classements, duree, cl_rares=None):
        m = [mesures(c, us, q) for c, q in zip(classements, qs)]
        res[nom] = {"fiche@5": int(sum(x[0] for x in m)), "paragraphe@1": int(sum(x[1] for x in m)),
                    "paragraphe@3": int(sum(x[2] for x in m))}
        r = res[nom]
        ligne = (f"    {nom:28} fiche@5 {r['fiche@5']:>2}  paragraphe@1 {r['paragraphe@1']:>2}  paragraphe@3 {r['paragraphe@3']:>2}"
                 f"  sur {len(qs)}   ({duree:.0f} s)")
        if cl_rares is not None and rares:
            # Faits rares : le paragraphe exact (fiche, ligne) dans les 1, 3, 10 premiers ; la fiche dans les 5 premiers.
            r1 = r3 = r10 = f5 = 0
            for c, q in zip(cl_rares, rares):
                cible = idx_rare.get(f"P:{q['fiche']}:{q['ligne']}")
                rangs = list(c[:10])
                if cible in rangs[:1]: r1 += 1
                if cible in rangs[:3]: r3 += 1
                if cible in rangs: r10 += 1
                if any(q["fiche"] in us[i]["fiches"] for i in c[:5]): f5 += 1
            res[nom]["rares"] = {"n": len(rares), "paragraphe@1": r1, "paragraphe@3": r3, "paragraphe@10": r10, "fiche@5": f5}
            ligne += f"  | faits rares ({len(rares)}) : paragraphe@1 {r1:>2} @3 {r3:>2} @10 {r10:>2}  fiche@5 {f5:>2}"
        print(ligne, flush=True)
    # Deux index, comme dans `repondre` : tout (synthèses et paragraphes), puis les paragraphes seuls — c'est lui qui
    # choisit l'extrait de fiche joint à la réponse, là où le banc perdait ses dernières questions.
    para = [i for i, u in enumerate(us) if u["id"].startswith("P:")]
    def seuls(classement):
        return [i for i in classement if i in set_para]
    set_para = set(para)
    t0 = time.time()
    index = Bm25(textes)
    bm = [list(np.argsort(-np.array(index.scores(q["question"])))) for q in qs]
    bilan("mots (BM25)", bm, time.time() - t0,
          [list(np.argsort(-np.array(index.scores(q["question"]))))[:10] for q in rares] if rares else None)
    bilan("  paragraphes seuls, mots", [seuls(c) for c in bm], 0)
    candidats = [("modèle de base", BASE)] + [(f"affiné {p.name[len('modele_'):]}", str(p))
                                             for p in sorted(dossier(graphe).glob("modele_*"), key=lambda p: (int(p.name.split("_")[1]), p.name))]
    for nom, chemin in candidats:
        t0 = time.time()
        m = SentenceTransformer(chemin)
        E = m.encode(textes, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        Q = m.encode([q["question"] for q in qs], normalize_embeddings=True, show_progress_bar=False)
        cl = [list(np.argsort(-(E @ Q[j]))) for j in range(len(qs))]
        cl_r = None
        if rares:
            QR = m.encode([q["question"] for q in rares], normalize_embeddings=True, show_progress_bar=False)
            cl_r = [list(np.argsort(-(E @ QR[j]))[:10]) for j in range(len(rares))]
        bilan(nom, cl, time.time() - t0, cl_r)
        bilan(f"  paragraphes seuls, {nom}"[:28], [seuls(c) for c in cl], 0)
    (dossier(graphe) / "evaluation.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("paires", "entrainer", "evaluer", "jeu-rare"))
    ap.add_argument("--graphe", required=True, help="le graphe à traiter : aucun par défaut")
    ap.add_argument("--paires", type=int, default=1000)
    ap.add_argument("--epoques", type=int, default=1)
    ap.add_argument("--unites", type=int, default=0, help="paires : au plus tant d'unités questionnées (0 : toutes)")
    ap.add_argument("--equilibre", action="store_true", help="paires : unités réparties entre faits populaires et rares (cinq strates)")
    ap.add_argument("--verbeux", action="store_true", help="entrainer : barre de progression et perte par pas (console dédiée)")
    ap.add_argument("--banc", type=Path, action="append", help="evaluer : un banc_questions.json à lire (répétable ; "
                    "un graphe fusionné prend ceux de ses graphes d'origine) ; sans banc, les faits rares seuls")
    ap.add_argument("--reextraire", action="store_true", help="entrainer : négatifs difficiles réextraits par le modèle "
                    "courant à chaque époque (R-GPL)")
    ap.add_argument("--nom", default="", help="entrainer : suffixe du dossier du modèle (modele_<paires>_<nom>), pour garder plusieurs essais")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    if a.action == "jeu-rare":
        jeu_rare(g); return 0
    if a.action == "paires":
        generer_paires(g, a.unites, a.equilibre)
    elif a.action == "entrainer":
        entrainer(g, a.paires, a.epoques, a.verbeux, a.nom, reextraire=a.reextraire)
    else:
        evaluer(g, a.banc)
    return 0


if __name__ == "__main__":
    sys.exit(main())

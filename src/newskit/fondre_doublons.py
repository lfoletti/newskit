#!/usr/bin/env python3
"""Doublons de notions : la même chose sous plusieurs libellés (traduction, sigle, variante), fondue à la composition.

Pourquoi (mesure du 09.10.2026, un graphe de conformité) : la consigne d'extraction gardait la langue du document, l'identifiant
d'un nœud est le slug de son libellé, et la composition ne fond que les libellés strictement égaux. « Data
Minimization » (53 arêtes) et « Minimisation des données » (49) étaient deux nœuds sans voisin commun ; EDPB, European
Data Protection Board et CEPD trois ; 621 paires de nœuds d'au moins 5 arêtes se ressemblaient, 10 % du degré.

Comment :
  1. candidats, sans appel : paires de libellés de concepts que l'encodeur multilingue local rapproche (cosinus ≥
     SEUIL), plus la règle du sigle (« European Data Protection Board (EDPB) » ↔ « EDPB » ↔ « European Data Protection
     Board » quand les initiales concordent) ;
  2. confirmation par lots par un petit modèle (Sonnet), avec pour chaque nœud quelques voisins en contexte : même
     notion EXACTEMENT (traduction, sigle, orthographe) → alias ; parent, variante, sous-cas, document → non ;
  3. mémoire dans graphe/<nom>/alias.json : `alias` (identifiant secondaire → canonique), `refuses` (paires tranchées
     non, jamais redemandées), `libelles`. Le canonique est le libellé anglais quand l'autre est français (la consigne
     d'extraction est passée à l'anglais le 09.10.2026), sinon le plus relié, sinon le plus court.
  4. `fusionner_graphe.py` applique la table à chaque composition : les arêtes du secondaire passent au canonique, qui
     garde les autres libellés sous `aussi` (la recherche les voit).

    python3 -m newskit.fondre_doublons --graphe <graphe>              # un passage (PLAFOND paires au plus)
    python3 -m newskit.fondre_doublons --graphe <graphe> --controle   # les candidats, sans appel
    python3 -m newskit.fondre_doublons --graphe <graphe> --etat       # la table

Chaque appel est consigné au registre des coûts (poste « alias »). Le coupe-circuit s'applique.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import couts  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

SEUIL = 0.93                  # cosinus de l'encodeur à partir duquel deux libellés sont candidats
PLAFOND = 300                 # paires soumises au modèle par passage
LOT = 40                      # paires par appel
MODELE = media.modele("lecture")  # trancher « même notion ? » ne demande pas le plus gros modèle
VOISINS = 3                   # voisins donnés en contexte pour chaque nœud
FR = re.compile(r"\b(de|des|du|la|le|les|et|sur|pour|données|protection|règlement|autorité|décision|droit|loi|européen|"
                r"européenne|traitement|responsable|délégué|amende|système|systèmes|modèle|modèles|risque|risques)\b|[éèêàçùîô]", re.I)
EN = re.compile(r"\b(of|the|and|for|data|protection|regulation|authority|decision|law|guidelines|european|act|processing|"
                r"controller|officer|fine|rights|risk|assessment|system|systems|model|models|approach|based)\b", re.I)
# Un libellé étranger (03.11.2026) : une lettre ou un mot-outil d'une autre langue, ou une majorité de mots en
# minuscules absents du vocabulaire anglais (le dictionnaire de la machine, plus les mots des libellés anglais du graphe).
ACCENTS = re.compile(r"[ąęłńśźżćőűøåæœäöüßéèêàçùîôâûëïÿãõñ]", re.I)
MOTS_OUTILS = re.compile(r"\b(de|des|du|la|le|les|et|sur|pour|une|aux|der|die|das|und|für|von|mit|zur|het|een|van|voor|op|"
                         r"jako|oraz|się|dla|przez|della|degli|che|per|il|los|las|del|para|el|y|w|z|i|na|en)\b", re.I)
DICTIONNAIRE = Path("/usr/share/dict/words")
_vocabulaire: set[str] | None = None


def vocabulaire() -> set[str]:
    global _vocabulaire
    if _vocabulaire is None:
        _vocabulaire = {w.strip().lower() for w in DICTIONNAIRE.read_text(errors="ignore").splitlines()} if DICTIONNAIRE.exists() else set()
    return _vocabulaire


def _forme_anglaise(w: str, voc: set[str]) -> bool:
    formes = {w, w.replace("isation", "ization"), w.replace("our", "or")}
    if w.endswith("ies"): formes.add(w[:-3] + "y")
    if w.endswith("es"): formes.add(w[:-2])
    if w.endswith("s"): formes.add(w[:-1])
    if w.endswith("ed"): formes |= {w[:-2], w[:-1]}
    if w.endswith("ing"): formes |= {w[:-3], w[:-3] + "e"}
    if w.endswith("ling"): formes.add(w[:-4])
    return any(x in voc for x in formes if len(x) > 2) or w in ("data", "online", "deepfake", "deepfakes", "cybersecurity", "chatbot", "chatbots")


def etranger(l: str, voc: set[str] | None = None) -> bool:
    """Le libellé est-il dans une autre langue que l'anglais ? Les noms propres, les sigles et les identifiants
    (« Bruce Schneier », « GDPR », « Azure/PyRIT ») ne le sont pas."""
    l = str(l or "")
    if "/" in l or not re.search(r"[a-zà-ÿ]{3}", l):
        return False
    if ACCENTS.search(l) or MOTS_OUTILS.search(l):
        return True
    voc = vocabulaire() if voc is None else voc
    if not voc:
        return False
    mots = re.findall(r"(?<![\w-])[a-z][a-z'-]{2,}", l)
    return bool(mots) and sum(not _forme_anglaise(w, voc) for w in mots) * 2 > len(mots)


TRADUCTION = media.modele("traduction")   # donner le nom anglais d'une notion : un petit modèle suffit
LOT_TRADUCTION = 80
SCHEMA_TRADUCTION = {"type": "object", "required": ["libelles"], "properties": {"libelles": {"type": "array", "items": {
    "type": "object", "required": ["i", "anglais"], "properties": {"i": {"type": "integer"}, "anglais": {"type": "string"}}}}}}


def traduire(graphe: str, d: dict, memo: dict, plafond: int = 4000) -> int:
    """Le nom anglais de chaque notion au libellé étranger, gardé dans memo["anglais"] (identifiant → libellé) : une
    fois demandé, jamais redemandé. Le nom propre, le sigle ou le titre officiel reste tel quel. Rend le nombre de
    libellés traduits. Chaque appel est consigné au registre des coûts (poste « alias-traduction »)."""
    memo.setdefault("anglais", {})
    voc = set(vocabulaire())
    for n in d["nodes"]:
        l = str(n.get("label") or "")
        if str(n.get("id", "")).startswith("concept_") and EN.search(l) and not ACCENTS.search(l) and not MOTS_OUTILS.search(l):
            voc |= set(re.findall(r"[a-z]{3,}", l.lower()))
    a_faire = [(n["id"], str(n["label"])) for n in d["nodes"]
               if str(n.get("id", "")).startswith("concept_") and n["id"] not in memo["anglais"] and n["id"] not in memo["alias"]
               and etranger(n.get("label"), voc)][:plafond]
    faits = 0
    for k0 in range(0, len(a_faire), LOT_TRADUCTION):
        if couts.en_limite():
            print("  ARRÊT : limite d'usage atteinte, les libellés restants attendent", file=sys.stderr)
            break
        lot = a_faire[k0:k0 + LOT_TRADUCTION]
        rep = te.appeler_claude(
            "Voici des libellés de notions d'un graphe de connaissances sur la gouvernance de l'IA, dans plusieurs langues. "
            "Pour chacun, donne le nom anglais qu'un spécialiste emploierait (le terme établi, au singulier, sans article). "
            "Un nom propre (personne, organisation, tribunal, loi ou autorité nationale), un sigle ou le titre officiel d'un "
            "texte reste tel quel.\n\n" + "\n".join(f"{i}. {l}" for i, (_, l) in enumerate(lot))
            + '\n\nRéponds en JSON : {"libelles": [{"i": <indice>, "anglais": "<nom anglais>"}, …]}.',
            SCHEMA_TRADUCTION, TRADUCTION, poste="alias-traduction", graphe=graphe)
        for x in (rep or {}).get("libelles", []):
            i, en = x.get("i"), str(x.get("anglais") or "").strip()
            if isinstance(i, int) and 0 <= i < len(lot) and en:
                memo["anglais"][lot[i][0]] = en; faits += 1
    return faits


SCHEMA = {"type": "object", "required": ["paires"], "properties": {"paires": {"type": "array", "items": {
    "type": "object", "required": ["i", "meme"],
    "properties": {"i": {"type": "integer"}, "meme": {"type": "boolean"}}}}}}


def chemin_alias(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "alias.json"


def lire_alias(graphe: str) -> dict:
    p = chemin_alias(graphe)
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for k in ("alias", "refuses", "libelles", "anglais"):
        d.setdefault(k, {})
    return d


def ecrire_alias(graphe: str, d: dict) -> None:
    d["date"] = time.strftime("%Y-%m-%d")
    chemin_alias(graphe).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def racine(alias: dict[str, str], n: str) -> str:
    """Le canonique au bout de la chaîne (a → b → c donne c), sans boucle."""
    vus = set()
    while n in alias and n not in vus:
        vus.add(n); n = alias[n]
    return n


def charger_graphe(graphe: str) -> dict | None:
    ch = cfg.chemins_du_graphe(graphe)
    gz, clair = Path(str(ch["graphe"]) + ".gz"), ch["graphe"]
    if gz.exists() and (not clair.exists() or gz.stat().st_mtime >= clair.stat().st_mtime):
        return json.loads(gzip.open(gz).read())
    if clair.exists():
        return json.loads(clair.read_text(encoding="utf-8"))
    return None


def langue(l: str) -> str:
    f, e = bool(FR.search(l)), bool(EN.search(l))
    return "fr" if f and not e else "en" if e and not f else "?"


def norm(l: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (l or "").lower()).strip()


def initiales(l: str) -> str:
    return "".join(m[0] for m in re.findall(r"[A-Za-z][A-Za-z0-9-]*", l) if m[0].isupper() or len(m) > 3).lower()


def candidats(graphe: str, d: dict, memo: dict) -> list[tuple[str, str, float, str]]:
    """(id_a, id_b, score, raison) des paires à trancher, hors paires déjà tranchées."""
    concepts = [n for n in d["nodes"] if str(n.get("id", "")).startswith("concept_")]
    ids = [n["id"] for n in concepts]
    labels = [str(n.get("label") or "").strip() for n in concepts]
    deg = collections.Counter()
    for l in d["links"]:
        deg[l["source"]] += 1; deg[l["target"]] += 1
    deja = set(memo["alias"]) | {x for k in memo["refuses"] for x in k.split("|")}
    tranchees = set(memo["refuses"]) | {f"{a}|{b}" for a, b in memo["alias"].items()}

    def cle(a: str, b: str) -> str:
        return "|".join(sorted((a, b)))

    out: dict[str, tuple[str, str, float, str]] = {}
    # 1. la règle du sigle
    par_norm: dict[str, list[int]] = collections.defaultdict(list)
    for i, l in enumerate(labels):
        par_norm[norm(l)].append(i)
    for i, l in enumerate(labels):
        m = re.match(r"^(.*?)\s*\(([A-Z][A-Za-z0-9.-]{1,12})\)$", l)
        if not m:
            continue
        long, sigle = m.group(1), m.group(2)
        if initiales(long) != sigle.lower().replace(".", "").replace("-", ""):
            continue
        for forme in (long, sigle):
            for j in par_norm.get(norm(forme), []):
                if j != i and ids[i] not in memo["alias"] and ids[j] not in memo["alias"]:
                    k = cle(ids[i], ids[j])
                    if k not in tranchees:
                        out[k] = (ids[i], ids[j], 1.0, "sigle")
    # 2. la traduction : le nom anglais d'un libellé étranger (traduire) qui est le libellé d'une autre notion
    for i, n in enumerate(ids):
        en = (memo.get("anglais") or {}).get(n)
        if not en or n in memo["alias"]:
            continue
        for j in par_norm.get(norm(en), []):
            if j != i and ids[j] not in memo["alias"]:
                k = cle(ids[i], ids[j])
                if k not in tranchees and k not in out:
                    out[k] = (ids[i], ids[j], 1.0, "traduction")
    # 3. l'encodeur
    try:
        import numpy as np  # noqa: PLC0415
        from newskit import synthese as s  # noqa: PLC0415
        enc = s.encodeur()
    except Exception as e:  # noqa: BLE001
        print(f"  (encodeur indisponible : {str(e)[:100]} ; règle du sigle seule)", file=sys.stderr)
        enc = None
    if enc is not None and labels:
        V = np.array(list(enc.embed(labels, batch_size=256)), dtype=np.float32)
        V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
        for i0 in range(0, len(V), 2000):
            S = V[i0:i0 + 2000] @ V.T
            for a, b in zip(*np.where(S >= SEUIL)):
                a = int(a) + i0; b = int(b)
                if a >= b or norm(labels[a]) == norm(labels[b]):
                    continue
                if ids[a] in memo["alias"] or ids[b] in memo["alias"]:
                    continue
                k = cle(ids[a], ids[b])
                if k not in tranchees and k not in out:
                    out[k] = (ids[a], ids[b], float(S[a - i0, b]), "encodeur")
    # les paires lourdes d'abord : c'est là que la fragmentation coûte
    return sorted(out.values(), key=lambda t: -(deg[t[0]] + deg[t[1]]))


def degres_des_lots(graphe: str) -> collections.Counter:
    """Le degré de chaque nœud dans les lots de couche 2 (avant toute fusion) : c'est lui qui dit quel libellé est le
    plus employé, le graphe composé portant déjà sur le canonique les arêtes du groupe."""
    deg = collections.Counter()
    for g in [graphe, *cfg.graphes_inclus(graphe)]:
        for p in (cfg.chemins_du_graphe(g)["couche2"] / "fiches").glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for e in d.get("edges", []):
                deg[e.get("source")] += 1; deg[e.get("target")] += 1
    return deg


def contexte_des_lots(graphe: str, max_voisins: int = 12) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(libellé, voisins) de chaque nœud d'après les lots de couche 2, avant toute fusion : c'est ce qui reste d'un
    nœud fondu, dont le graphe composé n'a plus que le canonique (banc d'essai des doublons, 09.10.2026)."""
    label: dict[str, str] = {}
    vois: dict[str, list[str]] = collections.defaultdict(list)
    for g in [graphe, *cfg.graphes_inclus(graphe)]:
        for p in (cfg.chemins_du_graphe(g)["couche2"] / "fiches").glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for n in d.get("nodes", []):
                if n.get("id") and n.get("label"):
                    label.setdefault(n["id"], str(n["label"]))
            for e in d.get("edges", []):
                a, b = e.get("source"), e.get("target")
                if a and b:
                    if len(vois[a]) < max_voisins:
                        vois[a].append(b)
                    if len(vois[b]) < max_voisins:
                        vois[b].append(a)
    return label, {n: [label.get(v, v) for v in vs] for n, vs in vois.items()}


def contexte(d: dict) -> tuple[dict[str, str], dict[str, list[str]], collections.Counter]:
    label = {n["id"]: str(n.get("label") or n["id"]) for n in d["nodes"]}
    vois: dict[str, list[str]] = collections.defaultdict(list)
    deg = collections.Counter()
    for l in d["links"]:
        a, b = l["source"], l["target"]
        deg[a] += 1; deg[b] += 1
        if len(vois[a]) < 12:
            vois[a].append(label.get(b, b))
        if len(vois[b]) < 12:
            vois[b].append(label.get(a, a))
    return label, vois, deg


def rang(n: str, label: dict, deg: collections.Counter) -> tuple:
    """Plus le rang est haut, plus le nœud mérite d'être le canonique : l'anglais devant le français (la consigne
    d'extraction est en anglais), sauf si le français est plus de quatre fois plus relié ; puis le plus relié ; puis
    un libellé qui commence par une majuscule ; puis le plus court."""
    l = label.get(n, n)
    fr = langue(l) == "fr"
    return (not fr, deg[n] / 4.0 if fr else deg[n], l[:1].isupper(), -len(l))


def canonique(a: str, b: str, label: dict, deg: collections.Counter) -> tuple[str, str]:
    """(canonique, secondaire)."""
    return (a, b) if rang(a, label, deg) >= rang(b, label, deg) else (b, a)


def rechoisir(memo: dict, label: dict, deg: collections.Counter) -> int:
    """Rechoisit la racine de chaque groupe d'alias par `rang` sur le graphe courant : une table bâtie paire par paire
    peut avoir pris pour canonique un libellé marginal (« data minimisation », 1 arête, devant « Data Minimization »,
    53). Rend le nombre de groupes dont la racine change. Les degrés sont ceux du graphe composé, où le canonique
    porte déjà les arêtes du groupe : les degrés passés ici sont ceux des lots de couche 2 (degres_des_lots)."""
    groupes: dict[str, list[str]] = collections.defaultdict(list)
    for sec, can in memo["alias"].items():
        groupes[racine(memo["alias"], can)].append(sec)
    changes = 0
    for can, membres in groupes.items():
        tous = [can, *membres]
        lab = {n: memo["libelles"].get(n) or label.get(n, n) for n in tous}
        dg = collections.Counter({n: deg.get(n, 0) for n in tous})
        meilleur = max(tous, key=lambda n: rang(n, lab, dg))
        if meilleur != can:
            changes += 1
            for n in tous:
                if n != meilleur:
                    memo["alias"][n] = meilleur
            memo["alias"].pop(meilleur, None)
    return changes


def trancher(graphe: str, paires: list[tuple[str, str, float, str]], label: dict, vois: dict, decl: dict,
             poste: str = "alias") -> dict[str, bool]:
    """{clé de paire: même notion ?} par lots, Sonnet. Une paire sans réponse n'est pas tranchée (revient au passage suivant).
    `poste` : celui du registre des coûts (« alias » en production ; le banc d'essai de `banc_decision.py` rejoue sous le sien)."""
    verdicts: dict[str, bool] = {}
    for k0 in range(0, len(paires), LOT):
        if couts.en_limite():
            print("  ARRÊT : limite d'usage atteinte, les paires restantes attendent", file=sys.stderr)
            break
        lot = paires[k0:k0 + LOT]
        lignes = []
        for i, (a, b, _, _) in enumerate(lot):
            lignes.append(f"  {i}. A = « {label[a]} » (voisins : {' ; '.join(vois.get(a, [])[:VOISINS]) or '—'})\n"
                          f"     B = « {label[b]} » (voisins : {' ; '.join(vois.get(b, [])[:VOISINS]) or '—'})")
        rep = te.appeler_claude(
            f"Domaine : « {decl['libelle']} ». Dans un graphe de connaissances, deux nœuds A et B portent les libellés ci-dessous, "
            "avec quelques voisins pour le contexte. Pour chaque paire, dis si A et B désignent EXACTEMENT la même notion — la même "
            "chose sous deux noms : traduction (français/anglais/allemand), sigle et forme longue, variante d'orthographe ou de "
            "ponctuation, singulier/pluriel. Ce n'est PAS la même notion quand l'un est un parent ou une généralisation de l'autre, "
            "un sous-cas, une variante datée ou versionnée, un document, un événement ou une décision QUI PORTE SUR la notion, "
            "un article de loi distinct, ou quand une précision entre parenthèses change le sens (une autre loi, un autre contexte). "
            "Dans le doute, non.\n\n" + "\n".join(lignes)
            + '\n\nRéponds en JSON : {"paires": [{"i": <indice>, "meme": <true|false>}, …]}.',
            SCHEMA, MODELE, poste=poste, graphe=graphe)
        for x in (rep or {}).get("paires", []):
            i = x.get("i")
            if isinstance(i, int) and 0 <= i < len(lot):
                a, b = lot[i][0], lot[i][1]
                verdicts["|".join(sorted((a, b)))] = bool(x.get("meme"))
        print(f"  lot {k0 // LOT + 1} : {sum(1 for i in range(len(lot)) if verdicts.get('|'.join(sorted((lot[i][0], lot[i][1]))))) } fusion(s) "
              f"sur {len(lot)} paire(s)", flush=True)
    return verdicts


def passage(graphe: str, controle: bool, plafond: int) -> int:
    d = charger_graphe(graphe)
    if not d:
        print(f"  {graphe} : pas de graphe composé, rien à fondre", file=sys.stderr)
        return 1
    memo = lire_alias(graphe)
    if not controle:
        t = traduire(graphe, d, memo)
        if t:
            print(f"  {graphe} : {t} libellé(s) étranger(s) traduit(s) en anglais")
            ecrire_alias(graphe, memo)
    paires = candidats(graphe, d, memo)
    label, vois, deg = contexte(d)
    deg0 = degres_des_lots(graphe)
    for n in list(deg0):
        label.setdefault(n, memo["libelles"].get(n, n))
    deg = collections.Counter({n: deg0.get(n, deg[n]) for n in set(deg) | set(deg0)})   # degrés d'origine quand ils existent
    print(f"  {graphe} : {len(paires)} paire(s) candidate(s) non tranchée(s) ; table : {len(memo['alias'])} alias, "
          f"{len(memo['refuses'])} refus")
    for a, b, sc, raison in paires[:12]:
        print(f"    {sc:.2f} {raison:8} {label[a]} ({deg[a]})  ↔  {label[b]} ({deg[b]})")
    memo.pop("degres", None)
    if memo["alias"]:
        rechoisis = rechoisir(memo, label, deg)
        if rechoisis:
            print(f"  {rechoisis} groupe(s) dont le libellé canonique change (rang : anglais, degré, majuscule)")
            ecrire_alias(graphe, memo)
    if controle or not paires:
        return 0
    decl = cfg.graphe_declare(graphe)
    verdicts = trancher(graphe, paires[:plafond], label, vois, decl)
    fusions = 0
    for a, b, _, _ in paires[:plafond]:
        k = "|".join(sorted((a, b)))
        if k not in verdicts:
            continue
        if not verdicts[k]:
            memo["refuses"][k] = time.strftime("%Y-%m-%d")
            continue
        ca, cb = canonique(a, b, label, deg)
        ca, cb = racine(memo["alias"], ca), racine(memo["alias"], cb)
        if ca == cb:
            continue
        memo["alias"][cb] = ca
        memo["libelles"][ca] = label.get(ca, ca); memo["libelles"][cb] = label.get(cb, cb)
        fusions += 1
        print(f"    alias  {label[cb]}  →  {label[ca]}")
    # Les chaînes sont aplaties : chaque secondaire pointe directement sur sa racine ; puis la racine de chaque groupe
    # est rechoisie par `rang`.
    for k in list(memo["alias"]):
        memo["alias"][k] = racine(memo["alias"], memo["alias"][k])
    rechoisis = rechoisir(memo, label, deg)
    if rechoisis:
        print(f"  {rechoisis} groupe(s) dont le libellé canonique change")
    ecrire_alias(graphe, memo)
    print(f"  {graphe} : {fusions} alias ajouté(s), {sum(1 for k in verdicts if not verdicts[k])} paire(s) refusée(s), "
          f"{len(paires) - len(verdicts)} non tranchée(s) ; table : {len(memo['alias'])} alias. "
          f"Appliqués à la prochaine composition (fusionner_graphe.py).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--controle", action="store_true", help="les candidats, sans appel")
    ap.add_argument("--etat", action="store_true", help="la table d'alias")
    ap.add_argument("--plafond", type=int, default=PLAFOND, help=f"paires soumises au modèle par passage (défaut {PLAFOND})")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    if a.etat:
        memo = lire_alias(g)
        print(f"  {g} : {len(memo['alias'])} alias, {len(memo['refuses'])} refus, table du {memo.get('date', '—')}")
        for sec, can in sorted(memo["alias"].items(), key=lambda kv: memo["libelles"].get(kv[1], kv[1])):
            print(f"    {memo['libelles'].get(sec, sec)}  →  {memo['libelles'].get(can, can)}")
        return 0
    return passage(g, a.controle, a.plafond)


if __name__ == "__main__":
    sys.exit(main())

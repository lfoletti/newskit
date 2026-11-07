#!/usr/bin/env python3
"""Thèmes émergents d'un graphe : détectés par la carte, soumis au porteur, jamais imposés.

Les « thèmes » annoncés par le skill /kg et par la page d'accueil sont la liste
`sujets` du registre du skill (~/.claude/skills/kg/graphes.json), écrite à la
main. Rien ne la faisait évoluer. Or la carte détecte à chaque passage des
communautés de notions : c'est là que les thèmes nouveaux apparaissent de fait —
« instrument contraignant sur les armes autonomes » était une communauté
d'un graphe d'éthique avant d'être un sujet déclaré (03.10.2026).

Ce script rapproche les deux. Les sujets sont en français, les notions le plus
souvent en anglais : le vocabulaire seul ne suffit pas (« explicabilité » ne
recoupe pas « Explainable AI »). Une communauté dont les mots recoupent un sujet
est couverte sans rien demander ; pour les autres, une seule question est posée
à Claude par passage et par graphe — quel sujet couvre quelle communauté, ou
aucun — et la réponse est tenue pour un jugement, pas pour un fait :

    candidat   une communauté dont aucune notion de tête ne partage de mot avec
               un sujet déclaré, vue à au moins MIN_PASSAGES passages consécutifs
               et forte d'au moins MIN_NOTIONS notions ;
    dormant    un sujet déclaré que rien ne porte depuis MIN_PASSAGES_DORMANT
               passages : ni une communauté, ni au moins MIN_PORTEURS notions du
               graphe entier. Vingt têtes de communauté ne peuvent pas porter
               cinquante sujets fins (« DPO », « GRPO ») : c'est le graphe entier
               qui dit si un sujet est vide. Un sujet français que son vocabulaire
               ne retrouve pas (« graphes de connaissances » / « Knowledge Graph »)
               est traduit une fois par Claude en expressions du graphe, gardées
               dans l'état ; la vérification reste faite par le vocabulaire.

Le découpage en communautés dépend de la graine de hachage de Python (03.10.2026 :
1 à 3 candidats et 19 à 29 dormants selon la graine, sur un graphe identique). La
détection se relance donc d'elle-même sous PYTHONHASHSEED=0, la graine de la
routine : un lancement à la main et la routine voient les mêmes communautés.

Le second appel de modèle est le libellé d'un candidat retenu : une ligne en
français, à partir des seules notions de la communauté, une fois, et gardée.
Sans Claude (hors ligne, hors abonnement), la détection se fait au vocabulaire
seul et l'étiquette de la notion centrale sert de libellé.

Le candidat paraît dans le rapport de routine et le courriel ; il n'entre dans
`sujets` que sur décision du porteur :

    python3 -m newskit.themes_emergents --graphe <graphe>            # détecter (la routine)
    python3 -m newskit.themes_emergents --graphe <graphe> --etat     # candidats et dormants
    python3 -m newskit.themes_emergents --graphe <graphe> accepter 1 # au registre du skill
    python3 -m newskit.themes_emergents --graphe <graphe> refuser 1  # ne plus le proposer

État : graphe/<nom>/themes.json (le principal : graphe/themes.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import couts  # noqa: E402
from newskit import digerer_graphe as dg  # noqa: E402

REGISTRE_SKILL = media.registre_des_graphes()     # le registre des graphes du média (config.yaml, N5)
MIN_NOTIONS = 10
MIN_PASSAGES = 2
MIN_PASSAGES_AUTO = 3       # passages consécutifs avant qu'un candidat entre de lui-même parmi les sujets (09.10.2026)
NUITS_NEUF = 5              # nuits où un sujet neuf passe en tête de l'éclaireur et du poseur (approfondissement)
EXPRESSIONS_ARXIV = 3       # expressions anglaises ajoutées à la veille arXiv du graphe pour un sujet neuf
MIN_PASSAGES_DORMANT = 3
MIN_PORTEURS = 3            # notions du graphe entier qui suffisent à dire un sujet porté
GRAINE = "0"                # PYTHONHASHSEED de la routine (routine_hebdo.lancer)
COMMUNAUTES = 20            # plus large que la carte (12) : un thème naissant est rarement dans les douze premières
TETE = 8                    # notions de tête comparées aux sujets
MODELE = os.environ.get("SE_CLAUDE_MODELE") or media.modele("analyse")
DELAI = 120

VIDES = {"dans", "avec", "pour", "sans", "sous", "entre", "comme", "leur", "leurs", "elles", "cette", "ces",
         "des", "les", "une", "the", "and", "with", "from", "into", "that", "this", "ai", "ia", "intelligence",
         "artificielle", "artificial", "machine", "learning", "model", "models", "modèles", "data", "données",
         "systèmes", "systems", "system", "approche", "approches", "notions", "notion"}


def mots(texte: str, mini: int = 4) -> set[str]:
    """Les racines des mots porteurs d'un libellé : minuscules, sans accents, hors mots vides, tronquées à
    six lettres — « algorithmique » et « algorithmic » se rejoignent, « explicabilité » et « explainable » non :
    pour ces cas, c'est au juge de trancher. `mini` = 3 garde les sigles (« RAG », « DPO », « MCP »)."""
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {m[:6] for m in re.findall(r"[a-z0-9]+", t) if len(m) >= mini and m not in VIDES}


def chemin_etat(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "themes.json"


def lire_etat(graphe: str) -> dict:
    p = chemin_etat(graphe)
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    d.setdefault("candidats", {}); d.setdefault("dormants", {}); d.setdefault("acceptes", []); d.setdefault("refuses", [])
    d.setdefault("traductions", {}); d.setdefault("couverture", {}); d.setdefault("nouveaux", {})
    return d


def ecrire_etat(graphe: str, d: dict) -> None:
    p = chemin_etat(graphe)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def sujets_declares(graphe: str) -> list[str]:
    if not REGISTRE_SKILL.exists():
        return []
    return list((json.loads(REGISTRE_SKILL.read_text(encoding="utf-8")).get(graphe) or {}).get("sujets") or [])


def charger(graphe: str) -> nx.Graph | None:
    p = cfg.chemins_du_graphe(graphe)["graphe"]
    if not p.exists():
        return None
    return json_graph.node_link_graph(json.loads(p.read_text(encoding="utf-8")), edges="links")


def communautes_du_graphe(graphe: str, G: nx.Graph | None = None) -> list[tuple[str, list[str], float]]:
    """(étiquette, notions de la plus centrale à la moins, cohésion), comme la carte, mais plus nombreuses."""
    G = G if G is not None else charger(graphe)
    if G is None:
        return []
    out = []
    for etiquette, membres, score in dg.communautes(G, COMMUNAUTES):
        out.append((etiquette, [dg._label(G, n) for n in membres], score))
    return out


def notions_du_graphe(G: nx.Graph) -> list[str]:
    """Les étiquettes de toutes les notions du graphe (les auteurs, papiers et dépôts n'en sont pas)."""
    return [str(G.nodes[n].get("label") or n) for n in G if dg._type(G, n) in dg.TYPES_CONCEPT]


def porteurs_de(notions: list[str]) -> dict[str, int]:
    """{racine: nombre de notions dont l'étiquette la contient}."""
    out: dict[str, int] = {}
    for etiquette in notions:
        for m in mots(etiquette, 3):
            out[m] = out.get(m, 0) + 1
    return out


def porte(expressions: list[str], porteurs: dict[str, int]) -> bool:
    """Vrai si une racine d'une des expressions se retrouve dans au moins MIN_PORTEURS notions. Volontairement
    large : un sujet n'est dit dormant que si le graphe n'en dit rien du tout."""
    return any(porteurs.get(m, 0) >= MIN_PORTEURS for e in expressions for m in mots(e, 3))


def cle_de(etiquette: str) -> str:
    return "-".join(sorted(mots(etiquette))) or etiquette.lower()


SCHEMA_COUVERTURE = {"type": "object", "required": ["couverture"],
                     "properties": {"couverture": {"type": "array", "items": {
                         "type": "object", "required": ["communaute", "sujet"],
                         "properties": {"communaute": {"type": "integer"}, "sujet": {"type": ["integer", "null"]}}}}}}


POSTE = "themes"             # le poste consigné au registre des coûts ; un script appelant pose le sien (synthese : pyramide)
GRAPHE_COURANT: str | None = None


def appeler_claude(prompt: str, schema: dict | None = None, modele: str | None = None,
                   poste: str | None = None, graphe: str | None = None) -> str | dict | None:
    """Une réponse de Claude (`claude -p`, abonnement), sans outil ; None s'il manque ou échoue. `modele` remplace
    MODELE pour cet appel seulement : sûr quand plusieurs appels se chevauchent (synthese.py, 06.10.2026).
    Chaque appel est consigné au registre des coûts (couts.py) ; en limite d'usage, rien n'est tenté."""
    m = modele or MODELE
    if couts.en_limite():
        return None
    # Toujours l'enveloppe JSON, même pour une réponse en texte : c'est elle qui porte le coût et les tokens.
    args = [*couts.CLAUDE_P, "--no-session-persistence", "--tools", "", "--output-format", "json"] + (["--model", m] if m else [])
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=DELAI, cwd=RACINE)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        couts.enregistrer(poste or POSTE, graphe or GRAPHE_COURANT, m, None, time.time() - t0, note=type(e).__name__)
        return None
    env = couts.enregistrer(poste or POSTE, graphe or GRAPHE_COURANT, m, r, time.time() - t0)
    if r.returncode != 0 or not env or env.get("is_error"):
        return None
    if not schema:
        return str(env.get("result") or "")
    brut = env.get("structured_output") or env.get("result")
    if isinstance(brut, str):
        try:
            brut = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", brut.strip(), flags=re.S))
        except json.JSONDecodeError:
            return None
    return brut if isinstance(brut, dict) else None


def couverture_par_claude(graphe: str, sujets: list[str], restantes: list[tuple[int, str, list[str]]]) -> dict[int, int | None]:
    """{indice de communauté: indice du sujet qui la couvre, ou None}. Vide si Claude manque."""
    if not sujets or not restantes:
        return {}
    decl = cfg.graphe_declare(graphe)
    prompt = (f"Graphe de connaissances sur « {decl['libelle']} ». Ses sujets déclarés :\n"
              + "\n".join(f"  S{i}. {s}" for i, s in enumerate(sujets))
              + "\n\nDes groupes de notions détectés dans le graphe :\n"
              + "\n".join(f"  C{i}. {e} — {', '.join(n)}" for i, e, n in restantes)
              + "\n\nPour chaque groupe, dis quel sujet déclaré le couvre déjà (le groupe en est un cas, un aspect ou une "
                "déclinaison), ou null si aucun sujet ne le couvre et qu'il mériterait un sujet à part. Un groupe qui n'est "
                "qu'un acteur (une entreprise, un institut) sans thème propre est couvert par le sujet le plus proche. "
                "Réponds en JSON : {\"couverture\": [{\"communaute\": <indice C>, \"sujet\": <indice S ou null>}, …]}.")
    rep = appeler_claude(prompt, SCHEMA_COUVERTURE)
    if not rep:
        return {}
    out: dict[int, int | None] = {}
    for x in rep.get("couverture", []):
        try:
            c, s = int(x["communaute"]), x["sujet"]
        except (KeyError, TypeError, ValueError):
            continue
        out[c] = int(s) if isinstance(s, int) and 0 <= s < len(sujets) else None
    return out


SCHEMA_TRADUCTIONS = {"type": "object", "required": ["traductions"],
                      "properties": {"traductions": {"type": "array", "items": {
                          "type": "object", "required": ["sujet", "expressions"],
                          "properties": {"sujet": {"type": "integer"},
                                         "expressions": {"type": "array", "items": {"type": "string"}}}}}}}


def traduire_par_claude(graphe: str, sujets: list[str]) -> dict[str, list[str]]:
    """{sujet: expressions sous lesquelles les notions du graphe le nommeraient}. Vide si Claude manque."""
    if not sujets:
        return {}
    decl = cfg.graphe_declare(graphe)
    prompt = (f"Graphe de connaissances sur « {decl['libelle']} ». Ses notions sont le plus souvent nommées en anglais. "
              "Voici des sujets déclarés en français :\n"
              + "\n".join(f"  S{i}. {s}" for i, s in enumerate(sujets))
              + "\n\nPour chacun, donne une à trois expressions courtes sous lesquelles une notion du graphe le "
                "nommerait (le terme anglais d'usage, un sigle, un synonyme). Ne donne que des termes propres au sujet, "
                "pas de mot générique. Réponds en JSON : "
                "{\"traductions\": [{\"sujet\": <indice S>, \"expressions\": [\"…\"]}, …]}.")
    rep = appeler_claude(prompt, SCHEMA_TRADUCTIONS)
    if not rep:
        return {}
    out: dict[str, list[str]] = {}
    for x in rep.get("traductions", []):
        try:
            i, expr = int(x["sujet"]), [str(e)[:80] for e in x["expressions"] if str(e).strip()][:3]
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(sujets) and expr:
            out[sujets[i]] = expr
    return out


def detecter(graphe: str, communautes: list[tuple[str, list[str], float]], sujets: list[str], etat: dict,
             horo: str, juge=None, notions: list[str] | None = None, traduire=None) -> dict:
    """Met l'état à jour avec les communautés de ce passage. Rend l'état ; n'écrit rien.

    `juge(sujets, restantes) -> {indice communauté: indice sujet | None}` tranche les communautés
    que le vocabulaire ne rattache à aucun sujet ; sans juge, elles sont toutes candidates.
    `notions` : les étiquettes de toutes les notions du graphe ; un sujet qu'aucune communauté ne
    couvre mais que MIN_PORTEURS notions portent n'est pas dormant. `traduire(sujets) -> {sujet:
    expressions}` est demandé une fois pour les sujets que leur propre vocabulaire ne retrouve pas.
    """
    porteurs = porteurs_de(notions) if notions is not None else None    # avant les boucles : `notions` y est réemployé
    vocab_sujets = [mots(s) for s in sujets]
    vus: set[str] = set()
    couverts_par_sujet = [False] * len(sujets)
    assez = [(i, e, n, s) for i, (e, n, s) in enumerate(communautes) if len(n) >= MIN_NOTIONS]
    recoupements: dict[int, list[int]] = {}
    for i, etiquette, notions, _ in assez:
        vocab = set().union(*(mots(x) for x in [etiquette, *notions[:TETE]]))
        recoupements[i] = [k for k, v in enumerate(vocab_sujets) if v & vocab]
    restantes = [(i, e, n[:TETE]) for i, e, n, _ in assez if not recoupements[i]]
    # Les verdicts du modèle sont gardés dans l'état (08.10.2026) : les mêmes communautés revenaient chaque nuit et la
    # même question leur était reposée. Un verdict vaut tant que les sujets déclarés ne changent pas (empreinte).
    cache = etat.setdefault("couverture", {})
    empreinte_sujets = hashlib.sha1("\n".join(sujets).encode("utf-8")).hexdigest()[:12]
    for k in [k for k, v in cache.items() if v.get("sujets") != empreinte_sujets]:
        del cache[k]
    verdicts: dict[int, int | None] = {}
    a_juger = []
    for i, e, n in restantes:
        c = cache.get(cle_de(e))
        if c is not None:
            verdicts[i] = c.get("sujet") if isinstance(c.get("sujet"), int) and 0 <= c["sujet"] < len(sujets) else None
        else:
            a_juger.append((i, e, n))
    if juge and a_juger:
        nouveaux = juge(sujets, a_juger)
        for i, e, _ in a_juger:
            if i in nouveaux:
                verdicts[i] = nouveaux[i]
                cache[cle_de(e)] = {"sujet": nouveaux[i], "sujets": empreinte_sujets, "date": horo, "etiquette": e}
    if restantes:
        print(f"  couverture : {len(restantes) - len(a_juger)} communauté(s) déjà jugée(s), {len(a_juger)} soumise(s) au modèle")
    for i, etiquette, notions, score in assez:
        tete = notions[:TETE]
        recoupe = list(recoupements[i])
        if not recoupe and verdicts.get(i) is not None:
            recoupe = [verdicts[i]]
        for k in recoupe:
            couverts_par_sujet[k] = True
        if recoupe:
            continue
        k = cle_de(etiquette)
        if k in etat["refuses"] or k in etat["acceptes"]:
            continue
        vus.add(k)
        c = etat["candidats"].get(k) or {"etiquette": etiquette, "premier": horo, "passages": 0, "libelle": None}
        c.update({"etiquette": etiquette, "notions": tete, "taille": len(notions), "cohesion": round(score, 2),
                  "passages": c["passages"] + (c.get("dernier") != horo), "dernier": horo})   # un passage par jour
        etat["candidats"][k] = c
    # Un candidat absent de ce passage repart de zéro : la persistance se compte sur des passages consécutifs.
    for k in [k for k in etat["candidats"] if k not in vus]:
        del etat["candidats"][k]
    if porteurs is not None:
        trad = etat.setdefault("traductions", {})
        for s in [s for s in trad if s not in sujets]:
            del trad[s]
        vides = [s for i, s in enumerate(sujets)
                 if not couverts_par_sujet[i] and not porte([s], porteurs) and s not in trad]
        if traduire and vides:
            trad.update(traduire(vides))
        for i, s in enumerate(sujets):
            if not couverts_par_sujet[i]:
                couverts_par_sujet[i] = porte([s, *trad.get(s, [])], porteurs)
    for i, s in enumerate(sujets):
        d = etat["dormants"].get(s) or {"passages": 0, "premier": horo}
        if couverts_par_sujet[i]:
            etat["dormants"].pop(s, None)
        else:
            d["passages"] += d.get("dernier") != horo; d["dernier"] = horo
            etat["dormants"][s] = d
    for s in [s for s in etat["dormants"] if s not in sujets]:
        del etat["dormants"][s]
    return etat


def retenus(etat: dict) -> list[tuple[str, dict]]:
    """Les candidats qui ont tenu assez de passages, les plus gros d'abord."""
    r = [(k, c) for k, c in etat["candidats"].items() if c["passages"] >= MIN_PASSAGES]
    return sorted(r, key=lambda kc: (-kc[1]["taille"], kc[0]))


def dormants(etat: dict) -> list[tuple[str, dict]]:
    return sorted(((s, d) for s, d in etat["dormants"].items() if d["passages"] >= MIN_PASSAGES_DORMANT),
                  key=lambda sd: -sd[1]["passages"])


def nommer(graphe: str, c: dict) -> str | None:
    """Un libellé d'une ligne, en français, à partir des seules notions de la communauté. None si Claude manque."""
    decl = cfg.graphe_declare(graphe)
    prompt = (f"Voici les notions les plus centrales d'un groupe détecté dans un graphe de connaissances sur « {decl['libelle']} » :\n"
              + "\n".join(f"- {n}" for n in c["notions"])
              + "\n\nNomme le thème que ces notions ont en commun, en français, en une ligne de 4 à 12 mots, "
                "sans point final, sans guillemets, à la manière de « équité algorithmique et biais » ou "
                "« armes autonomes et IA militaire : CCW, contrôle humain ». Ne nomme que ce que les notions "
                "montrent. Réponds par la ligne seule.")
    rep = appeler_claude(prompt)
    if not rep:
        return None
    lignes = [l.strip().strip('"«»').strip() for l in rep.strip().splitlines() if l.strip()]
    ligne = lignes[-1] if lignes else ""
    return ligne[:120] if 3 <= len(ligne.split()) <= 16 else None


def afficher(graphe: str, etat: dict) -> None:
    r, d = retenus(etat), dormants(etat)
    print(f"  {graphe} : {len(etat['candidats'])} communauté(s) hors des sujets déclarés, "
          f"{len(r)} retenue(s) (≥ {MIN_PASSAGES} passages, ≥ {MIN_NOTIONS} notions), {len(d)} sujet(s) dormant(s)")
    for n, (k, c) in enumerate(r, 1):
        print(f"    {n}. {c.get('libelle') or c['etiquette']}  — {c['taille']} notions, {c['passages']} passages")
        print(f"       notions : {', '.join(c['notions'][:6])}")
    for s, v in d:
        print(f"    dormant : « {s} » — ni communauté ni notion ne le porte depuis {v['passages']} passages")
    if r:
        print(f"  Accepter : python3 -m newskit.themes_emergents --graphe {graphe} accepter <n>   (refuser <n> pour ne plus le voir)")


def accepter(graphe: str, n: int, etat: dict) -> int:
    r = retenus(etat)
    if not 1 <= n <= len(r):
        print(f"  pas de candidat n° {n} (il y en a {len(r)})", file=sys.stderr); return 1
    k, c = r[n - 1]
    libelle = c.get("libelle") or c["etiquette"]
    reg = json.loads(REGISTRE_SKILL.read_text(encoding="utf-8"))
    sujets = reg.setdefault(graphe, {}).setdefault("sujets", [])
    if libelle not in sujets:
        sujets.append(libelle)
    REGISTRE_SKILL.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    etat["acceptes"].append(k); del etat["candidats"][k]
    entrer_sujet(graphe, libelle, etat, k, c.get("notions", [])[:6], "thème émergent")
    for script in (REGISTRE_SKILL.parent / "synchroniser.py", CODE / "archiver_skill.py"):
        if script.exists():
            subprocess.run([sys.executable, str(script)], cwd=RACINE, capture_output=True, text=True, timeout=120)
    print("  skill synchronisé et archivé ; la page d'accueil le reprendra au prochain envoi")
    return 0


def entrer_sujet(graphe: str, libelle: str, etat: dict, cle: str, notions: list[str], origine: str) -> None:
    """La chaîne d'approfondissement d'un sujet neuf (porteur, 09.10.2026) : en tête de l'éclaireur et du poseur
    NUITS_NEUF nuits, expressions anglaises ajoutées à la veille arXiv, canon du modèle redemandé de lui-même (son
    empreinte change avec les sujets), sous-jeu du banc par thème composé par la routine ; « tenu » quand le banc
    par thème lui donne 7 réponses justes sur 10. Commune aux candidats acceptés et aux sujets déclarés à la main."""
    expressions = []
    try:
        expressions = (traduire_par_claude(graphe, [libelle]) or {}).get(libelle, [])[:EXPRESSIONS_ARXIV]
    except Exception as e:  # noqa: BLE001
        print(f"  (expressions arXiv non obtenues : {str(e)[:80]})", file=sys.stderr)
    ajoutees = ajouter_themes_arxiv(graphe, libelle, expressions) if expressions else []
    etat["nouveaux"][libelle] = {"date": time.strftime("%Y-%m-%d"), "cle": cle, "nuits": 0, "arxiv": ajoutees,
                                 "statut": "en cours", "notions": notions, "origine": origine}
    ecrire_etat(graphe, etat)
    print(f"  sujet ajouté au registre du skill ({graphe}) : {libelle}")
    if ajoutees:
        print(f"  veille arXiv : {len(ajoutees)} expression(s) ajoutée(s) au registre — {' ; '.join(ajoutees)}")


def declarer(graphe: str, libelle: str, etat: dict) -> int:
    """Un sujet déclaré à la main par le porteur, avec la même chaîne d'approfondissement qu'un thème émergent accepté.
    Le sous-jeu du banc par thème est composé par la routine (ou : banc_themes.py --composer --themes « … »)."""
    libelle = " ".join(libelle.split())
    if len(libelle.split()) < 3:
        print("  libellé trop court", file=sys.stderr); return 2
    reg = json.loads(REGISTRE_SKILL.read_text(encoding="utf-8"))
    sujets = reg.setdefault(graphe, {}).setdefault("sujets", [])
    if any(x.casefold() == libelle.casefold() for x in sujets):
        print(f"  déjà parmi les sujets de {graphe}"); return 0
    sujets.append(libelle)
    REGISTRE_SKILL.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    entrer_sujet(graphe, libelle, etat, cle_de(libelle), [], "porteur")
    for script in (REGISTRE_SKILL.parent / "synchroniser.py", CODE / "archiver_skill.py"):
        if script.exists():
            subprocess.run([sys.executable, str(script)], cwd=RACINE, capture_output=True, text=True, timeout=120)
    print(f"  chaîne lancée : éclaireur et poseur en tête {NUITS_NEUF} passages, canon redemandé à la prochaine mesure, "
          f"sous-jeu du banc par thème à la prochaine routine (ou maintenant : python3 -m newskit.banc_themes --graphe {graphe} --composer --themes \"{libelle[:40]}…\")")
    return 0


def renommer(graphe: str, ancien: str, nouveau: str, etat: dict) -> int:
    """Renomme un sujet déclaré partout où son libellé fait clé : skill /kg, `nouveaux` de themes.json, jeu du banc par
    thème et son historique, commentaire de la veille arXiv dans sources.yaml. Idempotent."""
    nouveau = " ".join(nouveau.split())
    reg = json.loads(REGISTRE_SKILL.read_text(encoding="utf-8"))
    sujets = reg.setdefault(graphe, {}).setdefault("sujets", [])
    cibles = [x for x in sujets if x.casefold() == ancien.casefold() or ancien.casefold() in x.casefold()]
    if len(cibles) > 1:
        print(f"  {len(cibles)} sujets correspondent à « {ancien} » : préciser", file=sys.stderr); return 1
    # Déjà renommé au skill (second passage, après qu'un jeu composé sous l'ancien libellé est arrivé) : on poursuit
    # avec le libellé donné, pour le jeu, l'historique et `nouveaux`.
    vieux = cibles[0] if cibles else " ".join(ancien.split())
    if cibles:
        sujets[sujets.index(vieux)] = nouveau
        REGISTRE_SKILL.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if vieux in etat["nouveaux"]:
        etat["nouveaux"][nouveau] = etat["nouveaux"].pop(vieux)
    ecrire_etat(graphe, etat)
    base = cfg.chemins_du_graphe(graphe)["base"]
    n_q = 0
    for nom in ("themes_banc.json",):
        p = base / nom
        if p.exists():
            q = json.loads(p.read_text(encoding="utf-8"))
            for x in q:
                if x.get("theme") == vieux or (not cibles and ancien.casefold() in str(x.get("theme", "")).casefold()):
                    x["theme"] = nouveau; n_q += 1
            p.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    p = base / "themes_banc_historique.jsonl"
    if p.exists():
        p.write_text(p.read_text(encoding="utf-8").replace(json.dumps(vieux, ensure_ascii=False), json.dumps(nouveau, ensure_ascii=False)), encoding="utf-8")
    t = cfg.FICHIER.read_text(encoding="utf-8")
    if f"pour le sujet « {vieux[:70]} »" in t:
        cfg.FICHIER.write_text(t.replace(f"pour le sujet « {vieux[:70]} »", f"pour le sujet « {nouveau[:70]} »"), encoding="utf-8")
    for script in (REGISTRE_SKILL.parent / "synchroniser.py", CODE / "archiver_skill.py"):
        if script.exists():
            subprocess.run([sys.executable, str(script)], cwd=RACINE, capture_output=True, text=True, timeout=120)
    print(f"  sujet renommé ({graphe}) : « {vieux[:60]} » → « {nouveau[:80]} » ; {n_q} question(s) du banc par thème suivies")
    return 0


def accepter_auto(graphe: str, etat: dict) -> list[str]:
    """Les candidats tenus MIN_PASSAGES_AUTO passages et nommés entrent d'eux-mêmes parmi les sujets déclarés (porteur,
    09.10.2026 : la routine ajoute les thèmes émergents au graphe, et le banc par thème leur fait un sous-jeu de
    questions). Rend les libellés ajoutés. Se défait par `retirer`."""
    ajoutes = []
    for k, c in list(retenus(etat)):
        if c["passages"] < MIN_PASSAGES_AUTO or not c.get("libelle"):
            continue
        n = [x for x, _ in retenus(etat)].index(k) + 1
        if accepter(graphe, n, etat) == 0:
            ajoutes.append(c["libelle"])
    return ajoutes


def ajouter_themes_arxiv(graphe: str, libelle: str, expressions: list[str], registre: Path | None = None) -> list[str]:
    """Ajoute des thèmes à la veille arXiv du graphe dans sources.yaml, par édition des seules lignes du bloc
    `veille_arxiv.graphes.<graphe>.themes` (les commentaires du registre sont conservés, comme collecte_arxiv le fait
    pour retirer un thème muet). Rend les expressions ajoutées ; vide si le graphe n'a pas de veille."""
    registre = registre or cfg.FICHIER
    lignes = registre.read_text(encoding="utf-8").split("\n")
    debut = next((i for i, l in enumerate(lignes) if l.startswith("veille_arxiv:")), None)
    if debut is None:
        return []
    i_g = next((i for i in range(debut, len(lignes)) if lignes[i].rstrip() == f"    {graphe}:"), None)
    if i_g is None:
        return []
    i_t = next((i for i in range(i_g + 1, len(lignes)) if lignes[i].rstrip() == "      themes:"
                or (lignes[i].strip() and not lignes[i].startswith("      ") and not lignes[i].startswith("    #"))), None)
    if i_t is None or lignes[i_t].rstrip() != "      themes:":
        return []
    fin = i_t + 1
    while fin < len(lignes) and (lignes[fin].startswith("        ") or not lignes[fin].strip()):
        if not lignes[fin].strip():
            break
        fin += 1
    deja = {l.strip()[2:].strip().strip('"').casefold() for l in lignes[i_t + 1:fin] if l.strip().startswith("- ")}
    neuves = [e.strip() for e in expressions if e.strip() and e.strip().casefold() not in deja]
    if not neuves:
        return []
    ajout = [f"        # ajoutés le {time.strftime('%Y-%m-%d')} par themes_emergents.py pour le sujet « {libelle[:70]} »"]
    ajout += [f"        - {e}" for e in neuves]
    lignes[fin:fin] = ajout
    registre.write_text("\n".join(lignes), encoding="utf-8")
    return neuves


def nouveaux_en_cours(graphe: str, etat: dict | None = None) -> list[str]:
    """Les sujets neufs encore en approfondissement (moins de NUITS_NEUF nuits, non tenus), le plus récent d'abord."""
    etat = etat if etat is not None else lire_etat(graphe)
    # Les plus faibles au banc par thème d'abord (un sujet jamais mesuré compte comme 0), puis les plus récents.
    return [lib for lib, v in sorted(etat["nouveaux"].items(), key=lambda kv: (kv[1].get("juste", 0.0), kv[1].get("date", "")))
            if v.get("statut") == "en cours" and v.get("nuits", 0) < NUITS_NEUF]


def retirer(graphe: str, libelle: str, etat: dict) -> int:
    """Retire un sujet accepté (à la main ou de lui-même) des sujets déclarés ; sa communauté redevient candidate et
    peut rentrer si elle se représente (le retrait n'est pas un refus : `refuser` pour cela)."""
    reg = json.loads(REGISTRE_SKILL.read_text(encoding="utf-8"))
    sujets = reg.setdefault(graphe, {}).setdefault("sujets", [])
    cibles = [x for x in sujets if x.casefold() == libelle.casefold() or libelle.casefold() in x.casefold()]
    if len(cibles) != 1:
        print(f"  {len(cibles)} sujet(s) correspondent à « {libelle} » : préciser", file=sys.stderr); return 1
    sujets.remove(cibles[0])
    REGISTRE_SKILL.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Retirer n'est pas refuser (porteur, 09.10.2026) : la communauté redevient candidate, et si elle se représente
    # trois passages, le sujet rentre. `refuser <n>` reste le non définitif.
    k = (etat["nouveaux"].get(cibles[0]) or {}).get("cle") or cle_de(cibles[0])
    if k in etat["acceptes"]:
        etat["acceptes"].remove(k)
    etat["nouveaux"].pop(cibles[0], None)
    etat.setdefault("retires", []).append({"libelle": cibles[0], "date": time.strftime("%Y-%m-%d")})
    ecrire_etat(graphe, etat)
    print(f"  sujet retiré du registre du skill ({graphe}) : {cibles[0]}")
    for script in (REGISTRE_SKILL.parent / "synchroniser.py", CODE / "archiver_skill.py"):
        if script.exists():
            subprocess.run([sys.executable, str(script)], cwd=RACINE, capture_output=True, text=True, timeout=120)
    return 0


def refuser(graphe: str, n: int, etat: dict) -> int:
    r = retenus(etat)
    if not 1 <= n <= len(r):
        print(f"  pas de candidat n° {n} (il y en a {len(r)})", file=sys.stderr); return 1
    k, c = r[n - 1]
    etat["refuses"].append(k); del etat["candidats"][k]
    ecrire_etat(graphe, etat)
    print(f"  candidat écarté, il ne sera plus proposé : {c.get('libelle') or c['etiquette']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--etat", action="store_true", help="afficher sans détecter")
    ap.add_argument("--sans-modele", action="store_true", help="ne pas demander de libellé à Claude")
    ap.add_argument("action", nargs="?", choices=["accepter", "refuser", "auto", "retirer", "declarer", "renommer"],
                    help="auto : accepter les candidats tenus MIN_PASSAGES_AUTO passages ; retirer <libellé> : défaire ; "
                         "declarer <libellé> : un sujet du porteur, avec la chaîne d'approfondissement")
    ap.add_argument("numero", nargs="?", help="numéro du candidat (accepter, refuser) ou libellé (retirer, declarer, renommer)")
    ap.add_argument("nouveau", nargs="?", help="renommer : le nouveau libellé")
    args = ap.parse_args()
    graphe = cfg.nom_actuel(args.graphe)
    global GRAPHE_COURANT
    GRAPHE_COURANT = graphe
    etat = lire_etat(graphe)
    if args.action == "auto":
        ajoutes = accepter_auto(graphe, etat)
        print(f"  {len(ajoutes)} sujet(s) ajouté(s) de lui-même" + (" : " + " ; ".join(ajoutes) if ajoutes else ""))
        return 0
    if args.action == "retirer":
        if not args.numero:
            print("  libellé du sujet attendu", file=sys.stderr); return 2
        return retirer(graphe, str(args.numero), etat)
    if args.action == "renommer":
        if not args.numero or not args.nouveau:
            print("  ancien et nouveau libellés attendus", file=sys.stderr); return 2
        return renommer(graphe, str(args.numero), args.nouveau, etat)
    if args.action == "declarer":
        if not args.numero:
            print("  libellé du sujet attendu", file=sys.stderr); return 2
        return declarer(graphe, str(args.numero), etat)
    if args.action:
        if args.numero is None or not str(args.numero).isdigit():
            print("  numéro du candidat attendu", file=sys.stderr); return 2
        return (accepter if args.action == "accepter" else refuser)(graphe, int(args.numero), etat)
    if args.etat:
        afficher(graphe, etat); return 0
    if os.environ.get("PYTHONHASHSEED") != GRAINE:      # la graine se fixe au démarrage de l'interpréteur
        os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, "PYTHONHASHSEED": GRAINE})
    G = charger(graphe)
    notions = notions_du_graphe(G) if G is not None else []
    if len(notions) < MIN_NOTIONS:
        # Un graphe sans couche de notions (un graphe d'image, sous un ancien nom, le 03.10.2026) n'a ni communauté ni porteur :
        # tous ses sujets passeraient pour dormants. Rien à juger, l'état n'est pas touché.
        print(f"  {graphe} : {len(notions)} notion(s) dans le graphe — ni candidat ni dormant à juger")
        return 0
    horo = time.strftime("%Y-%m-%d")
    juge = None if args.sans_modele else (lambda sujets, restantes: couverture_par_claude(graphe, sujets, restantes))
    traduire = None if args.sans_modele else (lambda sujets: traduire_par_claude(graphe, sujets))
    etat = detecter(graphe, communautes_du_graphe(graphe, G), sujets_declares(graphe), etat, horo, juge,
                    notions=notions, traduire=traduire)
    if not args.sans_modele:
        for k, c in retenus(etat):
            if not c.get("libelle"):
                c["libelle"] = nommer(graphe, c)
    ecrire_etat(graphe, etat)
    afficher(graphe, etat)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Le banc de questions : le graphe répond-il mieux qu'un modèle, et qu'un modèle qui cherche sur le web ?

La métrique de tête de l'acuité (acuite.py). Les mêmes questions sont posées dans trois conditions :

    graphe   le modèle, avec la carte du graphe et son outil d'interrogation, et rien d'autre ;
    modele   le modèle seul, de mémoire ;
    web      le modèle doté de la recherche web.

Chaque réponse est jugée contre la réponse attendue et la citation de sa source : juste, fausse, ou
« ne sait pas ». Le faux compte autant que le juste — un graphe qui dit « rien là-dessus » vaut mieux
qu'un modèle qui invente.

D'où viennent les questions, pour que le graphe ne gagne pas par construction :
    recent        d'une fiche du graphe postérieure à l'horizon du modèle : ce qui s'est passé ;
    precis        de la même fiche : une version, un chiffre, une date, qui a dit quoi ;
    fond          d'une fiche antérieure à l'horizon (jalon, référence) : le socle du domaine ;
    relationnel   d'un lien que le graphe porte entre deux notions ;
    exterieur     d'une page que le graphe N'A PAS, trouvée sur le web dans ses sujets : c'est l'épreuve
                  de la complétude, celle que le graphe peut perdre ;
    porteur       les questions réelles restées sans réponse (lacunes.py), quand il y en a.

Le jeu de questions est gardé (graphe/<nom>/banc_questions.json) et rejoué tel quel d'une mesure à l'autre,
pour que la courbe se lise ; `--renouveler` en tire un nouveau. Bornes au registre (clé `banc`).

    python3 -m newskit.banc --graphe <graphe>              # mesure, écrit graphe/<nom>/banc.json
    python3 -m newskit.banc --graphe <graphe> --etat       # relit la dernière mesure
    python3 -m newskit.banc --graphe <graphe> --renouveler # nouveau jeu de questions, puis mesure

Limites : le juge est un modèle, il juge contre la citation et non contre sa mémoire, et ses verdicts
sont gardés pour être relus (banc.json, `details`). Trente questions classent, elles ne mesurent pas à
deux points près.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import couts  # noqa: E402
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

PYTHON = sys.executable
# Les outils que le modèle lance pendant un banc : sous CODE, la session tournant dans la racine des données (N1).
OUTIL_GRAPHE = f"{PYTHON} {CODE / 'interroger_graphe.py'}"
OUTIL_SYNTHESE = f"{PYTHON} {CODE / 'synthese.py'}"
CONDITIONS = ("graphe", "modele", "web")
CONDITIONS_POSEES: tuple[str, ...] = (
    CONDITIONS  # --conditions : les seules conditions posées (porteur, 10.10.2026 : « limite le tirage au
)
# graphe, saute LLM et LLM+web ») ; une condition non posée est absente du résultat (« — »)
# Le graphe est interrogé par sa pyramide de synthèses (newskit.synthese) quand il en a une, avec un modèle
# léger : c'est le standard depuis le 05.10.2026 (un graphe audio : 39 réponses sur 40 hors questions extérieures, aucune
# fausse, 0,014 $ par question contre 0,062 $ par les fiches et 0,075 $ par le web). Sans pyramide, par ses fiches.
# L'ancien chemin reste posable à part pour comparer : `--reposer fiches`.
CONDITIONS_EN_PLUS = ("fiches",)
MODELE_SYNTHESE = os.environ.get("KG_MODELE_SYNTHESE") or media.modele(
    "redaction"
)  # Opus pour un graphe où Sonnet ne tient pas
LOT_SYNTHESE = 10
SYSTEME_COURT = "Tu réponds à des questions en interrogeant un graphe de connaissances par l'outil Bash, puis tu rends le JSON demandé. Sois bref."
VERDICTS = ("juste", "faux", "ne_sait_pas")
# Des pages que nos scripts peuvent lire (07.10.2026) : au premier passage du poseur, la moitié des pages manquées
# n'a pas pu être ingérée — mur d'abonnement (Bloomberg Law), page vide côté serveur, agrégateur sans texte.
PAGES_LISIBLES = (
    "Uniquement des pages ouvertes, lisibles sans abonnement ni connexion, dont le texte est dans la page "
    "elle-même (plusieurs paragraphes) : pas de mur d'abonnement (Bloomberg, FT, WSJ, The Information, "
    "Nikkei…), pas de page de liste, d'agrégateur, de résumé d'actualités du jour, ni de PDF. "
)
EXTRAIT = 700  # mots d'une fiche donnés au rédacteur de questions

SCHEMA_QUESTIONS = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["document", "type", "question", "attendu", "citation"],
                "properties": {
                    "document": {"type": "integer"},
                    "type": {"type": "string"},
                    "question": {"type": "string"},
                    "attendu": {"type": "string"},
                    "citation": {"type": "string"},
                },
            },
        }
    },
}
SCHEMA_REPONSES = {
    "type": "object",
    "required": ["reponses"],
    "properties": {
        "reponses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "reponse", "sait"],
                "properties": {
                    "i": {"type": "integer"},
                    "reponse": {"type": "string"},
                    "sait": {"type": "boolean"},
                    "source": {"type": "string"},
                },
            },
        }
    },
}
SCHEMA_VERDICTS = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "verdict"],
                "properties": {"i": {"type": "integer"}, "verdict": {"type": "string", "enum": list(VERDICTS)}},
            },
        }
    },
}


# Ce que coûte une réponse, par condition (05.10.2026) : le banc compare la justesse, il doit aussi dire le prix
# de chaque réponse — avec le graphe, de mémoire, avec le web. Rempli par repondre(), lu par mesurer() et rejuger().
CONSO: dict[str, dict] = {}
# Le poste et le graphe consignés au registre des coûts (couts.py) pour chaque appel de `claude()` ; le poseur et le banc
# par thème, qui passent par ici, posent les leurs.
CONTEXTE: dict[str, str | None] = {"poste": "banc", "graphe": None}
# Les sujets d'un domaine qui n'est pas un graphe déclaré (banc_croise.py : un thème de gouvernance.yaml), lus par le tri
# des questions hors sujet à la place de ceux du registre du skill.
SUJETS_DOMAINE: dict[str, list[str]] = {}


def compter(compte: dict | None, env: dict) -> None:
    """Ajoute à `compte` les tokens et le coût d'une réponse de `claude -p` (son enveloppe JSON)."""
    if compte is None:
        return
    u = env.get("usage") or {}
    compte["appels"] = compte.get("appels", 0) + 1
    for cle, champ in (
        ("entree", "input_tokens"),
        ("cache_ecriture", "cache_creation_input_tokens"),
        ("cache_lecture", "cache_read_input_tokens"),
        ("sortie", "output_tokens"),
    ):
        compte[cle] = compte.get(cle, 0) + int(u.get(champ) or 0)
    compte["tours"] = compte.get("tours", 0) + int(env.get("num_turns") or 0)
    compte["cout_usd"] = round(compte.get("cout_usd", 0.0) + float(env.get("total_cost_usd") or 0), 4)


def par_question(compte: dict, n: int) -> dict:
    """Le compte d'une condition ramené à une question."""
    tokens = sum(compte.get(k, 0) for k in ("entree", "cache_ecriture", "cache_lecture", "sortie"))
    return {
        **compte,
        "questions": n,
        "tokens_par_question": round(tokens / n) if n else None,
        "sortie_par_question": round(compte.get("sortie", 0) / n) if n else None,
        "cout_usd_par_question": round(compte.get("cout_usd", 0) / n, 4) if n else None,
    }


def claude(
    prompt: str,
    schema: dict,
    outils: list[str] | None = None,
    delai: int = 900,
    compte: dict | None = None,
    charges: list[str] | None = None,
    modele: str | None = None,
    systeme: str | None = None,
) -> dict | None:
    """Une réponse JSON de `claude -p`. `outils` : les outils permis ; None, aucun. `compte` : où cumuler sa consommation.
    `charges` : les seuls outils chargés (sinon tous ceux de Claude Code, relus à chaque tour) ; `systeme` : une consigne
    système qui remplace celle de Claude Code ; `modele` : un autre modèle que celui du banc."""
    args = [*couts.CLAUDE_P, "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema)]
    args += ["--allowedTools", *outils] if outils else ["--tools", ""]
    if outils and charges:
        args += ["--tools", ",".join(charges)]
    if systeme:
        args += ["--system-prompt", systeme]
    if modele or te.MODELE:
        args += ["--model", modele or te.MODELE]
    if couts.en_limite():  # coupe-circuit : la limite d'usage atteinte, rien n'est tenté
        return None
    t0 = time.time()
    try:
        r = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=delai,
            cwd=RACINE,
            env={**os.environ, "KG_SANS_LACUNE": "1"},
        )  # le banc ne crée pas de lacunes
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        couts.enregistrer(
            CONTEXTE["poste"], CONTEXTE["graphe"], modele or te.MODELE, None, time.time() - t0, note=type(e).__name__
        )
        return None
    env = couts.enregistrer(CONTEXTE["poste"], CONTEXTE["graphe"], modele or te.MODELE, r, time.time() - t0)
    if r.returncode != 0 or not env:
        return None
    try:
        compter(compte, env)
        brut = env.get("structured_output") or env.get("result")
        if isinstance(brut, str):
            m = re.search(r"\{.*\}", brut, flags=re.S)
            brut = json.loads(m.group(0)) if m else None
        return brut if isinstance(brut, dict) else None
    except (json.JSONDecodeError, AttributeError):
        return None


# --- Le jeu de questions -----------------------------------------------------------------------


def fiches_extraites(graphe: str) -> list[Path]:
    """Les fiches dont le graphe porte les notions (extraites en couche 2), y compris celles des graphes inclus."""
    # Pas de question tirée d'une source interne (notes de projet, bibliothèques, dépôts du porteur, dossier
    # `recherche/`) : un LLM ne peut pas les connaître, et le banc mesure un savoir public (04.10.2026 : des
    # questions sur le projet `music` du porteur étaient entrées dans le banc d'un graphe d'ingénierie).
    internes = [f"sources/{s.dossier}/" for s in cfg.toutes() if s.abonnement == "interne"] + ["recherche/"]
    out = []
    for g in [*cfg.graphes_inclus(graphe), graphe]:
        p = cfg.chemins_du_graphe(g)["couche2"] / "empreintes.json"
        if p.exists():
            out += [
                RACINE / k
                for k in json.loads(p.read_text(encoding="utf-8"))
                if (RACINE / k).exists() and not any(k.startswith(i) for i in internes)
            ]
    return out


def extrait(f: Path) -> str:
    champs, corps = fiche.lire_fichier(f)
    return (
        f"Titre : {champs.get('title', f.stem)}\nPublication : {champs.get('publication', '')}   Date : {champs.get('date', '')}\n"
        + " ".join(corps.split()[:EXTRAIT])
    )


def rediger(
    decl: dict,
    documents: list[tuple[str, str]],
    consigne: str,
    outils: list[str] | None = None,
    sujets: list[str] | None = None,
) -> list[dict]:
    """Les questions tirées de documents numérotés. Chaque question se comprend seule et a une réponse courte."""
    perimetre = (
        (
            "\nSujets de la veille : " + " ; ".join(sujets) + ". Un document qui n'en relève pas ne reçoit AUCUNE "
            "question ; une question doit porter sur ce que le document dit de ces sujets.\n"
        )
        if sujets
        else ""
    )
    rep = claude(
        f"Tu prépares un banc d'essai sur « {decl['libelle']} ». {consigne}\n{perimetre}\n"
        "Règles :\n"
        "- La question est celle qu'un praticien du domaine poserait réellement à une veille : ce qu'apporte une version, "
        "ce que fait une méthode, ce que vaut un résultat, qui a décidé quoi. Pas d'anecdote : ni le prix d'un greffon, "
        "ni la date d'un communiqué, ni le poste d'une personne, ni un détail de jeu, de concours ou de promotion. Un "
        "document qui n'offre que des anecdotes ne reçoit aucune question.\n"
        "- Elle se comprend SEULE, sans le document : nommer le produit, la version, l'organisation ; jamais « selon "
        "l'article » ni « dans sa présentation ».\n"
        "- Elle est rédigée en français (les noms propres et les termes techniques gardent leur forme d'usage).\n"
        "- Sa réponse est un fait précis et court, qu'on ne devine pas. `attendu` : la réponse en une ligne. `citation` : "
        "la phrase du document qui l'établit, recopiée mot pour mot. Rien que le document ne dise pas.\n\n"
        + "\n\n".join(f"--- Document {i} ({genre}) ---\n{texte}" for i, (genre, texte) in enumerate(documents))
        + '\n\nRéponds en JSON : {"questions": [{"document": <indice>, "type": "…", "question": "…", "attendu": "…", "citation": "…"}, …]}.',
        SCHEMA_QUESTIONS,
        outils,
    )
    return [q for q in (rep or {}).get("questions", []) if q.get("question") and q.get("attendu")]


TYPES = ("recent", "precis", "fonctionnement", "fond", "relationnel", "exterieur")


def type_de(q: dict, fichier: Path | None) -> str:
    """Le type d'une question, vérifié contre sa source (04.10.2026 : des questions « fond » portaient sur des
    produits de 2026, des « relationnel » ne reliaient rien). Une fiche postérieure à l'horizon du LLM ne donne
    pas de question de fond ; une antérieure ne donne pas de « récent ». Un relationnel nomme deux notions."""
    t = q.get("type") if q.get("type") in TYPES else "precis"
    if t in ("exterieur", "fonctionnement") or fichier is None:  # le fonctionnement vaut quelle que soit la date
        return t
    apres = fichier.name[:10] > cfg.HORIZON_MODELE
    if t == "fond" and apres:
        return "precis"
    if t == "recent" and not apres:
        return "fond"
    if t == "relationnel" and not q.get("relie"):
        return "precis" if apres else "fond"
    return t


def tirer_exterieures(graphe: str, n_ext: int, bornes: dict, exclues: set[str] | None = None) -> list[dict]:
    """Des questions sur des pages récentes que le graphe n'a pas, hors des domaines qu'il suit. `exclues` : des pages
    à écarter en plus (celles d'un jeu retiré, déjà ingérées), retirées après coup si le modèle les rend quand même."""
    decl = cfg.graphe_declare(graphe)
    out: list[dict] = []
    sujets = te.sujets_declares(graphe)
    from newskit import eclaireur  # noqa: PLC0415

    # Par lots de 12 pages, chacun sur ses sujets : un seul appel pour 64 pages ne tenait pas dans le délai
    # (07.10.2026, passage à 60 extérieures). Les pages déjà prises sont écartées des lots suivants.
    vues: list[str] = []
    # Un quart de pages en plus : le filtre retire anecdotes et doublons (un graphe de conformité, 07.10.2026 : 64 pages
    # pour 60 extérieures n'en ont laissé que 48). 60 extérieures → 75 pages ; 10 → 14, comme avant.
    n_pages = n_ext + max(4, n_ext // 4)
    for k in range(0, n_pages, 12):
        m = min(12, n_pages - k)
        rep = claude(
            f"Tu prépares un banc d'essai sur « {decl['libelle']} ». Cherche sur le web {m} pages de fond ou d'annonce, "
            f"publiées après le {cfg.HORIZON_MODELE}, sur ces sujets : {' ; '.join(random.sample(sujets, min(5, len(sujets))))}. "
            f"Pas de ces domaines : {', '.join(eclaireur.domaines_suivis(graphe)[:60])}. "
            + (f"Pas ces pages, déjà prises : {' ; '.join(vues[-48:])}. " if vues else "")
            + PAGES_LISIBLES
            + "Lis chaque page, puis écris pour "
            "chacune UNE question de type « exterieur » qui se comprend seule, dont la réponse est un fait précis et court "
            "établi par la page. `attendu` : la réponse ; `citation` : la phrase qui l'établit, mot pour mot, suivie de "
            "l'adresse de la page entre parenthèses. `document` : le rang de la page (0, 1, …).\n"
            'Réponds en JSON : {"questions": [{"document": <rang>, "type": "exterieur", "question": "…", "attendu": "…", "citation": "…"}, …]}.',
            SCHEMA_QUESTIONS,
            ["WebSearch", "WebFetch"],
            bornes["delai"],
        )
        for q in (rep or {}).get("questions", [])[:m]:
            if q.get("question") and q.get("attendu"):
                out.append(
                    {
                        "type": "exterieur",
                        "question": q["question"],
                        "attendu": q["attendu"],
                        "citation": q.get("citation", ""),
                        "origine": "page hors du graphe",
                        "source": "",
                    }
                )
                u = re.search(r"\((https?://[^)\s]+)\)\s*$", q.get("citation", ""))
                if u:
                    vues.append(u.group(1))
        print(f"    extérieures : lot {k // 12 + 1}, {len((rep or {}).get('questions', []))} question(s)", flush=True)
    if exclues:
        out = [
            q for q in out if not ({u.rstrip("/") for u in re.findall(r"https?://[^)\s]+", q["citation"])} & exclues)
        ]
    return out


# --- Le banc agnostique (porteur, 07.10.2026) ------------------------------------------------------------------
# Le banc ancien savait ce que le graphe possède : ses questions de restitution venaient de ses fiches (biais pour lui),
# ses extérieures de pages hors de ses sources (biais contre lui), et le score dépendait du dosage. Le banc agnostique
# ignore le graphe : un modèle écrit les questions qu'un praticien poserait sur chaque sujet déclaré (le domaine, pas le
# contenu), un agent en cherche la réponse sur le web, sur n'importe quel site, avec sa citation. Moitié de questions
# récentes (après l'horizon du modèle, sinon il répond de mémoire), moitié de fond. C'est le chiffre publié ; les
# questions tirées du graphe restent, posées au seul graphe, comme test interne de restitution.
AGNOSTIQUES = ("ag_recent", "ag_fond")
# Seules les questions récentes sont comparées au LLM (porteur, 07.10.2026) : les questions de fond sont écrites par le
# modèle lui-même, sans outil, à partir de ce qu'il sait — il y répondait à 100 % au premier banc agnostique
# (un graphe de conformité), un écho et non une mesure. Elles ne mesurent que le graphe.
COMPAREES = ("ag_recent",)
ECART_BRUTAL = (
    0.25  # perte de la part « juste » du graphe, d'une repose à l'autre, qui arrête la publication (06.11.2026)
)
SCHEMA_QUESTIONS_SEULES = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["sujet", "question"],
                "properties": {"sujet": {"type": "integer"}, "question": {"type": "string"}},
            },
        }
    },
}
SCHEMA_VERIFS = {
    "type": "object",
    "required": ["reponses"],
    "properties": {
        "reponses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "trouve"],
                "properties": {
                    "i": {"type": "integer"},
                    "trouve": {"type": "boolean"},
                    "attendu": {"type": "string"},
                    "citation": {"type": "string"},
                },
            },
        }
    },
}
RE_ADRESSE_FINALE = re.compile(r"\((https?://[^)\s]+)\)\s*$")


def publie(q: dict) -> bool:
    """Une question du chiffre publié : une question agnostique."""
    return q.get("type") in AGNOSTIQUES


def questions_de_fond(graphe: str, sujets: list[str], n: int, delai: int) -> list[dict]:
    """Des questions de fond écrites sans outil ni accès au graphe : ce qu'un praticien demanderait sur chaque sujet."""
    decl = cfg.graphe_declare(graphe)
    par = max(1, -(-n // max(1, len(sujets))))
    rep = claude(
        f"Tu es un praticien expérimenté de « {decl['libelle']} ». Pour chacun de ces sujets, écris {par} question(s) que tu "
        "poserais à une veille spécialisée pour vérifier qu'elle est solide :\n"
        + "\n".join(f"  {k}. {s}" for k, s in enumerate(sujets))
        + "\n\nDes questions de fond : comment une méthode marche, pourquoi tel choix, quel compromis, quelle différence entre "
        "deux approches, quel résultat ou chiffre de référence, ce que prévoit tel texte. Chacune se comprend seule et a une "
        "réponse précise et vérifiable dans une source publique. Pas de piège, pas d'opinion, pas d'actualité de la semaine. "
        "`sujet` : le numéro du sujet.\n"
        'Réponds en JSON : {"questions": [{"sujet": <numéro>, "question": "…"}, …]}.',
        SCHEMA_QUESTIONS_SEULES,
        None,
        delai,
    )
    return [
        {
            "question": q["question"],
            "sujet": sujets[q["sujet"]] if isinstance(q.get("sujet"), int) and 0 <= q["sujet"] < len(sujets) else "",
        }
        for q in (rep or {}).get("questions", [])
        if q.get("question")
    ]


def verifier_sur_le_web(questions: list[dict], delai: int, lot: int = 8) -> list[dict]:
    """La réponse de chaque question, trouvée sur le web et citée ; une question sans réponse établie est écartée."""
    out = []
    for k in range(0, len(questions), lot):
        part = questions[k : k + lot]
        rep = claude(
            "Pour chaque question, cherche sur le web une page qui y répond clairement, lis-la, et donne `attendu` (la réponse, "
            "courte) et `citation` (la phrase de la page qui l'établit, mot pour mot, suivie de l'adresse de la page entre "
            "parenthèses). N'importe quel site fiable convient. "
            + PAGES_LISIBLES
            + "Si aucune page ne l'établit clairement, `trouve` faux.\n\n"
            + "\n".join(f"  {k + j}. {q['question']}" for j, q in enumerate(part))
            + '\n\nRéponds en JSON : {"reponses": [{"i": <numéro>, "trouve": <bool>, "attendu": "…", "citation": "…"}, …]}.',
            SCHEMA_VERIFS,
            ["WebSearch", "WebFetch"],
            delai,
        )
        trouvees = 0
        for x in (rep or {}).get("reponses", []):
            i = x.get("i")
            if (
                isinstance(i, int)
                and k <= i < k + len(part)
                and x.get("trouve")
                and x.get("attendu")
                and RE_ADRESSE_FINALE.search(x.get("citation") or "")
            ):
                out.append({**questions[i], "attendu": x["attendu"], "citation": x["citation"]})
                trouvees += 1
        print(f"    fond : lot {k // lot + 1}, {trouvees} réponse(s) trouvée(s) sur {len(part)}", flush=True)
    return out


def questions_recentes(
    graphe: str, sujets: list[str], n: int, delai: int, exclues: set[str] | None = None
) -> list[dict]:
    """Des questions sur ce qui s'est passé après l'horizon du modèle, tirées de pages trouvées sur n'importe quel site."""
    decl, out, vues = cfg.graphe_declare(graphe), [], []
    exclues = exclues or set()
    n_pages = n + max(4, n // 4)
    for k in range(0, n_pages, 12):
        m = min(12, n_pages - k)
        tour = [sujets[(k // 12 * 4 + j) % len(sujets)] for j in range(min(4, len(sujets)))]
        deja = vues[-48:] + sorted(exclues)[:30]
        rep = claude(
            f"Tu es un praticien de « {decl['libelle']} ». Cherche sur le web {m} pages publiées après le {cfg.HORIZON_MODELE} "
            f"qui comptent pour ces sujets : {' ; '.join(tour)}. N'importe quel site. "
            + (f"Pas ces pages, déjà prises : {' ; '.join(deja)}. " if deja else "")
            + PAGES_LISIBLES
            + "Lis chaque page, puis écris pour chacune UNE question que tu poserais à ta veille : elle se comprend seule, porte "
            "sur ce qu'un praticien a besoin de savoir (pas un prix, un poste, une date de communiqué), et sa réponse est un "
            "fait précis et court établi par la page. `attendu` : la réponse ; `citation` : la phrase qui l'établit, mot pour "
            "mot, suivie de l'adresse de la page entre parenthèses. `document` : le rang de la page.\n"
            'Réponds en JSON : {"questions": [{"document": <rang>, "type": "ag_recent", "question": "…", "attendu": "…", "citation": "…"}, …]}.',
            SCHEMA_QUESTIONS,
            ["WebSearch", "WebFetch"],
            delai,
        )
        for q in (rep or {}).get("questions", [])[:m]:
            u = RE_ADRESSE_FINALE.search(q.get("citation") or "")
            if q.get("question") and q.get("attendu") and u and u.group(1).rstrip("/") not in exclues:
                out.append({"question": q["question"], "attendu": q["attendu"], "citation": q["citation"]})
                vues.append(u.group(1))
        print(f"    récentes : lot {k // 12 + 1}, {len((rep or {}).get('questions', []))} question(s)", flush=True)
    return out


# --- Lecture locale (09.10.2026) : le modèle cherche, nous lisons --------------------------------------------------
#
# Mesuré : quand le modèle lit lui-même (WebFetch), nous ne voyons ni ce qu'il reçoit ni ce qu'il paie ; le texte
# principal d'une page pèse 11 % de son HTML. Ici le modèle ne fait que chercher (WebSearch), lecture_web extrait le
# texte principal entier, un seul appel à Sonnet écrit les questions depuis ces textes, et chaque citation est vérifiée
# mot pour mot dans la page avant d'être gardée. Poste au registre : celui de l'appelant, suffixé « _local ».

MODELE_LECTURE = media.modele("lecture")
SCHEMA_PAGES = {
    "type": "object",
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}, "titre": {"type": "string"}, "pour": {"type": "integer"}},
            },
        }
    },
}


def _poste_local():
    """Le poste du registre, suffixé pour comparer les deux voies (banc_themes / banc_themes_local)."""

    class _Ctx:
        def __enter__(self):
            self.avant = CONTEXTE["poste"]
            if not str(self.avant).endswith("_local"):
                CONTEXTE["poste"] = f"{self.avant}_local"

        def __exit__(self, *a):
            CONTEXTE["poste"] = self.avant

    return _Ctx()


def chercher_pages(
    graphe: str,
    sujets: list[str],
    n: int,
    delai: int,
    exclues: set[str] | None = None,
    questions: list[str] | None = None,
) -> list[dict]:
    """Des adresses de pages, par la seule recherche web du modèle (sans lecture). Avec `questions`, une ou deux pages
    susceptibles de répondre à chacune (`pour` : indice de la question) ; sinon n pages récentes sur les sujets."""
    decl = cfg.graphe_declare(graphe)
    deja = sorted(exclues or set())[:40]
    if questions:
        demande = (
            f"Tu es un praticien de « {decl['libelle']} ». Pour chacune de ces questions, trouve par la recherche web une ou "
            "deux pages qui y répondent clairement (page d'origine, pas un agrégateur). "
            + PAGES_LISIBLES
            + "\nNe lis pas les pages : rends seulement leurs adresses. `pour` : le numéro de la question.\n\n"
            + "\n".join(f"  {i}. {q}" for i, q in enumerate(questions))
        )
    else:
        demande = (
            f"Tu es un praticien de « {decl['libelle']} ». Trouve par la recherche web {n} pages publiées après le "
            f"{cfg.HORIZON_MODELE} qui comptent pour ces sujets : {' ; '.join(sujets)}. N'importe quel site. "
            + (f"Pas ces pages, déjà prises : {' ; '.join(deja)}. " if deja else "")
            + PAGES_LISIBLES
            + "\nNe lis pas les pages : rends seulement leurs adresses et leur titre, de sources variées."
        )
    with _poste_local():
        rep = claude(
            demande + '\n\nRéponds en JSON : {"pages": [{"url": "…", "titre": "…", "pour": <numéro ou 0>}, …]}.',
            SCHEMA_PAGES,
            ["WebSearch"],
            delai,
            charges=["WebSearch"],
            modele=MODELE_LECTURE,
        )
    out, vues = [], set(exclues or set())
    for x in (rep or {}).get("pages", []):
        u = str(x.get("url") or "").strip()
        if u.startswith("http") and u.rstrip("/") not in vues:
            vues.add(u.rstrip("/"))
            out.append({"url": u, "titre": str(x.get("titre") or ""), "pour": x.get("pour")})
    return out


def questions_depuis_pages(graphe: str, sujets: list[str], pages: list[dict], delai: int) -> list[dict]:
    """Une question par page lue, écrite par Sonnet depuis le texte principal entier ; citation vérifiée dans la page."""
    from newskit import lecture_web as lw  # noqa: PLC0415

    decl = cfg.graphe_declare(graphe)
    if not pages:
        return []
    blocs = [
        f"### Page {i} — {p['titre'] or p['url']} ({p['url']}){' — ' + p['date'] if p.get('date') else ''}\n{p['texte']}"
        for i, p in enumerate(pages)
    ]
    with _poste_local():
        rep = claude(
            f"Tu es un praticien de « {decl['libelle']} ». Voici le texte de pages récentes qui comptent pour ces sujets : "
            f"{' ; '.join(sujets)}.\nPour chaque page, écris UNE question que tu poserais à ta veille : elle se comprend seule, "
            "porte sur ce qu'un praticien a besoin de savoir (pas un prix, un poste, une date de communiqué), et sa réponse "
            "est un fait précis et court établi par la page. `attendu` : la réponse ; `citation` : la phrase de la page qui "
            "l'établit, recopiée MOT POUR MOT (elle sera vérifiée dans le texte), suivie de l'adresse de la page entre "
            "parenthèses. `document` : le numéro de la page. Une page sans fait précis : pas de question.\n\n"
            + "\n\n".join(blocs)
            + '\n\nRéponds en JSON : {"questions": [{"document": <numéro>, "type": "ag_recent", "question": "…", "attendu": "…", "citation": "…"}, …]}.',
            SCHEMA_QUESTIONS,
            None,
            delai,
            modele=MODELE_LECTURE,
        )
    out, rejets = [], 0
    for q in (rep or {}).get("questions", []):
        d = q.get("document")
        if not (isinstance(d, int) and 0 <= d < len(pages)) or not q.get("question") or not q.get("attendu"):
            continue
        cit = str(q.get("citation") or "")
        if not RE_ADRESSE_FINALE.search(cit):
            cit = cit.rstrip() + f" ({pages[d]['url']})"
        if not lw.citation_dans(cit, pages[d]):  # texte et notes de bas de page
            rejets += 1
            continue
        out.append({"question": q["question"], "attendu": q["attendu"], "citation": cit})
    print(
        f"    lecture locale : {len(pages)} page(s) lue(s), {len(out)} question(s) gardée(s), {rejets} citation(s) introuvable(s) dans la page",
        flush=True,
    )
    return out


def questions_recentes_locales(
    graphe: str, sujets: list[str], n: int, delai: int, exclues: set[str] | None = None
) -> list[dict]:
    from newskit import lecture_web as lw  # noqa: PLC0415

    adresses = chercher_pages(graphe, sujets, n + max(4, n // 4), delai, exclues)
    pages = lw.lire_plusieurs([a["url"] for a in adresses])
    for p in pages:
        p["titre"] = p["titre"] or next((a["titre"] for a in adresses if a["url"] == p["url"]), "")
    print(
        f"    recherche : {len(adresses)} adresse(s), {len(pages)} page(s) lisible(s), "
        f"{sum(p['mots'] for p in pages)} mots de texte principal",
        flush=True,
    )
    return questions_depuis_pages(graphe, sujets, pages, delai)[:n]


def verifier_localement(graphe: str, questions: list[dict], delai: int, lot: int = 8) -> list[dict]:
    """La réponse de chaque question de fond, trouvée dans une page que nous lisons ; citation vérifiée dans la page."""
    from newskit import lecture_web as lw  # noqa: PLC0415

    decl = cfg.graphe_declare(graphe)
    out = []
    for k in range(0, len(questions), lot):
        part = questions[k : k + lot]
        adresses = chercher_pages(graphe, [], 0, delai, None, questions=[q["question"] for q in part])
        pages = lw.lire_plusieurs([a["url"] for a in adresses])
        pour = {a["url"]: a.get("pour") for a in adresses}
        if not pages:
            continue
        blocs = [
            f"### Page {i} ({p['url']}) — pour la question {pour.get(p['url'])}\n{p['texte']}"
            for i, p in enumerate(pages)
        ]
        with _poste_local():
            rep = claude(
                f"Domaine : « {decl['libelle']} ». Pour chaque question, dis si l'une des pages ci-dessous y répond clairement ; "
                "alors `attendu` (la réponse, courte) et `citation` (la phrase de la page qui l'établit, recopiée MOT POUR MOT, "
                "suivie de l'adresse de la page entre parenthèses). Sinon `trouve` faux. N'emploie que ces pages.\n\n"
                + "\n".join(f"  {k + j}. {q['question']}" for j, q in enumerate(part))
                + "\n\n"
                + "\n\n".join(blocs)
                + '\n\nRéponds en JSON : {"reponses": [{"i": <numéro>, "trouve": <bool>, "attendu": "…", "citation": "…"}, …]}.',
                SCHEMA_VERIFS,
                None,
                delai,
                modele=MODELE_LECTURE,
            )
        trouvees = 0
        for x in (rep or {}).get("reponses", []):
            i = x.get("i")
            if not (isinstance(i, int) and k <= i < k + len(part) and x.get("trouve") and x.get("attendu")):
                continue
            cit = str(x.get("citation") or "")
            m = RE_ADRESSE_FINALE.search(cit)
            page = next((p for p in pages if m and p["url"].rstrip("/") == m.group(1).rstrip("/")), None)
            if page is None or not lw.citation_dans(cit, page):  # texte et notes
                continue
            out.append({**questions[i], "attendu": x["attendu"], "citation": cit})
            trouvees += 1
        print(
            f"    fond (lecture locale) : lot {k // lot + 1}, {trouvees} réponse(s) vérifiée(s) sur {len(part)}",
            flush=True,
        )
    return out


def lecture_locale(local: bool | None) -> bool:
    """La voie de lecture des pages : celle demandée, sinon celle du registre (`banc.lecture_locale`, vrai par défaut)."""
    return bool(cfg.banc().get("lecture_locale", True)) if local is None else bool(local)


def composer_agnostique(
    graphe: str, n: int, delai: int, exclues: set[str] | None = None, local: bool | None = None
) -> list[dict]:
    """n questions agnostiques : moitié récentes, moitié de fond, sur tous les sujets déclarés, triées comme le banc.
    `local` : le modèle cherche, nous lisons (lecture_web) ; sinon il cherche et lit lui-même ; None : le registre."""
    local = lecture_locale(local)
    sujets = te.sujets_declares(graphe) or [cfg.graphe_declare(graphe)["libelle"]]
    n_r, n_f = n - n // 2, n // 2
    f_rec, f_ver = (
        (questions_recentes_locales, lambda qs, d: verifier_localement(graphe, qs, d))
        if local
        else (questions_recentes, verifier_sur_le_web)
    )
    recentes = [
        {**q, "type": "ag_recent", "origine": "question agnostique, page récente", "source": ""}
        for q in f_rec(graphe, sujets, n_r, delai, exclues)
    ]
    fond = [
        {**q, "type": "ag_fond", "origine": "question agnostique, de fond", "source": ""}
        for q in f_ver(questions_de_fond(graphe, sujets, n_f + max(4, n_f // 3), delai), delai)
    ]
    if exclues:
        fond = [q for q in fond if not (adresses_de([q]) & exclues)]
    gardees = hors_sujet_retirees(graphe, recentes + fond)
    return [q for q in gardees if q["type"] == "ag_recent"][:n_r] + [q for q in gardees if q["type"] == "ag_fond"][:n_f]


def composer_jeu(graphe: str, bornes: dict) -> list[dict]:
    """Le jeu du banc : agnostique (registre : banc.agnostiques) avec ses questions de restitution, ou l'ancien jeu."""
    if not bornes.get("agnostiques"):
        return composer(graphe, bornes)
    restitution = composer(graphe, {**bornes, "questions": bornes.get("restitution", 40), "externes": 0})
    questions = restitution + composer_agnostique(graphe, bornes["agnostiques"], bornes["delai"])
    for i, q in enumerate(questions):
        q["i"] = i
    return questions


def composer(graphe: str, bornes: dict) -> list[dict]:
    """Tire un jeu de questions neuf : fiches récentes, fiches de fond, liens du graphe, pages extérieures, lacunes."""
    random.seed(time.strftime("%Y-%m-%d"))
    decl = cfg.graphe_declare(graphe)
    sujets_g = te.sujets_declares(graphe)
    rediger_g = lambda docs, consigne: rediger(decl, docs, consigne, sujets=sujets_g)  # noqa: E731
    toutes = fiches_extraites(graphe)
    recentes = [f for f in toutes if f.name[:10] > cfg.HORIZON_MODELE and "/arxiv" not in str(f)]
    anciennes = [f for f in toutes if f.name[:10] <= cfg.HORIZON_MODELE]
    n = bornes["questions"]
    n_ext = bornes["externes"]
    questions: list[dict] = []

    def ajouter(liste: list[dict], fichiers: list[Path], origine: str) -> None:
        for q in liste:
            d = q.get("document")
            f = fichiers[d] if isinstance(d, int) and 0 <= d < len(fichiers) else None
            questions.append(
                {
                    "type": type_de(q, f),
                    "question": q["question"],
                    "attendu": q["attendu"],
                    "citation": q.get("citation", ""),
                    "origine": origine,
                    "source": str(f.relative_to(RACINE)) if f else "",
                }
            )

    # Les parts du jeu, hors pages extérieures : récent et précis 60 %, fond 25 %, relationnel 15 %. Le rédacteur
    # écarte désormais les documents anecdotiques ou hors sujet, et le tri en retire encore : on tire donc des
    # lots jusqu'à dépasser chaque part d'un quart, puis on coupe (04.10.2026 : 32 questions obtenues pour 50).
    # Le fonctionnement (comment une méthode marche, pourquoi ce choix, quel compromis) a sa part propre depuis
    # le 04.10.2026 : le porteur constatait un banc fait presque entièrement de questions sur des produits.
    interne = n - n_ext
    part_f = bornes.get("fonctionnement", 30) / 100
    parts_cibles = {
        "fonct": round(interne * part_f),
        "rp": round(interne * (1 - part_f) * 0.6),
        "fond": round(interne * (1 - part_f) * 0.25),
    }
    parts_cibles["rel"] = interne - parts_cibles["fonct"] - parts_cibles["rp"] - parts_cibles["fond"]

    def compte(*types: str) -> int:
        return sum(q["type"] in types for q in questions if q["origine"] == "fiche du graphe")

    # Récent et précis : deux questions par fiche récente, lot par lot.
    reserve = random.sample(recentes, len(recentes))
    for _ in range(16):
        if compte("recent", "precis") >= parts_cibles["rp"] * 1.25 or not reserve:
            break
        lot, reserve = reserve[:4], reserve[4:]
        ajouter(
            rediger_g(
                [("fiche récente", extrait(f)) for f in lot],
                "Pour CHAQUE document, deux questions : une de type « recent » (ce qui est annoncé, décidé ou "
                "publié) et une de type « precis » (une version, un chiffre avec son unité, une date, ou qui affirme quoi).",
            ),
            lot,
            "fiche du graphe",
        )
    # Fonctionnement : les fiches qui portent le plus de principes et de mécanismes (nœuds « rationale »),
    # quelle que soit leur date — billets techniques, papiers jalons, notes de version qui expliquent.
    G0 = te.charger(graphe)
    poids: dict[str, int] = {}
    if G0 is not None:
        for nd, d in G0.nodes(data=True):
            if dg._type(G0, nd) == "rationale":
                for f in {str(e.get("source_file") or "") for _, _, e in G0.edges(nd, data=True)} | {
                    str(d.get("source_file") or "")
                }:
                    if f:
                        poids[f] = poids.get(f, 0) + 1
    connues = {str(f.relative_to(RACINE)): f for f in toutes}
    techniques = [connues[f] for f, k in sorted(poids.items(), key=lambda kv: -kv[1]) if f in connues and k >= 1]
    reserve = random.sample(techniques[:200], min(200, len(techniques)))
    for _ in range(12):
        if compte("fonctionnement") >= parts_cibles["fonct"] * 1.25 or not reserve:
            break
        lot, reserve = reserve[:5], reserve[5:]
        ajouter(
            rediger_g(
                [("fiche technique", extrait(f)) for f in lot],
                "Pour chaque document, une question de type « fonctionnement » : comment une méthode, un algorithme, "
                "un mécanisme ou une architecture fonctionne, pourquoi ce choix plutôt qu'un autre, ou quel compromis il "
                "fait. La réponse attendue est un principe ou un mécanisme, en une ligne — pas un nom de produit, pas un "
                "chiffre, pas une date. Si le document n'explique aucun fonctionnement, aucune question.",
            ),
            lot,
            "fiche du graphe",
        )
    # Fond : une question par fiche antérieure à l'horizon, lot par lot.
    reserve = random.sample(anciennes, len(anciennes))
    for _ in range(8):
        if compte("fond") >= parts_cibles["fond"] * 1.25 or not reserve:
            break
        lot, reserve = reserve[:6], reserve[6:]
        ajouter(
            rediger_g(
                [("fiche de fond", extrait(f)) for f in lot],
                "Pour chaque document, une question de type « fond » : ce qu'est la notion, la méthode ou "
                "l'outil qu'il présente, ou ce qui le distingue.",
            ),
            lot,
            "fiche du graphe",
        )
    # Relationnel : des liens explicites entre deux notions, avec la fiche qui les porte.
    G = te.charger(graphe)
    liens = (
        [
            (G.nodes[u].get("label"), G.nodes[v].get("label"), d.get("source_file"))
            for u, v, d in G.edges(data=True)
            if d.get("confidence") == "EXTRACTED"
            and d.get("relation") in ("implements", "rationale_for", "shares_data_with")
            and dg._type(G, u) in dg.TYPES_CONCEPT
            and dg._type(G, v) in dg.TYPES_CONCEPT
            and d.get("source_file")
        ]
        if G is not None
        else []
    )
    reserve_l = [l for l in random.sample(liens, min(60, len(liens))) if (RACINE / str(l[2])).exists()]
    for _ in range(4):
        if compte("relationnel") >= parts_cibles["rel"] * 1.25 or not reserve_l:
            break
        tirage_l, reserve_l = reserve_l[:4], reserve_l[4:]
        ajouter(
            rediger_g(
                [(f"lien entre « {a} » et « {b} »", extrait(RACINE / str(s))) for a, b, s in tirage_l],
                "Pour chaque document, une question de type « relationnel » sur le lien entre les deux notions "
                "nommées en tête du document : comment l'une se rattache à l'autre, d'après le texte. La question "
                "nomme les DEUX notions.",
            ),
            [RACINE / str(s) for _, _, s in tirage_l],
            "fiche du graphe",
        )
        # Un relationnel n'en est un que si sa question nomme bien les deux notions du lien.
        for q in questions[-len(tirage_l) * 2 :]:
            if (
                q["origine"] == "fiche du graphe"
                and q["type"] in ("precis", "fond")
                and any(
                    a.casefold() in q["question"].casefold() and b.casefold() in q["question"].casefold()
                    for a, b, _ in tirage_l
                )
            ):
                q["type"] = "relationnel"
    # Extérieur : des pages que le graphe n'a pas. L'épreuve qu'il peut perdre.
    if n_ext:
        questions += tirer_exterieures(graphe, n_ext, bornes)
    questions = hors_sujet_retirees(graphe, questions)
    # Couper chaque part à sa cible, puis compléter avec ce qui reste si une part manque.
    cibles = {
        ("fonctionnement",): parts_cibles["fonct"],
        ("recent", "precis"): parts_cibles["rp"],
        ("fond",): parts_cibles["fond"],
        ("relationnel",): parts_cibles["rel"],
        ("exterieur",): n_ext,
    }
    gardees, reste = [], []
    for types, cible in cibles.items():
        de_ce_type = [q for q in questions if q["type"] in types]
        if types == ("recent", "precis"):  # alterner pour garder les deux à parts égales
            r_, p_ = [q for q in de_ce_type if q["type"] == "recent"], [q for q in de_ce_type if q["type"] == "precis"]
            de_ce_type = [q for paire in zip(r_, p_) for q in paire] + r_[len(p_) :] + p_[len(r_) :]
        gardees += de_ce_type[:cible]
        reste += de_ce_type[cible:]
    gardees += reste[: max(n - len(gardees), 0)]
    return [{**q, "i": i} for i, q in enumerate(gardees[:n])]


MOTIFS_REJET = ("hors_sujet", "anecdote", "ambigue", "doublon")
SCHEMA_TRI = {
    "type": "object",
    "required": ["rejets"],
    "properties": {
        "rejets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "motif"],
                "properties": {"i": {"type": "integer"}, "motif": {"type": "string", "enum": list(MOTIFS_REJET)}},
            },
        }
    },
}


def hors_sujet_retirees(graphe: str, questions: list[dict]) -> list[dict]:
    """Retire les questions hors sujet, anecdotiques, ambiguës ou en double. Un appel pour tout le jeu.

    Le premier banc d'un graphe d'image (04.10.2026) interrogeait sur un héros d'Overwatch et un jackpot de casino,
    tirés de fiches de 80 Level collectées avant leur filtre : le graphe était jugé sur ce qu'il ne prétend
    pas couvrir. Sans réponse du LLM, rien n'est retiré."""
    sujets = SUJETS_DOMAINE.get(graphe) or te.sujets_declares(graphe)
    if not sujets or not questions:
        return questions
    rep = claude(
        f"Domaine d'une veille : « {cfg.graphe_declare(graphe)['libelle']} ». Ses sujets :\n"
        + "\n".join(f"  - {x}" for x in sujets)
        + "\n\nQuestions d'un banc d'essai :\n"
        + "\n".join(f"  {i}. {q['question']}  → {q['attendu']}" for i, q in enumerate(questions))
        + "\n\nRelis-les pour la cohérence du banc, et rejette :\n"
        "- `hors_sujet` : ne relève d'aucun de ces sujets ;\n"
        "- `anecdote` : un praticien du domaine ne la poserait pas à une veille (prix d'un greffon, date d'un "
        "communiqué, poste d'une personne, détail de jeu, de concours, de promotion, de vote) ;\n"
        "- `ambigue` : ne se comprend pas seule, ou admet plusieurs réponses justes ;\n"
        "- `doublon` : redit, ou contredit, une question gardée plus haut.\n"
        'Ne rejette que ce qui le mérite. Réponds en JSON : {"rejets": [{"i": <indice>, "motif": "…"}, …]}.',
        SCHEMA_TRI,
    )
    if not rep:
        return questions
    rejets = {
        x["i"]: x["motif"]
        for x in rep.get("rejets", [])
        if isinstance(x.get("i"), int) and x.get("motif") in MOTIFS_REJET
    }
    retirees = set(rejets)
    if retirees:
        par = {m: sum(v == m for v in rejets.values()) for m in MOTIFS_REJET if m in rejets.values()}
        print(f"  {len(retirees)} question(s) retirée(s) — " + ", ".join(f"{m} {n}" for m, n in par.items()))
        for k in sorted(retirees):
            if k < len(questions):
                print(f"    [{rejets[k]}] {questions[k]['question'][:110]}")
    return [q for i, q in enumerate(questions) if i not in retirees]


# --- Les trois conditions ----------------------------------------------------------------------


def consigne_de_reponse(questions: list[dict]) -> str:
    return (
        "Questions :\n"
        + "\n".join(f"  {q['i']}. {q['question']}" for q in questions)
        + "\n\nPour chacune : `reponse` en une ou deux lignes, `sait` vrai seulement si tu en es sûr, `source` d'où tu le "
        "tiens. Si tu ne sais pas, `sait` faux et `reponse` vide : ne devine pas.\n"
        'Réponds en JSON : {"reponses": [{"i": <indice>, "reponse": "…", "sait": <bool>, "source": "…"}, …]}.'
    )


def repondre(graphe: str, condition: str, questions: list[dict], bornes: dict) -> dict[int, dict]:
    decl = cfg.graphe_declare(graphe)
    out: dict[int, dict] = {}
    compte = CONSO[condition] = {}
    if condition == "graphe" and a_une_pyramide(graphe):
        return repondre_par_syntheses(graphe, questions, bornes, compte)
    if condition == "fiches":
        condition = "graphe"  # l'ancien chemin, par les fiches
    if condition == "modele":
        lots = [questions]
    else:
        lots = [questions[i : i + bornes["par_lot"]] for i in range(0, len(questions), bornes["par_lot"])]
    for lot in lots:
        if condition == "modele":
            rep = claude(
                f"Réponds de mémoire, sans outil, à ces questions sur « {decl['libelle']} ».\n\n"
                + consigne_de_reponse(lot),
                SCHEMA_REPONSES,
                None,
                bornes["delai"],
                compte,
            )
        elif condition == "web":
            rep = claude(
                f"Réponds à ces questions sur « {decl['libelle']} » en cherchant sur le web.\n\n"
                + consigne_de_reponse(lot),
                SCHEMA_REPONSES,
                ["WebSearch", "WebFetch"],
                bornes["delai"],
                compte,
            )
        else:
            outil = f"{OUTIL_GRAPHE} --graphe {graphe}"
            carte = cfg.chemins_du_graphe(graphe)["carte"]
            rep = claude(
                f"Réponds à ces questions sur « {decl['libelle']} » en n'employant QUE le graphe de connaissances local : sa "
                "carte ci-dessous, et son outil d'interrogation, par des commandes shell. Ta mémoire ne compte pas : si le "
                "graphe ne le dit pas, tu ne sais pas.\n\n"
                f'Commandes (enchaîne-les librement) :\n  {outil} chercher "<termes>"\n  {outil} expliquer "<notion>"\n'
                f'  {outil} voisinage "<notion>"\n  {outil} sources "<notion>"\n  {outil} fiche "<fragment du nom de fiche>"\n'
                f'  {outil} passages "<mots précis>"   — le texte des fiches : versions, chiffres, dates, qui a dit quoi\n\n'
                f"Carte du graphe :\n{carte.read_text(encoding='utf-8')[:9000] if carte.exists() else '(absente)'}\n\n"
                + consigne_de_reponse(lot),
                SCHEMA_REPONSES,
                [f"Bash({OUTIL_GRAPHE}:*)"],
                bornes["delai"],
                compte,
            )
        for x in (rep or {}).get("reponses", []):
            if isinstance(x.get("i"), int):
                out[x["i"]] = {
                    "reponse": str(x.get("reponse") or "")[:600],
                    "sait": bool(x.get("sait")),
                    "source": str(x.get("source") or "")[:200],
                }
    return out


def a_une_pyramide(graphe: str) -> bool:
    return (cfg.chemins_du_graphe(graphe)["base"] / "synthese" / "notions.json").exists()


def modele_pyramide(graphe: str) -> str:
    """Le modèle qui lit la pyramide : KG_MODELE_SYNTHESE s'il est posé, sinon celui du registre pour ce graphe
    (`modele_pyramide`, Opus pour un graphe d'ingénierie depuis le 06.10.2026 : Sonnet y faisait des réponses fausses),
    sinon Sonnet."""
    return (
        os.environ.get("KG_MODELE_SYNTHESE")
        or cfg.graphe_declare(graphe).get("modele_pyramide")
        or media.modele("redaction")
    )


def methode_du_graphe(graphe: str) -> str:
    return f"synthèses ({modele_pyramide(graphe)})" if a_une_pyramide(graphe) else f"fiches ({te.MODELE})"


def repondre_par_syntheses(graphe: str, questions: list[dict], bornes: dict, compte: dict) -> dict[int, dict]:
    """La condition « synthese » : la pyramide de synthèses d'abord (une sortie compacte : thème, notions, meilleurs
    passages), les fiches ensuite si le détail manque ; Sonnet, seul l'outil Bash chargé, consigne système courte."""
    decl = cfg.graphe_declare(graphe)
    S, I = OUTIL_SYNTHESE, f"{OUTIL_GRAPHE} --graphe {graphe}"
    out: dict[int, dict] = {}
    for k in range(0, len(questions), LOT_SYNTHESE):
        lot = questions[k : k + LOT_SYNTHESE]
        rep = claude(
            f"Réponds à ces questions sur « {decl['libelle']} » en n'employant QUE le graphe de connaissances local. Ta "
            "mémoire ne compte pas : si le graphe ne le dit pas, tu ne sais pas.\n\n"
            "Commence par les synthèses, qui répondent à la plupart des questions (et joignent les meilleurs extraits de fiches) :\n"
            f'  {S} repondre --graphe {graphe} "<la question>"   — thème, notions et extraits, avec leurs renvois R<n>\n'
            f"  {S} descendre --graphe {graphe} R<n>              — le paragraphe de fiche cité par un renvoi\n"
            "Si la réponse précise (chiffre, version, date, nom, citation) n'y est pas, cherche dans le texte des fiches AVANT de "
            "conclure que tu ne sais pas — les fiches sont surtout en anglais : mots-clés anglais, noms exacts :\n"
            f'  {I} passages "<mots précis>"\n\n'
            "Une commande par appel d'outil, qui commence exactement par l'outil ; plusieurs requêtes peuvent s'enchaîner avec &&.\n\n"
            # Deux réponses fausses au banc d'un graphe d'ingénierie (06.10.2026) : un extrait sur une autre version d'un modèle
            # pris pour la bonne (« Bonsai » pour « Bonsai 2 27B »), et un terme exact demandé que la synthèse ne
            # reprenait pas (« System One model »), sans descente à la fiche.
            "Deux règles avant de répondre :\n"
            "- Le nom et la version exacts doivent correspondre : un chiffre trouvé pour « X » ne vaut pas pour « X 2 » ou "
            "« X 27B ». Si la source ne nomme pas exactement ce qui est demandé, réponds que tu ne sais pas.\n"
            "- Quand la question demande un terme, un nom, une catégorie ou un chiffre exacts (« comment l'appelle-t-il », "
            "« sous quelle catégorie », « combien »), lis le paragraphe cité (`descendre`) ou cherche-le (`passages`) et "
            "reprends les mots de la source ; une synthèse résume, elle ne cite pas toujours le terme.\n\n"
            + consigne_de_reponse(lot),
            SCHEMA_REPONSES,
            [f"Bash({OUTIL_SYNTHESE}:*)", f"Bash({OUTIL_GRAPHE}:*)"],
            bornes["delai"],
            compte,
            charges=["Bash"],
            modele=modele_pyramide(graphe),
            systeme=SYSTEME_COURT,
        )
        for x in (rep or {}).get("reponses", []):
            if isinstance(x.get("i"), int):
                out[x["i"]] = {
                    "reponse": str(x.get("reponse") or "")[:600],
                    "sait": bool(x.get("sait")),
                    "source": str(x.get("source") or "")[:200],
                }
        print(
            f"    synthese : lot {k // LOT_SYNTHESE + 1}, {len((rep or {}).get('reponses', []))} réponse(s)", flush=True
        )
    return out


def juger(questions: list[dict], reponses: dict[int, dict], delai: int) -> dict[int, str]:
    """juste / faux / ne_sait_pas, contre la réponse attendue et la citation. Une question sans réponse : ne sait pas."""
    # Toute réponse non vide est jugée, que le répondant se soit dit sûr ou non : au premier banc (04.10.2026),
    # le LLM avec recherche web donnait 24 réponses, souvent justes, en se déclarant incertain — toutes comptées
    # « ne sait pas ». Une réponse donnée est une réponse : juste ou fausse.
    a_juger = [q for q in questions if reponses.get(q["i"], {}).get("reponse", "").strip()]
    verdicts = {q["i"]: "ne_sait_pas" for q in questions}
    if not a_juger:
        return verdicts
    rep = claude(
        "Tu juges des réponses à un banc d'essai. Pour chaque question, compare la réponse donnée à la réponse attendue, "
        "en t'appuyant sur la citation de la source et non sur ta mémoire. `juste` : elle dit le même fait (les mots peuvent "
        "différer, un détail en plus ne nuit pas). `faux` : elle affirme autre chose, ou un fait que la citation contredit. "
        "`ne_sait_pas` : elle n'affirme rien d'utile.\n\n"
        + "\n\n".join(
            f"{q['i']}. Question : {q['question']}\n   Attendu : {q['attendu']}\n   Citation : {q['citation'][:400]}\n"
            f"   Réponse donnée : {reponses[q['i']]['reponse']}"
            for q in a_juger
        )
        + '\n\nRéponds en JSON : {"verdicts": [{"i": <indice>, "verdict": "juste" | "faux" | "ne_sait_pas"}, …]}.',
        SCHEMA_VERDICTS,
        None,
        delai,
    )
    for x in (rep or {}).get("verdicts", []):
        if isinstance(x.get("i"), int) and x.get("verdict") in VERDICTS and x["i"] in verdicts:
            verdicts[x["i"]] = x["verdict"]
    return verdicts


def rejuger(graphe: str, redemander: tuple[str, ...] = ()) -> dict | None:
    """Rejuge les réponses gardées du dernier banc, sans les redemander (après un changement du juge).
    `redemander` : les conditions à reposer d'abord — « graphe » après une recomposition du graphe."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc.json"
    r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if not r:
        return None
    details, delai = r["details"], cfg.banc()["delai"]
    agnostique = any(publie(d) for d in details)
    for c in redemander:
        neuves = repondre(graphe, c, [d for d in details if c == "graphe" or publie(d) or not agnostique], cfg.banc())
        if c == "graphe":
            r["methode_graphe"] = methode_du_graphe(graphe)
        r.setdefault("couts", {})[c] = par_question(CONSO.get(c, {}), len(details))
        for d in details:
            if c == "graphe" or publie(d) or not agnostique:
                d.setdefault(c, {})["reponse"] = neuves.get(d["i"], {}).get("reponse", "")
    for c in CONDITIONS + tuple(c for c in CONDITIONS_EN_PLUS if c in details[0]):
        avec = [d for d in details if c in d]
        reponses = {d["i"]: {"reponse": d[c]["reponse"]} for d in avec}
        verdicts = juger(avec, reponses, delai)
        for d in avec:
            d[c]["verdict"] = verdicts[d["i"]]
        r["conditions"][c] = parts([verdicts[d["i"]] for d in avec if publie(d) or not agnostique])
        for t in dict.fromkeys(d["type"] for d in avec):
            r["par_type"].setdefault(t, {})[c] = parts([verdicts[d["i"]] for d in avec if d["type"] == t])
    if agnostique:
        r["restitution"] = parts([d["graphe"]["verdict"] for d in details if not publie(d) and "graphe" in d])
    r["rejuge_le"] = time.strftime("%Y-%m-%d")
    p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    archiver(graphe, r, "reposé : " + ", ".join(redemander) if redemander else "rejugé")
    return r


def etat_du_graphe(graphe: str) -> str:
    """L'horodatage de la dernière modification de ce que le graphe répond : sa composition (graphe.json), sa pyramide
    (synthese/notions.json) et ses cartes (synthese/cartes.json). Sert à ne pas reposer un banc à un graphe qui n'a pas
    bougé (règle « pas de requête redondante »)."""
    ch = cfg.chemins_du_graphe(graphe)
    fichiers = [ch["graphe"], ch["base"] / "synthese" / "notions.json", ch["base"] / "synthese" / "cartes.json"]
    mt = max((f.stat().st_mtime for f in fichiers if f.exists()), default=0)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else ""


def reposer_recentes(graphe: str) -> dict | None:
    """Repose au graphe seul les questions récentes du banc agnostique (COMPAREES), rejuge ces seules réponses et
    recompte : le chiffre publié « Graphe » suit la composition de la nuit (porteur, 10.10.2026 : « la repose devrait
    faire partie de la routine, après la mise à jour du graphe et des cartes »). « LLM seul » et « LLM + web » sont
    gardées : elles mesurent le modèle, pas le graphe, et attendent le renouvellement (`renouveler_jours`). Rien n'est
    reposé si le graphe, sa pyramide et ses cartes n'ont pas changé depuis la dernière pose (`graphe_repose_etat`) ;
    rien n'est écrit si la limite d'usage tombe. Premier précédent à la main : 08.10.2026 (commit 07eecde4)."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc.json"
    r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if not r:
        print(f"  {graphe} : pas de banc, rien à reposer")
        return None
    recentes = [d for d in r["details"] if d["type"] in COMPAREES]
    if not recentes or r.get("methode") != "agnostique":
        print(f"  {graphe} : banc de l'ancienne méthode, rien à reposer (attend son banc agnostique)")
        return r
    etat = etat_du_graphe(graphe)
    if etat and etat <= (r.get("graphe_repose_etat") or ""):
        print(
            f"  {graphe} : graphe, pyramide et cartes inchangés depuis la pose du {r.get('graphe_repose_le')}, rien à reposer"
        )
        return r
    avant = dict(r["conditions"].get("graphe") or {})
    neuves = {int(k): v for k, v in repondre(graphe, "graphe", recentes, cfg.banc()).items()}
    if interrompu(graphe, r, r["details"], "la repose des questions récentes"):
        return None
    if len(neuves) < 0.8 * len(recentes):
        print(
            f"  {graphe} : {len(recentes) - len(neuves)} question(s) sans réponse rendue — banc non touché",
            file=sys.stderr,
        )
        return None
    for d in recentes:
        x = neuves.get(int(d["i"]), {})
        d["graphe"] = {"reponse": x.get("reponse", ""), "sait": x.get("sait"), "source": x.get("source", "")}
    verdicts = {
        int(k): v
        for k, v in juger(
            recentes, {int(d["i"]): {"reponse": d["graphe"]["reponse"]} for d in recentes}, cfg.banc()["delai"]
        ).items()
    }
    for d in recentes:
        d["graphe"]["verdict"] = verdicts.get(int(d["i"]), "ne_sait_pas")
    r["methode_graphe"] = methode_du_graphe(graphe)
    r.setdefault("couts", {})["graphe"] = par_question(CONSO.get("graphe", {}), len(recentes))
    r["graphe_repose_le"], r["graphe_repose_etat"] = time.strftime("%Y-%m-%d"), etat
    r = recompter(r)
    perte = (avant.get("juste") or 0) - (r["conditions"]["graphe"].get("juste") or 0)
    if avant.get("juste") is not None and perte >= ECART_BRUTAL:
        # Un écart brutal arrête le banc (CLAUDE.md, Bancs) : la mesure est gardée à part, rien n'est publié, l'état de
        # pose reste l'ancien et la nuit suivante repose (une perte se repose avant d'être crue).
        (p.parent / "bancs").mkdir(parents=True, exist_ok=True)
        (p.parent / "bancs" / f"{time.strftime('%Y-%m-%dT%H-%M')}_ecart_brutal.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"  {graphe} : écart brutal au banc, part juste {round(avant['juste'] * 100)} → "
            f"{round((r['conditions']['graphe'].get('juste') or 0) * 100)} % : non publié, reposé au prochain passage",
            file=sys.stderr,
        )
        return None
    p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    archiver(graphe, r, "questions récentes reposées : graphe, après la composition de la nuit")
    a, b = avant, r["conditions"]["graphe"]
    pc = lambda x, k: round((x.get(k) or 0) * 100)  # noqa: E731
    print(
        f"  {graphe} : questions récentes reposées au graphe ({len(recentes)}) — "
        f"{pc(a, 'juste')} · {pc(a, 'faux')} · {pc(a, 'ne_sait_pas')} → {pc(b, 'juste')} · {pc(b, 'faux')} · {pc(b, 'ne_sait_pas')} "
        f"(juste · faux · ne sait pas), {r['couts']['graphe'].get('cout_usd', 0):.2f} $"
    )
    return r


def completer(graphe: str, existantes: list[dict], n: int) -> list[dict]:
    """Complète un jeu à n questions sans toucher aux questions gardées : de nouvelles sont tirées, puis un tri
    ne juge QUE les nouvelles, contre les gardées (04.10.2026 : un tri sur le jeu entier rejetait des questions
    gardées comme « doublons » des nouvelles)."""
    manque = n - len(existantes)
    if manque <= 0:
        return existantes[:n]
    neuves = composer(graphe, {**cfg.banc(), "questions": manque * 2 + 6, "externes": 0})
    vues = {q["question"] for q in existantes}
    neuves = [q for q in neuves if q["question"] not in vues]
    rep = claude(
        "Voici les questions d'un banc d'essai, déjà retenues :\n"
        + "\n".join(f"  - {q['question']}" for q in existantes)
        + "\n\nEt des questions candidates :\n"
        + "\n".join(f"  {i}. {q['question']}" for i, q in enumerate(neuves))
        + "\n\nDonne les indices des candidates à rejeter parce qu'elles redisent une question retenue, ou sont des "
        "anecdotes qu'un praticien ne poserait pas. Ne juge pas les questions retenues. "
        'Réponds en JSON : {"rejets": [{"i": <indice>, "motif": "doublon" | "anecdote"}, …]}.',
        SCHEMA_TRI,
    )
    rejets = {x["i"] for x in (rep or {}).get("rejets", []) if isinstance(x.get("i"), int)}
    ordre = {"relationnel": 0, "fonctionnement": 1, "fond": 2}
    ajout = sorted([q for i, q in enumerate(neuves) if i not in rejets], key=lambda q: ordre.get(q["type"], 3))[:manque]
    return [{**q, "i": i} for i, q in enumerate(existantes + ajout)]


def trier(graphe: str) -> dict | None:
    """Retire d'un banc déjà mesuré les questions hors sujet, et recompte sans rien redemander ni rejuger."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc.json"
    r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if not r:
        return None
    gardees = hors_sujet_retirees(graphe, r["details"])
    r["retirees_hors_sujet"] = len(r["details"]) - len(gardees)
    r["details"], r["questions"], r["par_type"] = gardees, len(gardees), {}
    for c in CONDITIONS:
        r["conditions"][c] = parts([d[c]["verdict"] for d in gardees])
        for t in dict.fromkeys(d["type"] for d in gardees):
            r["par_type"].setdefault(t, {})[c] = parts([d[c]["verdict"] for d in gardees if d["type"] == t])
    p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (p.parent / "banc_questions.json").write_text(
        json.dumps(
            [{k: v for k, v in d.items() if k not in CONDITIONS} for d in gardees], ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )
    archiver(graphe, r, "trié")
    return r


def archiver(graphe: str, r: dict, evenement: str) -> None:
    """Garde chaque résultat, pour la vue dans le temps (porteur, 04.10.2026) : une ligne de synthèse dans
    graphe/<nom>/banc_historique.jsonl, et le banc entier (questions, réponses, verdicts) dans
    graphe/<nom>/bancs/<horodatage>.json. Rien n'est jamais réécrit : on ajoute."""
    base = cfg.chemins_du_graphe(graphe)["base"]
    horo = time.strftime("%Y-%m-%dT%H-%M")
    (base / "bancs").mkdir(parents=True, exist_ok=True)
    (base / "bancs" / f"{horo}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    ligne = {
        "horodatage": horo,
        "evenement": evenement,
        "modele": r.get("modele"),
        "horizon": cfg.HORIZON_MODELE,
        "questions": r.get("questions"),
        "jeu": jeu_de(r),
        "conditions": {c: {k: v for k, v in (r["conditions"].get(c) or {}).items()} for c in CONDITIONS},
        "par_type": r.get("par_type"),
        "detail": f"bancs/{horo}.json",
    }
    with open(base / "banc_historique.jsonl", "a", encoding="utf-8") as h:
        h.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def jeu_de(r: dict) -> str:
    """L'empreinte du jeu de questions : deux mesures ne se comparent pas de la même façon sur deux jeux différents."""
    import hashlib  # noqa: PLC0415

    return hashlib.sha256("\n".join(d["question"] for d in r.get("details", [])).encode("utf-8")).hexdigest()[:10]


def parts(verdicts: list[str]) -> dict:
    n = len(verdicts)
    return {v: round(verdicts.count(v) / n, 3) if n else None for v in VERDICTS} | {"n": n}


BORNES_CLI: dict = {}  # --questions et --externes surchargent le registre pour un passage


def mesurer(graphe: str, renouveler: bool = False) -> dict | None:
    bornes = {**cfg.banc(), **{k: v for k, v in (BORNES_CLI or {}).items() if v}}
    base = cfg.chemins_du_graphe(graphe)["base"]
    p_q = base / "banc_questions.json"
    questions = json.loads(p_q.read_text(encoding="utf-8")) if p_q.exists() and not renouveler else []
    if not questions:
        questions = composer_jeu(graphe, bornes)
        if len(questions) < 5:
            print(f"  {graphe} : {len(questions)} question(s) seulement — pas de banc", file=sys.stderr)
            return None
        p_q.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"  {graphe} : {len(questions)} questions — "
        + ", ".join(
            f"{t} {sum(q['type'] == t for q in questions)}" for t in dict.fromkeys(q["type"] for q in questions)
        )
    )
    details = [{**q} for q in questions]
    resultat = {
        "graphe": graphe,
        "date": time.strftime("%Y-%m-%d"),
        "modele": te.MODELE,
        "questions": len(questions),
        "methode_graphe": methode_du_graphe(graphe),
        "conditions": {},
        "par_type": {},
    }
    agnostique = any(publie(q) for q in questions)
    if agnostique:
        # Deux phases distinctes (porteur, 07.10.2026). Phase 1, la restitution : les questions tirées du graphe, posées
        # au graphe seul — un test interne, qui ne compte pas dans le chiffre publié (le graphe retrouve presque toujours
        # ce qu'il contient). Phase 2, le banc agnostique : les questions écrites sans connaître le graphe, posées dans
        # les trois conditions — le chiffre publié.
        resultat["methode"] = "agnostique"
        restit = [q for q in questions if not publie(q)]
        fond = [q for q in questions if publie(q) and q["type"] not in COMPAREES]
        questions = [q for q in questions if q["type"] in COMPAREES]
        for cle, libelle, qs_g in (
            (
                "restitution",
                "Phase 1 — restitution : {n} questions tirées du graphe, posées au graphe seul (non publiée)",
                restit,
            ),
            (
                "fond",
                "Phase 2 — fond : {n} questions de fond, écrites sans connaître le graphe, posées au graphe seul",
                fond,
            ),
        ):
            print("  " + libelle.format(n=len(qs_g)))
            if not qs_g:
                continue
            reponses = repondre(graphe, "graphe", qs_g, bornes)
            resultat.setdefault("couts", {})[cle] = par_question(CONSO.get("graphe", {}), len(qs_g))
            CONSO.pop("graphe", None)
            if interrompu(graphe, resultat, details, f"phase {cle}"):
                return None
            verdicts = juger(qs_g, reponses, bornes["delai"])
            for d in details:
                if d["i"] in verdicts:
                    d["graphe"] = {"reponse": reponses.get(d["i"], {}).get("reponse", ""), "verdict": verdicts[d["i"]]}
            resultat[cle] = parts([verdicts[q["i"]] for q in qs_g])
            resultat[f"{cle}_par_type"] = {
                t: parts([verdicts[q["i"]] for q in qs_g if q["type"] == t])
                for t in dict.fromkeys(q["type"] for q in qs_g)
            }
            print(
                f"    graphe  juste {resultat[cle]['juste']:.0%}, erreurs {resultat[cle]['faux']:.0%}, "
                f"ne sait pas {resultat[cle]['ne_sait_pas']:.0%}"
            )
            ecrire_en_cours(graphe, resultat, details)
        print(f"  Phase 3 — banc agnostique comparé : {len(questions)} questions récentes, trois conditions (publié)")
    for c in CONDITIONS_POSEES:
        qs = questions
        reponses = repondre(graphe, c, qs, bornes)
        resultat.setdefault("couts", {})[c] = par_question(CONSO.get(c, {}), len(qs))
        if interrompu(graphe, resultat, details, f"condition {c}"):
            return None
        verdicts = juger(qs, reponses, bornes["delai"])
        for d in details:
            if d["i"] in verdicts:
                d[c] = {"reponse": reponses.get(d["i"], {}).get("reponse", ""), "verdict": verdicts[d["i"]]}
        resultat["conditions"][c] = parts([verdicts[q["i"]] for q in qs])
        for t in dict.fromkeys(q["type"] for q in qs):
            resultat["par_type"].setdefault(t, {})[c] = parts([verdicts[q["i"]] for q in qs if q["type"] == t])
        print(
            f"    {c:7} juste {resultat['conditions'][c]['juste']:.0%}, erreurs {resultat['conditions'][c]['faux']:.0%}, "
            f"ne sait pas {resultat['conditions'][c]['ne_sait_pas']:.0%}"
        )
        ecrire_en_cours(graphe, resultat, details)
    resultat["details"] = details
    (base / "banc.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "banc_en_cours.json").unlink(missing_ok=True)
    archiver(graphe, resultat, "mesure")
    return resultat


def ecrire_en_cours(graphe: str, resultat: dict, details: list[dict]) -> None:
    """Le banc en cours de mesure, écrit après chaque phase et chaque condition (08.10.2026) : un banc dure une à trois
    heures, et tout restait en mémoire jusqu'à la fin. Ce fichier n'est jamais publié ni lu comme un banc fini ;
    il est retiré quand banc.json est écrit."""
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc_en_cours.json"
    p.write_text(
        json.dumps({**resultat, "details": details, "en_cours": True}, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def interrompu(graphe: str, resultat: dict, details: list[dict], etape: str) -> bool:
    """Vrai si la limite d'usage est atteinte : le banc s'arrête là, écrit son état en cours, et ne publie rien —
    un banc dont les réponses manquent donnerait « 0 · 0 · 100 », qui n'est pas une mesure."""
    if not couts.en_limite():
        return False
    ecrire_en_cours(graphe, resultat, details)
    print(
        f"  ARRÊT : limite d'usage atteinte pendant {etape} ({couts.marque_en_cours() or 'ce processus'}) — banc non publié, "
        f"état dans banc_en_cours.json",
        file=sys.stderr,
    )
    return True


def recompter(r: dict) -> dict:
    """Refait les parts d'un banc agnostique déjà posé selon les règles en vigueur, à partir de ses réponses et verdicts
    gardés, sans rien redemander (07.10.2026 : la comparaison réduite aux questions récentes)."""
    det = r["details"]
    v = lambda d, c: (d.get(c) or {}).get("verdict")  # noqa: E731
    restit = [d for d in det if not publie(d) and v(d, "graphe")]
    fond = [d for d in det if publie(d) and d["type"] not in COMPAREES and v(d, "graphe")]
    comp = [d for d in det if d["type"] in COMPAREES]
    r["methode"] = "agnostique"
    r["restitution"] = parts([v(d, "graphe") for d in restit])
    r["restitution_par_type"] = {
        t: parts([v(d, "graphe") for d in restit if d["type"] == t]) for t in dict.fromkeys(d["type"] for d in restit)
    }
    r["fond"] = parts([v(d, "graphe") for d in fond])
    r["fond_par_type"] = {
        t: parts([v(d, "graphe") for d in fond if d["type"] == t]) for t in dict.fromkeys(d["type"] for d in fond)
    }
    r["conditions"] = {c: parts([v(d, c) for d in comp if v(d, c)]) for c in CONDITIONS}
    r["par_type"] = {
        t: {c: parts([v(d, c) for d in comp if d["type"] == t and v(d, c)]) for c in CONDITIONS}
        for t in dict.fromkeys(d["type"] for d in comp)
    }
    r["recompte_le"] = time.strftime("%Y-%m-%d")
    return r


def adresses_de(questions: list[dict]) -> set[str]:
    return {u.rstrip("/") for q in questions for u in re.findall(r"https?://[^)\s]+", q.get("citation") or "")}


def cycle(graphe: str) -> dict | None:
    """Le renouvellement des questions extérieures (porteur, 07.10.2026). Un banc est un échantillon : ingérer les pages
    de ses questions ferait monter le score sans rendre le graphe plus complet face à la question suivante. Mais ces
    pages sont de bonnes pages du domaine. D'où le cycle : (1) la page de chaque question extérieure manquée au dernier
    banc entre au graphe, par la voie de l'éclaireur ; (2) le jeu de ces questions est retiré, et 60 nouvelles sont
    tirées sur des pages que le graphe n'a pas ; (3) les questions de restitution, tirées du graphe, restent ; (4) le
    banc est posé dans les trois conditions. Les pages ingérées sont lues au passage suivant de la routine."""
    from newskit import eclaireur  # noqa: PLC0415

    base = cfg.chemins_du_graphe(graphe)["base"]
    p_r, p_q = base / "banc.json", base / "banc_questions.json"
    if not p_r.exists() or not p_q.exists():
        print(f"  {graphe} : pas encore de banc, premier banc posé")
        return mesurer(graphe, renouveler=True)
    r = json.loads(p_r.read_text(encoding="utf-8"))
    anciennes = json.loads(p_q.read_text(encoding="utf-8"))
    # (1) Les pages des extérieures manquées, ingérées.
    manquees = [
        d
        for d in r.get("details", [])
        if (d["type"] == "exterieur" or publie(d)) and (d.get("graphe") or {}).get("verdict") != "juste"
    ]
    bornes_e, aujourdhui = cfg.eclaireur(graphe), time.strftime("%Y-%m-%d")
    memoire, connues = eclaireur.lire_memoire(), eclaireur.adresses_connues(graphe)
    ecrites = 0
    for d in manquees:
        u = re.search(r"\((https?://[^)\s]+)\)\s*$", d.get("citation") or "")
        if not u:
            continue
        ligne = eclaireur.ingerer(
            graphe,
            {"url": u.group(1), "titre": "", "point": 0},
            [("question du banc retirée", d["question"])],
            bornes_e,
            connues,
            memoire,
            aujourdhui,
        )
        ecrites += ligne.startswith("  ok")
        print(ligne)
    eclaireur.MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"  {graphe} : {len(manquees)} question(s) extérieure(s) manquée(s) au banc du {r.get('date')}, {ecrites} page(s) ingérée(s)"
    )
    # (2) et (3) Les extérieures renouvelées, la restitution gardée.
    bornes = {**cfg.banc(), **{k: v for k, v in (BORNES_CLI or {}).items() if v}}
    gardees = [q for q in anciennes if q["type"] != "exterieur" and not publie(q)]
    if bornes.get("agnostiques"):
        # Agnostique : le graphe n'est pas consulté ; seules les pages du jeu retiré sont écartées.
        cible = bornes["agnostiques"]
        gardees = gardees[: bornes.get("restitution", 40)]
        neuves = composer_agnostique(graphe, cible, bornes["delai"], adresses_de(anciennes))
    else:
        cible = bornes["externes"]
        neuves = hors_sujet_retirees(
            graphe, tirer_exterieures(graphe, cible, bornes, adresses_de(anciennes) | connues)
        )[:cible]
    if len(neuves) < cible // 2:
        print(
            f"  {graphe} : {len(neuves)} question(s) extérieure(s) neuve(s) seulement — jeu gardé tel quel, banc non posé",
            file=sys.stderr,
        )
        return None
    questions = gardees + neuves
    for i, q in enumerate(questions):
        q["i"] = i
    p_q.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"  {graphe} : jeu renouvelé — {len(gardees)} question(s) de restitution gardée(s), {len(neuves)} question(s) neuve(s)"
    )
    # (4) Le banc, dans les trois conditions.
    return mesurer(graphe)


def preparer(graphe: str) -> list[dict] | None:
    """Tire un jeu agnostique neuf sans le poser (porteur, 07.10.2026) : il ne dépend que des sujets déclarés, pas du
    contenu du graphe, et peut donc être tiré pendant une extraction. Les questions de restitution du jeu en place sont
    gardées ; les pages du jeu en place sont écartées. Le banc se pose ensuite sans --renouveler."""
    bornes = {**cfg.banc(), **{k: v for k, v in (BORNES_CLI or {}).items() if v}}
    if not bornes.get("agnostiques"):
        print(f"  {graphe} : le registre ne demande pas de banc agnostique (banc.agnostiques)", file=sys.stderr)
        return None
    p_q = cfg.chemins_du_graphe(graphe)["base"] / "banc_questions.json"
    anciennes = json.loads(p_q.read_text(encoding="utf-8")) if p_q.exists() else []
    gardees = [q for q in anciennes if q["type"] != "exterieur" and not publie(q)][: bornes.get("restitution", 40)]
    if len(gardees) < bornes.get("restitution", 40) // 2:
        gardees = composer(graphe, {**bornes, "questions": bornes.get("restitution", 40), "externes": 0})
    neuves = composer_agnostique(graphe, bornes["agnostiques"], bornes["delai"], adresses_de(anciennes))
    if len(neuves) < bornes["agnostiques"] // 2:
        print(f"  {graphe} : {len(neuves)} question(s) agnostique(s) seulement — jeu en place gardé", file=sys.stderr)
        return None
    questions = gardees + neuves
    for i, q in enumerate(questions):
        q["i"] = i
    p_q.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"  {graphe} : jeu agnostique prêt — {len(gardees)} de restitution, "
        + ", ".join(f"{t} {sum(q['type'] == t for q in neuves)}" for t in AGNOSTIQUES)
    )
    return questions


def interne(graphe: str, etiquette: str = "") -> dict | None:
    """Les deux phases internes du banc agnostique, graphe seul, sur le jeu gardé : restitution (questions tirées du
    graphe) et fond (questions écrites sans le connaître). Rien n'est publié, banc.json n'est pas touché ; la mesure
    s'ajoute à banc_interne.jsonl. Sert à mesurer un changement du graphe lui-même (09.10.2026 : fusion des
    doublons de langue), sans payer les conditions « LLM seul » et « LLM + web », qui mesurent le modèle."""
    bornes = cfg.banc()
    base = cfg.chemins_du_graphe(graphe)["base"]
    p_q = base / "banc_questions.json"
    if not p_q.exists():
        print(f"  {graphe} : pas de jeu de questions (banc.py --preparer)", file=sys.stderr)
        return None
    questions = json.loads(p_q.read_text(encoding="utf-8"))
    restit = [q for q in questions if not publie(q)]
    fond = [q for q in questions if publie(q) and q["type"] not in COMPAREES]
    mesure = {
        "graphe": graphe,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "etiquette": etiquette,
        "methode_graphe": methode_du_graphe(graphe),
    }
    for cle, qs in (("restitution", restit), ("fond", fond)):
        if not qs:
            continue
        reponses = repondre(graphe, "graphe", qs, bornes)
        if couts.en_limite():
            print(f"  ARRÊT : limite d'usage atteinte pendant la phase {cle} ; mesure non enregistrée", file=sys.stderr)
            return None
        verdicts = juger(qs, reponses, bornes["delai"])
        mesure[cle] = parts([verdicts[q["i"]] for q in qs])
        mesure[f"{cle}_couts"] = par_question(CONSO.pop("graphe", {}), len(qs))
        mesure[f"{cle}_detail"] = [{"i": q["i"], "type": q["type"], "verdict": verdicts[q["i"]]} for q in qs]
        print(
            f"    {cle:12} {trois_parts(mesure[cle])}  ({len(qs)} q., {mesure[f'{cle}_couts'].get('cout_usd_par_question') or 0:.3f} $/q.)"
        )
    with open(base / "banc_interne.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(mesure, ensure_ascii=False) + "\n")
    return mesure


def cycle_du_jour(graphe: str) -> dict | None:
    """Le cycle, s'il est dû : le dernier banc a au moins `renouveler_jours` jours (registre, clé `banc`). 0 : jamais."""
    jours = cfg.banc().get("renouveler_jours", 0)
    p_r = cfg.chemins_du_graphe(graphe)["base"] / "banc.json"
    if not jours or not a_une_pyramide(graphe):
        print(f"  {graphe} : pas de renouvellement automatique")
        return None
    if p_r.exists():
        date = json.loads(p_r.read_text(encoding="utf-8")).get("date") or "1970-01-01"
        age = (time.time() - time.mktime(time.strptime(date, "%Y-%m-%d"))) // 86400
        if age < jours:
            print(f"  {graphe} : banc du {date}, renouvellement dans {int(jours - age)} jour(s)")
            return None
    return cycle(graphe)


def historique(graphe: str) -> None:
    """Les bancs successifs d'un graphe, du plus ancien au plus récent."""
    h = cfg.chemins_du_graphe(graphe)["base"] / "banc_historique.jsonl"
    if not h.exists():
        print(f"  pas d'historique de banc pour {graphe}")
        return
    print(f"  {graphe} — bancs successifs, bonnes réponses (erreurs)")
    print(f"    {'quand':17} {'jeu':10} {'q.':>3}  {'graphe':>11} {'LLM seul':>11} {'LLM + web':>11}  événement")
    for l in h.read_text(encoding="utf-8").splitlines():
        x = json.loads(l)
        c = x["conditions"]
        jf = lambda k: trois_parts(c[k]) if c.get(k, {}).get("n") else "—"  # noqa: E731
        print(
            f"    {x['horodatage']:17} {x['jeu']:10} {x['questions']:>3}  {jf('graphe'):>11} {jf('modele'):>11} {jf('web'):>11}  {x['evenement']}"
        )


def trois_parts(x: dict) -> str:
    """« 92 · 0 · 8 » : juste, faux, ne sait pas, en pour cent. « Ne sait pas » n'est ni juste ni faux :
    l'écrire évite qu'on le lise dans l'un ou l'autre (05.10.2026). Absent d'une mesure, c'est le reste."""
    j, f = x.get("juste") or 0, x.get("faux") or 0
    n = x.get("ne_sait_pas")
    n = max(0.0, 1 - j - f) if n is None else n
    return f"{round(j * 100)} · {round(f * 100)} · {round(n * 100)}"


def afficher(r: dict) -> None:
    if r.get("restitution"):
        x = r["restitution"]
        print(
            f"  {r['graphe']} — restitution du {r['date']} : {x['n']} questions tirées du graphe, posées au graphe seul, "
            f"non publiée — {trois_parts(x)} (juste · faux · ne sait pas, en %)"
        )
        for t, y in (r.get("restitution_par_type") or {}).items():
            print(f"    {t:13} {trois_parts(y):>14}   ({y['n']} q.)")
        print()
    if r.get("fond"):
        x = r["fond"]
        print(
            f"  {r['graphe']} — fond du {r['date']} : {x['n']} questions de fond écrites sans connaître le graphe, posées au "
            f"graphe seul — {trois_parts(x)} (juste · faux · ne sait pas, en %)"
        )
        print()
    n_publie = (r.get("conditions", {}).get("graphe") or {}).get("n") or r["questions"]
    titre = "banc agnostique" if r.get("methode") == "agnostique" else "banc"
    print(
        f"  {r['graphe']} — {titre} du {r['date']}, {n_publie} questions, LLM {r['modele']} — juste · faux · ne sait pas, en %"
    )
    conds = CONDITIONS + tuple(c for c in CONDITIONS_EN_PLUS if c in r.get("conditions", {}))
    noms = {"graphe": "graphe", "modele": "LLM seul", "web": "LLM + web", "fiches": "fiches"}
    print(f"    {'':13} " + "  ".join(f"{noms[n]:>14}" for n in conds))

    def ligne(nom: str, par: dict) -> None:
        print(
            f"    {nom:13} "
            + "  ".join(f"{trois_parts(par[c]):>14}" if par.get(c, {}).get("n") else f"{'—':>14}" for c in conds)
            + f"   ({next(iter(par.values()))['n']} q.)"
        )

    ligne("ensemble", r["conditions"])
    co = r.get("couts") or {}
    if co:
        cell = lambda c: (
            f"{co[c]['tokens_par_question']:>6} tok {co[c]['cout_usd_par_question']:.3f} $"
            if co.get(c, {}).get("tokens_par_question") is not None
            else f"{'—':>14}"
        )  # noqa: E731
        print(
            f"    {'coût / q.':13} "
            + "  ".join(f"{cell(c):>14}" for c in conds)
            + "   (tokens par question, cache compris ; équivalent API)"
        )
    for t, par in r.get("par_type", {}).items():
        if r.get("restitution_par_type") and t not in COMPAREES:
            continue
        ligne(t, par)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--etat", action="store_true", help="relire la dernière mesure")
    ap.add_argument("--renouveler", action="store_true", help="tirer un nouveau jeu de questions")
    ap.add_argument("--rejuger", action="store_true", help="rejuger les réponses gardées, sans les redemander")
    ap.add_argument(
        "--trier", action="store_true", help="retirer les questions hors sujet du dernier banc et recompter"
    )
    ap.add_argument("--composer", action="store_true", help="tirer et trier un nouveau jeu de questions, sans le poser")
    ap.add_argument("--historique", action="store_true", help="les bancs successifs du graphe")
    ap.add_argument(
        "--cycle",
        action="store_true",
        help="ingérer les pages des extérieures manquées, retirer leur jeu, en tirer un neuf, poser le banc",
    )
    ap.add_argument(
        "--cycle-du-jour", action="store_true", help="le cycle, s'il est dû (registre : banc.renouveler_jours)"
    )
    ap.add_argument("--preparer", action="store_true", help="tirer un jeu agnostique neuf sans le poser")
    ap.add_argument(
        "--interne",
        nargs="?",
        const="",
        metavar="ÉTIQUETTE",
        help="phases internes seules (restitution, fond ; graphe seul) sur le jeu gardé, sans publier — banc_interne.jsonl",
    )
    ap.add_argument("--questions", type=int, help="taille du jeu de questions (avec --renouveler)")
    ap.add_argument("--externes", type=int, help="dont tirées de pages que le graphe n'a pas")
    ap.add_argument(
        "--conditions",
        nargs="+",
        choices=CONDITIONS,
        default=list(CONDITIONS),
        help="les conditions à poser (défaut : les trois) ; « graphe » seul mesure le graphe sans payer le modèle ni le web",
    )
    ap.add_argument(
        "--reposer-recentes",
        action="store_true",
        help="reposer au graphe seul les questions récentes du banc agnostique, si le graphe a changé depuis la dernière pose (routine)",
    )
    ap.add_argument(
        "--reposer",
        nargs="+",
        choices=CONDITIONS + CONDITIONS_EN_PLUS,
        help="reposer ces conditions sur le même jeu, puis tout rejuger ; « fiches » : l'ancien chemin du graphe, pour comparer",
    )
    ap.add_argument(
        "--sans-cartes",
        action="store_true",
        help="répondre sans le routage par les cartes des sujets (synthese.repondre lit KG_SANS_CARTES) : mesurer l'un contre l'autre",
    )
    args = ap.parse_args()
    if args.sans_cartes:
        os.environ["KG_SANS_CARTES"] = "1"  # hérité par `claude -p`, puis par les commandes qu'il lance
    graphe = cfg.nom_actuel(args.graphe)
    CONTEXTE["graphe"] = graphe
    BORNES_CLI.update({"questions": args.questions, "externes": args.externes})
    global CONDITIONS_POSEES
    CONDITIONS_POSEES = tuple(c for c in CONDITIONS if c in args.conditions)
    p = cfg.chemins_du_graphe(graphe)["base"] / "banc.json"
    if args.etat:
        if not p.exists():
            print(f"  pas de banc pour {graphe}", file=sys.stderr)
            return 1
        afficher(json.loads(p.read_text(encoding="utf-8")))
        return 0
    if args.historique:
        historique(graphe)
        return 0
    if args.composer:
        BORNES_CLI.update({"questions": args.questions, "externes": args.externes})
        q = composer_jeu(graphe, {**cfg.banc(), **{k: v for k, v in BORNES_CLI.items() if v}})
        (cfg.chemins_du_graphe(graphe)["base"] / "banc_questions.json").write_text(
            json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"  {graphe} : {len(q)} questions — "
            + ", ".join(f"{t} {sum(x['type'] == t for x in q)}" for t in TYPES + AGNOSTIQUES)
        )
        return 0
    if args.preparer:
        return 0 if preparer(graphe) else 1
    if args.interne is not None:
        return 0 if interne(graphe, args.interne) else 1
    if args.reposer_recentes:
        return 0 if reposer_recentes(graphe) else 1
    if args.cycle_du_jour:
        r = cycle_du_jour(graphe)
        if r:
            afficher(r)
        return 0
    if args.trier:
        r = trier(graphe)
    elif args.cycle:
        r = cycle(graphe)
    elif args.reposer:
        r = rejuger(graphe, tuple(args.reposer))
    else:
        r = rejuger(graphe) if args.rejuger else mesurer(graphe, args.renouveler)
    if not r:
        return 1
    afficher(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())

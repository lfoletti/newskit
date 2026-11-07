#!/usr/bin/env python3
"""Banc de session : le coût d'une conversation, question après question, avec le graphe ou avec le web.

Le banc ordinaire (banc.py) ouvre une session neuve par lot de questions : il paie le contexte fixe à chaque lot
et ne voit jamais ce qui fait la différence dans un usage réel — dans une conversation, tout ce qu'un outil
renvoie reste dans le contexte et est relu à chaque tour suivant. Une page web lue pèse sur toute la suite ;
une réponse du graphe, compacte, beaucoup moins (05.10.2026).

Ici, les questions sont posées une à une dans UNE conversation par condition (`claude -p --session-id`, puis
`--resume`), comme un utilisateur de Claude Desktop. Chaque tour rend sa consommation ; on en tire la courbe du
coût cumulé, puis le juge du banc note les réponses.

    python3 -m newskit.banc_session --graphe <graphe> --questions 10
    python3 -m newskit.banc_session --graphe <graphe> --questions 10 --modele claude-sonnet-5-5
    python3 -m newskit.banc_session --graphe <graphe> --etat

Mêmes réglages pour les deux conditions, pour que la comparaison tienne : seuls les outils de la condition sont
chargés (`--tools`), et une consigne système courte remplace celle de Claude Code. Résultat :
graphe/<nom>/banc_session.json, et une ligne par essai dans graphe/<nom>/banc_session_historique.jsonl.

Premier essai, à éprouver : le jeu de questions est celui du banc (les N premières), la session web peut lire
autant de pages qu'elle veut, et le cache de Claude Code (cinq minutes) peut expirer entre deux questions lentes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import banc  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import couts  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

CONDITIONS = ("graphe", "web")
SCHEMA = {
    "type": "object",
    "required": ["reponse", "sait"],
    "properties": {"reponse": {"type": "string"}, "sait": {"type": "boolean"}, "source": {"type": "string"}},
}
SYSTEME = (
    "Tu réponds aux questions d'un utilisateur, une à une, avec l'outil permis, puis tu rends le JSON demandé. "
    "Sois bref."
)


def ouverture(graphe: str, condition: str) -> str:
    """Le premier message de la conversation : la consigne, et pour le graphe sa carte et son outil."""
    decl = cfg.graphe_declare(graphe)
    if condition == "web":
        return (
            f"Je vais te poser des questions sur « {decl['libelle']} », une à la fois. Cherche sur le web pour y "
            "répondre. Si tu ne trouves pas, dis que tu ne sais pas : ne devine pas.\n\n"
        )
    outil = f"{banc.OUTIL_GRAPHE} --graphe {graphe}"
    carte = cfg.chemins_du_graphe(graphe)["carte"]
    return (
        f"Je vais te poser des questions sur « {decl['libelle']} », une à la fois. Réponds en n'employant QUE le "
        "graphe de connaissances local : sa carte ci-dessous, et son outil d'interrogation, par des commandes shell. "
        "Ta mémoire ne compte pas : si le graphe ne le dit pas, tu ne sais pas.\n\n"
        f'Commandes :\n  {outil} chercher "<termes>"\n  {outil} expliquer "<notion>"\n'
        f'  {outil} voisinage "<notion>"\n  {outil} sources "<notion>"\n  {outil} fiche "<fragment du nom de fiche>"\n'
        f'  {outil} passages "<mots précis>"   — le texte des fiches : versions, chiffres, dates, qui a dit quoi\n'
        "Une commande par appel, qui commence exactement par l'outil ; plusieurs requêtes peuvent s'enchaîner avec &&.\n\n"
        f"Carte du graphe :\n{carte.read_text(encoding='utf-8')[:9000] if carte.exists() else '(absente)'}\n\n"
    )


def tour(condition: str, session: str, premier: bool, message: str, modele: str) -> tuple[dict, dict]:
    """(réponse, consommation) d'un tour de la conversation."""
    outils = ["Bash"] if condition == "graphe" else ["WebSearch", "WebFetch"]
    permis = [f"Bash({banc.OUTIL_GRAPHE}:*)"] if condition == "graphe" else outils
    args = [
        *couts.CLAUDE_P,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
        "--tools",
        ",".join(outils),
        "--allowedTools",
        *permis,
        "--system-prompt",
        SYSTEME,
        "--model",
        modele,
    ]
    args += ["--session-id", session] if premier else ["--resume", session]
    t0 = time.time()
    try:
        r = subprocess.run(
            args,
            input=message,
            capture_output=True,
            text=True,
            timeout=900,
            cwd=RACINE,
            env={**os.environ, "KG_SANS_LACUNE": "1"},
        )
        env = couts.enregistrer("banc_session", banc.CONTEXTE["graphe"], modele, r, time.time() - t0)
        if r.returncode != 0:
            env = {}
    except subprocess.TimeoutExpired:
        couts.enregistrer("banc_session", banc.CONTEXTE["graphe"], modele, None, time.time() - t0, note="délai dépassé")
        env = {}
    compte: dict = {}
    banc.compter(compte, env) if env else None
    brut = env.get("structured_output") or env.get("result") or {}
    if isinstance(brut, str):
        try:
            brut = json.loads(brut)
        except json.JSONDecodeError:
            brut = {}
    return (brut if isinstance(brut, dict) else {}), compte


def mesurer(graphe: str, n: int, modele: str) -> dict:
    questions = json.loads((cfg.chemins_du_graphe(graphe)["base"] / "banc_questions.json").read_text(encoding="utf-8"))[
        :n
    ]
    resultat = {
        "graphe": graphe,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "modele": modele,
        "questions": len(questions),
        "conditions": {},
    }
    for c in CONDITIONS:
        session, cumul, tours, reponses = str(uuid.uuid4()), 0.0, [], {}
        print(f"  {c} — session {session[:8]}")
        for k, q in enumerate(questions):
            message = (
                (ouverture(graphe, c) if k == 0 else "") + f"Question : {q['question']}\n\n"
                'Réponds en JSON : {"reponse": "…" (une ou deux lignes, vide si tu ne sais pas), "sait": <bool>, "source": "…"}.'
            )
            rep, compte = tour(c, session, k == 0, message, modele)
            reponses[q["i"]] = {"reponse": str(rep.get("reponse") or "")[:600]}
            cumul += compte.get("cout_usd", 0.0)
            tokens = sum(compte.get(x, 0) for x in ("entree", "cache_ecriture", "cache_lecture", "sortie"))
            tours.append(
                {
                    "i": q["i"],
                    "tokens": tokens,
                    "cout_usd": compte.get("cout_usd", 0.0),
                    "cumul_usd": round(cumul, 4),
                    "tours_agent": compte.get("tours", 0),
                }
            )
            print(f"    q{k + 1:>2}  {tokens:>7} tok  {compte.get('cout_usd', 0):.3f} $   cumul {cumul:.3f} $")
        verdicts = banc.juger(questions, reponses, 900)
        cv = Counter(verdicts.values())
        resultat["conditions"][c] = {
            "juste": cv["juste"],
            "faux": cv["faux"],
            "ne_sait_pas": cv["ne_sait_pas"],
            "cout_usd": round(cumul, 4),
            "cout_usd_par_question": round(cumul / len(questions), 4),
            "tours": tours,
            "reponses": {str(i): r["reponse"] for i, r in reponses.items()},
            "verdicts": {str(i): v for i, v in verdicts.items()},
        }
    base = cfg.chemins_du_graphe(graphe)["base"]
    (base / "banc_session.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(base / "banc_session_historique.jsonl", "a", encoding="utf-8") as h:
        h.write(
            json.dumps(
                {k: v for k, v in resultat.items() if k != "conditions"}
                | {
                    "conditions": {
                        c: {k: v for k, v in x.items() if k not in ("tours", "reponses", "verdicts")}
                        for c, x in resultat["conditions"].items()
                    }
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return resultat


def afficher(r: dict) -> None:
    print(f"  {r['graphe']} — banc de session du {r['date']}, {r['questions']} questions d'affilée, {r['modele']}")
    for c, x in r["conditions"].items():
        print(
            f"    {c:7} juste · faux · ne sait pas : {x['juste']} · {x['faux']} · {x['ne_sait_pas']}   "
            f"coût {x['cout_usd']:.3f} $ ({x['cout_usd_par_question']:.4f} $ par question)"
        )
    g, w = r["conditions"].get("graphe"), r["conditions"].get("web")
    if g and w and w["cout_usd"]:
        print(f"    le graphe coûte {g['cout_usd'] / w['cout_usd']:.0%} du web sur la conversation")
        print("    coût cumulé, question par question :")
        for a, b in zip(g["tours"], w["tours"]):
            print(f"      q{g['tours'].index(a) + 1:>2}   graphe {a['cumul_usd']:.3f} $   web {b['cumul_usd']:.3f} $")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--questions", type=int, default=10)
    ap.add_argument("--modele", default=te.MODELE or media.modele("analyse"))
    ap.add_argument("--etat", action="store_true", help="relire le dernier essai")
    args = ap.parse_args()
    graphe = cfg.nom_actuel(args.graphe)
    banc.CONTEXTE["graphe"] = graphe
    if args.etat:
        p = cfg.chemins_du_graphe(graphe)["base"] / "banc_session.json"
        if not p.exists():
            print(f"  pas de banc de session pour {graphe}")
            return 1
        afficher(json.loads(p.read_text(encoding="utf-8")))
        return 0
    afficher(mesurer(graphe, args.questions, args.modele))
    return 0


if __name__ == "__main__":
    sys.exit(main())

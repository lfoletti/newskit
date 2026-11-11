#!/usr/bin/env python3
"""La weekly : une sélection des daily des 8 derniers jours, parue le dimanche à midi (mainteneur, 03.11.2026).

Les daily envoyées sont gardées sous lettres/<graphe>/<envoi>.json (notifier_courriel.garder_lettre). Un appel au
modèle choisit la une (1 à 5 faits, ce que la semaine justifie), garde les meilleurs billets de chaque rubrique et
fond les sujets suivis sur plusieurs jours ; textes, lieux et angles des daily sont repris tels quels, sauf le texte
d'un sujet fondu, complété des faits des autres jours. La weekly est en couleurs d'automne
(lettre.AUTOMNE), la daily en vert.
Une ligne au registre des coûts (poste lettre, note « weekly »).

Usage :
    python3 -m newskit.lettre_semaine --graphe <graphe>                  # dimanche : compose, envoie aux abonnés, garde
    python3 -m newskit.lettre_semaine --graphe <graphe> --jour 2026-11-01 --essai <dossier>   # écrit, n'envoie rien
    python3 -m newskit.lettre_semaine --graphe <graphe> --daily <lettre.json> … --essai <dossier>
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from newskit import couts
from newskit import lettre as lt
from newskit import reglages  # noqa: E402 — les réglages du média

UNE_MAX = 5
PAR_RUBRIQUE = 4

SCHEMA = {
    "type": "object",
    "required": ["une", "rubriques", "fusions"],
    "properties": {
        "une": {"type": "array", "items": {"type": "integer"}},
        "rubriques": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rubrique", "billets"],
                "properties": {
                    "rubrique": {"type": "string"},
                    "billets": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "fusions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["garde", "fondus", "texte"],
                "properties": {
                    "garde": {"type": "integer"},
                    "fondus": {"type": "array", "items": {"type": "integer"}},
                    "texte": {"type": "string"},
                },
            },
        },
    },
}


def dailies_de_la_semaine(graphe: str, dimanche: str) -> list[dict]:
    """Les daily gardées des 8 jours qui finissent le `dimanche`, dans l'ordre des jours."""
    from newskit import notifier_courriel as nc

    fin = dt.date.fromisoformat(dimanche)
    debut = fin - dt.timedelta(days=lt.JOURS_SEMAINE - 1)
    out = []
    for p in sorted((nc.LETTRES / graphe).glob("????-??-??.json")):
        try:
            L = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if L.get("periode") == "jour" and debut <= dt.date.fromisoformat(str(L.get("envoi"))[:10]) <= fin:
            out.append(L)
    return out


def composer(dailies: list[dict], dimanche: str) -> dict:
    """La weekly des `dailies` (ordre des jours), datée du `dimanche`."""
    items = []
    for L in dailies:
        for f in L["faits"]:
            x = copy.deepcopy(f)
            x["descriptif"] = x.pop("paragraphe", "")
            x["groupe"] = x.pop("rubrique", None)
            items.append(("une", L["envoi"], x))
        for g, v in L["aussi"].items():
            for x in v:
                items.append(("rubrique", L["envoi"], dict(copy.deepcopy(x), groupe=g)))
    base = dailies[-1]
    ordre = base.get("ordre") or []
    lignes = [
        json.dumps(
            {
                "n": i,
                "jour": j,
                "place_daily": p,
                "rubrique": x.get("groupe"),
                "lieu": x.get("lieu", ""),
                "importance": x.get("importance"),
                "titre": x["titre"],
                "texte": x.get("descriptif", ""),
            },
            ensure_ascii=False,
        )
        for i, (p, j, x) in enumerate(items)
    ]
    invite = f"""Tu composes la lettre hebdomadaire d'une veille sur la gouvernance de l'IA à partir des billets de ses
lettres quotidiennes. Rubriques, dans l'ordre : {", ".join(ordre)}.
- une : de 1 à {UNE_MAX} numéros, les faits les plus importants de la semaine pour un responsable de la gouvernance de
  l'IA (décision publique, sanction, incident majeur, chiffre qui change l'appréciation), pas plus d'un par sujet. Ne
  mets à la une que ce qui le justifie : une semaine calme a une une courte.
- rubriques : pour chaque rubrique, {PAR_RUBRIQUE} numéros au plus parmi les autres billets, les plus importants d'abord.
- fusions : quand plusieurs billets traitent du même sujet (même événement suivi plusieurs jours), garde le plus
  récent ou le plus complet et donne les autres en « fondus » ; « texte » est alors le texte gardé complété des faits
  nouveaux des fondus, sans rien inventer, en autant de phrases qu'il faut. Sinon pas de fusion.
Un billet fondu ne paraît pas ailleurs. Rends l'objet demandé.

""" + "\n".join(lignes)
    args = [
        *couts.CLAUDE_P,
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        lt.MODELE,
        "--json-schema",
        json.dumps(SCHEMA),
    ]
    t0 = time.time()
    r = subprocess.run(args, input=invite, capture_output=True, text=True, timeout=900, cwd=lt.RACINE, check=False)
    env = couts.enregistrer(lt.POSTE, base.get("graphe") or "", lt.MODELE, r, time.time() - t0, note="weekly")
    rep = env.get("structured_output") or json.loads(
        re.sub(r"^```(?:json)?\s*|\s*```$", "", str(env.get("result") or "").strip())
    )
    fondu = set()
    for fu in rep["fusions"]:
        k = fu["garde"]
        if not 0 <= k < len(items):
            continue
        x = items[k][2]
        x["descriptif"] = fu["texte"] or x["descriptif"]
        for j in fu["fondus"]:
            if 0 <= j < len(items) and j != k:
                fondu.add(j)
                if items[j][2].get("source"):
                    x.setdefault("sources_plus", []).append(items[j][2]["source"])
                x["sources_plus"] = x.get("sources_plus", []) + items[j][2].get("sources_plus", [])
    W = copy.deepcopy(base)
    W["faits"], W["aussi"], W["bref"], W["bref_reste"] = [], {}, {}, 0
    pris = set()
    for k in rep["une"][:UNE_MAX]:
        if 0 <= k < len(items) and k not in fondu and k not in pris:
            x = copy.deepcopy(items[k][2])
            pris.add(k)
            x["paragraphe"] = x.pop("descriptif", "")
            x["rubrique"] = x.pop("groupe", None)
            x.setdefault("gras", [])
            x.setdefault("autres", 0)
            W["faits"].append(x)
    for ru in rep["rubriques"]:
        v = [
            copy.deepcopy(items[k][2])
            for k in ru["billets"][:PAR_RUBRIQUE]
            if 0 <= k < len(items) and k not in fondu and k not in pris
        ]
        pris |= set(ru["billets"])
        if v:
            W["aussi"].setdefault(ru["rubrique"], []).extend(v)
    W["aussi"] = lt.trier_groupes(W["aussi"], ordre)
    W["periode"], W["envoi"] = "semaine", dimanche
    W["debut"] = (dt.date.fromisoformat(dimanche) - dt.timedelta(days=lt.JOURS_SEMAINE - 1)).isoformat()
    W["cout_lettre"] = float(env.get("total_cost_usd") or 0)
    return W


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--jour", help="le dimanche de parution (défaut : aujourd'hui)")
    ap.add_argument("--daily", action="append", type=Path, help="une daily (lettre.json) au lieu des daily gardées")
    ap.add_argument("--essai", type=Path, help="écrit la weekly dans ce dossier, n'envoie rien")
    ap.add_argument("--envoyer", action="store_true", help="envoie la weekly aux abonnés ; sans lui, rien ne part")
    a = ap.parse_args()
    if not (a.essai or a.envoyer):
        # Rien ne part par erreur (mainteneur, 04.11.2026) : l'envoi se demande en toutes lettres.
        print(
            "  rien à faire : --envoyer pour envoyer, --essai <dossier> pour lire ; rien n'est composé", file=sys.stderr
        )
        return 2
    dimanche = a.jour or time.strftime("%Y-%m-%d")
    dailies = (
        [json.loads(p.read_text(encoding="utf-8")) for p in a.daily]
        if a.daily
        else dailies_de_la_semaine(a.graphe, dimanche)
    )
    dailies.sort(key=lambda L: L["envoi"])
    if not dailies:
        print(
            f"  aucune daily de {a.graphe} dans les 8 jours qui finissent le {dimanche} : pas de weekly",
            file=sys.stderr,
        )
        return 1
    W = composer(dailies, dimanche)
    print(
        f"  weekly {a.graphe} du {dimanche} : {len(dailies)} daily, une {len(W['faits'])}, "
        f"rubriques {sum(map(len, W['aussi'].values()))}, coût {W['cout_lettre']:.3f} $"
    )
    if a.essai:
        a.essai.mkdir(parents=True, exist_ok=True)
        (a.essai / "lettre.json").write_text(json.dumps(W, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        W.setdefault("expediteur", "")
        (a.essai / "lettre.html").write_text(lt.html(W), encoding="utf-8")
        (a.essai / "lettre.txt").write_text(lt.texte(W), encoding="utf-8")
        print(f"  essai écrit dans {a.essai}")
        return 0
    from newskit import diffusion
    from newskit import notifier_courriel as nc
    from newskit import routine_hebdo as rh

    code = diffusion.envoyer(W)
    if code == 0:
        nc.garder_lettre(W)
        rh.commiter_lettres(lt.RACINE)  # la page /lettres/ de la weekly vient de la passe horaire du VPS
    return code


reglages.appliquer(__name__, globals())  # les réglages du média (reglages.py)


if __name__ == "__main__":
    sys.exit(main())

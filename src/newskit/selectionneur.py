"""Le Sélectionneur : les sujets de la daily qui se prêtent le mieux à une enquête, proposés dans la WIP.

mainteneur, 04.11.2026 : « certains sujets se prêtent mieux que d'autres à l'enquête. un agent "Sélectionneur" pourrait-il
être encodé, de manière à choisir les sujets les plus pertinents ou compatibles dans la routine quotidienne ? » ;
« oui, code le sélectionneur en mode proposition ».

Mode proposition : il note les éléments de la daily et propose les PROPOSES meilleurs dans les Coulisses de la WIP,
avec leur numéro et une hypothèse d'enquête. Le mainteneur commande l'éclairage (reporter.py --lettre … --numero n) ;
le Sélectionneur ne lance rien.

Note d'un élément = thèse possible + sources primaires (un point au plus si le modèle ne nomme pas la principale) +
apport de l'enquête au-delà de la nouvelle + enjeu pour le lecteur, chacun de 0 à 3, donnés par un appel au modèle
sans recherche web ; + 1 si sa source se lit sans geste humain (cache/eclairages/<graphe>/gestes.jsonl) ; un sujet déjà enquêté
(cache/eclairages/<graphe>/*/*/enquete.json) ou publié n'est jamais proposé.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from newskit import lettre as lt
from newskit import reporter as rp

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)
SORTIE = RACINE / "cache" / "selection"
CANDIDATS_MAX = 12          # éléments de la daily notés, dans l'ordre de lecture
PROPOSES = 2
USD = 0.30                  # plafond de l'appel
MOTS_EXTRAIT = 200          # extrait de la fiche donné par élément
POSTE = "selectionneur"

SCHEMA = {"type": "object", "required": ["notes"], "properties": {"notes": {"type": "array", "items": {
    "type": "object", "required": ["ref", "groupe", "these", "primaires", "primaire", "apport", "enjeu", "hypothese", "raison"], "properties": {
        "ref": {"type": "integer"}, "groupe": {"type": "integer"}, "these": {"type": "integer"}, "primaires": {"type": "integer"},
        "primaire": {"type": "string"}, "apport": {"type": "integer"}, "enjeu": {"type": "integer"},
        "hypothese": {"type": "string"}, "raison": {"type": "string"}}}}}}


def domaines_illisibles(graphe: str) -> set[str]:
    """Les domaines qu'une enquête n'a pas pu lire sans geste humain."""
    f = rp.SORTIE / graphe / "gestes.jsonl"
    out = set()
    for ligne in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        try:
            out.add(rp.domaine(json.loads(ligne).get("url", "")))
        except ValueError:
            continue
    return out - {""}


def urls_traitees(graphe: str) -> set[str]:
    """Les adresses des sujets déjà enquêtés ou publiés du graphe."""
    out = set()
    for f in (rp.SORTIE / graphe).glob("*/*/enquete.json") if (rp.SORTIE / graphe).exists() else []:
        try:
            u = (json.loads(f.read_text(encoding="utf-8")).get("sujet") or {}).get("url", "")
        except (OSError, ValueError):
            continue
        if u:
            out.add(u.rstrip("/"))
    return out | {u.rstrip("/") for u in lt.eclairages_publies(graphe)}


def score(note: dict, url: str, graphe: str) -> float:
    def borne(x):
        try:
            return max(0, min(3, int(x)))
        except (TypeError, ValueError):
            return 0
    # Une source primaire que le modèle ne sait pas nommer vaut un point au plus (mainteneur, 04.11.2026 : « affiner »).
    primaires = borne(note.get("primaires")) if str(note.get("primaire") or "").strip() else min(1, borne(note.get("primaires")))
    return borne(note.get("these")) + primaires + borne(note.get("apport")) + borne(note.get("enjeu")) + \
        (0 if rp.domaine(url) in domaines_illisibles(graphe) else 1)


def _extrait(chemin: str) -> str:
    if not chemin:
        return ""
    try:
        f = lt.charger_fiches([chemin]).get(chemin) or {}
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(str(f.get("texte") or f.get("corps") or "").split()[:MOTS_EXTRAIT])


def invite(elements: list[dict]) -> str:
    blocs = "\n\n".join(f"n° {e['ref']} — {e['titre']} ({e.get('rubrique', '')}) — {e['url']}\n{_extrait(e.get('chemin', ''))}"
                        for e in elements)
    return ("SÉLECTION D'ENQUÊTE. Voici les éléments d'une lettre quotidienne de veille. Un éclairage est une enquête "
            "courte, sourcée, qui défend une thèse pour un lecteur cultivé non spécialiste. Note chaque élément sur ce "
            "qui fait une enquête, pas sur l'importance de la nouvelle. `these` (0 à 3) : y a-t-il un écart à établir, "
            "entre ce qui est annoncé et ce qui est fait, une règle contestée, une question ouverte qu'une recherche "
            "peut trancher ? 0 : un simple fait sans enjeu à démêler. `primaires` (0 à 3) : des sources primaires "
            "existent-elles et sont-elles probablement publiques (texte de loi, décision, rapport, déclaration "
            "officielle, données) ? `primaire` : le nom de la principale (« l'avis de l'avocat général dans l'affaire "
            "C-250/25 »), vide si tu ne peux pas la nommer. `groupe` : le plus petit "
            "numéro des éléments qui reposent sur la même source primaire (le même rapport, la même décision), sinon "
            "le numéro de l'élément lui-même. `apport` (0 à 3) : ce qu'une enquête ajouterait à la nouvelle "
            "elle-même ; 0 si la nouvelle dit déjà tout ou si la presse l'a déjà analysée de près, 3 si les faits "
            "épars demandent à être reliés ou confrontés. `enjeu` (0 à 3) : la conséquence concrète pour le lecteur "
            "de la lettre (une obligation, une échéance, une décision, un risque pour son organisation) ; 0 si le "
            "sujet reste une curiosité. `hypothese` : l'hypothèse d'enquête en une phrase, vérifiable, sans adjectif "
            "d'évaluation. `raison` : en une phrase, ce qui rend l'enquête faisable ou non. Ne cherche rien sur le "
            "web.\n\n" + rp._DONNEE + "\n\n⟦\n" + blocs + "\n⟧\n\nRéponds en JSON selon le schéma.")


def proposer(L: dict, graphe: str) -> list[dict]:
    """Les PROPOSES meilleurs éléments de la daily pour une enquête, gardés sous cache/selection/<graphe>/<envoi>.json ;
    [] sans réponse du modèle."""
    traites = urls_traitees(graphe)
    elements = [e for e in lt.index(L) if e["url"].rstrip("/") not in traites][:CANDIDATS_MAX]
    if not elements:
        return []
    budget = rp.Budget(usd=USD, lettre_usd=USD)
    r = rp.appeler(invite(elements), SCHEMA, budget, graphe, "sélection", poste=POSTE) or {}
    par_ref = {e["ref"]: e for e in elements}
    notes = [n for n in r.get("notes") or [] if isinstance(n, dict) and n.get("ref") in par_ref]
    classes = sorted(notes, key=lambda n: -score(n, par_ref[n["ref"]]["url"], graphe))
    # Une source primaire, un sujet : le mieux noté du groupe est proposé, les autres le suivent sous « aussi »
    # (mainteneur, 04.11.2026 : « fusionne les éléments de même source primaire »).
    tetes: dict = {}
    for n in classes:
        g = n.get("groupe") if n.get("groupe") in par_ref else n["ref"]
        if g in tetes:
            tetes[g]["aussi"].append(n["ref"])
        else:
            tetes[g] = {"note": n, "aussi": []}
    out = [{"ref": t["note"]["ref"], "titre": par_ref[t["note"]["ref"]]["titre"], "url": par_ref[t["note"]["ref"]]["url"],
            "hypothese": str(t["note"].get("hypothese") or ""), "raison": str(t["note"].get("raison") or ""),
            "score": score(t["note"], par_ref[t["note"]["ref"]]["url"], graphe), "aussi": t["aussi"]}
           for t in list(tetes.values())[:PROPOSES]]
    if notes:
        d = SORTIE / graphe
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{L.get('envoi') or time.strftime('%Y-%m-%d')}.json").write_text(json.dumps(
            {"graphe": graphe, "envoi": L.get("envoi"), "cout_usd": budget.depense, "proposes": out, "notes": notes},
            ensure_ascii=False, indent=1), encoding="utf-8")
    return out

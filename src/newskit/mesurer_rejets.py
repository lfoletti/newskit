#!/usr/bin/env python3
"""Pourquoi le contrôle de la pyramide retire-t-il une phrase ? Lecture du journal des rejets (synthese.journal_rejets),
sans appel au modèle. Chaque fait introuvable est classé :

    reformule     le fait est dans la preuve citée sous une autre forme (date « 12 juillet 2024 » pour 2024-07-12,
                  nombre « 35 000 000 » pour 35000000, « 3 % » pour 3, pluriel ou tiret) : rejet à tort du contrôle
    libelle       le fait est le libellé de la notion elle-même (ou un de ses mots) : rejet à tort
    cite_ailleurs le fait est dans une autre preuve de la même notion, non citée : citation fausse, matière présente
    absent        le fait n'est nulle part dans la matière de la notion : le modèle brode (mémoire ou déduction)
    sans_preuve   la phrase ne citait aucune preuve

Une phrase « à tort » n'a que des faits reformulés, de libellé ou cités ailleurs ; une phrase « brodée » a au moins un
fait absent. Né de la nuit du 10 au 11.10.2026 : 705 phrases retirées sur 1 527 (46 %), sans moyen de dire lesquelles.

    python3 -m newskit.mesurer_rejets --graphe <graphe> [--depuis "2026-10-11 14"] [--exemples 8]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import synthese  # noqa: E402

MOIS_FR = [
    "janvier",
    "fevrier",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
]
MOIS_EN = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
MOIS_DE = [
    "januar",
    "februar",
    "marz",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "dezember",
]


def formes_de_dates(texte: str) -> set[str]:
    """Toutes les écritures des dates lues dans `texte` (déjà plié) : AAAA-MM-JJ, JJ.MM.AAAA, JJ/MM/AAAA, « 12 juillet 2024 »,
    « july 12, 2024 », « 12. juli 2024 » ; rend les nombres et formes qu'une phrase peut en reprendre."""
    out: set[str] = set()
    noms = {m: i + 1 for liste in (MOIS_FR, MOIS_EN, MOIS_DE) for i, m in enumerate(liste)}

    def ajouter(j: int, m: int, a: int) -> None:
        out.update(
            {
                f"{a:04d}-{m:02d}-{j:02d}",
                f"{j:02d}.{m:02d}.{a:04d}",
                f"{j}.{m}.{a}",
                f"{j:02d}/{m:02d}/{a:04d}",
                str(a),
                str(j),
                f"{j:02d}",
                str(m),
                f"{m:02d}",
            }
        )

    for a, m, j in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", texte):
        ajouter(int(j), int(m), int(a))
    for j, m, a in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", texte):
        ajouter(int(j), int(m), int(a))
    for j, mois, a in re.findall(r"\b(\d{1,2})(?:er|st|nd|rd|th)?\.?\s+([a-z]+)\s+(\d{4})\b", texte):
        if mois in noms:
            ajouter(int(j), noms[mois], int(a))
    for mois, j, a in re.findall(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", texte):
        if mois in noms:
            ajouter(int(j), noms[mois], int(a))
    return out


def formes_de_nombres(texte: str) -> set[str]:
    """Les nombres de `texte` sous leurs écritures : sans séparateur de milliers (espace, fine, apostrophe), avec
    « million(s) » et « milliard(s) » développés, décimales à la virgule ou au point."""
    out: set[str] = set()
    t = texte.replace(" ", " ").replace(" ", " ")
    for m in re.findall(r"\d{1,3}(?:[ '’]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", t):
        brut = re.sub(r"[ '’]", "", m).replace(",", ".")
        out.add(brut)
        if "." in brut:
            out.add(brut.replace(".", ","))
            out.add(brut.rstrip("0").rstrip("."))
    for m, mot in re.findall(r"(\d+(?:[.,]\d+)?)\s*(millions?|milliards?|million|billion|mio|mrd)\b", t):
        v = float(m.replace(",", "."))
        f = 1e6 if mot.startswith(("mil", "mio")) and not mot.startswith("milliard") else 1e9
        if mot in ("billion", "mrd") or mot.startswith("milliard"):
            f = 1e9
        out.add(str(int(v * f)))
    return out


def matiere(preuves: list[dict], indices: list[int] | None = None) -> str:
    sel = preuves if indices is None else [preuves[i - 1] for i in indices if 0 < i <= len(preuves)]
    return ig._plier(" ".join(p["texte"] + " " + str(p.get("quand") or "") for p in sel))


def classer_fait(fait: str, label: str, citees: str, toutes: str) -> str:
    f = ig._plier(fait).strip(".-")
    lab = ig._plier(label)
    if f and (f in lab or f in {m.strip(".-") for m in re.findall(r"[\w][\w.+-]*", lab)}):
        return "libelle"
    for texte, verdict in ((citees, "reformule"), (toutes, "cite_ailleurs")):
        if f in texte.replace(",", "."):
            return verdict
        if re.fullmatch(r"[\d.]+", f) and (f in formes_de_nombres(texte) or f in formes_de_dates(texte)):
            return verdict
        # pluriel, tiret, point final : « Regulations » pour « Regulation », « opt-out » pour « opt out »
        racine = f.rstrip("s").replace("-", " ").replace("'", " ")
        if len(racine) >= 4 and racine in texte.replace("-", " ").replace("'", " "):
            return verdict
    return "absent"


def lire_journal(graphe: str, depuis: str | None) -> list[dict]:
    p = synthese.journal_rejets(graphe)
    if not p.exists():
        return []
    out = []
    for ligne in p.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            e = json.loads(ligne)
            if not depuis or e.get("quand", "") >= depuis:
                out.append(e)
    return out


def rejouer(graphe: str, entrees: list[dict], exemples: int) -> int:
    """Le contrôle d'aujourd'hui (synthese.verifier, matiere_de) rejoué sur les phrases retirées du journal, sans appel :
    la mesure d'un changement du contrôle. Les étiquettes des preuves sont relues du graphe."""
    if not entrees:
        print("  journal vide")
        return 1
    G = synthese.charger_graphe(graphe)
    tenues, retirees, encore = 0, 0, Counter()
    ex_tenues, ex_retirees = [], []
    for e in entrees:
        n = e["notion"]
        pr = [synthese.annoter_preuve(G, dict(p)) for p in e["preuves"]]
        for r in e["retirees"]:
            retirees += 1
            if not r["preuves"]:
                encore["(aucune preuve)"] += 1
                ex_retirees.append((e["label"], r["texte"], ["(aucune preuve)"]))
                continue
            citees = [pr[i - 1] for i in r["preuves"] if 0 < i <= len(pr)]
            manque = synthese.verifier(r["texte"], synthese.matiere_de(G, n, citees))
            if manque:
                for f in manque:
                    encore[f] += 1
                ex_retirees.append((e["label"], r["texte"], manque))
            else:
                tenues += 1
                ex_tenues.append((e["label"], r["texte"], r["manque"]))
    print(
        f"  {graphe} : {retirees} phrase(s) retirée(s) au journal ; avec le contrôle actuel, {tenues} tiendraient "
        f"({100 * tenues / max(retirees, 1):.0f} %), {retirees - tenues} resteraient retirées"
    )
    print("  faits encore introuvables : " + ", ".join(f"{k} {v}" for k, v in encore.most_common(25)))
    print("\n  — tiendraient désormais (faits qui manquaient hier) —")
    for label, texte, manque in ex_tenues[:exemples]:
        print(f"  [{label}] {texte[:200]}\n      hier : {', '.join(manque)}")
    print("\n  — encore retirées —")
    for label, texte, manque in ex_retirees[:exemples]:
        print(f"  [{label}] {texte[:200]}\n      manque : {', '.join(manque)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--depuis", help="ne lire que les entrées datées à partir de « AAAA-MM-JJ HH:MM »")
    ap.add_argument("--exemples", type=int, default=6, help="exemples montrés par catégorie")
    ap.add_argument(
        "--rejouer",
        action="store_true",
        help="rejouer le contrôle actuel sur les phrases du journal (charge le graphe) : "
        "combien tiendraient aujourd'hui, et ce qui manque encore",
    )
    a = ap.parse_args()
    if a.rejouer:
        return rejouer(a.graphe, lire_journal(a.graphe, a.depuis), a.exemples)
    entrees = lire_journal(a.graphe, a.depuis)
    if not entrees:
        print(f"  {a.graphe} : journal des rejets vide ({synthese.journal_rejets(a.graphe)})")
        return 1
    faits_par: Counter = Counter()
    phrases_par: Counter = Counter()
    exemples: dict[str, list] = {}
    notions, gardees, retirees = len(entrees), 0, 0
    for e in entrees:
        gardees += e.get("gardees", 0)
        toutes = matiere(e["preuves"])
        for r in e["retirees"]:
            retirees += 1
            if not r["preuves"] or r["manque"] == ["(aucune preuve)"]:
                phrases_par["sans_preuve"] += 1
                exemples.setdefault("sans_preuve", []).append((e["label"], r["texte"], []))
                continue
            citees = matiere(e["preuves"], r["preuves"])
            verdicts = [(f, classer_fait(f, e["label"], citees, toutes)) for f in r["manque"]]
            for _, v in verdicts:
                faits_par[v] += 1
            v_phrase = "brodee" if any(v == "absent" for _, v in verdicts) else "a_tort"
            phrases_par[v_phrase] += 1
            exemples.setdefault(v_phrase, []).append((e["label"], r["texte"], verdicts))
    total = gardees + retirees
    print(
        f"  {a.graphe} : {notions} notion(s) au journal, {retirees} phrase(s) retirée(s) sur {total} ({100 * retirees / max(total, 1):.0f} %)"
    )
    print(
        "  par phrase : "
        + ", ".join(f"{k} {v} ({100 * v / max(retirees, 1):.0f} %)" for k, v in phrases_par.most_common())
    )
    print("  par fait introuvable : " + ", ".join(f"{k} {v}" for k, v in faits_par.most_common()))
    for cat in ("a_tort", "brodee", "sans_preuve"):
        if not exemples.get(cat):
            continue
        print(f"\n  — {cat} —")
        for label, texte, verdicts in exemples[cat][: a.exemples]:
            print(f"  [{label}] {texte[:220]}")
            if verdicts:
                print("      " + " ; ".join(f"{f} → {v}" for f, v in verdicts))
    return 0


if __name__ == "__main__":
    sys.exit(main())

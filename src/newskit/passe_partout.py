#!/usr/bin/env python3
"""Le passe-partout d'un graphe : les paragraphes qu'une source répète d'une fiche à l'autre (encadrés, pieds de page,
liens « à lire aussi », présentations d'institution), à sauter à la lecture des fiches — passages, preuves des synthèses,
index — sans toucher aux fiches elles-mêmes.

Mesuré le 10.10.2026 sur un graphe de conformité (porteur : « l'ingestion est-elle calibrée pour ne garder que la substance ? ») :
4 488 paragraphes sur 46 640 (9,6 %) répétés dans cinq fiches ou plus, 13,5 % dans les flux institutionnels (pages de la
Commission : « The EU's approach to artificial intelligence promotes excellence and trust… », liens DigitalEU, actualités
connexes), 0 % dans les substacks et les pages de l'éclaireur. Ces paragraphes nomment des notions (AI Office, trustworthy
AI) et entraient comme preuves et comme passages.

    python3 -m newskit.passe_partout --graphe <graphe>            # calcule et écrit graphe/<nom>/passe_partout.json
    python3 -m newskit.passe_partout --graphe <graphe> --etat     # ce qui est écrit, par source
    python3 -m newskit.passe_partout --graphe <graphe> --montrer 12

Déterministe, sans appel au modèle ; relancé par la routine après chaque composition. Les textes de référence (articles de
loi) ne sont pas comptés : leurs répétitions sont de la substance (formules finales d'un règlement)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402

SEUIL = 5            # un paragraphe présent dans autant de fiches d'une même source est du passe-partout
MOTS_MIN = 6         # en dessous, un paragraphe n'est pas jugé (il est de toute façon sauté par la lecture)


def normaliser(texte: str) -> str:
    return re.sub(r"\s+", " ", texte.strip())


def empreinte(texte: str) -> str:
    return hashlib.sha1(normaliser(texte).encode("utf-8")).hexdigest()[:16]


def chemin(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "passe_partout.json"


def calculer(graphe: str) -> dict:
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    ig.PASSE_PARTOUT.clear()                                   # compter les fiches telles quelles, sans le filtre
    comptes: dict[str, Counter] = defaultdict(Counter)
    apercus: dict[str, str] = {}
    total = 0
    for d in cfg.dossiers_du_graphe(graphe):
        dossier = RACINE / d
        if not dossier.is_dir() or "/textes" in d or d.startswith("sources/textes"):
            continue
        source = "/".join(d.split("/")[1:3])
        for f in dossier.rglob("*.md"):
            if f.name.startswith("_"):
                continue
            vus: set[str] = set()
            for _, texte in ig._paragraphes(f):
                if len(texte.split()) < MOTS_MIN:
                    continue
                e = empreinte(texte)
                if e in vus:
                    continue
                vus.add(e)
                total += 1
                comptes[source][e] += 1
                apercus.setdefault(e, normaliser(texte)[:120])
    retenus = {e: n for c in comptes.values() for e, n in c.items() if n >= SEUIL}
    par_source = {s: {"paragraphes": sum(c.values()), "passe_partout": sum(n for e, n in c.items() if n >= SEUIL)}
                  for s, c in comptes.items()}
    return {"graphe": graphe, "calcule_le": time.strftime("%Y-%m-%d %H:%M"), "seuil": SEUIL, "paragraphes": total,
            "retenus": len(retenus), "occurrences": sum(retenus.values()), "par_source": par_source,
            "empreintes": {e: {"fiches": n, "apercu": apercus[e]} for e, n in sorted(retenus.items(), key=lambda kv: -kv[1])}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--etat", action="store_true", help="lire ce qui est écrit, sans recalculer")
    ap.add_argument("--montrer", type=int, default=0, metavar="N", help="les N paragraphes les plus répétés")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    p = chemin(g)
    if a.etat:
        if not p.exists():
            print(f"  {g} : pas de passe-partout calculé ({p.name} absent)")
            return 1
        r = json.loads(p.read_text(encoding="utf-8"))
    else:
        r = calculer(g)
        p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {g} : {r['paragraphes']} paragraphes lus, {r['retenus']} passe-partout ({r['occurrences']} occurrences, "
          f"{100 * r['occurrences'] / max(r['paragraphes'], 1):.1f} %), seuil {r['seuil']} fiches, calculé le {r['calcule_le']}")
    for s, v in sorted(r["par_source"].items(), key=lambda kv: -kv[1]["passe_partout"]):
        if v["passe_partout"]:
            print(f"    {s:28} {v['paragraphes']:6} paragraphes, passe-partout {100 * v['passe_partout'] / max(v['paragraphes'], 1):4.1f} %")
    for e, v in list(r["empreintes"].items())[:a.montrer]:
        print(f"    ×{v['fiches']:3}  {v['apercu']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

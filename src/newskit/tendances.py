#!/usr/bin/env python3
"""Les tendances d'un graphe (mainteneur, 15.11.2026 : « accueil sur "trending topics" »).

Un graphe qui déclare `tendances: {jours: 30, max: 8, hors: [...]}` garde dans `tendances.json` les notions que
plusieurs sources indépendantes ont attestées pendant la fenêtre, pour l'accueil du média. Aucun appel au modèle : le
compte se lit du graphe du cerveau, par les attestations datées de chaque notion (diachronie.py).

La règle :

- la fenêtre compte `jours` jours et finit au jour de référence, compris (aujourd'hui, ou `--jusqu`) ;
- un papier compte pour une source, un dossier d'articles pour une seule : cent billets d'un même blog repris d'un
  coup n'en font pas une tendance ;
- une notion entre avec deux sources au moins ; l'ordre : le nombre de sources, puis de fiches, puis le libellé ;
- un libellé qui est aussi celui d'un auteur, d'une entité ou d'une publication du graphe n'est pas une notion du
  domaine ; le média écarte les autres noms sous `hors`.

    python3 -m newskit.tendances --graphe <graphe>                     # écrit tendances.json du graphe
    python3 -m newskit.tendances --graphe <graphe> --jusqu 2026-10-09  # la fenêtre finit ce jour-là
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

JOURS = 30  # la fenêtre, sans `jours` au registre
JOURS_ADMIS = 365
MAX = 8  # tendances au plus, sans `max` au registre
MAX_ADMIS = 50
MIN_SOURCES = 2  # sources indépendantes qui font une tendance
FICHIER = "tendances.json"
NOMS = ("auteur", "entite", "publication")  # les genres de nœud dont le libellé est un nom, pas une notion


def declaration(decl) -> dict | None:
    """La déclaration `tendances` d'un graphe, complétée : {jours, max, hors}. None sans déclaration ; ValueError si
    elle est mal formée."""
    if decl is None:
        return None
    if not isinstance(decl, dict) or set(decl) - {"jours", "max", "hors"}:
        raise ValueError("{jours, max, hors} attendus")
    jours, maximum, hors = decl.get("jours", JOURS), decl.get("max", MAX), decl.get("hors", [])
    if isinstance(jours, bool) or not isinstance(jours, int) or not 1 <= jours <= JOURS_ADMIS:
        raise ValueError(f"jours : un entier de 1 à {JOURS_ADMIS}")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= MAX_ADMIS:
        raise ValueError(f"max : un entier de 1 à {MAX_ADMIS}")
    if not isinstance(hors, list) or not all(isinstance(h, str) for h in hors):
        raise ValueError("hors : une liste de noms")
    return {"jours": jours, "max": maximum, "hors": hors}


def _cle(libelle) -> str:
    return " ".join(str(libelle or "").casefold().split())


def compter(
    G,
    jusqu: str,
    jours: int = JOURS,
    maximum: int = MAX,
    hors: list[str] | tuple[str, ...] = (),
    min_sources: int = MIN_SOURCES,
) -> list[dict]:
    """Les tendances du graphe G (lu du cerveau) au jour `jusqu` (AAAA-MM-JJ) : [{notion, sources, fiches}]."""
    from newskit import diachronie  # noqa: PLC0415 — charge networkx

    debut = (date.fromisoformat(jusqu) - timedelta(days=jours)).isoformat()
    idx = diachronie.index_fiches(G)
    ecartes = {_cle(d.get("name")) for _, d in G.nodes(data=True) if d.get("node_type") in NOMS}
    ecartes |= {_cle(h) for h in hors}
    lues = []
    for n, d in G.nodes(data=True):
        if d.get("node_type") != "concept" or _cle(d.get("name")) in ecartes:
            continue
        fiches = [a["fiche"] for a in diachronie.attestations(G, n, idx) if debut < a["date"] <= jusqu]
        sources = {f if idx[f].get("node_type") == "papier" else str(Path(f).parent) for f in fiches}
        if len(sources) >= min_sources:
            lues.append({"notion": str(d.get("name") or n), "sources": len(sources), "fiches": len(fiches)})
    lues.sort(key=lambda t: (-t["sources"], -t["fiches"], t["notion"]))
    return lues[:maximum]


def lire(chemin: Path) -> list[dict]:
    """Les tendances gardées ; aucune si le fichier manque ou ne se lit pas."""
    try:
        d = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return d["tendances"] if isinstance(d, dict) and isinstance(d.get("tendances"), list) else []


def charger(graphe: str):
    """Le graphe du cerveau ; rien sans cerveau (les tendances gardées restent)."""
    from newskit import graphe_cerveau as gc  # noqa: PLC0415 — charge networkx

    try:
        return gc.charger(graphe)
    except gc.CerveauAbsent:
        return None


def aujourd_hui() -> str:
    return date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    from newskit import config_sources as cfg  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--jusqu", help="le jour où finit la fenêtre, AAAA-MM-JJ (défaut : aujourd'hui)")
    a = ap.parse_args(argv)
    decl = declaration(cfg.graphe_declare(a.graphe).get("tendances"))
    if decl is None:
        print(f"  {a.graphe} : pas de tendances (tendances: {{jours, max}} au registre)")
        return 0
    G = charger(a.graphe)
    if G is None:
        print(f"  {a.graphe} : graphe absent, tendances inchangées")
        return 0
    jusqu = a.jusqu or aujourd_hui()
    lues = compter(G, jusqu, decl["jours"], decl["max"], decl["hors"])
    chemin = cfg.chemins_du_graphe(a.graphe)["base"] / FICHIER
    chemin.parent.mkdir(parents=True, exist_ok=True)
    etat = {"jusqu": jusqu, "jours": decl["jours"], "tendances": lues}
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"  {a.graphe} : {len(lues)} tendance(s) : "
        + ", ".join(f"{t['notion']} ({t['sources']} sources)" for t in lues)
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

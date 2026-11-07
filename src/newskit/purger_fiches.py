#!/usr/bin/env python3
"""Retire du graphe les fiches qu'on a ôtées du corpus (publicités, mainteneur, 03.11.2026) : leurs nœuds de structure, les
relations qu'elles attestent, et les notions qui ne tiennent plus que par elles. Une notion attestée ailleurs reste et
prend pour source une fiche qui l'atteste encore. Fichiers touchés, dans chaque graphe : graphe.json (et son .gz),
squelette.json (et son .gz), structure.json, partage.json.

Usage :
    python3 -m newskit.purger_fiches <chemin de fiche> [...]
    python3 -m newskit.purger_fiches --publicites      # les fiches que fiche.publicitaire juge publicitaires
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)


def _prefixe(chemin: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", chemin.lower().removesuffix(".md")).strip("_")


def purger_graphe(d: dict, fiches: set[str]) -> tuple[int, int]:
    """Purge un graphe nœuds-liens en place ; rend (nœuds retirés, liens retirés)."""
    cle = "links" if "links" in d else "edges"
    liens = d.get(cle) or []
    prefixes = tuple(_prefixe(f) for f in fiches)

    def noeud_de_fiche(n: dict) -> bool:
        i = str(n.get("id") or "")
        return i.startswith(prefixes)

    garder_liens = [e for e in liens if str(e.get("source_file") or "") not in fiches]
    retires_liens = len(liens) - len(garder_liens)
    a_fiche = {n["id"] for n in d["nodes"] if noeud_de_fiche(n)}
    garder_liens = [e for e in garder_liens if e.get("source") not in a_fiche and e.get("target") not in a_fiche]
    retires_liens = len(liens) - len(garder_liens)
    source_de: dict[str, str] = {}
    for e in garder_liens:
        for b in (e.get("source"), e.get("target")):
            source_de.setdefault(b, str(e.get("source_file") or ""))
    garder = []
    for n in d["nodes"]:
        if n["id"] in a_fiche:
            continue
        if str(n.get("source_file") or "") in fiches:
            if n["id"] not in source_de or not source_de[n["id"]]:
                continue                                   # la notion ne tenait que par la fiche retirée
            n["source_file"] = source_de[n["id"]]
        garder.append(n)
    retires = len(d["nodes"]) - len(garder)
    d["nodes"], d[cle] = garder, garder_liens
    return retires, retires_liens


def purger_couche2(fiches: set[str]) -> None:
    """Le lot d'extraction de chaque fiche (couche2/fiches/<id>.json) et son empreinte : sans cela, la fusion suivante
    remet les notions de la fiche dans le graphe (fusionner_graphe lit tous les lots, fiche présente ou non)."""
    ids = {_prefixe(f) for f in fiches}
    for c2 in sorted((RACINE / "graphe").glob("*/couche2")):
        n = 0
        for i in ids:
            lot = c2 / "fiches" / f"{i}.json"
            if lot.exists():
                lot.unlink()
                n += 1
        emp = c2 / "empreintes.json"
        m = 0
        if emp.exists():
            d = json.loads(emp.read_text(encoding="utf-8"))
            m = sum(1 for f in fiches if d.pop(f, None) is not None)
            if m:
                emp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        if n or m:
            print(f"{c2.relative_to(RACINE)} : {n} lots, {m} empreintes retirés")


def purger(fiches: set[str]) -> None:
    purger_couche2(fiches)
    for p in sorted((RACINE / "graphe").glob("*/*.json")):
        if p.name not in ("graphe.json", "squelette.json", "structure.json", "partage.json"):
            continue
        texte = p.read_text(encoding="utf-8")
        if not any(f in texte for f in fiches):
            continue
        d = json.loads(texte)
        if p.name == "partage.json":
            n = sum(1 for f in fiches if d.pop(f, None) is not None)
            print(f"{p.relative_to(RACINE)} : {n} entrées retirées")
        else:
            nr, lr = purger_graphe(d, fiches)
            print(f"{p.relative_to(RACINE)} : {nr} nœuds, {lr} liens retirés")
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1 if "\n " in texte[:300] else None), encoding="utf-8")
        gz = p.with_suffix(".json.gz")
        if gz.exists():
            with open(p, "rb") as src, gzip.open(gz, "wb") as dst:
                shutil.copyfileobj(src, dst)


def publicites() -> set[str]:
    from newskit import fiche  # noqa: PLC0415
    out = set()
    for p in (RACINE / "sources").rglob("*.md"):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^title:\s*(.*)$", t, re.M)
        corps = re.sub(r"^\s*#[^\n]*\n+", "", t.split("---", 2)[-1] if t.startswith("---") else t)
        if fiche.publicitaire((m.group(1) if m else "").strip("\"' "), corps):
            out.add(str(p.relative_to(RACINE)))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--publicites"]:
        cibles = publicites()
        for c in sorted(cibles):
            print("publicité :", c)
            (RACINE / c).unlink()
    else:
        cibles = set(sys.argv[1:])
    purger(cibles)

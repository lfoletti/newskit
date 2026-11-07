#!/usr/bin/env python3
"""La naissance d'un graphe (CHANTIER_GENERIQUE.md § 4.1 ; pilote CortexDB, phase B, 22.10.2026).

Trois gardes, en calcul pur, et l'état dans `graphe/<g>/naissance.json` :
- le jeu de questions est **scellé avant toute collecte** (empreinte, adresses citées, date) : la mesure de naissance
  se fait sur des questions écrites sans le graphe, et un jeu retouché après le sceau se voit (`intact`) ;
- **une page citée par le sceau n'entre pas** : sinon la mesure dirait « suffisant » parce que le graphe a recopié le
  banc (`contaminees` ; les fiches touchées partent en quarantaine, hors du graphe, jamais effacées) ;
- **la dépense se lit au registre des coûts**, du début de la naissance (composition du jeu comprise) : chaque étape
  payante demande `reste` avant de partir.

    python3 -m newskit.naissance commencer --graphe <graphe> --plafond-usd 15 --plafond-fiches 80
    python3 -m newskit.naissance sceller --graphe <graphe>        # après banc_themes.composer, avant toute collecte
    python3 -m newskit.naissance quarantaine --graphe <graphe>    # après chaque collecte, avant l'extraction
    python3 -m newskit.naissance etat --graphe <graphe>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)


def adresse(url: str) -> str:
    """Une adresse comparable : sans schéma, sans www., sans fragment, sans barre finale."""
    u = re.sub(r"^https?://", "", (url or "").strip().casefold())
    u = re.sub(r"^www\.", "", u).split("#", 1)[0]
    return u.rstrip("/")


def adresses_citees(q: dict) -> set[str]:
    texte = " ".join(str(q.get(k) or "") for k in ("citation", "source", "url"))
    return {a for u in re.findall(r"https?://[^\s\"'<>]+", texte) if (a := adresse(u.rstrip(".,;)")))}


def sceller(jeu: list[dict], quand: str) -> dict:
    if not jeu:
        raise ValueError("un jeu vide ne se scelle pas")
    return {
        "questions": len(jeu),
        "scelle_le": quand,
        "empreinte": empreinte(jeu),
        "indices": [q.get("i") for q in jeu],
        "adresses": sorted(set().union(*(adresses_citees(q) for q in jeu))),
    }


def completer(jeu: list[dict], sceaux: list[dict], fiches: list[tuple[str, str]], quand: str) -> dict:
    """Scelle les questions ajoutées après une collecte. Écrites sans le graphe, elles peuvent pourtant citer une page
    déjà ingérée : celles-là sont `exclues` de la mesure (le graphe aurait recopié le banc)."""
    deja = {i for s in sceaux for i in s.get("indices", [])}
    neuves = [q for q in jeu if q.get("i") not in deja]
    s = sceller(neuves, quand)
    ingerees = {adresse(url) for _, url in fiches if url}
    return {**s, "exclues": [q.get("i") for q in neuves if adresses_citees(q) & ingerees]}


def reunir(sceaux: list[dict]) -> dict:
    """Les adresses de tous les sceaux, pour la quarantaine."""
    return {"adresses": sorted({a for s in sceaux for a in s["adresses"]})}


def mesurables(jeu: list[dict], sceaux: list[dict]) -> list[dict]:
    exclues = {i for s in sceaux for i in s.get("exclues", [])}
    return [q for q in jeu if q.get("i") not in exclues]


def empreinte(jeu: list[dict]) -> str:
    lignes = sorted(
        json.dumps(
            {k: q.get(k) for k in ("question", "attendu", "citation", "source")}, ensure_ascii=False, sort_keys=True
        )
        for q in jeu
    )
    return hashlib.sha256("\n".join(lignes).encode()).hexdigest()[:16]


def intact(jeu: list[dict], sceau: dict) -> bool:
    """Le jeu n'a pas été retouché depuis le sceau, sur les seules questions que ce sceau couvre."""
    if "indices" in sceau:
        jeu = [q for q in jeu if q.get("i") in set(sceau["indices"])]
    return bool(jeu) and empreinte(jeu) == sceau["empreinte"]


def contaminees(fiches: list[tuple[str, str]], sceau: dict) -> list[str]:
    """(chemin, url) des fiches → les chemins dont l'adresse est citée par le sceau."""
    citees = set(sceau["adresses"])
    return [chemin for chemin, url in fiches if url and adresse(url) in citees]


def depense(lignes: list[dict], graphe: str, depuis: str) -> float:
    return round(
        sum(
            float(l.get("cout_usd") or 0)
            for l in lignes
            if l.get("graphe") == graphe and str(l.get("quand") or "") >= depuis
        ),
        4,
    )


def reste(plafond: float, depense_: float) -> float:
    return round(max(0.0, plafond - depense_), 4)


# --- entrées-sorties --------------------------------------------------------------------------------------------------


def chemin_etat(graphe: str) -> Path:
    return RACINE / "graphe" / graphe / "naissance.json"


def lire_etat(graphe: str) -> dict:
    p = chemin_etat(graphe)
    if not p.exists():
        raise SystemExit(f"{graphe} : pas de naissance commencée (naissance.py commencer)")
    return json.loads(p.read_text(encoding="utf-8"))


def ecrire_etat(graphe: str, etat: dict) -> None:
    chemin_etat(graphe).write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")


def depense_du_registre(graphe: str, depuis: str) -> float:
    from newskit import couts  # noqa: PLC0415

    lignes = []
    if couts.REGISTRE.exists():
        for l in couts.REGISTRE.read_text(encoding="utf-8").splitlines():
            try:
                lignes.append(json.loads(l))
            except json.JSONDecodeError:
                continue
    return depense(lignes, graphe, depuis)


def mettre_en_quarantaine(chemins: list[str], racine: Path, cible: Path) -> list[str]:
    """Déplace les fiches (chemin relatif à `racine` ou absolu) dans `cible` ; rend leurs chemins relatifs, pour l'état."""
    cible.mkdir(parents=True, exist_ok=True)
    notes = []
    for c in chemins:
        p = Path(c) if Path(c).is_absolute() else racine / c
        shutil.move(str(p), str(cible / p.name))
        notes.append(p.relative_to(racine).as_posix())
    return notes


def fiches_du_graphe(graphe: str) -> list[tuple[str, str]]:
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import fiche  # noqa: PLC0415

    out = []
    for d in cfg.dossiers_du_graphe(graphe):
        for p in sorted(Path(d).rglob("*.md")):
            if p.name.startswith("_"):
                continue
            entete, _ = fiche.lire_fichier(p)
            out.append((str(p), str(entete.get("url") or "")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("commencer", "sceller", "completer", "plafond", "quarantaine", "etat"))
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--plafond-usd", type=float, default=15.0)
    ap.add_argument("--plafond-fiches", type=int, default=80)
    a = ap.parse_args()
    g = a.graphe
    if a.action == "commencer":
        if chemin_etat(g).exists():
            raise SystemExit(f"{g} : naissance déjà commencée ({chemin_etat(g)})")
        ecrire_etat(
            g,
            {
                "commence_le": time.strftime("%Y-%m-%d %H:%M:%S"),
                "plafond_usd": a.plafond_usd,
                "plafond_fiches": a.plafond_fiches,
                "sceau": None,
                "quarantaine": [],
            },
        )
        print(f"{g} : naissance commencée, plafond {a.plafond_usd} $ et {a.plafond_fiches} fiches")
        return 0
    etat = lire_etat(g)
    from newskit import banc_themes  # noqa: PLC0415

    p_jeu, _ = banc_themes.chemins(g)
    jeu = json.loads(p_jeu.read_text(encoding="utf-8")) if p_jeu.exists() else []
    if a.action == "sceller":
        if etat["sceau"]:
            raise SystemExit(f"{g} : jeu déjà scellé le {etat['sceau']['scelle_le']}")
        if fiches_du_graphe(g):
            raise SystemExit(f"{g} : des fiches existent déjà, le jeu ne peut plus être scellé avant la collecte")
        etat["sceau"] = sceller(jeu, time.strftime("%Y-%m-%d %H:%M:%S"))
        ecrire_etat(g, etat)
        print(
            f"{g} : {etat['sceau']['questions']} questions scellées, {len(etat['sceau']['adresses'])} adresses citées"
        )
        return 0
    if not etat["sceau"]:
        raise SystemExit(f"{g} : jeu non scellé")
    sceaux = [etat["sceau"], *etat.get("complements", [])]
    if a.action == "plafond":  # une décision du porteur, datée
        etat.setdefault("plafonds", []).append(
            {"quand": time.strftime("%Y-%m-%d %H:%M:%S"), "avant": etat["plafond_usd"], "apres": a.plafond_usd}
        )
        etat["plafond_usd"] = a.plafond_usd
        ecrire_etat(g, etat)
        print(f"{g} : plafond porté à {a.plafond_usd} $")
        return 0
    if a.action == "completer":
        c = completer(jeu, sceaux, fiches_du_graphe(g), time.strftime("%Y-%m-%d %H:%M:%S"))
        etat.setdefault("complements", []).append(c)
        ecrire_etat(g, etat)
        print(
            f"{g} : {c['questions']} question(s) ajoutée(s) scellée(s), {len(c['exclues'])} exclue(s) (page déjà ingérée)"
        )
        return 0
    if a.action == "quarantaine":
        touchees = contaminees(fiches_du_graphe(g), reunir(sceaux))
        cible = RACINE / "cache" / "naissance" / g / "quarantaine"
        etat["quarantaine"] += mettre_en_quarantaine(touchees, RACINE, cible)
        ecrire_etat(g, etat)
        print(f"{g} : {len(touchees)} fiche(s) citée(s) par le sceau mise(s) en quarantaine ({cible})")
        return 0
    d = depense_du_registre(g, etat["commence_le"])
    n = len(fiches_du_graphe(g))
    print(
        json.dumps(
            {
                "graphe": g,
                "depense_usd": d,
                "reste_usd": reste(etat["plafond_usd"], d),
                "fiches": n,
                "reste_fiches": max(0, etat["plafond_fiches"] - n),
                "jeu_intact": all(intact(jeu, s) for s in sceaux),
                "questions_mesurables": len(mesurables(jeu, sceaux)),
                "quarantaine": len(etat["quarantaine"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

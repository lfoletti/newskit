#!/usr/bin/env python3
"""Le partage des sources (GV2 de CHANTIER_GOUVERNANCE.md, mainteneur, 1er novembre 2026).

Chaque fiche des graphes du socle (les graphes `avant` du fichier des thèmes) reçoit une destination : un thème du
graphe fusionné (`gouvernance:<clé>`) ou l'archive, selon les thèmes et l'archive du fichier (D8). Une fiche du graphe
voisin (--voisin) passe au graphe fusionné si elle traite de la sécurité des agents (`gouvernance:<--theme-agents>`),
sinon elle reste. Une réponse
manquante ou hors liste donne `a_revoir`, jamais une destination devinée.

Le rangement se fait fiche par fiche, sur le titre et le début du texte, par lots, au modèle de lecture du banc ; il est
gardé (partage.json) et un second passage ne repaie que les fiches neuves ou à revoir. Le tableau propose chaque source
entière quand ses fiches vont presque toutes au même endroit (seuil 80 %), et fiche par fiche sinon : c'est lui que le mainteneur
relit.

    python3 -m newskit.partage_sources --themes <thèmes.yaml> --voisin <graphe> --theme-agents <clé> --controle
    python3 -m newskit.partage_sources --themes <thèmes.yaml> --voisin <graphe> --theme-agents <clé>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

import yaml

from newskit.commun import RACINE  # noqa: E402

from newskit import banc_croise as bc  # noqa: E402

LOT = 80
SEUIL = 0.8
DEBUT = 300
FINALES = {"archive", "reste", "a_revoir"}
SCHEMA = {"type": "object", "required": ["rangement"], "properties": {"rangement": {"type": "array", "items": {
    "type": "object", "required": ["i", "destination"],
    "properties": {"i": {"type": "integer"}, "destination": {"type": "string"}}}}}}


def dossier_sortie(racine: Path = RACINE) -> Path:
    return racine / "graphe" / "partage_gouvernance"


def _lire(p: Path) -> tuple[str, str]:
    from newskit import fiche  # noqa: PLC0415
    entete, corps = fiche.lire_fichier(p)
    lignes = [x for x in corps.splitlines() if x.strip() and not x.startswith("#")]
    return str(entete.get("title") or p.stem), " ".join(lignes)[:DEBUT]


def inventaire(sources: dict[str, list[tuple[str, str]]], racine: Path = RACINE) -> list[dict]:
    """Les fiches, graphe par graphe et source par source : chemin relatif, graphe, source, titre, début du texte."""
    out = []
    for g, liste in sources.items():
        for cle, d in liste:
            for p in sorted((racine / d).rglob("*.md")):
                if p.name.startswith("_"):
                    continue
                titre, debut = _lire(p)
                out.append({"chemin": p.relative_to(racine).as_posix(), "graphe": g, "source": cle,
                            "titre": titre, "debut": debut})
    return out


def consigne(lot: list[dict], themes: list[dict], archive: list[str], socle: bool, agents: str) -> str:
    fiches = "\n".join(f"{i}. {x['titre']} — {x['debut']}" for i, x in enumerate(lot))
    if socle:
        plan = "\n".join(f"{t['cle']} : {t['libelle']} (" + "; ".join(t["sujets"]) + ")" for t in themes)
        return ("Range chaque fiche sous le thème de la gouvernance de l'IA qu'elle traite, parmi ceux-ci :\n" + plan
                + "\n\nUne fiche qui ne relève d'aucun (par exemple : " + "; ".join(archive)
                + ", ou tout autre sujet hors de la gouvernance de l'IA) : `archive`.\n\n" + fiches
                + '\n\nRéponds en JSON : {"rangement": [{"i": <numéro>, "destination": "<clé du thème ou archive>"}, …]}.')
    return ("Ces fiches viennent d'un graphe d'ingénierie de l'IA. Dis pour chacune si elle traite de la sécurité des "
            "agents d'IA ou de leurs risques (injection de prompt, outils et MCP, permissions, exfiltration, garde-fous, "
            f"incidents) au point de servir la gouvernance de l'IA : `{agents}` ; sinon : `reste`.\n\n" + fiches
            + f'\n\nRéponds en JSON : {{"rangement": [{{"i": <numéro>, "destination": "{agents} ou reste"}}, …]}}.')


def appel_modele(delai: int) -> Callable:
    from newskit import banc  # noqa: PLC0415

    def appel(prompt: str, lot: list[dict], socle: bool) -> list[dict]:
        rep = banc.claude(prompt, SCHEMA, None, delai, modele=banc.MODELE_LECTURE)
        return (rep or {}).get("rangement", [])
    return appel


def _destination(rep: str, cles: set[str], socle: bool, agents: str) -> str:
    rep = (rep or "").strip()
    if socle:
        return f"gouvernance:{rep}" if rep in cles else "archive" if rep == "archive" else "a_revoir"
    return f"gouvernance:{agents}" if rep == agents else "reste" if rep else "a_revoir"


def ranger(fiches: list[dict], themes: list[dict], archive: list[str], appel: Callable, voisin: str, agents: str,
           lot: int = LOT, cache: Path | None = None, reposer: set[str] | None = None) -> list[dict]:
    """Une destination par fiche. Le cache (chemin → destination) garde ce qui est rangé ; `a_revoir`, et les
    destinations de `reposer` (après un sous-thème ajouté, par exemple), se reposent."""
    garde = json.loads(cache.read_text(encoding="utf-8")) if cache and cache.exists() else {}
    garde = {c: v for c, v in garde.items() if v not in (reposer or set())}
    cles = {t["cle"] for t in themes}
    dest = {x["chemin"]: garde[x["chemin"]] for x in fiches if garde.get(x["chemin"], "a_revoir") != "a_revoir"}
    for socle in (True, False):
        a_faire = [x for x in fiches if (x["graphe"] != voisin) == socle and x["chemin"] not in dest]
        for k in range(0, len(a_faire), lot):
            morceau = a_faire[k:k + lot]
            par = {r.get("i"): r.get("destination") for r in appel(consigne(morceau, themes, archive, socle, agents), morceau, socle)}
            for i, x in enumerate(morceau):
                dest[x["chemin"]] = _destination(par.get(i, ""), cles, socle, agents)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({**garde, **dest}, ensure_ascii=False, indent=0), encoding="utf-8")
            if len(a_faire) > lot:
                print(f"  {'socle' if socle else voisin} : lot {k // lot + 1} sur {-(-len(a_faire) // lot)}", flush=True)
    return [{**x, "destination": dest[x["chemin"]]} for x in fiches]


def par_source(rangees: list[dict], voisin: str, seuil: float = SEUIL) -> list[dict]:
    """Une ligne par source : effectifs par destination, thèmes, et la proposition à relire."""
    groupes: dict[tuple[str, str], list[dict]] = {}
    for x in rangees:
        groupes.setdefault((x["graphe"], x["source"]), []).append(x)
    out = []
    for (g, s), xs in groupes.items():
        c = Counter("gouvernance" if x["destination"].startswith("gouvernance:") else x["destination"] for x in xs)
        n = len(xs)
        themes = Counter(x["destination"].split(":", 1)[1] for x in xs if x["destination"].startswith("gouvernance:"))
        if g == voisin:
            prop = "gouvernance" if c["gouvernance"] >= seuil * n else "reste" if c["gouvernance"] == 0 and not c["a_revoir"] \
                else "fiche par fiche"
        else:
            prop = "gouvernance" if c["gouvernance"] >= seuil * n else "archive" if c["archive"] >= seuil * n else "mixte"
        out.append({"graphe": g, "source": s, "n": n, "gouvernance": c["gouvernance"], "archive": c["archive"],
                    "reste": c["reste"], "a_revoir": c["a_revoir"], "themes": dict(themes), "proposition": prop})
    return out


def verifier(fiches: list[dict], rangees: list[dict]) -> None:
    """Chaque fiche de l'inventaire a une destination, une seule, et connue."""
    vus = Counter(x["chemin"] for x in rangees)
    if (deux := [c for c, k in vus.items() if k > 1]):
        raise ValueError(f"{len(deux)} fiche(s) rangée(s) deux fois, dont {deux[0]}")
    if (sans := [x["chemin"] for x in fiches if x["chemin"] not in vus]):
        raise ValueError(f"{len(sans)} fiche(s) sans destination, dont {sans[0]}")
    if (mal := [x for x in rangees if x["destination"] not in FINALES and not x["destination"].startswith("gouvernance:")]):
        raise ValueError(f"destination inconnue : {mal[0]['destination']} ({mal[0]['chemin']})")


def en_markdown(rangees: list[dict], lignes: list[dict]) -> str:
    graphes = list(dict.fromkeys(x["graphe"] for x in rangees))
    out = ["# Partage des sources (GV2)", "", f"{len(rangees)} fiches.", "",
           "| Graphe | gouvernance | archive | reste | à revoir | total |", "|---|---|---|---|---|---|"]
    for g in graphes:
        c = Counter("gouvernance" if x["destination"].startswith("gouvernance:") else x["destination"]
                    for x in rangees if x["graphe"] == g)
        out.append(f"| {g} | {c['gouvernance']} | {c['archive']} | {c['reste']} | {c['a_revoir']} | {sum(c.values())} |")
    themes = Counter(x["destination"].split(":", 1)[1] for x in rangees if x["destination"].startswith("gouvernance:"))
    out += ["", "| Thème | fiches |", "|---|---|"] + [f"| {t} | {n} |" for t, n in themes.most_common()]
    out += ["", "| Graphe | Source | n | gouvernance | archive | reste | à revoir | Proposition | Thèmes |",
            "|---|---|---|---|---|---|---|---|---|"]
    for x in sorted(lignes, key=lambda x: (graphes.index(x["graphe"]), x["proposition"], -x["n"])):
        th = ", ".join(f"{t} {n}" for t, n in sorted(x["themes"].items(), key=lambda kv: -kv[1]))
        out.append(f"| {x['graphe']} | {x['source']} | {x['n']} | {x['gouvernance']} | {x['archive']} | {x['reste']} "
                   f"| {x['a_revoir']} | {x['proposition']} | {th} |")
    return "\n".join(out) + "\n"


def sources_declarees(socle: list[str], voisin: str) -> dict[str, list[tuple[str, str]]]:
    """Les dossiers des graphes du socle (sources déclarées, et dossiers lus sans source déclarée sous leur nom), et les
    sources déclarées du voisin (sans sa veille arXiv ni recherche/, qui ne parlent pas des agents en sécurité)."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    out = {}
    for g in (*socle, voisin):
        liste = [(s.cle, f"sources/{s.dossier}") for s in cfg.toutes(graphe=g)]
        if g != voisin:
            pris = {d for _, d in liste}
            liste += [(d.split("/")[-1], d) for d in cfg.dossiers_du_graphe(g, propres=True) if d not in pris]
        out[g] = liste
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--themes", type=Path, required=True)
    ap.add_argument("--voisin", required=True, help="le graphe dont seules les fiches de sécurité des agents passent")
    ap.add_argument("--theme-agents", required=True, help="la clé du thème qui les reçoit")
    ap.add_argument("--reposer", action="append", default=[], help="une destination à reranger (archive, gouvernance:<clé>…)")
    ap.add_argument("--controle", action="store_true", help="l'inventaire seul, sans modèle")
    ap.add_argument("--delai", type=int, default=600)
    a = ap.parse_args(argv)
    themes = bc.charger_themes(a.themes)
    archive = [x["en"] for x in (yaml.safe_load(a.themes.read_text(encoding="utf-8")) or {}).get("archive") or []]
    fiches = inventaire(sources_declarees(bc.graphes_du_socle(themes), a.voisin))
    c = Counter(x["graphe"] for x in fiches)
    print("inventaire : " + ", ".join(f"{g} {n}" for g, n in c.items()) + f", en tout {len(fiches)}")
    if a.controle:
        return 0
    d = dossier_sortie()
    rangees = ranger(fiches, themes, archive, appel_modele(a.delai), a.voisin, a.theme_agents, cache=d / "partage.json",
                     reposer=set(a.reposer))
    verifier(fiches, rangees)
    lignes = par_source(rangees, a.voisin)
    (d / "par_source.json").write_text(json.dumps(lignes, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "tableau.md").write_text(en_markdown(rangees, lignes), encoding="utf-8")
    revoir = sum(x["destination"] == "a_revoir" for x in rangees)
    print(f"tableau : {d / 'tableau.md'} ; à revoir : {revoir}")
    return 1 if revoir else 0


if __name__ == "__main__":
    sys.exit(main())

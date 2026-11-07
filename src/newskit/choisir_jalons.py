#!/usr/bin/env python3
"""Choisit les papiers arXiv qui méritent d'entrer en couche 2 : les jalons, pas le flot.

Extraire les notions de chaque résumé arXiv noie le graphe : la veille par thème
ramène des centaines de papiers par semaine, que personne n'a lus ni choisis.
Décision de l'utilisateur (02.10.2026) : un article édité (Substack, lettre,
blog) est ingéré systématiquement ; un papier arXiv ne l'est que s'il est
**une référence du domaine** — beaucoup cité par les autres papiers, ou cité
par plusieurs articles du corpus, qui l'ont donc choisi pour nous.

La règle est au registre (`veille_arxiv.jalons`) et se lit sur deux mesures :

    citations          citations reçues, comptées par Semantic Scholar ;
    fiches_citantes    fiches non arXiv du graphe qui citent le papier.

Un papier est un jalon s'il atteint l'un des deux seuils. Le résultat est écrit
dans `graphe/<graphe>/jalons.json` (le principal : `graphe/jalons.json`), avec
les deux mesures de chaque papier retenu : `extraire_concepts.py` ne traite que
ceux-là. Les comptes sont gardés dans `cache/citations_s2.json` et rafraîchis
après trente jours — un papier récent peut devenir un jalon.

Usage :
    python3 -m newskit.choisir_jalons                    # tous les graphes
    python3 -m newskit.choisir_jalons --graphe <graphe>
    python3 -m newskit.choisir_jalons --etat             # distribution, sans rien écrire ni appeler
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit.commun import contact  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit.extraire_squelette import canon_arxiv, est_exclu, liens_du_texte, nettoyer  # noqa: E402

CACHE = RACINE / "cache" / "citations_s2.json"
AGE_MAX = 30 * 86400
LOT = 400
API = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount,influentialCitationCount"
REGLE_DEFAUT = {"citations": 1000, "fiches_citantes": 5}


def regle() -> dict:
    r = dict(REGLE_DEFAUT)
    r.update({k: v for k, v in (cfg.veille_arxiv().get("jalons") or {}).items() if k in REGLE_DEFAUT})
    return {"citations": int(r["citations"]), "fiches_citantes": int(r["fiches_citantes"])}


def chemin_jalons(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "jalons.json"


def jalons_du_graphe(graphe: str) -> set[str]:
    """Identifiants arXiv retenus pour ce graphe, vide si la sélection n'a pas encore tourné."""
    p = chemin_jalons(graphe)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text(encoding="utf-8")).get("papiers", {}))


def jalons_detailles(graphe: str) -> dict[str, dict]:
    """arxiv_id → {citations, fiches_citantes, titre, chemin} des jalons du graphe."""
    p = chemin_jalons(graphe)
    return json.loads(p.read_text(encoding="utf-8")).get("papiers", {}) if p.exists() else {}


def choisis_a_la_main(graphe: str | None = None) -> list[str]:
    """Les papiers nommés au registre (`veille_arxiv.jalons.choisis`) : jalons d'office.

    Une liste vaut pour le graphe principal ; un dictionnaire les range par graphe.
    """
    c = (cfg.veille_arxiv().get("jalons") or {}).get("choisis") or []
    nom = graphe or cfg.GRAPHE_PRINCIPAL
    if isinstance(c, dict):
        registre = [str(i) for i in (c.get(nom) or [])]
    else:
        registre = [str(i) for i in c] if nom == cfg.GRAPHE_PRINCIPAL else []
    # Les papiers que le canon a fait entrer (combler_canon.py, 04.10.2026) : trouvés par leur titre
    # et vérifiés, ils valent un choix. Ils vivent dans graphe/<nom>/canon.json, pas au registre.
    from newskit import combler_canon  # noqa: PLC0415

    return list(dict.fromkeys(registre + combler_canon.papiers_du_canon(nom)))


def papiers_du_graphe(graphe: str) -> dict[str, dict]:
    """arxiv_id → {titre, chemin} pour les fiches arXiv de la veille du graphe."""
    out = {}
    for v in cfg.veilles_arxiv():
        if v["graphe"] != graphe:
            continue
        for f in sorted((RACINE / "sources" / v["dossier"]).glob("*.md")):
            champs, _ = fiche.lire_fichier(f)
            if champs.get("arxiv_id"):
                out[str(champs["arxiv_id"])] = {
                    "titre": str(champs.get("title") or f.stem),
                    "chemin": str(f.relative_to(RACINE)),
                }
    return out


def fiches_citantes(graphe: str) -> Counter:
    """Combien de fiches non arXiv du graphe citent chaque papier — les articles qui l'ont choisi."""
    arxiv = {RACINE / "sources" / v["dossier"] for v in cfg.veilles_arxiv()}
    compte: Counter = Counter()
    for dossier in cfg.dossiers_du_graphe(graphe):
        for f in sorted((RACINE / dossier).rglob("*.md")):
            if f.name.startswith("_") or f.parent in arxiv:
                continue
            _, corps = fiche.lire_fichier(f)
            vus = set()
            for url_brute, _ in liens_du_texte(corps, 1):
                url = nettoyer(url_brute)
                if not est_exclu(url):
                    aid = canon_arxiv(url)
                    if aid:
                        vus.add(aid)
            compte.update(vus)
    return compte


def citations(ids: list[str], sans_reseau: bool = False) -> dict[str, int]:
    """Citations reçues, par Semantic Scholar. Lues au cache quand il a moins de trente jours."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    maintenant = time.time()
    a_demander = [i for i in ids if i not in cache or maintenant - cache[i].get("ts", 0) > AGE_MAX]
    if a_demander and not sans_reseau:
        for k in range(0, len(a_demander), LOT):
            lot = a_demander[k : k + LOT]
            req = urllib.request.Request(
                API,
                data=json.dumps({"ids": ["ARXIV:" + i for i in lot]}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"{cfg.edition()['nom']}/1.0 (mailto:{contact()})",
                },
            )
            for essai in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=90) as r:
                        rendus = json.load(r)
                    for i, x in zip(lot, rendus):
                        # Un papier inconnu de Semantic Scholar compte zéro, et le dit (`inconnu`).
                        cache[i] = {
                            "n": int((x or {}).get("citationCount") or 0),
                            "influentes": int((x or {}).get("influentialCitationCount") or 0),
                            "inconnu": x is None,
                            "ts": maintenant,
                        }
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 429 or essai == 4:
                        print(
                            f"  !! Semantic Scholar : HTTP {e.code} sur un lot de {len(lot)} — comptes absents pour ce lot",
                            file=sys.stderr,
                        )
                        break
                    time.sleep(20 * (essai + 1))  # bridé sans clé : attendre, puis reprendre
                except (urllib.error.URLError, TimeoutError) as e:
                    print(f"  !! Semantic Scholar injoignable : {e}", file=sys.stderr)
                    break
            time.sleep(3)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {i: cache[i]["n"] for i in ids if i in cache}


def collecter_les_choisis(graphe: str) -> None:
    """Donne une fiche, dans le dossier arXiv du graphe, aux papiers choisis à la main qui n'en ont pas."""
    from newskit import collecte_arxiv as ca  # noqa: PLC0415

    dossier = next((RACINE / "sources" / v["dossier"] for v in cfg.veilles_arxiv() if v["graphe"] == graphe), None)
    if dossier is None:
        return
    connus = ca.identifiants_connus(dossier) | ca.identifiants_du_graphe(graphe)
    manquants = [i for i in choisis_a_la_main(graphe) if i not in connus]
    if not manquants:
        return
    dossier.mkdir(parents=True, exist_ok=True)
    metas, _ = ca.par_identifiants(manquants)
    for m in metas:
        ca.ecrire(m, motif="choisi à la main, jalon", dossier=dossier)
        print(f"  papier choisi, fiche écrite ({graphe}) : {m['arxiv_id']}  {m['title'][:64]}")
    for i in [x for x in manquants if x not in {m["arxiv_id"] for m in metas}]:
        print(f"  !! papier choisi introuvable sur arXiv : {i}", file=sys.stderr)


def choisir(graphe: str, ecrire: bool = True, sans_reseau: bool = False) -> dict:
    papiers = papiers_du_graphe(graphe)
    if not papiers:
        return {}
    r = regle()
    cit = citations(list(papiers), sans_reseau)
    citantes = fiches_citantes(graphe)
    choisis = set(choisis_a_la_main(graphe))
    retenus = {}
    for aid, p in papiers.items():
        n, m = cit.get(aid, 0), citantes.get(aid, 0)
        if n >= r["citations"] or m >= r["fiches_citantes"] or aid in choisis:
            retenus[aid] = {"citations": n, "fiches_citantes": m, "titre": p["titre"], "chemin": p["chemin"]}
    seuils = [100, 500, 1000, 5000]
    print(
        f"  {graphe} : {len(papiers)} papiers arXiv, {len(cit)} comptés"
        + "".join(f", ≥ {s} citations : {sum(1 for v in cit.values() if v >= s)}" for s in seuils)
    )
    print(
        f"      cités par ≥ {r['fiches_citantes']} fiches du graphe : {sum(1 for a in papiers if citantes.get(a, 0) >= r['fiches_citantes'])}"
        f" ; jalons (≥ {r['citations']} citations ou ≥ {r['fiches_citantes']} fiches citantes) : {len(retenus)}"
    )
    for aid, v in sorted(retenus.items(), key=lambda kv: -kv[1]["citations"])[:6]:
        print(f"        {v['citations']:>7}  {v['fiches_citantes']:>3} fiche(s)  {v['titre'][:70]}")
    if ecrire:
        sortie = chemin_jalons(graphe)
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(
            json.dumps(
                {"regle": r, "date": time.strftime("%Y-%m-%d"), "papiers": {k: retenus[k] for k in sorted(retenus)}},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return retenus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", help="un seul graphe (défaut : tous ceux qui ont une veille arXiv)")
    ap.add_argument("--etat", action="store_true", help="distribution sur le cache, sans appel ni écriture")
    args = ap.parse_args()
    graphes = [args.graphe] if args.graphe else list(dict.fromkeys(v["graphe"] for v in cfg.veilles_arxiv()))
    if not args.etat:
        for g in graphes:
            collecter_les_choisis(g)
    for g in graphes:
        choisir(g, ecrire=not args.etat, sans_reseau=args.etat)
    return 0


if __name__ == "__main__":
    sys.exit(main())

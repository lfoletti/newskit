#!/usr/bin/env python3
"""Le classement des candidats, mesuré sans lecteur (porteur, 10.10.2026 : « sélection par ranking ? ») : pour chaque
question du banc dont la page citée est une fiche du graphe, à quel rang la recherche place-t-elle un paragraphe de cette
fiche — par les mots (BM25), par le sens (encodeur), et par la fusion des rangs qu'emploie `repondre`. Rappel à 1, 3, 10,
50, 100 et rang réciproque moyen. Dit si le mal est dans la sélection finale (la fiche est dans les dix premiers mais pas
dans les trois servis : un reclassement y remédie) ou dans le rappel (absente des cent premiers : l'encodeur ou les mots).

    python3 -m newskit.classement --graphe <graphe> --jeu banc|themes
Écrit cache/contexte/classement_<graphe>_<jeu>_<horodatage>.json ; rien dans le dépôt."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import synthese as sy  # noqa: E402

K = (1, 3, 10, 50, 100)


def questions_du_jeu(graphe: str, jeu: str) -> list[dict]:
    base = cfg.chemins_du_graphe(graphe)["base"]
    if jeu == "banc":
        return [d for d in json.loads((base / "banc.json").read_text(encoding="utf-8"))["details"] if d.get("type") in ("ag_recent", "ag_fond")]
    return [q for q in json.loads((base / "themes_banc.json").read_text(encoding="utf-8")) if not q.get("comblee")]


def mesurer(graphe: str, jeu: str) -> dict:
    import numpy as np  # noqa: PLC0415
    ix = sy.index(graphe); enc = sy.encodeur(graphe)
    if not ix or enc is None:
        raise SystemExit("index ou encodeur absent : synthese.py indexer d'abord")
    ids, textes, E = ix
    ixm = sy.index_mots(graphe, textes)
    G = sy.charger_graphe(graphe)
    urls = {str(d.get("url") or "").rstrip("/"): str(d.get("source_file")) for _, d in G.nodes(data=True) if d.get("url") and d.get("source_file")}
    fiche_de = [i[0] for i in ids]
    lignes = []; t0 = time.time()
    for q in questions_du_jeu(graphe, jeu):
        url = (re.findall(r"https?://[^\s)\"]+", str(q.get("citation") or "") + " " + str(q.get("source") or "")) or [""])[0].rstrip("/")
        fiche = urls.get(url)
        if not fiche:
            continue
        rang_mots = np.argsort(-sy.bm25_inverse(q["question"], ixm))[:200]
        v = np.array(list(enc.embed([q["question"]]))[0], dtype=np.float32); v /= np.linalg.norm(v)
        rang_sens = np.argsort(-(E @ v))[:200]
        score = {}
        for rangs in (rang_mots, rang_sens):
            for r, i in enumerate(rangs):
                score[int(i)] = score.get(int(i), 0.0) + 1.0 / (60 + r)
        rang_fusion = sorted(score, key=lambda i: -score[i])
        def rang(liste):
            for r, i in enumerate(liste):
                if fiche_de[int(i)] == fiche:
                    return r + 1
            return None
        lignes.append({"i": q.get("i"), "fiche": fiche, "question": q["question"][:120],
                       "mots": rang(rang_mots), "sens": rang(rang_sens), "fusion": rang(rang_fusion)})
    n = len(lignes)
    bilan = {"graphe": graphe, "jeu": jeu, "horodatage": time.strftime("%Y-%m-%dT%H-%M"), "questions_atteignables": n, "duree_s": round(time.time() - t0)}
    for voie in ("mots", "sens", "fusion"):
        rs = [l[voie] for l in lignes]
        bilan[voie] = {f"rappel@{k}": round(sum(1 for r in rs if r and r <= k) / n, 3) if n else None for k in K}
        bilan[voie]["MRR"] = round(sum(1 / r for r in rs if r) / n, 3) if n else None
        bilan[voie]["absente des 200"] = sum(1 for r in rs if not r)
    bilan["lignes"] = lignes
    d = RACINE / "cache" / "contexte"; d.mkdir(parents=True, exist_ok=True)
    (d / f"classement_{graphe}_{jeu}_{bilan['horodatage']}.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    return bilan


def afficher(b: dict) -> None:
    print(f"  {b['graphe']} — {b['jeu']} : {b['questions_atteignables']} questions dont la page citée est dans le graphe, {b['duree_s']} s")
    print(f"    {'voie':8} " + " ".join(f"{'@'+str(k):>6}" for k in K) + f" {'MRR':>6} {'hors 200':>9}")
    for voie in ("mots", "sens", "fusion"):
        v = b[voie]
        print(f"    {voie:8} " + " ".join(f"{v[f'rappel@{k}']:>6.0%}" for k in K) + f" {v['MRR']:>6.2f} {v['absente des 200']:>9}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True); ap.add_argument("--jeu", choices=("banc", "themes"), default="themes")
    a = ap.parse_args()
    afficher(mesurer(cfg.nom_actuel(a.graphe), a.jeu))
    return 0


if __name__ == "__main__":
    sys.exit(main())

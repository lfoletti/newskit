#!/usr/bin/env python3
"""Mesure de la phase A du pilote (CHANTIER_PILOTE_CORTEXDB.md) : pour chaque question du banc d'un graphe, la part des
faits attendus (comme `contexte.py`, faits discriminants seulement) qui se lisent dans le contexte du moteur
(`synthese.repondre`, sans appel) et dans celui de CortexDB (morceaux de `search_graphrag_lexical`, bout à bout), à
budget de caractères égal : la longueur du contexte du moteur hors renvois. Aucun lecteur, aucun appel au modèle.

Deux temps, deux Pythons (le client gRPC vit dans le venv du pilote, l'encodeur et l'index dans celui du projet) :
    python3 -m newskit.mesure_pilote_cortexdb moteur --graphe <graphe>             # contextes et budgets
    cache/pilote_cortexdb/venv/bin/python newskit.mesure_pilote_cortexdb cortexdb --graphe <graphe> --jeton … \
        --etiquette vecteurs --modes lexical,graph,hybrid          # un cerveau par étiquette
    python3 -m newskit.mesure_pilote_cortexdb comparer --graphe <graphe>
Écrit cache/pilote_cortexdb/mesure/<graphe>_*.json ; rien dans le dépôt."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
SORTIE = RACINE / "cache" / "pilote_cortexdb" / "mesure"
COLLECTION_FICHES = "graphrag_chunks"   # la collection par défaut d'ingest_document (CortexDB v2.136.0)
SEUIL = 0.6           # comme contexte.SEUIL
PART_COMMUNE = 0.01   # comme contexte.PART_COMMUNE
PLANCHER_COMMUN = 20  # un fait n'est commun qu'au-delà de 20 paragraphes, quelle que soit la taille du corpus


def assembler(morceaux: list[str], budget: int) -> str:
    return "\n".join(morceaux)[:budget]


def assembler_en_deux(syntheses: list[str], morceaux: list[str], part: int, budget: int) -> str:
    """Comme le contexte du moteur : les synthèses d'abord, dans leur part du budget ; les morceaux remplissent le
    reste (toute la place, s'il n'y a pas de synthèse)."""
    s = "\n".join(syntheses)[:part]
    m = "\n".join(morceaux)
    return (s + "\n" + m if s and m else s or m)[:budget]


def commun(df: int, n: int) -> bool:
    """Un fait présent dans plus de PART_COMMUNE des paragraphes ne prouve rien ; sur un petit corpus (une naissance),
    1 % vaut un ou deux paragraphes et retirait les réponses mêmes : le plancher les garde."""
    return df > max(PART_COMMUNE * n, PLANCHER_COMMUN)


def couverture(ctx_plie: str, attendus: set[str]) -> tuple[float | None, set[str]]:
    if not attendus:
        return None, set()
    manquants = {f for f in attendus if f not in ctx_plie}
    return (len(attendus) - len(manquants)) / len(attendus), manquants


def fiche_presente(documents: list[str], source: str) -> bool:
    """La fiche attendue parmi les documents rendus : par son chemin (cerveau d'avant le 23.10.2026), dont `source` est
    le début, ou par l'identifiant court d'une fiche dont le chemin commence par `source`."""
    if not source:
        return False
    from newskit import lecture_cortexdb as lc  # noqa: PLC0415
    inverse = lc._inverse()
    return any(d.startswith(source) or inverse.get(d, "").startswith(source) for d in documents)


def passages_seuls(ctx: str) -> str:
    """Le bloc des extraits bruts des fiches, sans les synthèses de la pyramide : ce que la recherche seule a trouvé."""
    _, sep, reste = ctx.partition("PASSAGES —")
    return reste.partition("\n")[2] if sep else ""


def fiche_dans_renvois(renvois: str, source: str) -> bool:
    return bool(source) and any(c.startswith(source) for c in re.findall(r"sources/\S+", renvois))


def questions(graphe: str, jeu: str = "banc") -> list[dict]:
    """`banc` : les questions du banc (banc.json) ; `themes` : le jeu par thème (themes_banc.json), celui qu'une
    naissance scelle avant la collecte."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    base = cfg.chemins_du_graphe(graphe)["base"]       # le graphe principal vit à la racine de graphe/
    if jeu == "themes":
        qs = [q for q in json.loads((base / "themes_banc.json").read_text(encoding="utf-8")) if not q.get("comblee")]
        if (base / "naissance.json").exists():           # une naissance : hors des questions exclues par un complément
            from newskit import naissance  # noqa: PLC0415
            e = json.loads((base / "naissance.json").read_text(encoding="utf-8"))
            qs = naissance.mesurables(qs, [s for s in [e.get("sceau"), *e.get("complements", [])] if s])
        return qs
    return json.loads((base / "banc.json").read_text(encoding="utf-8"))["details"]


def temps_moteur(graphe: str, jeu: str = "banc") -> None:
    from newskit import contexte as cx  # noqa: PLC0415
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    from newskit import synthese as sy  # noqa: PLC0415
    G = sy.charger_graphe(graphe)
    ix = sy.index(graphe)
    paragraphes = [ig._plier(x) for x in ix[1]] if ix else []
    out = []
    for q in questions(graphe, jeu):
        attendus = {f for f in cx.faits_attendus(q)
                    if not (paragraphes and commun(sum(1 for p in paragraphes if f in p), len(paragraphes)))}
        corps, _, renvois = cx.contexte_de(graphe, q["question"], G, False).partition("\nRENVOIS")
        out.append({"i": q.get("i"), "type": q.get("type"), "question": q["question"], "source": q.get("source") or "",
                    "attendus": sorted(attendus), "budget": len(corps), "contexte": corps, "renvois": renvois})
    SORTIE.mkdir(parents=True, exist_ok=True)
    (SORTIE / f"{graphe}_moteur.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} contextes moteur, budget moyen {sum(o['budget'] for o in out) // max(len(out), 1)} caractères")


def temps_cortexdb(graphe: str, adresse: str, jeton: str, etiquette: str, modes: list[str], voie: str = "outil") -> None:
    """Pose les questions à un cerveau : morceaux de la collection des fiches dans chaque mode, et synthèses de la
    collection `pyramide` (mode graphe : elles sont rattachées à l'entité de leur notion).

    `voie` : `outil`, l'outil `search_graphrag_lexical`, dont les graines sont lexicales ; `graphrag`, le service
    `SearchGraphRag`, dont les graines viennent de l'encodeur. Mesuré le 23.10.2026 sur un graphe d'éthique (questions en
    français, fiches surtout en anglais) : 61 % des morceaux rendus par l'outil étaient français, contre 10 % du cerveau."""
    from cortexdb_client import CortexClient, proto  # noqa: PLC0415
    lignes = json.loads((SORTIE / f"{graphe}_moteur.json").read_text(encoding="utf-8"))
    out = []
    with CortexClient.connect(adresse, token=jeton) as c:
        def chercher(question: str, budget: int, mode: str, collection: str | None = None) -> dict:
            if voie == "graphrag":
                t0 = time.time()
                r = c.graphrag.SearchGraphRag(proto.SearchGraphRagRequest(query=question, collection=collection or "",
                        retrieval_mode=mode, max_context_chars=budget, max_context_chunks=30))
                return {"morceaux": [x.content for x in r.chunks], "documents": [x.document_id for x in r.chunks],
                        "duree_ms": round((time.time() - t0) * 1000)}
            args = {"query": question, "retrieval_mode": mode, "max_context_chars": budget, "max_context_chunks": 30}
            if collection:
                args["collection"] = collection
            t0 = time.time()
            d = json.loads(c.tools.CallTool(proto.CallToolRequest(name="search_graphrag_lexical", args_json=json.dumps(args))).result_json or "{}")
            ch = d.get("Chunks") or []
            return {"morceaux": [x.get("Content") or "" for x in ch], "documents": [x.get("DocumentID") or "" for x in ch],
                    "duree_ms": round((time.time() - t0) * 1000)}
        for l in lignes:
            # Sans collection nommée, CortexDB cherche dans toutes : `<mode>` lit les seules fiches, `<mode>/tout` le
            # mélange fiches et synthèses que CortexDB classe lui-même (constaté le 22.10.2026 : 155 morceaux sur 194).
            ligne = {"i": l["i"], **{m: chercher(l["question"], l["budget"], m, COLLECTION_FICHES) for m in modes},
                     **{f"{m}/tout": chercher(l["question"], l["budget"], m) for m in modes}}
            ligne["pyramide"] = chercher(l["question"], l["budget"], "graph", "pyramide")
            out.append(ligne)
    (SORTIE / f"{graphe}_cortexdb_{etiquette}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} questions posées au cerveau « {etiquette} » ({', '.join(modes)}, pyramide)")


def comparer(graphe: str) -> dict:
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    cx = json.loads((SORTIE / f"{graphe}_moteur.json").read_text(encoding="utf-8"))
    cerveaux = {f.stem.split("_cortexdb_", 1)[1]: {str(x["i"]): x for x in json.loads(f.read_text(encoding="utf-8"))}
                for f in sorted(SORTIE.glob(f"{graphe}_cortexdb_*.json"))}
    lignes, bras = [], ["moteur", "moteur_passages"]
    for l in cx:
        attendus = set(l["attendus"])
        ligne = {"i": l["i"], "type": l["type"], "faits": len(attendus), "budget": l["budget"]}
        def noter(nom: str, ctx: str, documents: list[str] | None) -> None:
            c, m = couverture(ig._plier(ctx), attendus)
            ligne[nom] = {"couverture": c, "manquants": sorted(m)[:8],
                          "fiche": None if documents is None else fiche_presente(documents, l["source"])}
            if nom not in bras:
                bras.append(nom)
        noter("moteur", l["contexte"], None)
        ligne["moteur"]["fiche"] = fiche_dans_renvois(l["renvois"], l["source"])
        passages = passages_seuls(l["contexte"])
        noter("moteur_passages", passages, None)
        ligne["moteur_passages"]["fiche"] = ligne["moteur"]["fiche"]
        part = l["budget"] - len(passages)          # la part des synthèses dans le contexte du moteur
        for etiq, rep in cerveaux.items():
            r = rep[str(l["i"])]
            for mode in [k for k in r if k not in ("i", "pyramide")]:
                docs = r[mode]["documents"]
                noter(f"{etiq}:{mode}", assembler(r[mode]["morceaux"], l["budget"]), docs)
                noter(f"{etiq}:{mode}@passages", assembler(r[mode]["morceaux"], len(passages)), None)
                if "pyramide" in r and not mode.endswith("/tout"):
                    noter(f"{etiq}:{mode}+pyramide", assembler_en_deux(r["pyramide"]["morceaux"], r[mode]["morceaux"], part, l["budget"]), docs)
        lignes.append(ligne)
    mesurees = [l for l in lignes if l["faits"]]
    bilan = {"graphe": graphe, "questions": len(lignes), "mesurees": len(mesurees), "seuil": SEUIL,
             "budget_moyen": round(sum(l["budget"] for l in lignes) / max(len(lignes), 1)), "bras": {}}
    for b in bras:
        cs = [l[b]["couverture"] for l in mesurees]
        fiches = [l[b]["fiche"] for l in lignes if l[b]["fiche"] is not None]
        bilan["bras"][b] = {"couverture_moyenne": round(sum(cs) / len(cs), 3), "contexte_suffisant": round(sum(1 for x in cs if x >= SEUIL) / len(cs), 3),
                            "fiche_citee_dans_contexte": round(sum(fiches) / len(fiches), 3) if fiches else None}
    bilan["par_type"] = {t: {"n": sum(1 for l in mesurees if l["type"] == t),
                             **{b: round(sum(l[b]["couverture"] for l in mesurees if l["type"] == t) / sum(1 for l in mesurees if l["type"] == t), 3) for b in bras}}
                         for t in sorted({l["type"] for l in mesurees})}
    bilan["lignes"] = lignes
    horo = time.strftime("%Y-%m-%dT%H-%M")
    (SORTIE / f"{graphe}_bilan_{horo}.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    for b, v in bilan["bras"].items():
        print(f"{b:34} couverture {v['couverture_moyenne']:.3f}  suffisants {v['contexte_suffisant']:.3f}  fiche {v['fiche_citee_dans_contexte']}")
    return bilan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("temps", choices=("moteur", "cortexdb", "comparer"))
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--adresse", default="127.0.0.1:47899")
    ap.add_argument("--jeton", default="")
    ap.add_argument("--jeu", choices=("banc", "themes"), default="banc", help="temps moteur : le jeu de questions")
    ap.add_argument("--etiquette", default="sans_vecteurs", help="nom du cerveau interrogé (temps cortexdb)")
    ap.add_argument("--modes", default="lexical,graph", help="modes de recherche (temps cortexdb)")
    ap.add_argument("--voie", choices=("outil", "graphrag"), default="outil", help="temps cortexdb : outil lexical ou service GraphRAG")
    a = ap.parse_args()
    if a.temps == "moteur":
        temps_moteur(a.graphe, a.jeu)
    elif a.temps == "cortexdb":
        temps_cortexdb(a.graphe, a.adresse, a.jeton, a.etiquette, a.modes.split(","), a.voie)
    else:
        comparer(a.graphe)
    return 0


if __name__ == "__main__":
    sys.exit(main())

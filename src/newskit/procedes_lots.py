#!/usr/bin/env python3
"""Les passes d'un procédé d'ingestion par les lots de l'API d'Anthropic (procedes.py, `appel: lots` ; mainteneur, 15
et 19.11.2026 : « on va aller avec les lots »).

Un lot porte les passes de plusieurs fiches, à moitié prix, rendues plus tard. Le texte de la fiche est le premier
bloc de chaque requête, marqué pour le cache, la consigne vient après : les passes d'une même fiche portent le même
bloc, mot pour mot. Dans un lot, le cache n'est pas garanti ; la part relue se lit au registre des coûts.

`soumettre` envoie, pour chaque fiche en attente (au plus `--plafond`), les passes qui manquent : le tri d'une passe
qui en déclare un, sinon la passe. Un seul lot attend son relevé par type de contenu. `relever` lit le lot fini : les
réponses se gardent, se refusent et achèvent la fiche comme par `claude -p` (procedes_passes). La passe que le tri
retient part dans le lot suivant.

La clé : ANTHROPIC_API_KEY dans l'environnement, sinon dans ~/.secrets/anthropic.env. Jamais affichée.

Usage :
    python3 -m newskit.procedes_lots --graphe ai-engineering --type papier soumettre --plafond 50 [--a-blanc]
    python3 -m newskit.procedes_lots --graphe ai-engineering --type papier relever
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from newskit import couts, fiche
from newskit import procedes_formes as pf
from newskit import procedes_passes as pp

API = "https://api.anthropic.com/v1/messages/batches"
VERSION = "2023-06-01"
DELAI = 300
SECRETS = Path.home() / ".secrets" / "anthropic.env"
EFFORT = "medium"  # écrit ici pour qu'il ne change pas seul
JETONS = {"sortie": 64000, "forme": 32000, "tri": 4000}  # max_tokens, réflexion comprise
# Dollars par million de jetons : entrée, sortie (prix publics du 3.11.2026), avant la remise des lots. Un modèle
# absent se consigne à zéro.
PRIX = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.8, 4.0)}
REMISE = 0.5


def cle_api() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    texte = SECRETS.read_text(encoding="utf-8") if SECRETS.exists() else ""
    m = re.search(r"^(?:export\s+)?ANTHROPIC_API_KEY=[\"']?([^\"'\s]+)", texte, flags=re.M)
    if not m:
        raise SystemExit(f"clé absente : ANTHROPIC_API_KEY dans l'environnement ou dans {SECRETS}")
    return m.group(1)


def _http(methode: str, url: str, corps: dict | None = None) -> bytes:
    entetes = {"x-api-key": cle_api(), "anthropic-version": VERSION, "content-type": "application/json"}
    data = json.dumps(corps).encode("utf-8") if corps is not None else None
    req = urllib.request.Request(url, data=data, headers=entetes, method=methode)
    with urllib.request.urlopen(req, timeout=DELAI) as r:
        return r.read()


def etat_du_lot(type_: str) -> Path:
    return pp.dossier(type_) / "lot.json"


def cout(usage: dict, modele: str) -> float:
    """Le prix d'une réponse de lot, en dollars."""
    entree, sortie = PRIX.get(modele, (0.0, 0.0))
    u = lambda k: int(usage.get(k) or 0)  # noqa: E731
    brut = (
        u("input_tokens") * entree
        + u("cache_creation_input_tokens") * entree * couts.PRIX_CACHE_ECRIT
        + u("cache_read_input_tokens") * entree * couts.PRIX_CACHE_LU
        + u("output_tokens") * sortie
    ) / 1e6
    return brut * REMISE


def _bloc(texte: str, mots_max: int) -> dict:
    """Le texte de la fiche, premier bloc et seul marqué pour le cache : rien de propre à la passe n'y entre."""
    corps = " ".join(texte.split()[:mots_max]).replace("</document>", "< /document>")
    return {"type": "text", "text": f"<document>\n{corps}\n</document>\n", "cache_control": {"type": "ephemeral"}}


def _requete(
    cid: str, bloc: dict, consigne: str, jetons: int, modele: str, schema: dict | None, effort: str | None = None
) -> dict:
    sortie = {"effort": effort or EFFORT} | ({"format": {"type": "json_schema", "schema": schema}} if schema else {})
    params = {
        "model": modele,
        "max_tokens": jetons,
        "thinking": {"type": "adaptive"},
        "output_config": sortie,
        "messages": [{"role": "user", "content": [bloc, {"type": "text", "text": consigne}]}],
    }
    return {"custom_id": cid, "params": params}


def requetes(graphe: str, type_: str, decl: dict, plafond: int) -> tuple[list[dict], dict]:
    """Les requêtes du lot et, par identifiant, ce qu'elles portent (fiche, passe, tri)."""
    reqs: list[dict] = []
    cles: dict[str, dict] = {}
    refusees = pp.manquants(type_)
    fiches = 0
    for chemin, champs, cle in pp.fiches(graphe, decl):
        if fiches >= plafond:
            break
        a_faire = [
            p for p in decl["passes"] if not pp.deja_faite(type_, p, cle) and p["nom"] not in refusees.get(cle, {})
        ]
        if not a_faire:
            continue
        t = pp.texte(chemin, champs, cle, decl)
        if t is None:
            continue
        fiches += 1
        bloc = _bloc(t, decl["texte"]["mots_max"])
        for p in a_faire:
            tri = bool(p["tri"]) and not pp.tri_retenu(type_, p, cle)
            cid = f"r{len(reqs):05d}"
            if tri:
                consigne, jetons = p["tri"].read_text(encoding="utf-8"), JETONS["tri"]
                req = _requete(cid, _bloc(t, pp.MOTS_TRI), consigne, jetons, pp.media.modele("lecture"), pf.SCHEMA_TRI)
            else:
                schema = pf.SCHEMAS[p["forme"]] if p["forme"] else None
                jetons = JETONS["forme" if p["forme"] else "sortie"]
                consigne = p["consigne"].read_text(encoding="utf-8")
                req = _requete(cid, bloc, consigne, jetons, pp.media.modele("extraction"), schema, p["effort"])
            reqs.append(req)
            cles[cid] = {"chemin": str(Path(chemin).relative_to(pp.RACINE)), "cle": cle, "passe": p["nom"], "tri": tri}
    return reqs, cles


def soumettre(graphe: str, type_: str, decl: dict, plafond: int = 10, a_blanc: bool = False) -> str:
    f = etat_du_lot(type_)
    if f.exists() and not json.loads(f.read_text(encoding="utf-8")).get("releve"):
        raise SystemExit(f"{type_} : un lot attend son relevé ({f})")
    reqs, cles = requetes(graphe, type_, decl, plafond)
    if not reqs:
        return f"{type_} : rien à soumettre"
    if a_blanc:
        signes = sum(len(b["text"]) for r in reqs for b in r["params"]["messages"][0]["content"])
        return f"{type_} : à blanc, {len(reqs)} requêtes, {signes // 1000} milliers de signes ; rien n'est envoyé"
    lot = json.loads(_http("POST", API, {"requests": reqs}))
    etat = {"id": lot["id"], "soumis": time.strftime("%Y-%m-%d %H:%M:%S"), "requetes": cles}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{type_} : lot {lot['id']} : {len(reqs)} requêtes soumises"


def lire_resultat(r: dict, json_attendu: bool) -> tuple[object, dict, str]:
    """(réponse, relevé de jetons, diagnostic) d'un résultat de lot. La réponse est None sur un échec, un refus ou une
    coupe à `max_tokens` ; le texte, ou l'objet JSON (une chaîne illisible reste chaîne : `garder` la rejette)."""
    res = r.get("result") or {}
    if res.get("type") != "succeeded":
        return None, {}, f"{res.get('type')} : {json.dumps(res.get('error') or {}, ensure_ascii=False)[:160]}"
    m = res["message"]
    usage = m.get("usage") or {}
    if m.get("stop_reason") == "refusal":
        return None, usage, pp.REFUS
    if m.get("stop_reason") == "max_tokens":
        return None, usage, "coupée à max_tokens"
    texte = "".join(b.get("text", "") for b in m.get("content") or [])  # la réflexion n'a pas de `text`
    if json_attendu:
        try:
            return json.loads(texte), usage, "ok"
        except ValueError:
            return texte, usage, "ok"
    return texte, usage, "ok"


def relever(graphe: str, type_: str, decl: dict) -> tuple[int, list[str]]:
    """(code, lignes) : 2 tant que le lot tourne ; 0 une fois relevé, ou sans lot."""
    f = etat_du_lot(type_)
    etat = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if not etat or etat.get("releve"):
        return 0, [f"{type_} : aucun lot à relever"]
    lot = json.loads(_http("GET", f"{API}/{etat['id']}"))
    n = lot.get("request_counts") or {}
    lignes = [f"{type_} : lot {etat['id']} : {lot['processing_status']} ; {json.dumps(n)}"]
    if lot["processing_status"] != "ended":
        return 2, lignes
    passes = {p["nom"]: p for p in decl["passes"]}
    touchees: dict[str, Path] = {}
    for brut in _http("GET", lot["results_url"]).decode("utf-8").splitlines():
        r = json.loads(brut)
        q = etat["requetes"].get(r["custom_id"])
        if not q:
            continue
        p, cle = passes[q["passe"]], q["cle"]
        rendu, usage, diag = lire_resultat(r, q["tri"] or bool(p["forme"]))
        modele = pp.media.modele("lecture" if q["tri"] else "extraction")
        env = {"usage": usage, "total_cost_usd": cout(usage, modele), "stop_reason": diag}
        note = f"lot {type_}:{p['nom']}{' (tri)' if q['tri'] else ''} {cle}"
        proc = SimpleNamespace(returncode=0 if rendu is not None else 1, stdout="", stderr="")
        couts.enregistrer(pp.POSTE, graphe, modele, proc, None, env=env, note=note)
        touchees[cle] = pp.RACINE / q["chemin"]
        if rendu is None:
            if diag == pp.REFUS and not q["tri"]:
                pp.noter_manquant(type_, cle, p["nom"], pp.REFUS)
            lignes.append(f"!! {p['nom']} {cle} : {diag}")
        elif q["tri"]:
            if not isinstance(rendu, dict):
                lignes.append(f"!! {p['nom']} {cle} : tri, réponse sans JSON")
            else:
                retenu = pp.garder_tri(type_, p, cle, rendu)
                lignes.append(f"{'retenue' if retenu else 'écartée'} {p['nom']} {cle} : tri")
        else:
            rejet = pp.garder(type_, p, cle, rendu)
            lignes.append(f"!! {p['nom']} {cle} : {rejet}" if rejet else f"fait  {p['nom']} {cle}")
    for cle, chemin in touchees.items():
        lignes += pp.achever(type_, decl, chemin, cle)
    etat["releve"] = time.strftime("%Y-%m-%d %H:%M:%S")
    f.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0, lignes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--type", required=True, dest="type_")
    ap.add_argument("action", choices=("soumettre", "relever"))
    ap.add_argument("--plafond", type=int, default=10, help="fiches par lot")
    ap.add_argument("--a-blanc", action="store_true", help="compter, sans envoi")
    a = ap.parse_args(argv)
    pp.ec.viser(a.graphe)
    decls = pp.declaration_du_graphe(a.graphe)
    if a.type_ not in decls:
        raise SystemExit(f"{a.graphe} : type de contenu non déclaré : {a.type_} (déclarés : {sorted(decls)})")
    if a.action == "soumettre":
        print(soumettre(a.graphe, a.type_, decls[a.type_], a.plafond, a.a_blanc))
        return 0
    code, lignes = relever(a.graphe, a.type_, decls[a.type_])
    print("\n".join(lignes))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

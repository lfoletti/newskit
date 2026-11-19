#!/usr/bin/env python3
"""Les passes de texte d'un procédé d'ingestion (procedes.py ; mainteneur, 15 et 19.11.2026).

Pour chaque fiche d'un type de contenu déclaré, parmi celles que lit la couche 2 du graphe : le texte intégral (le PDF
à l'adresse déclarée, lu puis effacé), puis chaque passe à sortie, dans l'ordre, par `claude -p`. La réponse se garde
sous `graphe/procedes/<type>/<passe>/<clé>.md` ; une passe déjà faite ne repart pas. La passe `sortie: corps` devient
le corps de la fiche, sous son titre : la fiche change d'empreinte et repasse en couche 2. Un refus du modèle se note
dans `graphe/procedes/<type>/manquants.json` (« manquant, à ingérer »). Les passes à forme viennent ensuite.

Usage :
    python3 -m newskit.procedes_passes --graphe ai-engineering --type papier --plafond 20
    python3 -m newskit.procedes_passes --graphe ai-engineering --type papier --etat
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from newskit import config_sources as cfg
from newskit import couts, fiche, media, procedes
from newskit import extraire_concepts as ec
from newskit.collecte_pdf_local import lire_pdf, nettoyer
from newskit.commun import RACINE

DELAI = 900  # secondes par appel : une lecture d'un texte de 45 000 mots prend quelques minutes
PAUSE = 3  # secondes entre deux téléchargements (arXiv en demande trois)
PARALLELE = 4
POSTE = "procede"
LIMITE = "limite d'usage atteinte"
REFUS = "refus du modèle"


def cle_de(valeur) -> str:
    """La clé d'une fiche dans un nom de fichier : la valeur de son champ de contenu, sans signe hors de [A-Za-z0-9._-]."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(valeur)).strip("_")[:80]


def dossier(type_: str) -> Path:
    return RACINE / "graphe" / "procedes" / type_


def sortie(type_: str, passe: str, cle: str) -> Path:
    return dossier(type_) / passe / f"{cle}.md"


def _cache_texte(cle: str) -> Path:
    return RACINE / "cache" / "procedes" / "textes" / f"{cle}.txt"


def manquants(type_: str) -> dict:
    p = dossier(type_) / "manquants.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _ecrire_manquants(type_: str, m: dict) -> None:
    p = dossier(type_) / "manquants.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(sorted(m.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def noter_manquant(type_: str, cle: str, passe: str, motif: str) -> None:
    m = manquants(type_)
    m[cle] = f"{passe} : {motif} ({time.strftime('%Y-%m-%d')})"
    _ecrire_manquants(type_, m)


def _effacer_manquant(type_: str, cle: str) -> None:
    m = manquants(type_)
    if m.pop(cle, None) is not None:
        _ecrire_manquants(type_, m)


def declaration_du_graphe(graphe: str) -> dict:
    """Les procédés déclarés du graphe, vérifiés ; {} sans déclaration."""
    return procedes.declaration(cfg.graphe_declare(graphe).get("procedes"), RACINE) or {}


def fiches(graphe: str, decl: dict) -> list[tuple[Path, dict, str]]:
    """(chemin, en-tête, clé) des fiches du type : celles que lit la couche 2 du graphe et dont l'en-tête porte le
    champ du contenu."""
    champ, out = decl["contenu"]["champ"], []
    for f in ec.fiches_lues(graphe):
        champs, _ = fiche.lire_fichier(f)
        if champs.get(champ):
            out.append((f, champs, cle_de(champs[champ])))
    return out


def telecharger(url: str, chemin: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "newskit"})
    with urllib.request.urlopen(req, timeout=120) as r:
        chemin.write_bytes(r.read())


def adresse(decl: dict, champs: dict) -> str | None:
    """L'adresse du texte intégral : le champ `pdf` de la fiche, sinon l'adresse déclarée remplie par l'en-tête."""
    if champs.get("pdf"):
        return str(champs["pdf"])
    modele = decl["texte"]["adresse"]
    try:
        return modele.format_map({k: str(v) for k, v in champs.items()}) if modele else None
    except KeyError:
        return None


def texte(chemin: Path, champs: dict, cle: str, decl: dict) -> str | None:
    """Le texte que lisent les passes : le texte intégral (PDF lu une fois, gardé en cache le temps des passes,
    jamais le PDF), ou le corps de la fiche sans texte intégral. None sans adresse ou sur un échec."""
    if not decl["texte"]["integral"]:
        return fiche.lire_fichier(chemin)[1]
    cache = _cache_texte(cle)
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    url = adresse(decl, champs)
    if not url:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    pdf = cache.with_suffix(".pdf")
    try:
        telecharger(url, pdf)
        time.sleep(PAUSE)
        brut, _, _ = lire_pdf(pdf)
    except Exception:  # noqa: BLE001 — réseau, PDF illisible : la fiche attend le passage suivant
        return None
    finally:
        pdf.unlink(missing_ok=True)
    t = nettoyer(brut).encode("utf-8", "ignore").decode("utf-8")  # un PDF peut rendre des demi-caractères
    cache.write_text(t, encoding="utf-8")
    return t


def invite(consigne: str, texte_: str, mots_max: int) -> str:
    """La consigne, puis le texte coupé à `mots_max` mots entre balises <document>, la balise neutralisée dedans."""
    corps = " ".join(texte_.split()[:mots_max]).replace("</document>", "< /document>")
    return f"{consigne.rstrip()}\n<document>\n{corps}\n</document>\n"


def appeler(invite_: str, graphe: str, note: str) -> tuple[str | None, str]:
    """(réponse, diagnostic) d'un appel à `claude -p`, consigné au registre des coûts."""
    if couts.en_limite():
        return None, LIMITE
    modele = media.modele("extraction")
    args = [*couts.CLAUDE_P, "--no-session-persistence", "--tools", "", "--output-format", "json", "--model", modele]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=invite_, capture_output=True, text=True, timeout=DELAI, cwd=RACINE)
    except subprocess.TimeoutExpired:
        couts.enregistrer(POSTE, graphe, modele, None, time.time() - t0, note=f"{note} : délai dépassé")
        return None, f"délai de {DELAI} s dépassé"
    env = couts.enregistrer(POSTE, graphe, modele, r, time.time() - t0, note=note) or {}
    if env.get("stop_reason") == "refusal":
        return None, REFUS
    if r.returncode != 0 or env.get("is_error"):
        return None, f"échec : {(r.stderr or str(env.get('result') or ''))[:160]}"
    return str(env.get("result") or ""), "ok"


def passer(graphe: str, type_: str, decl: dict, passe: dict, chemin: Path, champs: dict, cle: str) -> str:
    """Une passe de texte sur une fiche ; la ligne de compte rendu."""
    nom, out = passe["nom"], sortie(type_, passe["nom"], cle)
    if out.exists() and len(out.read_text(encoding="utf-8").split()) >= passe["mots_min"]:
        return f"déjà  {nom} {cle}"
    t = texte(chemin, champs, cle, decl)
    if t is None:
        return f"!! {nom} {cle} : pas de texte"
    consigne = passe["consigne"].read_text(encoding="utf-8")
    rendu, diag = appeler(invite(consigne, t, decl["texte"]["mots_max"]), graphe, f"{type_}:{nom} {cle}")
    if rendu is None:
        if diag == REFUS:
            noter_manquant(type_, cle, nom, REFUS)
        return f"!! {nom} {cle} : {diag}"
    if len(rendu.split()) < passe["mots_min"]:
        return f"!! {nom} {cle} : réponse de {len(rendu.split())} mots, {passe['mots_min']} attendus"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendu, encoding="utf-8")
    _effacer_manquant(type_, cle)
    return f"fait  {nom} {cle} : {len(rendu.split())} mots"


def ecrire_corps(chemin: Path, type_: str, passe: str, cle: str) -> bool:
    """La réponse de la passe devient le corps de la fiche, sous son titre ; une fiche déjà écrite ne bouge pas."""
    champs, _ = fiche.lire_fichier(chemin)
    marque = f"{type_}:{passe}"
    if champs.get("procede") == marque:
        return False
    tete = f"# {champs.get('title', chemin.stem)}\n\n*{champs.get('subtitle', '')} · {champs.get('author', '')}*\n\n"
    corps = tete + sortie(type_, passe, cle).read_text(encoding="utf-8").strip() + "\n"
    champs = {k: v for k, v in champs.items() if k != "words"} | {"procede": marque}
    fiche.ecrire(chemin, champs, corps, strict=False)
    return True


def traiter(graphe: str, type_: str, decl: dict, chemin: Path) -> list[str]:
    """Les passes de texte d'une fiche, dans l'ordre ; le corps écrit après sa passe ; le texte en cache effacé quand
    toutes sont faites."""
    champs, _ = fiche.lire_fichier(chemin)
    cle = cle_de(champs[decl["contenu"]["champ"]])
    lignes, du_texte = [], [p for p in decl["passes"] if p["sortie"]]
    for p in du_texte:
        lignes.append(passer(graphe, type_, decl, p, chemin, champs, cle))
        if (
            p["sortie"] == "corps"
            and sortie(type_, p["nom"], cle).exists()
            and ecrire_corps(chemin, type_, p["nom"], cle)
        ):
            lignes.append(f"fiche {cle} : corps écrit par {p['nom']}")
    if all(sortie(type_, p["nom"], cle).exists() for p in du_texte):
        _cache_texte(cle).unlink(missing_ok=True)
    return lignes


def _faite(type_: str, decl: dict, cle: str) -> bool:
    return all(sortie(type_, p["nom"], cle).exists() for p in decl["passes"] if p["sortie"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--type", required=True, dest="type_")
    ap.add_argument("--plafond", type=int, default=10, help="fiches par passage")
    ap.add_argument("--etat", action="store_true", help="compter, sans appel")
    a = ap.parse_args(argv)
    decls = declaration_du_graphe(a.graphe)
    if a.type_ not in decls:
        raise SystemExit(f"{a.graphe} : type de contenu non déclaré : {a.type_} (déclarés : {sorted(decls)})")
    decl = decls[a.type_]
    tout = fiches(a.graphe, decl)
    attente = [f for f, _, cle in tout if not _faite(a.type_, decl, cle)]
    if a.etat:
        n = len(manquants(a.type_))
        print(
            f"{a.type_} : {len(tout)} fiches, {len(tout) - len(attente)} faites, {len(attente)} en attente, {n} manquantes"
        )
        return 0
    with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
        for lignes in pool.map(lambda f: traiter(a.graphe, a.type_, decl, f), attente[: a.plafond]):
            print("\n".join(lignes), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

#!/usr/bin/env python3
"""Les passes de texte d'un procédé d'ingestion (procedes.py ; mainteneur, 15 et 19.11.2026).

Pour chaque fiche d'un type de contenu déclaré, parmi celles que lit la couche 2 du graphe : le texte intégral (le PDF
à l'adresse déclarée, lu puis effacé), puis chaque passe, dans l'ordre, par `claude -p`. Une passe à sortie garde sa
réponse sous `graphe/procedes/<type>/<passe>/<clé>.md` ; une passe à forme répond au schéma de sa forme
(procedes_formes) et la garde en `.json`, après un tri sur le début du texte quand elle en déclare un. Une passe faite
ne repart pas. Quand toutes les passes sont faites ou refusées, la fiche s'écrit une fois : la réponse de la passe
`sortie: corps` sous son titre, puis la section de chaque forme. Elle change d'empreinte et repasse en couche 2 ; un
lot de couche 2 déjà là reprend les nœuds des formes. Un refus du modèle se note dans
`graphe/procedes/<type>/manquants.json` (« manquant, à ingérer »).

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
from newskit import procedes_formes as pf
from newskit import extraire_concepts as ec
from newskit.collecte_pdf_local import lire_pdf, nettoyer
from newskit.commun import RACINE

DELAI = 900  # secondes par appel : une lecture d'un texte de 45 000 mots prend quelques minutes
PAUSE = 3  # secondes entre deux téléchargements (arXiv en demande trois)
PARALLELE = 4
MOTS_TRI = 1500  # mots du début du texte lus par le tri (titre, résumé, introduction)
POSTE = "procede"
LIMITE = "limite d'usage atteinte"
REFUS = "refus du modèle"


def cle_de(valeur) -> str:
    """La clé d'une fiche dans un nom de fichier : la valeur de son champ de contenu, sans signe hors de [A-Za-z0-9._-]."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(valeur)).strip("_")[:80]


def dossier(type_: str) -> Path:
    return RACINE / "graphe" / "procedes" / type_


def sortie(type_: str, passe: str, cle: str, ext: str = ".md") -> Path:
    return dossier(type_) / passe / f"{cle}{ext}"


def _sortie_de(type_: str, passe: dict, cle: str) -> Path:
    return sortie(type_, passe["nom"], cle, ".json" if passe["forme"] else ".md")


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
    m.setdefault(cle, {})[passe] = f"{motif} ({time.strftime('%Y-%m-%d')})"
    _ecrire_manquants(type_, m)


def _effacer_manquant(type_: str, cle: str, passe: str) -> None:
    m = manquants(type_)
    if m.get(cle, {}).pop(passe, None) is not None:
        if not m[cle]:
            del m[cle]
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


def appeler(
    invite_: str,
    graphe: str,
    note: str,
    schema: dict | None = None,
    role: str = "extraction",
    effort: str | None = None,
) -> tuple[str | dict | None, str]:
    """(réponse, diagnostic) d'un appel à `claude -p`, consigné au registre des coûts : le texte, ou avec `schema` le
    JSON structuré. `effort` : celui que la passe déclare, sinon celui de `claude -p`."""
    if couts.en_limite():
        return None, LIMITE
    modele = media.modele(role)
    args = [*couts.CLAUDE_P, "--no-session-persistence", "--tools", "", "--output-format", "json", "--model", modele]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    if effort:
        args += ["--effort", effort]
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
    if schema:
        s = env.get("structured_output")
        return (s, "ok") if isinstance(s, dict) else (None, "réponse sans JSON")
    return str(env.get("result") or ""), "ok"


def deja_faite(type_: str, passe: dict, cle: str) -> bool:
    out = _sortie_de(type_, passe, cle)
    return out.exists() and (bool(passe["forme"]) or len(out.read_text(encoding="utf-8").split()) >= passe["mots_min"])


def tri_retenu(type_: str, passe: dict, cle: str) -> bool:
    """Le tri de la passe a déjà retenu la fiche : il ne repart pas, même si la lecture entière a échoué."""
    return sortie(type_, passe["nom"], cle, ".tri").exists()


def garder_tri(type_: str, passe: dict, cle: str, verdict: dict) -> bool:
    """Garde le verdict du tri : la marque d'une fiche retenue, la forme vide d'une fiche écartée. Vrai si retenue."""
    retenu = bool(verdict.get("retenu"))
    out = sortie(type_, passe["nom"], cle, ".tri") if retenu else _sortie_de(type_, passe, cle)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("retenu\n" if retenu else json.dumps(pf.VIDE[passe["forme"]], ensure_ascii=False), encoding="utf-8")
    return retenu


def garder(type_: str, passe: dict, cle: str, rendu) -> str | None:
    """Garde la réponse d'une passe ; le motif du rejet, ou None. Une forme veut un objet JSON, une sortie au moins
    `mots_min` mots."""
    nom, forme = passe["nom"], passe["forme"]
    if forme and not isinstance(rendu, dict):
        return "réponse sans JSON"
    if not forme and len(rendu.split()) < passe["mots_min"]:
        return f"réponse de {len(rendu.split())} mots, {passe['mots_min']} attendus"
    out = _sortie_de(type_, passe, cle)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rendu, ensure_ascii=False, indent=1) if forme else rendu, encoding="utf-8")
    _effacer_manquant(type_, cle, nom)
    return None


def passer(graphe: str, type_: str, decl: dict, passe: dict, chemin: Path, champs: dict, cle: str) -> str:
    """Une passe sur une fiche ; la ligne de compte rendu."""
    nom, forme = passe["nom"], passe["forme"]
    if deja_faite(type_, passe, cle):
        return f"déjà  {nom} {cle}"
    t = texte(chemin, champs, cle, decl)
    if t is None:
        return f"!! {nom} {cle} : pas de texte"
    note = f"{type_}:{nom} {cle}"
    if passe["tri"] and not tri_retenu(type_, passe, cle):
        tri = passe["tri"].read_text(encoding="utf-8")
        verdict, diag = appeler(invite(tri, t, MOTS_TRI), graphe, f"{note} (tri)", pf.SCHEMA_TRI, "lecture")
        if verdict is None:
            return f"!! {nom} {cle} : tri sans réponse ({diag})"
        if not garder_tri(type_, passe, cle, verdict):
            return f"écartée {nom} {cle} : le tri ne la retient pas"
    consigne = passe["consigne"].read_text(encoding="utf-8")
    schema = pf.SCHEMAS[forme] if forme else None
    rendu, diag = appeler(invite(consigne, t, decl["texte"]["mots_max"]), graphe, note, schema, effort=passe["effort"])
    if rendu is None:
        if diag == REFUS:
            noter_manquant(type_, cle, nom, REFUS)
        return f"!! {nom} {cle} : {diag}"
    rejet = garder(type_, passe, cle, rendu)
    if rejet:
        return f"!! {nom} {cle} : {rejet}"
    return f"fait  {nom} {cle}" + ("" if forme else f" : {len(rendu.split())} mots")


def _terminee(type_: str, passe: dict, cle: str) -> bool:
    """Faite, ou refusée par le modèle."""
    return _sortie_de(type_, passe, cle).exists() or passe["nom"] in manquants(type_).get(cle, {})


def ecrire_corps(chemin: Path, type_: str, decl: dict, cle: str) -> bool:
    """La fiche sous son titre : la réponse de la passe en corps, puis la section de chaque forme faite. Une fiche
    déjà écrite ainsi ne bouge pas."""
    champs, actuel = fiche.lire_fichier(chemin)
    corps_ = next(p for p in decl["passes"] if p["sortie"] == "corps")
    marque = f"{type_}:{corps_['nom']}"
    sections = [sortie(type_, corps_["nom"], cle).read_text(encoding="utf-8").strip()]
    for p in decl["passes"]:
        f = _sortie_de(type_, p, cle)
        if p["forme"] and f.exists():
            sections.append(pf.rendre(p["forme"], json.loads(f.read_text(encoding="utf-8"))))
    tete = f"# {champs.get('title', chemin.stem)}\n\n*{champs.get('subtitle', '')} · {champs.get('author', '')}*\n\n"
    corps = tete + "\n\n".join(s for s in sections if s) + "\n"
    if champs.get("procede") == marque and actuel.strip() == corps.strip():
        return False
    champs = {k: v for k, v in champs.items() if k != "words"} | {"procede": marque}
    fiche.ecrire(chemin, champs, corps, strict=False)
    return True


def reposer_lot(chemin: Path, type_: str, decl: dict) -> bool:
    """Le lot de couche 2 déjà écrit de la fiche reprend les nœuds de ses formes."""
    from newskit import extraire_concepts as ec_  # noqa: PLC0415

    champs, _ = fiche.lire_fichier(chemin)
    lot = ec_.LOTS / f"{ec_.identifiant_de_fiche(chemin, champs)}.json"
    if not lot.exists():
        return False
    avant = json.loads(lot.read_text(encoding="utf-8"))
    apres = pf.composer_lot(avant, chemin, champs, type_, decl)
    if apres == avant:
        return False
    lot.write_text(json.dumps(apres, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


def traiter(graphe: str, type_: str, decl: dict, chemin: Path) -> list[str]:
    """Les passes d'une fiche, dans l'ordre. Quand toutes sont terminées : la fiche écrite une fois, son lot repris,
    le texte en cache effacé."""
    champs, _ = fiche.lire_fichier(chemin)
    cle = cle_de(champs[decl["contenu"]["champ"]])
    return [passer(graphe, type_, decl, p, chemin, champs, cle) for p in decl["passes"]] + achever(
        type_, decl, chemin, cle
    )


def achever(type_: str, decl: dict, chemin: Path, cle: str) -> list[str]:
    """Quand toutes les passes de la fiche sont terminées : la fiche écrite une fois, son lot repris, le texte en
    cache effacé. Rien avant."""
    lignes: list[str] = []
    if not all(_terminee(type_, p, cle) for p in decl["passes"]):
        return lignes
    corps_ = [p for p in decl["passes"] if p["sortie"] == "corps"]
    if corps_ and sortie(type_, corps_[0]["nom"], cle).exists() and ecrire_corps(chemin, type_, decl, cle):
        lignes.append(f"fiche {cle} : écrite")
    if reposer_lot(chemin, type_, decl):
        lignes.append(f"lot {cle} : formes reposées")
    _cache_texte(cle).unlink(missing_ok=True)
    return lignes


def _faite(type_: str, decl: dict, cle: str) -> bool:
    return all(_terminee(type_, p, cle) for p in decl["passes"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--type", required=True, dest="type_")
    ap.add_argument("--plafond", type=int, default=10, help="fiches par passage")
    ap.add_argument("--etat", action="store_true", help="compter, sans appel")
    a = ap.parse_args(argv)
    ec.viser(a.graphe)  # les lots que reprennent les formes sont ceux du graphe
    decls = declaration_du_graphe(a.graphe)
    if a.type_ not in decls:
        raise SystemExit(f"{a.graphe} : type de contenu non déclaré : {a.type_} (déclarés : {sorted(decls)})")
    decl = decls[a.type_]
    if decl["appel"] == "lots" and not a.etat:
        raise SystemExit(f"{a.type_} : déclaré `appel: lots` ; passer par python3 -m newskit.procedes_lots")
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

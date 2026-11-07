#!/usr/bin/env python3
"""Le plan du déménagement d'un graphe fusionné (GV7 de CHANTIER_GOUVERNANCE.md, mainteneur, 2 novembre 2026 : « ajoute-le
au GV7 », puis « tu peux passer à gv7 »).

Depuis GV4, le graphe fusionné (`inclut` et `partage` au registre) lit les dossiers et les lots des graphes qu'il
reprend. Pour qu'il collecte, extraie et serve lui-même, ses fiches, leurs lots d'extraction et leurs sources passent
sous son nom ; la part qui ne lui revient pas va à l'archive (GV5), et la part du graphe voisin qui n'est pas rangée
chez lui reste au voisin. Ce script calcule ce plan, fiche par fiche, sans rien écrire et sans appel au modèle :

- la destination d'une fiche vient du rangement (partage.json de GV2) : le graphe fusionné, l'archive, `reste` (fiche
  du voisin qui ne change pas de graphe) ou `a_revoir` (jamais une destination devinée) ; une fiche collectée après le
  rangement suit la majorité de sa source, et une fiche neuve du voisin reste au voisin ;
- deux fiches d'un même corps, ou d'une même adresse et d'un texte presque identique (règles de partage_graphe), n'en
  font qu'une : la plus ancienne ; l'autre est `doublon`, et son lot est écarté ;
- chaque lot suit sa fiche (graphe/<avant>/couche2/fiches/<id>.json → graphe/<après>/…) ; un lot dont la fiche
  n'existe plus est `orphelin` ;
- une source va dans un seul graphe, celui de la majorité de ses fiches ; les fiches qui vont ailleurs sont nommées
  (`exceptions`) : elles demandent de scinder la source, ou une décision.

Les fiches ne changent pas de chemin dans ce plan : leur chemin fait leur identifiant, celui de leur lot, de leurs nœuds
et des références des synthèses. Le plan dit aussi ce que les graphes inclus portent d'autre (synthèses, alias, bancs,
encodeur, cerveau, daemon).

    python3 -m newskit.demenager_gouvernance --graphe <fusion> --archive <archive>                     # bilan
    python3 -m newskit.demenager_gouvernance --graphe <fusion> --archive <archive> --sortie <plan.json>  # plan entier
    python3 -m newskit.demenager_gouvernance --graphe <fusion> --archive <archive> --appliquer           # l'appliquer

Une source sans fiche suit la destination donnée à son graphe d'origine (--sans-fiche <graphe>=<destination>, répétable ;
pour la gouvernance, mainteneur, 2 novembre 2026 : conformité et éthique vers le graphe fusionné, sécurité vers
l'archive).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from newskit import partage_graphe as pgr

RESTE, A_REVOIR, DOUBLON, ECARTE, ORPHELIN = "reste", "a_revoir", "doublon", "ecarte", "orphelin"
def destinations(fusion: str, archive: str) -> set[str]:
    return {fusion, archive, RESTE, A_REVOIR, DOUBLON}


def _du_rangement(valeur: str | None, fusion: str, archive: str) -> str | None:
    if valeur is None:
        return None
    tete = valeur.split(":", 1)[0]
    return {"gouvernance": fusion, "archive": archive, "reste": RESTE}.get(tete, A_REVOIR)


def _majorite(compte: Counter, ordre: list[str]) -> str | None:
    if not compte:
        return None
    return min(compte, key=lambda d: (-compte[d], ordre.index(d) if d in ordre else len(ordre), d))


def doublons_gardes(fiches: list[dict]) -> dict[str, str]:
    """{fiche écartée: fiche gardée}, avec les règles de partage_graphe.doublons (même corps ; même adresse et texte
    presque identique), la plus ancienne gardée (date, puis chemin)."""
    def ordre(f):
        return (f.get("date") or "9999", f["chemin"])
    out: dict[str, str] = {}
    par_corps, par_url = defaultdict(list), defaultdict(list)
    for f in sorted(fiches, key=ordre):
        if e := pgr._empreinte(f.get("corps") or ""):
            par_corps[e].append(f)
        if u := pgr.url_normalisee(f.get("url") or ""):
            par_url[u].append(f)
    for fs in par_corps.values():
        for f in fs[1:]:
            out[f["chemin"]] = fs[0]["chemin"]
    for fs in par_url.values():
        gardees: list[dict] = []
        for f in fs:
            if f["chemin"] in out:
                continue
            g = next((g for g in gardees if pgr._meme_texte(f, g)), None)
            if g is not None:
                out[f["chemin"]] = g["chemin"]
            else:
                gardees.append(f)
    for perdu, garde in list(out.items()):          # chaînes : la gardée d'une écartée peut l'être elle-même
        while garde in out:
            garde = out[garde]
        out[perdu] = garde
    assert set(out) == pgr.doublons(fiches), "règles de doublon divergentes de partage_graphe"
    return dict(sorted(out.items()))


def planifier(fiches: list[dict], lots: dict[str, dict], partage: dict[str, str], inclus: list[str],
              fusion: str, archive: str, vides: list[tuple[str, str]] | None = None,
              sans_fiche: dict[str, str] | None = None) -> dict:
    """Le plan : `fiches` {chemin: destination…}, `lots` {id: destination, avant, après}, `sources` {clé: destination,
    exceptions}, `bilan`. `fiches` : chemin, graphe, source, url, date, corps ; `lots` : {id: {graphe, fiche, avant?}}."""
    ordre = [fusion, archive, RESTE, A_REVOIR]
    rangees = {f["chemin"]: _du_rangement(partage.get(f["chemin"]), fusion, archive) for f in fiches}
    par_source: dict[str, Counter] = defaultdict(Counter)
    for f in fiches:
        if rangees[f["chemin"]] not in (None, A_REVOIR):
            par_source[f["source"]][rangees[f["chemin"]]] += 1
    dest: dict[str, dict] = {}
    for f in fiches:
        d = rangees[f["chemin"]]
        if d is None:                                # collectée après le rangement
            d = (_majorite(par_source[f["source"]], ordre) or A_REVOIR) if f["graphe"] in inclus else RESTE
        dest[f["chemin"]] = {"destination": d, "graphe": f["graphe"], "source": f["source"],
                             "rangement": partage.get(f["chemin"])}
    for perdu, garde in doublons_gardes([f for f in fiches if dest[f["chemin"]]["destination"] == fusion]).items():
        dest[perdu].update(destination=DOUBLON, garde=garde)

    sources: dict[str, dict] = {}
    membres: dict[str, list[str]] = defaultdict(list)
    for c, v in dest.items():
        membres[v["source"]].append(c)
    for s in sorted(membres):
        compte = Counter(dest[c]["destination"] for c in membres[s] if dest[c]["destination"] != DOUBLON)
        d = _majorite(compte, ordre) or fusion       # une source dont toutes les fiches sont des doublons
        graphes = sorted({dest[c]["graphe"] for c in membres[s]})
        sources[s] = {"destination": d, "graphe_avant": graphes[0] if len(graphes) == 1 else graphes,
                      "fiches": len(membres[s]),
                      "exceptions": sorted(c for c in membres[s] if dest[c]["destination"] not in (d, DOUBLON))}

    # Une source sans fiche n'a pas de majorité : elle suit la destination donnée à son graphe d'origine (mainteneur,
    # 2 novembre 2026, « ok, d'accord pour les 28 sources »), sinon elle est à revoir.
    vides_comptees: Counter = Counter()
    for cle, g in sorted(vides or []):
        d = (sans_fiche or {}).get(g, A_REVOIR)
        sources[cle] = {"destination": d, "graphe_avant": g, "fiches": 0, "exceptions": []}
        vides_comptees[d] += 1

    plan_lots: dict[str, dict] = {}
    for ident in sorted(lots):
        lot = lots[ident]
        avant = lot.get("avant") or f"graphe/{lot['graphe']}/couche2/fiches/{ident}.json"
        f = dest.get(lot.get("fiche") or "")
        if f is None:
            plan_lots[ident] = {"destination": ORPHELIN, "fiche": lot.get("fiche"), "avant": avant, "apres": avant}
            continue
        d = {DOUBLON: ECARTE}.get(f["destination"], f["destination"])
        apres = f"graphe/{d}/couche2/fiches/{ident}.json" if d in (fusion, archive) else avant
        plan_lots[ident] = {"destination": d, "fiche": lot["fiche"], "avant": avant, "apres": apres}

    bilan = {"fiches": dict(sorted(Counter(v["destination"] for v in dest.values()).items())),
             "lots": dict(sorted(Counter(v["destination"] for v in plan_lots.values()).items())),
             "sources": dict(sorted(Counter(v["destination"] for v in sources.values()).items())),
             "sources_mixtes": sorted(s for s, v in sources.items() if v["exceptions"]),
             "fiches_en_exception": sum(len(v["exceptions"]) for v in sources.values()),
             "sources_sans_fiche": dict(sorted(vides_comptees.items())),
             "fiches_sans_lot": sorted(set(dest) - {v["fiche"] for v in plan_lots.values()})}
    bilan["fiches_sans_lot"] = len(bilan["fiches_sans_lot"])
    return {"fiches": dest, "lots": plan_lots, "sources": sources, "bilan": bilan}


# --- L'application -------------------------------------------------------------------------------
# Mainteneur, 2 novembre 2026 (« 1 ok mixte, 2 ok, 3 ok, 4 ok ») : chaque source va au graphe de sa majorité, le partage
# fiche par fiche reste le filtre du graphe d'accueil, les fichiers des fiches ne bougent pas, rien n'est effacé.

JOURNAL = "demenagement.json"


def rattachements(texte: str) -> list[tuple[str, str]]:
    """(clé, graphe) de chaque source du registre, dans l'ordre du fichier."""
    out = []
    for m in re.finditer(r"^  - cle: (\S+)[ \t]*$", texte, flags=re.MULTILINE):
        fin = re.search(r"^(  - cle: |\S)", texte[m.end():], flags=re.MULTILINE)
        bloc = texte[m.end(): m.end() + fin.start()] if fin else texte[m.end():]
        g = re.search(r"^    graphe: (\S+)[ \t]*$", bloc, flags=re.MULTILINE)
        out.append((m.group(1), g.group(1) if g else ""))
    return out


def _bornes_source(texte: str, cle: str) -> tuple[int, int]:
    m = re.search(rf"^  - cle: {re.escape(cle)}[ \t]*$", texte, flags=re.MULTILINE)
    fin = re.search(r"^(  - cle: |\S)", texte[m.end():], flags=re.MULTILINE)
    return m.start(), (m.end() + fin.start()) if fin else len(texte)


def _bornes_graphe(texte: str, nom: str) -> tuple[int, int] | None:
    m = re.search(rf"^  {re.escape(nom)}:[ \t]*$", texte, flags=re.MULTILINE)
    if not m:
        return None
    fin = re.search(r"^(  \S|\S)", texte[m.end():], flags=re.MULTILINE)
    return m.start(), (m.end() + fin.start()) if fin else len(texte)


def bloc_graphe(texte: str, nom: str) -> str:
    b = _bornes_graphe(texte, nom)
    return texte[b[0]:b[1]] if b else ""


def _a_rattacher(plan: dict, texte: str, fusion: str, archive: str) -> list[tuple[str, str, str]]:
    actuels = dict(rattachements(texte))
    return [(c, actuels[c], v["destination"]) for c, v in sorted(plan["sources"].items())
            if v["destination"] in (fusion, archive) and c in actuels and actuels[c] != v["destination"]]


def restant(plan: dict, racine: Path, fusion: str, archive: str) -> dict:
    """Ce que l'application a encore à faire : sources à rattacher, lots à recopier, `inclut` à retirer."""
    texte = (racine / "sources.yaml").read_text(encoding="utf-8")
    sources = [c for c, _, _ in _a_rattacher(plan, texte, fusion, archive)]
    lots = sorted(i for i, v in plan["lots"].items()
                  if v["destination"] in (fusion, archive) and not (racine / v["apres"]).exists())
    decl = [g for g in (fusion, archive) if re.search(r"^    inclut:", bloc_graphe(texte, g), flags=re.MULTILINE)]
    return {"sources": sources, "lots": lots, "declarations": decl, "total": len(sources) + len(lots) + len(decl)}


def _lire_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def appliquer(plan: dict, racine: Path, fusion: str, archive: str, socle: list[str]) -> dict:
    """Rattache les sources, recopie les lots et leurs empreintes, retire `inclut` des deux graphes d'accueil, tient
    le journal du déménagement. Idempotent : une seconde application ne change rien. Rend le bilan."""
    registre = racine / "sources.yaml"
    texte = registre.read_text(encoding="utf-8")
    a_faire = _a_rattacher(plan, texte, fusion, archive)
    for cle, de, vers in a_faire:
        debut, fin = _bornes_source(texte, cle)
        bloc, n = re.subn(rf"^    graphe: {re.escape(de)}[ \t]*$", f"    graphe: {vers}", texte[debut:fin], count=1,
                          flags=re.MULTILINE)
        if n != 1:
            raise SystemExit(f"{cle} : champ « graphe: {de} » introuvable dans son bloc")
        texte = texte[:debut] + bloc + texte[fin:]
    for g in (fusion, archive):
        b = _bornes_graphe(texte, g)
        if b:
            bloc = re.sub(r"^    inclut:.*\n", "", texte[b[0]:b[1]], flags=re.MULTILINE)
            texte = texte[:b[0]] + bloc + texte[b[1]:]
    copies = 0
    empreintes: dict[Path, dict] = {}
    for ident, v in sorted(plan["lots"].items()):
        if v["destination"] not in (fusion, archive):
            continue
        src, dst = racine / v["avant"], racine / v["apres"]
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copies += 1
        de = _lire_json(src.parent.parent / "empreintes.json")
        p = dst.parent.parent / "empreintes.json"
        vers = empreintes.setdefault(p, _lire_json(p))
        if v["fiche"] in de and v["fiche"] not in vers:
            vers[v["fiche"]] = de[v["fiche"]]
    for p, e in empreintes.items():
        neuf = json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True)
        if not p.exists() or p.read_text(encoding="utf-8") != neuf:
            p.write_text(neuf, encoding="utf-8")
    if texte != registre.read_text(encoding="utf-8"):
        tmp = registre.with_suffix(".yaml.neuf")
        tmp.write_text(texte, encoding="utf-8")
        tmp.replace(registre)
    jp = racine / "graphe" / fusion / JOURNAL
    j = _lire_json(jp) or {"socle": list(socle), "archive": archive, "sources": {}, "passages": []}
    for cle, de, vers in a_faire:
        j["sources"].setdefault(cle, {"avant": de, "apres": vers})
    bilan = {"sources_rattachees": len(a_faire), "lots_copies": copies}
    j["passages"].append({"date": time.strftime("%Y-%m-%d %H:%M:%S"), **bilan})
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    return bilan


# --- Lecture des vraies données --------------------------------------------------------------------

def _voisins(par_source: list[dict], inclus: list[str]) -> list[str]:
    return sorted({x["graphe"] for x in par_source} - set(inclus))


def charger(fusion: str) -> tuple[list[dict], dict[str, dict], dict[str, str], list[str], list[tuple[str, str]]]:
    """Les fiches, les lots et le rangement, avant comme après l'application : le socle et le graphe d'origine de chaque
    source viennent de `inclut` tant qu'il est là, puis du journal du déménagement."""
    from newskit import config_sources as cfg
    from newskit import extraire_concepts as ec
    from newskit import fiche as fi
    decl = cfg.graphe_declare(fusion)
    journal = _lire_json(cfg.chemins_du_graphe(fusion)["base"] / JOURNAL)
    inclus = cfg.graphes_inclus(fusion) or list(journal.get("socle") or [])
    origine = {c: v["avant"] for c, v in (journal.get("sources") or {}).items()}
    fichier = cfg.RACINE / decl["partage"]["fichier"]
    partage = json.loads(fichier.read_text(encoding="utf-8"))
    par_source = json.loads((fichier.parent / "par_source.json").read_text(encoding="utf-8"))
    voisins = _voisins(par_source, inclus)
    rangees_voisin = {(x["graphe"], x["source"]) for x in par_source if x["graphe"] in voisins}
    a_lire: list[tuple[str, str, str]] = []                    # (dossier, graphe d'origine, source)
    for s in cfg.toutes():
        g = origine.get(s.cle, s.graphe)
        if g in inclus or (g, s.cle) in rangees_voisin:
            a_lire.append((f"sources/{s.dossier}", g, s.cle))
    veille = cfg.charger()["veille_arxiv"]
    for g in inclus:
        v = (veille.get("graphes") or {}).get(g)
        if v and v.get("dossier"):
            a_lire.append((f"sources/{v['dossier']}", g, f"dossier:sources/{v['dossier']}"))
    fiches, ident_vers_fiche, lues = [], {}, set()
    avec_fiche: set[str] = set()
    for d, g, cle in sorted(set(a_lire)):
        for p in sorted((cfg.RACINE / d).rglob("*.md")):
            rel = p.relative_to(cfg.RACINE).as_posix()
            if p.name.startswith("_") or rel in lues:
                continue
            entete, corps = fi.lire_fichier(p)
            lues.add(rel)
            fiches.append({"chemin": rel, "graphe": g, "source": cle, "url": str(entete.get("url") or ""),
                           "date": str(entete.get("date") or ""), "corps": corps})
            ident_vers_fiche[(g, ec.identifiant_de_fiche(p, entete))] = rel
            avec_fiche.add(cle)
    lots: dict[str, dict] = {}
    for g in inclus + voisins:
        base = cfg.chemins_du_graphe(g)["couche2"] / "fiches"
        idents = (sorted(p.stem for p in base.glob("*.json")) if g in inclus
                  else sorted(i for (gg, i) in ident_vers_fiche if gg == g and (base / f"{i}.json").exists()))
        for i in idents:
            rel = ident_vers_fiche.get((g, i))
            if rel is None:                                    # pas de fiche sous cet identifiant : le lot la nomme-t-il ?
                try:
                    noeuds = json.loads((base / f"{i}.json").read_text(encoding="utf-8")).get("nodes") or []
                except (OSError, json.JSONDecodeError):
                    noeuds = []
                rel = next((pgr._relatif(n.get("source_file"), cfg.RACINE) for n in noeuds if n.get("source_file")), None)
                # Une fiche d'une source retirée (gov_km_mollick, mainteneur, 2 novembre 2026, « 4 ok ») : rangée, sur le
                # disque, dans aucun dossier déclaré ; elle suit son rangement, et sa source n'a pas de bloc à rattacher.
                if rel and rel not in lues and rel in partage and (cfg.RACINE / rel).exists():
                    entete, corps = fi.lire_fichier(cfg.RACINE / rel)
                    lues.add(rel)
                    fiches.append({"chemin": rel, "graphe": g, "source": f"retiree:{Path(rel).parent.as_posix()}",
                                   "url": str(entete.get("url") or ""), "date": str(entete.get("date") or ""),
                                   "corps": corps})
            cle = i if i not in lots else f"{g}/{i}"
            lots[cle] = {"graphe": g, "fiche": rel, "avant": (base / f"{i}.json").relative_to(cfg.RACINE).as_posix()}
    vides = sorted({(cle, g) for _, g, cle in a_lire if g in inclus and cle not in avec_fiche
                    and not cle.startswith("dossier:")})
    return fiches, lots, partage, inclus, vides


def routines_en_cours() -> list[str]:
    """Les processus de routine qui tournent : l'application attend qu'ils aient fini."""
    import subprocess
    motif = ("routine_hebdo|extraire_concepts|fusionner_graphe|extraire_squelette|synthese.py|cerveau_cortexdb|"
             "puits_cortexdb|banc.py|banc_croise")
    r = subprocess.run(["pgrep", "-fl", motif], check=False, capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if "pgrep" not in x]


def inventaire(inclus: list[str], fusion: str) -> list[dict]:
    """Ce que chaque graphe inclus porte en plus des fiches et des lots, et s'il a déjà son équivalent fusionné."""
    from newskit import config_sources as cfg
    out = []
    for g in inclus:
        base = cfg.chemins_du_graphe(g)["base"]
        for p in sorted(base.iterdir()) if base.exists() else []:
            if p.name == "couche2":
                continue
            out.append({"graphe": g, "chemin": p.relative_to(cfg.RACINE).as_posix(),
                        "dans_la_fusion": (cfg.chemins_du_graphe(fusion)["base"] / p.name).exists()})
        for extra in (cfg.RACINE / "cache" / "encodeur" / g, cfg.RACINE / "cache" / "cortexdb" / f"{g}.db"):
            if extra.exists():
                out.append({"graphe": g, "chemin": extra.relative_to(cfg.RACINE).as_posix(), "dans_la_fusion": False})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True, help="le graphe fusionné (`inclut` et `partage` au registre)")
    ap.add_argument("--archive", required=True, help="le graphe d'archive, déclaré ou à déclarer (GV5)")
    ap.add_argument("--plan", action="store_true", help="le défaut : calculer le plan, rien n'est écrit dans le dépôt")
    ap.add_argument("--sortie", type=Path, help="écrire le plan entier dans ce fichier JSON")
    ap.add_argument("--appliquer", action="store_true",
                    help="rattacher les sources, recopier les lots, retirer `inclut` (rien n'est effacé ; idempotent)")
    ap.add_argument("--sans-fiche", action="append", default=[], metavar="GRAPHE=DESTINATION",
                    help="destination d'une source sans fiche, selon son graphe d'origine (répétable)")
    a = ap.parse_args(argv)
    sans_fiche = dict(x.split("=", 1) for x in a.sans_fiche)
    fiches, lots, partage, inclus, vides = charger(a.graphe)
    p = planifier(fiches, lots, partage, inclus, a.graphe, a.archive, vides, sans_fiche)
    p["autres"] = inventaire(inclus, a.graphe)
    b = p["bilan"]
    print(f"  graphes inclus : {', '.join(inclus)} ; fiches lues : {len(fiches)} ; lots : {len(lots)}")
    print(f"  fiches par destination : {b['fiches']}")
    print(f"  lots par destination : {b['lots']} ; fiches sans lot : {b['fiches_sans_lot']}")
    print(f"  sources sans fiche : {b['sources_sans_fiche']}")
    print(f"  sources par destination : {b['sources']} ; sources mixtes : {len(b['sources_mixtes'])}, "
          f"{b['fiches_en_exception']} fiche(s) hors du graphe de leur source")
    print(f"  autres pièces des graphes inclus : {len(p['autres'])}, dont {sum(x['dans_la_fusion'] for x in p['autres'])} "
          f"déjà présentes sous {a.graphe}")
    from newskit import config_sources as cfg
    r = restant(p, cfg.RACINE, a.graphe, a.archive)
    print(f"  à faire : {len(r['sources'])} source(s) à rattacher, {len(r['lots'])} lot(s) à recopier, "
          f"`inclut` à retirer de {r['declarations'] or 'aucun'}")
    if a.appliquer:
        if en_cours := routines_en_cours():
            print("  une routine tourne, rien n'est appliqué :\n    " + "\n    ".join(en_cours), file=sys.stderr)
            return 1
        avant = cfg.FICHIER.read_text(encoding="utf-8")
        b2 = appliquer(p, cfg.RACINE, a.graphe, a.archive, inclus)
        try:
            cfg.charger(cfg.FICHIER)                       # le registre réécrit doit rester valide
        except Exception:
            cfg.FICHIER.write_text(avant, encoding="utf-8")
            raise
        print(f"  appliqué : {b2} ; reste : {restant(p, cfg.RACINE, a.graphe, a.archive)['total']}")
    if a.sortie:
        a.sortie.write_text(json.dumps({k: v for k, v in p.items()}, ensure_ascii=False, indent=1, sort_keys=True),
                            encoding="utf-8")
        print(f"  plan écrit : {a.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

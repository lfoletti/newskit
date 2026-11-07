#!/usr/bin/env python3
"""L'avance d'un graphe sur un modèle de langage : deux mesures, par sujet déclaré.

Le but des graphes, fixé par le porteur le 04.10.2026 : être plus complets et plus à jour
qu'un modèle à qui l'on poserait la question. Cela se mesure, donc cela se mesure ici.

    complétude   on demande au modèle, sans outil, les notions qu'il tient pour essentielles
                 sur chaque sujet déclaré du graphe (son « canon »), et on compte celles que
                 le graphe porte. Au-dessous de 100 %, le modèle en sait plus que le graphe
                 sur ce sujet ; la liste des absentes est la liste de ce qu'il faut ingérer.

    avance       on prend les notions du graphe dont toutes les sources sont postérieures à
                 l'horizon du modèle, on lui en soumet un échantillon tiré source par source,
                 et on compte celles qu'il dit ne pas connaître. C'est sa déclaration, pas une
                 preuve : un modèle peut croire connaître une version qu'il n'a jamais vue.

    rendement    par source : la part de ses notions récentes que le modèle ignore ET qui
                 relèvent d'un sujet déclaré. Nouveau ne veut pas dire utile : une source
                 d'anecdotes a une forte avance et un rendement nul. C'est ce chiffre qui dit
                 quelles sources nourrir, filtrer ou retirer.

Un graphe est « en avance » quand la complétude approche 100 % et que l'avance est grande.

    python3 -m newskit.mesurer_avance --graphe <graphe>            # mesure, écrit graphe/<nom>/avance.json
    python3 -m newskit.mesurer_avance --graphe <graphe> --etat     # relit la dernière mesure

Deux appels à `claude -p` par mesure (abonnement, sans outil). L'horizon par défaut est
celui du modèle employé le jour de l'écriture ; le passer par --horizon s'il change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

PAR_SUJET = 15
PAR_SUJET_CRITIQUE = 8  # le volet critique du canon, pour les graphes qui le demandent (canon_critique)
PAR_SOURCE = 8  # notions récentes soumises au modèle, par source
ECHANTILLON = 240  # plafond de l'échantillon entier
CANON_JOURS = 30  # le canon du modèle (ce qu'il tient pour essentiel) est redemandé au plus tous les 30 jours
HORIZON = cfg.HORIZON_MODELE
te.DELAI = 600  # un seul appel long vaut mieux que quinze courts

SCHEMA_CANON = {
    "type": "object",
    "required": ["sujets"],
    "properties": {
        "sujets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["sujet", "notions"],
                "properties": {"sujet": {"type": "integer"}, "notions": {"type": "array", "items": {"type": "string"}}},
            },
        }
    },
}
SCHEMA_CONNU = {
    "type": "object",
    "required": ["jugements"],
    "properties": {
        "jugements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "inconnu", "hors_sujet"],
                "properties": {
                    "i": {"type": "integer"},
                    "inconnu": {"type": "boolean"},
                    "hors_sujet": {"type": "boolean"},
                },
            },
        }
    },
}


def norme(texte: str) -> str:
    return " ".join(sorted(te.mots(texte, 2)))


def porte_par_le_graphe(terme: str, racines_des_noeuds: list[set[str]], textes: list[str]) -> bool:
    """Une notion du canon est portée si une étiquette du graphe contient toutes ses racines, ou son texte."""
    r = te.mots(terme, 2)
    bas = re.sub(r"[^a-z0-9]+", " ", terme.lower()).strip()
    return bool(r) and (any(r <= n for n in racines_des_noeuds) or any(bas in t for t in textes))


def date_de(chemin: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", Path(chemin).name)
    return m.group(1) if m else ""


def source_de(chemin: str, noms: dict[str, str]) -> str:
    """Le nom de la source dont le dossier contient cette fiche (le plus long dossier qui la préfixe)."""
    rel = chemin[chemin.index("sources/") :] if "sources/" in chemin else chemin
    for dossier in sorted(noms, key=len, reverse=True):
        if rel.startswith(dossier + "/"):
            return noms[dossier]
    return "(autre)"


def notions_posterieures(G, horizon: str, noms: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """(étiquette, source) des notions dont toutes les fiches qui les attestent sont datées après l'horizon."""
    out = []
    for n in G:
        if dg._type(G, n) not in dg.TYPES_CONCEPT:
            continue
        fiches = {str(d.get("source_file") or "") for _, _, d in G.edges(n, data=True)}
        fiches |= {str(G.nodes[n].get("source_file") or "")}
        # Une fiche du canon porte la date de sa page de référence, pas celle de la notion : hors compte.
        datees = sorted((date_de(f), f) for f in fiches if date_de(f) and "sources/canon/" not in f)
        if datees and datees[0][0] > horizon:
            out.append((str(G.nodes[n].get("label") or n), source_de(datees[0][1], noms or {})))
    return out


def empreinte_sujets(sujets: list[str]) -> str:
    return hashlib.sha1(
        (f"{te.MODELE}|{PAR_SUJET}|{PAR_SUJET_CRITIQUE}|" + "\n".join(sujets)).encode("utf-8")
    ).hexdigest()[:12]


def canon_en_memoire(base: Path, sujets: list[str]) -> dict | None:
    """Le canon du modèle gardé (avance_canon.json) s'il a moins de CANON_JOURS jours pour ce modèle et ces sujets."""
    p = base / "avance_canon.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if d.get("empreinte") != empreinte_sujets(sujets) or not d.get("canon"):
        return None
    age = (time.time() - time.mktime(time.strptime(d.get("date", "1970-01-01"), "%Y-%m-%d"))) / 86400
    return d if age < CANON_JOURS else None


def ecrire_canon(base: Path, sujets: list[str], canon: dict | None, critique: dict | None) -> None:
    if canon:
        (base / "avance_canon.json").write_text(
            json.dumps(
                {
                    "date": time.strftime("%Y-%m-%d"),
                    "modele": te.MODELE,
                    "empreinte": empreinte_sujets(sujets),
                    "canon": canon,
                    "critique": critique,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )


def mesurer(graphe: str, horizon: str) -> dict | None:
    G = te.charger(graphe)
    sujets = te.sujets_declares(graphe)
    if G is None or not sujets:
        print(f"  {graphe} : graphe ou sujets absents", file=sys.stderr)
        return None
    etiquettes = [str(G.nodes[n].get("label") or n) for n in G if dg._type(G, n) != "auteur"]
    racines = [te.mots(e, 2) for e in etiquettes]
    textes = [re.sub(r"[^a-z0-9]+", " ", e.lower()) for e in etiquettes]
    decl = cfg.graphe_declare(graphe)
    p_canon = cfg.chemins_du_graphe(graphe)["base"] / "canon.json"
    alias = set(json.loads(p_canon.read_text(encoding="utf-8")).get("alias", {})) if p_canon.exists() else set()
    base = cfg.chemins_du_graphe(graphe)["base"]
    te.POSTE, te.GRAPHE_COURANT = "avance", graphe  # registre des coûts

    # Le canon du modèle — ce qu'il tient pour essentiel, de mémoire — est une propriété du modèle et des sujets, pas
    # du graphe : il était redemandé chaque nuit (08.10.2026). Il est gardé CANON_JOURS jours pour un même modèle et
    # les mêmes sujets ; seule la part que le graphe porte est recalculée.
    canon = canon_en_memoire(base, sujets)
    if canon:
        rep = canon["canon"]
        print(
            f"  canon du modèle repris de la mesure du {canon['date']} (même modèle, mêmes sujets ; "
            f"redemandé après {CANON_JOURS} jours)"
        )
    else:
        rep = te.appeler_claude(
            f"Domaine : « {decl['libelle']} ». Voici ses sujets :\n"
            + "\n".join(f"  S{i}. {s}" for i, s in enumerate(sujets))
            + f"\n\nPour chaque sujet, donne les {PAR_SUJET} notions, techniques, produits, formats ou travaux qu'un "
            "spécialiste tient pour essentiels aujourd'hui, de mémoire, sous leur nom d'usage le plus court (en anglais "
            "si c'est l'usage : « path tracing », « Depth Anything », « OpenPBR »). Pas de phrase, pas de doublon entre "
            'sujets. Réponds en JSON : {"sujets": [{"sujet": <indice S>, "notions": ["…"]}, …]}.',
            SCHEMA_CANON,
        )
    if not rep:
        print("  le modèle n'a pas répondu : pas de mesure", file=sys.stderr)
        return None
    par_sujet = []
    for x in rep.get("sujets", []):
        try:
            i, notions = int(x["sujet"]), [str(n).strip() for n in x["notions"] if str(n).strip()]
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < len(sujets) or not notions:
            continue
        # Une notion rapprochée d'un nœud existant sous un autre nom (combler_canon.py) est portée.
        absentes = [n for n in notions if n not in alias and not porte_par_le_graphe(n, racines, textes)]
        par_sujet.append(
            {"sujet": sujets[i], "canon": len(notions), "portees": len(notions) - len(absentes), "absentes": absentes}
        )

    # Le volet critique (06.10.2026) : demander les notions « essentielles » rend le courant dominant ; les
    # concepts et les auteurs de la critique (TESCREAL, colonialisme des données…) n'y entraient pas, et leur
    # absence ne se voyait pas. Mesurés à part : la « pluralité ».
    rep_c = None
    if decl.get("canon_critique"):
        rep_c = (
            canon.get("critique")
            if canon
            else te.appeler_claude(
                f"Domaine : « {decl['libelle']} ». Voici ses sujets :\n"
                + "\n".join(f"  S{i}. {s}" for i, s in enumerate(sujets))
                + f"\n\nPour chaque sujet, donne les {PAR_SUJET_CRITIQUE} concepts, courants, auteurs ou travaux CRITIQUES "
                "qu'un spécialiste tiendrait pour indispensables à une vue pluraliste : les critiques du courant dominant, "
                "les voix des personnes concernées, les contradicteurs de chaque camp (y compris les défenseurs d'une "
                "position quand le sujet est surtout traité par ses critiques). Sous leur nom d'usage le plus court, sans "
                "phrase, sans reprendre les notions dominantes évidentes. Réponds en JSON : "
                '{"sujets": [{"sujet": <indice S>, "notions": ["…"]}, …]}.',
                SCHEMA_CANON,
            )
        )
        for x in (rep_c or {}).get("sujets", []):
            try:
                i, notions = int(x["sujet"]), [str(n).strip() for n in x["notions"] if str(n).strip()]
            except (KeyError, TypeError, ValueError):
                continue
            ps = next((y for y in par_sujet if y["sujet"] == sujets[i]), None) if 0 <= i < len(sujets) else None
            if ps is None or not notions:
                continue
            absentes = [n for n in notions if n not in alias and not porte_par_le_graphe(n, racines, textes)]
            ps["critique"] = {"canon": len(notions), "portees": len(notions) - len(absentes), "absentes": absentes}
    if not canon:
        ecrire_canon(base, sujets, rep, rep_c)
    voix: dict[str, int] = {}
    for x in cfg.toutes(graphe=graphe):
        n = sum(1 for f in x.chemin.rglob("*.md") if not f.name.startswith("_")) if x.chemin.exists() else 0
        if n:
            voix[x.posture or "non précisée"] = voix.get(x.posture or "non précisée", 0) + n

    noms = {
        f"sources/{x.dossier}": x.nom
        for x in cfg.toutes()
        if x.graphe == graphe or x.graphe in cfg.graphes_inclus(graphe)
    }
    noms |= {f"sources/{v['dossier']}": "arXiv (veille)" for v in cfg.veilles_arxiv() if v["graphe"] == graphe}
    apres = notions_posterieures(G, horizon, noms)
    random.seed(0)
    par_source: dict[str, list[str]] = {}
    for etiquette, source in apres:
        par_source.setdefault(source, []).append(etiquette)
    echantillon: list[tuple[str, str]] = []
    for source, etiquettes in sorted(par_source.items(), key=lambda kv: -len(kv[1])):
        if len(etiquettes) >= 5 and len(echantillon) < ECHANTILLON:
            echantillon += [(e, source) for e in random.sample(etiquettes, min(PAR_SOURCE, len(etiquettes)))]
    inconnues: list[str] = []
    sources: list[dict] = []
    juges: dict[int, dict] = {}
    if echantillon:
        # Un nom jugé « inconnu du modèle » ou « hors sujet » l'est pour de bon (même modèle, mêmes sujets) : les
        # jugements sont gardés par nom (avance_jugements.json), et seuls les noms jamais vus sont soumis. Avant le
        # 08.10.2026, le même échantillon (graine fixe) était rejugé chaque nuit, et deux fois pour un graphe financier et un graphe de trading quantitatif.
        p_j = base / "avance_jugements.json"
        memo = json.loads(p_j.read_text(encoding="utf-8")) if p_j.exists() else {}
        if memo.get("sujets") != empreinte_sujets(sujets):
            memo = {"sujets": empreinte_sujets(sujets), "modele": te.MODELE, "noms": {}}
        noms = memo.setdefault("noms", {})
        a_soumettre = [(i, e) for i, (e, _) in enumerate(echantillon) if e not in noms]
        for i, (e, _) in enumerate(echantillon):
            if e in noms:
                juges[i] = {
                    "i": i,
                    "inconnu": bool(noms[e].get("inconnu")),
                    "hors_sujet": bool(noms[e].get("hors_sujet")),
                }
        if a_soumettre:
            rep2 = te.appeler_claude(
                f"Domaine : « {decl['libelle']} ». Ses sujets :\n"
                + "\n".join(f"  - {x}" for x in sujets)
                + "\n\nVoici des noms relevés dans des articles récents :\n"
                + "\n".join(f"  {k}. {e}" for k, (_, e) in enumerate(a_soumettre))
                + "\n\nPour chacun, deux jugements. `inconnu` : vrai si tu ne le connais pas, ou si tu ne saurais rien en "
                "dire de précis et de sûr de mémoire (produit, version, projet, technique, notion) — sois strict, dans le "
                "doute c'est inconnu. `hors_sujet` : vrai s'il ne relève d'aucun des sujets ci-dessus. Réponds en JSON : "
                '{"jugements": [{"i": <indice>, "inconnu": <bool>, "hors_sujet": <bool>}, …]}.',
                SCHEMA_CONNU,
            )
            for x in (rep2 or {}).get("jugements", []):
                k = x.get("i")
                if isinstance(k, int) and 0 <= k < len(a_soumettre):
                    i, e = a_soumettre[k]
                    juges[i] = {"i": i, "inconnu": bool(x.get("inconnu")), "hors_sujet": bool(x.get("hors_sujet"))}
                    noms[e] = {
                        "inconnu": bool(x.get("inconnu")),
                        "hors_sujet": bool(x.get("hors_sujet")),
                        "date": time.strftime("%Y-%m-%d"),
                    }
            if rep2:
                p_j.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"  jugements : {len(echantillon) - len(a_soumettre)} nom(s) déjà jugé(s), {len(a_soumettre)} soumis au modèle"
        )
        inconnues = [echantillon[i][0] for i, x in juges.items() if x.get("inconnu")]
        for source in dict.fromkeys(src for _, src in echantillon):
            idx = [i for i, (_, src) in enumerate(echantillon) if src == source and i in juges]
            if idx:
                sources.append(
                    {
                        "source": source,
                        "posterieures": len(par_source[source]),
                        "soumises": len(idx),
                        "inconnues": sum(bool(juges[i].get("inconnu")) for i in idx),
                        "hors_sujet": sum(bool(juges[i].get("hors_sujet")) for i in idx),
                        "utiles": sum(bool(juges[i].get("inconnu")) and not juges[i].get("hors_sujet") for i in idx),
                    }
                )
    canon = sum(s["canon"] for s in par_sujet)
    canon_c = sum(s["critique"]["canon"] for s in par_sujet if "critique" in s)
    return {
        "pluralite": round(sum(s["critique"]["portees"] for s in par_sujet if "critique" in s) / canon_c, 3)
        if canon_c
        else None,
        "voix": dict(sorted(voix.items(), key=lambda kv: -kv[1])),
        "graphe": graphe,
        "date": time.strftime("%Y-%m-%d"),
        "modele": te.MODELE,
        "horizon": horizon,
        "completude": round(sum(s["portees"] for s in par_sujet) / canon, 3) if canon else None,
        "sujets": par_sujet,
        "notions": sum(1 for n in G if dg._type(G, n) in dg.TYPES_CONCEPT),
        "posterieures": len(apres),
        "echantillon": len(echantillon),
        "inconnues_du_modele": inconnues,
        "sources": sources,
    }


def afficher(m: dict) -> None:
    print(f"  {m['graphe']} — mesure du {m['date']}, modèle {m['modele']}, horizon {m['horizon']}")
    print(f"  complétude : {m['completude']:.0%} du canon du modèle est porté par le graphe")
    for s in sorted(m["sujets"], key=lambda s: s["portees"] / s["canon"]):
        print(f"    {s['portees']:2d}/{s['canon']:2d}  {s['sujet'][:70]}")
        if s["absentes"]:
            print(f"           absentes : {', '.join(s['absentes'])}")
        if s.get("critique"):
            c = s["critique"]
            print(
                f"    {c['portees']:2d}/{c['canon']:2d}  └ voix critiques"
                + (f" — absentes : {', '.join(c['absentes'])}" if c["absentes"] else "")
            )
    if m.get("pluralite") is not None:
        print(f"  pluralité : {m['pluralite']:.0%} du volet critique du canon est porté par le graphe")
    if m.get("voix"):
        tot = sum(m["voix"].values())
        print(
            "  voix (fiches par posture des sources) : "
            + ", ".join(f"{k} {v * 100 / tot:.0f} %" for k, v in m["voix"].items())
        )
    part = len(m["inconnues_du_modele"]) / m["echantillon"] if m["echantillon"] else 0
    print(
        f"  avance : {m['posterieures']} notion(s) sur {m['notions']} n'ont que des sources postérieures à l'horizon ; "
        f"le modèle en dit inconnues {len(m['inconnues_du_modele'])} sur {m['echantillon']} soumises ({part:.0%})"
    )
    if m["inconnues_du_modele"]:
        print(f"           par exemple : {', '.join(m['inconnues_du_modele'][:12])}")
    if m.get("sources"):
        print(
            "  rendement par source (notions récentes : soumises, inconnues du modèle, hors sujet, utiles = inconnues et dans le sujet) :"
        )
        for x in sorted(m["sources"], key=lambda x: -x["utiles"] / x["soumises"]):
            print(
                f"    {x['utiles'] / x['soumises']:4.0%} utiles  {x['inconnues']}/{x['soumises']} inconnues, "
                f"{x['hors_sujet']} hors sujet, {x['posterieures']:4d} notions récentes  {x['source'][:48]}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--horizon", default=HORIZON, help="fin des connaissances du modèle, AAAA-MM-JJ")
    ap.add_argument("--etat", action="store_true", help="relire la dernière mesure, sans appel")
    args = ap.parse_args()
    graphe = cfg.nom_actuel(args.graphe)
    sortie = cfg.chemins_du_graphe(graphe)["base"] / "avance.json"
    if args.etat:
        if not sortie.exists():
            print(f"  pas de mesure pour {graphe}", file=sys.stderr)
            return 1
        afficher(json.loads(sortie.read_text(encoding="utf-8")))
        return 0
    m = mesurer(graphe, args.horizon)
    if not m:
        return 1
    sortie.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    afficher(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())

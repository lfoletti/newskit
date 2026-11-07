#!/usr/bin/env python3
"""Diachronie : une notion dans le temps, et l'actualité de ce qui l'atteste.

Les textes de loi changent (porteur, 09.10.2026 : « les textes de loi sont connus pour changer dans le temps »), et
le graphe doit le savoir sans devenir ambigu. Une notion est un seul nœud quelle que soit la version du texte qui la
porte (libellé canonique, sans date : « Une notion, un nœud ») ; ce sont ses **attestations** qui sont datées, et
c'est d'elles que se lit son actualité.

Trois pièces, aucune n'appelle le modèle :

1. **La version d'un texte de référence est une propriété de ses fiches.** `collecte_textes.TEXTES` déclare chaque
   version avec sa date ; les versions d'un même texte partagent une `famille` (« aiact » pour le texte consolidé du
   27.07.2026 et le texte initial du 12.07.2024). La plus récente est **en vigueur**, les autres **remplacées**, et
   chaque article d'une version remplacée sait par quelle fiche il est remplacé (même rang dans la version en
   vigueur). `annoter_fiches` écrit ces champs dans l'en-tête des fiches (`texte`, `version`, `statut`,
   `remplacee_par`, `partie`) — l'en-tête seulement : l'empreinte d'extraction porte sur le corps, rien n'est
   réextrait. Le squelette les porte sur le nœud de la fiche.

2. **L'actualité d'une notion se calcule du graphe**, à la composition (`annoter_graphe`, appelé par
   `fusionner_graphe.py`) : première et dernière attestation, versions des textes de référence qui la nomment, et
   trois verdicts — `en_vigueur` (un texte en vigueur l'atteste), `remplacee_seulement` (seules des versions
   remplacées l'attestent : la notion est à lire comme périmée), `evolue` (deux versions du même texte l'attestent :
   sa synthèse doit décrire le changement). Les synthèses (`synthese.py`) lisent ces verdicts pour dater leurs
   preuves, ordonner le récit et dire ce qui est en vigueur ; `repondre` l'affiche.

3. **La veille des versions** (`--versions`) interroge le Cellar de l'Office des publications (versions consolidées
   d'un CELEX) et le SPARQL de Fedlex (versions d'un acte, `jolux:isMemberOf` et `dateApplicability`) et compare aux
   versions déclarées : une version nouvelle est un « à faire » du rapport, jamais une entrée automatique — un texte
   se déclare et se relit (« rien n'entre sans mesure »).

    python3 -m newskit.diachronie --graphe <graphe> --etat              # familles, versions, verdicts comptés
    python3 -m newskit.diachronie --graphe <graphe> --notion "Deployer"  # la chronologie d'une notion
    python3 -m newskit.diachronie --graphe <graphe> --annoter-fiches     # en-têtes des fiches de référence
    python3 -m newskit.diachronie --graphe <graphe> --versions           # veille : versions nouvelles en ligne
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

EN_VIGUEUR, REMPLACEE = "en vigueur", "remplacée"
CHAMPS = ("texte", "version", "statut", "remplacee_par", "partie")
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/sparql-results+json",
}


# --- 1. Les versions déclarées et les fiches qui les portent -------------------------------------------------


def familles(graphe: str) -> dict[str, list[dict]]:
    """Les textes de référence du graphe par famille, chaque famille triée de la version la plus ancienne à la plus
    récente ; chaque version reçoit son `statut` et, si elle est remplacée, le `court` de la version en vigueur."""
    from newskit.collecte_textes import TEXTES  # noqa: PLC0415

    out: dict[str, list[dict]] = defaultdict(list)
    for t in TEXTES.get(graphe) or []:
        out[t.get("famille") or t["court"]].append(dict(t))
    for fam, versions in out.items():
        versions.sort(key=lambda t: t["date"])
        for t in versions:
            t["statut"] = EN_VIGUEUR if t is versions[-1] else REMPLACEE
            t["remplacee_par_court"] = None if t is versions[-1] else versions[-1]["court"]
    return dict(out)


def dossier_du_texte(graphe: str, t: dict) -> Path | None:
    s = {x.cle: x for x in cfg.toutes()}.get(t["cle"])
    return (RACINE / "sources" / s.dossier / t["court"]) if s else None


def _rang(chemin: Path, court: str) -> str:
    """« art-015 » dans `2026-07-27_aiact-art-015.md` : ce qui identifie la partie d'une version à l'autre."""
    m = re.match(r"\d{4}-\d{2}-\d{2}_" + re.escape(court) + r"-(.+)\.md$", chemin.name)
    return m.group(1) if m else chemin.stem


def annoter_fiches(graphe: str, controle: bool = False) -> dict:
    """Écrit `texte`, `version`, `statut`, `remplacee_par`, `partie` dans l'en-tête des fiches de référence ; une
    fiche déjà juste n'est pas réécrite. Rend le compte par statut."""
    compte = {"ecrites": 0, "a_jour": 0, EN_VIGUEUR: 0, REMPLACEE: 0, "sans_remplacante": 0}
    for fam, versions in familles(graphe).items():
        chemins = {t["court"]: dossier_du_texte(graphe, t) for t in versions}
        en_vigueur = versions[-1]
        parties_vigueur = {}
        if chemins.get(en_vigueur["court"]) and chemins[en_vigueur["court"]].exists():
            parties_vigueur = {_rang(p, en_vigueur["court"]): p for p in chemins[en_vigueur["court"]].glob("*.md")}
        for t in versions:
            d = chemins.get(t["court"])
            if not d or not d.exists():
                continue
            for p in sorted(d.glob("*.md")):
                if p.name.startswith("_"):
                    continue
                champs, corps = fiche.lire_fichier(p)
                rang = _rang(p, t["court"])
                voulu = {"texte": fam, "version": t["date"], "statut": t["statut"], "partie": rang}
                if t["statut"] == REMPLACEE:
                    r = parties_vigueur.get(rang)
                    voulu["remplacee_par"] = str(r.relative_to(RACINE)) if r else ""
                    if not r:
                        compte["sans_remplacante"] += 1
                compte[t["statut"]] += 1
                if all(champs.get(k) == v for k, v in voulu.items()) and (
                    "remplacee_par" in voulu or "remplacee_par" not in champs
                ):
                    compte["a_jour"] += 1
                    continue
                champs = {k: v for k, v in champs.items() if k != "remplacee_par"} | voulu
                if not controle:
                    fiche.ecrire(p, champs, corps, strict=False)
                compte["ecrites"] += 1
    return compte


# --- 2. L'actualité d'une notion, lue du graphe -------------------------------------------------------------


def index_fiches(G) -> dict[str, dict]:
    """source_file → attributs du nœud de fiche (date, statut, texte, version…)."""
    return {
        str(d["source_file"]): {**d, "_id": n}
        for n, d in G.nodes(data=True)
        if d.get("kind") in ("article", "papier") and d.get("source_file")
    }


def attestations(G, n, idx: dict[str, dict] | None = None) -> list[dict]:
    """Les fiches qui attestent la notion `n`, datées, de la plus ancienne à la plus récente. Une fiche de référence
    porte son texte, sa version et son statut ; une autre, sa plateforme."""
    idx = idx if idx is not None else index_fiches(G)
    fiches: list[str] = []
    for v in G.neighbors(n):
        f = str((G.get_edge_data(n, v) or {}).get("source_file") or "")
        if f and f not in fiches:
            fiches.append(f)
        f = str(G.nodes[v].get("source_file") or "") if G.nodes[v].get("kind") in ("article", "papier") else ""
        if f and f not in fiches:
            fiches.append(f)
    f0 = str(G.nodes[n].get("source_file") or "")
    if f0 and f0 not in fiches and f0 in idx:
        fiches.append(f0)
    out = []
    for f in fiches:
        d = idx.get(f)
        if not d:
            continue
        a = {"fiche": f, "date": str(d.get("date") or ""), "plateforme": str(d.get("plateforme") or "")}
        if d.get("statut"):
            a |= {
                "texte": d.get("texte"),
                "version": d.get("version"),
                "statut": d.get("statut"),
                "partie": d.get("partie"),
                "remplacee_par": d.get("remplacee_par") or "",
            }
        out.append(a)
    out.sort(key=lambda a: (a["date"], a["fiche"]))
    return out


def actualite(G, n, idx: dict[str, dict] | None = None) -> dict:
    """Ce que les attestations disent du présent de la notion. `en_vigueur` et `remplacee_seulement` ne valent que
    pour une notion qu'un texte de référence atteste ; `evolue` dit que deux versions d'un même texte la nomment."""
    att = attestations(G, n, idx)
    dates = [a["date"] for a in att if a["date"]]
    a = {"attestations": len(att), "premiere": min(dates) if dates else "", "derniere": max(dates) if dates else ""}
    ref = [x for x in att if x.get("statut")]
    if ref:
        versions = defaultdict(set)
        for x in ref:
            versions[x["texte"]].add(x["version"])
        a["textes"] = {k: sorted(v) for k, v in versions.items()}
        a["en_vigueur"] = any(x["statut"] == EN_VIGUEUR for x in ref)
        # « Remplacée seulement » ne se dit que si la partie en vigueur qui remplace chaque attestation est elle-même
        # dans le graphe avec ses notions : sinon c'est l'extraction qui est en retard, pas la notion qui est périmée
        # (09.10.2026 : « AI Office » paraissait périmée parce que l'article de 2026 n'était pas encore extrait).
        if not a["en_vigueur"]:
            remplacantes = [x.get("remplacee_par") or "" for x in ref]
            if any(_nommee_dans(G, n, f) for f in remplacantes):
                a["en_vigueur"], a["attestee_textuellement"] = True, True
        a["remplacee_seulement"] = not a["en_vigueur"] and all(_extraite(G, idx, f) for f in remplacantes)
        a["evolue"] = any(len(v) >= 2 for v in versions.values())
    return a


def _extraite(G, idx: dict[str, dict], f: str) -> bool:
    """La fiche `f` est-elle dans le graphe avec au moins une notion extraite (couche 2) ?"""
    d = idx.get(f)
    if not d:
        return False
    return any(
        G.nodes[v].get("file_type") == "concept" and G.nodes[v].get("kind") not in ("auteur", "publication")
        for v in G.neighbors(d["_id"])
    )


def _nommee_dans(G, n, f: str) -> bool:
    """La fiche `f` nomme-t-elle la notion `n` (libellé ou alias, mot entier) ? Deux extractions du même article ne
    donnent pas les mêmes libellés : une notion que l'extraction de la version en vigueur n'a pas relevée, mais que
    son texte nomme, n'est pas périmée — elle est attestée textuellement (09.10.2026)."""
    from newskit import interroger_graphe as ig  # noqa: PLC0415

    p = RACINE / f
    if not f or not p.exists():
        return False
    formes = [ig._label(G, n), *(G.nodes[n].get("aussi") or [])]
    motif = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(ig._plier(x)) for x in formes if x) + r")(?![\w])")
    return motif.search(ig._plier(p.read_text(encoding="utf-8", errors="replace"))) is not None


def annoter_graphe(G) -> dict:
    """Pose `actualite` sur chaque notion du graphe (à la composition). Rend les comptes pour le journal."""
    from newskit import digerer_graphe as dg  # noqa: PLC0415
    from newskit import interroger_graphe as ig  # noqa: PLC0415

    idx = index_fiches(G)
    c = {"notions": 0, "reference": 0, "en_vigueur": 0, "remplacee_seulement": 0, "evolue": 0}
    for n, d in G.nodes(data=True):
        if dg._type(G, n) not in dg.TYPES_CONCEPT or ig.est_auteur(G, n):
            continue
        a = actualite(G, n, idx)
        if not a["attestations"]:
            continue
        d["actualite"] = a
        c["notions"] += 1
        if "en_vigueur" in a:
            c["reference"] += 1
            c["en_vigueur"] += a["en_vigueur"]
            c["remplacee_seulement"] += a["remplacee_seulement"]
            c["evolue"] += a["evolue"]
    return c


def phrase(a: dict) -> str:
    """L'actualité en une ligne, pour une réponse ou une page : « attestée de 2024-07-12 à 2026-07-27 ; texte en
    vigueur ; deux versions de aiact »."""
    if not a:
        return ""
    bouts = []
    if a.get("premiere") and a.get("derniere"):
        bouts.append(
            f"attestée de {a['premiere']} à {a['derniere']}"
            if a["premiere"] != a["derniere"]
            else f"attestée le {a['derniere']}"
        )
    if "en_vigueur" in a:
        bouts.append(
            "texte en vigueur" if a["en_vigueur"] else "SEULEMENT dans une version remplacée : à lire comme périmée"
        )
        if a.get("evolue"):
            bouts.append("évolue : " + ", ".join(f"{k} {' → '.join(v)}" for k, v in a["textes"].items() if len(v) >= 2))
    return " ; ".join(bouts)


# --- 3. La veille des versions en ligne ---------------------------------------------------------------------


def _sparql(point: str, requete: str) -> list[dict]:
    url = point + "?" + urllib.parse.urlencode({"query": requete, "format": "application/sparql-results+json"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
        return json.loads(r.read())["results"]["bindings"]


def versions_en_ligne(t: dict) -> list[str] | None:
    """Les dates des versions connues en ligne pour un texte déclaré (AAAA-MM-JJ), ou None si la source n'est pas
    interrogeable (PDF du Conseil de l'Europe, NIST…)."""
    # Secteur 3 (acte au Journal officiel) ou 0 (texte consolidé) : les versions consolidées sont toujours en secteur 0.
    m = re.search(r"CELEX:[03](\d{4}[A-Z]\d{4})", t.get("url", ""))
    if t.get("forme") == "eurlex" and m:
        celex = "0" + m.group(1)
        rows = _sparql(
            "https://publications.europa.eu/webapi/rdf/sparql",
            "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> SELECT DISTINCT ?c WHERE "
            f'{{ ?w cdm:resource_legal_id_celex ?c . FILTER(STRSTARTS(STR(?c), "{celex}-")) }}',
        )
        dates = sorted(
            {f"{d[:4]}-{d[4:6]}-{d[6:]}" for b in rows if (d := b["c"]["value"].rsplit("-", 1)[-1]).isdigit()}
        )
        return dates
    m = re.search(
        r"fedlex\.(?:data\.)?admin\.ch/(?:filestore/fedlex\.data\.admin\.ch/)?(eli/cc/\d+/\d+)",
        t.get("lien") or t.get("url") or "",
    )
    if t.get("forme") == "fedlex" and m:
        acte = f"https://fedlex.data.admin.ch/{m.group(1)}"
        rows = _sparql(
            "https://fedlex.data.admin.ch/sparqlendpoint",
            "PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#> SELECT ?d WHERE "
            f"{{ ?c jolux:isMemberOf <{acte}> ; jolux:dateApplicability ?d }}",
        )
        return sorted({b["d"]["value"][:10] for b in rows})
    return None


def veille(graphe: str) -> list[str]:
    """Compare les versions déclarées aux versions en ligne ; rend les lignes à signaler (vide : rien de neuf)."""
    signaux = []
    for fam, versions in familles(graphe).items():
        declarees = {t["date"] for t in versions}
        t = versions[-1]
        try:
            en_ligne = versions_en_ligne(t)
        except Exception as e:  # noqa: BLE001
            print(f"  {fam} : veille impossible ({type(e).__name__} {str(e)[:60]})", file=sys.stderr)
            continue
        if en_ligne is None:
            print(f"  {fam} : source non interrogeable, version déclarée {t['date']} à vérifier à la main")
            continue
        nouvelles = [d for d in en_ligne if d > max(declarees)]
        print(
            f"  {fam} : déclarée {', '.join(sorted(declarees))} ; en ligne {', '.join(en_ligne) or '—'}"
            + (f" ; NOUVELLE {', '.join(nouvelles)}" if nouvelles else "")
        )
        if nouvelles:
            signaux.append(
                f"version nouvelle de {fam} ({t['nom'][:60]}) : {', '.join(nouvelles)} — déclarer la version "
                f"dans collecte_textes.TEXTES (famille « {fam} »), collecter, puis la précédente devient remplacée"
            )
    return signaux


# --- Ligne de commande ----------------------------------------------------------------------------------------


def etat(graphe: str) -> None:
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    from newskit import themes_emergents as te  # noqa: PLC0415

    print("Textes de référence déclarés, par famille :")
    for fam, versions in familles(graphe).items():
        print(f"  {fam:<20} " + " ; ".join(f"{t['date']} ({t['court']}, {t['statut']})" for t in versions))
    G = te.charger(graphe)
    if G is None:
        return
    c = {"notions": 0, "reference": 0, "en_vigueur": 0, "remplacee_seulement": 0, "evolue": 0}
    perimees, evoluent = [], []
    for n, d in G.nodes(data=True):
        a = d.get("actualite")
        if not a:
            continue
        c["notions"] += 1
        if "en_vigueur" in a:
            c["reference"] += 1
            c["en_vigueur"] += a["en_vigueur"]
            if a["remplacee_seulement"]:
                c["remplacee_seulement"] += 1
                perimees.append(ig._label(G, n))
            if a["evolue"]:
                c["evolue"] += 1
                evoluent.append(ig._label(G, n))
    if not c["notions"]:
        print("  (graphe composé avant la diachronie : recomposer pour poser `actualite` sur les notions)")
        return
    print(
        f"\nNotions datées : {c['notions']} ; attestées par un texte de référence : {c['reference']}, dont en vigueur "
        f"{c['en_vigueur']}, remplacée seulement {c['remplacee_seulement']}, évoluant entre deux versions {c['evolue']}"
    )
    if perimees:
        print(
            "  à lire comme périmées (version remplacée seulement) : "
            + ", ".join(sorted(perimees)[:15])
            + (" …" if len(perimees) > 15 else "")
        )
    if evoluent:
        print(
            "  évoluent (synthèse chronologique) : "
            + ", ".join(sorted(evoluent)[:15])
            + (" …" if len(evoluent) > 15 else "")
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--etat", action="store_true")
    ap.add_argument("--notion", help="la chronologie d'une notion (libellé ou identifiant)")
    ap.add_argument(
        "--annoter-fiches", action="store_true", help="écrit version et statut dans l'en-tête des fiches de référence"
    )
    ap.add_argument("--controle", action="store_true", help="avec --annoter-fiches : dit ce qui serait écrit")
    ap.add_argument("--versions", action="store_true", help="veille : versions nouvelles en ligne (Cellar, Fedlex)")
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    if a.annoter_fiches:
        c = annoter_fiches(g, a.controle)
        print(
            f"  {g} : {c['ecrites']} en-tête(s) {'à écrire' if a.controle else 'écrit(s)'}, {c['a_jour']} déjà justes ; "
            f"{c[EN_VIGUEUR]} fiche(s) en vigueur, {c[REMPLACEE]} remplacée(s)"
            + (
                f", {c['sans_remplacante']} sans partie correspondante dans la version en vigueur"
                if c["sans_remplacante"]
                else ""
            )
        )
    if a.versions:
        s = veille(g)
        for l in s:
            print(f"  À FAIRE : {l}")
        if not s:
            print(f"  {g} : aucune version nouvelle")
    if a.notion:
        from newskit import interroger_graphe as ig  # noqa: PLC0415
        from newskit import themes_emergents as te  # noqa: PLC0415

        G = te.charger(g)
        n = ig.resoudre(G, a.notion, muet=True) if G is not None else None
        if n is None:
            print(f"  notion introuvable : {a.notion}")
            return 1
        print(f"{ig._label(G, n)} — {phrase(actualite(G, n)) or 'sans attestation datée'}")
        for x in attestations(G, n):
            print(
                f"  {x['date'] or '????-??-??'}  "
                + (f"{x['texte']} {x['version']} ({x['statut']})" if x.get("statut") else x["plateforme"])
                + f"  {x['fiche']}"
            )
    if a.etat or not (a.annoter_fiches or a.versions or a.notion):
        etat(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())

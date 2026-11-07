#!/usr/bin/env python3
"""Prospection de sources : ce qu'un domaine vaut avant de le déclarer.

Trouver des sources est un objectif du projet, pas une corvée préalable. Une
veille ne vaut que par ce qu'elle regarde, et un thème mal couvert — l'audio
l'était — se corrige d'abord par une source, ensuite par une collecte.

Ce script répond à une seule question, par la mesure : **cette adresse est-elle
collectable, et ce qu'elle sert vaut-il d'être collecté ?** Il essaie l'API
Substack, puis les flux RSS ou Atom usuels, puis l'autodécouverte du flux dans
la page. Pour ce qu'il trouve, il mesure ce que les autres collecteurs mesurent
déjà : nombre d'entrées, date de la plus récente, médiane de mots par entrée.

Ce qu'il ne fait pas : déclarer la source. Le verdict est rendu à l'utilisateur,
avec le bloc YAML prêt à coller si la mesure est bonne. Une source entre au
registre parce qu'on l'a décidé, pas parce qu'un script l'a trouvée.

Les seuils viennent des mesures faites sur les sources déjà en service :
au-dessous de 120 mots par entrée, on collecte des entrefilets ; au-delà de
dix-huit mois sans publication, la source est éteinte.

D'où viennent les candidats. La question n'est pas « cette adresse est-elle
bonne ? » mais « quelle adresse essayer ? », et le corpus y répond lui-même :
il cite des domaines qu'aucune source ne suit. C'est le même principe que la
collecte des papiers cités, appliqué aux sites — `--depuis-corpus` classe ces
domaines par nombre de fiches citantes et éprouve les premiers. Une source
n'est donc pas devinée, elle est proposée par ce qu'on lit déjà.

Usage :
    python3 -m newskit.prospecter_source exemple.com autre.substack.com
    python3 -m newskit.prospecter_source --fichier candidats.txt
    python3 -m newskit.prospecter_source --depuis-corpus 12
    python3 -m newskit.prospecter_source --sante
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import collecte_rss as rss  # noqa: E402
from newskit.commun import UA_NAVIGATEUR as UA, html_to_md  # noqa: E402

CHEMINS_FLUX = (
    "/feed/",
    "/feed",
    "/rss/",
    "/rss.xml",
    "/atom.xml",
    "/index.xml",
    "/feed.xml",
    "/blog/feed.xml",
    "/blog/rss.xml",
)
MOTS_MINIMUM = 120
MOIS_MORT = 18


def _get(url: str, json_attendu: bool = False, timeout: int = 25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json"
            if json_attendu
            else "application/rss+xml,application/atom+xml,application/xml,text/html",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        brut = r.read()
    return json.loads(brut) if json_attendu else brut


def essai_substack(domaine: str) -> dict | None:
    """L'API publique de Substack, que le collecteur sait déjà lire."""
    try:
        d = _get(f"https://{domaine}/api/v1/archive?sort=new&limit=12", json_attendu=True)
    except Exception:
        return None
    if not (isinstance(d, list) and d and isinstance(d[0], dict) and "post_date" in d[0]):
        return None
    mots = [int(p.get("wordcount") or 0) for p in d if p.get("wordcount")]
    payants = sum(1 for p in d if p.get("audience") == "only_paid")
    return {
        "echantillon": " ".join((p.get("title") or "") + " " + (p.get("subtitle") or "") for p in d),
        "voie": "Substack",
        "adresse": f"https://{domaine}",
        "entrees": len(d),
        "dernier": d[0]["post_date"][:10],
        "mots": int(statistics.median(mots)) if mots else 0,
        "note": f"{payants}/{len(d)} billets réservés aux abonnés payants",
    }


def flux_declare(domaine: str) -> str | None:
    """Le flux annoncé par la page elle-même, quand les chemins usuels échouent."""
    for schema in ("https://", "http://"):
        try:
            html = _get(schema + domaine).decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"<link[^>]+>", html, re.I):
            bal = m.group(0)
            if "alternate" in bal.lower() and re.search(r"rss|atom", bal, re.I):
                href = re.search(r'href="([^"]+)"', bal)
                if href:
                    return urllib.parse.urljoin(schema + domaine, htmlmod.unescape(href.group(1)))
        return None
    return None


def essai_flux(domaine: str) -> dict | None:
    candidats = [f"https://{domaine}{c}" for c in CHEMINS_FLUX]
    declare = None
    for url in candidats:
        try:
            brut = _get(url)
        except Exception:
            continue
        try:
            items = rss.entrees(brut)
        except Exception:
            items = rss.entrees_tolerantes(brut)
        if items:
            return mesurer_flux(url, items)
    declare = flux_declare(domaine)
    if declare:
        try:
            brut = _get(declare)
            items = rss.entrees(brut) or rss.entrees_tolerantes(brut)
            if items:
                return mesurer_flux(declare, items, decouvert=True)
        except Exception:
            return None
    return None


def mesurer_flux(url: str, items: list[dict], decouvert: bool = False) -> dict:
    mots = [len(html_to_md(htmlmod.unescape(i["corps"])).split()) for i in items[:12]]
    dates = [i["date"] for i in items if i["date"]]
    echantillon = " ".join((i["titre"] + " " + htmlmod.unescape(i["corps"])[:1500]) for i in items[:12])
    return {
        "echantillon": re.sub(r"<[^>]+>", " ", echantillon),
        "voie": "flux" + (" (autodécouvert)" if decouvert else ""),
        "adresse": url,
        "entrees": len(items),
        "dernier": max(dates) if dates else "?",
        "mots": int(statistics.median(mots)) if mots else 0,
        "note": "le flux porte le texte"
        if (mots and statistics.median(mots) >= MOTS_MINIMUM)
        else "flux sans texte : il faudra lire la page (page: true)",
    }


def pertinence(r: dict, items_texte: str) -> tuple[int, str]:
    """Recouvrement de vocabulaire entre la source et les notions du graphe.

    La question « est-ce pertinent ? » reste un jugement, mais elle peut être
    éclairée : si aucune des notions centrales du graphe n'apparaît dans ce que
    la source publie, le doute est fondé. C'est ce qui distingue `amazon.science`
    de `petfinder.com`, que le corpus cite pourtant tous les deux.

    Rend (nombre de notions retrouvées, les premières). Une mesure basse n'est
    pas un refus : elle demande qu'on regarde.
    """
    carte = RACINE / "graphe" / "carte.md"
    if not carte.exists() or not items_texte:
        return -1, "carte absente, pertinence non mesurée"
    notions = set()
    for ligne in carte.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"\| ([^|]{4,60}) \| \d+ \|", ligne)
        if m:
            notions.add(m.group(1).strip().lower())
    bas = items_texte.lower()
    trouvees = sorted(n for n in notions if len(n) > 5 and n in bas)
    return len(trouvees), ", ".join(trouvees[:5]) or "aucune notion du graphe"


def sante_des_sources() -> int:
    """Réexamine les sources déjà déclarées. Une source qui meurt ne le dit pas.

    C'est le pendant de la prospection : on mesure ce qu'on suit déjà, parce
    qu'un flux qui s'éteint, qui se met à tronquer ou qui change d'adresse ne
    produit aucune erreur — il produit zéro fiche neuve, ce qui ressemble à une
    semaine calme.
    """
    from newskit import config_sources as cfg  # noqa: PLC0415

    print(f"{'source':30} {'état':>8}  {'dernier':>10} {'mots':>6}  remarque")
    mauvaises = 0
    for s in cfg.toutes():
        if s.plateforme == "Flux RSS":
            r = essai_flux(urllib.parse.urlsplit(s.flux).netloc) or mesure_directe(s.flux)
        elif s.plateforme == "Substack":
            r = essai_substack(s.domaine)
        else:
            continue  # sources locales ou sans réseau
        v, pourquoi = verdict(r)
        mauvaises += 0 if v == "OUI" else 1
        if r:
            print(f"{s.nom[:30]:30} {v:>8}  {r['dernier']:>10} {r['mots']:>6}  {pourquoi}")
        else:
            print(f"{s.nom[:30]:30} {v:>8}  {'—':>10} {'—':>6}  {pourquoi}")
        time.sleep(0.4)
    if mauvaises:
        print(f"\n  {mauvaises} source(s) à regarder : éteinte, injoignable, ou devenue maigre.")
    else:
        print("\n  toutes les sources en réseau répondent et publient.")
    return 1 if mauvaises else 0


def mesure_directe(url: str) -> dict | None:
    """Mesure un flux dont on connaît déjà l'adresse exacte."""
    try:
        brut = _get(url)
        items = rss.entrees(brut) or rss.entrees_tolerantes(brut)
    except Exception:
        return None
    return mesurer_flux(url, items) if items else None


def verdict(r: dict) -> tuple[str, str]:
    if not r:
        return "NON", "ni API Substack ni flux lisible"
    vieux = ""
    try:
        d = datetime.strptime(r["dernier"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        mois = (datetime.now(tz=timezone.utc) - d).days / 30.4
        if mois > MOIS_MORT:
            vieux = f"éteinte : rien depuis {mois:.0f} mois"
    except Exception:
        pass
    if vieux:
        return "NON", vieux
    if r["mots"] < MOTS_MINIMUM and "page" not in r["note"]:
        return "NON", f"{r['mots']} mots par entrée : des entrefilets"
    return "OUI", r["note"]


def bloc_yaml(domaine: str, r: dict) -> str:
    cle = re.sub(r"[^a-z0-9]+", "_", domaine.lower()).strip("_")[:40]
    if r["voie"] == "Substack":
        return (
            f"  - cle: {cle}\n    plateforme: Substack\n    nom: ?\n"
            f"    dossier: substack/{cle}\n    domaine: {domaine}\n    abonnement: gratuit"
        )
    lire_page = "\n    page: true" if r["mots"] < MOTS_MINIMUM else ""
    return (
        f"  - cle: {cle}\n    plateforme: Flux RSS\n    nom: ?\n"
        f"    dossier: rss/{cle}\n    abonnement: gratuit\n"
        f"    flux: {r['adresse']}\n    site: https://{domaine}{lire_page}"
    )


# Domaines qui ne seront jamais une source de veille : dépôts de code, réseaux
# sociaux, encyclopédies, boutiques, hébergeurs d'images. Les écarter ici évite
# de les repeser à chaque passage.
JAMAIS = (
    "arxiv.org",
    "github.com",
    "gitlab.com",
    "doi.org",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "substack.com",
    "medium.com",
    "wikipedia.org",
    "reddit.com",
    "discord.com",
    "discord.gg",
    "threads.net",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "amazon.com",
    "shoutout.io",
    "unsplash.com",
    "nostarch.com",
    "eventbrite.com",
    "zoom.us",
    "docs.google.com",
    "drive.google.com",
    "forms.gle",
    "bit.ly",
    "t.co",
    "lnkd.in",
)


def candidats_du_corpus(combien: int) -> list[tuple[str, int, int]]:
    """Domaines cités par le corpus et suivis par aucune source.

    Rend (domaine, citations, fiches citantes), les plus cités d'abord. Le
    nombre de **fiches** compte davantage que celui de citations : dix renvois
    depuis une seule fiche disent l'intérêt d'un auteur, dix fiches distinctes
    disent celui du domaine.
    """
    import json  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415

    from newskit import config_sources as cfg  # noqa: PLC0415

    squelette = RACINE / "graphe" / "squelette.json"
    if not squelette.exists():
        raise SystemExit("  squelette absent : lancer newskit.extraire_squelette")
    d = json.loads(squelette.read_text(encoding="utf-8"))
    index = {n["id"]: n for n in d["nodes"]}

    declares = set()
    for s in cfg.toutes():
        for champ in ("domaine", "site", "flux"):
            v = getattr(s, champ, None)
            if v:
                declares.add(urllib.parse.urlsplit(v if "//" in v else "https://" + v).netloc.removeprefix("www."))

    citations: Counter = Counter()
    fiches: dict[str, set] = {}
    for e in d["edges"]:
        if e["relation"] != "cite_externe":
            continue
        n = index.get(e["target"], {})
        hote = urllib.parse.urlsplit(n.get("label") or n.get("url") or "").netloc
        hote = hote.removeprefix("www.").lower()
        if not hote or "." not in hote or "%" in hote:
            continue
        if hote in declares or any(hote == j or hote.endswith("." + j) for j in JAMAIS):
            continue
        citations[hote] += 1
        fiches.setdefault(hote, set()).add(e["source_file"])
    classe = sorted(citations, key=lambda h: (-len(fiches[h]), -citations[h]))
    return [(h, citations[h], len(fiches[h])) for h in classe[:combien]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domaines", nargs="*", help="domaines à éprouver")
    ap.add_argument("--fichier", type=Path, help="fichier de domaines, un par ligne")
    ap.add_argument(
        "--depuis-corpus",
        type=int,
        metavar="N",
        nargs="?",
        const=12,
        help="éprouver les N domaines que le corpus cite le plus sans les suivre",
    )
    ap.add_argument(
        "--sante", action="store_true", help="réexaminer les sources déjà déclarées : éteintes, muettes, maigries"
    )
    args = ap.parse_args()

    if args.sante:
        return sante_des_sources()

    domaines = list(args.domaines)
    origine: dict[str, str] = {}
    if args.depuis_corpus:
        for hote, cit, fic in candidats_du_corpus(args.depuis_corpus):
            domaines.append(hote)
            origine[hote] = f"cité par {fic} fiche(s), {cit} fois"
        print(f"  {len(domaines)} domaine(s) proposés par le corpus lui-même\n")
    if args.fichier:
        domaines += [l.strip() for l in args.fichier.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if not domaines:
        ap.error("aucun domaine à éprouver (voir --depuis-corpus ou --sante)")

    retenus = []
    print(f"{'domaine':38} {'verdict':>7}  {'voie':18} {'dernier':>10} {'mots':>6}  note")
    for d in domaines:
        d = d.strip().removeprefix("https://").removeprefix("http://").strip("/")
        r = essai_substack(d) or essai_flux(d)
        v, pourquoi = verdict(r)
        if r:
            print(f"{d[:38]:38} {v:>7}  {r['voie']:18} {r['dernier']:>10} {r['mots']:>6}  {pourquoi}")
        else:
            print(f"{d[:38]:38} {v:>7}  {'—':18} {'—':>10} {'—':>6}  {pourquoi}")
        if r:
            n, quelles = pertinence(r, r.get("echantillon", ""))
            if n >= 0:
                print(f"{'':38} {'':>7}  pertinence : {n} notion(s) du graphe — {quelles}")
        if origine.get(d):
            print(f"{'':38} {'':>7}  {origine[d]}")
        if v == "OUI":
            retenus.append((d, r))
        time.sleep(0.5)

    if retenus:
        print(
            f"\n  {len(retenus)} source(s) collectable(s). À déclarer dans sources.yaml après avoir choisi le nom :\n"
        )
        for d, r in retenus:
            print(bloc_yaml(d, r), "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

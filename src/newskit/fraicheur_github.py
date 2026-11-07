#!/usr/bin/env python3
"""Contrôle ponctuel de fraîcheur des fiches GitHub (mainteneur, 1er novembre 2026 : « ajoute aussi un mécanisme de contrôle
ponctuel de fraîcheur pour les projets github »).

Un dépôt se réécrit : la fiche du guide requie/AI-Red-Teaming-Guide, collectée le 17.10.2026, ignorait la version du
28.10.2026. Ce script compare la date de collecte de chaque fiche dont l'adresse est un dépôt GitHub (ou un fichier d'un
dépôt) à sa dernière modification, lue par l'API de GitHub : le dernier commit de son README pour la page d'un dépôt,
de son chemin pour un fichier. Un dépôt n'est demandé qu'une fois par passage.

    python3 -m newskit.fraicheur_github --controle                     # les fiches périmées, sans rien écrire
    python3 -m newskit.fraicheur_github --rafraichir                   # réécrit leur corps (README ou fichier brut)
    python3 -m newskit.fraicheur_github --ajouter-dossier <url de dossier> --vers sources/<dossier>
                                                                       # une fiche par fichier Markdown du dossier

Une source disparue (404, 410, 451) n'efface jamais sa fiche (règle du mainteneur, 01.11.2026) : le rafraîchissement la
marque `source_manquante: "<code>, <date>"` et garde son corps. Le rafraîchissement garde l'en-tête de la fiche ; sa date devient celle de la dernière modification, sa date de
collecte celle du jour. La fiche réécrite repasse à l'extraction comme toute fiche modifiée.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

from newskit.commun import RACINE  # noqa: E402
from newskit import fiche  # noqa: E402

API = "https://api.github.com/repos"
BRUT = "https://raw.githubusercontent.com"
# Premiers segments d'une adresse github.com qui ne sont pas un propriétaire de dépôt.
RESERVES = {
    "orgs",
    "topics",
    "collections",
    "marketplace",
    "sponsors",
    "settings",
    "features",
    "about",
    "pricing",
    "search",
    "explore",
    "trending",
    "login",
    "join",
    "apps",
    "enterprise",
    "security",
    "customer-stories",
    "readme",
    "events",
    "site",
    "notifications",
    "issues",
    "pulls",
    "codespaces",
    "users",
}


class Refus(Exception):
    """Une adresse qui ne répond pas (dépôt supprimé, privé, limite de l'API)."""


def adresse_github(url: str) -> tuple[str, str, str | None] | None:
    """(propriétaire, dépôt, chemin) d'une adresse de dépôt GitHub ; chemin None pour la page du dépôt."""
    m = re.match(
        r"^https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)(?:/(?:blob|tree)/[^/?#]+/([^?#]*))?/?(?:[?#].*)?$",
        url or "",
    )
    if not m or m[1].lower() in RESERVES:
        return None
    depot = m[2][:-4] if m[2].endswith(".git") else m[2]
    return m[1], depot, (m[3].strip("/") or None) if m[3] else None


def _jour(iso: str) -> str:
    return iso[:10]


def derniere_modif(o: str, r: str, chemin: str | None, lire: Callable[[str], str]) -> str:
    """La date (AAAA-MM-JJ) de la dernière modification du chemin dans le dépôt ; pour la page du dépôt, celle de son
    README, que la page montre (un dépôt actif reçoit des commits chaque jour sans que son README change)."""
    if chemin is None:
        chemin = _readme(o, r, lire)
    c = json.loads(lire(f"{API}/{o}/{r}/commits?path={chemin}&per_page=1"))
    if not c:
        raise Refus(f"{o}/{r} : aucun commit pour {chemin}")
    return _jour(c[0]["commit"]["committer"]["date"])


def perimees(fiches: list[dict], lire: Callable[[str], str]) -> list[dict]:
    """Les fiches GitHub dont le dépôt (ou le fichier) a changé après la collecte, et celles qui ne répondent plus
    (clé `erreur`). Chaque cible n'est demandée qu'une fois."""
    vus: dict[tuple, str | Refus] = {}
    out = []
    for x in fiches:
        a = adresse_github(x.get("url", ""))
        if a is None:
            continue
        if a not in vus:
            try:
                vus[a] = derniere_modif(*a, lire)
            except (Refus, KeyError, ValueError) as e:
                vus[a] = Refus(str(e))
        m = vus[a]
        if isinstance(m, Refus):
            out.append({**x, "erreur": str(m), "manquante": code_manquant(str(m))})
        elif m > str(x.get("collected") or ""):
            out.append({**x, "modifie": m})
    return out


def fichiers_markdown(o: str, r: str, dossier: str, lire: Callable[[str], str]) -> list[str]:
    """Les chemins des fichiers Markdown d'un dossier du dépôt (sans descendre dans les sous-dossiers)."""
    return [
        e["path"]
        for e in json.loads(lire(f"{API}/{o}/{r}/contents/{dossier}"))
        if e.get("type") == "file" and e.get("name", "").lower().endswith(".md")
    ]


def _slug(texte: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texte.lower()).strip("-")[:90]


def _titre(corps: str, chemin: str) -> str:
    m = re.search(r"^#\s+(.+)$", corps, re.M)
    return m[1].strip() if m else Path(chemin).stem


def collecter_fichier(
    o: str,
    r: str,
    chemin: str,
    sortie: Path,
    lire: Callable[[str], str],
    lire_brut: Callable[[str], str],
    aujourdhui: str | None = None,
) -> Path:
    """Une fiche pour un fichier du dépôt : son texte brut, daté de sa dernière modification."""
    aujourdhui = aujourdhui or time.strftime("%Y-%m-%d")
    date = derniere_modif(o, r, chemin, lire)
    corps = lire_brut(f"{BRUT}/{o}/{r}/HEAD/{chemin}")
    p = Path(sortie) / f"{date}_github-{_slug(o)}-{_slug(r)}-{_slug(str(Path(chemin).with_suffix('')))}.md"
    try:
        fiche.ecrire(
            p,
            {
                "title": f"{o}/{r} : {_titre(corps, chemin)}",
                "subtitle": "",
                "author": "",
                "date": date,
                "url": f"https://github.com/{o}/{r}/blob/HEAD/{chemin}",
                "plateforme": "GitHub",
                "publication": "github.com",
                "words": len(corps.split()),
                "complet": True,
                "collected": aujourdhui,
                "source": f"fichier du dépôt {o}/{r}",
            },
            corps,
        )
    except fiche.IdentiteDejaVue as deja:  # le fichier est déjà une fiche du graphe
        return deja.existante
    return p


def code_manquant(message: str) -> str | None:
    """Le code HTTP d'une disparition durable (404, 410, 451) ; None pour une erreur passagère (429, 5xx, délai)."""
    m = re.search(r"\b(404|410|451)\b", message or "")
    return m[1] if m else None


def marquer_manquante(p: Path, code: str, date: str) -> None:
    """Règle du mainteneur (01.11.2026) : une fiche dont la source a disparu (404) n'est jamais supprimée ; elle porte
    `source_manquante: "<code>, <date du premier constat>"` et garde son corps."""
    # en octets, l'en-tête seul : réécrire la fiche par le contrat, ou la relire en mode texte, changerait son corps
    # (blancs, fins de ligne CRLF) et donc son empreinte
    b = Path(p).read_bytes()
    nl = b"\r\n" if b.startswith(b"---\r\n") else b"\n"
    sep = nl + b"---" + nl
    if not b.startswith(b"---" + nl) or sep not in b:
        return
    tete, reste = b.split(sep, 1)
    if re.search(rb"^source_manquante:", tete, re.M):
        return
    ligne = ("source_manquante: " + json.dumps(f"{code}, {date}", ensure_ascii=False)).encode("utf-8")
    Path(p).write_bytes(tete + nl + ligne + sep + reste)


def ligne_rapport(x: dict, aujourdhui: str) -> str:
    """Une ligne du rapport : périmée, source manquante (« <code>, <date> »), ou injoignable pour une autre raison."""
    if "modifie" in x:
        return f"    {x['chemin']} : collectée le {x['collected']}, modifiée le {x['modifie']}"
    if x.get("manquante"):
        return f"    {x['chemin']} : source manquante : {x['manquante']}, {aujourdhui}"
    return f"    {x['chemin']} : {x['erreur']}"


def _readme(o: str, r: str, lire: Callable[[str], str]) -> str:
    return json.loads(lire(f"{API}/{o}/{r}/readme"))["path"]


def rafraichir(
    p: Path,
    lire: Callable[[str], str],
    lire_brut: Callable[[str], str],
    aujourdhui: str | None = None,
    readme: Callable | None = None,
) -> None:
    """Réécrit le corps d'une fiche GitHub avec le texte actuel (le README pour la page d'un dépôt), en gardant son
    en-tête ; date = dernière modification, collecte = aujourd'hui."""
    aujourdhui = aujourdhui or time.strftime("%Y-%m-%d")
    entete, _ = fiche.lire_fichier(Path(p))
    o, r, chemin = adresse_github(str(entete.get("url") or ""))
    date = derniere_modif(o, r, chemin, lire)
    corps = lire_brut(f"{BRUT}/{o}/{r}/HEAD/{chemin or (readme or (lambda o, r, l: _readme(o, r, l)))(o, r, lire)}")
    fiche.ecrire(
        Path(p),
        {**entete, "date": date, "collected": aujourdhui, "words": len(corps.split()), "complet": True},
        corps,
        strict=False,
    )


def _fiches_github() -> list[dict]:
    out = []
    for p in (RACINE / "sources").rglob("*.md"):
        if p.name.startswith("_"):
            continue
        try:
            tete = p.read_text(encoding="utf-8", errors="ignore")[:2000]
        except OSError:
            continue
        if "github.com/" not in tete:
            continue
        e, _ = fiche.lire(tete if tete.count("---") >= 2 else tete + "\n---\n")
        if adresse_github(str(e.get("url") or "")):
            out.append(
                {
                    "chemin": str(p.relative_to(RACINE)),
                    "url": str(e.get("url")),
                    "collected": str(e.get("collected") or ""),
                }
            )
    return out


def _lecteurs() -> tuple[Callable[[str], str], Callable[[str], str]]:
    from newskit import combler_canon as cc  # noqa: PLC0415

    def lire(url: str) -> str:
        try:
            return cc.lire_url(url, accept="application/vnd.github+json")
        except cc.Refus as e:
            raise Refus(str(e)) from e

    def lire_brut(url: str) -> str:
        try:
            return cc.lire_url(url, accept="text/plain")
        except cc.Refus as e:
            raise Refus(str(e)) from e

    return lire, lire_brut


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--controle", action="store_true")
    g.add_argument("--rafraichir", action="store_true")
    g.add_argument("--ajouter-dossier", metavar="URL")
    ap.add_argument("--vers", type=Path, help="dossier des fiches créées par --ajouter-dossier")
    a = ap.parse_args(argv)
    lire, lire_brut = _lecteurs()
    if a.ajouter_dossier:
        cible = adresse_github(a.ajouter_dossier)
        if not cible or not cible[2] or not a.vers:
            print(
                "  --ajouter-dossier attend l'adresse d'un dossier de dépôt (…/tree/<branche>/<dossier>) et --vers",
                file=sys.stderr,
            )
            return 1
        o, r, dossier = cible
        for chemin in fichiers_markdown(o, r, dossier, lire):
            print(f"  écrit : {collecter_fichier(o, r, chemin, RACINE / a.vers, lire, lire_brut).relative_to(RACINE)}")
        return 0
    fiches = _fiches_github()
    p = perimees(fiches, lire)
    print(
        f"  {len(fiches)} fiche(s) GitHub ; {sum(1 for x in p if 'modifie' in x)} périmée(s), "
        f"{sum(1 for x in p if x.get('manquante'))} source(s) manquante(s), "
        f"{sum(1 for x in p if 'erreur' in x and not x.get('manquante'))} injoignable(s)"
    )
    for x in p:
        print(ligne_rapport(x, time.strftime("%Y-%m-%d")))
    if a.rafraichir:
        for x in p:
            if x.get("manquante"):
                marquer_manquante(RACINE / x["chemin"], x["manquante"], time.strftime("%Y-%m-%d"))
                print(f"  source manquante, fiche gardée : {x['chemin']}")
        for x in p:
            if "modifie" in x:
                rafraichir(RACINE / x["chemin"], lire, lire_brut)
                print(f"  rafraîchie : {x['chemin']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

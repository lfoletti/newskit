"""La santé des sources et leur réparation automatique (mainteneur, 06.11.2026 : « un audit sap devrait être
systématiquement mené après tant d'échecs, et un processus d'auto-réparation devrait être intégré à la routine »).

Chaque lecture d'un hôte se note (`succes`, `echec`) : un succès remet son compte à zéro, un échec l'augmente. Au SEUIL
d'échecs consécutifs, l'hôte passe à l'audit d'accès (sap probe, accesspath), au plus une fois par jour et au plus
AUDITS_PAR_PASSE hôtes par passage. La réparation essaie, dans l'ordre :
  1. l'imitation d'un navigateur (empreinte TLS de Chrome), si robots.txt n'exclut pas les robots d'IA, et seulement
     si IMITATION_AUTO le permet (allumée par décision du mainteneur, 06.11.2026 : « 403 et 503 ne viennent pas uniquement
     de filtres anti-bot », « change la variable IMITATION_AUTO en true ») ;
  2. pour un flux, les flux que l'audit a trouvés sur le site, lus simplement (puis par imitation, si permise).
La première voie qui rend un contenu utilisable est retenue (`imite`, `adresse`). Sans voie, l'hôte se repose : une
heure, doublée à chaque échec qui suit, un jour au plus ; une alerte part, une par semaine et par hôte. Le repos évite
de frapper toutes les cinq minutes un site qui refuse (EDPB : 503 pendant deux jours, 230 refus, 06.11.2026).

L'état vit dans cache/sante_sources.json. Les dépendances (audit, lecture, robots.txt, alerte, contrôle du contenu) sont
passées par l'appelant, collecte_rss.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Callable

from newskit.commun import RACINE  # noqa: E402

ETAT = RACINE / "cache" / "sante_sources.json"
SEUIL = 6  # échecs consécutifs d'un hôte avant l'audit (environ trois quarts d'heure de collecte)
AUDITS_PAR_PASSE = 2  # un audit dure de 10 à 40 s : le passage de cinq minutes reste court
IMITATION_AUTO = True  # décision du mainteneur, 06.11.2026
AUDIT_H = 24  # un même hôte n'est pas audité plus d'une fois par jour
REPOS_H, REPOS_MAX_H = 1, 24
ALERTE_JOURS = 7
F = "%Y-%m-%d %H:%M"

_audits_du_passage = 0


def hote(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def cle(url: str, flux: bool) -> str:
    """Ce qui se compte : un flux ou un plan de site à son adresse, relue à chaque passage ; les pages d'articles d'un
    hôte ensemble, chacune n'étant lue qu'une fois. Un succès ailleurs sur l'hôte n'efface rien (06.11.2026 : le flux
    d'openai.com passait, ses pages étaient refusées, et le seuil n'était jamais atteint)."""
    return url if flux else f"{hote(url)} pages"


def lire_etat() -> dict:
    try:
        e = json.loads(ETAT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        e = {}
    for k in ("suivis", "imite", "remplace"):
        e.setdefault(k, {})
    return e


def ecrire_etat(e: dict) -> None:
    ETAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = ETAT.with_name(ETAT.name + ".tmp")
    tmp.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ETAT)


def _plus(t: str, heures: float) -> str:
    return (dt.datetime.strptime(t, F) + dt.timedelta(hours=heures)).strftime(F)


def adresse(url: str) -> str:
    """L'adresse à lire : celle qu'une réparation a mise à la place, sinon la même."""
    return lire_etat()["remplace"].get(url, url)


def imite(h: str) -> bool:
    """Vrai si une réparation a retenu l'imitation d'un navigateur pour cet hôte."""
    return bool(lire_etat()["imite"].get(h))


def en_repos(url: str, flux: bool, maintenant: str) -> str:
    """La fin du repos (« AAAA-MM-JJ HH:MM ») de l'adresse ou des pages de l'hôte, sinon ""."""
    r = lire_etat()["suivis"].get(cle(url, flux), {}).get("repos") or ""
    return r if r > maintenant else ""


def succes(url: str, maintenant: str, flux: bool) -> None:
    e = lire_etat()
    h = e["suivis"].get(cle(url, flux))
    if h and (h.get("echecs") or h.get("repos")):
        h.update(echecs=0, repos="", n_repos=0, dernier_succes=maintenant)
        ecrire_etat(e)


def echec(
    url: str,
    maintenant: str,
    *,
    flux: bool,
    auditer: Callable[[str], dict],
    lire: Callable[[str, bool], bytes],
    robots_exclut_ia: Callable[[str], bool],
    alerter: Callable[[str, str], object],
    utilisable: Callable[[bytes, bool], bool],
) -> str:
    """Note l'échec d'une lecture ; au seuil, audite et répare. Rend la voie trouvée, « repos » ou ""."""
    global _audits_du_passage
    e = lire_etat()
    h = e["suivis"].setdefault(cle(url, flux), {"echecs": 0})
    h["echecs"] = h.get("echecs", 0) + 1
    h.setdefault("premier_echec", maintenant)
    h["dernier_echec"] = maintenant
    if h["echecs"] < SEUIL:
        ecrire_etat(e)
        return ""
    audit_recent = h.get("audit_le") and _plus(h["audit_le"], AUDIT_H) > maintenant
    if not audit_recent and _audits_du_passage < AUDITS_PAR_PASSE:
        _audits_du_passage += 1
        e["imite"].pop(hote(url), None)
        a = auditer(url) or {}
        if a:  # un audit raté (accesspath illisible) ne compte pas comme l'audit du jour
            h["audit_le"] = maintenant
            h["audit"] = {k: a.get(k) for k in ("tier", "tls", "rationale")}
        voie = _reparer(e, url, a, flux, lire, robots_exclut_ia, utilisable)
        if voie:
            h.update(echecs=0, repos="", n_repos=0, voie=voie, repare_le=maintenant)
            ecrire_etat(e)
            print(f"  ~~ sap {cle(url, flux)} : réparé, {voie}", file=sys.stderr)
            return voie
    h["n_repos"] = h.get("n_repos", 0) + 1
    h["repos"] = _plus(maintenant, min(REPOS_MAX_H, REPOS_H * 2 ** (h["n_repos"] - 1)))
    h["voie"] = ""
    alerte = not h.get("alerte_le") or _plus(h["alerte_le"], ALERTE_JOURS * 24) <= maintenant
    if alerte:
        h["alerte_le"] = maintenant
    ecrire_etat(e)
    a = h.get("audit") or {}
    print(
        f"  ~~ sap {cle(url, flux)} : sans voie, au repos jusqu'à {h['repos']} (tier {a.get('tier')}, "
        f"{str(a.get('rationale') or '')[:120]})",
        file=sys.stderr,
    )
    if alerte:
        alerter(
            f"source injoignable sans réparation : {hote(url)}",
            f"Adresse : {url}\n{h['echecs']} échecs consécutifs depuis {h.get('premier_echec')}.\n"
            f"Audit sap du {h.get('audit_le')} : tier {a.get('tier')}, empreinte TLS {a.get('tls')}.\n"
            f"{a.get('rationale') or ''}\n"
            f"L'hôte se repose jusqu'à {h['repos']} ; l'audit se refait au plus une fois par jour.\n",
        )
    return "repos"


def _reparer(e: dict, url: str, a: dict, flux: bool, lire, robots_exclut_ia, utilisable) -> str:
    imiter_permis = IMITATION_AUTO and not robots_exclut_ia(url)
    essais: list[tuple[str, bool]] = [(url, True)] if imiter_permis else []
    if flux:
        for ep in a.get("rss") or []:
            alt = urllib.parse.urljoin(url, ep)
            if alt != url:
                essais.append((alt, False))
                if imiter_permis:
                    essais.append((alt, True))
    for u, imiter in essais:
        if utilisable(lire(u, imiter) or b"", flux):
            if imiter:
                e["imite"][hote(url)] = True
            if u != url:
                e["remplace"][url] = u
            return ("imitation de navigateur" if imiter else "lecture simple") + ("" if u == url else f", flux {u}")
    return ""

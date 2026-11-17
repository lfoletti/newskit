"""Les refus fréquents de la relecture, portés en consigne de la première rédaction (mainteneur, 17.11.2026 : « les
motifs récurrents ne devraient-ils pas être consignés et portés en consigne lors des premières passes? »).

Le 17.11.2026, 295 reproches de la relecture d'ai-governance revenaient sous quelques formes : un fait absent de la
source (79), un terme non expliqué (28), « agents » sans dire qu'il s'agit d'IA (21), le fait neuf absent du titre (17).
Chaque refus coûte une réécriture. Les règles fixes de la lettre (lt.REGLES) disent déjà ces principes ; les exemples
récents les montrent au rédacteur, dans les mots du relecteur.

Deux sources, les journaux du média : cache/fil_refus.jsonl (chaque billet non tenu, ses essais et leurs reproches) et
les fiches refusées du registre du fil (champ `fiche` des billets, daté par l'ajout du billet). Les reproches des JOURS
derniers jours se regroupent par leur forme (la tête avant « : », nombres réduits) ; les MAX plus fréquents, vus au
moins MIN fois, entrent dans la consigne avec leur dernier exemple. Un motif qui ne revient plus sort de lui-même.
Jamais bloquant : un journal absent ou illisible donne une consigne vide.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

JOURS = 14  # la fenêtre des reproches lus
MAX = 10  # motifs portés en consigne, au plus
MIN = 2  # un motif vu une seule fois n'est pas récurrent
MOTS_CLE = 6  # la forme d'un reproche : ses premiers mots ; le relecteur écrit librement la suite
EXEMPLE_MAX = 220


def journal() -> Path:
    from newskit import fil  # noqa: PLC0415 — fil importe ce module

    return fil.JOURNAL_REFUS


def cle(reproche: str) -> str:
    """Le motif d'un reproche. Un reproche qui commence par un terme cité porte sur ce terme : le terme est le motif
    (17.11.2026 : 22 reproches sur « agents », écrits de 8 façons). Sinon, sa forme : la tête avant « : », nombres
    réduits, ses MOTS_CLE premiers mots. Casse pliée."""
    r = " ".join(str(reproche).split())
    terme = re.match(r"«\s*([^»]+?)\s*»", r)
    if terme:
        return f"« {terme.group(1).lower()} »"
    tete = re.sub(r"\d+", "N", r.split(" : ", 1)[0])
    return " ".join(tete.split()[:MOTS_CLE]).lower()


def frequents(entrees: list[tuple[str, str]], maintenant: datetime) -> list[tuple[int, str]]:
    """(nombre, dernier exemple) des motifs les plus fréquents de la fenêtre ; `entrees` : (quand, reproche)."""
    depuis = f"{maintenant - timedelta(days=JOURS):%Y-%m-%d %H:%M}"
    groupes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for quand, r in entrees:
        if re.match(r"\d{4}-\d{2}-\d{2}", quand or "") and quand >= depuis:
            groupes[cle(r)].append((quand, r))
    out = [(len(v), max(v)[1]) for v in groupes.values() if len(v) >= MIN]
    return sorted(out, key=lambda x: -x[0])[:MAX]


def _entrees(graphe: str) -> list[tuple[str, str]]:
    from newskit import fil  # noqa: PLC0415

    out = []
    j = journal()
    if j.exists():
        for ligne in j.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(ligne)
            except ValueError:
                continue
            if x.get("graphe") == graphe:
                out += [(x.get("quand", ""), r) for e in x.get("essais") or [] for r in e.get("reproches") or []]
    F = json.loads(fil.chemin_fil(graphe).read_text(encoding="utf-8"))
    for b in (F.get("billets") or []) + (F.get("pile") or []):
        f = b.get("fiche")
        if isinstance(f, dict):  # une fiche tenue ne garde pas de reproches
            out += [(str(b.get("ajoute") or ""), r) for r in f.get("reproches") or []]
    return out


def consigne(graphe: str, maintenant: datetime | None = None) -> str:
    """Le bloc ajouté aux règles de la première rédaction ; vide sans motif récurrent ou sans journal lisible."""
    try:
        motifs = frequents(_entrees(graphe), maintenant or datetime.now())
    except (OSError, ValueError):
        return ""
    if not motifs:
        return ""
    lignes = "".join(f"- {n} fois : {ex[:EXEMPLE_MAX]}\n" for n, ex in motifs)
    return (
        f"Refus fréquents de la relecture ces {JOURS} derniers jours, à éviter dès la première rédaction "
        f"(nombre de refus, dernier exemple) :\n{lignes}"
    )

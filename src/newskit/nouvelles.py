"""Le registre unique des nouvelles (CHANTIER_NOUVELLES.md ; mainteneur, 05.11.2026 : « on ne devrait pas lire la même
nouvelle deux fois, sauf s'il y a un développement nouveau »).

Le registre est le fichier du fil, graphe/<graphe>/fil.json : un billet est une nouvelle, c'est-à-dire un événement.
- `chemin` : la fiche de sa première source ; c'est l'identifiant de la nouvelle ;
- `sources_plus` : les sources attachées (le même événement, vu par une autre source), chacune avec le `chemin` de sa
  fiche ;
- `suite_de`, `developpement` : un fait neuf sur un événement déjà au registre est une nouvelle à part entière, liée à
  la première.
Le fil écrit le registre. La lettre le lit. Ce qu'une lettre a déjà dit se lit dans les lettres gardées
(lettres/<graphe>/<date>.json).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from newskit import config_sources as cfg  # noqa: E402
from newskit.commun import RACINE  # noqa: E402 — la racine des données : les lettres gardées y sont

MEMOIRE = 30  # jours : une nouvelle dite par une lettre ne revient pas pendant ce temps


def chemin_du_registre(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "fil.json"


def lire(graphe: str) -> dict:
    try:
        return json.loads(chemin_du_registre(graphe).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"billets": [], "vues": [], "depense": {}}


def toutes(F: dict) -> list[dict]:
    """Les nouvelles du registre : celles qui ont paru au fil, puis celles qui attendent dans la pile."""
    return (F.get("billets") or []) + (F.get("pile") or [])


def texte(b: dict) -> str:
    """Le texte d'une nouvelle tel qu'il se lit, au fil comme dans la lettre : son descriptif, puis la définition d'un
    terme que sa source n'explique pas (fil.definir)."""
    return " ".join(
        t
        for t in (str(b.get("descriptif") or "").strip(), str((b.get("definition") or {}).get("texte") or "").strip())
        if t
    )


def a_completer(b: dict) -> bool:
    """Un terme que le lecteur ne connaît pas reste sans définition, trouvée ou pas encore cherchée."""
    return bool(b.get("terme")) and not str((b.get("definition") or {}).get("texte") or "").strip()


def fiches_de(b: dict) -> list[str]:
    """Les fiches d'une nouvelle : sa première source, puis ses attaches."""
    return [c for c in [b.get("chemin"), *(s.get("chemin") for s in b.get("sources_plus") or [])] if c]


def _adresses(b: dict) -> set[str]:
    return {b.get("url"), *(s.get("url") for s in b.get("sources_plus") or [])}


def nouvelle_de(F: dict, chemin: str, url: str | None = None) -> dict | None:
    """La nouvelle d'une fiche : celle dont elle est la première source ou une attache. `url` : l'adresse de la fiche,
    pour une attache faite avant le 05.11.2026, qui n'a pas de chemin."""
    for b in toutes(F):
        if chemin in fiches_de(b) or (url and url in _adresses(b)):
            return b
    return None


def attacher(b: dict, f: dict) -> bool:
    """Attache la fiche `f` à la nouvelle `b` : le même événement, une source de plus. Faux si elle y est déjà."""
    if f.get("chemin") in fiches_de(b) or (f.get("url") and f["url"] in _adresses(b)):
        return False
    b.setdefault("sources_plus", []).append({k: f.get(k) or "" for k in ("titre", "editeur", "date", "url", "chemin")})
    return True


def rattacher(F: dict, charger) -> int:
    """Les attaches faites avant le 05.11.2026 reçoivent le chemin de leur fiche, cherchée par son adresse parmi les
    fiches vues par le fil ; rend leur nombre. Une attache dont la fiche est partie reçoit un chemin vide, pour ne pas
    la rechercher à chaque passage. `charger` : lettre.charger_fiches."""
    sans = [s for b in toutes(F) for s in b.get("sources_plus") or [] if "chemin" not in s]
    if not sans:
        return 0
    connues = {c for b in toutes(F) for c in fiches_de(b)}
    fiches = charger(sorted(c for c in F.get("vues") or [] if c not in connues))
    par_adresse = {f.get("url"): c for c, f in fiches.items() if f.get("url")}
    for s in sans:
        s["chemin"] = par_adresse.get(s.get("url")) or ""
    return len(sans)


def portees(graphe: str, jour: str, jours: int = MEMOIRE) -> dict[str, str]:
    """Les fiches citées par les lettres gardées des `jours` jours avant `jour`, lignes liées comprises : {chemin: date
    de la première lettre}. La lettre de `jour` ne compte pas : une lettre recomposée le même jour redit les siennes."""
    d = RACINE / "lettres" / graphe
    debut = (dt.date.fromisoformat(jour[:10]) - dt.timedelta(days=jours)).isoformat()
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        if not debut <= p.stem[:10] < jour[:10]:
            continue
        try:
            L = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        blocs = [L.get("faits") or []]
        for cle in ("aussi", "bref"):
            v = L.get(cle) or []
            blocs += list(v.values()) if isinstance(v, dict) else [v]
        for x in (x for bloc in blocs for x in bloc if isinstance(x, dict)):
            for c in [
                x.get("chemin"),
                *(s.get("chemin") for s in [*(x.get("sources_plus") or []), *(x.get("liees") or [])]),
            ]:
                if c:
                    out.setdefault(c, p.stem[:10])
    return out


def dites(F: dict, chemins: list[str], portees: dict[str, str]) -> dict[str, str]:
    """Les fiches dont la nouvelle a déjà paru dans une lettre : {chemin: date de cette lettre}. Une fiche est dite
    quand une lettre l'a citée, ou a cité une autre source de la même nouvelle. Un développement est une autre
    nouvelle : la lettre qui a dit l'événement d'origine ne l'a pas dit."""
    out = {}
    for c in chemins:
        b = nouvelle_de(F, c)
        quand = sorted(portees[x] for x in {c, *(fiches_de(b) if b else [])} if x in portees)
        if quand:
            out[c] = quand[0]
    return out


def ecartees(F: dict, chemins: list[str]) -> set[str]:
    """Les fiches que le fil a lues sans en faire une nouvelle ni une attache : hors champ ou sans fait."""
    vues = set(F.get("vues") or [])
    connues = {c for b in toutes(F) for c in fiches_de(b)}
    return {c for c in chemins if c in vues and c not in connues}


# --- La signature : ce qui distingue un événement (N3) ----------------------------------------------------------------
# Mesuré le 05.11.2026 sur les 121 fiches lues par le fil : les paires d'une même nouvelle (Volta, DNSFilter, noyau
# Linux, Szpunar, NCSC) sortent dans les 45 premières de 7 260, avec des paires fausses entre elles (pages d'un même
# site, discours d'une même institution). La signature propose donc ; le modèle décide.

PROCHES = 3  # les nouvelles proches proposées au modèle pour une fiche
RE_NOM = re.compile(r"[A-ZÀ-ÖØ-Þ][\w-]*(?:[ -][A-ZÀ-ÖØ-Þ][\w-]*)*")
RE_NOMBRE = re.compile(r"\d+(?:[.,]\d+)*(?:[ \u00a0\u202f]\d{3})*(\s?(?:%|percent|per cent|pour cent))?")
RE_DEBUT = re.compile(r"(?:^|[.!?:;»”\"\n]\s+|\(\s*)$")


def _plier(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def _noms(texte: str) -> list[str]:
    """Les suites de mots à majuscule. Le premier mot d'une phrase porte une majuscule sans être un nom : il sort de la
    suite, sauf s'il porte une autre majuscule ou un chiffre (DNSFilter, ASUS)."""
    out = []
    for m in RE_NOM.finditer(texte):
        mots = re.split(r"[ ]", m.group(0))
        if RE_DEBUT.search(texte[: m.start()]) and not re.search(r"[A-ZÀ-ÖØ-Þ\d]", mots[0][1:]):
            mots = mots[1:]
        if mots:
            out.append(_plier(" ".join(mots)))
    return out


def _nombres(texte: str) -> list[str]:
    """Les nombres, sans séparateur (« 20,1 % », « 20.1% » : « 201% » ; « 1 271 », « 1,271 » : « 1271 »)."""
    return [re.sub(r"\D", "", m.group(0)) + ("%" if m.group(1) else "") for m in RE_NOMBRE.finditer(texte)]


def signature(texte: str | None) -> set[str]:
    """Les noms propres et les nombres d'un texte. Un chiffre seul ou une année ne distingue pas une nouvelle."""
    texte = str(texte or "")
    return {n for n in _noms(texte) if len(n) > 2} | {
        "#" + n for n in _nombres(texte) if len(n) > 1 and not re.fullmatch(r"(?:19|20)\d\d", n)
    }


def proches(cible: set[str], autres: dict[str, set[str]], k: int = PROCHES, plafond: int | None = None) -> list[str]:
    """Les `k` textes de `autres` les plus proches de `cible`, par leurs termes communs pesés par leur rareté ; le plus
    proche d'abord. Un terme présent dans plus de `plafond` textes ne compte pas (par défaut 3, ou un vingtième)."""
    n = len(autres) + 1
    df = Counter(t for s in [cible, *autres.values()] for t in s)
    plafond = max(3, n // 20) if plafond is None else plafond
    notes = {c: sum(math.log(1 + n / df[t]) for t in cible & s if df[t] <= plafond) for c, s in autres.items()}
    return [c for c in sorted(notes, key=lambda c: -notes[c]) if notes[c] > 0][:k]


def fait_neuf(developpement: str, avant: str) -> bool:
    """Un développement porte un fait neuf : un nombre, une date ou un nom propre absent des sources d'avant. Sans
    cela, la source redit la nouvelle : elle s'y attache."""
    connus = set(re.findall(r"\w+", _plier(str(avant or ""))))
    mots = {m for n in _noms(str(developpement or "")) for m in re.findall(r"\w+", n) if len(m) > 2}
    return bool(set(_nombres(str(developpement or ""))) - set(_nombres(str(avant or ""))) or mots - connus)


def fusionner(F: dict, garde: dict, absorbe: dict) -> None:
    """Deux nouvelles du registre portent le même événement : `garde` reçoit les sources d'`absorbe`, qui sort du
    registre. Une suite de la nouvelle absorbée devient une suite de la nouvelle gardée, qui reçoit son contexte si
    elle n'en a pas."""
    src = absorbe.get("source") or {}
    attacher(
        garde,
        {
            "titre": src.get("titre") or absorbe.get("titre"),
            "editeur": src.get("editeur"),
            "date": src.get("date") or absorbe.get("date"),
            "url": absorbe.get("url"),
            "chemin": absorbe.get("chemin"),
        },
    )
    for s in absorbe.get("sources_plus") or []:
        attacher(garde, s)
    for cle in ("billets", "pile"):
        F[cle] = [b for b in F.get(cle) or [] if b is not absorbe]
    for b in toutes(F):
        if b.get("suite_de") == absorbe.get("chemin"):
            b["suite_de"] = garde.get("chemin")
    if absorbe.get("contexte") and not garde.get("contexte"):  # le groupe de la nouvelle fondue (fil.lier)
        garde["contexte"], garde["evenement"] = absorbe["contexte"], absorbe.get("evenement") or ""

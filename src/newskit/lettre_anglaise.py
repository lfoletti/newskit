"""La lettre en anglais (mainteneur, 06.11.2026 : « fais tout l'écosystème » ; « les traductions doivent venir des textes
originaux, pas du fil. ce serait des dérivés de dérivés »).

`pour(L)` rend la lettre anglaise d'une lettre française : les mêmes éléments, chacun écrit d'après sa source par la
chaîne du fil anglais (fil_anglais.ecrire : rédaction, relecture adverse, réécritures), sans voir le texte français. Un
billet anglais du fil, écrit lui aussi d'après la source, sert tel quel. Un élément refusé à chaque essai ne paraît
pas ; ce qui est écrit en français hors de ces textes (l'éclairage, les mots en gras, le lieu, les liens dans le texte)
ne passe pas, et une nouvelle liée garde le titre de sa source. La lettre anglaise se garde sous cache/lettres_en : un
second envoi ne la réécrit pas.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from newskit import fil
from newskit import fil_anglais as fa

CACHE = fa.RACINE / "cache" / "lettres_en"
RETIRES = ("lecture", "gras", "lieu", "liens")        # écrits en français, hors des textes réécrits


def chemin(L: dict) -> Path:
    return CACHE / str(L.get("graphe") or "") / f"{L['envoi']}{'-weekly' if L.get('periode') == 'semaine' else ''}.json"


def _elements(L: dict) -> list[tuple[dict, str]]:
    """(élément, champ du texte) de la lettre : la une, puis les billets et les brèves de chaque rubrique."""
    out = [(f, "paragraphe") for f in L.get("faits") or []]
    for groupe in ("aussi", "bref"):
        for v in (L.get(groupe) or {}).values():
            out += [(x, "descriptif") for x in v]
    return out


def _du_fil(graphe: str) -> dict[str, dict]:
    """Les billets anglais tenus du fil, par chemin de fiche."""
    try:
        F = fil.lire(graphe)
    except Exception:  # noqa: BLE001 — sans fil lisible, tout s'écrit
        return {}
    return {b["chemin"]: b["en"] for b in (F.get("pile") or []) + (F.get("billets") or [])
            if (b.get("en") or {}).get("tenu") and b.get("chemin")}


def ecrire(L: dict) -> dict | None:
    """La lettre anglaise de L, sans la garder ; None si aucun élément n'est tenu."""
    graphe = str(L.get("graphe") or "")
    E = copy.deepcopy(L)
    elements = _elements(E)
    tenus = _du_fil(graphe)
    a_ecrire = [x for x, _ in elements if x.get("chemin") and x["chemin"] not in tenus]
    if a_ecrire:
        fiches = fa.lt.charger_fiches(sorted({x["chemin"] for x in a_ecrire}))
        lot = [({"chemin": x["chemin"], "date": (x.get("source") or {}).get("date") or ""}, fiches[x["chemin"]])
               for x in a_ecrire if x["chemin"] in fiches]
        ecrits, _ = fa.ecrire(lot, graphe) if lot else ({}, 0.0)
        for n, en in ecrits.items():
            if en.get("tenu"):
                tenus.setdefault(lot[n][0]["chemin"], en)
    garde = set()
    for x, champ in elements:
        en = tenus.get(x.get("chemin") or "")
        if not en:
            continue
        x["titre"], x[champ] = en["titre"], en["texte"]
        for k in RETIRES:
            x.pop(k, None)
        for y in x.get("liees") or []:
            y["titre"] = (y.get("source") or {}).get("titre") or y.get("titre")
        garde.add(id(x))
    rub = lambda g: fil.RUBRIQUES_EN.get(g, g)  # noqa: E731
    E["faits"] = [{**f, **({"rubrique": rub(f["rubrique"])} if f.get("rubrique") else {})}
                  for f in E.get("faits") or [] if id(f) in garde]
    for groupe in ("aussi", "bref"):
        E[groupe] = {rub(g): [x for x in v if id(x) in garde] for g, v in (E.get(groupe) or {}).items()}
        E[groupe] = {g: v for g, v in E[groupe].items() if v}
    if "ordre" in E:
        E["ordre"] = [rub(g) for g in E["ordre"]]
    if not E["faits"] and not E["aussi"] and not E["bref"]:
        return None
    E["langue"], E["bref_reste"] = "en", 0
    return E


def pour(L: dict) -> dict | None:
    """La lettre anglaise de L, gardée sous CACHE et relue au second appel."""
    p = chemin(L)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    E = ecrire(L)
    if E is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        garde = {k: v for k, v in E.items() if k not in ("expediteur", "desinscription")}
        p.write_text(json.dumps(garde, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    return E

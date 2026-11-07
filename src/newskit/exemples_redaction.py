"""Les corrections du rédacteur en chef, gardées comme exemples pour le rédacteur du reporter.

mainteneur, 04.11.2026 : « comment le faire passer d'un piètre journaliste à un bon journaliste ? que pourrait-il
apprendre ? » ; « oui, commence par les corrections comme exemples ».

Chaque correction faite à la relecture humaine d'un éclairage (`reporter.py --corriger`, avec `--raison`) se garde en
paires « avant → après » de phrases, avec sa raison, dans docs/corrections_eclairages.jsonl (suivi par git, dépôt
privé). La consigne de rédaction reçoit les EXEMPLES paires les plus proches du sujet : un modèle apprend mieux d'un
exemple concret que d'une règle de plus. Les corrections du relecteur adverse n'y entrent pas : seul le jugement
humain fait exemple.
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from newskit import lettre as lt

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)

FICHIER = RACINE / "docs" / "corrections_eclairages.jsonl"
EXEMPLES = 6  # paires données au rédacteur
MOTS_PAIRE = 120  # une paire plus longue est coupée dans la consigne
BONUS_HUMAIN = 2  # une correction humaine passe devant une correction du relecteur aussi proche du sujet
_VIDES = {
    "le",
    "la",
    "les",
    "de",
    "des",
    "du",
    "un",
    "une",
    "et",
    "a",
    "au",
    "aux",
    "en",
    "sur",
    "pour",
    "par",
    "que",
    "qui",
    "ne",
    "pas",
    "se",
    "sa",
    "son",
    "ses",
    "est",
    "il",
    "elle",
    "dans",
    "d",
    "l",
}


def noter(
    avant: str,
    apres: str,
    raison: str,
    graphe: str = "",
    slug: str = "",
    quand: str | None = None,
    source: str = "humain",
) -> None:
    """Garde une correction, ajoutée, jamais remplacée. `source` : « humain » (relecture du mainteneur) ou « relecteur »
    (correction du relecteur adverse gardée jusqu'à la publication)."""
    FICHIER.parent.mkdir(parents=True, exist_ok=True)
    with FICHIER.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "quand": quand or time.strftime("%Y-%m-%d %H:%M:%S"),
                    "graphe": graphe,
                    "slug": slug,
                    "avant": " ".join(str(avant).split()),
                    "apres": " ".join(str(apres).split()),
                    "raison": " ".join(str(raison).split()),
                    "source": source,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _phrases(ecl: dict) -> list[str]:
    out = [str(ecl.get("titre") or "").strip()]
    for t in [ecl.get("chapeau") or ""] + list(ecl.get("recit") or []) + [ecl.get("impose") or ""]:
        out += lt.phrases(str(t))
    return [p for p in out if p]


def paires(avant: dict, apres: dict) -> list[tuple[str, str]]:
    """Les phrases changées entre deux versions d'un éclairage : (avant, après) ; une phrase retirée a un après vide,
    une phrase ajoutée un avant vide."""
    a, b = _phrases(avant), _phrases(apres)
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        xa, xb = a[i1:i2], b[j1:j2]
        for k in range(max(len(xa), len(xb))):
            pa, pb = (xa[k] if k < len(xa) else ""), (xb[k] if k < len(xb) else "")
            if pa != pb:
                out.append((pa, pb))
    return out


def noter_paires(avant: dict, apres: dict, raison: str, graphe: str = "", slug: str = "") -> int:
    ps = paires(avant, apres)
    for a, b in ps:
        noter(a, b, raison or "correction à la relecture", graphe, slug)
    return len(ps)


def lire() -> list[dict]:
    if not FICHIER.exists():
        return []
    out = []
    for ligne in FICHIER.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ligne))
        except ValueError:
            continue
    return out


def _mots(t: str) -> set[str]:
    return {m for m in re.findall(r"[a-z0-9]+", lt.plier(t)) if len(m) > 2 and m not in _VIDES}


def choisir(texte: str, n: int = EXEMPLES) -> list[dict]:
    """Les n corrections les plus proches de `texte` (mots communs), la plus récente d'abord à égalité."""
    cible = _mots(texte)
    tout = lire()
    notes = [
        (
            len(cible & _mots(f"{x.get('avant', '')} {x.get('apres', '')} {x.get('raison', '')}"))
            + (BONUS_HUMAIN if x.get("source", "humain") == "humain" else 0),
            i,
            x,
        )
        for i, x in enumerate(tout)
    ]
    return [x for _, _, x in sorted(notes, key=lambda t: (-t[0], -t[1]))[:n]]


def bloc(exemples: list[dict]) -> str:
    """Le passage de la consigne de rédaction ; vide sans exemple."""
    if not exemples:
        return ""

    def court(t):
        m = str(t).split()
        return " ".join(m[:MOTS_PAIRE]) + (" …" if len(m) > MOTS_PAIRE else "")

    lignes = [
        f"- avant : « {court(x.get('avant', ''))} »\n  après : « {court(x.get('apres', '')) or '(supprimé)'} »\n"
        f"  raison : {x.get('raison', '')}"
        for x in exemples
    ]
    return (
        "Corrections du rédacteur en chef sur des éclairages précédents. Elles montrent ce qu'il attend ; "
        "applique la même exigence, sans recopier ces phrases :\n" + "\n".join(lignes)
    )

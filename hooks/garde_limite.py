#!/usr/bin/env python3
"""Crochet PreToolUse du plugin Newskit : pas d'appel au modèle quand la fenêtre d'usage est fermée.

Le moteur pose `cache/limite_atteinte` dans le dossier du média quand le modèle répond « usage limit » trois fois
d'affilée (newskit.couts) ; la marque vaut trois heures. Tant qu'elle est fraîche, ce crochet refuse toute commande
Bash qui lance `claude -p` ou une nuit (`newskit routine`). La garde tient hors du modèle : elle ne dépend ni du skill
ni de la session.

Bibliothèque standard seule : le Python qui lance le crochet n'a pas forcément le paquet newskit.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

MARQUE_VALIDE_S = 10800                  # newskit.couts.MARQUE_VALIDE_S (3 h) ; un test garde l'égalité
APPEL_AU_MODELE = re.compile(r"(?:^|[\s;&|(])(?:claude\s+-p|newskit\s+routine)\b")
MEDIA = re.compile(r"^media\s*:", re.MULTILINE)


def dossier_du_media(cwd: Path) -> Path | None:
    """NEWSKIT_DONNEES, sinon le dossier courant ou le plus proche au-dessus qui porte un config.yaml de média."""
    if os.environ.get("NEWSKIT_DONNEES"):
        return Path(os.environ["NEWSKIT_DONNEES"]).expanduser()
    for d in (cwd, *cwd.parents):
        c = d / "config.yaml"
        try:
            if c.is_file() and MEDIA.search(c.read_text(encoding="utf-8")):
                return d
        except OSError:
            continue
    return None


def marque_fraiche(media: Path) -> str | None:
    m = media / "cache" / "limite_atteinte"
    try:
        if m.is_file() and time.time() - m.stat().st_mtime < MARQUE_VALIDE_S:
            return m.read_text(encoding="utf-8").strip()[:200] or "limite d'usage"
    except OSError:
        pass
    return None


def main() -> int:
    try:
        e = json.loads(sys.stdin.read())
        commande = e["tool_input"]["command"]
        cwd = Path(e.get("cwd") or os.getcwd())
    except (ValueError, KeyError, TypeError):
        return 0                         # une entrée illisible ne bloque rien
    if not isinstance(commande, str) or not APPEL_AU_MODELE.search(commande):
        return 0
    media = dossier_du_media(cwd)
    note = marque_fraiche(media) if media else None
    if note is None:
        return 0
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": (f"Newskit : la fenêtre d'usage du modèle est fermée (limite atteinte, marque de "
                                     f"{media / 'cache' / 'limite_atteinte'} : {note}). Attendre qu'elle se rouvre ; "
                                     f"la marque tombe d'elle-même après 3 h.")}}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

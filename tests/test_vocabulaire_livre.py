"""Le vocabulaire anglais du moteur est livré avec lui (07.11.2026).

fondre_doublons juge un libellé étranger s'il n'a pas de mots anglais ; il lisait la liste du système,
/usr/share/dict/words, présente sur macOS (web2, 234 936 mots) et absente d'Ubuntu. Le même graphe donnait d'autres
libellés à traduire selon la machine : le témoin était rouge dans la CI de Newskit, et le serveur jugeait autrement que
le Mac. La liste (web2, le Webster de 1934, tombé dans le domaine public) est livrée dans le dépôt.
"""

from __future__ import annotations

import sys
from pathlib import Path

from newskit import commun  # noqa: E402
from newskit import fondre_doublons as fd  # noqa: E402


def test_le_vocabulaire_vient_du_depot_et_non_du_systeme():
    assert Path(fd.DICTIONNAIRE).resolve().is_relative_to(Path(commun.CODE).resolve())
    assert Path(fd.DICTIONNAIRE).is_file()


def test_le_vocabulaire_est_web2_entier(monkeypatch):
    monkeypatch.setattr(fd, "_vocabulaire", None)
    v = fd.vocabulaire()
    assert len(v) > 230_000 and {"swarm", "wax", "hive", "tithe"} <= v

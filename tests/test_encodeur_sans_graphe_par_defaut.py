"""`encodeur.py` ne choisit pas de graphe à la place de celui qui le lance (CHANTIER_GENERIQUE.md, étape 1).

`--graphe` valait « ai-music » par défaut : un `encodeur.py paires` oublié payait des appels de modèle sur un graphe
qu'on n'avait pas nommé. Un moteur sans thème ne porte aucun nom de graphe (NEWSKIT.md, N2).
"""

from __future__ import annotations

import sys

import pytest

from newskit import encodeur


@pytest.mark.parametrize("action", ["paires", "entrainer", "evaluer", "jeu-rare"])
def test_sans_graphe_rien_ne_part(monkeypatch, action):
    appels = []
    for f in ("generer_paires", "entrainer", "evaluer", "jeu_rare"):
        monkeypatch.setattr(encodeur, f, lambda *a, _f=f, **k: appels.append(_f))
    monkeypatch.setattr(sys, "argv", ["encodeur.py", action])
    with pytest.raises(SystemExit) as e:
        encodeur.main()
    assert e.value.code == 2
    assert appels == []


def test_le_graphe_nomme_est_celui_qui_sert(monkeypatch):
    vus = []
    monkeypatch.setattr(encodeur, "evaluer", lambda g, bancs=None: vus.append(g))
    monkeypatch.setattr(encodeur.cfg, "nom_actuel", lambda g: g)
    monkeypatch.setattr(sys, "argv", ["encodeur.py", "evaluer", "--graphe", "essai"])
    assert encodeur.main() == 0
    assert vus == ["essai"]

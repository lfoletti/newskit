"""Un résultat payé est écrit avant qu'un nouvel appel parte (couche 2, 07.11.2026).

Les appels tournaient dans un pool de fils et les lots s'écrivaient dans le fil principal : avec un seul fil, l'appel
suivant partait pendant que le lot précédent attendait d'être validé et écrit. L'étape tuée à ce moment perdait des
réponses payées, qui étaient repayées au passage suivant (le témoin de reprise tombait en CI, plus lente). Un appel
n'entre maintenant dans une place qu'une fois le résultat d'une place précédente traité.
"""

from __future__ import annotations

import sys
import time

from newskit import couts  # noqa: E402
from newskit import extraire_concepts as ec  # noqa: E402


def test_aucun_appel_ne_part_avant_l_ecriture_du_lot_precedent(tmp_path, monkeypatch):
    racine = tmp_path
    fiches = []
    for i in range(5):
        f = racine / "sources" / f"f{i}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"fiche {i}", encoding="utf-8")
        fiches.append((f, f"h{i}"))
    lots = racine / "graphe" / "couche2" / "fiches"
    monkeypatch.setattr(ec, "RACINE", racine)
    monkeypatch.setattr(ec, "LOTS", lots)
    monkeypatch.setattr(ec, "EMPREINTES", racine / "graphe" / "couche2" / "empreintes.json")
    monkeypatch.setattr(ec, "PARALLELE", 1)
    monkeypatch.setattr(ec, "fiches_en_retard", lambda: fiches)
    monkeypatch.setattr(ec, "refus_connus", lambda: {})
    monkeypatch.setattr(ec, "prompt_de", lambda f: ({"titre": f.stem}, f.read_text(encoding="utf-8")))
    vus = []

    def appeler(invite):
        vus.append(len(list(lots.glob("*.json"))) if lots.exists() else 0)
        return {"nodes": [{"id": "n"}], "edges": []}, ""
    monkeypatch.setattr(ec, "appeler_claude", appeler)
    monkeypatch.setattr(ec, "normaliser_extraction", lambda ext, f, champs: (ext, []))

    def valider(ext):
        time.sleep(0.2)                       # le fil principal lent, comme en CI
        return []
    monkeypatch.setattr(ec, "valider", valider)
    monkeypatch.setattr(ec, "identifiant_de_fiche", lambda f, champs=None: f.stem)
    monkeypatch.setattr(ec.cfg, "graphe_declare", lambda nom: {"domaine": "ruchers"})
    monkeypatch.setattr(couts, "en_limite", lambda: False, raising=False)
    monkeypatch.setattr(sys, "argv", ["extraire_concepts", "--plafond", "5"])
    assert ec.main() == 0
    assert vus == [0, 1, 2, 3, 4]

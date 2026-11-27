"""La purge d'une fiche retirée du corpus touche ses fichiers de couche 1 et le partage ; graphe.json n'en est plus un
(K3, 27.11.2026 : le cerveau est le graphe, il se purge à son versement)."""

import json

from newskit import purger_fiches as pf

FICHE = "sources/x/2026-01-01_pub.md"


def test_la_purge_touche_squelette_et_partage_et_plus_graphe_json(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "RACINE", tmp_path)
    g = tmp_path / "graphe" / "g"
    g.mkdir(parents=True)
    squelette = {
        "nodes": [{"id": pf._prefixe(FICHE), "source_file": FICHE}, {"id": "auteur_a", "source_file": FICHE}],
        "links": [{"source": pf._prefixe(FICHE), "target": "auteur_a", "source_file": FICHE}],
    }
    (g / "squelette.json").write_text(json.dumps(squelette), encoding="utf-8")
    (g / "partage.json").write_text(json.dumps({FICHE: "g", "sources/x/autre.md": "g"}), encoding="utf-8")
    ancien = json.dumps({"nodes": [{"id": "n", "source_file": FICHE}], "links": []})
    (g / "graphe.json").write_text(ancien, encoding="utf-8")
    pf.purger({FICHE})
    assert json.loads((g / "squelette.json").read_text(encoding="utf-8")) == {"nodes": [], "links": []}
    assert json.loads((g / "partage.json").read_text(encoding="utf-8")) == {"sources/x/autre.md": "g"}
    assert (g / "graphe.json").read_text(encoding="utf-8") == ancien

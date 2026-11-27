"""nouveau_graphe.py : un graphe né par la recherche web n'existe pour config_sources que par ses sources ; celles de
l'éclaireur et du canon sont des pièces (crooners, 22.10.2026 : « graphe inconnu » tant qu'elles manquaient)."""

from types import SimpleNamespace

import yaml

from newskit import nouveau_graphe as ng


def test_les_blocs_de_sources_de_l_eclaireur_et_du_canon_se_collent_tels_quels():
    blocs = yaml.safe_load(ng.blocs_sources("ai-exemple"))
    assert [(b["cle"], b["dossier"], b["plateforme"], b["graphe"]) for b in blocs] == [
        ("eclaireur_ai_exemple", "eclaireur/ai-exemple", "Recherche web", "ai-exemple"),
        ("canon_ai_exemple", "canon/ai-exemple", "Référence", "ai-exemple"),
    ]
    assert all(b["dense"] is True and b["abonnement"] == "gratuit" for b in blocs)


def test_les_sources_de_l_eclaireur_et_du_canon_sont_des_pieces(monkeypatch):
    monkeypatch.setattr(ng.cfg, "toutes", lambda graphe=None: [SimpleNamespace(dossier="eclaireur/ai-exemple")])
    pieces = {p: ok for ok, p, _ in ng.pieces_sources("ai-exemple")}
    assert pieces == {"source de l'éclaireur : eclaireur/ai-exemple": True, "source du canon : canon/ai-exemple": False}


def test_le_registre_du_skill_nomme_le_cerveau_et_plus_graphe_json(monkeypatch, tmp_path):
    """K3 (27.11.2026) : le cerveau est le graphe ; le registre du skill le nomme, relatif à la racine, et une entrée
    ancienne perd son chemin vers graphe.json."""
    import json

    from newskit import cerveau_cortexdb as cc

    monkeypatch.setattr(ng, "SKILL", tmp_path / "graphes.json")
    monkeypatch.setattr(ng, "RACINE", tmp_path)
    monkeypatch.setattr(cc, "DOSSIER", tmp_path / "cache" / "cortexdb")
    monkeypatch.setattr(ng.cfg, "edition", lambda: {"nom": "m"})
    ng.SKILL.write_text(json.dumps({"crooners": {"graphe": "graphe/crooners/graphe.json"}}), encoding="utf-8")
    ng.ecrire_skill("crooners", "Crooners", "", [])
    e = json.loads(ng.SKILL.read_text(encoding="utf-8"))["crooners"]
    assert e["cerveau"] == "cache/cortexdb/crooners.db" and "graphe" not in e

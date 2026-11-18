"""Un nom propre ou un sigle qui a une forme française établie la prend (mainteneur, 14.11.2026 : aucune notion
trouvée pour le nom français d'une personne, mais la notion existe sous son nom anglais) ; les libellés gardés tels
quels se revoient, les plus reliés d'abord."""

from __future__ import annotations

from newskit import francais as fr


def test_la_consigne_demande_la_forme_francaise_etablie_d_un_nom_propre_ou_d_un_sigle():
    c = fr._consigne("l")
    assert "Un nom propre ou un sigle qui a une forme française établie la prend" in c
    assert "« United Nations » → « Nations unies », « UN » → « ONU »" in c
    assert "n'est pas « nom_propre »" in c
    assert (
        "Un nom propre (personne, organisation, tribunal, produit, dépôt de code), un sigle ou le titre officiel"
        not in c
    )


def _noeuds():
    return [
        {"id": "concept_a", "label": "Alpha Body", "file_type": "concept"},
        {"id": "concept_b", "label": "Beta Org", "file_type": "concept"},
        {"id": "concept_c", "label": "Gamma Thing", "file_type": "concept"},
        {"id": "concept_d", "label": "Démocratie", "file_type": "concept"},
        {"id": "auteur_x", "label": "Xavier Writer", "file_type": "concept", "kind": "auteur"},
    ]


def _table():
    t = {"libelles": {}, "textes": {}}
    for i, s, f in (
        ("concept_a", "Alpha Body", "Alpha Body"),
        ("concept_b", "Beta Org", "Beta Org"),
        ("concept_c", "Gamma Thing", "Chose gamma"),
        ("concept_d", "Démocratie", "Démocratie"),
        ("auteur_x", "Xavier Writer", "Xavier Writer"),
    ):
        t["libelles"][i] = [f, fr.empreinte(s)]
    return t


def test_seuls_les_libelles_gardes_tels_quels_se_revoient_les_plus_relies_d_abord():
    liens = {"concept_a": 3, "concept_b": 9, "concept_c": 50, "concept_d": 40, "auteur_x": 99}
    assert fr.gardes_tels_quels(_noeuds(), _table(), liens) == ["concept_b", "concept_a"]
    # traduit (c), déjà français (d), auteur caché (x) : dehors


def test_revoir_rend_les_libelles_a_retraduire_sans_les_retirer_de_la_table(monkeypatch):
    noeuds, table = _noeuds(), _table()
    liens = {"concept_a": 3, "concept_b": 9}
    items = fr.revoir(noeuds, table, liens, 1)
    assert items == [("concept_b", "l", "Beta Org")]
    assert table["libelles"]["concept_b"][0] == "Beta Org"  # un passage interrompu garde l'ancien français
    monkeypatch.setattr(fr.couts, "en_limite", lambda: False)
    monkeypatch.setattr(fr, "_lot", lambda g, c, lot: [(i, c, "Organisation bêta", s) for i, _, s in lot])
    assert fr.traduire("g", items, table, 1) == 1
    assert table["libelles"]["concept_b"] == ["Organisation bêta", fr.empreinte("Beta Org")]


def test_les_liens_d_un_graphe_comptent_les_deux_bouts():
    assert fr.compter_liens([{"source": "a", "target": "b"}, {"source": "a", "target": "c"}]) == {
        "a": 2,
        "b": 1,
        "c": 1,
    }


def test_la_commande_revoit_les_noms_avant_le_reste(monkeypatch, capsys):
    vus = {}
    graphe = {
        "nodes": _noeuds() + [{"id": "concept_e", "label": "Epsilon rule", "file_type": "concept"}],
        "links": [{"source": "concept_b", "target": "concept_a"}, {"source": "concept_b", "target": "concept_c"}],
    }
    monkeypatch.setattr(fr.cfg, "nom_actuel", lambda g: g)
    monkeypatch.setattr(fr, "charger_graphe", lambda g: graphe)
    monkeypatch.setattr(fr, "lire_table", lambda g: _table())
    monkeypatch.setattr(fr, "ecrire_table", lambda g, t: None)
    monkeypatch.setattr(fr, "traduire", lambda g, items, table, p, sauver=None: vus.setdefault("items", items) and 0)
    monkeypatch.setattr("sys.argv", ["francais", "--graphe", "g", "--revoir-noms", "1"])
    assert fr.main() == 0
    assert vus["items"] == [("concept_b", "l", "Beta Org"), ("concept_e", "l", "Epsilon rule")]
    assert "g : 2 libellé(s) gardé(s) tel(s) quel(s) ; 1 à revoir" in capsys.readouterr().out


def test_les_noeuds_se_lisent_dans_le_graphe(monkeypatch, tmp_path):
    p = tmp_path / "graphe.json"
    p.write_text('{"nodes": [{"id": "n"}], "links": []}', encoding="utf-8")
    monkeypatch.setattr(fr.cfg, "chemins_du_graphe", lambda g: {"graphe": p})
    assert fr.charger_noeuds("g") == [{"id": "n"}] and fr.charger_graphe("g")["links"] == []


def test_le_graphe_compresse_plus_recent_passe_devant(monkeypatch, tmp_path):
    import gzip

    p = tmp_path / "graphe.json"
    p.write_text('{"nodes": [{"id": "ancien"}]}', encoding="utf-8")
    (tmp_path / "graphe.json.gz").write_bytes(gzip.compress(b'{"nodes": [{"id": "neuf"}], "links": []}'))
    monkeypatch.setattr(fr.cfg, "chemins_du_graphe", lambda g: {"graphe": p})
    assert fr.charger_graphe("g")["nodes"] == [{"id": "neuf"}]

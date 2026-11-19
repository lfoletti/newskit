"""La nuit calcule la couche 1 comme le passage continu.

Le puits de la nuit tirait la couche 1 de chaque fiche du graphe composé ; le passage continu la tire du squelette et de
la structure des titres de la fiche. Les deux chemins donnaient rarement la même empreinte (3 fiches sur 197, mesuré) :
le graphe composé range les extrémités d'une arête dans un ordre qui n'est pas son sens (`_src`, `_tgt` le gardent), il
fond des titres de même libellé et donne une arête à une seule de deux fiches. La nuit remplaçait donc presque chaque
document versé le jour. Elle passe par `CoucheUn`, comme le passage continu : une fiche inchangée garde son empreinte de
l'un à l'autre, et le puits ne lit plus le graphe composé pour un graphe à couche 1."""

from __future__ import annotations

import json
import sys

import pytest

from newskit import cerveau_continu as ct
from newskit import config_sources as cfg
from newskit import puits_cortexdb as pc

A = "sources/a/2026-11-18_un-article.md"
B = "sources/a/2026-11-17_un-autre.md"
ABSENTE = "sources/a/2026-11-16_disparue.md"
SQUELETTE = {
    "nodes": [
        {"id": "a", "label": "Un article", "file_type": "document", "kind": "article", "source_file": A},
        {"id": "ada", "label": "Ada Auteur", "file_type": "concept", "kind": "auteur", "source_file": A},
        {"id": "b", "label": "Un autre", "file_type": "document", "kind": "article", "source_file": B},
        {"id": "bob", "label": "Bob Auteur", "file_type": "concept", "kind": "auteur", "source_file": B},
    ],
    "edges": [
        {"source": "a", "target": "ada", "relation": "ecrit_par", "confidence": "EXTRACTED", "source_file": A,
         "source_location": "L1", "weight": 1.0, "_origin": "squelette"},
        {"source": "b", "target": "bob", "relation": "ecrit_par", "confidence": "EXTRACTED", "source_file": B,
         "source_location": "L1", "weight": 1.0, "_origin": "squelette"},
    ],
}  # fmt: skip
LOT = {
    "nodes": [
        {"id": "a", "label": "Un article", "file_type": "document", "source_file": A},
        {"id": "amende", "label": "Amende", "file_type": "concept", "source_file": A},
    ],
    "edges": [{"source": "a", "target": "amende", "relation": "mentions", "confidence": "EXTRACTED",
               "confidence_score": 1.0, "source_file": A, "weight": 1.0}],
    "hyperedges": [],
}  # fmt: skip


def relations(c1) -> list[tuple[str, str, str]]:
    return [(r["from"], r["type"], r["to"]) for r in c1["relations"]]


# --- le sens d'une arête du graphe composé


def test_la_couche_1_suit_le_sens_garde_par_la_composition():
    """La composition range `source` et `target` dans un ordre à elle et garde le sens dans `_src` et `_tgt`."""
    compose = {
        "nodes": SQUELETTE["nodes"],
        "links": [
            {**SQUELETTE["edges"][0], "source": "ada", "target": "a", "_src": "a", "_tgt": "ada"},
            SQUELETTE["edges"][1],  # sans `_src` : `source` et `target` font foi
        ],
    }
    par_fiche, _ = pc.couche1_par_fiche(compose, pc.RACINE)
    assert relations(par_fiche[A]) == [("Un article", "ecrit_par", "Ada Auteur")]
    assert relations(par_fiche[B]) == [("Un autre", "ecrit_par", "Bob Auteur")]


def test_une_fiche_ne_parcourt_que_les_noeuds_qu_elle_touche(monkeypatch):
    """La nuit calcule la couche 1 de chaque fiche du graphe : le squelette entier, par fiche, coûterait des heures."""
    vus = []
    vraie = pc.couche1_par_fiche
    monkeypatch.setattr(
        pc,
        "couche1_par_fiche",
        lambda compose, *a: vus.append([n["id"] for n in compose["nodes"]]) or vraie(compose, *a),
    )
    c = pc.CoucheUn(SQUELETTE, pc.RACINE)
    assert relations(c.d_une_fiche(A, None)) == [("Un article", "ecrit_par", "Ada Auteur")]
    assert vus == [["a", "ada"]]


def test_un_titre_tient_a_une_fiche_sans_arete_au_squelette():
    """La fiche n'a aucune arête au squelette : son nœud y est quand même l'extrémité de ses titres."""
    seule = {"nodes": [SQUELETTE["nodes"][0]], "edges": []}
    structure = {
        "nodes": [{"id": "a_partie", "label": "Une partie", "file_type": "document", "source_file": A, "_origin": "ast"}],
        "edges": [{"source": "a", "target": "a_partie", "relation": "contains", "confidence": "EXTRACTED",
                   "source_file": A, "_origin": "ast"}],
    }  # fmt: skip
    c = pc.CoucheUn(seule, pc.RACINE)
    assert relations(c.d_une_fiche(A, structure)) == [
        ("Un article", "contains", pc.PREFIXE_TITRE + pc.court("a_partie"))
    ]
    assert c.ecartes["arete_sans_extremite"] == 0


def test_un_lien_vers_un_fichier_hors_du_graphe_n_est_pas_une_anomalie():
    """La structure d'une fiche nomme les fichiers que ses liens visent : seul un fichier du graphe fait une arête."""
    lien = {"relation": "references", "confidence": "EXTRACTED", "source_file": A, "_origin": "ast"}
    structure = {"nodes": [], "edges": [{**lien, "source": "a", "target": "media_ailleurs_md"},
                                        {**lien, "source": "a", "target": "b"},
                                        {**lien, "source": "a", "target": "inconnu", "relation": "contains"}]}  # fmt: skip
    c = pc.CoucheUn(SQUELETTE, pc.RACINE)
    assert relations(c.d_une_fiche(A, structure)) == [
        ("Un article", "ecrit_par", "Ada Auteur"),
        ("Un article", "references", "Un autre"),
    ]
    assert c.ecartes["arete_sans_extremite"] == 1  # le titre sans nœud, pas le lien


# --- la commande de la nuit


@pytest.fixture
def depot(tmp_path, monkeypatch):
    for chemin, titre in ((A, "Un article"), (B, "Un autre")):
        p = tmp_path / chemin
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f'---\ntitle: "{titre}"\ndate: "2026-11-18"\n---\n\n# {titre}\n\n## Une partie\n\nTexte.\n',
            encoding="utf-8",
        )
    base = tmp_path / "graphe" / "g"
    base.mkdir(parents=True)
    lot = base / "lot.json"
    lot.write_text(json.dumps({**LOT, "nodes": [{**n, "source_file": str(tmp_path / A)} for n in LOT["nodes"]]}))
    monkeypatch.setattr(pc, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "source_du_chemin", lambda s: None)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": base, "graphe": base / "graphe.json"})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: None)
    monkeypatch.setattr(pc, "corps_versable", lambda src, entete, corps, droits=None, chemin="": corps)
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [lot])
    monkeypatch.setattr(pc, "squelette_du_graphe", lambda g: SQUELETTE)
    (tmp_path / "avant.json").write_text("{}", encoding="utf-8")
    return tmp_path


def nuit(depot, monkeypatch, capsys, *options) -> tuple[int, dict, dict]:
    """Un passage de la commande en comptage : son code, son bilan, l'empreinte de chaque versement."""
    monkeypatch.setattr(
        sys, "argv", ["puits", "--graphe", "g", *options, "--compter", "--empreintes-avant", str(depot / "avant.json")]
    )
    recus: dict[str, str] = {}
    vraie = pc.EnPlace.verser
    with monkeypatch.context() as m:
        m.setattr(
            pc.EnPlace,
            "verser",
            lambda self, v, vus: recus.update({v["origine"]: pc.empreinte(v)}) or vraie(self, v, vus),
        )
        code = pc.main()
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1]), recus


def du_continu(depot, source: str, lot: dict | None) -> str:
    """L'empreinte du versement de la fiche par le chemin du passage continu (`cerveau_continu._passer`)."""
    squelette = ct.squelette_du_graphe("g")
    c = pc.CoucheUn(squelette, pc.RACINE, {})
    canon = pc.canon_squelette({"nodes": squelette["nodes"]})
    return pc.empreinte(
        pc.versement_d_une_fiche(source, lot, c.d_une_fiche(source, ct.structure_de(source)), {}, canon, None, depot)
    )


def test_la_nuit_rend_l_empreinte_du_passage_continu(depot, monkeypatch, capsys):
    """Le graphe composé contredit le squelette (sens inversé, fiche absente, titre fondu) : la nuit ne le lit pas."""
    compose = {
        "nodes": [n for n in SQUELETTE["nodes"] if n["source_file"] == A],
        "links": [{**SQUELETTE["edges"][0], "source": "ada", "target": "a"}],
    }
    (depot / "graphe" / "g" / "graphe.json").write_text(json.dumps(compose), encoding="utf-8")
    code, bilan, recus = nuit(depot, monkeypatch, capsys, "--couche1")
    lot = json.loads((depot / "graphe" / "g" / "lot.json").read_text())
    assert code == 0 and recus == {A: du_continu(depot, A, lot), B: du_continu(depot, B, None)}
    assert (bilan["verses"], bilan["sans_lot"], bilan["ajoutes"]) == (1, 1, 2)
    # pour chaque fiche, l'arête du squelette et ses deux titres
    assert bilan["couche1_relations"] == 6 and bilan["couche1_sans_document"] == 0


def test_la_nuit_ne_charge_pas_le_graphe_compose_d_un_graphe_a_couche_1(depot, monkeypatch, capsys):
    (depot / "graphe" / "g" / "graphe.json").write_text("illisible", encoding="utf-8")
    code, bilan, recus = nuit(depot, monkeypatch, capsys, "--couche1")
    assert code == 0 and set(recus) == {A, B}


def test_la_structure_des_titres_de_chaque_fiche_entre_dans_son_versement(depot, monkeypatch, capsys):
    _, _, avec = nuit(depot, monkeypatch, capsys, "--couche1")
    monkeypatch.setattr(pc, "structure_de", lambda source: None)
    _, bilan, sans = nuit(depot, monkeypatch, capsys, "--couche1")
    assert bilan["couche1_relations"] == 2 and avec[A] != sans[A] and avec[B] != sans[B]


def test_la_nuit_compte_les_aretes_ecartees_et_la_couche_1_sans_document(depot, monkeypatch, capsys):
    squelette = {
        "nodes": [
            *SQUELETTE["nodes"],
            {"id": "x", "label": "Disparue", "file_type": "document", "kind": "article", "source_file": ABSENTE},
        ],
        "edges": [
            *SQUELETTE["edges"],
            {**SQUELETTE["edges"][0], "target": "inconnu"},  # une extrémité hors du squelette
            {**SQUELETTE["edges"][0], "source": "x", "target": "ada", "source_file": ABSENTE},
            {
                "source": "y",
                "target": "z",
                "relation": "cite",
                "source_file": "/ailleurs/hors.md",
                "_origin": "squelette",
            },
        ],
    }
    monkeypatch.setattr(pc, "squelette_du_graphe", lambda g: squelette)
    code, bilan, recus = nuit(depot, monkeypatch, capsys, "--couche1")
    assert code == 0 and set(recus) == {A, B}  # ni la fiche disparue, ni un chemin hors du dépôt
    assert bilan["couche1_arete_sans_extremite"] == 1
    assert (bilan["fiche_absente"], bilan["couche1_sans_document"]) == (1, 1)
    assert (bilan["couche1_fiche_hors_depot"], bilan["couche1_boucle"]) == (1, 0)


def test_sans_couche_1_la_nuit_lit_encore_le_graphe_compose(depot, monkeypatch, capsys):
    """Un graphe sans couche 1 n'a pas de squelette au cerveau : ses fiches sans lot se lisent au graphe composé."""
    monkeypatch.setattr(pc, "squelette_du_graphe", lambda g: pytest.fail("pas de squelette sans couche 1"))
    compose = {"nodes": [SQUELETTE["nodes"][2]], "links": []}
    (depot / "graphe" / "g" / "graphe.json").write_text(json.dumps(compose), encoding="utf-8")
    code, bilan, recus = nuit(depot, monkeypatch, capsys)
    assert code == 0 and set(recus) == {A, B} and bilan["couche1_relations"] == 0
    (depot / "graphe" / "g" / "graphe.json").unlink()
    code, bilan, recus = nuit(depot, monkeypatch, capsys)
    assert code == 0 and set(recus) == {A}


# --- un seul calcul du squelette et de la structure pour les deux passages


def test_le_passage_continu_lit_le_squelette_et_la_structure_du_puits(depot, monkeypatch):
    monkeypatch.setattr(pc, "structure_de", lambda source: {"nodes": [source], "edges": []})
    assert ct.squelette_du_graphe("g") is SQUELETTE
    assert ct.structure_de(A) == {"nodes": [A], "edges": []}


def test_le_squelette_et_la_structure_viennent_de_leurs_modules(depot, monkeypatch):
    from newskit import extraire_squelette as ex

    monkeypatch.undo()
    monkeypatch.setattr(ex, "construire", lambda g: ({"nodes": [g]}, {}, []))
    assert pc.squelette_du_graphe("g") == {"nodes": ["g"]}
    monkeypatch.setattr(pc, "RACINE", depot)
    assert "Une partie" in {n["label"] for n in pc.structure_de(A)["nodes"]}

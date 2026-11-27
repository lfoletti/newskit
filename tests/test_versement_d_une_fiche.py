"""Le versement d'une seule fiche, pour le passage continu et pour le puits de la nuit.

Le puits calculait le versement de chaque fiche dans les deux boucles de sa commande, à partir du graphe composé de la
nuit. Le passage continu verse une fiche quelques minutes après sa collecte : il lui faut le même versement, pour une
fiche, sans graphe composé. `versement_d_une_fiche` est ce calcul, que la commande de la nuit appelle aussi ;
`couche1_d_une_fiche` tire la couche 1 d'une fiche du squelette et de la structure de ses titres ; `EnPlace.retirer`
retire un document du cerveau."""

from __future__ import annotations

import copy
import json
import sys

import pytest

from newskit import config_sources as cfg
from newskit import puits_cortexdb as pc

FICHE = "sources/a/2026-11-18_un-article.md"
AUTRE = "sources/a/2026-11-17_un-autre.md"
ID = "sources_a_2026_11_18_un_article"
SQUELETTE = {
    "nodes": [
        {"id": ID, "label": "Un article", "file_type": "document", "kind": "article", "source_file": FICHE},
        {"id": "auteur_a", "label": "Ada Auteur", "file_type": "concept", "kind": "auteur", "source_file": FICHE},
        {"id": "autre", "label": "Un autre", "file_type": "document", "kind": "article", "source_file": AUTRE},
        {"id": "auteur_b", "label": "Bob Auteur", "file_type": "concept", "kind": "auteur", "source_file": AUTRE},
    ],
    "edges": [
        {"source": ID, "target": "auteur_a", "relation": "ecrit_par", "confidence": "EXTRACTED", "source_file": FICHE,
         "source_location": "L1", "weight": 1.0, "_origin": "squelette"},
        {"source": "autre", "target": "auteur_b", "relation": "ecrit_par", "confidence": "EXTRACTED",
         "source_file": AUTRE, "source_location": "L1", "weight": 1.0, "_origin": "squelette"},
    ],
}  # fmt: skip
STRUCTURE = {
    "nodes": [
        # la structure redéclare la fiche, étiquetée par son nom de fichier : le squelette fait foi
        {"id": ID, "label": "2026-11-18_un-article", "file_type": "document", "source_file": FICHE, "_origin": "ast"},
        {"id": ID + "_total", "label": "**Total des amendes**", "file_type": "document", "source_file": FICHE,
         "source_location": "L12", "_origin": "ast"},
    ],
    "edges": [
        {"source": ID, "target": ID + "_total", "relation": "contains", "confidence": "EXTRACTED", "source_file": FICHE,
         "source_location": "L12", "weight": 1.0, "_origin": "ast"},
    ],
}  # fmt: skip


def couche1(structure=STRUCTURE, source=FICHE, squelette=SQUELETTE, tmp=None):
    return pc.couche1_d_une_fiche(squelette, structure, source, tmp or pc.RACINE)


def relations(c1) -> set[tuple[str, str, str]]:
    return {(r["from"], r["type"], r["to"]) for r in c1["relations"]}


# --- la couche 1 d'une fiche


def test_meme_couche1_que_le_graphe_compose_pour_une_fiche_simple():
    """Le graphe composé de la nuit réunit le squelette et la structure nettoyée : la fiche y a la même couche 1."""
    compose = {
        "nodes": [*SQUELETTE["nodes"], {**STRUCTURE["nodes"][1], "label": "Total des amendes"}],
        "links": [*SQUELETTE["edges"], *STRUCTURE["edges"]],
    }
    par_fiche, _ = pc.couche1_par_fiche(compose, pc.RACINE)
    assert couche1() == par_fiche[FICHE]
    assert couche1(source=AUTRE, structure=None) == par_fiche[AUTRE]


def test_le_noeud_de_fiche_de_la_structure_se_fond_dans_le_squelette():
    c1 = couche1()
    assert relations(c1) == {
        ("Un article", "ecrit_par", "Ada Auteur"),
        ("Un article", "contains", pc.PREFIXE_TITRE + pc.court(ID + "_total")),
    }
    assert "2026-11-18_un-article" not in {e["name"] for e in c1["entites"]}


def test_un_titre_en_gras_perd_son_balisage():
    assert "Total des amendes" in {e["name"] for e in couche1()["entites"]}
    assert STRUCTURE["nodes"][1]["label"] == "**Total des amendes**"  # la structure reçue n'est pas modifiée


def test_une_fiche_sans_structure_garde_son_squelette():
    assert relations(couche1(structure=None)) == {("Un article", "ecrit_par", "Ada Auteur")}


def test_la_couche_1_ne_porte_que_les_aretes_de_la_fiche():
    assert all("Bob Auteur" not in (r["from"], r["to"]) for r in couche1()["relations"])


def test_une_fiche_absente_du_squelette_rend_rien():
    assert couche1(source="sources/a/inconnue.md") is None
    seul_auteur = {"nodes": [SQUELETTE["nodes"][1]], "edges": []}  # un nœud de la fiche, mais pas la fiche
    assert couche1(squelette=seul_auteur) is None


def test_une_fiche_sans_arete_a_une_couche_1_vide():
    assert couche1(structure=None, squelette={"nodes": [SQUELETTE["nodes"][0]], "edges": []}) == {
        "entites": [],
        "relations": [],
    }


def test_un_noeud_hors_du_depot_ne_casse_pas_la_preparation(tmp_path):
    hors = {"id": "ailleurs", "label": "Ailleurs", "kind": "article", "source_file": "/ailleurs/x.md"}
    c = pc.CoucheUn({"nodes": [*SQUELETTE["nodes"], hors], "edges": SQUELETTE["edges"]}, tmp_path)
    assert c.d_une_fiche(FICHE, None) == couche1(structure=None)


def test_le_squelette_d_un_passage_se_prepare_une_fois():
    c = pc.CoucheUn(SQUELETTE, pc.RACINE)
    assert c.d_une_fiche(FICHE, STRUCTURE) == couche1()
    assert c.d_une_fiche(AUTRE, None) == couche1(source=AUTRE, structure=None)


# --- le versement d'une fiche

LOT = {
    "nodes": [
        {"id": ID, "label": "Un article", "file_type": "document", "source_file": FICHE},
        {"id": "concept_amende", "label": "Amende", "file_type": "concept", "source_file": FICHE},
    ],
    "edges": [
        {"source": ID, "target": "concept_amende", "relation": "mentions", "confidence": "EXTRACTED",
         "confidence_score": 1.0, "source_file": FICHE, "weight": 1.0},
    ],
    "hyperedges": [],
}  # fmt: skip

# un lot porte une seule fiche : celui-ci en nomme deux
DEUX_FICHES = {"nodes": [LOT["nodes"][0], {**LOT["nodes"][1], "source_file": AUTRE}], "edges": []}


@pytest.fixture
def fiche_sur_disque(tmp_path, monkeypatch):
    p = tmp_path / FICHE
    p.parent.mkdir(parents=True)
    p.write_text(
        '---\ntitle: "Un article"\nauthor: "Ada Auteur"\ndate: "2026-11-18"\nurl: "https://e.org/1"\n---\n\n'
        "# Un article\n\nLe texte de l'article.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "source_du_chemin", lambda s: None)
    monkeypatch.setattr(
        pc, "corps_versable", lambda src, entete, corps, droits=None, chemin="": f"[{droits or 'publiable'}] {corps}"
    )
    return tmp_path


def verser(tmp, lot=None, c1=None, droits=None, source=FICHE):
    return pc.versement_d_une_fiche(source, lot, c1, None, droits, tmp)


def test_une_fiche_avec_lot_porte_ses_entites(fiche_sur_disque):
    v = verser(fiche_sur_disque, LOT)
    assert v["document"]["document_id"] == pc.court(FICHE)
    assert {e["name"] for e in v["entites"]} == {"Un article", "Amende"}
    assert [(r["from"], r["to"]) for r in v["relations"]] == [("Un article", "Amende")]


def test_une_fiche_sans_lot_se_verse_seule(fiche_sur_disque):
    v = verser(fiche_sur_disque)
    assert v["document"]["document_id"] == pc.court(FICHE) and v["origine"] == FICHE
    assert v["entites"] == [] and v["relations"] == []
    assert "Le texte de l'article." in v["document"]["content"]


def test_la_couche_1_complete_le_versement(fiche_sur_disque):
    c1 = couche1(tmp=fiche_sur_disque)
    for lot in (LOT, None):
        v = verser(fiche_sur_disque, lot, c1)
        assert ("Un article", "ecrit_par", "Ada Auteur") in relations(v)
        assert "Ada Auteur" in {e["name"] for e in v["entites"]}


def test_une_fiche_refusee_se_verse_en_notice(fiche_sur_disque):
    """Le texte versé passe par `corps_versable`, qui rend une notice pour une fiche non relayée."""
    for lot in (LOT, None):
        v = verser(fiche_sur_disque, lot, droits="masque d'un auteur")
        assert v["document"]["content"].count("[masque d'un auteur]") == 1


def test_une_fiche_absente_du_disque_ne_se_verse_pas(fiche_sur_disque):
    assert verser(fiche_sur_disque, LOT, source="sources/a/disparue.md") is None


def test_un_lot_invalide_se_dit(fiche_sur_disque):
    with pytest.raises(pc.LotInvalide):
        verser(fiche_sur_disque, DEUX_FICHES)


# --- le retrait d'un document


def test_retirer_appelle_les_deux_effacements(monkeypatch):
    appels = []
    monkeypatch.setattr(pc, "appeler_outil", lambda client, nom, args: appels.append((nom, args)) or {})
    bilan: dict = {}
    ep = pc.EnPlace(object(), {"a": "1"}, bilan)
    ep.retirer("a")
    assert appels == [("delete_document_graph", {"document_id": "a"}), ("knowledge_delete", {"knowledge_id": "a"})]
    assert bilan["retires"] == 1
    compte: dict = {}
    pc.EnPlace(None, {"a": "1"}, compte, compter=True).retirer("a")
    assert compte["retires"] == 1 and len(appels) == 2  # un comptage n'appelle rien


# --- la commande de la nuit passe par le même calcul


def test_le_puits_de_la_nuit_rend_la_meme_empreinte(fiche_sur_disque, tmp_path, monkeypatch, capsys):
    """La commande verse une fiche avec lot et une fiche sans lot par `versement_d_une_fiche` : mêmes empreintes."""
    tmp = fiche_sur_disque
    sans_lot = tmp / AUTRE
    sans_lot.write_text((tmp / FICHE).read_text(encoding="utf-8").replace("Un article", "Un autre"), encoding="utf-8")
    base = tmp / "graphe" / "g"
    base.mkdir(parents=True)
    lot_p = base / "lot.json"
    lot_p.write_text(json.dumps({**LOT, "nodes": [{**n, "source_file": str(tmp / FICHE)} for n in LOT["nodes"]]}))
    compose = {"nodes": copy.deepcopy(SQUELETTE["nodes"]), "links": copy.deepcopy(SQUELETTE["edges"])}
    (base / "graphe.json").write_text(json.dumps(compose), encoding="utf-8")
    monkeypatch.setattr(pc, "RACINE", tmp)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": base})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: None)
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [lot_p])
    monkeypatch.setattr(pc, "squelette_du_graphe", lambda g: copy.deepcopy(SQUELETTE))
    monkeypatch.setattr(pc, "structure_de", lambda source: None)
    apres = tmp / "apres.json"
    apres.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["puits", "--graphe", "g", "--couche1", "--compter", "--empreintes-avant", str(apres)]
    )
    appels = []
    vrai = pc.versement_d_une_fiche
    monkeypatch.setattr(pc, "versement_d_une_fiche", lambda *a: appels.append(a[0]) or vrai(*a))
    recus = {}
    vraie = pc.EnPlace.verser
    monkeypatch.setattr(
        pc.EnPlace, "verser", lambda self, v, vus: recus.update({v["origine"]: pc.empreinte(v)}) or vraie(self, v, vus)
    )
    assert pc.main() == 0
    bilan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (bilan["verses"], bilan["sans_lot"], bilan["ajoutes"], bilan["couche1_relations"]) == (1, 1, 2, 2)
    assert appels == [FICHE, AUTRE]
    c = pc.CoucheUn({"nodes": SQUELETTE["nodes"], "edges": SQUELETTE["edges"]}, tmp)
    canon = pc.canon_squelette(compose)
    lot = json.loads(lot_p.read_text())
    attendu = {
        FICHE: pc.empreinte(pc.versement_d_une_fiche(FICHE, lot, c.d_une_fiche(FICHE, None), canon, None, tmp)),
        AUTRE: pc.empreinte(pc.versement_d_une_fiche(AUTRE, None, c.d_une_fiche(AUTRE, None), canon, None, tmp)),
    }
    assert recus == attendu


def test_la_nuit_compte_une_fiche_disparue_et_un_lot_invalide(fiche_sur_disque, monkeypatch, capsys):
    tmp = fiche_sur_disque
    base = tmp / "graphe" / "g"
    base.mkdir(parents=True)
    disparue, invalide = base / "disparue.json", base / "invalide.json"
    disparue.write_text(json.dumps({"nodes": [{**LOT["nodes"][0], "source_file": "sources/a/disparue.md"}]}))
    invalide.write_text(json.dumps(DEUX_FICHES))
    monkeypatch.setattr(pc, "RACINE", tmp)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": base})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: None)
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [disparue, invalide])
    avant = tmp / "avant.json"
    avant.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["puits", "--graphe", "g", "--compter", "--empreintes-avant", str(avant)])
    assert pc.main() == 1
    sortie = capsys.readouterr()
    bilan = json.loads(sortie.out.strip().splitlines()[-1])
    assert (bilan["lots"], bilan["fiche_absente"], bilan["invalides"], bilan["verses"]) == (2, 1, 1, 0)
    assert "!! invalide.json" in sortie.err

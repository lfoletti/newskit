"""La table d'alias entre au cerveau par des fusions d'entités, plus par le nom versé.

Le pont versait chaque entité sous le nom de son canonique (`alias.json`, refait chaque nuit) : ce nom entrait dans
l'empreinte de la fiche, et une paire ajoutée à la table faisait réécrire chaque fiche qui nommait l'alias. Mesuré sur
six nuits d'un graphe de 10 939 lots : 119 à 192 fiches réécrites par nuit pour 62 à 113 paires changées, sans qu'aucune
fiche ait changé. Une fiche se verse maintenant sous les noms que son lot porte. La table s'applique après les
versements : `merge_entities` (CortexDB v2.157.0) fond chaque alias dans son canonique et note son nom sur l'entité
gardée, si bien qu'un versement ultérieur sous l'ancien nom arrive sur elle."""

from __future__ import annotations

import contextlib
import json
import sys
import types

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import puits_cortexdb as pc

A = "sources/a/2026-11-18_un-article.md"
LOT = {
    "nodes": [
        {"id": "a", "label": "Un article", "file_type": "document", "source_file": A},
        {"id": "minimisation", "label": "Minimisation", "file_type": "concept", "source_file": A},
        {"id": "data_minimization", "label": "Data minimization", "file_type": "concept", "source_file": A},
    ],
    "edges": [{"source": "a", "target": "minimisation", "relation": "mentions", "confidence": "EXTRACTED",
               "source_file": A}],
    "hyperedges": [],
}  # fmt: skip
TABLE = {"alias": {"minimisation": "data_minimization"}, "libelles": {"data_minimization": "Data minimization"}}
NOTION = {"label": "Sobriété des données", "phrases": [{"texte": "Une phrase.", "refs": [{"fiche": A}]}]}


def ident(nom: str) -> str:
    return pc.identifiant_d_entite(nom)


# --- le versement


def test_un_lot_se_verse_sous_les_noms_qu_il_porte(tmp_path):
    v = pc.lot_vers_cortexdb(LOT, {}, "Corps.", tmp_path)
    assert [e["name"] for e in v["entites"]] == ["Un article", "Minimisation", "Data minimization"]
    assert [(r["from"], r["to"]) for r in v["relations"]] == [("Un article", "Minimisation")]
    assert v["noms"] == {"a": "Un article", "minimisation": "Minimisation", "data_minimization": "Data minimization"}


def test_les_noms_du_versement_n_entrent_pas_dans_son_empreinte(tmp_path):
    v = pc.lot_vers_cortexdb(LOT, {}, "Corps.", tmp_path)
    assert pc.empreinte(v) == pc.empreinte({**v, "noms": {}})


def test_un_noeud_que_le_squelette_nomme_n_est_l_alias_de_rien(tmp_path):
    canon = {"ids": {"a": ("Un article du squelette", "article")}, "titres": {}}
    v = pc.lot_vers_cortexdb(LOT, {}, "Corps.", tmp_path, canon)
    assert v["entites"][0]["name"] == "Un article du squelette"
    assert v["noms"] == {"minimisation": "Minimisation", "data_minimization": "Data minimization"}


def test_une_synthese_garde_le_nom_de_sa_notion():
    v = pc.synthese_vers_cortexdb("minimisation", NOTION)
    assert v["document"]["title"] == "Sobriété des données" and v["entites"][0]["name"] == "Sobriété des données"


def test_la_couche_1_garde_le_libelle_d_un_noeud_sans_origine(tmp_path):
    n = {"id": "minimisation", "label": "Minimisation", "file_type": "concept"}
    assert pc.entite_composee(n, A)["name"] == "Minimisation"


# --- l'identifiant qu'une entité reçoit de son nom


@pytest.mark.parametrize(
    "nom, attendu",
    [
        ("EU AI Act", "entity:eu_ai_act"),
        ("  Données-clés_2  ", "entity:données_clés_2"),
        ("A.I. (Act)", "entity:ai_act"),
        ("ΣΟΦΟΣ", "entity:σοφοσ"),  # minuscule lettre par lettre, sans sigma final
        ("x²", "entity:x"),  # un exposant n'est pas un chiffre décimal
        ("a\tb", "entity:ab"),  # seuls l'espace, le tiret et le souligné séparent
        ("_-x-_", "entity:x"),
        ("!!!", "entity:entity"),
        ("entity:deja_la", "entity:deja_la"),
    ],
)
def test_l_identifiant_d_une_entite_se_tire_de_son_nom_comme_le_fait_cortexdb(nom, attendu):
    assert ident(nom) == attendu


# --- les groupes de la table


def test_un_groupe_reunit_sous_le_libelle_du_canonique_les_noms_verses_de_ses_alias():
    table = {"alias": {"p1": "g", "p2": "g"}, "libelles": {"g": "Data minimization"}}
    noms = {"p1": {"Minimisation"}, "p2": {"Minimisation des données", "data-minimization"}, "g": {"Data minimization"}}
    # « data-minimization » a l'identifiant du canonique : c'est déjà la même entité
    assert pc.groupes_d_alias(table, noms) == {"Data minimization": ["Minimisation", "Minimisation des données"]}


def test_un_alias_jamais_verse_ou_un_canonique_sans_libelle_ne_fait_pas_de_groupe():
    table = {"alias": {"p1": "g", "p2": "sans_libelle"}, "libelles": {"g": "Data minimization"}}
    assert pc.groupes_d_alias(table, {"p2": {"Autre"}}) == {}
    assert pc.groupes_d_alias({}, {"p1": {"Minimisation"}}) == {}


def test_une_chaine_d_alias_mene_a_son_dernier_canonique_et_une_boucle_a_rien():
    table = {
        "alias": {"a": "b", "b": "c", "w": "x", "x": "y", "y": "x"},
        "libelles": {"b": "B", "c": "C", "x": "X", "y": "Y"},
    }
    noms = {"a": {"A"}, "b": {"B"}, "w": {"W"}, "x": {"X"}, "y": {"Y"}}
    assert pc.groupes_d_alias(table, noms) == {"C": ["A", "B"]}


# --- la fusion


class Cerveau:
    """Les entités présentes, par identifiant ; `get_nodes` rend celles qu'il a, `merge_entities` retire les alias."""

    def __init__(self, *noms: str):
        self.noeuds, self.appels, self.refus = {ident(n) for n in noms}, [], set()

    def appel(self, nom: str, **args) -> dict:
        self.appels.append((nom, args))
        if nom == "get_nodes":
            return {"nodes": [{"id": i} for i in args["node_ids"] if i in self.noeuds]}
        if nom == "merge_entities":
            if args["canonical"] in self.refus:
                raise RuntimeError(self.refus_texte)
            self.noeuds -= set(args["aliases"])
            return {"merged": args["aliases"]}
        return {}

    refus_texte = 'StatusCode.NOT_FOUND details = "entity "entity:x" not found"'

    def fusions(self) -> list[tuple[str, list[str]]]:
        return [(a["canonical"], a["aliases"]) for n, a in self.appels if n == "merge_entities"]


def test_les_alias_presents_se_fondent_dans_leur_canonique_en_un_appel():
    c = Cerveau("Data minimization", "Minimisation", "Minimisation des données")
    bilan = pc.fondre_alias(c.appel, {"Data minimization": ["Minimisation", "Minimisation des données"]})
    assert c.fusions() == [("entity:data_minimization", ["entity:minimisation", "entity:minimisation_des_données"])]
    assert bilan == {"alias_groupes": 1, "alias_fondus": 2, "alias_refuses": 0}


def test_un_alias_deja_fondu_ne_se_rejoue_pas():
    c = Cerveau("Data minimization", "Minimisation des données")
    bilan = pc.fondre_alias(c.appel, {"Data minimization": ["Minimisation", "Minimisation des données"]})
    assert c.fusions() == [("entity:data_minimization", ["entity:minimisation_des_données"])]
    assert bilan["alias_fondus"] == 1
    assert pc.fondre_alias(c.appel, {"Data minimization": ["Minimisation"]})["alias_fondus"] == 0
    assert len(c.fusions()) == 1


def test_sans_groupe_le_serveur_n_est_pas_appele():
    c = Cerveau()
    assert pc.fondre_alias(c.appel, {}) == {"alias_groupes": 0, "alias_fondus": 0, "alias_refuses": 0}
    assert c.appels == []


def test_un_canonique_absent_laisse_seul_son_unique_alias_et_reunit_les_autres_sous_le_premier():
    """La table a pu inverser une paire : le canonique d'hier, fondu depuis, n'a plus de nœud à lui."""
    c = Cerveau("Minimisation")
    assert pc.fondre_alias(c.appel, {"Data minimization": ["Minimisation"]})["alias_fondus"] == 0
    assert c.fusions() == []
    c = Cerveau("Minimisation", "Minimisation des données")
    bilan = pc.fondre_alias(c.appel, {"Data minimization": ["Minimisation des données", "Minimisation"]})
    assert c.fusions() == [("entity:minimisation_des_données", ["entity:minimisation"])]  # le premier du groupe
    assert bilan["alias_fondus"] == 1


def test_un_nom_alias_de_deux_canoniques_se_fond_une_fois():
    c = Cerveau("A", "B", "C")
    bilan = pc.fondre_alias(c.appel, {"A": ["B"], "C": ["B"]})
    assert c.fusions() == [("entity:a", ["entity:b"])] and bilan["alias_fondus"] == 1 and bilan["alias_refuses"] == 0


def test_les_entites_se_lisent_par_paquets(monkeypatch):
    monkeypatch.setattr(pc, "NOEUDS_PAR_LECTURE", 2)
    c = Cerveau("A", "B", "C")
    pc.fondre_alias(c.appel, {"A": ["B", "C"]})
    lus = [a["node_ids"] for n, a in c.appels if n == "get_nodes"]
    assert lus == [["entity:a", "entity:b"], ["entity:c"]]


def test_une_fusion_refusee_faute_d_entite_se_compte_et_les_suivantes_passent(capsys):
    c = Cerveau("A", "B", "C", "D")
    c.refus = {"entity:a"}
    bilan = pc.fondre_alias(c.appel, {"A": ["B"], "C": ["D"]})
    assert bilan == {"alias_groupes": 2, "alias_fondus": 1, "alias_refuses": 1}
    assert c.fusions() == [("entity:a", ["entity:b"]), ("entity:c", ["entity:d"])]  # la refusée, puis la suivante
    assert c.noeuds == {"entity:a", "entity:b", "entity:c"}
    assert "entity:a" in capsys.readouterr().err


def test_une_autre_erreur_de_fusion_arrete_le_passage():
    """Un serveur sans l'outil (avant v2.157.0) ne doit pas publier un cerveau aux alias épars."""
    c = Cerveau("A", "B")
    c.refus, c.refus_texte = {"entity:a"}, "unknown tool: merge_entities"
    with pytest.raises(RuntimeError, match="unknown tool"):
        pc.fondre_alias(c.appel, {"A": ["B"]})


# --- la commande de la nuit


@pytest.fixture
def depot(tmp_path, monkeypatch):
    p = tmp_path / A
    p.parent.mkdir(parents=True)
    p.write_text('---\ntitle: "Un article"\n---\n\nTexte.\n', encoding="utf-8")
    base = tmp_path / "graphe" / "g"
    (base / "synthese").mkdir(parents=True)
    lot = base / "lot.json"
    lot.write_text(json.dumps({**LOT, "nodes": [{**n, "source_file": str(p)} for n in LOT["nodes"]]}))
    (base / "synthese" / "notions.json").write_text(json.dumps({"notions": {"notion_min": NOTION}}), encoding="utf-8")
    monkeypatch.setattr(pc, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "source_du_chemin", lambda s: None)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": base, "graphe": base / "absent.json"})
    monkeypatch.setattr(cfg, "charger_droits_auteurs", lambda: None)
    monkeypatch.setattr(pc, "corps_versable", lambda src, entete, corps, droits=None, chemin="": corps)
    monkeypatch.setattr(pc, "lots_du_graphe", lambda g: [lot])
    (tmp_path / "avant.json").write_text("{}", encoding="utf-8")
    return tmp_path


def table(depot, contenu: dict | None) -> None:
    p = depot / "graphe" / "g" / "alias.json"
    p.unlink(missing_ok=True)
    if contenu is not None:
        p.write_text(json.dumps(contenu), encoding="utf-8")


def compte(depot, monkeypatch, capsys) -> dict[str, str]:
    """Un passage en comptage : l'empreinte de chaque versement."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["puits", "--graphe", "g", "--pyramide", "--compter", "--empreintes-avant", str(depot / "avant.json")],
    )
    recus: dict[str, str] = {}
    vraie = pc.EnPlace.verser
    with monkeypatch.context() as m:
        m.setattr(
            pc.EnPlace,
            "verser",
            lambda self, v, vus: recus.update({v["origine"]: pc.empreinte(v)}) or vraie(self, v, vus),
        )
        assert pc.main() == 0
    bilan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "alias_fondus" not in bilan  # un comptage ne fond rien
    return recus


def test_l_empreinte_d_une_fiche_et_d_une_synthese_ne_suit_pas_la_table_d_alias(depot, monkeypatch, capsys):
    table(depot, None)
    sans = compte(depot, monkeypatch, capsys)
    table(depot, {**TABLE, "alias": {**TABLE["alias"], "notion_min": "data_minimization"}})
    assert compte(depot, monkeypatch, capsys) == sans and set(sans) == {A, "pyramide/notion_min"}


def serveur(monkeypatch, cerveau: Cerveau) -> None:
    """Un client dont chaque appel d'outil passe par `cerveau` ; une entité versée y prend son nœud."""

    def appeler(client, nom: str, args: dict) -> dict:
        if nom == "upsert_entities":
            cerveau.noeuds |= {ident(e["name"]) for e in args["entities"]}
        if nom == "ingest_document":
            cerveau.appels.append((nom, args))
            return {"chunk_node_ids": []}
        return cerveau.appel(nom, **args)

    client = types.ModuleType("cortexdb_client")
    client.CortexClient = types.SimpleNamespace(connect=lambda adresse, token: contextlib.nullcontext(object()))
    monkeypatch.setitem(sys.modules, "cortexdb_client", client)
    monkeypatch.setattr(pc, "appeler_outil", appeler)


def test_la_nuit_fond_les_alias_apres_les_versements_et_les_retraits(depot, monkeypatch, capsys):
    table(
        depot, {**TABLE, "alias": {**TABLE["alias"], "notion_min": "data_minimization", "jamais": "data_minimization"}}
    )
    (depot / "avant.json").write_text(json.dumps({"parti": "x"}), encoding="utf-8")
    c = Cerveau()
    serveur(monkeypatch, c)
    monkeypatch.setattr(
        sys,
        "argv",
        ["puits", "--graphe", "g", "--pyramide", "--jeton", "j", "--empreintes-avant", str(depot / "avant.json")],
    )
    assert pc.main() == 0
    bilan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # un alias vient du lot, l'autre de la synthèse d'une notion ; celui qu'aucun versement ne nomme est ignoré
    assert c.fusions() == [("entity:data_minimization", ["entity:minimisation", "entity:sobriété_des_données"])]
    assert (bilan["alias_groupes"], bilan["alias_fondus"], bilan["alias_refuses"]) == (1, 2, 0)
    noms = [n for n, _ in c.appels]
    assert noms.index("merge_entities") > max(
        i for i, n in enumerate(noms) if n in ("upsert_entities", "knowledge_delete")
    )


def test_un_versement_d_essai_ne_fond_rien(depot, monkeypatch, capsys):
    table(depot, TABLE)
    c = Cerveau()
    serveur(monkeypatch, c)
    monkeypatch.setattr(sys, "argv", ["puits", "--graphe", "g", "--jeton", "j", "--limite", "1"])
    assert pc.main() == 0
    assert c.fusions() == [] and "alias_fondus" not in json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# --- la construction


@pytest.mark.parametrize(
    "mode, versement, attendu",
    [
        ("complet", {}, True),
        ("en_place", {"inchanges": 9}, False),
        ("en_place", {"remplaces": 1}, True),
        ("en_place", {"ajoutes": 1}, True),
        ("en_place", {"retires": 1}, True),
        ("en_place", {"alias_fondus": 1}, True),  # une fusion retire des nœuds de l'index des vecteurs
        ("en_place", {"alias_refuses": 1}, False),
    ],
)
def test_un_cerveau_change_quand_un_document_ou_une_fusion_l_a_touche(mode, versement, attendu):
    assert cc.a_change(mode, versement) is attendu


def test_le_bilan_de_la_construction_porte_les_comptes_du_versement():
    versement = {"inchanges": 5, "remplaces": 4, "ajoutes": 3, "retires": 2, "alias_fondus": 1, "verses": 9}
    assert cc.comptes_du_versement(versement) == {
        "inchanges": 5,
        "migres": 0,
        "remplaces": 4,
        "ajoutes": 3,
        "retires": 2,
        "alias_fondus": 1,
        "alias_refuses": 0,
    }

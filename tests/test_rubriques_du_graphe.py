"""Les rubriques tirées du graphe (mainteneur, 15.11.2026 : « rubriques à adapter dynamiquement, maximum 7 »).

Un graphe qui déclare `rubriques: {du_graphe: true, max: 7}` n'a pas de table de rubriques tenue à la main : ses sujets
déclarés, que les thèmes émergents font bouger, sont regroupés en `max` rubriques au plus par un appel au modèle, refait
seulement quand les sujets ou la borne changent. Le résultat vit dans `rubriques.json` du graphe, sous la forme que
lisent déjà le fil et la lettre."""

from __future__ import annotations

import json

import pytest

from newskit import config_sources as cfg
from newskit import fil
from newskit import rubriques_du_graphe as rdg

SUJETS = ["mémoire d'agent", "agents de codage", "RAG et GraphRAG", "évaluations et vérificateurs", "quantification"]


def reponse(*groupes):
    """Une réponse du modèle : (nom, en, mots, indices des sujets) par rubrique."""
    return {"rubriques": [{"nom": n, "en": e, "mots": m, "sujets": s} for n, e, m, s in groupes]}


BONNE = reponse(
    ("Agents", "Agents", ["agent", "harness"], [0, 1]),
    ("Recherche", "Retrieval", ["rag", "retrieval"], [2]),
    ("Évaluation", "Evaluation", ["benchmark"], [3]),
    ("Modèles", "Models", ["quantization"], [4]),
)


def modele(rep, appels=None):
    def appeler(consigne, schema):
        if appels is not None:
            appels.append((consigne, schema))
        return rep

    return appeler


# --- la validation de la réponse


def test_une_reponse_valide_rend_la_forme_des_rubriques_declarees():
    rubs = rdg.valider(BONNE, SUJETS, 7)
    assert [r["nom"] for r in rubs] == ["Agents", "Recherche", "Évaluation", "Modèles"]
    assert rubs[2]["cle"] == "evaluation" and rubs[1]["en"] == "Retrieval"
    assert [x["fr"] for x in rubs[0]["sujets"]] == ["mémoire d'agent", "agents de codage"]
    # le vocabulaire de la rubrique se lit par son premier sujet (lettre.rubrique_de_fiche lit `mots` par sujet)
    assert rubs[0]["sujets"][0]["mots"] == ["agent", "harness"] and rubs[0]["sujets"][1]["mots"] == []
    assert all(set(x) == {"fr", "en", "mots"} for r in rubs for x in r["sujets"])


@pytest.mark.parametrize(
    "rep, motif",
    [
        (None, "illisible"),
        ({"rubriques": []}, "aucune"),
        (reponse(*[(f"R{i}", f"R{i}", [], [i]) for i in range(5)]), "plus de 4"),
        (reponse(("Agents", "Agents", [], [0, 1, 2]), ("Agents", "Other", [], [3, 4])), "deux fois"),
        (reponse(("", "Agents", [], [0, 1, 2, 3, 4])), "sans nom"),
        (reponse(("Agents", "", [], [0, 1, 2, 3, 4])), "sans nom"),
        (reponse(("Un nom de rubrique beaucoup trop long", "Agents", [], [0, 1, 2, 3, 4])), "trop long"),
        (reponse(("Agents", "Agents", [], [0, 1, 2, 3, 9])), "inconnu"),
        (reponse(("Agents", "Agents", [], [0, 1, 2]), ("Autres", "Other", [], [2, 3, 4])), "rangé deux fois"),
        (reponse(("Agents", "Agents", [], [0, 1, 2, 3])), "sans rubrique"),
        (reponse(("Agents", "Agents", [], [0, 1, 2, 3, 4]), ("Vide", "Empty", [], [])), "sans sujet"),
        (reponse(("Agents", "Agents", "agent", [0, 1, 2, 3, 4])), "illisible"),
    ],
)
def test_une_reponse_hors_contrat_est_refusee(rep, motif):
    with pytest.raises(ValueError, match=motif):
        rdg.valider(rep, SUJETS, 4)


# --- le calcul, gardé avec son empreinte


def test_le_calcul_ecrit_les_rubriques_et_ne_rappelle_pas_le_modele_sans_changement(tmp_path):
    p, appels = tmp_path / "g" / "rubriques.json", []
    assert rdg.calculer(SUJETS, 7, modele(BONNE, appels), p) is True
    assert [r["nom"] for r in rdg.lire(p)] == ["Agents", "Recherche", "Évaluation", "Modèles"]
    assert len(appels) == 1 and all(s in appels[0][0] for s in SUJETS) and "7" in appels[0][0]
    assert rdg.calculer(SUJETS, 7, modele(BONNE, appels), p) is False
    assert len(appels) == 1


def test_un_sujet_neuf_ou_une_borne_changee_refait_le_calcul(tmp_path):
    p, appels = tmp_path / "rubriques.json", []
    rdg.calculer(SUJETS, 7, modele(BONNE, appels), p)
    autre = reponse(("Tout", "All", [], [0, 1, 2, 3, 4, 5]))
    assert rdg.calculer([*SUJETS, "inférence locale"], 7, modele(autre, appels), p) is True
    assert [r["nom"] for r in rdg.lire(p)] == ["Tout"]
    assert rdg.calculer([*SUJETS, "inférence locale"], 3, modele(autre, appels), p) is True
    assert len(appels) == 3


def test_l_ordre_des_sujets_ne_change_pas_l_empreinte():
    assert rdg.empreinte(SUJETS, 7) == rdg.empreinte(list(reversed(SUJETS)), 7)
    assert rdg.empreinte(SUJETS, 7) != rdg.empreinte(SUJETS, 6)


@pytest.mark.parametrize("mauvaise", [None, {"rubriques": []}, reponse(("Agents", "Agents", [], [0]))])
def test_une_reponse_refusee_garde_les_rubriques_d_avant(tmp_path, mauvaise):
    p = tmp_path / "rubriques.json"
    rdg.calculer(SUJETS, 7, modele(BONNE), p)
    avant = p.read_text(encoding="utf-8")
    assert rdg.calculer([*SUJETS, "inférence locale"], 7, modele(mauvaise), p) is False
    assert p.read_text(encoding="utf-8") == avant


def test_sans_sujet_declare_aucun_appel(tmp_path):
    appels = []
    assert rdg.calculer([], 7, modele(BONNE, appels), tmp_path / "rubriques.json") is False
    assert appels == [] and rdg.lire(tmp_path / "rubriques.json") == []


@pytest.mark.parametrize("contenu", [None, "{pas du json", "[]", '{"rubriques": "x"}'])
def test_un_fichier_absent_ou_illisible_se_lit_sans_rubrique(tmp_path, contenu):
    p = tmp_path / "rubriques.json"
    if contenu is not None:
        p.write_text(contenu, encoding="utf-8")
    assert rdg.lire(p) == []


# --- le registre


def test_la_declaration_du_graphe_est_validee():
    assert rdg.declaration({"du_graphe": True, "max": 7}) == 7
    assert rdg.declaration({"du_graphe": True}) == rdg.MAX == 7
    assert rdg.declaration({"fichier": "t.yaml", "ordre": []}) is None
    assert rdg.declaration(None) is None
    for mauvaise in (
        {"du_graphe": False},
        {"du_graphe": "oui"},
        {"du_graphe": True, "max": 0},
        {"du_graphe": True, "max": "7"},
        {"du_graphe": True, "max": True},
        {"du_graphe": True, "max": rdg.MAX_ADMIS + 1},
        {"du_graphe": True, "ordre": []},
    ):
        with pytest.raises(ValueError):
            rdg.declaration(mauvaise)


def test_le_registre_rend_les_rubriques_calculees_du_graphe(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True, "max": 7}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    assert cfg.rubriques("g") == []
    rdg.calculer(SUJETS, 7, modele(BONNE), tmp_path / "rubriques.json")
    assert [r["nom"] for r in cfg.rubriques("g")] == ["Agents", "Recherche", "Évaluation", "Modèles"]


def test_le_registre_refuse_une_declaration_mal_formee(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True, "max": 0}})
    with pytest.raises(cfg.ConfigInvalide, match="graphes.g.rubriques"):
        cfg.rubriques("g")


# --- le nom anglais d'une rubrique


def test_le_fil_anglais_prend_le_nom_anglais_de_la_rubrique_calculee(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    rdg.calculer(SUJETS, 7, modele(BONNE), tmp_path / "rubriques.json")
    assert fil.rubrique_anglaise("Recherche", "g") == "Retrieval"
    assert fil.rubrique_anglaise("Conformité", "g") == "Compliance"  # la table du moteur reste lue
    assert fil.rubrique_anglaise("Inconnue", "g") == "Inconnue"
    assert fil.rubrique_anglaise("Recherche") == "Recherche"  # sans graphe : la table seule
    assert fil.rubrique_anglaise(None, "g") is None


def test_le_flux_anglais_porte_le_nom_anglais_de_la_rubrique_calculee(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True}, "libelle": "G"})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    rdg.calculer(SUJETS, 7, modele(BONNE), tmp_path / "rubriques.json")
    billet = {
        "titre": "Un titre",
        "descriptif": "Un texte.",
        "rubrique": "Recherche",
        "ajoute": "2026-11-15 09:00",
        "en": {"tenu": True, "titre": "A title", "texte": "A text."},
    }
    flux = fil.rss({"billets": [billet]}, "https://exemple.org", "en", "g")
    assert "<category>Retrieval</category>" in flux
    assert "<category>Recherche</category>" in fil.rss({"billets": [billet]}, "https://exemple.org", "fr", "g")


# --- la commande


def test_la_commande_calcule_pour_un_graphe_qui_le_declare(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True, "max": 4}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    monkeypatch.setattr(rdg, "sujets_declares", lambda g: SUJETS)
    vus = []
    monkeypatch.setattr(rdg, "appeler_modele", lambda g: modele(BONNE, vus))
    assert rdg.main(["--graphe", "g"]) == 0
    assert "4 rubrique(s)" in capsys.readouterr().out and "4" in vus[0][0]
    assert rdg.main(["--graphe", "g"]) == 0
    assert "inchangées" in capsys.readouterr().out and len(vus) == 1
    assert json.loads((tmp_path / "rubriques.json").read_text(encoding="utf-8"))["max"] == 4


def test_la_commande_ne_fait_rien_pour_un_graphe_aux_rubriques_declarees(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    monkeypatch.setattr(rdg, "appeler_modele", lambda g: pytest.fail("aucun appel attendu"))
    assert rdg.main(["--graphe", "g"]) == 0
    assert "pas de rubriques tirées du graphe" in capsys.readouterr().out


# --- la routine


def test_la_routine_lance_l_etape_pour_un_graphe_qui_tire_ses_rubriques_du_graphe(monkeypatch):
    from newskit import routine_hebdo as rh

    lances = []
    monkeypatch.setattr(rh, "lancer", lambda j, libelle, args, **k: lances.append((libelle, args, k)) or (True, ""))
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True}})
    assert rh.rubriques_du_graphe(None, "g", " (g)") is True
    assert lances == [
        ("rubriques du graphe (g)", ["scripts/rubriques_du_graphe.py", "--graphe", "g"], {"garder": 1, "modele": True})
    ]
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"fichier": "t.yaml", "ordre": []}})
    assert rh.rubriques_du_graphe(None, "g") is False
    assert len(lances) == 1


def test_la_lettre_anglaise_prend_le_nom_anglais_de_la_rubrique_calculee(tmp_path, monkeypatch):
    from newskit import lettre_anglaise as la

    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"rubriques": {"du_graphe": True}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": tmp_path})
    rdg.calculer(SUJETS, 7, modele(BONNE), tmp_path / "rubriques.json")
    monkeypatch.setattr(la, "_du_fil", lambda g: {"a.md": {"tenu": True, "titre": "A title", "texte": "A text."}})
    L = {
        "graphe": "g",
        "faits": [{"chemin": "a.md", "titre": "Un titre", "paragraphe": "Un texte.", "rubrique": "Recherche"}],
        "aussi": {"Recherche": [{"chemin": "a.md", "titre": "Un titre", "descriptif": "Un texte."}]},
        "bref": {},
        "ordre": ["Recherche"],
    }
    E = la.ecrire(L)
    assert E["faits"][0]["rubrique"] == "Retrieval" and E["faits"][0]["titre"] == "A title"
    assert list(E["aussi"]) == ["Retrieval"] and E["ordre"] == ["Retrieval"]


def test_le_chargement_du_registre_valide_la_declaration():
    import copy

    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    d["graphes"][g]["rubriques"] = {"du_graphe": True, "max": 7}
    assert cfg._valider(d, cfg.FICHIER)
    d["graphes"][g]["rubriques"] = {"du_graphe": True, "max": 0}
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.rubriques : max"):
        cfg._valider(d, cfg.FICHIER)
    d["graphes"][g]["rubriques"] = {"fichier": "absent.yaml", "ordre": []}
    with pytest.raises(cfg.ConfigInvalide, match="illisible"):
        cfg._valider(d, cfg.FICHIER)


def test_les_sujets_et_le_modele_viennent_du_moteur(monkeypatch):
    from newskit import media
    from newskit import themes_emergents as te

    monkeypatch.setattr(te, "sujets_declares", lambda g: [f"sujet de {g}"])
    assert rdg.sujets_declares("g") == ["sujet de g"]
    vus = {}
    monkeypatch.setattr(te, "appeler_claude", lambda consigne, **k: vus.update(k, consigne=consigne) or BONNE)
    assert rdg.appeler_modele("g")("la consigne", rdg.SCHEMA) is BONNE
    assert vus == {
        "consigne": "la consigne",
        "schema": rdg.SCHEMA,
        "modele": media.modele("analyse"),
        "poste": "rubriques",
        "graphe": "g",
    }

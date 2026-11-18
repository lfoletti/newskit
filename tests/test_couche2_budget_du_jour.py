"""La couche 2 d'une liste de fiches, sous un budget de fiches par jour.

Le passage continu extrait les concepts d'une fiche quelques minutes après sa collecte ; la nuit extrait le retard. Les
deux partagent un budget de fiches par jour (`couche2_continu_jour` au registre) : le compte du jour se lit au registre
des coûts, les fiches du fil prennent ce qui reste du jour, les autres ne dépassent pas la part du jour écoulée, et la
nuit retranche de son plafond ce que le jour a déjà extrait."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime

import pytest

from newskit import config_sources as cfg
from newskit import couts
from newskit import extraire_concepts as ec


@pytest.fixture
def atelier(tmp_path, monkeypatch):
    """Cinq fiches en retard et un modèle qui répond toujours ; rend (fiches, lots, appels)."""
    fiches = []
    for i in range(5):
        f = tmp_path / "sources" / f"f{i}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"fiche {i}", encoding="utf-8")
        fiches.append((f, f"h{i}"))
    lots = tmp_path / "graphe" / "couche2" / "fiches"
    appels = []
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "LOTS", lots)
    monkeypatch.setattr(ec, "EMPREINTES", tmp_path / "graphe" / "couche2" / "empreintes.json")
    monkeypatch.setattr(ec, "GRAPHE", None)
    monkeypatch.setattr(ec, "DOMAINE", "")
    monkeypatch.setattr(ec, "PARALLELE", 1)
    monkeypatch.setattr(ec, "fiches_en_retard", lambda: fiches)
    monkeypatch.setattr(ec, "prompt_de", lambda f: ({"titre": f.stem}, f.stem))
    monkeypatch.setattr(
        ec, "appeler_claude", lambda invite: appels.append(invite) or ({"nodes": [{"id": "n"}], "edges": []}, "")
    )
    monkeypatch.setattr(ec, "normaliser_extraction", lambda ext, f, champs: (ext, []))
    monkeypatch.setattr(ec, "valider", lambda ext: [])
    monkeypatch.setattr(ec, "identifiant_de_fiche", lambda f, champs=None: f.stem)
    monkeypatch.setattr(couts, "REGISTRE", tmp_path / "cache" / "couts.jsonl")
    return fiches, lots, appels


# --- l'extraction d'une liste


def test_extraire_ne_traite_que_la_liste_donnee(atelier):
    fiches, lots, appels = atelier
    assert ec.extraire(fiches[1:3]) == {"faites": 2, "echecs": 0, "limites": 0}
    assert sorted(appels) == ["f1", "f2"]
    assert sorted(p.name for p in lots.glob("*.json")) == ["f1.json", "f2.json"]
    assert json.loads(ec.EMPREINTES.read_text(encoding="utf-8")) == {"sources/f1.md": "h1", "sources/f2.md": "h2"}


def test_extraire_compte_les_echecs_et_la_limite(atelier, monkeypatch):
    fiches, lots, _ = atelier
    reponses = {"f0": (None, "erreur du modèle"), "f1": (None, ec.LIMITE + " atteinte")}
    monkeypatch.setattr(ec, "appeler_claude", lambda i: reponses.get(i, ({"nodes": [{"id": "n"}], "edges": []}, "")))
    monkeypatch.setattr(ec, "est_un_refus", lambda *a: False)
    assert ec.extraire(fiches[:3]) == {"faites": 1, "echecs": 2, "limites": 1}
    assert [p.name for p in lots.glob("*.json")] == ["f2.json"]


def test_extraire_une_liste_vide_n_appelle_rien(atelier):
    _, lots, appels = atelier
    assert ec.extraire([]) == {"faites": 0, "echecs": 0, "limites": 0}
    assert appels == []


def test_viser_pose_le_graphe_et_ses_chemins(atelier, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"couche2": tmp_path / g / "couche2"})
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"domaine": f"domaine de {g}"})
    decl = ec.viser("ruchers")
    assert decl == {"domaine": "domaine de ruchers"}
    assert (ec.GRAPHE, ec.DOMAINE) == ("ruchers", "domaine de ruchers")
    assert ec.EMPREINTES == tmp_path / "ruchers" / "couche2" / "empreintes.json"
    assert ec.LOTS == tmp_path / "ruchers" / "couche2" / "fiches"


@pytest.mark.parametrize("nom", [None, cfg.GRAPHE_PRINCIPAL])
def test_viser_le_graphe_principal_garde_ses_chemins(atelier, monkeypatch, nom):
    _, lots, _ = atelier
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"domaine": f"domaine de {g}"})
    ec.viser(nom)
    assert ec.GRAPHE is None and ec.LOTS == lots
    assert ec.DOMAINE == f"domaine de {cfg.GRAPHE_PRINCIPAL}"


# --- le compte du jour


def ligne(quand, poste="extraction", graphe="g", ok=True):
    return json.dumps({"quand": quand, "poste": poste, "graphe": graphe, "ok": ok})


def test_le_compte_du_jour_se_lit_au_registre_des_couts(atelier):
    couts.REGISTRE.parent.mkdir(parents=True)
    couts.REGISTRE.write_text(
        "\n".join(
            [
                ligne("2026-11-17 23:59:59"),  # la veille
                ligne("2026-11-18 00:00:00"),
                ligne("2026-11-18 12:00:00"),
                ligne("2026-11-18 23:59:59"),
                ligne("2026-11-18 12:00:00", poste="synthese"),  # un autre poste
                ligne("2026-11-18 12:00:00", graphe="autre"),  # un autre graphe
                ligne("2026-11-18 12:00:00", graphe="principal"),
                ligne("2026-11-19 00:00:00"),  # le lendemain
            ]
        ),
        encoding="utf-8",
    )
    assert ec.faites_du_jour("g", "2026-11-18") == 3
    assert ec.faites_du_jour(None, "2026-11-18") == 1  # le graphe principal s'écrit « principal »
    assert ec.faites_du_jour("g", "2026-11-20") == 0


def test_un_appel_en_echec_ne_compte_pas(atelier):
    couts.REGISTRE.parent.mkdir(parents=True)
    couts.REGISTRE.write_text(
        ligne("2026-11-18 10:00:00") + "\n" + ligne("2026-11-18 10:01:00", ok=False), encoding="utf-8"
    )
    assert ec.faites_du_jour("g", "2026-11-18") == 1


# --- le quota d'un passage


def a(heure: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 18, heure, minute)


def test_le_fil_prend_ce_qui_reste_du_jour():
    assert ec.quota(200, 0, a(0, 1), 12) == (12, 0)  # dès minuit, sans attendre la part écoulée
    assert ec.quota(200, 190, a(8), 30) == (10, 0)
    assert ec.quota(200, 100, a(23), 4) == (4, 87)


def test_les_autres_suivent_la_part_du_jour_ecoulee():
    assert ec.quota(200, 0, a(6), 0) == (0, 50)
    assert ec.quota(200, 30, a(6), 0) == (0, 20)
    assert ec.quota(200, 30, a(6), 5) == (5, 15)  # le fil compte dans la part écoulée
    assert ec.quota(200, 60, a(6), 0) == (0, 0)  # en avance sur la journée : rien
    assert ec.quota(200, 60, a(6), 3) == (3, 0)
    assert ec.quota(200, 0, a(23, 59), 0) == (0, 199)


def test_budget_epuise_rien():
    assert ec.quota(200, 200, a(12), 9) == (0, 0)
    assert ec.quota(200, 240, a(23), 9) == (0, 0)
    assert ec.quota(0, 0, a(23), 9) == (0, 0)


# --- la nuit


def nuit(monkeypatch, decl: dict, faites: int, *argv) -> list:
    monkeypatch.setattr(cfg, "graphe_declare", lambda nom: {"domaine": "d", "stock_couche2": 0, **decl})
    vus = []
    monkeypatch.setattr(ec, "faites_du_jour", lambda g, jour: vus.append((g, jour)) or faites)
    monkeypatch.setattr(sys, "argv", ["extraire_concepts", *argv])
    assert ec.main() == 0
    return vus


def test_la_nuit_retranche_les_extractions_du_jour(atelier, monkeypatch):
    _, lots, appels = atelier
    vus = nuit(monkeypatch, {"plafond_couche2": 4, "couche2_continu_jour": 10}, 8)
    assert len(appels) == 2  # 10 par jour, 8 faites : il en reste 2, sous le plafond de 4
    assert vus == [(None, datetime.now().strftime("%Y-%m-%d"))]


def test_la_nuit_garde_son_plafond_sous_le_budget(atelier, monkeypatch):
    _, _, appels = atelier
    nuit(monkeypatch, {"plafond_couche2": 4, "couche2_continu_jour": 10}, 1)
    assert len(appels) == 4


def test_la_nuit_n_extrait_rien_quand_le_budget_est_epuise(atelier, monkeypatch):
    _, _, appels = atelier
    nuit(monkeypatch, {"plafond_couche2": 4, "couche2_continu_jour": 10}, 12)
    assert appels == []


def test_sans_budget_du_jour_la_nuit_ne_change_pas(atelier, monkeypatch):
    _, _, appels = atelier
    assert nuit(monkeypatch, {"plafond_couche2": 4}, 99) == []
    assert len(appels) == 4


def test_un_plafond_donne_a_la_main_passe_outre(atelier, monkeypatch):
    _, _, appels = atelier
    assert nuit(monkeypatch, {"plafond_couche2": 4, "couche2_continu_jour": 10}, 10, "--plafond", "5") == []
    assert len(appels) == 5


def test_la_commande_garde_ses_options(atelier, monkeypatch, capsys):
    """La boucle sortie de la commande, ses options restent : un motif, l'état sans appel, un retard vide."""
    fiches, _, appels = atelier
    nuit(monkeypatch, {}, 0, "--seulement", "f[13]", "--plafond", "5")
    assert sorted(appels) == ["f1", "f3"]
    del appels[:]
    nuit(monkeypatch, {}, 0, "--etat")
    assert appels == [] and "    - sources/f0.md" in capsys.readouterr().out
    monkeypatch.setattr(ec, "fiches_en_retard", lambda: [])
    nuit(monkeypatch, {}, 0)
    assert appels == [] and "couche 2 à jour" in capsys.readouterr().out


# --- le registre


def registre(valeur) -> tuple[dict, str]:
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    d["graphes"][g]["couche2_continu_jour"] = valeur
    return d, g


@pytest.mark.parametrize("v", [0, 1, 200])
def test_le_registre_admet_couche2_continu_jour(v):
    d, _ = registre(v)
    assert cfg._valider(d, cfg.FICHIER)


@pytest.mark.parametrize("v", [-1, 1.5, "200", True, None])
def test_couche2_continu_jour_est_un_entier_positif(v):
    d, g = registre(v)
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.couche2_continu_jour : entier positif ou nul"):
        cfg._valider(d, cfg.FICHIER)

"""`synthese.construire` (22.10.2026, naissance de crooners) : 20 synthèses payées 0,61 $ perdues, parce que construire ne
passait pas `ecrire` à rediger_notions et écrivait ensuite son résultat, qui porte un ensemble (`repondues`)."""
import json

import pytest

from newskit import synthese as sy

UNE = {"label": "Frank Sinatra", "degre": 9, "phrases": [{"texte": "Il chante.", "refs": []}], "empreinte": "e"}


@pytest.fixture
def pyramide(tmp_path, monkeypatch):
    monkeypatch.setattr(sy, "charger_graphe", lambda g: object())
    monkeypatch.setattr(sy, "choisir_notions", lambda G, n: ["concept_sinatra"])
    monkeypatch.setattr(sy, "dossier", lambda g: tmp_path)
    monkeypatch.setattr(sy, "rediger_themes", lambda g, G, notions, modele, combien=40: {"themes": {}, "phrases": 0, "retirees": 0})
    return tmp_path


def test_une_synthese_redigee_est_ecrite_avant_la_fin(pyramide, monkeypatch):
    def tue_apres_un_lot(graphe, G, ids, modele, ecrire=None):
        if ecrire:
            ecrire({"concept_sinatra": UNE}, {"concept_sinatra"})
        raise KeyboardInterrupt("processus tué")
    monkeypatch.setattr(sy, "rediger_notions", tue_apres_un_lot)
    with pytest.raises(KeyboardInterrupt):
        sy.construire("crooners", 20, "m")
    assert json.loads((pyramide / "notions.json").read_text())["notions"] == {"concept_sinatra": UNE}


def test_le_resultat_complet_s_ecrit_malgre_l_ensemble_des_repondues(pyramide, monkeypatch):
    monkeypatch.setattr(sy, "rediger_notions", lambda graphe, G, ids, modele, ecrire=None: {
        "notions": {"concept_sinatra": UNE}, "phrases": 1, "retirees": 0, "repondues": {"concept_sinatra"}, "non_tentes": 0})
    sy.construire("crooners", 20, "m")
    n = json.loads((pyramide / "notions.json").read_text())
    assert n == {"notions": {"concept_sinatra": UNE}, "phrases": 1, "retirees": 0}
    assert json.loads((pyramide / "themes.json").read_text())["themes"] == {}


def test_avec_ajouter_les_anciennes_synthese_restent_et_la_nouvelle_s_ecrit_avant_la_fin(pyramide, monkeypatch):
    ANCIENNE = {**UNE, "label": "Bing Crosby"}
    (pyramide / "notions.json").write_text(json.dumps({"notions": {"concept_crosby": ANCIENNE}, "phrases": 1, "retirees": 0}))
    monkeypatch.setattr(sy, "notions_suivantes", lambda G, deja, n: ["concept_sinatra"])
    def tue_apres_un_lot(graphe, G, ids, modele, ecrire=None):
        if ecrire:
            ecrire({"concept_sinatra": UNE}, {"concept_sinatra"})
        raise KeyboardInterrupt
    monkeypatch.setattr(sy, "rediger_notions", tue_apres_un_lot)
    with pytest.raises(KeyboardInterrupt):
        sy.construire("crooners", 20, "m", ajouter=1)
    assert set(json.loads((pyramide / "notions.json").read_text())["notions"]) == {"concept_crosby", "concept_sinatra"}

"""Le fil continu (mainteneur, 11.11.2026 : « je ne veux plus lire "tel plafond a été atteint, c'est pourquoi plus rien
ne paraît depuis 2 heures" » ; plafond dur 100 $, un billet au moins toutes les 30 minutes de 06 h à 23 h).

Le 11.11.2026, le plafond des fiches est atteint à 09:40 : les billets rédigés attendent leur fiche dans la pile ; rien
ne paraît jusqu'à minuit. La parution ne dépend plus d'une étape payante ; le budget du jour sert d'abord la rédaction ;
la pile garde une réserve contre les heures sans nouvelle ; les alertes partent par paliers.
"""

from __future__ import annotations

import json
import math
import time

import pytest

from newskit import alertes_fil as af
from newskit import fil
from newskit import fil_fiche as ff

JOUR = "2026-11-11"


def _b(u, ajoute=f"{JOUR} 09:41", date=JOUR, **k):
    return {
        "titre": "T " + u,
        "descriptif": "D",
        "url": u,
        "chemin": f"sources/{u}.md",
        "date": date,
        "ajoute": ajoute,
        "langue": "en",
        "tenu": True,
        "rapproche": True,
        "source": {},
        **k,
    }


@pytest.fixture
def fiches(monkeypatch):
    monkeypatch.setattr(ff, "arbre", lambda g: {"x": "y"})
    monkeypatch.setattr(ff, "existe", lambda c: True)
    monkeypatch.setattr(ff.cfg, "graphe_declare", lambda g: {"fiche_usd_jour": 25, "fil_usd_jour": 100})
    monkeypatch.setattr(fil.couts, "en_limite", lambda: False)


# 1. La parution ne dépend jamais d'une étape payante


def test_un_billet_attend_sa_fiche_vingt_minutes_au_plus(fiches):
    F = {"pile": [_b("a")], "billets": []}
    assert ff.en_attente(F, "g", f"{JOUR} 09:55") == F["pile"]
    assert ff.en_attente(F, "g", f"{JOUR} 10:01") == []  # 20 minutes : il paraît sans sa fiche


def test_un_billet_sans_heure_de_redaction_n_attend_pas(fiches):
    assert ff.en_attente({"pile": [{**_b("a"), "ajoute": None}], "billets": []}, "g", f"{JOUR} 09:45") == []
    assert ff.en_attente({"pile": [{**_b("a"), "ajoute": "?"}], "billets": []}, "g", f"{JOUR} 09:45") == []


def test_sans_budget_de_fiche_le_billet_n_attend_pas(fiches):
    F = {"pile": [_b("a")], "billets": [], "depense_fiche": {JOUR: 25.0}}
    assert ff.en_attente(F, "g", f"{JOUR} 09:45") == []


def test_au_dela_de_la_part_secondaire_le_billet_n_attend_pas(fiches):
    F = {"pile": [_b("a")], "billets": [], "depense": {JOUR: 80.0}}
    assert ff.en_attente(F, "g", f"{JOUR} 09:45") == []
    F["depense"][JOUR] = 79.0
    assert ff.en_attente(F, "g", f"{JOUR} 09:45") == F["pile"]


def test_a_la_limite_d_usage_le_billet_n_attend_pas(fiches, monkeypatch):
    monkeypatch.setattr(fil.couts, "en_limite", lambda: True)
    assert ff.en_attente({"pile": [_b("a")], "billets": []}, "g", f"{JOUR} 09:45") == []


def test_paraitre_donne_l_heure_a_l_attente_de_la_fiche(fiches, monkeypatch):
    monkeypatch.setattr(fil, "maintenant", lambda: f"{JOUR} 10:05")
    monkeypatch.setattr(fil, "cadence_du_moment", lambda g, n, q: 10)
    F = {"pile": [_b("a")], "billets": [], "paru_le": f"{JOUR} 09:10"}
    assert fil.paraitre("g", F) == 1 and F["pile"] == []


# 2. Le budget du jour sert d'abord la rédaction


def test_le_budget_secondaire_s_arrete_a_quatre_vingts_pour_cent(monkeypatch):
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"fil_usd_jour": 100})
    assert fil.budget_secondaire({"depense": {JOUR: 50.0}}, "g", JOUR) == pytest.approx(30.0)
    assert fil.budget_secondaire({"depense": {JOUR: 85.0}}, "g", JOUR) == 0.0


def _passer(monkeypatch, depense):
    jour = time.strftime("%Y-%m-%d")
    F = {
        "billets": [_b("a", ajoute=f"{jour} 08:00")],
        "pile": [],
        "vues": [],
        "paru_le": f"{jour} 08:00",
        "depense": {jour: depense},
    }
    budgets = {}
    monkeypatch.setattr(fil, "poser_langues", lambda b: 0)
    monkeypatch.setattr(fil, "poser_versions", lambda b: 0)
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil, "lire", lambda g: F)
    monkeypatch.setattr(fil, "ecrire", lambda g, f: None)
    monkeypatch.setattr(fil, "maintenant", lambda: f"{jour} 10:00")
    monkeypatch.setattr(fil, "cadence_du_moment", lambda g, n, q: 10)
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"fil_usd_jour": 100})
    monkeypatch.setattr(fil, "candidates", lambda g, vues, jour: [{"chemin": "sources/x.md", "url": "x", "titre": "x"}])
    monkeypatch.setattr(fil, "rediger", lambda lot, recents, g: ([], 0.0))
    monkeypatch.setattr(fil, "proposer", lambda *a: None)
    monkeypatch.setattr(fil, "textes_du_registre", lambda F: {})
    monkeypatch.setattr(fil, "relire", lambda billets, g: budgets.__setitem__("relus", len(billets)) or 0.0)
    monkeypatch.setattr(fil, "reviser_titres", lambda b, g: 0.0)
    monkeypatch.setattr(fil, "completer", lambda b, g, reste: 0.0)
    monkeypatch.setattr(fil, "a_relire", lambda F: [F["billets"][0]])
    monkeypatch.setattr(fil, "a_lier", lambda F: [])
    monkeypatch.setattr(fil, "a_definir", lambda F: [])
    monkeypatch.setattr(fil, "billets_anglais_a_ecrire", lambda F: [])
    monkeypatch.setattr(fil, "rapprocher", lambda F, g: budgets.__setitem__("rapprocher", True) or 0.0)
    monkeypatch.setattr(fil.nv, "toutes", lambda F: [{"rapproche": False}])
    monkeypatch.setattr(fil.nv, "rattacher", lambda F, c: 0)
    monkeypatch.setattr(fil, "definir", lambda F, g, reste: budgets.__setitem__("definir", reste) or 0.0)
    monkeypatch.setattr(fil, "lier", lambda F, g: budgets.__setitem__("lier", True) or 0.0)
    monkeypatch.setattr(fil.fil_anglais(), "anglais", lambda F, g, reste: budgets.__setitem__("anglais", reste) or 0.0)
    monkeypatch.setattr(ff, "a_ecrire", lambda F: [])
    monkeypatch.setattr(ff, "etoffer", lambda F, g, reste: budgets.__setitem__("etoffer", reste) or 0.0)
    monkeypatch.setattr(ff, "en_attente", lambda F, g, quand=None: [])
    monkeypatch.setattr(fil.couts, "en_limite", lambda: False)
    fil.passer("g")
    return budgets


def test_sous_quatre_vingts_pour_cent_les_taches_secondaires_tournent(monkeypatch):
    b = _passer(monkeypatch, 50.0)
    assert b["definir"] == b["etoffer"] == pytest.approx(30.0)
    assert b["anglais"] == pytest.approx(50.0)  # l'anglais suit le plafond, comme la rédaction
    assert b["lier"] and b["rapprocher"] and b["relus"] == 1


def test_au_dela_de_quatre_vingts_pour_cent_seule_la_redaction_tourne(monkeypatch):
    b = _passer(monkeypatch, 85.0)
    assert b["definir"] == b["etoffer"] == 0.0
    assert "lier" not in b and "rapprocher" not in b and b["relus"] == 0  # les billets d'avant attendent demain


def test_au_dela_de_quatre_vingts_pour_cent_l_anglais_continue_jusqu_au_plafond(monkeypatch):
    """Mainteneur, 11.11.2026 (option A) : un billet sans version anglaise ne paraît pas (publier_fil.aligne). Au palier
    2, l'anglais continue donc avec la rédaction, jusqu'au plafond du jour."""
    assert _passer(monkeypatch, 85.0)["anglais"] == pytest.approx(15.0)
    assert _passer(monkeypatch, 99.0)["anglais"] == pytest.approx(1.0)


# 3. La réserve de la pile contre les heures sans nouvelle


def test_au_dessus_de_la_reserve_la_cadence_de_la_pile(monkeypatch):
    assert fil.garder(fil.PILE_GARDE + 1, f"{JOUR} 14:00", 10) == 10
    assert fil.garder(fil.PILE_GARDE + 1, f"{JOUR} 02:00", 10) == 10


def test_dans_la_reserve_un_billet_par_demi_heure_le_jour(monkeypatch):
    assert fil.PILE_GARDE == 6 and fil.PILE_GARANTIE == 30
    assert fil.garder(6, f"{JOUR} 06:00", 10) == 30 and fil.garder(1, f"{JOUR} 22:59", 10) == 30


def test_dans_la_reserve_aucun_billet_la_nuit(monkeypatch):
    assert fil.garder(6, f"{JOUR} 23:00", 10) == math.inf and fil.garder(1, f"{JOUR} 05:59", 10) == math.inf


def test_une_cadence_infinie_ne_fait_rien_paraitre():
    F = {"pile": [_b("a")], "billets": []}
    assert fil.liberer(F, f"{JOUR} 23:30", math.inf) == 0 and len(F["pile"]) == 1


def test_la_reserve_couvre_trois_heures_sans_nouvelle():
    F = {"pile": [_b(str(i)) for i in range(fil.PILE_GARDE)], "billets": [], "paru_le": f"{JOUR} 12:00"}
    heures = []
    for m in range(5, 240, 5):
        quand = f"{JOUR} {12 + m // 60:02d}:{m % 60:02d}"
        if fil.liberer(F, quand, fil.garder(len(F["pile"]), quand, fil.delai_de_parution(len(F["pile"])))):
            heures.append(m)
    assert heures == [30, 60, 90, 120, 150, 180]


def test_la_routine_du_graphe_garde_son_etalement(monkeypatch):
    from newskit import routine_hebdo as rh

    monkeypatch.setattr(fil.cfg, "reporter", lambda g: {"fil_routine_heure": "05:00", "fil_routine_minutes": 150})
    monkeypatch.setattr(rh, "debut_du_passage", lambda g: None)
    monkeypatch.setattr(rh, "graphe_en_routine", lambda *a: "g")
    assert fil.cadence_du_moment("g", 6, f"{JOUR} 05:30") == 20  # 120 minutes pour 6 billets, de nuit
    monkeypatch.setattr(rh, "graphe_en_routine", lambda *a: None)
    assert fil.cadence_du_moment("g", 6, f"{JOUR} 05:30") == math.inf
    assert fil.cadence_du_moment("g", 6, f"{JOUR} 14:00") == 30


# 4. Les alertes par paliers


def _F(dernier=f"{JOUR} 09:10", pile=0, depense=50.0):
    return {"billets": [_b("a", ajoute=dernier)], "pile": [_b(str(i)) for i in range(pile)], "depense": {JOUR: depense}}


def _cles(F, quand, **k):
    return {
        (p, c) for p, c, _ in af.constats(F, 100.0, quand, k.get("limite", False), k.get("fil", 0), k.get("pub", 0))
    }


def test_un_fil_qui_avance_n_alerte_pas():
    assert _cles(_F(dernier=f"{JOUR} 09:50", pile=3), f"{JOUR} 10:00") == set()


def test_une_heure_sans_billet_avec_une_pile_est_un_blocage():
    assert _cles(_F(pile=3), f"{JOUR} 10:10") == {(2, "blocage")}
    assert _cles(_F(pile=0), f"{JOUR} 10:10") == set()  # pile vide : pénurie, pas blocage


def test_trois_heures_sans_billet_alertent_quelle_que_soit_la_cause():
    assert _cles(_F(pile=0), f"{JOUR} 12:10") == {(3, "silence")}


def test_la_nuit_le_silence_n_alerte_pas():
    assert _cles(_F(dernier=f"{JOUR} 19:00", pile=3), f"{JOUR} 23:30") == set()


def test_le_budget_alerte_a_quatre_vingts_pour_cent_puis_au_plafond():
    assert _cles(_F(dernier=f"{JOUR} 09:50", depense=79.0), f"{JOUR} 10:00") == set()
    assert _cles(_F(dernier=f"{JOUR} 09:50", depense=80.0), f"{JOUR} 10:00") == {(2, "budget")}
    assert _cles(_F(dernier=f"{JOUR} 09:50", depense=99.95), f"{JOUR} 10:00") == {(3, "plafond")}


def test_la_limite_d_usage_les_echecs_du_passage_et_de_la_publication():
    F, q = _F(dernier=f"{JOUR} 09:50"), f"{JOUR} 10:00"
    assert _cles(F, q, limite=True) == {(3, "limite")}
    assert _cles(F, q, fil=1) == set() and _cles(F, q, fil=2) == {(3, "passage")}
    assert _cles(F, q, pub=2) == set() and _cles(F, q, pub=3) == {(2, "publication")}


@pytest.fixture
def surveillance(monkeypatch, tmp_path):
    p = tmp_path / "fil.json"
    monkeypatch.setattr(af, "chemin_fil", lambda g: p)
    monkeypatch.setattr(af, "ETAT", tmp_path / "alertes_{graphe}.json")
    monkeypatch.setattr(af.fil, "plafond", lambda jour, g: 100.0)
    monkeypatch.setattr(af.couts, "en_limite", lambda: False)
    envois = []
    monkeypatch.setattr(af.nc, "envoyer", lambda o, c, *a, **k: envois.append((o, c)) or 0)

    def ecrire(F):
        p.write_text(json.dumps(F), encoding="utf-8")

    return ecrire, envois, tmp_path


def test_le_palier_deux_part_une_fois_par_jour(surveillance):
    ecrire, envois, _ = surveillance
    ecrire(_F(pile=3))
    af.surveiller("g", 0, "inchangé", f"{JOUR} 10:10")
    af.surveiller("g", 0, "inchangé", f"{JOUR} 10:40")
    assert len(envois) == 1 and envois[0][0].startswith("[fil · palier 2] g : ")
    assert "3 billet(s) en attente" in envois[0][1] and "50.00 $ sur 100.00 $" in envois[0][1]


def test_le_palier_trois_se_repete_toutes_les_deux_heures(surveillance):
    ecrire, envois, _ = surveillance
    ecrire(_F())
    for q in ("12:10", "13:10", "14:05", "14:10"):
        af.surveiller("g", 0, "inchangé", f"{JOUR} {q}")
    assert [o.startswith("[fil · palier 3]") for o, _ in envois] == [True, True]


def test_les_echecs_se_comptent_de_suite(surveillance):
    ecrire, envois, tmp_path = surveillance
    ecrire(_F(dernier=f"{JOUR} 09:58"))
    af.surveiller("g", 1, "inchangé", f"{JOUR} 10:00")
    af.surveiller("g", 0, "échec du dépôt (ssh 255) : x", f"{JOUR} 10:05")  # le passage repart : compteur à zéro
    af.surveiller("g", 1, "échec du dépôt (ssh 255) : x", f"{JOUR} 10:10")
    assert envois == []
    af.surveiller("g", 1, "sans accès au VPS (env)", f"{JOUR} 10:15")
    assert [o.split("] ")[0] for o, _ in envois] == ["[fil · palier 3"]
    assert "publication" in envois[0][1]  # le palier 2 part dans le même courriel


def test_un_envoi_manque_se_retente(surveillance, monkeypatch):
    ecrire, envois, _ = surveillance
    ecrire(_F())
    monkeypatch.setattr(af.nc, "envoyer", lambda o, c, *a, **k: 1)
    af.surveiller("g", 0, "inchangé", f"{JOUR} 12:10")
    monkeypatch.setattr(af.nc, "envoyer", lambda o, c, *a, **k: envois.append(o) or 0)
    af.surveiller("g", 0, "inchangé", f"{JOUR} 12:15")
    assert len(envois) == 1


def test_sans_fil_lisible_rien_ne_part(surveillance):
    _, envois, _ = surveillance
    af.surveiller("g", 0, "inchangé", f"{JOUR} 12:10")
    assert envois == []


def test_chaque_passage_du_fil_est_surveille(monkeypatch, tmp_path):
    import subprocess

    from newskit import collecte_continue as cc

    monkeypatch.setattr(cc.rh, "VERROU", tmp_path / "routine.verrou")
    monkeypatch.setattr(cc.rh, "VERROU_COLLECTE", tmp_path / "collecte.verrou")
    monkeypatch.setattr(cc.rh, "graphe_en_routine", lambda *a: None)
    monkeypatch.setattr(cc.rh.cfg, "graphe_du_site", lambda: "g")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 3, "", ""))
    monkeypatch.setattr(cc.publier_fil, "publier", lambda base: "échec du dépôt (ssh 255) : x")
    monkeypatch.setattr(cc, "inscriptions", lambda: "")
    vus = []
    monkeypatch.setattr(af, "surveiller", lambda g, code, pub: vus.append((g, code, pub)))
    monkeypatch.setattr("sys.argv", ["collecte_continue.py"])
    assert cc.main() == 0 and vus == [("g", 3, "échec du dépôt (ssh 255) : x")]
    monkeypatch.setattr(af, "surveiller", lambda *a: 1 / 0)  # une surveillance en panne n'arrête rien
    assert cc.main() == 0


def _fil_plein(n):
    return {
        "billets": [{"chemin": f"a/{i}.md", "titre": f"t{i}", "date": f"2026-10-{1 + i % 28:02d}"} for i in range(n)]
    }


def test_le_graphe_fixe_la_longueur_du_fil(monkeypatch):
    # Mainteneur, 11.11.2026 : « la variable FIL_MAX ne devrait pas passer par newskit, mais se faire en corrigeant le
    # fichier config » ; « monter la limite d'affichage du fil à 400 ».
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"fil_max": 400})
    assert fil.fil_max("g") == 400
    F = _fil_plein(400)
    F["pile"] = [{"chemin": "neuf.md", "titre": "neuf", "date": "2026-11-11"}]
    monkeypatch.setattr(fil, "maintenant", lambda: f"{JOUR} 10:00")
    monkeypatch.setattr(ff, "en_attente", lambda F, g, q=None: [])
    monkeypatch.setattr(fil, "cadence_du_moment", lambda g, n, q: 1)
    fil.paraitre("g", F)
    assert len(F["billets"]) == 400 and F["billets"][0]["chemin"] == "neuf.md"
    assert len(F["archive"]) == 1


def test_sans_declaration_le_fil_garde_fil_max(monkeypatch):
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {})
    assert fil.fil_max("g") == fil.FIL_MAX == 300


def test_l_arriere_plan_garde_la_longueur_du_graphe():
    F = _fil_plein(400)
    F["pile"] = [{"chemin": "vieux.md", "titre": "vieux", "date": "2026-09-01"}]
    assert fil.arriere_plan(F, f"{JOUR} 10:00", 400) == 1
    assert len(F["billets"]) == 400 and list(F["archive"]) == ["vieux.md"] and F["pile"] == []

"""Les motifs de refus récurrents deviennent des règles de rédaction du média (mainteneur, 17.11.2026 : « les motifs
récurrents ne devraient-ils pas être consignés et portés en consigne lors des premières passes? » ; 18.11.2026 : « il
manque visiblement un mécanisme ajoutant des règles d'écriture pour éviter les motifs récurrents »).

refus_frequents porte en consigne des exemples récents, sur 14 jours, à la première rédaction seulement. Une réécriture
refusée 3 fois de suite pour le même motif (« agents » sans « IA ») ne le voyait pas, et un motif sorti de la fenêtre
revenait. Une passe quotidienne transforme chaque motif récurrent en règle générale, en français et en anglais, gardée
dans le dossier de données du média ; lettre.regles l'ajoute aux règles du média, donc à toutes les rédactions et
réécritures.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from newskit import config_sources as cfg
from newskit import fil
from newskit import lettre as lt
from newskit import refus_frequents as rf
from newskit import regles_apprises as ra

MAINTENANT = datetime(2026, 11, 18, 4, 30)
AGENTS = "« agents » ne dit pas qu'il s'agit d'IA ; on croit à des employés."
SIGLE = "sigle non développé : TIM"
REGLE_AGENTS = {
    "fr": "Écris « agents d'IA », jamais « agents » seul.",
    "en": "Write “AI agents”, never “agents” alone.",
}


@pytest.fixture
def media(tmp_path, monkeypatch):
    """Un graphe sans fichier de règles, son fil dans tmp_path, des reproches et un modèle simulés."""
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"libelle": "IA"})
    monkeypatch.setattr(fil, "chemin_fil", lambda g: tmp_path / "fil.json")
    etat = {"entrees": [], "reponse": {"regles": []}, "invites": [], "courriels": [], "envoi": 0}
    monkeypatch.setattr(rf, "_entrees", lambda g: etat["entrees"])

    def demander(inv, schema, graphe, note, modele=None):
        etat["invites"].append(inv)
        return etat["reponse"], 0.05

    monkeypatch.setattr(fil, "demander", demander)

    def envoyer(objet, corps, **k):
        etat["courriels"].append((objet, corps))
        return etat["envoi"]

    monkeypatch.setattr(ra.nc, "envoyer", envoyer)
    return etat


def _vus(reproche: str, n: int) -> list[tuple[str, str]]:
    return [(f"2026-11-1{i % 7} 10:00", reproche) for i in range(n)]


# Les règles apprises entrent dans les règles du média


def test_sans_fichier_aucune_regle_apprise(media):
    assert ra.texte("g", "fr") == "" and ra.texte("g", "en") == ""


def test_les_regles_apprises_suivent_les_regles_du_media_dans_les_deux_langues(media, tmp_path):
    (tmp_path / "regles_apprises.json").write_text(
        json.dumps({"jour": "2026-11-17", "regles": [{"cle": "x", **REGLE_AGENTS}]}), encoding="utf-8"
    )
    fr, en = lt.regles("g"), lt.regles("g", "en")
    assert fr.startswith(lt.REGLES) and REGLE_AGENTS["fr"] in fr and REGLE_AGENTS["en"] not in fr
    assert REGLE_AGENTS["en"] in en and REGLE_AGENTS["fr"] not in en


def test_une_regle_couverte_reste_retenue_sans_etre_ecrite(media, tmp_path):
    (tmp_path / "regles_apprises.json").write_text(
        json.dumps({"jour": "2026-11-17", "regles": [{"cle": "x", "fr": "", "en": ""}]}), encoding="utf-8"
    )
    assert ra.texte("g", "fr") == ""


def test_un_fichier_illisible_ne_donne_aucune_regle(media, tmp_path):
    (tmp_path / "regles_apprises.json").write_text("{", encoding="utf-8")
    assert ra.texte("g", "fr") == "" and lt.regles("g") == lt.REGLES


def test_sans_graphe_les_regles_restent_celles_du_paquet():
    assert lt.regles(None) == lt.REGLES


# La passe du jour


def test_un_motif_recurrent_devient_une_regle(media, tmp_path):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, **REGLE_AGENTS}]}
    ajoutees = ra.passer("g", MAINTENANT)
    assert [r["fr"] for r in ajoutees] == [REGLE_AGENTS["fr"]]
    lu = json.loads((tmp_path / "regles_apprises.json").read_text(encoding="utf-8"))
    (r,) = lu["regles"]
    assert lu["jour"] == "2026-11-18"
    assert (
        r["cle"] == rf.cle(AGENTS) and r["vus"] == ra.SEUIL and r["exemple"] == AGENTS and r["ajoutee"] == "2026-11-18"
    )
    assert AGENTS in media["invites"][0] and lt.REGLES[:40] in media["invites"][0]


def test_un_motif_sous_le_seuil_ne_devient_pas_une_regle(media):
    media["entrees"] = _vus(AGENTS, ra.SEUIL - 1)
    assert ra.passer("g", MAINTENANT) == [] and media["invites"] == []


def test_un_motif_deja_appris_ne_repasse_pas(media, tmp_path):
    (tmp_path / "regles_apprises.json").write_text(
        json.dumps({"jour": "2026-11-17", "regles": [{"cle": rf.cle(AGENTS), **REGLE_AGENTS}]}), encoding="utf-8"
    )
    media["entrees"] = _vus(AGENTS, 9) + _vus(SIGLE, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, "fr": "Développe un sigle.", "en": "Spell out an acronym."}]}
    ra.passer("g", MAINTENANT)
    assert SIGLE in media["invites"][0] and AGENTS not in media["invites"][0].split("Motifs")[-1]


def test_les_plus_frequents_d_abord_et_pas_plus_que_le_maximum(media):
    media["entrees"] = [
        x for i in range(ra.MAX_PAR_JOUR + 2) for x in _vus(f"motif {chr(97 + i)} : détail", ra.SEUIL + i)
    ]
    ra.passer("g", MAINTENANT)
    motifs = [json.loads(x) for x in media["invites"][0].split("\n") if x.startswith('{"n"')]
    assert len(motifs) == ra.MAX_PAR_JOUR
    assert [m["fois"] for m in motifs] == sorted((m["fois"] for m in motifs), reverse=True)


def test_une_regle_vide_retient_le_motif_couvert(media, tmp_path):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, "fr": "", "en": ""}]}
    assert ra.passer("g", MAINTENANT) == []
    lu = json.loads((tmp_path / "regles_apprises.json").read_text(encoding="utf-8"))
    assert [r["cle"] for r in lu["regles"]] == [rf.cle(AGENTS)] and media["courriels"] == []


def test_une_regle_sans_numero_connu_est_ignoree(media, tmp_path):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 7, **REGLE_AGENTS}]}
    assert ra.passer("g", MAINTENANT) == []
    assert (
        not (tmp_path / "regles_apprises.json").exists()
        or "Écris" not in (tmp_path / "regles_apprises.json").read_text()
    )


def test_une_passe_par_jour(media):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, **REGLE_AGENTS}]}
    ra.passer("g", MAINTENANT)
    media["entrees"] = _vus(SIGLE, ra.SEUIL)
    assert ra.passer("g", MAINTENANT.replace(hour=22)) == [] and len(media["invites"]) == 1


def test_le_mainteneur_recoit_les_regles_ajoutees(media):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, **REGLE_AGENTS}]}
    ra.passer("g", MAINTENANT)
    ((objet, corps),) = media["courriels"]
    assert "g" in objet and "1" in objet
    assert REGLE_AGENTS["fr"] in corps and REGLE_AGENTS["en"] in corps and AGENTS in corps


def test_un_courriel_manque_garde_les_regles(media, tmp_path):
    media["entrees"] = _vus(AGENTS, ra.SEUIL)
    media["reponse"] = {"regles": [{"n": 0, **REGLE_AGENTS}]}
    media["envoi"] = 1
    assert len(ra.passer("g", MAINTENANT)) == 1
    assert REGLE_AGENTS["fr"] in ra.texte("g", "fr")


def test_des_reproches_illisibles_ne_bloquent_pas(media, monkeypatch):
    def illisible(g):
        raise OSError("journal")

    monkeypatch.setattr(rf, "_entrees", illisible)
    assert ra.passer("g", MAINTENANT) == [] and media["invites"] == []


# La passe tourne chaque jour, avant la lettre, sans jamais bloquer le fil


def test_avant_la_lettre_la_passe_des_regles_tourne(monkeypatch, capsys):
    vu = []
    monkeypatch.setattr(ra, "passer", lambda g, maintenant=None: vu.append(g) or [REGLE_AGENTS])
    monkeypatch.setattr(fil, "passer", lambda g, hors_plafond=False: 0)
    fil.lire_le_reste("g")
    assert vu == ["g"] and f"règle apprise : {REGLE_AGENTS['fr']}" in capsys.readouterr().out


def test_une_passe_des_regles_en_echec_ne_bloque_pas_la_lettre(monkeypatch):
    def casse(g, maintenant=None):
        raise RuntimeError("modèle")

    lus = []
    monkeypatch.setattr(ra, "passer", casse)
    monkeypatch.setattr(fil, "passer", lambda g, hors_plafond=False: lus.append(g) or 0)
    assert fil.lire_le_reste("g") == 0 and lus == ["g"]

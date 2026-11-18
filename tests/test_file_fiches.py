"""Qui écrit ou retire une fiche le signale.

Le cerveau d'un graphe continu reçoit ses fiches au fil de la journée. Le passage continu lit une file d'événements,
`<g>.file.jsonl` : fiche parue, modifiée, retirée. Une fiche s'écrit par `fiche.ecrire`, se retire par `fiche.retirer`
et se déplace par `fiche.deplacer` ; ces trois gestes signalent. La file n'existe que pour un graphe continu, sur la
machine où le passage continu tourne ; elle n'empêche jamais l'écriture d'une fiche."""

from __future__ import annotations

import fcntl
import json
import threading
import types

import pytest

from newskit import cerveau_cortexdb as cc
from newskit import collecte_arxiv as ca
from newskit import collecte_dossier_local as cdl
from newskit import config_sources as cfg
from newskit import doublons_d_identite as dd
from newskit import fiche
from newskit import file_fiches as ff
from newskit import naissance as na
from newskit import purger_fiches as pf

GRAPHES = {"g": ["sources/a"], "autre": ["sources/b"], "large": ["sources/a", "sources/b"]}
CHAMPS = {
    "title": "Un titre",
    "subtitle": "",
    "author": "",
    "date": "2026-11-18",
    "url": "https://e.org/1",
    "publication": "p",
    "collected": "2026-11-18",
}
CORPS = "# Un titre\n\nUn corps de fiche assez long pour le contrat.\n"


@pytest.fixture
def racine(tmp_path, monkeypatch):
    """Des données où `g` et `large` sont des graphes continus, le passage continu déjà venu."""
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(fiche, "dossiers_des_graphes", lambda: GRAPHES)
    monkeypatch.setattr(fiche, "noter_contacts", lambda *a: None)
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: g in ("g", "large"))
    monkeypatch.setattr(cc, "DOSSIER", tmp_path / "cerveaux")
    fiche._CARTE.clear()
    fiche.oublier_index()
    ff.ouvrir("g")
    yield tmp_path
    fiche._CARTE.clear()
    fiche.oublier_index()


def evenements(graphe: str = "g") -> list[tuple[str, str]]:
    return [(e["chemin"], e["sorte"]) for e in ff.lire(graphe)]


def poser(racine, rel: str, **champs):
    p = racine / rel
    fiche.ecrire(p, {**CHAMPS, **champs}, CORPS)
    ff.acquitter("g", len(ff.lire("g")))
    return p


# --- l'écriture


def test_une_fiche_neuve_signale_parue(racine):
    fiche.ecrire(racine / "sources/a/x.md", CHAMPS, CORPS)
    (e,) = ff.lire("g")
    assert (e["chemin"], e["sorte"], e["par"]) == ("sources/a/x.md", "parue", "")
    assert e["quand"][:4] == "2026" or len(e["quand"]) == 19


def test_une_fiche_reecrite_avec_un_autre_texte_signale_modifiee(racine):
    p = poser(racine, "sources/a/x.md")
    fiche.ecrire(p, CHAMPS, CORPS + "\nUne phrase de plus.\n")
    assert evenements() == [("sources/a/x.md", "modifiee")]


def test_une_reecriture_identique_ne_signale_rien(racine):
    p = poser(racine, "sources/a/x.md")
    fiche.ecrire(p, CHAMPS, CORPS)
    assert evenements() == []


def test_un_en_tete_enrichi_signale_modifiee(racine):
    """Le fil ajoute son titre et son résumé à l'en-tête, sans toucher au corps : le document versé change."""
    p = poser(racine, "sources/a/x.md")
    champs, corps = fiche.lire_fichier(p)
    fiche.ecrire(p, {**champs, "titre_fr": "Le titre du fil", "resume_fr": "Le résumé du fil."}, corps, strict=False)
    assert evenements() == [("sources/a/x.md", "modifiee")]


def test_une_publicite_non_ecrite_ne_signale_rien(racine, monkeypatch):
    monkeypatch.setattr(fiche, "publicitaire", lambda titre, corps: True)
    assert fiche.ecrire(racine / "sources/a/x.md", CHAMPS, CORPS) == ["publicité : fiche non écrite"]
    assert evenements() == [] and not (racine / "sources/a/x.md").exists()


# --- le retrait et le déplacement


def test_retirer_efface_et_signale_retiree(racine):
    p = poser(racine, "sources/a/x.md")
    fiche.retirer(p, par="un programme")
    assert not p.exists()
    (e,) = ff.lire("g")
    assert (e["chemin"], e["sorte"], e["par"]) == ("sources/a/x.md", "retiree", "un programme")


def test_retirer_une_fiche_absente(racine):
    with pytest.raises(FileNotFoundError):
        fiche.retirer(racine / "sources/a/absente.md")
    fiche.retirer(racine / "sources/a/absente.md", absente_admise=True)
    assert evenements() == []  # rien n'a été retiré : rien à signaler


def test_deplacer_signale_le_depart_et_l_arrivee(racine):
    ff.ouvrir("large")
    p = poser(racine, "sources/a/x.md")
    ff.acquitter("large", len(ff.lire("large")))
    (racine / "sources/b").mkdir(parents=True)
    fiche.deplacer(p, racine / "sources/b/x.md")
    assert not p.exists() and (racine / "sources/b/x.md").exists()
    assert evenements("g") == [("sources/a/x.md", "retiree")]
    assert evenements("large") == [("sources/a/x.md", "retiree"), ("sources/b/x.md", "parue")]


def test_copier_ne_signale_que_l_arrivee(racine):
    ff.ouvrir("large")
    p = poser(racine, "sources/a/x.md")
    ff.acquitter("large", len(ff.lire("large")))
    (racine / "sources/b").mkdir(parents=True)
    fiche.deplacer(p, racine / "sources/b/x.md", copie=True)
    assert p.exists() and (racine / "sources/b/x.md").read_text() == p.read_text()
    assert evenements("g") == [] and evenements("large") == [("sources/b/x.md", "parue")]


# --- où la file existe


def test_sans_graphe_continu_aucune_file(racine, monkeypatch):
    monkeypatch.setattr(cfg, "cerveau_continu", lambda g: False)
    fiche.ecrire(racine / "sources/a/x.md", CHAMPS, CORPS)
    assert not ff.chemin("g").exists() and ff.ouverte("g") is False


def test_sans_passage_continu_sur_la_machine_aucune_file(racine):
    """`large` est continu au registre, mais son passage continu n'a jamais tourné ici : sa file n'existe pas."""
    fiche.ecrire(racine / "sources/a/x.md", CHAMPS, CORPS)
    assert evenements("g") == [("sources/a/x.md", "parue")]
    assert not ff.chemin("large").exists() and ff.ouverte("large") is False
    ff.ouvrir("large")
    assert ff.ouverte("large") is True


def test_une_fiche_hors_des_donnees_ou_d_un_autre_graphe_ne_signale_rien(racine, tmp_path_factory):
    ff.signaler(tmp_path_factory.mktemp("ailleurs") / "x.md", "parue")
    ff.signaler(racine / "sources/b/x.md", "parue")  # lu par « autre », non continu, et par « large », non ouvert
    ff.signaler("sources/a/relatif.md", "retiree")  # un chemin relatif part de la racine des données
    assert evenements() == [("sources/a/relatif.md", "retiree")]


def test_une_file_illisible_n_empeche_pas_l_ecriture_de_la_fiche(racine, capsys):
    ff.chemin("g").mkdir()  # un dossier à la place du fichier : l'ajout échoue
    fiche.ecrire(racine / "sources/a/x.md", CHAMPS, CORPS)
    assert (racine / "sources/a/x.md").exists()
    assert "file des fiches : IsADirectoryError" in capsys.readouterr().err


def test_une_sorte_inconnue_ne_s_ecrit_pas(racine, capsys):
    ff.signaler(racine / "sources/a/x.md", "perdue")
    assert evenements() == [] and "sorte d'événement inconnue : perdue" in capsys.readouterr().err


# --- lire et acquitter


def test_acquitter_ne_perd_pas_un_evenement_arrive_entre_temps(racine):
    for nom in ("x", "y"):
        ff.signaler(racine / f"sources/a/{nom}.md", "parue")
    lus = ff.lire("g")
    ff.signaler(racine / "sources/a/z.md", "parue")
    ff.acquitter("g", len(lus))
    assert evenements() == [("sources/a/z.md", "parue")]
    ff.acquitter("g", 0)
    assert evenements() == [("sources/a/z.md", "parue")]
    ff.acquitter("g", 5)
    assert evenements() == [] and ff.lire("absent") == []


def test_une_ligne_illisible_garde_sa_place(racine):
    ff.signaler(racine / "sources/a/x.md", "parue")
    with open(ff.chemin("g"), "a", encoding="utf-8") as f:
        f.write("pas du JSON\n[1]\n\n")
    ff.signaler(racine / "sources/a/y.md", "parue")
    assert [e.get("chemin") for e in ff.lire("g")] == ["sources/a/x.md", None, None, "sources/a/y.md"]
    ff.acquitter("g", 3)
    assert evenements() == [("sources/a/y.md", "parue")]


@pytest.mark.parametrize("geste", ["signaler", "acquitter"])
def test_un_seul_ecrivain_a_la_fois(racine, geste):
    ff.signaler(racine / "sources/a/x.md", "parue")
    gestes = {
        "signaler": lambda: ff.signaler(racine / "sources/a/y.md", "parue"),
        "acquitter": lambda: ff.acquitter("g", 1),
    }
    avant = ff.chemin("g").read_text(encoding="utf-8")
    with open(cc.DOSSIER / "g.file.verrou", "a", encoding="utf-8") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        t = threading.Thread(target=gestes[geste])
        t.start()
        t.join(0.3)
        assert t.is_alive() and ff.chemin("g").read_text(encoding="utf-8") == avant  # il attend le verrou
    t.join(5)
    assert not t.is_alive() and ff.chemin("g").read_text(encoding="utf-8") != avant


# --- chaque programme qui retire une fiche passe par fiche.retirer ou fiche.deplacer


def test_doublons_d_identite_passe_par_fiche_retirer(racine, monkeypatch):
    monkeypatch.setattr(dd, "extraites", lambda: set())
    poser(racine, "sources/a/x.md", date="2024-05-01")
    y = racine / "sources/a/y.md"
    y.write_text((racine / "sources/a/x.md").read_text().replace("2024-05-01", "2024-02-01"), encoding="utf-8")
    fiche.oublier_index()
    retirees, _ = dd.fondre()
    assert retirees == ["sources/a/x.md"]
    assert evenements() == [("sources/a/x.md", "retiree")]


def test_purger_fiches_passe_par_fiche_retirer(racine, monkeypatch, capsys):
    monkeypatch.setattr(pf, "RACINE", racine)
    poser(racine, "sources/a/x.md")
    monkeypatch.setattr(fiche, "publicitaire", lambda titre, corps: True)
    assert pf.retirer_publicites() == {"sources/a/x.md"}
    assert not (racine / "sources/a/x.md").exists()
    assert evenements() == [("sources/a/x.md", "retiree")]
    assert "publicité : sources/a/x.md" in capsys.readouterr().out


def test_la_commande_de_purge_retire_les_publicites(racine, monkeypatch):
    vus = []
    monkeypatch.setattr(pf, "retirer_publicites", lambda: {"sources/a/x.md"})
    monkeypatch.setattr(pf, "purger", vus.append)
    assert pf.main(["--publicites"]) == 0 and pf.main(["sources/a/y.md"]) == 0
    assert vus == [{"sources/a/x.md"}, {"sources/a/y.md"}]


def test_collecte_arxiv_redater_passe_par_fiche_retirer(racine, monkeypatch):
    p = poser(racine, "sources/a/2026-11_un-papier.md", arxiv_id="2401.01234", url="https://arxiv.org/abs/2401.01234")
    monkeypatch.setattr(ca, "DOSSIERS_ARXIV", [racine / "sources/a"])
    assert ca.mode_redater() == 0
    assert not p.exists() and (racine / "sources/a/2024-01-01_un-papier.md").exists()
    assert evenements() == [
        ("sources/a/2024-01-01_un-papier.md", "parue"),
        ("sources/a/2026-11_un-papier.md", "retiree"),
    ]


def test_collecte_arxiv_repartir_passe_par_fiche_deplacer(racine, monkeypatch):
    ff.ouvrir("large")
    p = poser(racine, "sources/a/x.md", source="veille arXiv, thème « robots »")
    ff.acquitter("large", len(ff.lire("large")))
    monkeypatch.setattr(ca, "RACINE", racine)
    monkeypatch.setattr(ca, "DOSSIERS_ARXIV", [racine / "sources/a"])
    monkeypatch.setattr(ca, "VEILLES", [{"dossier": "b", "themes": ["robots"]}])
    monkeypatch.setattr(ca, "identifiants_cites", lambda: {})
    ca.mode_repartir(controle=False)
    assert not p.exists() and (racine / "sources/b/x.md").exists()
    assert evenements("large") == [("sources/a/x.md", "retiree"), ("sources/b/x.md", "parue")]


def test_collecte_dossier_local_passe_par_fiche_retirer(racine):
    dossier = racine / "sources/a"
    poser(racine, "sources/a/disparue.md")
    manifeste = racine / "manifeste.json"
    manifeste.write_text(json.dumps({"disparue.md": {"file": "disparue.md"}, "jamais-ecrite.md": {}}), encoding="utf-8")
    src = types.SimpleNamespace(racine=str(racine), chemin=dossier, manifeste=manifeste, fichiers=[], nom="local")
    compte = cdl.traiter_source(src, controle=False, echecs=[])
    assert compte["retirees"] == 2 and not (dossier / "disparue.md").exists()
    assert json.loads(manifeste.read_text(encoding="utf-8")) == {}
    assert evenements() == [("sources/a/disparue.md", "retiree")]


def test_la_quarantaine_passe_par_fiche_deplacer(racine):
    poser(racine, "sources/a/x.md")
    notes = na.mettre_en_quarantaine(["sources/a/x.md"], racine, racine / "quarantaine")
    assert notes == ["sources/a/x.md"] and (racine / "quarantaine/x.md").exists()
    assert evenements() == [("sources/a/x.md", "retiree")]


def test_aucun_programme_n_efface_une_fiche_hors_de_fiche_retirer():
    """Les effacements qui restent hors de `fiche.py` ne visent pas une fiche de `sources/` : chacun est nommé ici."""
    import re
    from pathlib import Path

    admis = {
        "reporter.py": 'Path(E["fiche_au_graphe"]).unlink()',  # réécrite aussitôt au même chemin : l'écriture signale
    }
    src = Path(fiche.__file__).parent
    for nom in ("doublons_d_identite.py", "collecte_arxiv.py", "collecte_dossier_local.py", "naissance.py"):
        texte = (src / nom).read_text(encoding="utf-8")
        assert not re.search(r"\.unlink\(|shutil\.move|shutil\.copy2", texte), nom
    assert "(RACINE / c).unlink()" not in (src / "purger_fiches.py").read_text(encoding="utf-8")
    assert admis["reporter.py"] in (src / "reporter.py").read_text(encoding="utf-8")


def test_la_purge_se_lance_en_ligne_de_commande(monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["purger_fiches"])  # aucune fiche nommée : rien à purger
    with pytest.raises(SystemExit) as fin:
        runpy.run_module("newskit.purger_fiches", run_name="__main__")
    assert fin.value.code == 0

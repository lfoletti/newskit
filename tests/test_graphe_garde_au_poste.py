"""Un graphe gardé au poste ne part pas au serveur.

Un média tient parfois un graphe pour lui seul : des notes internes, un essai. Il le déclare `publie: false` au
registre. Rien de ce graphe ne quitte alors la machine où il se construit : ni son dossier sous `graphe/`, ni ses
lettres, ni les dossiers de fiches qu'aucun graphe publié ne lit, ni son index, ni son cerveau. La règle tient lors d'un
passage complet, pour un envoi qui nomme le graphe, et quand les données sont un dépôt git sans distant, dont tout
fichier suivi part au serveur."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from newskit import config_sources as cfg
from newskit import publier_cerveau as pcv
from newskit import publier_graphes as pg
from newskit import publier_index as pi

# --- La clé au registre ------------------------------------------------------------------------------------------


def registre(**cles) -> tuple[dict, str]:
    d = copy.deepcopy(cfg._brut(cfg.FICHIER))
    g = next(iter(d["graphes"]))
    d["graphes"][g].pop("publie", None)
    d["graphes"][g].update(cles)
    return d, g


def test_le_registre_admet_publie(monkeypatch):
    d, g = registre(publie=False)
    assert cfg._valider(d, cfg.FICHIER)
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": d["graphes"]})
    assert cfg.garde_au_poste(g) is True
    assert cfg.garde_au_poste("graphe-absent") is False
    d["graphes"][g]["publie"] = True
    assert cfg.garde_au_poste(g) is False
    del d["graphes"][g]["publie"]
    assert cfg.garde_au_poste(g) is False  # sans la clé, un graphe se publie


@pytest.mark.parametrize("v", ["non", 0, None, ["a"]])
def test_publie_est_vrai_ou_faux(v):
    d, g = registre(publie=v)
    with pytest.raises(cfg.ConfigInvalide, match=f"graphes.{g}.publie : vrai ou faux"):
        cfg._valider(d, cfg.FICHIER)


# --- L'envoi des graphes ------------------------------------------------------------------------------------------

FICHIERS = (
    "graphe/ouvert/graphe.json.gz",
    "graphe/interne/graphe.json.gz",
    "graphe/interne/couche2/fiches/f.json",
    "graphe/internet/graphe.json.gz",  # un nom qui commence comme celui du graphe gardé
    "sources/ouvert/rss/a.md",
    "sources/commun/rss/b.md",
    "sources/interne/notes/c.md",
    "lettres/ouvert/2026-11-18.json",
    "lettres/interne/2026-11-04.json",
    "ROUTINE.md",
)
SUIVIS = ("sources", "lettres", "ROUTINE.md")
DOSSIERS = {  # les dossiers de fiches de chaque graphe ; « interne » lit aussi ceux d'« ouvert » (il l'inclut)
    "ouvert": ["sources/ouvert/rss", "sources/commun/rss"],
    "internet": [],
    "interne": ["sources/interne/notes", "sources/commun/rss"],
}


def donnees(tmp_path, monkeypatch, gardes=("interne",), distant=None):
    """Un dépôt de données à trois graphes, dont un gardé au poste ; rend la liste des envois de rsync."""
    r = tmp_path / "donnees"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    if distant:
        subprocess.run(["git", "remote", "add", "origin", str(distant)], cwd=r, check=True)
    for rel in FICHIERS:
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", *SUIVIS], cwd=r, check=True)
    decl = {g: ({"publie": False} if g in gardes else {}) for g in DOSSIERS}
    monkeypatch.setattr(pg, "RACINE", r)
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": decl})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": r / "graphe" / g})
    monkeypatch.setattr(cfg, "dossiers_du_graphe", lambda g, propres=False: list(DOSSIERS[g]))
    monkeypatch.setattr(pg.pi, "lire_acces", lambda: {"VPS_HOTE": "127.0.0.1", "VPS_UTILISATEUR": "u", "VPS_CLE": "/k"})
    envois = []
    vrai = subprocess.run

    def run(cmd, **k):
        if cmd[0] == "git":
            return vrai(cmd, **k)
        if cmd[0] == "rsync":
            envois.append(sorted(open(cmd[cmd.index("--files-from") + 1]).read().split()))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pg.subprocess, "run", run)
    return envois


def test_les_chemins_gardes_sont_ceux_du_graphe_seul(tmp_path, monkeypatch):
    donnees(tmp_path, monkeypatch)
    # Le dossier commun reste : un graphe publié le lit.
    assert pg.chemins_gardes() == ["graphe/interne", "lettres/interne", "sources/interne/notes"]
    assert pg.garde("graphe/interne/graphe.json.gz", pg.chemins_gardes())
    assert pg.garde("graphe/interne", pg.chemins_gardes())
    assert not pg.garde("graphe/internet/graphe.json.gz", pg.chemins_gardes())


def test_sans_graphe_garde_rien_n_est_retenu(tmp_path, monkeypatch):
    donnees(tmp_path, monkeypatch, gardes=())

    def jamais(g, propres=False):
        raise AssertionError("sans graphe gardé, les dossiers de fiches ne se lisent pas")

    monkeypatch.setattr(cfg, "dossiers_du_graphe", jamais)
    assert pg.chemins_gardes() == []


def test_un_passage_complet_n_envoie_pas_le_graphe_garde(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch, distant=tmp_path / "ailleurs")
    lignes = pg.publier(verbeux=False)
    assert envois == [["graphe/internet/graphe.json.gz", "graphe/ouvert/graphe.json.gz"]]
    assert any("interne" in l and "gardé au poste" in l for l in lignes)
    assert not any("interne," in l or ", interne " in l for l in lignes if l.startswith("Graphes en ligne"))


def test_un_envoi_qui_nomme_le_graphe_garde_n_envoie_rien(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch, distant=tmp_path / "ailleurs")
    lignes = pg.publier(["interne"], verbeux=False)
    assert envois == []
    assert any("interne" in l and "gardé au poste" in l for l in lignes)


def test_sans_distant_les_fichiers_suivis_du_graphe_garde_restent(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch)
    pg.publier(["ouvert"], verbeux=False)
    assert envois == [
        [
            "ROUTINE.md",
            "graphe/ouvert/graphe.json.gz",
            "lettres/ouvert/2026-11-18.json",
            "sources/commun/rss/b.md",
            "sources/ouvert/rss/a.md",
        ]
    ]


def test_sans_graphe_garde_l_envoi_ne_change_pas(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch, gardes=())
    lignes = pg.publier(["interne"], verbeux=False)
    assert "graphe/interne/graphe.json.gz" in envois[0] and "sources/interne/notes/c.md" in envois[0]
    assert "lettres/interne/2026-11-04.json" in envois[0]
    assert not any("gardé au poste" in l for l in lignes)


# --- L'index et le cerveau ----------------------------------------------------------------------------------------


def test_l_index_du_graphe_garde_ne_part_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": {"interne": {"publie": False}, "ouvert": {}}})
    monkeypatch.setattr(pi, "lire_acces", lambda: {"VPS_HOTE": "127.0.0.1", "VPS_UTILISATEUR": "u", "VPS_CLE": "/k"})
    vus = []
    monkeypatch.setattr(pi, "a_envoyer", lambda g, modeles=True: vus.append(g) or [])
    monkeypatch.setattr(pi, "a_un_index", lambda g: True)
    lignes = pi.publier(["interne", "ouvert"], verbeux=False)
    assert vus == ["ouvert"]
    assert any("interne" in l and "gardé au poste" in l for l in lignes)
    vus.clear()
    assert pi.graphes_indexes() == ["ouvert"]


def test_le_cerveau_du_graphe_garde_ne_part_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": {"interne": {"publie": False}, "ouvert": {}}})
    monkeypatch.setattr(pcv, "DOSSIER", tmp_path)
    monkeypatch.setattr(pcv, "VPS", tmp_path / "vps")
    for g in ("interne", "ouvert"):
        (tmp_path / f"{g}.db").write_text("x")
        (tmp_path / f"{g}.json").write_text(json.dumps({"publie": True}))
    assert pcv.tenu_au_serveur("interne") is False and pcv.tenu_au_serveur("ouvert") is True
    assert [rel for _, rel in pcv.a_envoyer(["interne", "ouvert"])] == [
        "cache/cortexdb/ouvert.envoi.db",
        "cache/cortexdb/ouvert.envoi.json",
    ]

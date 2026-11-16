"""Un média dont les données sont un dépôt git sans distant publie quand même (chantier K, 16.11.2026).

Au serveur, les fiches, les lots et le cerveau vivent dans un dépôt git local, jamais poussé (décision du mainteneur,
16.11.2026). La routine commite comme au poste ; sans distant, elle ne pousse rien, mais publie le fil, l'index, les
graphes et le cerveau comme après une poussée réussie. Les dépôts du navigateur ne lèvent pas d'alerte."""

from __future__ import annotations

import subprocess

import pytest

from newskit import depots
from newskit import routine_hebdo as rh


def depot(chemin, distant=None):
    chemin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=chemin, check=True)
    if distant:
        subprocess.run(["git", "remote", "add", "origin", str(distant)], cwd=chemin, check=True)
    return chemin


@pytest.fixture
def publications(monkeypatch):
    appels = []
    monkeypatch.setattr(rh, "publier_en_ligne", lambda: appels.append("publier"))
    return appels


def test_sans_distant_la_routine_publie_sans_pousser(tmp_path, monkeypatch, publications, capsys):
    monkeypatch.setattr(rh, "RACINE", depot(tmp_path / "donnees"))
    rh.pousser()
    assert publications == ["publier"]
    assert "ALERTE" not in capsys.readouterr().out


def test_avec_un_distant_injoignable_la_routine_alerte_et_ne_publie_pas(tmp_path, monkeypatch, publications, capsys):
    monkeypatch.setattr(rh, "RACINE", depot(tmp_path / "donnees", distant=tmp_path / "absent"))
    rh.pousser()
    assert publications == []
    assert "ALERTE : envoi vers origin impossible" in capsys.readouterr().out


def test_sans_distant_les_depots_ne_levent_pas_d_alerte(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(depots, "RACINE", depot(tmp_path / "donnees"))
    assert depots.pousser() == ""
    assert "ALERTE" not in capsys.readouterr().err


def test_avec_un_distant_injoignable_les_depots_alertent(tmp_path, monkeypatch):
    monkeypatch.setattr(depots, "RACINE", depot(tmp_path / "donnees", distant=tmp_path / "absent"))
    assert depots.pousser().startswith("ALERTE : push refusé")


# Sans poussée, le clone du serveur ne reçoit plus par git le rapport, les lettres, les éclairages, le registre des
# graphes ni les fiches. Sans distant, publier_graphes envoie donc aussi tout fichier suivi par le dépôt de données,
# sauf les liens (la configuration, liée depuis le clone du code).
from newskit import config_sources as cfg  # noqa: E402
from newskit import publier_graphes as pg  # noqa: E402


def donnees(tmp_path, monkeypatch, distant=None):
    r = depot(tmp_path / "donnees", distant=distant)
    for rel in ("graphe/g/graphe.json.gz", "graphe/test/x.json", "sources/a.md", "lettres/l.json", "ROUTINE.md"):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text("x", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("x", encoding="utf-8")
    (r / "config.yaml").symlink_to(tmp_path / "config.yaml")
    subprocess.run(
        ["git", "add", "graphe/test/x.json", "sources", "lettres", "ROUTINE.md", "config.yaml"], cwd=r, check=True
    )
    bases = {"g": r / "graphe" / "g", "test": r / "graphe" / "test"}
    monkeypatch.setattr(pg, "RACINE", r)
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": bases[g]})
    monkeypatch.setattr(cfg, "charger", lambda: {"graphes": {g: {} for g in bases}})
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


def test_sans_distant_la_publication_envoie_les_fichiers_suivis_sauf_les_liens(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch)
    pg.publier(["g", "test"], verbeux=False)
    assert envois == [["ROUTINE.md", "graphe/g/graphe.json.gz", "graphe/test/x.json", "lettres/l.json", "sources/a.md"]]


def test_avec_un_distant_la_publication_laisse_les_fichiers_suivis_au_pull(tmp_path, monkeypatch):
    envois = donnees(tmp_path, monkeypatch, distant=tmp_path / "ailleurs")
    pg.publier(["g", "test"], verbeux=False)
    assert envois == [["graphe/g/graphe.json.gz"]]


def test_un_dossier_qui_n_est_pas_un_depot_garde_la_regle_du_pull(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "RACINE", tmp_path)
    (tmp_path / ".git").write_text("gitdir: ailleurs", encoding="utf-8")  # un worktree : .git est un fichier
    assert pg.sans_distant() is False
    (tmp_path / ".git").unlink()
    assert pg.sans_distant() is False

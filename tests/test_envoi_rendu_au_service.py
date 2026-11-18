"""Tout fichier envoyé au serveur revient au compte du service.

L'envoi des graphes écrit sur le serveur sous root (`--rsync-path=sudo rsync`). La commande qui suit rendait au compte du
service le seul dossier `graphe/`. Quand les données sont un dépôt git sans distant, l'envoi porte aussi les fichiers
suivis : fiches, lettres, fichiers de tête. Ils restaient à root, avec les dossiers créés pour eux, et le service ne
pouvait plus les réécrire ni les retirer. La commande rend désormais chaque racine envoyée."""

from __future__ import annotations

import shlex
import subprocess

from newskit import config_sources as cfg
from newskit import publier_graphes as pg

MARQUE = f"runuser -u kg -- touch /c/{pg.MARQUE}"


def test_les_racines_sont_les_premiers_elements_des_chemins():
    liste = ["sources/a/rss/x.md", "graphe/a/graphe.json.gz", "ROUTINE.md", "lettres/a/1.json", "sources/b/y.md"]
    assert pg.racines(liste) == ["ROUTINE.md", "graphe", "lettres", "sources"]
    assert pg.racines([]) == []


def test_la_commande_rend_chaque_racine_envoyee():
    liste = ["sources/a/rss/x.md", "ROUTINE.md", "lettres/a/1.json"]
    assert pg.commande_distante("/c", liste) == (
        f"chown -R kg:kg -- /c/ROUTINE.md /c/graphe /c/lettres /c/sources && {MARQUE}"
    )


def test_sans_liste_la_commande_rend_le_dossier_des_graphes():
    assert pg.commande_distante("/c") == f"chown -R kg:kg -- /c/graphe && {MARQUE}"
    assert pg.commande_distante("/c", ["graphe/a/graphe.json.gz"]) == f"chown -R kg:kg -- /c/graphe && {MARQUE}"


def test_les_chemins_de_la_commande_sont_proteges():
    cmd = pg.commande_distante("/mon clone", ["mes notes/a.md", "a;b/c.md"])
    assert shlex.split(cmd)[:8] == [
        "chown",
        "-R",
        "kg:kg",
        "--",
        "/mon clone/a;b",
        "/mon clone/graphe",
        "/mon clone/mes notes",
        "&&",
    ]


def donnees(tmp_path, monkeypatch, distant=None):
    """Un dépôt de données jetable ; rend les commandes ssh lancées par l'envoi."""
    r = tmp_path / "donnees"
    for rel in ("graphe/a/graphe.json.gz", "sources/a/rss/x.md", "lettres/a/1.json", "ROUTINE.md"):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    if distant:
        subprocess.run(["git", "remote", "add", "origin", str(distant)], cwd=r, check=True)
    subprocess.run(["git", "add", "sources", "lettres", "ROUTINE.md"], cwd=r, check=True)
    monkeypatch.setattr(pg, "RACINE", r)
    monkeypatch.setattr(cfg, "charger", lambda chemin=None: {"graphes": {"a": {}}})
    monkeypatch.setattr(cfg, "chemins_du_graphe", lambda g: {"base": r / "graphe" / g})
    monkeypatch.setattr(
        pg.pi,
        "lire_acces",
        lambda: {"VPS_HOTE": "127.0.0.1", "VPS_UTILISATEUR": "u", "VPS_CLE": "/k", "VPS_CLONE": "/c"},
    )
    lancees = []
    vrai = subprocess.run

    def run(cmd, **k):
        if cmd[0] == "git":
            return vrai(cmd, **k)
        if cmd[0] == "ssh":
            lancees.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pg.subprocess, "run", run)
    return lancees


def test_sans_distant_l_envoi_rend_les_fichiers_suivis(tmp_path, monkeypatch):
    lancees = donnees(tmp_path, monkeypatch)
    pg.publier(["a"], verbeux=False)
    attendu = pg.commande_distante("/c", ["ROUTINE.md", "graphe/a/x", "lettres/a/1.json", "sources/a/rss/x.md"])
    assert "/c/sources" in attendu and "/c/lettres" in attendu and "/c/ROUTINE.md" in attendu
    assert lancees == ["sudo bash -c " + shlex.quote(attendu)]


def test_avec_un_distant_l_envoi_rend_le_dossier_des_graphes(tmp_path, monkeypatch):
    lancees = donnees(tmp_path, monkeypatch, distant=tmp_path / "ailleurs")
    pg.publier(["a"], verbeux=False)
    assert lancees == ["sudo bash -c " + shlex.quote(f"chown -R kg:kg -- /c/graphe && {MARQUE}")]

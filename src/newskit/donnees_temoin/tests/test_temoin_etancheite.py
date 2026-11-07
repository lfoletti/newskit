"""Le témoin absurde, tranche 1 : l'étanchéité (NEWSKIT.md, N4 ; CHANTIER_GENERIQUE.md § 4.3).

Le témoin fait tourner le moteur sur une copie de lui-même, avec un graphe sans rapport (apiculture-medievale), une
maison temporaire (HOME : les `Path.home()` du moteur y tombent), un faux `claude` en tête du PATH et un canari
(`sitecustomize`, dans chaque processus Python) qui refuse et journalise toute écriture vers le réel et toute
connexion hors de la boucle locale. Une seule fuite écrirait dans le vrai registre des coûts, poserait la vraie
marque de limite (toutes les boucles arrêtées trois heures) ou réécrirait le registre du skill : le canari fait
partie du verdict.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from newskit import commun  # noqa: E402
from newskit import temoin  # noqa: E402

REEL_COUTS = Path(commun.RACINE) / "cache" / "couts.jsonl"      # le vrai registre, pas celui du conftest
REEL_MARQUE = Path(commun.RACINE) / "cache" / "limite_atteinte"


def _etat(p: Path):
    return (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None


def _lancer(t, code: str, env=None, cwd=None):
    """Un processus Python dans l'environnement du témoin, ses scripts sur le chemin."""
    return subprocess.run([sys.executable, "-c", code], env=env or temoin.environnement(t),
                          cwd=cwd or t.code / "newskit", capture_output=True, text=True, timeout=120)


def _journal(t) -> list[dict]:
    return [json.loads(l) for l in t.journal.read_text(encoding="utf-8").splitlines()] if t.journal.exists() else []


SONDE = """
import subprocess; from newskit import couts, themes_emergents as te
couts.enregistrer("temoin", "apiculture-medievale", "faux", subprocess.CompletedProcess([], 0,
    '{"type":"result","is_error":false,"total_cost_usd":0,"usage":{"input_tokens":1,"output_tokens":1}}', ""))
couts._poser_marque("essai du témoin")
te.REGISTRE_SKILL.parent.mkdir(parents=True, exist_ok=True)
te.REGISTRE_SKILL.write_text("{}", encoding="utf-8")
print(couts.REGISTRE, couts.MARQUE, te.REGISTRE_SKILL, sep="\\n")
"""


def test_temoin_ne_touche_pas_au_reel(tmp_path):
    avant = (_etat(REEL_COUTS), _etat(REEL_MARQUE))
    t = temoin.preparer(tmp_path / "t")
    r = _lancer(t, SONDE)
    assert r.returncode == 0, r.stderr[-800:]
    registre, marque, skill = map(Path, r.stdout.split())
    assert (_etat(REEL_COUTS), _etat(REEL_MARQUE)) == avant          # le vrai registre et la vraie marque intacts
    assert _journal(t) == []                                           # aucune tentative vers le réel
    assert registre.is_relative_to(t.donnees) and len(registre.read_text(encoding="utf-8").splitlines()) == 1
    assert marque.is_relative_to(t.donnees) and marque.exists()
    assert skill.is_relative_to(t.donnees) and skill.exists()                 # le registre des graphes du média


def test_le_canari_refuse_une_ecriture_vers_le_reel_et_la_journalise(tmp_path):
    reel = tmp_path / "reel"
    reel.mkdir()
    (reel / "garde.txt").write_text("intact", encoding="utf-8")
    t = temoin.preparer(tmp_path / "t")
    env = temoin.environnement(t, reel=[reel])
    for geste in (f"open({str(reel / 'x.txt')!r}, 'w')",
                  f"open({str(reel / 'garde.txt')!r}, 'a')",
                  f"import os; os.remove({str(reel / 'garde.txt')!r})",
                  f"import os; os.mkdir({str(reel / 'd')!r})",
                  f"import os; open('ici.txt', 'w').close(); os.replace('ici.txt', {str(reel / 'y.txt')!r})"):
        r = _lancer(t, geste, env=env, cwd=t.dossier)
        assert r.returncode != 0 and "témoin" in r.stderr, geste
    assert sorted(p.name for p in reel.iterdir()) == ["garde.txt"]
    assert (reel / "garde.txt").read_text(encoding="utf-8") == "intact"
    assert [x["quoi"] for x in _journal(t)] == ["open", "open", "os.remove", "os.mkdir", "os.rename"]


def test_le_canari_laisse_lire_le_reel(tmp_path):
    reel = tmp_path / "reel"
    reel.mkdir()
    (reel / "a.txt").write_text("lu", encoding="utf-8")
    t = temoin.preparer(tmp_path / "t")
    r = _lancer(t, f"print(open({str(reel / 'a.txt')!r}).read())", env=temoin.environnement(t, reel=[reel]))
    assert (r.returncode, r.stdout.strip(), _journal(t)) == (0, "lu", [])


def test_le_canari_coupe_le_reseau_sauf_la_boucle_locale(tmp_path):
    t = temoin.preparer(tmp_path / "t")
    r = _lancer(t, "import socket; s = socket.socket(); s.settimeout(5); s.connect(('192.0.2.1', 80))")   # TEST-NET-1
    assert r.returncode != 0 and "réseau coupé" in r.stderr
    r = _lancer(t, "import socket; socket.getaddrinfo('exemple.org', 443)")
    assert r.returncode != 0 and "réseau coupé" in r.stderr
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        r = _lancer(t, f"import socket; socket.create_connection(('127.0.0.1', {s.getsockname()[1]}), timeout=5).close()")
    assert r.returncode == 0, r.stderr[-400:]
    assert [x["quoi"] for x in _journal(t)] == ["socket.connect", "socket.getaddrinfo"]


def test_le_faux_claude_rejoue_une_reponse_enregistree(tmp_path):
    t = temoin.preparer(tmp_path / "t", reponses=tmp_path / "reponses")
    cle = temoin.empreinte("claude-opus-5-5", '{"type": "object"}', "  Une   consigne\n sur les ruches ")
    (tmp_path / "reponses").mkdir()
    (tmp_path / "reponses" / f"{cle}.json").write_text('{"type":"result","result":"essaim"}', encoding="utf-8")
    r = subprocess.run(["claude", "-p", "--model", "claude-opus-5-5", "--json-schema", '{"type": "object"}'],
                       input="Une consigne  sur\tles ruches\n", env=temoin.environnement(t), capture_output=True, text=True)
    assert (r.returncode, json.loads(r.stdout)["result"]) == (0, "essaim")


def test_une_consigne_inconnue_rend_98_et_va_dans_manquantes(tmp_path):
    t = temoin.preparer(tmp_path / "t", reponses=tmp_path / "reponses")
    r = subprocess.run(["claude", "-p", "--model", "m"], input="consigne changée", env=temoin.environnement(t),
                       capture_output=True, text=True)
    assert r.returncode == temoin.CONSIGNE_INCONNUE == 98 and "consigne changée" in r.stderr
    (m,) = t.manquantes.iterdir()
    assert m.stem == temoin.empreinte("m", "", "consigne changée")
    assert json.loads(m.read_text(encoding="utf-8")) == {"modele": "m", "schema": "", "consigne": "consigne changée",
                                                         "argv": ["-p", "--model", "m"]}


def test_la_copie_est_un_depot_sans_distant_avec_le_moteur(tmp_path):
    t = temoin.preparer(tmp_path / "t")
    assert (t.code / "newskit" / "routine_hebdo.py").exists()
    r = subprocess.run(["git", "-C", str(t.code), "remote"], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


def test_l_environnement_du_temoin(tmp_path):
    t = temoin.preparer(tmp_path / "t")
    env = temoin.environnement(t)
    assert env["HOME"] == str(t.maison) and env["NEWSKIT_DONNEES"] == str(t.donnees)
    assert env["PATH"].split(":")[0] == str(t.bin) and (t.bin / "claude").exists()
    assert (env["PYTHONHASHSEED"], env["HF_HUB_OFFLINE"]) == ("0", "1")
    assert str(t.canari) in env["PYTHONPATH"].split(":")
    reel = env["TEMOIN_REEL"].split(":")
    assert str(Path(commun.CODE)) in reel and str(Path(commun.RACINE)) in reel and str(Path.home()) in reel


def test_le_registre_du_temoin_ne_declare_que_le_graphe_absurde(tmp_path):
    import yaml
    t = temoin.preparer(tmp_path / "t")
    c = yaml.safe_load((t.donnees / "config.yaml").read_text(encoding="utf-8"))
    d = yaml.safe_load((t.donnees / "sources.yaml").read_text(encoding="utf-8"))
    assert c["sources"] == "sources.yaml" and "graphes" not in d
    assert list(c["graphes"]) == [temoin.GRAPHE] and {s["graphe"] for s in d["sources"]} == {temoin.GRAPHE}
    r = _lancer(t, "from newskit import config_sources as cfg; print(len(cfg.charger()['sources']))")
    assert r.returncode == 0, r.stderr[-800:]


def test_le_temoin_pose_sous_une_racine_reelle_ecrit_chez_lui(tmp_path):
    """La nuit, le témoin vit sous cache/ des vraies données : son propre dossier reste à lui."""
    reel = tmp_path / "reel"
    t = temoin.preparer(reel / "cache" / "temoin")
    r = _lancer(t, f"open({str(t.donnees / 'cache' / 'a.txt')!r}, 'w').write('x')", env=temoin.environnement(t, reel=[reel]))
    assert (r.returncode, _journal(t)) == (0, []), r.stderr[-400:]
    r = _lancer(t, f"open({str(reel / 'cache' / 'b.txt')!r}, 'w')", env=temoin.environnement(t, reel=[reel]))
    assert r.returncode != 0 and [x["quoi"] for x in _journal(t)] == ["open"]


@pytest.mark.parametrize("outil", temoin.RESEAU)
def test_les_outils_reseau_du_systeme_sont_coupes_et_journalises(tmp_path, outil):
    """06.11.2026 : remis à la vraie maison, le témoin lisait la table des dépôts du VPS par ssh (depots.py) ; un
    binaire échappe au canari, le PATH le remplace."""
    t = temoin.preparer(tmp_path / "t")
    r = subprocess.run([outil, "kg@temoin.invalid", "true"], env=temoin.environnement(t), capture_output=True, text=True)
    assert r.returncode == temoin.RESEAU_COUPE == 97 and "réseau coupé" in r.stderr
    assert [(x["quoi"], x["vise"]) for x in _journal(t)] == [(outil, "kg@temoin.invalid true")]


def test_les_fiches_du_temoin_ont_une_date_fixe_quelle_que_soit_la_copie(tmp_path, monkeypatch):
    """Sans date dans le texte, une fiche du dossier local prend la date de son fichier : une copie neuve du moteur
    (un clone, la CI, le paquet installé) changerait toutes les consignes et le témoin serait rouge (07.11.2026).
    Le témoin pose une date fixe sur ses fiches."""
    import datetime as dt
    import os
    import shutil
    copie = tmp_path / "temoin_source"
    shutil.copytree(temoin.TEMOIN, copie)
    for f in (copie / "dossier").glob("*.md"):
        os.utime(f, (0, 0))                     # une copie dont les fichiers portent une autre date
    monkeypatch.setattr(temoin, "TEMOIN", copie)
    t = temoin.preparer(tmp_path / "t", reponses=copie / "reponses")
    dates = {dt.date.fromtimestamp(f.stat().st_mtime) for f in (t.donnees / "dossier_local").glob("*.md")}
    assert dates == {temoin.DATE_DES_FICHES}

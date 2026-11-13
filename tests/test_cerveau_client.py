"""Le client gRPC du cerveau trouve Newskit (mainteneur, 13.11.2026, essai de refus J2).

Du 08.11 au 13.11.2026, chaque nuit, « cerveau CortexDB (ai-governance) : code 1 — non bloquant » :
`ModuleNotFoundError: No module named 'newskit'`. Le versement (puits_cortexdb.py) tourne sous le Python du client
(cache/pilote_cortexdb/venv), qui n'a que cortexdb_client ; depuis que Newskit est un paquet installé, il ne s'y
trouvait plus. Le cerveau publié restait celui du 7 novembre. Le client reçoit un chemin qui ne porte que Newskit :
ni le reste des paquets du poste, qui masqueraient grpcio ou protobuf du client."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from newskit import cerveau_cortexdb as cc
from newskit.commun import CODE

IMPORT = ["-S", "-c", "import newskit, sys; print(newskit.__file__)"]  # -S : sans les paquets du site


def test_sans_chemin_un_python_nu_ne_trouve_pas_newskit(tmp_path):
    r = subprocess.run(
        [sys.executable, *IMPORT], capture_output=True, text=True, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}
    )
    assert r.returncode and "No module named 'newskit'" in r.stderr


def test_le_client_trouve_newskit_et_rien_d_autre(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CHEMIN_CLIENT", tmp_path / "chemin")
    env = cc.env_du_client()
    r = subprocess.run([sys.executable, *IMPORT], capture_output=True, text=True, cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()).resolve().parent == CODE
    chemins = env["PYTHONPATH"].split(":")
    assert chemins[0] == str(tmp_path / "chemin")
    assert [p.name for p in (tmp_path / "chemin").iterdir()] == ["newskit"]  # rien d'autre que Newskit
    assert str(CODE.parent) not in chemins


def test_le_lien_suit_le_paquet_installe(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CHEMIN_CLIENT", tmp_path / "chemin")
    (tmp_path / "chemin").mkdir()
    (tmp_path / "chemin" / "newskit").symlink_to(tmp_path)  # un ancien paquet
    cc.env_du_client()
    assert (tmp_path / "chemin" / "newskit").resolve() == CODE
    cc.env_du_client()  # deux fois : rien ne casse


def test_le_versement_et_les_outils_passent_par_ce_chemin(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "CHEMIN_CLIENT", tmp_path / "chemin")
    vus = []
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda args, **k: vus.append(k.get("env")) or subprocess.CompletedProcess(args, 0, "{}", ""),
    )
    cc.lancer_client(["x"])
    cc.appeler_outil(type("S", (), {"adresse": "a", "jeton": "j"})(), "outil", {})
    assert len(vus) == 2 and all(e and e["PYTHONPATH"].startswith(str(tmp_path / "chemin")) for e in vus)


def test_tout_appel_au_python_du_client_passe_par_son_chemin():
    """Chaque fonction qui lance PYTHON_CLIENT le fait avec env= ou par lancer_client (le versement est une fonction
    imbriquée, hors de portée d'un appel direct)."""
    import ast
    import inspect

    arbre = ast.parse(inspect.getsource(cc))
    fautes = []
    for f in ast.walk(arbre):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) or f.name == "lancer_client":
            continue
        corps = f.body
        noms = {n.id for b in corps for n in ast.walk(b) if isinstance(n, ast.Name)}
        if "PYTHON_CLIENT" not in noms:
            continue
        for b in corps:
            for n in ast.walk(b):
                if (
                    isinstance(n, ast.Call)
                    and ast.unparse(n.func) == "subprocess.run"
                    and not any(k.arg == "env" for k in n.keywords)
                ):
                    fautes.append(f"{f.name}:{n.lineno}")
    assert fautes == []

"""Le témoin absurde a son cerveau CortexDB (K3, 27.11.2026 : le cerveau est le graphe).

Le graphe du témoin est déclaré avec son cerveau, en couche 1 ; la nuit le construit avec le binaire CortexDB réel. Le
client du versement est le Python du témoin. L'encodeur du cerveau est un faux, déterministe, que le canari pose dans
le seul processus `embeddings_locaux` : le témoin n'a ni réseau ni modèle, et l'index de la nuit garde sa recherche par
les mots (les consignes enregistrées n'en dépendent pas).
"""

from __future__ import annotations

import json
import subprocess
import sys

from newskit import cerveau_cortexdb as cc  # noqa: E402
from newskit import temoin  # noqa: E402

SONDE = """
import json
try:
    import fastembed
except ImportError:
    print(json.dumps({"faux": False}))
else:
    if not getattr(fastembed, "TEMOIN", False):
        print(json.dumps({"faux": False}))  # le vrai fastembed n'a pas de modèle « m » : on le constate, sans encoder
    else:
        vecteurs = [list(v) for v in fastembed.TextEmbedding("m").embed(["essaim", "essaim", "cire"])]
        print(json.dumps({"faux": True, "vecteurs": vecteurs}))
"""


def _sonder(t, nom, tmp_path):
    script = tmp_path / nom
    script.write_text(SONDE, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(script)], env=temoin.environnement(t), capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def test_le_graphe_du_temoin_a_son_cerveau_en_couche_1():
    d = temoin.registre()["graphes"][temoin.GRAPHE]
    assert (d.get("cortexdb"), d.get("couche1")) == (True, True)


def test_le_temoin_donne_au_versement_le_binaire_reel_et_son_python_pour_client(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEXDB_BINAIRE", raising=False)
    t = temoin.preparer(tmp_path / "t")
    env = temoin.environnement(t)
    assert env["CORTEXDB_BINAIRE"] == str(cc.BINAIRE)
    client = t.donnees / "cache" / "pilote_cortexdb" / "venv" / "bin" / "python"
    r = subprocess.run(
        [str(client), "-c", "import sys, cortexdb_client, networkx; print(sys.executable)"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert (r.returncode, r.stdout.strip()) == (0, sys.executable), r.stderr[-800:]


def test_un_binaire_choisi_reste_celui_du_temoin(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXDB_BINAIRE", str(tmp_path / "cortexdb-grpc"))
    t = temoin.preparer(tmp_path / "t")
    assert temoin.environnement(t)["CORTEXDB_BINAIRE"] == str(tmp_path / "cortexdb-grpc")


def test_le_faux_encodeur_sert_l_encodeur_du_cerveau_et_lui_seul(tmp_path):
    t = temoin.preparer(tmp_path / "t")
    encodeur = _sonder(t, "embeddings_locaux.py", tmp_path)
    a, b, c = encodeur["vecteurs"]
    assert encodeur["faux"] is True and len(a) == 384 and a == b and a != c
    assert _sonder(t, "synthese.py", tmp_path)["faux"] is False

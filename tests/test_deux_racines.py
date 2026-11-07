"""N1 de NEWSKIT.md : la racine des données est donnée de l'extérieur (NEWSKIT_DONNEES), le code reste où il est.

Fini quand : la routine tourne sur une racine vide dans un dossier temporaire, sans lire un seul fichier du dépôt de
Contaixt. Le passage entier se prouve à la main (commit de N1) ; ces tests gardent les pièces qui le permettent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from newskit import commun
from newskit import routine_hebdo as rh

SCRIPTS = Path(commun.CODE)


def test_un_seul_script_calcule_une_racine_depuis_son_emplacement():
    """Seul `commun.py` déduit une racine de `__file__` : les autres l'importent. Un script qui recalcule la sienne
    lirait les données du dépôt du code, quelle que soit NEWSKIT_DONNEES."""
    fautifs = [p.name for p in sorted(SCRIPTS.glob("*.py"))
               if p.name != "commun.py" and re.search(r"Path\(__file__\)\.resolve\(\)\.parent\.parent", p.read_text(encoding="utf-8"))]
    assert fautifs == []


def _dans(env_donnees: str | None, code: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != commun.VARIABLE_DONNEES}
    if env_donnees is not None:
        env[commun.VARIABLE_DONNEES] = env_donnees
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); {code}"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_la_variable_deplace_les_donnees_et_pas_le_code(tmp_path):
    sortie = _dans(str(tmp_path), "from newskit import config_sources as cfg, commun; print(cfg.SOURCES); print(cfg.FICHIER); print(commun.CODE)")
    sources, fichier, code = sortie.splitlines()
    assert Path(sources) == tmp_path.resolve() / "sources"
    assert Path(fichier).parent == tmp_path.resolve()
    assert Path(code) == Path(commun.CODE)


def test_sans_variable_ni_media_les_donnees_sont_le_dossier_courant():
    """Installé, le paquet n'a sous CODE que son code : sans média, la racine est le dossier courant."""
    assert Path(_dans(None, "from newskit import commun; print(commun.RACINE)")) == Path.cwd().resolve()


def test_les_modules_du_moteur_suivent_la_racine_des_donnees(tmp_path):
    sortie = _dans(str(tmp_path), "from newskit import couts, interroger_graphe as ig, cartes; print(couts.REGISTRE); print(ig.RACINE); print(cartes.RACINE)")
    for ligne in sortie.splitlines():
        assert Path(ligne).resolve().is_relative_to(tmp_path.resolve()), ligne


def test_une_etape_lance_le_script_du_moteur_sous_code():
    assert rh.script_du_moteur(["scripts/carte.py", "--graphe", "g"]) == [str(Path(commun.CODE) / "carte.py"), "--graphe", "g"]
    assert rh.script_du_moteur(["/ailleurs/synchroniser.py"]) == ["/ailleurs/synchroniser.py"]


def test_le_skill_ne_se_synchronise_que_pour_nos_graphes(tmp_path, monkeypatch):
    monkeypatch.setattr(rh, "RACINE", tmp_path)
    reg = tmp_path / "graphes.json"
    reg.write_text(json.dumps({"nôtre": {"racine": str(tmp_path)}, "autre": {"racine": "/un/autre/depot"}}), encoding="utf-8")
    assert rh.skill_a_synchroniser(["nôtre"], reg)
    assert not rh.skill_a_synchroniser(["autre"], reg)
    assert not rh.skill_a_synchroniser(["inconnu"], reg)
    assert not rh.skill_a_synchroniser(["nôtre"], tmp_path / "absent.json")

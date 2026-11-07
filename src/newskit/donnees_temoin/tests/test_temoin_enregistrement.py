"""Le témoin absurde, tranche 3 : l'enregistrement des réponses (NEWSKIT.md, N4 ; CHANTIER_GENERIQUE.md § 4.3).

`temoin.reenregistrer(t, plafond)` paie les consignes manquantes, une par une, sous un plafond d'appels : le vrai
`claude` (hors du témoin, avec la vraie maison pour l'abonnement) reçoit les mêmes arguments et la même consigne ; la
réponse va sous `reponses/<empreinte>.json`, la manquante est retirée, chaque appel entre au vrai registre des coûts,
poste « temoin ». Une réponse en erreur ne s'enregistre pas ; la limite d'usage arrête tout. Ici, un faux « vrai
claude » répond : aucun appel payé.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from newskit import couts  # noqa: E402
from newskit import temoin  # noqa: E402

ENVELOPPE = {"type": "result", "is_error": False, "result": "ok", "total_cost_usd": 0.01,
             "usage": {"input_tokens": 10, "output_tokens": 5}}


def _vrai_faux(tmp_path, reponse=ENVELOPPE, code=0):
    """Un « vrai claude » de test : écrit ce qu'il reçoit dans recus.jsonl et rend `reponse`."""
    f = tmp_path / "vrai_claude"
    f.write_text(f"""#!{sys.executable}
import json, sys
open({str(tmp_path / 'recus.jsonl')!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "consigne": sys.stdin.read()}}) + "\\n")
sys.stdout.write({json.dumps(json.dumps(reponse))})
sys.exit({code})
""", encoding="utf-8")
    f.chmod(0o755)
    return f


def _manquante(t, modele, schema, consigne, argv):
    t.manquantes.mkdir(exist_ok=True)
    cle = temoin.empreinte(modele, schema, consigne)
    (t.manquantes / f"{cle}.json").write_text(json.dumps({"modele": modele, "schema": schema, "consigne": consigne,
                                                          "argv": argv}), encoding="utf-8")
    return cle


@pytest.fixture
def t(tmp_path):
    return temoin.preparer(tmp_path / "t", reponses=tmp_path / "reponses")


def test_une_manquante_payee_devient_une_reponse_rejouable(tmp_path, t):
    vrai = _vrai_faux(tmp_path)
    cle = _manquante(t, "claude-opus-5-5", '{"type": "object"}', "Une consigne",
                     ["-p", "--model", "claude-opus-5-5", "--json-schema", '{"type": "object"}'])
    bilan = temoin.reenregistrer(t, plafond=5, claude=vrai)
    assert bilan == {"payes": 1, "erreurs": 0, "restantes": 0, "cout_usd": 0.01}
    (recu,) = [json.loads(l) for l in (tmp_path / "recus.jsonl").read_text().splitlines()]
    assert recu == {"argv": ["-p", "--model", "claude-opus-5-5", "--json-schema", '{"type": "object"}'], "consigne": "Une consigne"}
    assert json.loads((t.reponses / f"{cle}.json").read_text()) == ENVELOPPE and not list(t.manquantes.iterdir())
    import subprocess
    r = subprocess.run(["claude", "-p", "--model", "claude-opus-5-5", "--json-schema", '{"type": "object"}'],
                       input="Une consigne", env=temoin.environnement(t), capture_output=True, text=True)
    assert (r.returncode, json.loads(r.stdout)) == (0, ENVELOPPE)              # le témoin la rejoue


def test_le_plafond_borne_les_appels_payes(tmp_path, t):
    vrai = _vrai_faux(tmp_path)
    for i in range(5):
        _manquante(t, "m", "", f"consigne {i}", ["-p"])
    assert temoin.reenregistrer(t, plafond=3, claude=vrai)["payes"] == 3
    assert len((tmp_path / "recus.jsonl").read_text().splitlines()) == 3 and len(list(t.manquantes.iterdir())) == 2


def test_chaque_appel_entre_au_registre_des_couts_sous_le_poste_temoin(tmp_path, t):
    vrai = _vrai_faux(tmp_path)
    _manquante(t, "m", "", "c", ["-p"])
    temoin.reenregistrer(t, plafond=1, claude=vrai)
    (l,) = [json.loads(x) for x in couts.REGISTRE.read_text().splitlines()]       # le registre du conftest
    assert (l["poste"], l["graphe"], l["cout_usd"]) == ("temoin", temoin.GRAPHE, 0.01)


@pytest.mark.parametrize("reponse, code", [({**ENVELOPPE, "is_error": True}, 0), (ENVELOPPE, 1)])
def test_une_reponse_en_erreur_ne_s_enregistre_pas(tmp_path, t, reponse, code):
    vrai = _vrai_faux(tmp_path, reponse, code)
    _manquante(t, "m", "", "c", ["-p"])
    assert temoin.reenregistrer(t, plafond=1, claude=vrai) == {"payes": 0, "erreurs": 1, "restantes": 1,
                                                              "cout_usd": 0.01}
    assert not t.reponses.exists() or not list(t.reponses.glob("*.json"))


def test_la_limite_d_usage_arrete_l_enregistrement(tmp_path, t, monkeypatch):
    vrai = _vrai_faux(tmp_path)
    _manquante(t, "m", "", "c", ["-p"])
    monkeypatch.setattr(couts, "en_limite", lambda: True)
    assert temoin.reenregistrer(t, plafond=5, claude=vrai)["payes"] == 0
    assert not (tmp_path / "recus.jsonl").exists()


def test_le_vrai_claude_n_est_pas_le_faux(tmp_path, t, monkeypatch):
    """Lancé depuis un terminal qui a le témoin en tête du PATH, l'enregistreur paierait son propre faux."""
    vrai = tmp_path / "vrai_bin"
    vrai.mkdir()
    (vrai / "claude").write_text("#!/bin/sh\n", encoding="utf-8")
    (vrai / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{t.bin}:{vrai}")
    assert temoin.vrai_claude() == vrai / "claude"
    monkeypatch.setenv("PATH", str(t.bin))
    assert temoin.vrai_claude() is None

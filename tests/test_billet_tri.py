"""Le billet trie, la fiche écrit (mainteneur, 18.11.2026 : « la publication des billets coûte cher pour une publication
éphémère remplacée par la fiche. je préférerais voir une relecture de la fiche, plutôt que du billet »).

Mesuré du 11 au 17.11.2026 sur un média à arbre des catégories : le billet coûtait 223 $, la fiche 276 $ ; 276 billets
sur 300 prenaient le titre et le chapeau de leur fiche. Le billet écrivait, relisait et traduisait un texte que la
fiche remplaçait dans la plupart des cas.

Pour un graphe qui déclare `arbre_fiches` :
1. le rédacteur du billet trie seulement : pertinent, doublon ou suite, rubrique ; il n'écrit ni titre ni texte ;
2. le billet n'a ni relecture, ni révision des titres, ni complément, ni version anglaise : la fiche écrit tout, et sa
   relecture adverse est la seule ;
3. le billet attend sa fiche dans la pile, sans limite de temps ; une fiche refusée ESSAIS fois le retire, et le refus
   va au journal des refus ;
4. la fiche s'écrit sur le budget de la rédaction, pas sur le budget secondaire.
Un graphe sans arbre garde le billet écrit et relu.
"""

from __future__ import annotations

import json

import pytest

from newskit import fil
from newskit import fil_anglais as fa
from newskit import fil_fiche as ff
from newskit import refus_frequents as rf

A_LIER = fil.a_lier  # la vraie : conftest.py la neutralise pendant les tests
ARBRE = {"arbre_fiches": "arbre.yaml", "fil_usd_jour": 100}
SANS_ARBRE = {"fil_usd_jour": 100}


def _declarer(monkeypatch, d: dict) -> None:
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: d)


# --- 1. Le rédacteur trie ---------------------------------------------------------------------------------------------


def _rediger(monkeypatch, d: dict):
    _declarer(monkeypatch, d)
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [{"nom": "Conformité"}])
    vu: dict = {"regles": 0}
    monkeypatch.setattr(fil.lt, "regles", lambda g=None: vu.update(regles=vu["regles"] + 1) or "REGLES\n")
    monkeypatch.setattr(fil.rf, "consigne", lambda g, maintenant=None: "")
    monkeypatch.setattr(fil, "extrait", lambda f, n=None: "source")

    def demander(inv, schema, graphe, note, **k):
        vu.update(inv=inv, schema=schema, note=note)
        return {"billets": [{"n": 0, "pertinent": True, "doublon_de": -1}]}, 0.25

    monkeypatch.setattr(fil, "demander", demander)
    reps, cout = fil.rediger([{"titre": "T", "chemin": "s.md"}], [], "g")
    return vu, reps, cout


def test_avec_arbre_le_redacteur_trie_sans_ecrire(monkeypatch):
    vu, reps, cout = _rediger(monkeypatch, ARBRE)
    assert vu["schema"] is fil.SCHEMA_TRI
    proprietes = fil.SCHEMA_TRI["properties"]["billets"]["items"]["properties"]
    assert "titre" not in proprietes and "texte" not in proprietes
    assert {"pertinent", "doublon_de", "developpement", "rubrique"} <= set(proprietes)
    assert "REGLES" not in vu["inv"] and vu["regles"] == 0  # les règles d'écriture vont à la fiche
    assert "Tu n'écris pas le billet" in vu["inv"] and "chapeau" not in vu["inv"]
    assert "Conformité" in vu["inv"]
    assert reps == [{"n": 0, "pertinent": True, "doublon_de": -1}] and cout == 0.25


def test_sans_arbre_le_redacteur_ecrit_toujours(monkeypatch):
    vu, _, _ = _rediger(monkeypatch, SANS_ARBRE)
    assert vu["schema"] is fil.SCHEMA
    assert "REGLES" in vu["inv"]


@pytest.mark.parametrize(("d", "attendu"), [(ARBRE, True), (SANS_ARBRE, False), ({"arbre_fiches": ""}, False)])
def test_le_tri_seul_suit_l_arbre_des_categories(monkeypatch, d, attendu):
    _declarer(monkeypatch, d)
    assert fil.tri_seul("g") is attendu


# --- 2. Le passage : ni relecture, ni révision, ni complément pour le billet trié ---------------------------------------


def _cand(u):
    return {"chemin": f"s/{u}.md", "url": u, "date": "2026-11-18", "titre": f"Titre source {u}", "editeur": "e"}


def _passer(monkeypatch, d, reps, F=None):
    F = F if F is not None else {"billets": [], "pile": [], "vues": [], "depense": {}}
    appels: dict[str, list] = {"relire": [], "reviser": [], "completer": [], "enrichir": [], "etoffer": []}
    _declarer(monkeypatch, d)
    monkeypatch.setattr(fil, "lire", lambda g: F)
    monkeypatch.setattr(fil, "ecrire", lambda g, f: None)
    monkeypatch.setattr(fil, "candidates", lambda g, vues, jour: [_cand("a")])
    monkeypatch.setattr(fil, "rediger", lambda lot, recents, g: (list(reps), 1.0))
    monkeypatch.setattr(fil, "proposer", lambda *a: None)
    monkeypatch.setattr(fil, "textes_du_registre", lambda F_: {})
    monkeypatch.setattr(fil, "rapprocher", lambda F_, g: 0.0)
    monkeypatch.setattr(fil, "relire", lambda b, g: appels["relire"].append(list(b)) or 0.0)
    monkeypatch.setattr(fil, "reviser_titres", lambda b, g: appels["reviser"].append(list(b)) or 0.0)
    monkeypatch.setattr(fil, "completer", lambda b, g, reste: appels["completer"].append(list(b)) or 0.0)
    monkeypatch.setattr(fil, "enrichir", lambda b: appels["enrichir"].append(b))
    monkeypatch.setattr(fil, "paraitre", lambda g, f: 0)
    monkeypatch.setattr(fil, "poser_langues", lambda b: 0)
    monkeypatch.setattr(fil, "poser_versions", lambda b: 0)
    monkeypatch.setattr(fil, "definir", lambda F_, g, reste: 0.0)
    monkeypatch.setattr(fil, "lier", lambda F_, g: 0.0)
    monkeypatch.setattr(fil, "extrait", lambda f, n=None: ("début de la source " * 40)[:n])
    monkeypatch.setattr(fil.fil_anglais(), "anglais", lambda F_, g, reste: 0.0)
    monkeypatch.setattr(fil.nv, "rattacher", lambda F_, c: 0)
    monkeypatch.setattr(fil, "maintenant", lambda: "2026-11-18 07:00")
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil.couts, "en_limite", lambda: False)
    monkeypatch.setattr(fil, "PART_SECONDAIRE", 0.0)  # le budget secondaire est épuisé
    monkeypatch.setattr(ff, "a_ecrire", lambda F_: [])
    monkeypatch.setattr(ff, "etoffer", lambda F_, g, reste: appels["etoffer"].append(reste) or 0.0)
    fil.passer("g")
    return F, appels


def test_un_billet_trie_attend_sa_fiche_sans_relecture(monkeypatch):
    F, appels = _passer(monkeypatch, ARBRE, [{"n": 0, "pertinent": True, "doublon_de": -1}])
    [b] = F["pile"]
    assert b["tri"] is True
    assert b["titre"] == "Titre source a"  # provisoire : la fiche donne le titre
    assert len(b["descriptif"]) <= fil.RECENT_MAX and b["descriptif"].startswith("début")
    assert appels["relire"] == [[]] and appels["reviser"] == [[]] and appels["completer"] == [[]]
    assert appels["enrichir"] == []


def test_la_fiche_s_ecrit_sur_le_budget_de_la_redaction(monkeypatch):
    _, appels = _passer(monkeypatch, ARBRE, [{"n": 0, "pertinent": True, "doublon_de": -1}])
    assert appels["etoffer"] == [pytest.approx(99.0)]  # plafond 100 $, rédaction 1 $ ; le budget secondaire est vide


def test_un_billet_trie_non_pertinent_n_entre_pas(monkeypatch):
    F, _ = _passer(monkeypatch, ARBRE, [{"n": 0, "pertinent": False, "doublon_de": -1}])
    assert F["pile"] == []


def test_sans_arbre_le_billet_est_relu_et_revise(monkeypatch):
    rep = {"n": 0, "pertinent": True, "doublon_de": -1, "titre": "T", "texte": "X"}
    F, appels = _passer(monkeypatch, SANS_ARBRE, [rep])
    [b] = F["pile"]
    assert "tri" not in b and b["titre"] == "T" and b["descriptif"] == "X"
    assert appels["relire"] == [[b]] and appels["reviser"] == [[b]] and appels["completer"] == [[b]]
    assert appels["enrichir"] == [b]
    assert appels["etoffer"] == [0.0]  # sur le budget secondaire, épuisé


def test_sans_arbre_un_billet_sans_texte_n_entre_pas(monkeypatch):
    F, _ = _passer(monkeypatch, SANS_ARBRE, [{"n": 0, "pertinent": True, "doublon_de": -1}])
    assert F["pile"] == []


# --- Les autres étapes laissent le billet trié à sa fiche --------------------------------------------------------------


def _trie(u="a", **k):
    return {
        "titre": "Titre source",
        "descriptif": "d",
        "chemin": f"s/{u}.md",
        "ajoute": "2026-11-18 06:00",
        "tri": True,
        **k,
    }


TENUE = {"tenu": True, "titre": "T", "resume": "R", "en": {"titre": "T", "resume": "R"}}


def test_la_relecture_des_billets_d_avant_saute_le_billet_trie():
    ancien = {"titre": "T", "descriptif": "d", "chemin": "s/v.md"}
    assert fil.a_relire({"pile": [_trie(), ancien], "billets": []}) == [ancien]


def test_l_anglais_ne_s_ecrit_pas_pour_un_billet_trie(monkeypatch):
    monkeypatch.setattr(fa, "existe", lambda c: True)
    ancien = {"titre": "T", "descriptif": "d", "chemin": "s/v.md"}
    assert fa.a_ecrire({"pile": [_trie(), ancien], "billets": []}) == [ancien]


def test_la_liaison_attend_la_fiche_du_billet_trie():
    sans, avec = _trie("a"), _trie("b", fiche=dict(TENUE))
    assert A_LIER({"pile": [sans, avec], "billets": []}) == [avec]


# --- 3. La parution attend la fiche tenue -----------------------------------------------------------------------------


@pytest.fixture
def pile(monkeypatch, tmp_path):
    monkeypatch.setattr(fil, "JOURNAL_REFUS", tmp_path / "fil_refus.jsonl")
    monkeypatch.setattr(ff, "existe", lambda c: c != "s/perdu.md")
    monkeypatch.setattr(ff, "en_attente", lambda F, g, quand=None: [])  # l'attente des 20 minutes est passée
    monkeypatch.setattr(fil, "maintenant", lambda: "2026-11-18 12:00")
    monkeypatch.setattr(fil, "cadence_du_moment", lambda g, n, q: 1.0)
    monkeypatch.setattr(fil, "fil_max", lambda g: 300)
    monkeypatch.setattr(fil, "PILE_PAR_PASSAGE", 10)
    return tmp_path / "fil_refus.jsonl"


def _F(*pile_):
    return {"billets": [], "pile": list(pile_), "paru_le": "2026-11-18 09:00", "vues": []}


def test_un_billet_trie_sans_fiche_reste_dans_la_pile(pile):
    b = _trie()
    F = _F(b)
    assert fil.paraitre("g", F) == 0
    assert F["pile"] == [b] and F["billets"] == []


def test_un_billet_trie_dont_la_fiche_est_en_essai_reste_dans_la_pile(pile):
    b = _trie(fiche={"tenu": False, "reproches": ["pas clair"], "essais": ff.ESSAIS - 1})
    F = _F(b)
    fil.paraitre("g", F)
    assert F["pile"] == [b]


def test_un_billet_trie_paraît_avec_sa_fiche_tenue(pile):
    b = _trie(fiche=dict(TENUE))
    F = _F(b)
    assert fil.paraitre("g", F) == 1
    assert F["billets"] == [b] and F["pile"] == []


def test_une_fiche_refusee_trois_fois_retire_le_billet_et_va_au_journal(pile):
    refuse = _trie("r", fiche={"tenu": False, "reproches": ["absent de la source : X"], "essais": ff.ESSAIS})
    perdu = _trie("perdu")
    F = _F(refuse, perdu)
    assert fil.paraitre("g", F) == 0
    assert F["pile"] == [] and F["billets"] == []
    lignes = [json.loads(x) for x in pile.read_text(encoding="utf-8").splitlines()]
    assert [x["chemin"] for x in lignes] == ["s/r.md", "s/perdu.md"]
    assert lignes[0]["graphe"] == "g" and lignes[0]["etape"] == "fiche"
    assert lignes[0]["essais"] == [{"reproches": ["absent de la source : X"]}]
    assert lignes[1]["essais"] == [{"reproches": ["source illisible"]}]


def test_le_refus_d_une_fiche_retiree_reste_dans_les_refus_frequents(pile, monkeypatch, tmp_path):
    refuse = _trie("r", fiche={"tenu": False, "reproches": ["absent de la source : X"], "essais": ff.ESSAIS})
    F = _F(refuse)
    fil.paraitre("g", F)
    (tmp_path / "fil.json").write_text(json.dumps(F), encoding="utf-8")
    monkeypatch.setattr(fil, "chemin_fil", lambda g: tmp_path / "fil.json")
    assert ("2026-11-18 12:00", "absent de la source : X") in rf._entrees("g")


def test_un_billet_d_avant_le_tri_garde_son_attente(pile):
    ancien = {"titre": "T", "descriptif": "d", "chemin": "s/v.md", "ajoute": "2026-11-18 06:00"}
    refuse = {**ancien, "chemin": "s/w.md", "fiche": {"tenu": False, "reproches": ["x"], "essais": ff.ESSAIS}}
    F = _F(ancien, refuse)
    assert fil.paraitre("g", F) == 2  # sans fiche tenue : ils paraissent après l'attente, avec leur texte relu
    assert not pile.exists()

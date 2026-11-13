"""L'angle des suites (mainteneur, 13.11.2026 : « un fait nouveau peut tout à fait justifier une nouvelle fiche, à
condition d'en faire l'angle de la fiche, et de la titrer adéquatement »).

Le 13.11.2026 le fil a publié trois billets sur le départ de Jensen Huang : Al Jazeera le 12 au soir, puis Fortune et
Gary Marcus le 13 au matin, chacun titré sur le départ. Les deux derniers étaient des suites, et leur fait neuf
(l'intérim de Colette Kress) n'était ni dans leur titre ni dans leur première phrase. Quatre défauts :
1. la relecture et la révision des titres ignoraient le fait neuf d'une suite ;
2. le rattrapage faisait d'un billet une suite sans le faire réécrire ;
3. deux suites qui apportent le même fait n'étaient jamais comparées entre elles ;
4. le fait neuf se mesurait contre le seul billet d'avant, et le nom de l'auteur cité (« d'après Gary Marcus ») passait
   pour un fait.
"""

from __future__ import annotations

import json

import pytest

from newskit import fil
from newskit import nouvelles as nv

ALJAZEERA = (
    "Jensen Huang quitte la direction de Nvidia après un scandale d'espionnage chinois. Le conseil d'administration "
    "de Nvidia a annoncé la démission de Jensen Huang, 63 ans, le 12 novembre 2026."
)
DEV_KRESS = "Colette Kress, directrice financière, assure l'intérim à la direction de Nvidia, d'après Gary Marcus."


def _b(u, texte="", ajoute="2026-11-12 19:58", editeur="e", **k):
    return {
        "titre": "T " + u,
        "descriptif": texte or "D " + u,
        "url": u,
        "chemin": f"s/{u}.md",
        "date": "2026-11-12",
        "ajoute": ajoute,
        "tenu": True,
        "rapproche": True,
        "source": {"titre": "source " + u, "editeur": editeur, "date": "2026-11-12", "url": u},
        **k,
    }


def _F(*billets, pile=()):
    return {"billets": list(billets), "pile": list(pile), "vues": [], "depense": {}}


# --- 4. Le fait neuf : contre toute l'affaire, sans le nom de l'auteur cité --------------------------------------------


def test_le_nom_de_l_auteur_cite_n_est_pas_un_fait_neuf():
    avant = ALJAZEERA + " Colette Kress, la directrice financière, assure l'intérim."
    assert nv.termes_neufs(DEV_KRESS, avant) == []  # seul « Gary Marcus » manquait : c'est l'attribution
    assert nv.fait_neuf(DEV_KRESS, avant) is False
    # Le fait neuf, sans l'auteur ; « Colette » ouvre la phrase et ne compte pas comme nom (nouvelles._noms).
    assert nv.termes_neufs(DEV_KRESS, ALJAZEERA) == ["kress"]
    assert nv.termes_neufs("L'intérim revient à Colette Kress, d'après Gary Marcus.", ALJAZEERA) == ["colette", "kress"]
    assert nv.termes_neufs("Selon Fortune Magazine, Huang a démissionné.", ALJAZEERA) == []
    assert nv.termes_neufs("According to Jeremy Kahn, Huang resigned.", ALJAZEERA) == []
    assert nv.termes_neufs("Selon lui, Harvey Jones préside le conseil.", ALJAZEERA) == ["harvey", "jones"]


def test_l_editeur_de_la_source_n_est_pas_un_fait_neuf():
    dev = "Gary Marcus estime que Nvidia a contourné la politique américaine."
    assert nv.termes_neufs(dev, ALJAZEERA) == ["marcus"]
    assert nv.termes_neufs(dev, ALJAZEERA, auteurs=["Gary Marcus"]) == []
    assert nv.fait_neuf(dev, ALJAZEERA, auteurs=["Gary Marcus"]) is False


def test_un_nombre_neuf_reste_un_fait_neuf():
    assert nv.termes_neufs("L'action perd 13 % à l'ouverture, d'après Gary Marcus.", ALJAZEERA) == ["#13%"]


def test_l_affaire_suit_les_suites_et_le_contexte():
    a, s1, s2 = _b("a"), _b("s1", suite_de="s/a.md"), _b("s2", suite_de="s/s1.md")
    c = _b("c", contexte="s/a.md")
    a["contexte"] = "s/a.md"
    autre = _b("autre")
    F = _F(a, s1, c, autre, pile=[s2])
    for x in (a, s1, s2, c):
        assert {m["chemin"] for m in nv.affaire(F, x)} == {"s/a.md", "s/s1.md", "s/s2.md", "s/c.md"}
    assert [m["chemin"] for m in nv.affaire(F, autre)] == ["s/autre.md"]


def test_le_billet_dit_le_fait_neuf():
    assert nv.dit_le_fait("Colette Kress assure l'intérim de Nvidia", ["colette", "kress"]) is True
    assert nv.dit_le_fait("Jensen Huang quitte la direction de Nvidia", ["colette", "kress"]) is False
    assert nv.dit_le_fait("L'action Nvidia perd 13 %", ["#13%"]) is True
    assert nv.dit_le_fait("L'action Nvidia perd 4 %", ["#13%"]) is False
    assert nv.dit_le_fait("Kressman parle", ["kress"]) is False  # un mot entier, pas un morceau


def _passer(monkeypatch, F, cands, reps, textes, relire=None):
    monkeypatch.setattr(fil, "lire", lambda g: F)
    monkeypatch.setattr(fil, "ecrire", lambda g, f: None)
    monkeypatch.setattr(fil, "candidates", lambda g, vues, jour: cands)
    monkeypatch.setattr(fil, "rediger", lambda lot, recents, g: (list(reps), 0.0))
    monkeypatch.setattr(fil, "proposer", lambda *a: None)
    monkeypatch.setattr(fil, "textes_du_registre", lambda F_: textes)
    monkeypatch.setattr(fil, "rapprocher", lambda F_, g: 0.0)
    monkeypatch.setattr(fil, "relire", relire or (lambda billets, g: 0.0))
    monkeypatch.setattr(fil, "reviser_titres", lambda b, g: 0.0)
    monkeypatch.setattr(fil, "completer", lambda b, g, reste: 0.0)
    monkeypatch.setattr(fil, "enrichir", lambda b: None)
    monkeypatch.setattr(fil, "paraitre", lambda g, f: 0)
    monkeypatch.setattr(fil, "poser_langues", lambda b: 0)
    monkeypatch.setattr(fil, "poser_versions", lambda b: 0)
    monkeypatch.setattr(fil, "definir", lambda F_, g, reste: 0.0)
    monkeypatch.setattr(fil, "lier", lambda F_, g: 0.0)
    monkeypatch.setattr(fil.fil_anglais(), "anglais", lambda F_, g, reste: 0.0)
    monkeypatch.setattr(fil.nv, "rattacher", lambda F_, c: 0)
    monkeypatch.setattr(fil, "maintenant", lambda: "2026-11-13 07:00")
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [])
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {"fil_usd_jour": 100})
    monkeypatch.setattr(fil.couts, "en_limite", lambda: False)
    from newskit import fil_fiche as ff

    monkeypatch.setattr(ff, "a_ecrire", lambda F_: [])
    monkeypatch.setattr(ff, "etoffer", lambda F_, g, reste: 0.0)
    fil.passer("g")


def _cand(u, editeur="e"):
    return {"chemin": f"s/{u}.md", "url": u, "date": "2026-11-12", "titre": u, "editeur": editeur, "texte": "t"}


def _rep(dev, titre="N", doublon_de=0):
    return {"n": 0, "pertinent": True, "titre": titre, "texte": "t", "doublon_de": doublon_de, "developpement": dev}


def test_un_fait_deja_dit_par_une_suite_de_l_affaire_s_attache_a_cette_suite(monkeypatch):
    a = _b("a", ALJAZEERA)
    s1 = _b("s1", "Colette Kress assure l'intérim.", ajoute="2026-11-13 06:29", suite_de="s/a.md")
    F = _F(a, s1)
    textes = {"s/a.md": ALJAZEERA, "s/s1.md": "Colette Kress assure l'intérim de Nvidia."}
    # Le fil est rangé par date puis par heure d'entrée : s1 vient en tête, la nouvelle d'Al Jazeera est la seconde.
    rep = _rep("Colette Kress dirige Nvidia par intérim.", doublon_de=1)
    _passer(monkeypatch, F, [_cand("fortune")], [rep], textes)
    assert F["pile"] == []  # pas de billet : le fait est déjà au registre
    assert nv.nouvelle_de(F, "s/fortune.md") is s1  # la source rejoint la suite qui dit ce fait


def test_un_developpement_qui_nomme_seulement_l_auteur_cite_s_attache(monkeypatch):
    a = _b("a", ALJAZEERA)
    F = _F(a)
    dev = "Jensen Huang a démissionné de Nvidia, d'après Gary Marcus."
    _passer(monkeypatch, F, [_cand("marcus", editeur="Gary Marcus")], [_rep(dev)], {"s/a.md": ALJAZEERA})
    assert F["pile"] == [] and nv.nouvelle_de(F, "s/marcus.md") is a


def test_l_editeur_de_la_fiche_n_est_pas_un_fait_neuf_au_passage(monkeypatch):
    a = _b("a", ALJAZEERA)
    F = _F(a)
    dev = "Pour Gary Marcus, Jensen Huang a soutenu l'IA chinoise."
    assert nv.termes_neufs(dev, ALJAZEERA) == ["gary", "marcus"]  # sans l'éditeur de la fiche, un fait neuf
    _passer(monkeypatch, F, [_cand("marcus", editeur="Gary Marcus")], [_rep(dev)], {"s/a.md": ALJAZEERA})
    assert F["pile"] == [] and nv.nouvelle_de(F, "s/marcus.md") is a


def test_une_suite_redigee_porte_ses_termes_neufs_et_reste_a_rapprocher(monkeypatch):
    a = _b("a", ALJAZEERA)
    F = _F(a)
    _passer(
        monkeypatch,
        F,
        [_cand("marcus")],
        [_rep(DEV_KRESS, titre="Colette Kress assure l'intérim")],
        {"s/a.md": ALJAZEERA},
    )
    (s,) = F["pile"]
    assert s["suite_de"] == "s/a.md" and s["neuf"] == ["kress"]
    assert s["rapproche"] is False  # le rattrapage la compare aux autres suites de l'affaire


# --- 1. La relecture et la révision des titres tiennent l'angle d'une suite --------------------------------------------


@pytest.fixture
def relecture(monkeypatch):
    appels = []
    reecritures = []

    def demander(inv, schema, graphe, note, modele=None):
        appels.append((note, inv))
        lignes = [json.loads(x) for x in inv.split("\n") if x.startswith('{"n"')]
        if note == "relecteur":
            return {"billets": [{"n": x["n"], "absents": [], "clair": True} for x in lignes]}, 0.01
        if note == "réécriture":
            return {"billets": [{"n": x["n"], **reecritures.pop(0)} for x in lignes]}, 0.02
        raise AssertionError(note)

    monkeypatch.setattr(fil, "demander", demander)
    monkeypatch.setattr(fil.lt, "charger_fiches", lambda chemins: {c: {"chemin": c} for c in chemins})
    monkeypatch.setattr(fil, "forme", lambda t, x, f: [])
    monkeypatch.setattr(fil, "doutes", lambda t, x, f: [])
    monkeypatch.setattr(fil, "extrait", lambda f, n=1200: "source")
    monkeypatch.setattr(fil.lt, "regles", lambda g=None, langue="fr": "")
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {})
    monkeypatch.setattr(fil, "journaliser_refus", lambda g, r: None)
    return appels, reecritures


def _suite(titre, texte="Jensen Huang a quitté Nvidia. Colette Kress assure l'intérim."):
    return _b(
        "fortune",
        texte,
        ajoute="2026-11-13 07:00",
        titre=titre,
        suite_de="s/a.md",
        developpement="Colette Kress assure l'intérim.",
        neuf=["colette", "kress"],
    )


def test_un_billet_qui_n_est_pas_une_suite_n_a_pas_d_angle_a_tenir(relecture):
    appels, _ = relecture
    b = _suite("Jensen Huang quitte la direction de Nvidia")
    del b["suite_de"]  # des termes neufs sans nouvelle d'origine : pas une suite
    fil.relire([b], "g")
    assert [n for n, _ in appels] == ["relecteur"] and b["tenu"] is True


def test_une_suite_titree_sur_l_evenement_d_avant_est_reecrite_sur_son_fait_neuf(relecture):
    appels, reecritures = relecture
    s = _suite("Jensen Huang quitte la direction de Nvidia")
    del s["tenu"]
    neuf = "Colette Kress assure l'intérim de Nvidia après Jensen Huang"
    reecritures.append({"titre": neuf, "texte": "Colette Kress assure l'intérim de Nvidia. Huang est parti."})
    fil.relire([s], "g")
    assert s["titre"] == neuf and s["tenu"] is True
    (inv,) = [i for n, i in appels if n == "réécriture"]
    x = json.loads(next(ligne for ligne in inv.split("\n") if ligne.startswith('{"n"')))
    assert x["developpement"] == "Colette Kress assure l'intérim."
    assert any("fait neuf" in r for r in x["reproches"])


def test_la_reecriture_recoit_les_mots_du_fait_neuf_a_nommer(relecture):
    appels, reecritures = relecture
    s = _suite("Jensen Huang quitte la direction de Nvidia")
    reecritures.append(
        {"titre": "Colette Kress assure l'intérim de Nvidia", "texte": "Colette Kress assure l'intérim."}
    )
    fil.relire([s], "g")
    (inv,) = [i for n, i in appels if n == "réécriture"]
    x = json.loads(next(ligne for ligne in inv.split("\n") if ligne.startswith('{"n"')))
    assert fil.reproche_angle(s, "fr") in x["reproches"] and "(Colette Kress)" in fil.reproche_angle(s, "fr")


def test_une_suite_dont_la_premiere_phrase_tait_le_fait_neuf_est_reecrite(relecture):
    appels, reecritures = relecture
    s = _suite("Colette Kress assure l'intérim de Nvidia")  # le titre le dit, la première phrase non
    reecritures.append({"titre": s["titre"], "texte": "Colette Kress assure l'intérim. Huang est parti."})
    fil.relire([s], "g")
    assert s["descriptif"] == "Colette Kress assure l'intérim. Huang est parti."


def test_une_suite_dont_seul_le_titre_tait_le_fait_neuf_est_reecrite(relecture):
    _, reecritures = relecture
    s = _suite("Jensen Huang quitte la direction de Nvidia", "Colette Kress assure l'intérim. Huang est parti.")
    reecritures.append({"titre": "Colette Kress assure l'intérim de Nvidia", "texte": s["descriptif"]})
    fil.relire([s], "g")
    assert s["titre"] == "Colette Kress assure l'intérim de Nvidia"


def test_une_reecriture_encore_sans_angle_est_reprise(relecture):
    appels, reecritures = relecture
    s = _suite("Jensen Huang quitte la direction de Nvidia")
    reecritures += [
        {"titre": "Jensen Huang part", "texte": "Jensen Huang a quitté Nvidia."},
        {"titre": "Colette Kress assure l'intérim de Nvidia", "texte": "Colette Kress assure l'intérim. Huang part."},
    ]
    fil.relire([s], "g")
    assert [n for n, _ in appels].count("réécriture") == 2
    assert s["titre"] == "Colette Kress assure l'intérim de Nvidia" and s["tenu"] is True


def test_une_suite_qui_dit_son_fait_neuf_n_est_pas_reecrite(relecture):
    appels, _ = relecture
    s = _suite("Colette Kress assure l'intérim de Nvidia", "Colette Kress assure l'intérim. Huang est parti.")
    fil.relire([s], "g")
    assert [n for n, _ in appels] == ["relecteur"] and s["tenu"] is True


def test_une_suite_que_la_reecriture_ne_met_pas_sur_son_angle_se_fond_dans_sa_nouvelle(relecture):
    _, reecritures = relecture
    a = _b("a", ALJAZEERA)
    s = _suite("Jensen Huang quitte la direction de Nvidia")
    F = _F(a, s)
    reecritures += [{"titre": "Jensen Huang part", "texte": "Jensen Huang a quitté Nvidia."}] * fil.REECRITURES
    fil.relire([s], "g")
    assert s["tenu"] is False and s["sans_angle"] is True
    assert fil.fondre_sans_angle(F, [s]) == 1
    assert F["billets"] == [a] and nv.nouvelle_de(F, "s/fortune.md") is a
    assert s["fondu_dans"] == "s/a.md"


def test_un_billet_non_tenu_qui_n_est_pas_une_suite_ne_se_fond_pas(relecture):
    _, reecritures = relecture
    a = _b("a", ALJAZEERA)
    s = _suite("Jensen Huang quitte la direction de Nvidia")
    s["sans_angle"] = True  # sans origine au registre : rien où se fondre
    s["tenu"] = False
    F = _F(a, s)
    s["suite_de"] = "s/inconnue.md"
    assert fil.fondre_sans_angle(F, [s]) == 0 and "fondu_dans" not in s
    s["suite_de"], s["tenu"] = "s/a.md", True  # tenue : elle reste
    assert fil.fondre_sans_angle(F, [s]) == 0
    x = _b("x", tenu=False, suite_de="s/a.md")  # non tenue, sans `sans_angle` : hors du fil, sans fonte
    F["billets"].append(x)
    assert fil.fondre_sans_angle(F, [x]) == 0 and x in F["billets"]


def test_au_passage_une_suite_sans_angle_ne_parait_pas_et_rejoint_sa_nouvelle(monkeypatch):
    a = _b("a", ALJAZEERA)
    F = _F(a)

    def relire(billets, g):
        for b in billets:
            b["tenu"], b["sans_angle"] = False, True
        return 0.0

    _passer(monkeypatch, F, [_cand("marcus")], [_rep(DEV_KRESS)], {"s/a.md": ALJAZEERA}, relire=relire)
    assert F["pile"] == [] and nv.nouvelle_de(F, "s/marcus.md") is a


def _titres(monkeypatch, candidats, choix):
    def demander(inv, schema, graphe, note, modele=None):
        if "candidats" in schema["properties"]["titres"]["items"]["properties"]:
            return {"titres": [{"n": 0, "candidats": candidats}]}, 0.02
        return {"titres": [{"n": 0, "choix": choix, "raison": "r"}]}, 0.01

    monkeypatch.setattr(fil, "demander", demander)
    monkeypatch.setattr(fil.lt, "charger_fiches", lambda chemins: {c: {"chemin": c} for c in chemins})
    monkeypatch.setattr(fil, "_source_de", lambda f: "Jensen Huang Nvidia Colette Kress")
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {})


def test_la_revision_ne_retire_pas_le_fait_neuf_du_titre_d_une_suite(monkeypatch):
    s = _suite("Colette Kress assure l'intérim de Nvidia")
    _titres(monkeypatch, ["Jensen Huang quitte Nvidia", "Nvidia sans Huang", "Huang part"], 1)
    fil.reviser_titres([s], "g")
    assert s["titre"] == "Colette Kress assure l'intérim de Nvidia"
    assert s["revision_titre"]["refus"] == "le titre ne dit pas le fait neuf"


def test_la_revision_d_un_billet_qui_n_est_pas_une_suite_ne_cherche_pas_de_fait_neuf(monkeypatch):
    b = _b("x", titre="Colette Kress assure l'intérim de Nvidia", neuf=["colette", "kress"])
    _titres(monkeypatch, ["Jensen Huang quitte Nvidia", "Nvidia sans Huang", "Huang part"], 1)
    fil.reviser_titres([b], "g")
    assert b["titre"] == "Jensen Huang quitte Nvidia"


def test_les_candidats_d_une_suite_recoivent_son_fait_neuf(monkeypatch):
    s = _suite("Colette Kress assure l'intérim de Nvidia")
    vus = []
    _titres(monkeypatch, ["a", "b", "c"], 0)
    demander = fil.demander
    monkeypatch.setattr(fil, "demander", lambda inv, *a, **k: vus.append(inv) or demander(inv, *a, **k))
    fil.reviser_titres([s], "g")
    (x,) = [json.loads(ligne) for ligne in vus[0].split("\n") if ligne.startswith('{"n"')]
    assert x["developpement"] == "Colette Kress assure l'intérim."


# --- 2 et 3. Le rattrapage : une suite se fait réécrire ; deux suites du même fait se fondent -------------------------


def _rapprocher(monkeypatch, F, textes, liens):
    vus = []

    def demander(inv, schema, graphe, note, modele=None):
        vus.append(inv)
        return {"paires": [{"n": n, **x} for n, x in enumerate(liens)]}, 0.01

    monkeypatch.setattr(fil, "demander", demander)
    monkeypatch.setattr(fil, "textes_du_registre", lambda F_: textes)
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda g: {})
    fil.rapprocher(F, "g")
    return vus


def test_le_rattrapage_fait_relire_la_nouvelle_qu_il_change_en_suite(monkeypatch):
    a = _b("a", ALJAZEERA)
    b = _b("b", "Colette Kress dirige Nvidia.", ajoute="2026-11-13 07:00", rapproche=False)
    F = _F(a, b)
    textes = {"s/a.md": ALJAZEERA + " Jensen Huang Nvidia", "s/b.md": "Colette Kress Jensen Huang Nvidia 63"}
    _rapprocher(monkeypatch, F, textes, [{"lien": "suite", "developpement": "Colette Kress assure l'intérim."}])
    assert b["suite_de"] == "s/a.md" and b["neuf"] == ["kress"]
    assert "tenu" not in b and b in fil.a_relire(F)  # relue, donc réécrite sur son fait neuf


def test_le_rattrapage_ne_fait_pas_une_suite_d_un_fait_deja_dit_par_l_affaire(monkeypatch):
    a = _b("a", ALJAZEERA)
    s1 = _b("s1", "Colette Kress assure l'intérim.", ajoute="2026-11-13 06:29", suite_de="s/a.md")
    b = _b("b", "Colette Kress dirige Nvidia.", ajoute="2026-11-13 07:00", rapproche=False)
    F = _F(a, s1, b)
    textes = {
        "s/a.md": ALJAZEERA + " Jensen Huang Nvidia",
        "s/s1.md": "Colette Kress assure l'intérim de Nvidia.",
        "s/b.md": "Colette Kress Jensen Huang Nvidia 63",
    }
    _rapprocher(monkeypatch, F, textes, [{"lien": "suite", "developpement": "Colette Kress assure l'intérim."}] * 3)
    assert "suite_de" not in b and nv.nouvelle_de(F, "s/b.md") is not b  # fondue : le fait était dit


def test_deux_suites_du_meme_fait_sont_comparees_et_se_fondent(monkeypatch):
    a = _b("a", ALJAZEERA)
    s1 = _b(
        "s1",
        "Colette Kress assure l'intérim.",
        ajoute="2026-11-13 06:29",
        suite_de="s/a.md",
        developpement="Colette Kress assure l'intérim.",
    )
    s2 = _b(
        "s2",
        "Colette Kress dirige Nvidia.",
        ajoute="2026-11-13 07:00",
        suite_de="s/a.md",
        developpement="Colette Kress et Harvey Jones dirigent Nvidia.",
    )
    F = _F(a, s1, s2)  # toutes rapprochées : seule la paire des deux suites reste à juger
    textes = {"s/a.md": "rien", "s/s1.md": "rien", "s/s2.md": "rien"}
    assert fil.paires_a_juger(F, textes) == [(s1, s2)]
    vus = _rapprocher(monkeypatch, F, textes, [{"lien": "meme"}])
    assert "Colette Kress et Harvey Jones dirigent Nvidia." in vus[0]  # le juge voit le fait neuf de chacune
    assert F["billets"] == [a, s1] and nv.nouvelle_de(F, "s/s2.md") is s1


def test_deux_suites_de_faits_distincts_ne_se_fondent_pas_et_ne_reviennent_pas(monkeypatch):
    a = _b("a", ALJAZEERA)
    s1 = _b("s1", ajoute="2026-11-13 06:29", suite_de="s/a.md", developpement="Colette Kress assure l'intérim.")
    s2 = _b("s2", ajoute="2026-11-13 07:00", suite_de="s/a.md", developpement="Le Congrès convoque Huang.")
    F = _F(a, s1, s2)
    textes = {"s/a.md": "rien", "s/s1.md": "rien", "s/s2.md": "rien"}
    _rapprocher(monkeypatch, F, textes, [{"lien": "autre"}])
    assert F["billets"] == [a, s1, s2] and s2["suite_de"] == "s/a.md"
    assert fil.paires_a_juger(F, textes) == []


def test_une_suite_jugee_meme_qu_une_nouvelle_hors_de_son_affaire_garde_son_origine(monkeypatch):
    a, x = _b("a", ALJAZEERA), _b("x", "Colette Kress Harvey Jones", ajoute="2026-11-12 08:00")
    s = _b("s", ajoute="2026-11-13 07:00", suite_de="s/a.md", rapproche=False)
    F = _F(x, a, s)
    textes = {"s/a.md": "rien", "s/x.md": "Colette Kress Harvey Jones", "s/s.md": "Colette Kress Harvey Jones"}
    _rapprocher(monkeypatch, F, textes, [{"lien": "meme"}])
    assert s["suite_de"] == "s/a.md" and s in F["billets"] and "s/x.md" in s["comparee_a"]


def test_le_rattrapage_ne_compte_ni_la_nouvelle_jugee_ni_celles_entrees_apres(monkeypatch):
    a = _b("a", ALJAZEERA, contexte="s/a.md")
    b = _b("b", ajoute="2026-11-13 07:00", rapproche=False, contexte="s/a.md")  # dans l'affaire par le contexte
    s3 = _b("s3", ajoute="2026-11-13 08:00", suite_de="s/a.md", contexte="s/a.md")
    F = _F(a, b, s3)
    textes = {
        "s/a.md": ALJAZEERA + " Jensen Huang Nvidia",
        "s/b.md": "Colette Kress Jensen Huang Nvidia 63",  # son propre texte dit le fait : il ne compte pas
        "s/s3.md": "Colette Kress assure l'intérim.",  # entrée après b : ne compte pas
    }
    _rapprocher(monkeypatch, F, textes, [{"lien": "suite", "developpement": "Colette Kress assure l'intérim."}] * 3)
    assert b["suite_de"] == "s/a.md" and b["neuf"] == ["kress"]


def test_le_rattrapage_ne_prend_pas_l_editeur_de_la_source_pour_un_fait_neuf(monkeypatch):
    a = _b("a", ALJAZEERA)
    b = _b("b", "Jensen Huang Nvidia 63", ajoute="2026-11-13 07:00", rapproche=False, editeur="Gary Marcus")
    F = _F(a, b)
    textes = {"s/a.md": ALJAZEERA + " Jensen Huang Nvidia", "s/b.md": "Jensen Huang Nvidia 63"}
    _rapprocher(monkeypatch, F, textes, [{"lien": "suite", "developpement": "Pour Gary Marcus, Huang a démissionné."}])
    assert "suite_de" not in b and nv.nouvelle_de(F, "s/b.md") is a


def test_les_auteurs_d_une_fiche_et_d_une_nouvelle():
    assert fil.auteurs_de({"editeur": "Fortune"}) == ["Fortune"]
    n = {"source": {"editeur": "Gary Marcus"}, "sources_plus": [{"editeur": "Fortune"}, {"editeur": ""}]}
    assert fil.auteurs_de(n) == ["Gary Marcus", "Fortune"]

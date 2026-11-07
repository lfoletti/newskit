"""La naissance d'un graphe (CHANTIER_GENERIQUE.md § 4.1, pilote CortexDB phase B) : le jeu de questions est scellé avant
toute collecte, une page citée par le sceau n'entre jamais, et la dépense se lit au registre des coûts."""

import pytest

from newskit import naissance as na

JEU = [
    {"i": 0, "question": "Q1 ?", "attendu": "R1", "citation": "voir https://ex.org/a/ et https://ex.org/b"},
    {"i": 1, "question": "Q2 ?", "attendu": "R2", "citation": "", "source": "http://www.ex.org/c?x=1"},
]


def test_le_sceau_garde_l_empreinte_les_adresses_et_la_date():
    s = na.sceller(JEU, "2026-10-22 21:30")
    assert s["questions"] == 2 and s["scelle_le"] == "2026-10-22 21:30"
    assert s["adresses"] == ["ex.org/a", "ex.org/b", "ex.org/c?x=1"]  # normalisées, triées
    assert s["empreinte"] == na.sceller(list(reversed(JEU)), "autre date")["empreinte"]  # l'ordre ne compte pas
    assert s["empreinte"] != na.sceller([{**JEU[0], "attendu": "autre"}, JEU[1]], "x")["empreinte"]


def test_un_jeu_vide_ne_se_scelle_pas():
    with pytest.raises(ValueError):
        na.sceller([], "2026-10-22")


def test_une_fiche_dont_l_adresse_est_citee_par_le_sceau_est_contaminee():
    s = na.sceller(JEU, "2026-10-22")
    fiches = [
        ("f1.md", "https://ex.org/a"),
        ("f2.md", "https://ex.org/autre"),
        ("f3.md", "https://www.ex.org/c?x=1#h"),
        ("f4.md", ""),
    ]
    assert na.contaminees(fiches, s) == ["f1.md", "f3.md"]


def test_un_jeu_modifie_apres_le_sceau_se_voit():
    s = na.sceller(JEU, "2026-10-22")
    assert na.intact(JEU, s)
    assert not na.intact(JEU[:1], s)


def test_la_depense_ne_compte_que_le_graphe_depuis_le_sceau():
    lignes = [
        {"quand": "2026-10-22 21:00:00", "graphe": "crooners", "cout_usd": 5.0},
        {"quand": "2026-10-22 21:31:00", "graphe": "crooners", "cout_usd": 1.25},
        {"quand": "2026-10-22 21:40:00", "graphe": "ai-audio", "cout_usd": 9.0},
        {"quand": "2026-10-22 22:00:00", "graphe": "crooners", "cout_usd": 0.5},
    ]
    assert na.depense(lignes, "crooners", "2026-10-22 21:30") == 1.75


def test_le_reste_du_budget_n_est_jamais_negatif():
    assert na.reste(15.0, 16.2) == 0.0
    assert na.reste(15.0, 3.5) == 11.5


def test_une_citation_mal_formee_ne_scelle_pas_d_adresse_vide_et_ne_touche_pas_les_fiches_sans_adresse():
    s = na.sceller([{"question": "Q ?", "attendu": "R", "citation": "voir https://) et http://."}], "x")
    assert s["adresses"] == []
    assert na.contaminees([("f.md", "")], {**s, "adresses": [""]}) == []


def test_la_quarantaine_deplace_une_fiche_au_chemin_relatif_ou_absolu_et_rend_son_chemin_relatif(tmp_path):
    # 22.10.2026 : dossiers_du_graphe rend des chemins relatifs ; relative_to(RACINE) levait après le déplacement, et
    # l'état ne notait pas la fiche de Jean Sablon mise en quarantaine.
    for nom in ("a.md", "b.md"):
        (tmp_path / "sources" / "canon").mkdir(parents=True, exist_ok=True)
        (tmp_path / "sources" / "canon" / nom).write_text("x")
    cible = tmp_path / "quarantaine"
    notes = na.mettre_en_quarantaine(
        ["sources/canon/a.md", str(tmp_path / "sources" / "canon" / "b.md")], tmp_path, cible
    )
    assert notes == ["sources/canon/a.md", "sources/canon/b.md"]
    assert sorted(p.name for p in cible.iterdir()) == ["a.md", "b.md"]
    assert not list((tmp_path / "sources" / "canon").iterdir())


# --- complément de sceau : des questions ajoutées après une collecte (porteur, 22.10.2026 : jeu de 20 pour crooners) ---

JEU3 = [
    {**JEU[0], "i": 0},
    {**JEU[1], "i": 1},
    {"i": 2, "question": "Q3 ?", "attendu": "R3", "citation": "https://ex.org/deja-ingeree"},
    {"i": 3, "question": "Q4 ?", "attendu": "R4", "citation": "https://neuf.org/page"},
]


def test_un_sceau_ne_verifie_que_ses_questions():
    s = na.sceller(JEU3[:2], "t1")
    assert s["indices"] == [0, 1]
    assert na.intact(JEU3, s)  # des questions ajoutées ne rompent pas le premier sceau
    assert not na.intact([{**JEU3[0], "attendu": "retouché"}, *JEU3[1:]], s)


def test_un_complement_exclut_les_questions_dont_la_page_est_deja_dans_le_graphe():
    premier = na.sceller(JEU3[:2], "t1")
    c = na.completer(JEU3, [premier], [("f.md", "https://www.ex.org/deja-ingeree/")], "t2")
    assert c["indices"] == [2, 3] and c["exclues"] == [2] and c["scelle_le"] == "t2"
    assert "neuf.org/page" in c["adresses"]


def test_un_complement_sans_question_neuve_est_refuse():
    with pytest.raises(ValueError):
        na.completer(JEU3[:2], [na.sceller(JEU3[:2], "t1")], [], "t2")


def test_la_quarantaine_lit_les_adresses_de_tous_les_sceaux():
    premier, c = na.sceller(JEU3[:2], "t1"), na.completer(JEU3, [na.sceller(JEU3[:2], "t1")], [], "t2")
    assert na.contaminees(
        [("a.md", "https://ex.org/a"), ("n.md", "https://neuf.org/page")], na.reunir([premier, c])
    ) == ["a.md", "n.md"]


def test_les_questions_mesurees_sont_hors_des_exclues():
    c = na.completer(JEU3, [na.sceller(JEU3[:2], "t1")], [("f.md", "https://ex.org/deja-ingeree")], "t2")
    assert [q["i"] for q in na.mesurables(JEU3, [c])] == [0, 1, 3]

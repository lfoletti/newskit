"""La file du canon (combler_canon.a_traiter) : sans mesure par thème (un graphe qui naît), les sujets passent à tour
de rôle ; avec elle, les sujets faibles d'abord (règle du 09.10.2026). Crooners, 22.10.2026 : quatre passages n'avaient
pas atteint le cinquième sujet, la file alignant les notions sujet après sujet."""
from newskit import combler_canon as cc

SUJETS = [{"sujet": "a", "absentes": ["a1", "a2", "a3"]}, {"sujet": "b", "absentes": ["b1"]},
          {"sujet": "c", "absentes": ["c1", "c2"], "critique": {"absentes": ["c3"]}}]


def test_sans_mesure_les_sujets_passent_a_tour_de_role():
    assert cc.file_du_canon(SUJETS, {}) == ["a1", "b1", "c1", "a2", "c2", "a3", "c3"]


def test_avec_la_mesure_les_sujets_faibles_passent_d_abord_en_entier():
    assert cc.file_du_canon(SUJETS, {"a": 0.9, "b": 0.5, "c": 0.2}) == ["c1", "c2", "c3", "b1", "a1", "a2", "a3"]


def test_une_notion_commune_a_deux_sujets_ne_vient_qu_une_fois():
    assert cc.file_du_canon([{"sujet": "a", "absentes": ["x", "y"]}, {"sujet": "b", "absentes": ["x", "z"]}], {}) == ["x", "y", "z"]

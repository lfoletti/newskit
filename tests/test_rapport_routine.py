"""Le rapport de routine remplace la WIP (mainteneur, 11.11.2026 : « l'envoi WIP -> rapport de routine, avec la partie
stats et coulisses uniquement. mieux organiser/mettre en forme cette partie. supprimer l'envoi de la version WIP »).

Le mainteneur reçoit la daily, comme les abonnés, puis le rapport : les chiffres du passage par section, la santé et
les sujets proposés, sans la lettre.
"""

from __future__ import annotations

import sys

from newskit import lettre as lt
from newskit import notifier_courriel as nc

NBSP = " "


def _lettre(**plus) -> dict:
    L = {
        "site": "https://site.example",
        "marque": "site.example",
        "nom": "ai-governance",
        "graphe": "ai-governance",
        "depot": "depot",
        "lettre_nom": "The Governance News",
        "lettre_titre": "The Governance",
        "lettre_slogan": "",
        "periode": "jour",
        "jour": "2026-11-11",
        "envoi": "2026-11-11",
        "heure": "2026-11-11 07:13",
        "faits": [
            {
                "titre": "Fait de la une",
                "paragraphe": "Texte du fait.",
                "source": {"editeur": "example.org", "url": "https://example.org/a", "date": "2026-11-10"},
                "rubrique": "Conformité",
                "gras": [],
            }
        ],
        "aussi": {},
        "bref": {},
        "ordre": [],
        "bref_reste": 0,
        "notions_neuves": 3,
        "identifiants": 5,
        "neuf": "+2",
        "noeuds": "1200",
        "aretes": "3400",
        "ecart_noeuds": "+12",
        "ecart_aretes": "",
        "fiches": "10500",
        "cout_passage": "0.42 $",
        "cout_lettre": 0.1,
        "sante": ["une source <muette>"],
        "etat": "ok",
    }
    L.update(plus)
    return L


def test_l_objet_nomme_le_graphe_la_date_et_les_points_a_suivre():
    assert lt.rapport_objet(_lettre()) == "Rapport de routine — ai-governance — 11 novembre 2026 — 1 point à suivre"
    assert lt.rapport_objet(_lettre(sante=["a", "b"])).endswith("— 2 points à suivre")
    assert lt.rapport_objet(_lettre(sante=[])).endswith("— rien à signaler")
    assert "— ÉCHEC partiel —" in lt.rapport_objet(_lettre(etat="ÉCHEC partiel"))


def test_l_objet_et_les_dates_restent_en_francais_pour_une_lettre_anglaise():
    L = _lettre(langue="en")
    assert "11 novembre 2026" in lt.rapport_objet(L)
    assert "11 novembre 2026, 7 h 13" in lt.rapport_texte(L)


def test_le_rapport_ne_porte_pas_la_lettre():
    L = _lettre()
    for rendu in (lt.rapport_texte(L), lt.rapport_html(L)):
        assert "Fait de la une" not in rendu and "Désinscription" not in rendu and "À retenir" not in rendu


def test_le_html_range_les_chiffres_par_section():
    h = lt.rapport_html(_lettre())
    titres = ["Rapport de routine", "Corpus et graphe", "Qualité", "Coût", "Santé du passage"]
    positions = [h.find(lt._etiquette(t, m)) for t, m in zip(titres, ["0"] + ["0 0 8px 0"] * 4)]
    assert -1 not in positions[:2] and positions[3] > positions[1]
    assert h.count('class="tuile"') == 4
    assert "1 200 +12" in h.replace(NBSP, " ")
    assert h.count('<!--[if mso]></td><td width="134" valign="top"><![endif]-->') == 3  # tuiles côte à côte (Outlook)
    assert h.count("<!--[if mso]></td></tr></table><![endif]-->") == 2  # la table des tuiles, puis la colonne
    assert lt.AMBRE_FOND in h and "une source &lt;muette&gt;" in h


def test_une_section_sans_valeur_ne_parait_pas():
    h = lt.rapport_html(_lettre(identifiants=0))
    assert "Identifiants neufs" not in h
    assert lt._etiquette("Qualité", "0 0 8px 0") not in h  # ni actualité ni banc
    h = lt.rapport_html(_lettre(actualite=[{"nom": "Droit", "n": 4}], actualite_jours=7))
    assert lt._etiquette("Qualité", "0 0 8px 0") in h and "Droit 4" in h


def test_sans_point_a_suivre_le_rapport_le_dit_en_vert():
    L = _lettre(sante=[])
    h = lt.rapport_html(L)
    assert "Rien à signaler." in h and lt.VERT_FOND in h and lt.AMBRE_FOND not in h
    assert "Rien à signaler." in lt.rapport_texte(L)
    assert lt.AMBRE_FOND in lt.rapport_html(_lettre(sante=[], etat="ÉCHEC partiel"))


def test_les_sujets_proposes_sont_designes_par_leur_titre():
    sel = [{"ref": 2, "titre": "Un sujet", "hypothese": "une piste", "raison": "trois sources"}]
    L = _lettre(selection=sel)
    for rendu in (lt.rapport_html(L), lt.rapport_texte(L)):
        assert "SUJETS D" in rendu.upper() and "Un sujet" in rendu and "n° 2" not in rendu
    assert "Sujets d" not in lt.rapport_html(_lettre())


def test_le_texte_suit_les_memes_sections_avec_les_insecables():
    t = lt.rapport_texte(_lettre())
    ordre = ["RAPPORT DE ROUTINE", "CORPUS ET GRAPHE", "COÛT", "SANTÉ DU PASSAGE", "Rapport complet"]
    assert [t.index(x) for x in ordre] == sorted(t.index(x) for x in ordre)
    assert "  - une source <muette>" in t
    assert f"Rapport complet{NBSP}:" in t and " :" not in t


def test_les_liens_du_pied_suivent_le_site():
    assert "site.example/#qualite" in lt.rapport_html(_lettre())
    h = lt.rapport_html(_lettre(site=""))
    assert "#qualite" not in h and "ROUTINE.md" in h
    assert "#qualite" not in lt.rapport_texte(_lettre(site=""))


def test_l_envoi_part_en_daily_puis_en_rapport_sans_wip(tmp_path, monkeypatch):
    rapport = tmp_path / "ROUTINE.md"
    rapport.write_text("rapport", encoding="utf-8")
    monkeypatch.setattr(nc, "RAPPORT", rapport)
    monkeypatch.setattr(nc, "LETTRES", tmp_path / "lettres")
    monkeypatch.setattr(sys, "argv", ["notifier_courriel.py", "--envoyer"])
    monkeypatch.setattr(lt, "eclairages_publies", lambda g: {})
    monkeypatch.setattr(lt, "lettre_du_graphe", lambda *a, **k: {**_lettre(), "redige": True})
    vus = []
    monkeypatch.setattr(nc, "envoyer", lambda o, c, h="", vers=None, **k: vus.append((o, c, h, k)) or 0)
    assert nc.main() == 0
    assert len(vus) == 2
    (o1, c1, h1, k1), (o2, c2, h2, k2) = vus
    assert k1.get("compte") == nc.COMPTE_LETTRE and "Fait de la une" in c1
    assert o2.startswith("Rapport de routine — ") and not any(o.startswith("[WIP]") for o, *_ in vus)
    assert "Fait de la une" not in c2 + h2 and "Santé du passage" in h2
    assert "compte" not in k2  # l'adresse du registre seule, comme la WIP


def test_le_rapport_ne_part_pas_si_la_daily_echoue(tmp_path, monkeypatch):
    rapport = tmp_path / "ROUTINE.md"
    rapport.write_text("rapport", encoding="utf-8")
    monkeypatch.setattr(nc, "RAPPORT", rapport)
    monkeypatch.setattr(nc, "LETTRES", tmp_path / "lettres")
    monkeypatch.setattr(sys, "argv", ["notifier_courriel.py", "--envoyer"])
    monkeypatch.setattr(lt, "eclairages_publies", lambda g: {})
    monkeypatch.setattr(lt, "lettre_du_graphe", lambda *a, **k: {**_lettre(), "redige": True})
    vus = []
    monkeypatch.setattr(nc, "envoyer", lambda o, c, h="", vers=None, **k: vus.append(o) or 1)
    assert nc.main() == 1 and len(vus) == 1

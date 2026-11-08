"""Les prochaines fiches du fil au format validé le 8.11.2026 : résumé, catégorie, éléments, en français et en anglais."""

from __future__ import annotations

import pytest
import yaml

from newskit import config_sources as cfg
from newskit import fil, fil_fiche as ff
from newskit import lettre as lt
from newskit import publier_fil

CORPS = "La Commission conclut que le fil enfreint le DSA selon ses constatations préliminaires publiées mardi dernier à Bruxelles."
CITE = "La Commission conclut que le fil enfreint le DSA"
ARBRE = {
    "racines": [
        {
            "cle": "conformite",
            "fr": "Conformité",
            "en": "Compliance",
            "champs": ["constats"],
            "feuilles": [{"cle": "dsa", "fr": "Application du DSA", "en": "DSA enforcement"}],
        }
    ]
}


def element(citation=CITE):
    return {
        "champ": "Constats",
        "champ_en": "Findings",
        "texte": "La Commission retient une infraction.",
        "texte_en": "The Commission finds a breach.",
        "citation": citation,
    }


def reponse(n=0, **autre):
    return {
        "n": n,
        "categorie": "conformite:dsa",
        "resume": "Résumé.",
        "resume_en": "Summary.",
        "elements": [element(), element()],
        **autre,
    }


@pytest.fixture
def banc(monkeypatch, tmp_path):
    (tmp_path / "arbre.yaml").write_text(yaml.safe_dump(ARBRE), encoding="utf-8")
    monkeypatch.setattr(ff, "RACINE", tmp_path)
    monkeypatch.setattr(
        cfg,
        "graphe_declare",
        lambda g: {
            "libelle": "IA",
            "arbre_fiches": "arbre.yaml",
            "fiche_modele": "modele-du-media",
            "fiche_usd_jour": 10.0,
        },
    )
    monkeypatch.setattr(ff, "existe", lambda c: True)
    monkeypatch.setattr(lt, "charger_fiches", lambda cs: {c: {"chemin": c} for c in cs})
    monkeypatch.setattr(fil, "extrait", lambda b, n: CORPS)
    monkeypatch.setattr(ff, "corps_de", lambda b: CORPS)
    etat = {"verdicts": None, "modele": None, "invite": "", "reponses": [reponse()], "paires": []}

    def demander(inv, schema, graphe, note, modele=None):
        etat["modele"], etat["invite"] = modele, inv
        return {"billets": etat["reponses"]}, 1.5

    def juger(paires, graphe, source=3000):
        etat["paires"], etat["source"] = paires, source
        return (
            {i: {"absents": [], "clair": True, "motif": ""} for i in range(len(paires))}
            if etat["verdicts"] is None
            else etat["verdicts"]
        ), 0.5

    etat["appels"] = 0
    _demander = demander

    def compte(*a, **k):
        etat["appels"] += 1
        return _demander(*a, **k)

    monkeypatch.setattr(fil, "demander", compte)
    monkeypatch.setattr(fil, "juger", juger)
    return etat


def billet():
    return {"titre": "T", "descriptif": "D", "chemin": "c.md", "rubrique": "Conformité", "url": "u"}


def test_sans_arbre_le_texte_d_avant_reste(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert ff.arbre("g") is None


def test_une_fiche_tenue_porte_resume_categorie_elements_dans_les_deux_langues(banc):
    b = billet()
    assert ff.etoffer({"billets": [b]}, "g", 20) == 2.0
    f = b["fiche"]
    assert f["tenu"] is True and f["resume"] == "Résumé." and f["categorie"] == "Conformité : Application du DSA"
    assert f["elements"][0] == {"champ": "Constats", "texte": "La Commission retient une infraction.", "citation": CITE}
    assert f["en"]["resume"] == "Summary." and f["en"]["categorie"] == "Compliance: DSA enforcement"
    assert f["en"]["elements"][0] == {"champ": "Findings", "texte": "The Commission finds a breach."}
    assert f["texte"] == "La Commission retient une infraction.\n\nLa Commission retient une infraction."


def test_opus_ecrit_et_les_deux_langues_sont_relues(banc):
    ff.etoffer({"billets": [billet()]}, "g", 20)
    assert banc["modele"] == "modele-du-media"
    assert len(banc["paires"]) == 2 and "Summary." in banc["paires"][1][1]
    assert "constats" in banc["invite"] and "conformite:dsa" in banc["invite"]


def test_une_fiche_deja_ecrite_n_est_pas_convertie(banc):
    b = {**billet(), "fiche": {"texte": "ancien", "tenu": True}}
    assert ff.etoffer({"billets": [b]}, "g", 20) == 0.0
    assert b["fiche"] == {"texte": "ancien", "tenu": True}


def test_une_citation_inexacte_retire_l_element(banc):
    banc["reponses"] = [
        reponse(elements=[element(), element(), element("cette phrase ne figure nulle part dans la source")])
    ]
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is True and len(b["fiche"]["elements"]) == 2


def test_trop_peu_d_elements_prouves_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(elements=[element(), element("cette phrase ne figure nulle part dans la source")])]
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False and "éléments" in b["fiche"]["reproches"][0]


def test_une_categorie_hors_arbre_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(categorie="autre:chose")]
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False and "catégorie" in b["fiche"]["reproches"][0]


def test_il_manque_une_langue_refuse_la_fiche(banc):
    banc["reponses"] = [reponse(resume_en="")]
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False


@pytest.mark.parametrize("rang", [0, 1])
def test_un_refus_de_relecture_dans_une_langue_refuse_la_fiche(banc, rang):
    banc["verdicts"] = {0: {"absents": [], "clair": True, "motif": ""}, 1: {"absents": [], "clair": True, "motif": ""}}
    banc["verdicts"][rang] = {"absents": ["un nom"], "clair": True, "motif": ""}
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is False and b["fiche"]["reproches"] == ["absent de la source : un nom"]


def test_pas_clair_refuse_et_garde_le_motif(banc):
    banc["verdicts"] = {
        0: {"absents": [], "clair": False, "motif": "flou"},
        1: {"absents": [], "clair": True, "motif": ""},
    }
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["reproches"] == ["flou"]


def test_sans_verdict_la_fiche_reste_a_ecrire(banc):
    banc["verdicts"] = {0: {"absents": [], "clair": True, "motif": ""}}
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert "fiche" not in b


def test_un_numero_inconnu_est_ignore(banc):
    banc["reponses"] = [reponse(n=7)]
    b = billet()
    assert ff.etoffer({"billets": [b]}, "g", 20) == 1.5
    assert "fiche" not in b


def test_un_billet_sans_fiche_lisible_ne_coute_rien(banc, monkeypatch):
    monkeypatch.setattr(lt, "charger_fiches", lambda cs: {})
    assert ff.etoffer({"billets": [billet()]}, "g", 20) == 0.0


def test_rien_a_juger_ne_relit_pas(banc):
    banc["reponses"] = [reponse(resume="")]
    banc["paires"] = "intact"
    ff.etoffer({"billets": [billet()]}, "g", 20)
    assert banc["paires"] == "intact"


def test_le_plafond_du_jour_se_declare(banc):
    F = {"billets": [billet()], "depense_fiche": {__import__("time").strftime("%Y-%m-%d"): 9.0}}
    assert ff.etoffer(F, "g", 20) == 2.0
    F2 = {"billets": [billet()], "depense_fiche": {__import__("time").strftime("%Y-%m-%d"): 10.0}}
    assert ff.etoffer(F2, "g", 20) == 0.0


def test_sans_reglage_le_plafond_et_le_modele_par_defaut(monkeypatch):
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {})
    assert ff.plafond("g") == ff.FICHE_USD_JOUR == 3.0 and ff.modele("g") is None


def test_corps_de_lit_la_source_entiere(monkeypatch, tmp_path):
    # Mainteneur, 08.11.2026 : « la rédaction doit lire l'article en entier ».
    long = "mot " * 5000
    (tmp_path / "c.md").write_text("---\nt: 1\n---\n" + long, encoding="utf-8")
    monkeypatch.setattr(fil, "RACINE", tmp_path)
    assert ff.corps_de({"chemin": "c.md"}) == long.strip()


def test_le_relecteur_lit_aussi_la_source_entiere(banc):
    ff.etoffer({"billets": [billet()]}, "g", 20)
    assert banc["source"] is None


@pytest.mark.parametrize(("source", "attendu"), [(None, 8000), (3000, 3000)])
def test_juger_donne_la_longueur_demandee(monkeypatch, tmp_path, source, attendu):
    (tmp_path / "c.md").write_text("x" * 8000, encoding="utf-8")
    monkeypatch.setattr(fil, "RACINE", tmp_path)
    monkeypatch.setattr(cfg, "graphe_declare", lambda g: {"libelle": "IA"})
    vu = {}

    def demander(inv, schema, graphe, note):
        vu["n"] = inv.count("x")
        return {"billets": []}, 0.0

    monkeypatch.setattr(fil, "demander", demander)
    monkeypatch.setattr(fil, "doutes", lambda t, x, f: [])
    fil.juger([("T", "", {"chemin": "c.md"}, {})], "g", source=source)
    assert attendu <= vu["n"] < attendu + 2000


def test_un_appel_par_billet(banc):
    F = {"billets": [billet(), {**billet(), "chemin": "d.md"}]}
    assert ff.etoffer(F, "g", 20) == 4.0
    assert banc["appels"] == 2 and F["billets"][0]["fiche"]["tenu"] is True and F["billets"][1]["fiche"]["tenu"] is True


def test_le_passage_s_arrete_au_plafond(banc):
    F = {"billets": [billet(), {**billet(), "chemin": "d.md"}]}
    assert ff.etoffer(F, "g", 2.0) == 2.0
    assert banc["appels"] == 1 and "fiche" not in F["billets"][1]


def test_la_copie_publique_montre_les_elements_sans_les_citations(monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})
    fiche = {
        "texte": "x",
        "tenu": True,
        "resume": "R",
        "categorie": "C",
        "elements": [{"champ": "A", "texte": "B", "citation": "secret"}],
        "en": {"resume": "RE", "categorie": "CE", "elements": [{"champ": "AE", "texte": "BE"}]},
    }
    p = publier_fil.public({"billets": [{**billet(), "fiche": fiche}]})["billets"][0]
    assert p["resume"] == "R" and p["categorie"] == "C" and p["elements"] == [{"champ": "A", "texte": "B"}]
    assert p["resume_en"] == "RE" and p["categorie_en"] == "CE" and p["elements_en"] == [{"champ": "AE", "texte": "BE"}]
    assert "secret" not in str(p)


def test_une_fiche_refusee_ou_ancienne_ne_publie_pas_d_elements(monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})
    p = publier_fil.public({"billets": [{**billet(), "fiche": {"texte": "x", "tenu": True}}]})["billets"][0]
    assert p["elements"] == [] and p["elements_en"] == [] and p["resume"] == ""


def test_une_fiche_refusee_ne_publie_rien_de_ses_elements(monkeypatch):
    monkeypatch.setattr(publier_fil, "liens", lambda b: {})
    fiche = {"tenu": False, "resume": "R", "elements": [{"champ": "A", "texte": "B", "citation": "c"}]}
    p = publier_fil.public({"billets": [{**billet(), "fiche": fiche}]})["billets"][0]
    assert p["elements"] == [] and p["resume"] == ""


@pytest.mark.parametrize(("modele", "attendu"), [("opus-x", "opus-x"), (None, lt.MODELE)])
def test_demander_passe_le_modele_choisi(monkeypatch, modele, attendu):
    vu = {}

    def run(args, **k):
        vu["args"] = args

    def enregistrer(poste, graphe, m, r, duree, note):
        vu["registre"] = m
        return {"structured_output": {"ok": 1}, "total_cost_usd": 1}

    monkeypatch.setattr(fil.subprocess, "run", run)
    monkeypatch.setattr(fil.couts, "enregistrer", enregistrer)
    assert fil.demander("i", {}, "g", "n", modele=modele) == ({"ok": 1}, 1.0)
    assert vu["args"][vu["args"].index("--model") + 1] == attendu and vu["registre"] == attendu


def test_la_clarte_de_la_version_anglaise_ne_refuse_pas_la_fiche(banc):
    # Essai du 08.11.2026, 0 fiche tenue sur 5 : le relecteur, écrit pour des billets français, rejetait l'anglais comme tel.
    banc["verdicts"] = {
        0: {"absents": [], "clair": True, "motif": ""},
        1: {"absents": [], "clair": False, "motif": "en anglais"},
    }
    b = billet()
    ff.etoffer({"billets": [b]}, "g", 20)
    assert b["fiche"]["tenu"] is True


def test_la_consigne_demande_le_contenu_pas_la_parution():
    # Mainteneur, 08.11.2026 : « ce qui est intéressant quand un rapport sort, c'est son contenu, pas le fait qu'il est sorti ».
    assert "ce qu'ils établissent" in ff.REDACTION_ELEMENTS
    assert "le fait de la parution" in ff.REDACTION_ELEMENTS

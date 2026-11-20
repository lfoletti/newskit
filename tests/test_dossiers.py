"""Les dossiers transversaux du fil (mainteneur, 20.11.2026).

« certains thèmes/sujets pourraient devenir des rubriques, comme Bugmaggedon, RSI, technologies éducatives, en
impliquant le reclassement de certains enregistrements » ; « il y a des critères de filtre à définir. par exemple pour
bugmaggedon, la plupart des failles critiques détectées en 2026 y sont rattachables, même si elles ne nomment pas le
phénomène directement » ; « dossier transversal pour les trois » ; « ce sont des thèmes transversaux et aussi,
forcément, des noeuds à part entière ».

Un billet garde sa rubrique et reçoit zéro, un ou plusieurs dossiers. Un dossier se déclare dans le dossier du média
(clé, libellés, critère écrit, date de départ) ; le moteur le lit, le donne au tri du fil et au reclassement, l'écrit
dans l'en-tête de la fiche, et le squelette en fait un nœud relié à chaque article du dossier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from newskit import config_sources as cfg
from newskit import dossiers as dos
from newskit import extraire_squelette as sq
from newskit import fil
from newskit import publier_fil

DECLARES = [
    {
        "cle": "bugmageddon",
        "fr": "Bugmageddon",
        "en": "Bugmageddon",
        "critere": "Faille critique rendue publique en 2026, que l'article nomme le phénomène ou non.",
        "depuis": "2026-01-01",
    },
    {
        "cle": "rsi",
        "fr": "Auto-amélioration récursive",
        "en": "Recursive self-improvement",
        "critere": "Systèmes d'IA qui améliorent leurs propres capacités ou automatisent la recherche en IA.",
    },
]


@pytest.fixture
def media(tmp_path: Path) -> Path:
    (tmp_path / "dossiers.yaml").write_text(
        yaml.safe_dump({"dossiers": DECLARES}, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def declares(media, monkeypatch) -> list[dict]:
    monkeypatch.setattr(dos, "RACINE", media)
    monkeypatch.setattr(dos.cfg, "graphe_declare", lambda nom: {"dossiers": {"fichier": "dossiers.yaml"}})
    return dos.du_graphe("essai")


def refuse(media: Path, contenu, motif: str) -> None:
    (media / "d.yaml").write_text(yaml.safe_dump(contenu, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match=motif):
        dos.declaration({"fichier": "d.yaml"}, media)


# --- la déclaration


def test_la_declaration_se_lit_du_fichier_du_media(media):
    d = dos.declaration({"fichier": "dossiers.yaml"}, media)
    assert [x["cle"] for x in d] == ["bugmageddon", "rsi"]
    assert d[0] == {**DECLARES[0]}
    assert d[1]["depuis"] == ""


def test_une_date_yaml_se_lit_comme_un_texte(media):
    (media / "d.yaml").write_text(
        "dossiers:\n- {cle: a, fr: A, en: A, critere: c, depuis: 2026-01-01}\n", encoding="utf-8"
    )
    assert dos.declaration({"fichier": "d.yaml"}, media)[0]["depuis"] == "2026-01-01"


def test_sans_declaration_aucun_dossier(monkeypatch):
    monkeypatch.setattr(dos.cfg, "graphe_declare", lambda nom: {})
    assert dos.du_graphe("essai") == []


def test_la_declaration_est_un_fichier(media):
    with pytest.raises(ValueError, match="fichier"):
        dos.declaration({"liste": []}, media)
    with pytest.raises(ValueError, match="fichier absent du dossier du média : rien.yaml"):
        dos.declaration({"fichier": "rien.yaml"}, media)


@pytest.mark.parametrize(
    ("contenu", "motif"),
    [
        ({"dossiers": []}, "liste « dossiers » non vide"),
        ({"autre": 1}, "liste « dossiers » non vide"),
        ({"dossiers": [{**DECLARES[0], "cle": "Bug-mageddon"}]}, "cle"),
        ({"dossiers": [DECLARES[0], DECLARES[0]]}, "en double : bugmageddon"),
        ({"dossiers": [{**DECLARES[0], "critere": " "}]}, "bugmageddon : critere"),
        ({"dossiers": [{k: v for k, v in DECLARES[0].items() if k != "en"}]}, "bugmageddon : en"),
        ({"dossiers": [{**DECLARES[0], "depuis": "janvier"}]}, "bugmageddon : depuis"),
    ],
)
def test_une_declaration_fautive_est_refusee(media, contenu, motif):
    refuse(media, contenu, motif)


def registre(tmp_path: Path, dossiers_) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": [
                    {
                        "cle": "blog",
                        "plateforme": "Flux RSS",
                        "nom": "Blog",
                        "dossier": "rss/blog",
                        "abonnement": "gratuit",
                        "flux": "https://exemple.org/flux",
                    }
                ],
                "graphes": {"essai": {"libelle": "Essai", "domaine": "essai", "dossiers": dossiers_}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return p


def test_le_registre_accepte_les_dossiers_d_un_graphe(media, monkeypatch):
    monkeypatch.setattr(cfg, "RACINE", media)
    conf = cfg.charger(registre(media, {"fichier": "dossiers.yaml"}))
    assert conf["graphes"]["essai"]["dossiers"] == {"fichier": "dossiers.yaml"}


def test_le_registre_nomme_le_graphe_d_une_declaration_refusee(media, monkeypatch):
    monkeypatch.setattr(cfg, "RACINE", media)
    with pytest.raises(cfg.ConfigInvalide, match=r"graphes\.essai\.dossiers.*absent"):
        cfg.charger(registre(media, {"fichier": "absent.yaml"}))


# --- le tri du fil rend les dossiers


def test_la_consigne_du_tri_donne_chaque_critere(declares):
    c = dos.consigne(declares)
    assert "bugmageddon : Faille critique" in c and "rsi : Systèmes d'IA" in c
    assert "à partir du 2026-01-01" in c
    assert "même quand la fiche ne nomme pas le phénomène" in c
    assert dos.consigne([]) == ""


@pytest.mark.parametrize("tri", [True, False])
def test_le_tri_demande_les_dossiers_dans_le_meme_appel(declares, monkeypatch, tri):
    vus = {}
    monkeypatch.setattr(fil, "tri_seul", lambda g: tri)
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda nom: {"libelle": "Essai", "dossiers": {"fichier": "x"}})
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [{"nom": "Sécurité"}])
    monkeypatch.setattr(fil.dos, "du_graphe", lambda g: declares)
    monkeypatch.setattr(fil.lt, "regles", lambda g: "")
    monkeypatch.setattr(fil.rf, "consigne", lambda g: "")
    monkeypatch.setattr(fil, "demander", lambda inv, schema, g, note: vus.update(inv=inv, schema=schema) or ({}, 0.0))
    fil.rediger([{"titre": "t", "chemin": "c", "date": "2026-11-20"}], [], "essai")
    assert "bugmageddon : Faille critique" in vus["inv"]
    props = vus["schema"]["properties"]["billets"]["items"]["properties"]
    assert props["dossiers"] == {"type": "array", "items": {"type": "string"}}


def test_sans_dossier_la_consigne_ne_change_pas(monkeypatch):
    vus = {}
    monkeypatch.setattr(fil, "tri_seul", lambda g: True)
    monkeypatch.setattr(fil.cfg, "graphe_declare", lambda nom: {"libelle": "Essai"})
    monkeypatch.setattr(fil.cfg, "rubriques", lambda g: [{"nom": "Sécurité"}])
    monkeypatch.setattr(fil.dos, "du_graphe", lambda g: [])
    monkeypatch.setattr(fil, "demander", lambda inv, schema, g, note: vus.update(inv=inv) or ({}, 0.0))
    fil.rediger([{"titre": "t", "chemin": "c"}], [], "essai")
    assert "dossiers" not in vus["inv"]


# --- poser : clés déclarées, date de départ, en-tête de la fiche


def test_poser_garde_les_cles_declarees_une_fois(declares):
    b = {"chemin": "sources/a/2026-03-02_x.md", "date": "2026-03-02"}
    dos.poser(b, ["rsi", "inconnu", "rsi", 3, "bugmageddon"], declares)
    assert b["dossiers"] == ["bugmageddon", "rsi"]
    assert b["dossiers_vus"] == dos.vus(declares)


def test_un_billet_d_avant_la_date_de_depart_n_entre_pas(declares):
    b = {"chemin": "sources/a/2025-12-30_x.md", "date": "2025-12-30"}
    dos.poser(b, ["bugmageddon", "rsi"], declares)
    assert b["dossiers"] == ["rsi"]


def test_sans_date_la_date_se_lit_dans_le_chemin(declares):
    assert dos.date_de({}, "sources/a/2025-12-30_x.md") == "2025-12-30"
    b = {}
    dos.poser(b, ["bugmageddon"], declares, chemin="sources/a/2025-12-30_x.md")
    assert b["dossiers"] == []
    b = {}
    dos.poser(b, ["bugmageddon"], declares, chemin="sources/a/sans-date.md")
    assert b["dossiers"] == []


def test_l_empreinte_change_avec_le_critere(declares):
    autre = [{**declares[0], "critere": "autre"}, declares[1]]
    assert dos.empreinte(declares[0]) != dos.empreinte(autre[0])
    assert dos.empreinte(declares[0]) == dos.empreinte(dict(declares[0]))
    assert dos.empreinte({**declares[0], "fr": "autre libellé"}) == dos.empreinte(declares[0])


def fiche_de(racine: Path, rel: str, dossiers_=None) -> Path:
    from newskit import fiche

    p = racine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    champs = {
        "title": "Une faille critique",
        "author": "A. Auteur",
        "date": "2026-03-02",
        "url": "https://exemple.org/a",
        "plateforme": "Flux RSS",
        "collected": "2026-03-02",
        "words": 300,
        "complet": True,
    }
    if dossiers_ is not None:
        champs["dossiers"] = dossiers_
    fiche.ecrire(p, champs, "# Une faille critique\n\n" + "mot " * 300, strict=False, hors_graphe=True)
    return p


def test_les_dossiers_entrent_dans_l_en_tete_de_la_fiche(tmp_path, monkeypatch):
    from newskit import fiche

    monkeypatch.setattr(dos, "RACINE", tmp_path)
    p = fiche_de(tmp_path, "sources/rss/blog/2026-03-02_x.md")
    assert dos.ecrire_dans_la_fiche("sources/rss/blog/2026-03-02_x.md", ["bugmageddon"]) is True
    champs, _ = fiche.lire_fichier(p)
    assert champs["dossiers"] == ["bugmageddon"]
    # La même liste : rien n'est réécrit (une fiche réécrite repart au cerveau).
    avant = p.stat().st_mtime_ns
    assert dos.ecrire_dans_la_fiche("sources/rss/blog/2026-03-02_x.md", ["bugmageddon"]) is False
    assert p.stat().st_mtime_ns == avant
    # Une liste vide retire le champ.
    assert dos.ecrire_dans_la_fiche("sources/rss/blog/2026-03-02_x.md", []) is True
    assert "dossiers" not in fiche.lire_fichier(p)[0]


def test_une_fiche_absente_ne_bloque_rien(tmp_path, monkeypatch):
    monkeypatch.setattr(dos, "RACINE", tmp_path)
    assert dos.ecrire_dans_la_fiche("sources/absente.md", ["rsi"]) is False


# --- le reclassement de l'existant


def fil_d_essai() -> dict:
    return {
        "billets": [
            {"chemin": "sources/a/2026-03-02_faille.md", "date": "2026-03-02", "titre": "Faille", "descriptif": "d"},
            {"chemin": "sources/a/2026-03-03_autre.md", "date": "2026-03-03", "titre": "Autre", "descriptif": "d"},
        ],
        "pile": [{"chemin": "sources/a/2026-03-04_rsi.md", "date": "2026-03-04", "titre": "RSI", "descriptif": "d"}],
        "archive": {"sources/a/2025-12-01_vieille.md": {"titre": "Vieille faille", "descriptif": "d"}},
    }


def test_a_juger_prend_billets_pile_et_archive(declares):
    F = fil_d_essai()
    chemins = [c for c, _, _ in dos.a_juger(F, declares)]
    assert chemins == [
        "sources/a/2026-03-04_rsi.md",
        "sources/a/2026-03-02_faille.md",
        "sources/a/2026-03-03_autre.md",
        "sources/a/2025-12-01_vieille.md",
    ]
    F["billets"][0]["dossiers_vus"] = dos.vus(declares)
    assert "sources/a/2026-03-02_faille.md" not in [c for c, _, _ in dos.a_juger(F, declares)]


def test_le_reclassement_juge_par_lots_et_pose_les_dossiers(declares, monkeypatch):
    F = fil_d_essai()
    ecrits = []
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: ecrits.append((chemin, cles)) or True)
    monkeypatch.setattr(dos, "PAR_APPEL", 2)
    appels = []

    def demander(inv, schema, graphe, note):
        appels.append(inv)
        lot = [json.loads(x) for x in inv.split("Nouvelles :\n", 1)[1].splitlines() if x.strip()]
        rep = []
        for x in lot:
            cles = (["bugmageddon"] if "aille" in x["titre"] else []) + (["rsi"] if x["titre"] == "RSI" else [])
            rep.append({"n": x["n"], "dossiers": cles, "motif": "m" if cles else ""})
        return {"nouvelles": rep}, 0.25

    n, cout = dos.reclasser(F, "essai", declares, demander=demander)
    assert n == 4 and cout == 0.5 and len(appels) == 2
    assert "bugmageddon : Faille critique" in appels[0]
    assert F["billets"][0]["dossiers"] == ["bugmageddon"] and F["billets"][0]["dossiers_motif"] == "m"
    assert F["billets"][1]["dossiers"] == []
    assert F["pile"][0]["dossiers"] == ["rsi"]
    # Avant la date de départ de Bugmageddon : le modèle l'y range, le moteur l'écarte.
    assert F["archive"]["sources/a/2025-12-01_vieille.md"]["dossiers"] == []
    assert ("sources/a/2026-03-02_faille.md", ["bugmageddon"]) in ecrits
    assert ("sources/a/2026-03-04_rsi.md", ["rsi"]) in ecrits
    # Relancé : rien à juger.
    assert dos.reclasser(F, "essai", declares, demander=demander) == (0, 0.0) and len(appels) == 2


def test_une_nouvelle_sans_reponse_reste_a_juger(declares, monkeypatch):
    F = fil_d_essai()
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    n, _ = dos.reclasser(F, "essai", declares, demander=lambda *a: ({"nouvelles": [{"n": 9, "dossiers": []}]}, 0.1))
    assert n == 0 and len(dos.a_juger(F, declares)) == 4


def test_le_reclassement_s_arrete_au_maximum(declares, monkeypatch):
    F = fil_d_essai()
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    rep = lambda inv, *a: ({"nouvelles": [{"n": 0, "dossiers": []}]}, 0.1)  # noqa: E731
    n, _ = dos.reclasser(F, "essai", declares, maximum=1, demander=rep)
    assert n == 1 and len(dos.a_juger(F, declares)) == 3


def test_un_critere_change_relance_le_jugement(declares, monkeypatch):
    F = fil_d_essai()
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    for _, b, _ in dos.a_juger(F, declares):
        b["dossiers_vus"] = dos.vus(declares)
    assert dos.a_juger(F, declares) == []
    a_revoir = dos.a_juger(F, [{**declares[0], "critere": "autre"}, declares[1]])
    assert len(a_revoir) == 4 and {tuple(d["cle"] for d in j) for _, _, j in a_revoir} == {("bugmageddon",)}


# --- la page publique et le graphe


def test_la_copie_publique_porte_les_dossiers():
    p = publier_fil._champs({"billets": [{"titre": "t", "descriptif": "d", "dossiers": ["rsi"]}, {"titre": "u"}]})
    assert p[0]["dossiers"] == ["rsi"] and p[1]["dossiers"] == []


def test_le_dossier_est_un_noeud_relie_a_l_article():
    g = sq.Squelette()
    f = {
        "champs": {"title": "Une faille", "dossiers": ["bugmageddon", "rsi"]},
        "rel": "sources/a/x.md",
        "id": "x",
        "arxiv_id": None,
    }
    aid = sq.declarer_fiche(g, Path("sources/a/x.md"), f, [], libelles={"bugmageddon": "Bugmageddon"})
    n = g.noeuds[sq.make_id("dossier", "bugmageddon")]
    assert n["label"] == "Bugmageddon" and n["kind"] == "dossier"
    assert g.noeuds[sq.make_id("dossier", "rsi")]["label"] == "rsi"
    assert {(e["source"], e["target"], e["relation"]) for e in g.aretes} >= {
        (aid, sq.make_id("dossier", "bugmageddon"), "dans_le_dossier"),
        (aid, sq.make_id("dossier", "rsi"), "dans_le_dossier"),
    }


def test_une_fiche_sans_dossier_n_a_pas_d_arete_de_dossier():
    g = sq.Squelette()
    f = {"champs": {"title": "t"}, "rel": "sources/a/x.md", "id": "x", "arxiv_id": None}
    sq.declarer_fiche(g, Path("sources/a/x.md"), f, [])
    assert not [e for e in g.aretes if e["relation"] == "dans_le_dossier"]


def test_le_compte_par_dossier(declares):
    F = fil_d_essai()
    F["billets"][0]["dossiers"] = ["bugmageddon"]
    F["pile"][0]["dossiers"] = ["rsi", "bugmageddon"]
    assert dos.par_dossier(F, declares) == {"bugmageddon": 2, "rsi": 1}


# --- le passage du fil pose les dossiers du billet neuf et les écrit dans sa fiche


def test_le_passage_pose_les_dossiers_du_billet_neuf(declares, monkeypatch):
    from tests import test_billet_tri as tb

    ecrits = []
    monkeypatch.setattr(fil.dos, "du_graphe", lambda g: declares)
    monkeypatch.setattr(fil.dos, "ecrire_dans_la_fiche", lambda chemin, cles: ecrits.append((chemin, cles)) or True)
    rep = {"n": 0, "pertinent": True, "doublon_de": -1, "dossiers": ["bugmageddon", "inconnu"]}
    F, _ = tb._passer(monkeypatch, tb.ARBRE, [rep])
    [b] = F["pile"]
    assert b["dossiers"] == ["bugmageddon"] and b["dossiers_vus"] == dos.vus(declares)
    assert ecrits == [("s/a.md", ["bugmageddon"])]


def test_sans_dossier_le_passage_ne_touche_pas_la_fiche(monkeypatch):
    from tests import test_billet_tri as tb

    ecrits = []
    monkeypatch.setattr(fil.dos, "du_graphe", lambda g: [])
    monkeypatch.setattr(fil.dos, "ecrire_dans_la_fiche", lambda chemin, cles: ecrits.append(chemin))
    F, _ = tb._passer(monkeypatch, tb.ARBRE, [{"n": 0, "pertinent": True, "doublon_de": -1}])
    assert "dossiers" not in F["pile"][0] and ecrits == []


# --- la commande


@pytest.fixture
def commande(declares, monkeypatch):
    import contextlib

    F = fil_d_essai()
    vu = {"ecrit": 0}
    monkeypatch.setattr(dos, "du_graphe", lambda g: declares)
    monkeypatch.setattr(fil, "verrou", lambda g: contextlib.nullcontext())
    monkeypatch.setattr(fil, "lire", lambda g: F)
    monkeypatch.setattr(fil, "ecrire", lambda g, F_: vu.update(ecrit=vu["ecrit"] + 1))
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    monkeypatch.setattr(dos.cfg, "graphe_declare", lambda nom: {"libelle": "Essai"})
    return F, vu


def test_la_commande_compte_sans_appel(commande, monkeypatch, capsys):
    F, vu = commande
    monkeypatch.setattr(fil, "demander", lambda *a: pytest.fail("aucun appel"))
    assert dos.main(["--graphe", "essai", "--compter"]) == 0
    assert "4 nouvelle(s) à juger, 2 dossier(s)" in capsys.readouterr().out and vu["ecrit"] == 0


def test_la_commande_reclasse_et_ecrit_le_fil(commande, monkeypatch, capsys):
    F, vu = commande
    rep = {"nouvelles": [{"n": k, "dossiers": ["rsi"], "motif": "m"} for k in range(4)]}
    monkeypatch.setattr(fil, "demander", lambda inv, schema, g, note: (rep, 0.5))
    assert dos.main(["--graphe", "essai", "--max", "2"]) == 0
    out = capsys.readouterr().out
    assert "2 nouvelle(s) jugée(s), 0.50 $" in out and "rsi 2" in out and vu["ecrit"] == 1


def test_la_commande_sans_dossier_declare(monkeypatch, capsys):
    monkeypatch.setattr(dos, "du_graphe", lambda g: [])
    assert dos.main(["--graphe", "essai"]) == 0
    assert "aucun dossier déclaré" in capsys.readouterr().out


def test_les_libelles_d_une_declaration_illisible_sont_vides(monkeypatch):
    monkeypatch.setattr(dos, "du_graphe", lambda g: (_ for _ in ()).throw(ValueError("x")))
    assert dos.libelles("essai") == {}


def test_les_libelles_d_une_declaration(declares, monkeypatch):
    monkeypatch.setattr(dos, "du_graphe", lambda g: declares)
    assert dos.libelles("essai") == {"bugmageddon": "Bugmageddon", "rsi": "Auto-amélioration récursive"}


def test_une_fiche_qui_ne_s_ecrit_pas_garde_son_en_tete(tmp_path, monkeypatch, capsys):
    from newskit import fiche

    monkeypatch.setattr(dos, "RACINE", tmp_path)
    fiche_de(tmp_path, "sources/rss/blog/2026-03-02_x.md")
    monkeypatch.setattr(fiche, "ecrire", lambda *a, **k: (_ for _ in ()).throw(ValueError("contrat")))
    assert dos.ecrire_dans_la_fiche("sources/rss/blog/2026-03-02_x.md", ["rsi"]) is False
    assert "dossiers : ValueError" in capsys.readouterr().err


def test_la_construction_nomme_les_noeuds_de_dossier_par_la_declaration(monkeypatch):
    from newskit import partage_graphe

    f = {
        "champs": {"title": "Une faille", "dossiers": ["bugmageddon"]},
        "rel": "sources/a/x.md",
        "id": "x",
        "arxiv_id": None,
        "corps": "",
        "premiere_ligne": 1,
    }
    monkeypatch.setattr(cfg, "dossiers_du_graphe", lambda g: ["sources/a"])
    monkeypatch.setattr(partage_graphe, "fiches_du_graphe", lambda g: None)
    monkeypatch.setattr(sq, "charger_corpus", lambda av, d, fichiers: {Path("sources/a/x.md"): f})
    monkeypatch.setattr(dos, "libelles", lambda g: {"bugmageddon": "Bugmageddon"})
    extraction, stats, _ = sq.construire("essai")
    noeuds = {n["id"]: n for n in extraction["nodes"]}
    assert noeuds[sq.make_id("dossier", "bugmageddon")]["label"] == "Bugmageddon"
    assert stats["noeuds_dossier"] == 1


# --- une empreinte par dossier (mainteneur, 20.11.2026 : « une empreinte par dossier »)

EDUCATION = {
    "cle": "edtech",
    "fr": "Technologies éducatives",
    "en": "Educational technology",
    "critere": "IA à l'école et dans la formation.",
    "depuis": "",
}


def test_un_dossier_ajoute_seul_est_juge_et_les_autres_restent(declares, monkeypatch):
    F = fil_d_essai()
    for _, b, _ in dos.a_juger(F, declares):
        b["dossiers_vus"] = dos.vus(declares)
    F["billets"][0]["dossiers"] = ["bugmageddon"]
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    appels = []

    def demander(inv, schema, graphe, note):
        appels.append(inv)
        return {"nouvelles": [{"n": k, "dossiers": ["edtech", "rsi"], "motif": "m"} for k in range(4)]}, 0.1

    n, _ = dos.reclasser(F, "essai", declares + [EDUCATION], demander=demander)
    assert n == 4 and len(appels) == 1
    assert "edtech : IA à l'école" in appels[0] and "bugmageddon :" not in appels[0] and "rsi :" not in appels[0]
    # Le modèle rend aussi rsi, qui n'était pas jugé : rien ne change pour rsi.
    assert F["billets"][0]["dossiers"] == ["bugmageddon", "edtech"]
    assert F["billets"][1]["dossiers"] == ["edtech"]
    assert F["billets"][0]["dossiers_vus"] == dos.vus(declares + [EDUCATION])


def test_un_critere_change_rejuge_ce_dossier_seul(declares):
    b = {"chemin": "sources/a/2026-03-02_x.md", "date": "2026-03-02", "dossiers": ["bugmageddon", "rsi"]}
    b["dossiers_vus"] = dos.vus(declares)
    change = [{**declares[0], "critere": "autre"}, declares[1]]
    assert [d["cle"] for d in dos.a_revoir(b, change)] == ["bugmageddon"]
    dos.poser(b, [], [change[0]], declares=change)
    assert b["dossiers"] == ["rsi"] and b["dossiers_vus"] == dos.vus(change)


def test_un_dossier_retire_de_la_declaration_quitte_la_nouvelle(declares):
    b = {"chemin": "sources/a/2026-03-02_x.md", "date": "2026-03-02", "dossiers": ["bugmageddon", "ancien"]}
    b["dossiers_vus"] = {**dos.vus(declares), "ancien": "x"}
    dos.poser(b, ["rsi"], [declares[1]], declares=declares)
    assert b["dossiers"] == ["bugmageddon", "rsi"] and set(b["dossiers_vus"]) == {"bugmageddon", "rsi"}


def test_l_ordre_des_dossiers_suit_la_declaration(declares):
    b = {"chemin": "sources/a/2026-03-02_x.md", "date": "2026-03-02"}
    dos.poser(b, ["rsi", "bugmageddon"], declares)
    assert b["dossiers"] == ["bugmageddon", "rsi"]


def test_une_marque_illisible_fait_tout_juger(declares):
    assert len(dos.a_revoir({"dossiers_vus": "ancienne"}, declares)) == 2


def test_les_lots_se_font_par_dossiers_a_juger(declares, monkeypatch):
    F = fil_d_essai()
    for _, b, _ in dos.a_juger(F, declares):
        b["dossiers_vus"] = dos.vus(declares)
    F["billets"][0]["dossiers_vus"] = {}
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    lots = []

    def demander(inv, schema, graphe, note):
        lot = [json.loads(x) for x in inv.split("Nouvelles :\n", 1)[1].splitlines() if x.strip()]
        lots.append((len(lot), "bugmageddon :" in inv, "edtech :" in inv))
        return {"nouvelles": [{"n": x["n"], "dossiers": []} for x in lot]}, 0.1

    n, _ = dos.reclasser(F, "essai", declares + [EDUCATION], demander=demander)
    assert n == 4 and sorted(lots) == [(1, True, True), (3, False, True)]


def test_un_numero_rendu_deux_fois_compte_une_fois(declares, monkeypatch):
    F = fil_d_essai()
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    rep = {"nouvelles": [{"n": 0, "dossiers": ["rsi"]}, {"n": 0, "dossiers": []}]}
    n, _ = dos.reclasser(F, "essai", declares, demander=lambda *a: (rep, 0.1))
    assert n == 1 and F["pile"][0]["dossiers"] == ["rsi"]


@pytest.mark.parametrize("k", [-1, 4, "0"])
def test_un_numero_hors_du_lot_ne_compte_pas(declares, monkeypatch, k):
    F = fil_d_essai()
    monkeypatch.setattr(dos, "ecrire_dans_la_fiche", lambda chemin, cles: True)
    n, _ = dos.reclasser(
        F, "essai", declares, demander=lambda *a: ({"nouvelles": [{"n": k, "dossiers": ["rsi"]}]}, 0.1)
    )
    assert n == 0 and len(dos.a_juger(F, declares)) == 4

"""Les passes de texte d'un procédé d'ingestion (P2 du portage du procédé des papiers, mainteneur, 19.11.2026).

Un procédé lit le texte intégral d'une fiche de son type de contenu, envoie à chaque passe sa consigne et ce texte, et
garde la réponse. La passe déclarée `sortie: corps` devient le corps de la fiche, qui repasse alors en couche 2. Un
refus du modèle se note : la fiche reste « manquante, à ingérer ». Les passes à forme viennent ensuite (P4)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from newskit import choisir_jalons, couts, fiche, partage_graphe, procedes
from newskit import extraire_concepts as ec
from newskit import procedes_passes as pp

CONVENTION = {
    "nom": "essai-1",
    "noeuds": {"canoniques": [{"id": "notion"}], "propres": [{"id": "procedure"}, {"id": "step"}, {"id": "variant"}]},
    "relations": [{"id": "presents"}, {"id": "has_step"}, {"id": "precedes, follows"}],
}
LECTURE = " ".join(["mot"] * 30)


@pytest.fixture
def media(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "procedes").mkdir()
    (tmp_path / "procedes" / "lecture.txt").write_text("Lis le document.\n", encoding="utf-8")
    (tmp_path / "procedes" / "etapes.txt").write_text("Les étapes.\n", encoding="utf-8")
    (tmp_path / "convention.yaml").write_text(yaml.safe_dump(CONVENTION), encoding="utf-8")
    monkeypatch.setattr(pp, "RACINE", tmp_path)
    monkeypatch.setattr(pp, "PAUSE", 0)
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr(
        couts,
        "enregistrer",
        lambda *a, **k: json.loads(getattr(a[3], "stdout", "") or "{}") if len(a) > 3 and a[3] is not None else {},
    )
    return tmp_path


def declaration(media: Path, **texte) -> dict:
    decl = {
        "papier": {
            "contenu": {"champ": "arxiv_id"},
            "texte": {"integral": True, "adresse": "https://exemple.org/pdf/{arxiv_id}", "mots_max": 1000, **texte},
            "convention": "convention.yaml",
            "passes": {
                "lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps", "mots_min": 20},
                "procede": {"consigne": "procedes/etapes.txt", "forme": "etapes"},
            },
        }
    }
    return procedes.declaration(decl, media)["papier"]


def papier(media: Path, aid: str = "2610.39302", **champs) -> Path:
    p = media / "sources" / "arxiv" / f"2026-10-01_{aid}_essai.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    entete = {"title": "Un papier", "subtitle": "cs.LG", "author": "A. Auteur", "date": "2026-10-01", "url": f"https://arxiv.org/abs/{aid}",
              "plateforme": "arXiv", "publication": "arXiv", "collected": "2026-10-02", "source": "arXiv", "arxiv_id": aid, **champs}  # fmt: skip
    fiche.ecrire(p, entete, "# Un papier\n\nLe résumé d'arXiv.\n", strict=False, hors_graphe=True)
    return p


class Claude:
    """Un faux `claude -p` : rend `reponse`, compte les appels, garde les invites."""

    def __init__(self, reponse: str = LECTURE, stop: str = "end_turn", code: int = 0):
        self.reponse, self.stop, self.code, self.invites, self.args = reponse, stop, code, [], []

    def __call__(self, args, input=None, **k):
        self.args.append(args)
        self.invites.append(input)
        env = {
            "type": "result",
            "result": self.reponse,
            "is_error": self.code != 0,
            "stop_reason": self.stop,
            "total_cost_usd": 0.5,
        }
        return subprocess.CompletedProcess(args, self.code, stdout=json.dumps(env), stderr="")


@pytest.fixture
def texte_pdf(monkeypatch):
    """Le PDF « téléchargé » contient ce texte ; les adresses demandées se gardent."""
    vues: list[str] = []

    def telecharger(url: str, chemin: Path) -> None:
        vues.append(url)
        chemin.write_bytes(b"%PDF")

    monkeypatch.setattr(pp, "telecharger", telecharger)
    monkeypatch.setattr(
        pp, "lire_pdf", lambda chemin: ("Texte  intégral du\npapier. " + " ".join(f"m{i}" for i in range(2000)), 9, {})
    )
    return vues


# --- la déclaration : l'adresse du texte, le minimum de mots d'une passe


def test_l_adresse_du_texte_et_le_minimum_de_mots_se_declarent(media):
    d = declaration(media)
    assert d["texte"]["adresse"] == "https://exemple.org/pdf/{arxiv_id}"
    assert d["passes"][0]["mots_min"] == 20 and d["passes"][1]["mots_min"] == 1


@pytest.mark.parametrize("texte", [{"adresse": ""}, {"adresse": 3}, {"adresse": "ftp://x/{a}"}])
def test_une_adresse_mal_formee_se_refuse(media, texte):
    with pytest.raises(ValueError, match="adresse"):
        declaration(media, **texte)


@pytest.mark.parametrize("mots", [-1, "20", True])
def test_un_minimum_de_mots_mal_forme_se_refuse(media, mots):
    decl = {"papier": {"contenu": {"champ": "arxiv_id"}, "convention": "convention.yaml",
                       "passes": {"lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps", "mots_min": mots}}}}  # fmt: skip
    with pytest.raises(ValueError, match="mots_min"):
        procedes.declaration(decl, media)


# --- les fiches du type


def test_les_fiches_du_type_sont_celles_que_lit_la_couche_2_et_qui_portent_le_champ(media, monkeypatch):
    a = papier(media, "2610.00001")
    b = media / "sources" / "blog" / "2026-10-01_billet.md"
    b.parent.mkdir(parents=True)
    fiche.ecrire(b, {"title": "Billet", "date": "2026-10-01", "url": "https://exemple.org/b", "plateforme": "Flux RSS",
                     "publication": "Blog", "collected": "2026-10-02", "source": "Blog"}, "# Billet\n", strict=False, hors_graphe=True)  # fmt: skip
    monkeypatch.setattr(ec, "fiches_lues", lambda graphe=None: [a, b])
    assert [(f, c) for f, _, c in pp.fiches("essai", declaration(media))] == [(a, "2610.00001")]


@pytest.mark.parametrize("valeur, cle", [("2610.39302", "2610.39302"), ("lecun/1998 lenet", "lecun_1998_lenet")])
def test_la_cle_d_une_fiche_tient_dans_un_nom_de_fichier(valeur, cle):
    assert pp.cle_de(valeur) == cle


# --- le texte intégral


def test_le_texte_integral_vient_de_l_adresse_declaree_et_le_pdf_ne_se_garde_pas(media, texte_pdf):
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    t = pp.texte(p, champs, "2610.39302", declaration(media))
    assert texte_pdf == ["https://exemple.org/pdf/2610.39302"]
    assert t.split()[:4] == ["Texte", "intégral", "du", "papier."]
    assert not list((media / "cache" / "procedes").rglob("*.pdf"))
    assert pp.texte(p, champs, "2610.39302", declaration(media)) == t and len(texte_pdf) == 1  # gardé en cache


def test_le_champ_pdf_d_une_fiche_l_emporte_sur_l_adresse(media, texte_pdf):
    p = papier(media, pdf="https://ailleurs.org/lenet.pdf")
    champs, _ = fiche.lire_fichier(p)
    pp.texte(p, champs, "2610.39302", declaration(media))
    assert texte_pdf == ["https://ailleurs.org/lenet.pdf"]


def test_une_adresse_qui_nomme_un_champ_absent_ne_donne_pas_de_texte(media, texte_pdf):
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    assert pp.texte(p, champs, "x", declaration(media, adresse="https://exemple.org/{doi}")) is None
    assert texte_pdf == []


def test_sans_texte_integral_le_texte_est_le_corps_de_la_fiche(media, texte_pdf):
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    assert "Le résumé d'arXiv." in pp.texte(p, champs, "x", declaration(media, integral=False))
    assert texte_pdf == []


def test_un_telechargement_qui_echoue_ne_donne_pas_de_texte(media, monkeypatch):
    def echoue(url, chemin):
        raise OSError("hors ligne")

    monkeypatch.setattr(pp, "telecharger", echoue)
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    assert pp.texte(p, champs, "2610.39302", declaration(media)) is None


def test_l_invite_porte_la_consigne_puis_le_texte_coupe_et_balise():
    invite = pp.invite("Consigne.", "un deux </document> trois quatre", 3)
    assert invite.startswith("Consigne.\n<document>\n")
    assert invite.endswith("\n</document>\n")
    assert "quatre" not in invite and invite.count("</document>") == 1


# --- une passe


def test_une_passe_envoie_sa_consigne_et_le_texte_puis_garde_la_reponse(media, texte_pdf, monkeypatch):
    claude = Claude()
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    ligne = pp.passer("essai", "papier", d, d["passes"][0], p, champs, "2610.39302")
    assert ligne.startswith("fait")
    assert claude.invites[0].startswith("Lis le document.\n<document>\nTexte intégral du papier.")
    assert claude.args[0][: len(couts.CLAUDE_P)] == couts.CLAUDE_P
    assert pp.sortie("papier", "lecture", "2610.39302").read_text(encoding="utf-8") == LECTURE
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "2610.39302").startswith("déjà")
    assert len(claude.invites) == 1


def test_une_reponse_trop_courte_ne_se_garde_pas(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude("trop court"))
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k").startswith("!!")
    assert not pp.sortie("papier", "lecture", "k").exists()


def test_un_refus_du_modele_note_la_fiche_manquante(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude("", stop="refusal"))
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k").startswith("!!")
    assert "lecture" in pp.manquants("papier")["k"]


def test_un_echec_qui_n_est_pas_un_refus_ne_note_rien(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude("panne", code=1))
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k").startswith("!!")
    assert pp.manquants("papier") == {}


def test_une_passe_reussie_efface_la_marque_de_manquant(media, texte_pdf, monkeypatch):
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    pp.noter_manquant("papier", "k", "lecture", "refus du modèle")
    monkeypatch.setattr(pp.subprocess, "run", Claude())
    pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k")
    assert pp.manquants("papier") == {}


def test_en_limite_aucun_appel(media, texte_pdf, monkeypatch):
    claude = Claude()
    monkeypatch.setattr(pp.subprocess, "run", claude)
    monkeypatch.setattr(couts, "en_limite", lambda: True)
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k").startswith("!!")
    assert claude.invites == []


def test_un_delai_depasse_ne_garde_rien(media, texte_pdf, monkeypatch):
    def lent(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)

    monkeypatch.setattr(pp.subprocess, "run", lent)
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media)
    assert "délai" in pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k")


def test_sans_texte_aucun_appel(media, monkeypatch):
    claude = Claude()
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    champs, _ = fiche.lire_fichier(p)
    d = declaration(media, adresse="https://exemple.org/{doi}")
    assert pp.passer("essai", "papier", d, d["passes"][0], p, champs, "k").startswith("!!")
    assert claude.invites == []


# --- la fiche


def test_la_passe_en_corps_devient_le_corps_de_la_fiche_qui_repasse_en_couche_2(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude())
    p = papier(media)
    avant = ec.empreinte(fiche.lire_fichier(p)[1])
    lignes = pp.traiter("essai", "papier", declaration(media), p)
    champs, corps = fiche.lire_fichier(p)
    assert corps.lstrip().startswith("# Un papier\n\n*cs.LG · A. Auteur*\n\n" + LECTURE)
    assert "Le résumé d'arXiv." not in corps
    assert champs["procede"] == "papier:lecture"
    assert ec.empreinte(corps) != avant
    assert any(x.startswith("fait") for x in lignes)


def test_une_fiche_deja_ecrite_par_sa_passe_ne_se_reecrit_pas(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude())
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    mtime = p.stat().st_mtime_ns
    pp.traiter("essai", "papier", declaration(media), p)
    assert p.stat().st_mtime_ns == mtime


def test_les_passes_a_forme_attendent_leur_forme(media, texte_pdf, monkeypatch):
    claude = Claude()
    monkeypatch.setattr(pp.subprocess, "run", claude)
    pp.traiter("essai", "papier", declaration(media), papier(media))
    assert len(claude.invites) == 1  # la lecture seule ; le procédé suit sa forme (P4)


def test_le_texte_en_cache_s_efface_quand_toutes_les_passes_de_texte_sont_faites(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude())
    pp.traiter("essai", "papier", declaration(media), papier(media))
    assert not list((media / "cache" / "procedes").rglob("*.txt"))


def test_le_texte_en_cache_reste_tant_qu_une_passe_manque(media, texte_pdf, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude("court"))
    pp.traiter("essai", "papier", declaration(media), papier(media))
    assert list((media / "cache" / "procedes").rglob("*.txt"))


# --- la commande


def test_la_commande_traite_les_fiches_en_attente_sous_le_plafond(media, texte_pdf, monkeypatch, capsys):
    claude = Claude()
    monkeypatch.setattr(pp.subprocess, "run", claude)
    fiches_ = [papier(media, f"2610.0000{i}") for i in range(3)]
    monkeypatch.setattr(ec, "fiches_lues", lambda graphe=None: fiches_)
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda graphe: {"papier": declaration(media)})
    assert pp.main(["--graphe", "essai", "--type", "papier", "--plafond", "2"]) == 0
    assert len(claude.invites) == 2
    assert pp.main(["--graphe", "essai", "--type", "papier", "--etat"]) == 0
    assert "3 fiches, 2 faites, 1 en attente" in capsys.readouterr().out
    assert len(claude.invites) == 2


def test_la_commande_refuse_un_type_non_declare(media, monkeypatch):
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda graphe: {"papier": declaration(media)})
    with pytest.raises(SystemExit, match="article"):
        pp.main(["--graphe", "essai", "--type", "article"])


def test_la_declaration_du_graphe_se_lit_au_registre(media, monkeypatch):
    monkeypatch.setattr(pp.cfg, "graphe_declare", lambda nom: {"procedes": None})
    assert pp.declaration_du_graphe("essai") == {}


# --- les fiches que lit la couche 2 (extraire_concepts.fiches_lues)


def test_les_fiches_lues_sont_les_denses_les_jalons_et_les_papiers_de_veille_retenus(tmp_path, monkeypatch):
    def poser(rel: str, source: str = "x") -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\nsource: "{source}"\n---\ncorps\n', encoding="utf-8")
        return p

    dense = poser("sources/blog/2026-10-01_a.md")
    poser("sources/blog/_index.md")
    jalon = poser("sources/arxiv/2020-01-01_j.md")
    veille = poser("sources/veille/2026-11-01_v.md", "arXiv, thème « agents »")
    poser("sources/veille/2026-11-02_cite.md", "arXiv")
    poser("sources/ailleurs/2026-11-03_autre.md", "arXiv, thème « agents »")
    poser("sources/blog/2026-11-04_b.md", "arXiv, thème « agents »")
    poser("sources/veille/2020-01-01_vieux.md", "arXiv, thème « agents »")
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(ec.cfg, "dossiers_denses", lambda g=None: ["sources/blog"])
    monkeypatch.setattr(
        ec.cfg,
        "veilles_arxiv",
        lambda: [
            {"graphe": "autre", "dossier": "ailleurs"},
            {"graphe": "essai", "dossier": "veille"},
            {"graphe": "essai", "dossier": "blog"},
        ],
    )
    monkeypatch.setattr(ec.cfg, "HORIZON_MODELE", "2026-01-01")
    monkeypatch.setattr(
        choisir_jalons,
        "jalons_detailles",
        lambda g: {"1": {"chemin": "sources/arxiv/2020-01-01_j.md"}, "2": {"chemin": "sources/arxiv/absent.md"}},
    )
    assert ec.fiches_lues("essai") == [dense, tmp_path / "sources/blog/2026-11-04_b.md", jalon, veille]


def test_le_telechargement_ecrit_ce_que_rend_l_adresse(tmp_path, monkeypatch):
    class Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"%PDF-1.7"

    vues = []
    monkeypatch.setattr(
        pp.urllib.request,
        "urlopen",
        lambda req, timeout: vues.append((req.full_url, req.get_header("User-agent"))) or Reponse(),
    )
    pp.telecharger("https://exemple.org/a.pdf", tmp_path / "a.pdf")
    assert (tmp_path / "a.pdf").read_bytes() == b"%PDF-1.7"
    assert vues == [("https://exemple.org/a.pdf", "newskit")]


def test_les_fiches_en_retard_sont_les_fiches_lues_dont_l_empreinte_a_change(tmp_path, monkeypatch):
    def poser(nom: str, corps: str) -> Path:
        p = tmp_path / "sources" / "blog" / nom
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntitle: x\ncollected: 2026-11-0{len(nom) % 9}\n---\n{corps}\n", encoding="utf-8")
        return p

    a, b = poser("2026-11-01_a.md", "inchangé"), poser("2026-11-02_bb.md", "nouveau")
    monkeypatch.setattr(ec, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "fiches_lues", lambda graphe=None: [a, b])
    monkeypatch.setattr(ec, "refus_connus", lambda: {})
    monkeypatch.setattr(ec, "EMPREINTES", tmp_path / "empreintes.json")
    (tmp_path / "empreintes.json").write_text(
        json.dumps({"sources/blog/2026-11-01_a.md": ec.empreinte(ec.corps_de(a)[1])}), encoding="utf-8"
    )
    monkeypatch.setattr(partage_graphe, "fiches_du_graphe", lambda nom: None)
    assert [f for f, _ in ec.fiches_en_retard()] == [b]

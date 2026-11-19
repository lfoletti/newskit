"""Les passes à forme d'un procédé d'ingestion (P4 du portage du procédé des papiers, mainteneur, 15 et 19.11.2026).

Une passe à forme répond en JSON, au schéma de sa forme (`etapes` : procédés, étapes ordonnées, variantes ;
`composants` : architectures, composants, flux, sauts, blocs). Le moteur en tire, sans modèle, une section de la fiche
et des nœuds reliés, posés dans le lot de couche 2 de la fiche. Une passe peut d'abord trier : un oui ou un non sur le
début du texte, avant la lecture entière."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from newskit import couts, fiche, procedes
from newskit import extraire_concepts as ec
from newskit import procedes_formes as pf
from newskit import procedes_passes as pp

CONVENTION = {
    "nom": "essai-1",
    "noeuds": {
        "canoniques": [{"id": "notion"}, {"id": "model"}],
        "propres": [{"id": "procedure"}, {"id": "step"}, {"id": "variant"}, {"id": "component"}],
    },
    "relations": [
        {"id": "presents"},
        {"id": "has_step"},
        {"id": "precedes, follows"},
        {"id": "has_component"},
        {"id": "feeds"},
        {"id": "skip"},
        {"id": "instance_of"},
    ],  # fmt: skip
}
LECTURE = " ".join(["lecture"] * 30)
ETAPES = {
    "procedures": [
        {"name": "Pre-training", "purpose": "a model", "comes_after": None, "inputs": ["BooksCorpus"], "output": "BERT",
         "steps": [{"does": "Mask tokens", "settings": ["rate = 15 %"], "produces": "masked text", "where": "§3.1"},
                   {"does": "Optimize", "settings": ["optimizer = Adam", "lr = 1e-4"], "produces": "weights", "where": "§A.2"}]},
        {"name": "Fine-tuning", "purpose": "a classifier", "comes_after": 1, "inputs": [], "output": "scores",
         "steps": [{"does": "Train on GLUE", "settings": [], "produces": "a classifier", "where": "§4"}]},
    ],
    "variants": [{"name": "No NSP", "change": "drops next sentence prediction", "effect": "-1.0 MNLI", "where": "Table 5"}],
}  # fmt: skip
COMPOSANTS = {
    "defines_architecture": True,
    "architectures": [
        {"name": "ResNet-50", "family": "Convolutional network", "input": "224×224 image", "output": "1000 classes", "size": "25.6M",
         "components": [
             {"name": "conv1", "is": ["Convolutional Layer"], "repeats": "1", "settings": ["kernel = 7×7"], "receives_from": [], "skips": [], "where": "Table 1"},
             {"name": "conv2_x", "is": ["Bottleneck Block"], "repeats": "3", "settings": [], "receives_from": [1], "skips": [{"from": 1, "kind": "projection"}], "where": "Table 1"},
         ]},
    ],
    "blocks": [{"name": "Bottleneck Block", "components": [
        {"name": "1×1 conv", "is": ["Convolutional Layer"], "repeats": "1", "settings": [], "receives_from": [], "skips": [], "where": "Fig. 5"},
        {"name": "add", "is": [], "repeats": "1", "settings": [], "receives_from": [1], "skips": [{"from": None, "kind": "identity"}], "where": "Fig. 5"},
    ]}],
    "variants": [{"name": "ResNet-101", "differences": "23 blocks in conv4_x"}],
}  # fmt: skip
CHAMPS = {"title": "BERT", "subtitle": "cs.CL", "author": "Jacob Devlin, Ming-Wei Chang", "date": "2018-10-11",
          "url": "https://arxiv.org/abs/1810.04805", "collected": "2026-11-01", "arxiv_id": "1810.04805"}  # fmt: skip
PID = "concept_p1810_04805"


@pytest.fixture
def media(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "procedes").mkdir()
    for f in ("lecture", "etapes", "composants", "tri"):
        (tmp_path / "procedes" / f"{f}.txt").write_text(f"Consigne {f}.\n", encoding="utf-8")
    (tmp_path / "convention.yaml").write_text(yaml.safe_dump(CONVENTION), encoding="utf-8")
    for m in (pp, ec):
        monkeypatch.setattr(m, "RACINE", tmp_path)
    monkeypatch.setattr(ec, "LOTS", tmp_path / "graphe" / "couche2" / "fiches")
    monkeypatch.setattr(pp, "PAUSE", 0)
    monkeypatch.setattr(couts, "en_limite", lambda: False)
    monkeypatch.setattr(
        couts, "enregistrer", lambda *a, **k: json.loads(a[3].stdout) if len(a) > 3 and a[3] is not None else {}
    )
    monkeypatch.setattr(pp, "telecharger", lambda url, chemin: chemin.write_bytes(b"%PDF"))
    monkeypatch.setattr(
        pp, "lire_pdf", lambda chemin: ("Title. Abstract. " + " ".join(f"m{i}" for i in range(3000)), 9, {})
    )
    return tmp_path


def declaration(media: Path, tri: bool = True) -> dict:
    archi = {"consigne": "procedes/composants.txt", "forme": "composants"} | (
        {"tri": "procedes/tri.txt"} if tri else {}
    )
    decl = {
        "papier": {
            "contenu": {"champ": "arxiv_id"},
            "texte": {"adresse": "https://exemple.org/{arxiv_id}"},
            "convention": "convention.yaml",
            "passes": {
                "lecture": {"consigne": "procedes/lecture.txt", "sortie": "corps", "mots_min": 20},
                "procede": {"consigne": "procedes/etapes.txt", "forme": "etapes"},
                "architecture": archi,
            },
        }
    }
    return procedes.declaration(decl, media)["papier"]


def papier(media: Path) -> Path:
    p = media / "sources" / "arxiv" / "2018-10-11_1810.04805_bert.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fiche.ecrire(
        p,
        {**CHAMPS, "plateforme": "arXiv", "publication": "arXiv", "source": "arXiv"},
        "# BERT\n\nLe résumé.\n",
        strict=False,
        hors_graphe=True,
    )
    return p


class Claude:
    """Un faux `claude -p` : une réponse par consigne (le début de l'invite), en texte ou en JSON structuré."""

    def __init__(self, **reponses):
        self.reponses, self.appels = reponses, []

    def __call__(self, args, input=None, **k):
        nom = input.split(".")[0].split()[-1]
        schema = json.loads(args[args.index("--json-schema") + 1]) if "--json-schema" in args else None
        self.appels.append((nom, schema, args[args.index("--model") + 1], len(input.split())))
        r = self.reponses.get(nom, LECTURE)
        env = {"type": "result", "is_error": False, "stop_reason": "end_turn", "total_cost_usd": 0.1}
        env |= {"structured_output": r, "result": ""} if isinstance(r, dict) else {"result": r}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(env), stderr="")


def ctx(media: Path, passe: str = "procede") -> dict:
    return pf.contexte(media / "sources/arxiv/x.md", CHAMPS, "papier", passe, "1810.04805")


# --- les schémas


def test_chaque_forme_a_son_schema_et_le_tri_le_sien():
    assert set(pf.SCHEMAS) == set(procedes.FORMES)
    assert pf.SCHEMAS["etapes"]["required"] == ["procedures", "variants"]
    assert "defines_architecture" in pf.SCHEMAS["composants"]["required"]
    assert pf.SCHEMA_TRI["required"] == ["retenu"]


# --- les nœuds d'une forme, sans modèle


def test_les_etapes_posent_procedes_etapes_et_variantes_dans_l_ordre(media):
    noeuds, aretes = pf.noeuds("etapes", ETAPES, ctx(media))
    ids = {n["id"]: n for n in noeuds}
    p1, p2 = f"{PID}__proc_1_pre_training", f"{PID}__proc_2_fine_tuning"
    assert ids[p1]["label"] == "Pre-training (Devlin 2018)" and ids[p1]["nature"] == "procedure"
    assert ids[f"{p1}__step_2"]["nature"] == "step"
    assert ids[f"{p1}__step_2"]["label"].startswith("Step 2 of Pre-training: Optimize")
    assert "optimizer = Adam" in ids[f"{p1}__step_2"]["rationale"]
    assert ids[f"{PID}__var_no_nsp"]["nature"] == "variant"
    rel = {(a["source"], a["relation"], a["target"]) for a in aretes}
    fid = ec.identifiant_de_fiche(media / "sources/arxiv/x.md", CHAMPS)
    assert (fid, "presents", p1) in rel and (fid, "presents", f"{PID}__var_no_nsp") in rel
    assert (p1, "has_step", f"{p1}__step_1") in rel
    assert (f"{p1}__step_1", "precedes", f"{p1}__step_2") in rel and (
        f"{p1}__step_2",
        "follows",
        f"{p1}__step_1",
    ) in rel
    assert (p1, "precedes", p2) in rel and (p2, "follows", p1) in rel
    assert all(a["contributor"] == "procédé papier:procede" for a in aretes)
    assert all(n["contributor"] == "procédé papier:procede" for n in noeuds)


def test_un_rang_anterieur_inconnu_ne_relie_rien(media):
    data = {"procedures": [{**ETAPES["procedures"][0], "comes_after": 7}], "variants": []}
    _, aretes = pf.noeuds("etapes", data, ctx(media))
    assert not [a for a in aretes if a["relation"] in ("precedes", "follows") and "__step_" not in a["source"]]


def test_les_composants_posent_architecture_composants_flux_sauts_et_blocs(media):
    noeuds, aretes = pf.noeuds("composants", COMPOSANTS, ctx(media, "architecture"))
    ids = {n["id"]: n for n in noeuds}
    a = f"{PID}__arch_1_resnet_50"
    b = f"{PID}__bloc_bottleneck_block"
    assert ids[a]["nature"] == "model" and ids[a]["label"] == "Architecture: ResNet-50 (Devlin 2018)"
    assert ids[f"{a}__comp_2"]["nature"] == "component" and ids[b]["nature"] == "component"
    assert (
        ids["concept_convolutional_layer"]["label"] == "Convolutional Layer"
        and "nature" not in ids["concept_convolutional_layer"]
    )
    rel = {(x["source"], x["relation"], x["target"]) for x in aretes}
    assert (a, "has_component", f"{a}__comp_1") in rel
    assert (f"{a}__comp_1", "feeds", f"{a}__comp_2") in rel
    assert (f"{a}__comp_1", "skip", f"{a}__comp_2") in rel
    assert (f"{a}__comp_1", "instance_of", "concept_convolutional_layer") in rel
    assert (b, "instance_of", "concept_bottleneck_block") in rel
    assert (b, "skip", f"{b}__comp_2") in rel  # saut depuis l'entrée du bloc
    fid = ec.identifiant_de_fiche(media / "sources/arxiv/x.md", CHAMPS)
    assert (fid, "presents", a) in rel and (fid, "presents", b) in rel
    assert len([n for n in noeuds if n["id"] == "concept_convolutional_layer"]) == 1


def test_sans_architecture_aucun_noeud(media):
    assert pf.noeuds(
        "composants", {"defines_architecture": False, "architectures": [], "blocks": [], "variants": []}, ctx(media)
    ) == ([], [])


# --- la section de la fiche


def test_les_etapes_se_rendent_en_section_de_fiche():
    md = pf.rendre("etapes", ETAPES)
    assert md.startswith("## Procedures\n\n### 1. Pre-training\n- **Purpose**: a model\n- **Comes after**: none")
    assert "  2. **Does**: Optimize\n     - **Settings**: optimizer = Adam; lr = 1e-4" in md
    assert "- **Comes after**: Procedure 1" in md
    assert (
        "## Variants and ablations of the procedure\n\n### No NSP\n- **Change**: drops next sentence prediction" in md
    )


def test_les_composants_se_rendent_en_section_de_fiche():
    md = pf.rendre("composants", COMPOSANTS)
    assert md.startswith("## Architectures\n\n### ResNet-50\n- **Family**: Convolutional network")
    assert "  2. conv2_x\n     - **Is**: Bottleneck Block\n" in md
    assert "     - **Receives from**: 1; skip from 1 (projection)" in md
    assert "## Blocks\n\n### Bottleneck Block\n" in md and "skip from input (identity)" in md
    assert "## Variants\n\n### ResNet-101\n- 23 blocks in conv4_x" in md
    assert (
        pf.rendre("composants", {"defines_architecture": False, "architectures": [], "blocks": [], "variants": []})
        == ""
    )


# --- le lot de la fiche


def test_le_lot_garde_l_extraction_et_reprend_les_noeuds_des_formes(media):
    p = papier(media)
    d = declaration(media)
    for passe, data in (("procede", ETAPES), ("architecture", COMPOSANTS)):
        pp.sortie("papier", passe, "1810.04805", ".json").parent.mkdir(parents=True, exist_ok=True)
        pp.sortie("papier", passe, "1810.04805", ".json").write_text(json.dumps(data), encoding="utf-8")
    champs, _ = fiche.lire_fichier(p)
    lot = {"nodes": [{"id": "concept_bert", "label": "BERT", "contributor": "Claude"}], "edges": [], "hyperedges": []}
    lot = pf.composer_lot(lot, p, champs, "papier", d)
    ids = [n["id"] for n in lot["nodes"]]
    assert "concept_bert" in ids and f"{PID}__proc_1_pre_training" in ids and f"{PID}__arch_1_resnet_50" in ids
    fid = ec.identifiant_de_fiche(p, champs)
    assert ids.count(fid) == 1
    deux = pf.composer_lot(json.loads(json.dumps(lot)), p, champs, "papier", d)
    assert len(deux["nodes"]) == len(lot["nodes"]) and len(deux["edges"]) == len(lot["edges"])


def test_l_extraction_d_une_fiche_du_type_ecrit_un_lot_avec_ses_formes(media, monkeypatch):
    p = papier(media)
    d = declaration(media)
    pp.sortie("papier", "procede", "1810.04805", ".json").parent.mkdir(parents=True, exist_ok=True)
    pp.sortie("papier", "procede", "1810.04805", ".json").write_text(json.dumps(ETAPES), encoding="utf-8")
    monkeypatch.setattr(ec, "procede_de", lambda champs: d if champs.get("arxiv_id") else None)
    monkeypatch.setattr(ec, "PARALLELE", 1)
    monkeypatch.setattr(ec, "refus_connus", lambda: {})
    monkeypatch.setattr(ec, "EMPREINTES", media / "graphe" / "couche2" / "empreintes.json")
    (media / "graphe" / "couche2" / "fiches").mkdir(parents=True, exist_ok=True)
    reponse = {
        "nodes": [{"id": "concept_bert", "label": "BERT", "file_type": "concept", "nature": "model"}],
        "edges": [],
    }
    monkeypatch.setattr(ec, "appeler_claude", lambda invite, schema=None: (reponse, "ok"))
    ec.extraire([(p, "h")])
    lot = json.loads(
        (ec.LOTS / f"{ec.identifiant_de_fiche(p, fiche.lire_fichier(p)[0])}.json").read_text(encoding="utf-8")
    )
    assert f"{PID}__proc_1_pre_training" in [n["id"] for n in lot["nodes"]]


# --- les passes à forme


def test_une_passe_a_forme_envoie_le_schema_de_sa_forme_et_garde_le_json(media, monkeypatch):
    claude = Claude(etapes=ETAPES)
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    ligne = pp.passer("essai", "papier", d, d["passes"][1], p, champs, "1810.04805")
    assert ligne.startswith("fait")
    assert claude.appels[0][1] == pf.SCHEMAS["etapes"]
    assert json.loads(pp.sortie("papier", "procede", "1810.04805", ".json").read_text(encoding="utf-8")) == ETAPES
    assert pp.passer("essai", "papier", d, d["passes"][1], p, champs, "1810.04805").startswith("déjà")


def test_le_tri_lit_le_debut_du_texte_avec_le_modele_de_lecture_et_ecarte_sans_lire_le_tout(media, monkeypatch):
    claude = Claude(tri={"retenu": False})
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    ligne = pp.passer("essai", "papier", d, d["passes"][2], p, champs, "1810.04805")
    assert ligne.startswith("écartée")
    assert [a[0] for a in claude.appels] == ["tri"]
    assert claude.appels[0][1] == pf.SCHEMA_TRI
    assert claude.appels[0][2] == pp.media.modele("lecture")
    assert claude.appels[0][3] <= pp.MOTS_TRI + 10
    assert (
        json.loads(pp.sortie("papier", "architecture", "1810.04805", ".json").read_text(encoding="utf-8"))
        == pf.VIDE["composants"]
    )


def test_le_tri_qui_retient_laisse_lire_le_texte_entier(media, monkeypatch):
    claude = Claude(tri={"retenu": True}, composants=COMPOSANTS)
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    assert pp.passer("essai", "papier", d, d["passes"][2], p, champs, "1810.04805").startswith("fait")
    assert [a[0] for a in claude.appels] == ["tri", "composants"]
    assert claude.appels[1][3] > pp.MOTS_TRI


def test_un_tri_sans_reponse_ne_decide_rien(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(tri="pas du JSON"))
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    assert pp.passer("essai", "papier", d, d["passes"][2], p, champs, "1810.04805").startswith("!!")
    assert not pp.sortie("papier", "architecture", "1810.04805", ".json").exists()


def test_une_forme_sans_json_ne_se_garde_pas(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes="du texte"))
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    ligne = pp.passer("essai", "papier", d, d["passes"][1], p, champs, "1810.04805")
    assert ligne.startswith("!!") and "sans JSON" in ligne
    assert not pp.sortie("papier", "procede", "1810.04805", ".json").exists()


# --- la fiche, une seule fois


def test_la_fiche_s_ecrit_une_fois_apres_toutes_les_passes_lecture_puis_sections(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": True}, composants=COMPOSANTS))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    champs, corps = fiche.lire_fichier(p)
    assert champs["procede"] == "papier:lecture"
    corps = corps.lstrip()
    assert corps.index(LECTURE) < corps.index("## Procedures") < corps.index("## Architectures")


def test_tant_qu_une_passe_manque_la_fiche_attend(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes="panne", tri={"retenu": True}, composants=COMPOSANTS))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    champs, corps = fiche.lire_fichier(p)
    assert "procede" not in champs and "Le résumé." in corps


def test_une_passe_ecartee_par_le_tri_compte_pour_faite(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": False}))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    champs, corps = fiche.lire_fichier(p)
    assert champs["procede"] == "papier:lecture" and "## Architectures" not in corps
    assert not list((media / "cache" / "procedes").rglob("*.txt"))


def test_un_refus_note_la_fiche_et_la_fiche_s_ecrit_sans_cette_section(media, monkeypatch):
    class Refus(Claude):
        def __call__(self, args, input=None, **k):
            if input.startswith("Consigne etapes"):
                env = {"type": "result", "result": "", "is_error": False, "stop_reason": "refusal"}
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(env), stderr="")
            return super().__call__(args, input, **k)

    monkeypatch.setattr(pp.subprocess, "run", Refus(tri={"retenu": True}, composants=COMPOSANTS))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    champs, corps = fiche.lire_fichier(p)
    assert "procede" in pp.manquants("papier")["1810.04805"]
    assert champs["procede"] == "papier:lecture" and "## Procedures" not in corps and "## Architectures" in corps


def test_les_formes_reposent_le_lot_existant_de_la_fiche(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": False}))
    p = papier(media)
    lot = ec.LOTS / f"{ec.identifiant_de_fiche(p, fiche.lire_fichier(p)[0])}.json"
    lot.parent.mkdir(parents=True, exist_ok=True)
    lot.write_text(
        json.dumps({"nodes": [{"id": "concept_bert", "label": "BERT"}], "edges": [], "hyperedges": []}),
        encoding="utf-8",
    )
    pp.traiter("essai", "papier", declaration(media), p)
    assert f"{PID}__proc_1_pre_training" in [n["id"] for n in json.loads(lot.read_text(encoding="utf-8"))["nodes"]]


def test_un_lot_deja_repose_ne_se_reecrit_pas(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": False}))
    p = papier(media)
    lot = ec.LOTS / f"{ec.identifiant_de_fiche(p, fiche.lire_fichier(p)[0])}.json"
    lot.parent.mkdir(parents=True, exist_ok=True)
    lot.write_text(json.dumps({"nodes": [], "edges": [], "hyperedges": []}), encoding="utf-8")
    d = declaration(media)
    pp.traiter("essai", "papier", d, p)
    avant = lot.stat().st_mtime_ns
    assert pp.reposer_lot(p, "papier", d) is False
    assert lot.stat().st_mtime_ns == avant


def test_une_reponse_de_forme_qui_change_retire_les_anciens_noeuds(media):
    p = papier(media)
    d = declaration(media)
    f = pp.sortie("papier", "procede", "1810.04805", ".json")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(ETAPES), encoding="utf-8")
    champs, _ = fiche.lire_fichier(p)
    lot = pf.composer_lot({"nodes": [], "edges": []}, p, champs, "papier", d)
    f.write_text(json.dumps({"procedures": ETAPES["procedures"][:1], "variants": []}), encoding="utf-8")
    lot = pf.composer_lot(lot, p, champs, "papier", d)
    ids = [n["id"] for n in lot["nodes"]]
    assert f"{PID}__proc_2_fine_tuning" not in ids and f"{PID}__var_no_nsp" not in ids


def test_une_notion_deja_extraite_n_entre_pas_deux_fois(media):
    p = papier(media)
    d = declaration(media)
    f = pp.sortie("papier", "architecture", "1810.04805", ".json")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(COMPOSANTS), encoding="utf-8")
    champs, _ = fiche.lire_fichier(p)
    lot = {
        "nodes": [{"id": "concept_convolutional_layer", "label": "Convolutional Layer", "nature": "notion"}],
        "edges": [],
    }
    lot = pf.composer_lot(lot, p, champs, "papier", d)
    assert [n["id"] for n in lot["nodes"]].count("concept_convolutional_layer") == 1


def test_une_reponse_gardee_d_une_passe_a_sortie_ne_pose_rien(media):
    p = papier(media)
    d = declaration(media)
    f = pp.sortie("papier", "lecture", "1810.04805", ".json")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(ETAPES), encoding="utf-8")
    champs, _ = fiche.lire_fichier(p)
    assert pf.composer_lot({"nodes": [], "edges": []}, p, champs, "papier", d) == {"nodes": [], "edges": []}


def test_une_forme_faite_ne_se_refait_pas_quel_que_soit_le_minimum_de_mots(media, monkeypatch):
    claude = Claude(etapes=ETAPES)
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    d = declaration(media)
    passe = {**d["passes"][1], "mots_min": 100000}
    champs, _ = fiche.lire_fichier(p)
    pp.passer("essai", "papier", d, passe, p, champs, "1810.04805")
    assert pp.passer("essai", "papier", d, passe, p, champs, "1810.04805").startswith("déjà")
    assert len(claude.appels) == 1


def test_deux_passes_refusees_se_notent_toutes_deux(media):
    pp.noter_manquant("papier", "k", "procede", "refus du modèle")
    pp.noter_manquant("papier", "k", "architecture", "refus du modèle")
    assert set(pp.manquants("papier")["k"]) == {"procede", "architecture"}


def test_sans_lot_de_couche_2_les_passes_n_en_creent_pas(media, monkeypatch):
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": False}))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    assert not ec.LOTS.exists() or not list(ec.LOTS.glob("*.json"))


def test_une_fiche_aux_passes_faites_ou_refusees_n_est_plus_en_attente(media, monkeypatch, capsys):
    pp.noter_manquant("papier", "1810.04805", "procede", "refus du modèle")
    monkeypatch.setattr(pp.subprocess, "run", Claude(tri={"retenu": False}))
    p = papier(media)
    d = declaration(media)
    pp.traiter("essai", "papier", d, p)
    monkeypatch.setattr(ec, "fiches_lues", lambda graphe=None: [p])
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda graphe: {"papier": d})
    pp.main(["--graphe", "essai", "--type", "papier", "--etat"])
    assert "1 fiches, 1 faites, 0 en attente, 1 manquantes" in capsys.readouterr().out


def test_la_commande_vise_le_graphe_avant_de_toucher_ses_lots(media, monkeypatch):
    vises = []
    monkeypatch.setattr(ec, "viser", lambda graphe: vises.append(graphe) or {})
    monkeypatch.setattr(ec, "fiches_lues", lambda graphe=None: [])
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda graphe: {"papier": declaration(media)})
    pp.main(["--graphe", "essai", "--type", "papier", "--etat"])
    assert vises == ["essai"]

"""La structure des titres sans graphify (chantier E2, 14.11.2026) : sur un arbre de fiches qui passe par chaque chemin
de l'extraction Markdown, `structure_titres.extraire` rend exactement ce que rendait `graphify.extract.extract` 0.9.20
(attendu figé dans fixtures/sans_graphify/structure.json, produit par graphify) : mêmes nœuds, mêmes arêtes, mêmes
attributs, dans le même ordre."""

import base64
import json
from pathlib import Path

from newskit import structure_titres as st

FIXTURE = Path(__file__).parent / "fixtures" / "sans_graphify" / "structure.json"


def _arbre(tmp_path: Path) -> tuple[Path, dict]:
    f = json.loads(FIXTURE.read_text(encoding="utf-8"))
    racine = tmp_path.resolve() / "racine"
    for r, t in f["textes"].items():
        (racine / r).parent.mkdir(parents=True, exist_ok=True)
        (racine / r).write_text(t, encoding="utf-8")
    for r, b in f["octets"].items():
        (racine / r).write_bytes(base64.b64decode(b))
    for r in f["dossiers"]:
        (racine / r).mkdir(parents=True, exist_ok=True)
    for r, t in f["dehors"].items():
        (racine.parent / r).parent.mkdir(parents=True, exist_ok=True)
        (racine.parent / r).write_text(t, encoding="utf-8")
    for r, c in f["liens"].items():
        (racine / r).parent.mkdir(parents=True, exist_ok=True)
        (racine / r).symlink_to(racine.parent / c)
    texte = json.dumps(f["attendu"], ensure_ascii=False)
    for cle, val in (
        ("{RACINE_ID}", st.make_id(str(racine))),
        ("{RACINE}", str(racine)),
        ("{PARENT_ID}", st.make_id(str(racine.parent))),
        ("{PARENT}", str(racine.parent)),
    ):
        texte = texte.replace(cle, val)
    return racine, json.loads(texte)


def test_la_structure_des_titres_est_celle_de_graphify(tmp_path):
    racine, attendu = _arbre(tmp_path)
    fichiers = sorted((racine / "sources").rglob("*.md"))
    obtenu = st.extraire(fichiers, racine)
    assert json.dumps(obtenu, ensure_ascii=False, indent=1) == json.dumps(attendu, ensure_ascii=False, indent=1)


def test_sans_collision_les_identifiants_restent_ceux_des_fiches(tmp_path):
    racine = tmp_path.resolve()
    (racine / "a.md").write_text("# Un\n## Deux\n", encoding="utf-8")
    (racine / "b.md").write_text("# Un\n", encoding="utf-8")
    obtenu = st.extraire([racine / "a.md", racine / "b.md"], racine)
    assert [n["id"] for n in obtenu["nodes"]] == ["a", "a_un", "a_deux", "b", "b_un"]
    assert [(e["source"], e["target"]) for e in obtenu["edges"]] == [("a", "a_un"), ("a_un", "a_deux"), ("b", "b_un")]

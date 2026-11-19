"""Les formes des passes d'un procédé d'ingestion (procedes.py ; mainteneur, 15 et 19.11.2026).

Une passe à forme répond en JSON, au schéma de sa forme. Sans modèle, le moteur en tire une section de la fiche et des
nœuds reliés, posés dans le lot de couche 2 de la fiche (`composer_lot`), sous le contributeur `procédé <type>:<passe>`
qu'un nouveau passage remplace.

    etapes      procédés dans l'ordre d'exécution, leurs étapes et leurs réglages, les variantes essayées ;
                nœuds procedure, step, variant ; liens presents, has_step, precedes, follows.
    composants  architectures décrites comme des composants que traversent les données, blocs répétés, variantes ;
                nœuds model, component et le type canonique de chaque composant ; liens presents, has_component,
                feeds, skip, instance_of.

Les types et les relations que pose une forme figurent dans la convention du média (procedes.FORMES, vérifié au
chargement). Les libellés propres au document finissent par « (<premier auteur> <année>) » ; le type canonique d'un
composant garde son nom seul, pour se fondre avec la même notion lue ailleurs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_TEXTE = {"type": "string"}
_TEXTES = {"type": "array", "items": _TEXTE}


def _objet(**proprietes) -> dict:
    return {"type": "object", "properties": proprietes, "required": list(proprietes), "additionalProperties": False}


_ETAPE = _objet(does=_TEXTE, settings=_TEXTES, produces=_TEXTE, where=_TEXTE)
_COMPOSANT = _objet(
    name=_TEXTE,
    **{"is": _TEXTES},
    repeats=_TEXTE,
    settings=_TEXTES,
    receives_from={"type": "array", "items": {"type": "integer"}},
    skips={
        "type": "array",
        "items": _objet(
            **{"from": {"type": ["integer", "null"]}}, kind={"type": "string", "enum": ["identity", "projection"]}
        ),
    },
    where=_TEXTE,
)
SCHEMAS = {
    "etapes": _objet(
        procedures={
            "type": "array",
            "items": _objet(
                name=_TEXTE,
                purpose=_TEXTE,
                comes_after={"type": ["integer", "null"]},
                inputs=_TEXTES,
                steps={"type": "array", "items": _ETAPE},
                output=_TEXTE,
            ),
        },
        variants={"type": "array", "items": _objet(name=_TEXTE, change=_TEXTE, effect=_TEXTE, where=_TEXTE)},
    ),
    "composants": _objet(
        defines_architecture={"type": "boolean"},
        architectures={
            "type": "array",
            "items": _objet(
                name=_TEXTE,
                family=_TEXTE,
                input=_TEXTE,
                output=_TEXTE,
                size=_TEXTE,
                components={"type": "array", "items": _COMPOSANT},
            ),
        },
        blocks={"type": "array", "items": _objet(name=_TEXTE, components={"type": "array", "items": _COMPOSANT})},
        variants={"type": "array", "items": _objet(name=_TEXTE, differences=_TEXTE)},
    ),
}
# La réponse d'une passe que le tri écarte : la forme vide.
VIDE = {
    "etapes": {"procedures": [], "variants": []},
    "composants": {"defines_architecture": False, "architectures": [], "blocks": [], "variants": []},
}
SCHEMA_TRI = _objet(retenu={"type": "boolean"})


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:60]


def contexte(chemin: Path, champs: dict, type_: str, passe: str, cle: str) -> dict:
    """Ce que posent les nœuds d'une fiche : l'identifiant de la fiche, le préfixe des nœuds propres, le nom court,
    le gabarit des nœuds, le contributeur."""
    from newskit import extraire_concepts as ec  # noqa: PLC0415

    v = ec.valeurs_de_consigne(champs, cle)
    return {
        "fid": ec.identifiant_de_fiche(chemin, champs),
        "pid": f"concept_p{v['cle_id']}",
        "court": v["court"],
        "contributeur": f"procédé {type_}:{passe}",
        "gabarit": {
            "file_type": "concept",
            "source_file": str(Path(chemin).resolve()),
            "source_location": None,
            "source_url": champs.get("url") or None,
            "captured_at": champs.get("collected") or None,
            "author": champs.get("author") or None,
        },
    }


class _Pose:
    def __init__(self, c: dict):
        self.c, self.noeuds, self.aretes, self.vus = c, [], [], set()

    def noeud(self, i: str, label: str, texte: str, nature: str | None, propre: bool = True) -> None:
        if i in self.vus:
            return
        self.vus.add(i)
        n = {**self.c["gabarit"], "id": i, "label": f"{label} ({self.c['court']})" if propre else label}
        n |= {"rationale": texte.strip()[:1800], "evidence": (texte.strip().splitlines() or [label])[0][:200]}
        n |= ({"nature": nature} if nature else {}) | {"contributor": self.c["contributeur"]}
        self.noeuds.append(n)

    def arete(self, s: str, t: str, relation: str, preuve: str) -> None:
        self.aretes.append(
            {
                "source": s,
                "target": t,
                "relation": relation,
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "evidence": str(preuve)[:200],
                "source_file": self.c["gabarit"]["source_file"],
                "source_location": None,
                "weight": 1.0,
                "contributor": self.c["contributeur"],
            }
        )


def _etapes(data: dict, p: _Pose) -> None:
    c, rang = p.c, {}
    for k, pr in enumerate(data.get("procedures") or [], 1):
        i = f"{c['pid']}__proc_{k}_{_slug(pr['name'])}"
        rang[k] = i
        texte = f"Purpose: {pr['purpose']}\nInputs: {'; '.join(pr['inputs'])}\nOutput: {pr['output']}"
        p.noeud(i, pr["name"], texte, "procedure")
        p.arete(c["fid"], i, "presents", pr["name"])
        avant = pr.get("comes_after")
        if avant in rang and avant != k:
            p.arete(rang[avant], i, "precedes", f"Comes after: Procedure {avant}")
            p.arete(i, rang[avant], "follows", f"Comes after: Procedure {avant}")
        precedent = None
        for s, e in enumerate(pr.get("steps") or [], 1):
            j = f"{i}__step_{s}"
            texte = f"{e['does']}\nSettings: {'; '.join(e['settings']) or 'none'}\nProduces: {e['produces']}\nWhere: {e['where']}"
            p.noeud(j, f"Step {s} of {pr['name']}: {e['does'][:80]}", texte, "step")
            p.arete(i, j, "has_step", e["does"])
            if precedent:
                p.arete(precedent, j, "precedes", e["does"])
                p.arete(j, precedent, "follows", e["does"])
            precedent = j
    for v in data.get("variants") or []:
        i = f"{c['pid']}__var_{_slug(v['name'])}"
        p.noeud(
            i, f"Variant: {v['name'][:100]}", f"{v['change']}\nEffect: {v['effect']}\nWhere: {v['where']}", "variant"
        )
        p.arete(c["fid"], i, "presents", v["name"])


def _composants(parent: str, titre: str, liste: list[dict], p: _Pose) -> None:
    rang: dict[int, str] = {}
    for r, e in enumerate(liste, 1):
        i = f"{parent}__comp_{r}"
        rang[r] = i
        texte = f"{e['name']}: {' + '.join(e['is'])} × {e['repeats']}\nSettings: {'; '.join(e['settings']) or 'none'}\nWhere: {e['where']}"
        p.noeud(i, f"Component {r} of {titre}: {e['name'][:80]}", texte, "component")
        p.arete(parent, i, "has_component", e["name"])
        for t in e["is"]:
            canon = f"concept_{_slug(t)}"
            p.noeud(canon, t, f"{t}: component type named by {titre}.", None, propre=False)
            p.arete(i, canon, "instance_of", t)
        for k in e["receives_from"]:
            if k in rang and k != r:
                p.arete(rang[k], i, "feeds", f"Receives from: {k}")
        for saut in e["skips"]:
            origine = parent if saut["from"] is None else rang.get(saut["from"])
            if origine and origine != i:
                p.arete(origine, i, "skip", f"skip from {saut['from'] or 'input'} ({saut['kind']})")


def _architectures(data: dict, p: _Pose) -> None:
    c = p.c
    for k, a in enumerate(data.get("architectures") or [], 1):
        i = f"{c['pid']}__arch_{k}_{_slug(a['name'])}"
        texte = f"Family: {a['family']}\nInput: {a['input']}\nOutput: {a['output']}\nSize: {a['size']}"
        p.noeud(i, f"Architecture: {a['name']}", texte, "model")
        p.arete(c["fid"], i, "presents", a["name"])
        _composants(i, a["name"], a["components"], p)
    for b in data.get("blocks") or []:
        i = f"{c['pid']}__bloc_{_slug(b['name'])}"
        p.noeud(i, f"Block: {b['name']}", b["name"], "component")
        p.arete(c["fid"], i, "presents", b["name"])
        canon = f"concept_{_slug(b['name'])}"
        p.noeud(canon, b["name"], f"{b['name']}: block named by the architecture.", None, propre=False)
        p.arete(i, canon, "instance_of", b["name"])
        _composants(i, b["name"], b["components"], p)


def noeuds(forme: str, data: dict, c: dict) -> tuple[list[dict], list[dict]]:
    """Les nœuds et les arêtes d'une réponse de forme, sans modèle."""
    p = _Pose(c)
    {"etapes": _etapes, "composants": _architectures}[forme](data, p)
    return p.noeuds, p.aretes


def _liste_de_composants(liste: list[dict]) -> list[str]:
    out = []
    for r, e in enumerate(liste, 1):
        recoit = [str(k) for k in e["receives_from"]] + [
            f"skip from {s['from'] if s['from'] is not None else 'input'} ({s['kind']})" for s in e["skips"]
        ]
        out += [
            f"  {r}. {e['name']}",
            f"     - **Is**: {' + '.join(e['is']) or 'not stated'}",
            f"     - **Repeats**: {e['repeats']}",
            f"     - **Settings**: {'; '.join(e['settings']) or 'not stated'}",
            f"     - **Receives from**: {'; '.join(recoit) or 'input'}",
            f"     - **Where**: {e['where']}",
        ]
    return out


def rendre(forme: str, data: dict) -> str:
    """La section de la fiche tirée d'une réponse de forme ; vide quand la forme est vide."""
    L: list[str] = []
    if forme == "etapes":
        if data["procedures"]:
            L += ["## Procedures", ""]
        for k, pr in enumerate(data["procedures"], 1):
            avant = f"Procedure {pr['comes_after']}" if pr.get("comes_after") else "none"
            L += [f"### {k}. {pr['name']}", f"- **Purpose**: {pr['purpose']}", f"- **Comes after**: {avant}"]
            L += [f"- **Inputs**: {'; '.join(pr['inputs']) or 'not stated'}", "- **Steps**:"]
            for s, e in enumerate(pr["steps"], 1):
                L += [
                    f"  {s}. **Does**: {e['does']}",
                    f"     - **Settings**: {'; '.join(e['settings']) or 'not stated'}",
                ]
                L += [f"     - **Produces**: {e['produces']}", f"     - **Where**: {e['where']}"]
            L += [f"- **Output**: {pr['output']}", ""]
        if data["variants"]:
            L += ["## Variants and ablations of the procedure", ""]
        for v in data["variants"]:
            L += [f"### {v['name']}", f"- **Change**: {v['change']}", f"- **Effect**: {v['effect']}"]
            L += [f"- **Where**: {v['where']}", ""]
    else:
        if data["architectures"]:
            L += ["## Architectures", ""]
        for a in data["architectures"]:
            L += [f"### {a['name']}", f"- **Family**: {a['family']}", f"- **Input**: {a['input']}"]
            L += [f"- **Output**: {a['output']}", f"- **Size**: {a['size']}", "- **Components**:"]
            L += _liste_de_composants(a["components"]) + [""]
        if data["blocks"]:
            L += ["## Blocks", ""]
        for b in data["blocks"]:
            L += [f"### {b['name']}"] + _liste_de_composants(b["components"]) + [""]
        if data["variants"]:
            L += ["## Variants", ""]
        for v in data["variants"]:
            L += [f"### {v['name']}", f"- {v['differences']}", ""]
    return "\n".join(L).strip()


def composer_lot(lot: dict, chemin: Path, champs: dict, type_: str, decl: dict) -> dict:
    """Le lot de couche 2 de la fiche, ses nœuds de forme remplacés par ceux des réponses gardées. Le nœud de la fiche
    entre une fois quand une arête part d'elle."""
    from newskit import procedes_passes as pp  # noqa: PLC0415

    cle = pp.cle_de(champs[decl["contenu"]["champ"]])
    propre = lambda x: str(x.get("contributor") or "").startswith("procédé ")  # noqa: E731
    lot = {
        **lot,
        "nodes": [n for n in lot["nodes"] if not propre(n)],
        "edges": [e for e in lot["edges"] if not propre(e)],
    }
    presents = {n["id"] for n in lot["nodes"]}
    for passe in decl["passes"]:
        f = pp.sortie(type_, passe["nom"], cle, ".json")
        if not passe["forme"] or not f.exists():
            continue
        c = contexte(chemin, champs, type_, passe["nom"], cle)
        ns, ars = noeuds(passe["forme"], json.loads(f.read_text(encoding="utf-8")), c)
        lot["nodes"] += [n for n in ns if n["id"] not in presents]
        presents |= {n["id"] for n in ns}
        lot["edges"] += ars
        if ars and c["fid"] not in presents:
            lot["nodes"].insert(0, {**c["gabarit"], "id": c["fid"], "label": str(champs.get("title") or chemin.stem),
                                    "file_type": "document", "contributor": c["contributeur"]})  # fmt: skip
            presents.add(c["fid"])
    return lot

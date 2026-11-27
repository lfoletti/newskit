# Adapté de graphify 0.9.20 (graphify/build.py, graphify/dedup.py, graphify/_minhash.py, graphify/validate.py), sous
# licence MIT :
#
# Copyright (c) 2026 Safi Shamsi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""Le contrôle du schéma d'une extraction (repris de `graphify.validate.validate_extraction`, version 0.9.20) : la
couche 1 et la couche 2 le passent avant d'écrire un lot. La composition qui le portait est retirée (K3, étape 3,
27.11.2026) ; le contrôle reste."""

from __future__ import annotations

TYPES_VALIDES = {"code", "document", "paper", "image", "rationale", "concept"}
CONFIANCES_VALIDES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
CHAMPS_DE_NOEUD = {"id", "label", "file_type", "source_file"}
CHAMPS_D_ARETE = {"source", "target", "relation", "confidence", "source_file"}


def valider_extraction(data: dict) -> list[str]:
    """Les erreurs de schéma d'une extraction (liste vide : valide), dans les termes de graphify."""
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]
    erreurs: list[str] = []
    ids: set = set()
    if "nodes" not in data:
        erreurs.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        erreurs.append("'nodes' must be a list")
    else:
        for i, n in enumerate(data["nodes"]):
            if not isinstance(n, dict):
                erreurs.append(f"Node {i} must be an object")
                continue
            for champ in CHAMPS_DE_NOEUD:
                if champ not in n:
                    erreurs.append(f"Node {i} (id={n.get('id', '?')!r}) missing required field '{champ}'")
            if "id" in n:
                try:
                    hash(n["id"])
                except TypeError:
                    erreurs.append(f"Node {i} has non-hashable id {n['id']!r} - id must be a string")
                else:
                    ids.add(n["id"])
            if "file_type" in n and n["file_type"] not in TYPES_VALIDES:
                erreurs.append(
                    f"Node {i} (id={n.get('id', '?')!r}) has invalid file_type "
                    f"'{n['file_type']}' - must be one of {sorted(TYPES_VALIDES)}"
                )
    aretes = data.get("edges") if "edges" in data else data.get("links")
    if aretes is None:
        erreurs.append("Missing required key 'edges'")
    elif not isinstance(aretes, list):
        erreurs.append("'edges' must be a list")
    else:
        for i, e in enumerate(aretes):
            if not isinstance(e, dict):
                erreurs.append(f"Edge {i} must be an object")
                continue
            for champ in CHAMPS_D_ARETE:
                if champ not in e:
                    erreurs.append(f"Edge {i} missing required field '{champ}'")
            if "confidence" in e and e["confidence"] not in CONFIANCES_VALIDES:
                erreurs.append(
                    f"Edge {i} has invalid confidence '{e['confidence']}' - must be one of {sorted(CONFIANCES_VALIDES)}"
                )
            for bout in ("source", "target"):
                if bout not in e:
                    continue
                val = e[bout]
                try:
                    orphelin = bool(ids) and val not in ids
                except TypeError:
                    erreurs.append(f"Edge {i} {bout} {val!r} is non-hashable - must be a string")
                    continue
                if orphelin:
                    erreurs.append(f"Edge {i} {bout} '{val}' does not match any node id")
    return erreurs


# --- Déduplication ---------------------------------------------------------------------------------------------------

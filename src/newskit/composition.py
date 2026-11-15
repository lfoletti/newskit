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
"""La composition des extractions en un graphe, et le contrôle de leur schéma.

Jusqu'à la version 0.1.49, `fusionner_graphe` composait par `graphify.build.build` et validait par
`graphify.validate.validate_extraction`. Ce module en reprend les chemins que les graphes de newskit empruntent,
à l'identique (mêmes nœuds, mêmes arêtes, mêmes attributs, même ordre) :

1. déduplication (`dedupliquer`) : un nœud par identifiant (celui dont le fichier définit l'identifiant gagne), puis
   fusion des nœuds d'étiquette identique dans un même fichier, puis des étiquettes proches (MinHash, puis
   Jaro-Winkler, ou Jaro seul entre fichiers), avec les refus de graphify (variantes, nombres, préfixes, documents) ;
2. construction (`construire`) : schéma hérité normalisé, identifiants d'avant le chemin complet réécrits, nœuds
   fantômes fondus dans leur nœud AST, extrémités d'arêtes retrouvées par normalisation ou par l'ancien préfixe,
   arêtes pendantes écartées, poids assainis, direction gardée (`_src`, `_tgt`), hyperarêtes vérifiées.

Ce qui n'est pas repris ne sert pas à newskit : l'arbitrage par un modèle des paires ambiguës (jamais demandé), le
graphe orienté, les multigraphes, l'ancienne clé `links`, la garde contre plusieurs dépôts.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import unicodedata
from collections import defaultdict
from pathlib import Path

import networkx as nx
import numpy as np
from rapidfuzz.distance import DamerauLevenshtein, Jaro, JaroWinkler

from newskit.commun import normaliser_identifiant
from newskit.structure_titres import make_id, tige

# --- Schéma ----------------------------------------------------------------------------------------------------------

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

SEUIL_ENTROPIE = 2.5
SEUIL_FUSION = 92.0  # similarité rapidfuzz × 100
PERMUTATIONS = 128
# Les bandes et les rangées que graphify calcule pour un seuil LSH de 0,7 et 128 permutations (erreur pondérée
# minimale des faux positifs et des faux négatifs).
BANDES, RANGEES = 14, 9
RE_SUFFIXE_DE_LOT = re.compile(r"_c\d+$")
RE_VARIANTE = re.compile(r"^(.*[a-z])([0-9]+[a-z]*|[a-z]{2,})$")
RE_CHIFFRES = re.compile(r"\d+")
RE_SEGMENT = re.compile(r"[^a-z0-9]+")
RE_EXTENSION = re.compile(r"\.[^./]+$")
ANCRES_DE_FICHIER = frozenset({"rationale", "document"})
_PREMIER = np.uint64((1 << 61) - 1)
_MASQUE = np.uint64(0xFFFF_FFFF)
_rng = np.random.RandomState(1)
_A = _rng.randint(1, int(_PREMIER), PERMUTATIONS, dtype=np.uint64)
_B = _rng.randint(0, int(_PREMIER), PERMUTATIONS, dtype=np.uint64)


def _norm(label) -> str:
    if not isinstance(label, str):
        label = "" if label is None else str(label)
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", label).casefold(), flags=re.UNICODE).strip()


def _entropie(label: str) -> float:
    s = _norm(label)
    if not s:
        return 0.0
    freq: dict[str, int] = defaultdict(int)
    for c in s:
        freq[c] += 1
    return -sum((k / len(s)) * math.log2(k / len(s)) for k in freq.values())


def _minhash(texte: str) -> np.ndarray:
    """Empreinte MinHash des trigrammes du texte sans espaces (permutations de Mersenne, graine 1)."""
    t = texte.replace(" ", "")
    valeurs = np.full(PERMUTATIONS, int(_MASQUE), dtype=np.uint64)
    for trigramme in {t[i : i + 3] for i in range(len(t) - 2)}:
        hv = np.uint64(struct.unpack("<I", hashlib.sha1(trigramme.encode("utf-8")).digest()[:4])[0])
        valeurs = np.minimum(valeurs, np.bitwise_and((_A * hv + _B) % _PREMIER, _MASQUE))
    return valeurs


def _variantes(a: str, b: str) -> bool:
    """Deux variantes courtes d'un même modèle (même tige, suffixe différent)."""
    if max(len(a), len(b)) >= 12:
        return False
    ma, mb = RE_VARIANTE.match(a), RE_VARIANTE.match(b)
    if not (ma and mb):
        return False
    return ma.group(1) == mb.group(1) and ma.group(2) != mb.group(2)


def _courtes_refusees(a: str, b: str, score: float) -> bool:
    """Deux étiquettes courtes ne fusionnent que par une seule substitution, à longueur égale."""
    if max(len(a), len(b)) >= 12:
        return False
    if score >= 97.0 and len(a) == len(b) and DamerauLevenshtein.distance(a, b) <= 1:
        return False
    return True


def _nombres_differents(a: str, b: str) -> bool:
    return sorted(t.lstrip("0") or "0" for t in RE_CHIFFRES.findall(a)) != sorted(
        t.lstrip("0") or "0" for t in RE_CHIFFRES.findall(b)
    )


def _ancres_de_fichiers_differents(n: dict, m: dict) -> bool:
    if n.get("file_type") not in ANCRES_DE_FICHIER and m.get("file_type") not in ANCRES_DE_FICHIER:
        return False
    return (n.get("source_file") or "") != (m.get("source_file") or "")


def _prefixes_d_identifiant(source: str) -> set[str]:
    tige_ = RE_EXTENSION.sub("", source.replace("\\", "/"))
    segments = [s for s in (RE_SEGMENT.sub("_", p.casefold()).strip("_") for p in tige_.split("/")) if s]
    return {"_".join(segments[i:]) for i in range(len(segments))}


def _definit(n: dict) -> bool:
    """Vrai quand le fichier du nœud est celui dont son identifiant est tiré."""
    nid, source = n.get("id") or "", n.get("source_file") or ""
    if not nid or not source:
        return False
    return any(nid == p or nid.startswith(f"{p}_") for p in _prefixes_d_identifiant(source))


def _rang(n: dict) -> tuple:
    label = n.get("label") or ""
    return (not _definit(n), len(label), label, n.get("source_file") or "")


def _gagnant(noeuds: list[dict]) -> dict:
    """Le survivant d'un groupe : sans suffixe de lot, puis l'identifiant le plus court, puis le premier."""
    return min(noeuds, key=lambda n: (1 if RE_SUFFIXE_DE_LOT.search(n["id"]) else 0, len(n["id"])))


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def racine(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def unir(self, x: str, y: str) -> None:
        rx, ry = self.racine(x), self.racine(y)
        if rx != ry:
            self.parent[ry] = rx


def dedupliquer(noeuds: list[dict], aretes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Les nœuds sans doublon et les arêtes réécrites vers les survivants (voir le docstring du module)."""
    par_id: dict[str, dict] = {}
    for n in noeuds:
        nid = n.get("id", "")
        if not nid:
            continue
        tenant = par_id.get(nid)
        if tenant is None or _rang(n) < _rang(tenant):
            par_id[nid] = n
    uniques = list(par_id.values())

    uf = _Union()
    exactes = 0
    par_cle: dict[str, list[dict]] = defaultdict(list)
    for n in uniques:
        if n.get("file_type") == "code":
            continue
        cle = _norm(n.get("label", n.get("id", "")))
        if cle:
            par_cle[cle].append(n)
    for groupe in par_cle.values():
        par_fichier: dict[str, list[dict]] = defaultdict(list)
        for n in groupe:
            par_fichier[n.get("source_file") or ""].append(n)
        for source, membres in par_fichier.items():
            if source and len(membres) > 1:
                g = _gagnant(membres)
                for n in membres:
                    uf.unir(g["id"], n["id"])
                exactes += len(membres) - 1

    candidats: list[dict] = []
    vues: set[str] = set()
    for n in uniques:
        if n.get("file_type") == "code":
            continue
        cle = _norm(n.get("label", n.get("id", "")))
        if cle and cle not in vues:
            vues.add(cle)
            if _entropie(n.get("label", "")) >= SEUIL_ENTROPIE:
                candidats.append(n)
    proches = 0
    tables: list[dict[bytes, list[str]]] = [{} for _ in range(BANDES)]
    empreintes: dict[str, np.ndarray] = {}
    normes: dict[str, str] = {}
    par_id_candidat: dict[str, dict] = {}
    for n in candidats:
        normes[n["id"]] = _norm(n.get("label", n.get("id", "")))
        empreintes[n["id"]] = _minhash(normes[n["id"]])
        par_id_candidat[n["id"]] = n
        for i, table in enumerate(tables):
            table.setdefault(empreintes[n["id"]][i * RANGEES : (i + 1) * RANGEES].tobytes(), []).append(n["id"])
    for n in candidats:
        nid, a = n["id"], normes[n["id"]]
        voisins: set[str] = set()
        for i, table in enumerate(tables):
            voisins.update(table.get(empreintes[nid][i * RANGEES : (i + 1) * RANGEES].tobytes(), []))
        for vid in list(voisins):
            if vid == nid or uf.racine(nid) == uf.racine(vid):
                continue
            v, b = par_id_candidat[vid], normes[vid]
            # Entre fichiers, une étiquette longue se juge sur Jaro seul : le bonus de préfixe de Jaro-Winkler
            # fabriquait des fusions entre fichiers (graphify #1243).
            if (n.get("source_file") or "") != (v.get("source_file") or "") and max(len(a), len(b)) >= 12:
                score = Jaro.normalized_similarity(a, b) * 100
            else:
                score = JaroWinkler.normalized_similarity(a, b) * 100
            if _variantes(a, b) or _courtes_refusees(a, b, score):
                continue
            court, long_ = sorted((a, b), key=len)
            if long_.startswith(court):
                continue
            if _nombres_differents(a, b) or _ancres_de_fichiers_differents(n, v):
                continue
            if score >= SEUIL_FUSION:
                g = _gagnant([n, v])
                uf.unir(g["id"], nid)
                uf.unir(g["id"], vid)
                proches += 1

    groupes: dict[str, list[str]] = defaultdict(list)
    for x in uf.parent:
        groupes[uf.racine(x)].append(x)
    rang = {n["id"]: i for i, n in enumerate(uniques)}
    renvoi: dict[str, str] = {}
    for membres in groupes.values():
        if len(membres) == 1:
            continue
        g = _gagnant([uniques[i] for i in sorted(rang[m] for m in membres)])["id"]
        for m in membres:
            if m != g:
                renvoi[m] = g
    if not renvoi:
        return uniques, aretes
    print(f"    {len(renvoi)} nœud(s) fondus à la composition ({exactes} exact(s), {proches} proche(s))")
    sorties = []
    for e in aretes:
        e = dict(e)
        src = e["source"] if "source" in e else e.get("from")
        tgt = e["target"] if "target" in e else e.get("to")
        if src is None or tgt is None:
            continue
        e["source"], e["target"] = renvoi.get(src, src), renvoi.get(tgt, tgt)
        e.pop("from", None)
        e.pop("to", None)
        if e["source"] != e["target"]:
            sorties.append(e)
    return [n for n in uniques if n["id"] not in renvoi], sorties


# --- Construction ----------------------------------------------------------------------------------------------------

FAMILLES = {
    ".py": "py", ".pyi": "py",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
    ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".go": "go", ".rs": "rs",
    ".java": "jvm", ".kt": "jvm", ".scala": "jvm", ".groovy": "jvm",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c",
    ".cxx": "c", ".hh": "c", ".hxx": "c",
    ".cu": "c", ".cuh": "c", ".metal": "c", ".m": "c", ".mm": "c",
    ".rb": "rb", ".rake": "rb", ".php": "php", ".cs": "cs", ".swift": "swift", ".lua": "lua",
}  # fmt: skip
SYNONYMES_DE_TYPE = {
    "markdown": "document",
    "text": "document",
    "tool": "code",
    "library": "code",
    "pattern": "concept",
    "principle": "concept",
    "constraint": "concept",
    "tech": "concept",
    "technology": "concept",
    "data-source": "concept",
    "data_source": "concept",
    "gotcha": "concept",
    "framework": "concept",
}


def source_relative(p: str | None, racine: str | None) -> str | None:
    """Le chemin d'une source en barres obliques, relatif à la racine quand il est absolu et dessous."""
    if not p:
        return p
    p = p.replace("\\", "/")
    if racine and os.path.isabs(p):
        try:
            p = Path(p).relative_to(racine).as_posix()
        except ValueError:
            try:
                p = Path(p).resolve().relative_to(Path(racine).resolve()).as_posix()
            except (ValueError, OSError):
                pass
    return p


def _anciennes_tiges(rel: Path) -> list[str]:
    formes = []
    if rel.parent.name:
        formes.append(make_id(f"{rel.parent.name}.{rel.stem}"))
    formes.append(make_id(rel.stem))
    vues: set[str] = set()
    return [f for f in formes if f and not (f in vues or vues.add(f))]


def _renvoi_semantique(noeuds: list[dict], racine: str | None) -> dict[str, str]:
    """Les identifiants d'un nœud non AST tirés d'un ancien préfixe (dossier parent seul, ou nom seul) prennent le
    chemin complet de leur fichier (graphify #1504)."""
    renvoi: dict[str, str] = {}
    for n in noeuds:
        if n.get("_origin") == "ast":
            continue
        nid, sf = n.get("id"), n.get("source_file")
        if not nid or not isinstance(nid, str) or not sf:
            continue
        rel = Path(source_relative(str(sf), racine) or str(sf))
        if rel.is_absolute() or not rel.name:
            continue
        neuve = make_id(tige(rel))
        norm = normaliser_identifiant(nid)
        if not neuve or norm == neuve or norm.startswith(neuve + "_"):
            continue
        nouvel = None
        for ancienne in _anciennes_tiges(rel):
            if ancienne == neuve:
                continue
            if norm == ancienne:
                nouvel = neuve
                break
            if norm.startswith(ancienne + "_"):
                nouvel = make_id(neuve, norm[len(ancienne) + 1 :])
                break
        if nouvel and nouvel != nid:
            renvoi[nid] = nouvel
    return renvoi


def _renvoi_des_jumeaux(noeuds: list[dict]) -> dict[str, str]:
    """Le nœud `<x>` d'un document se fond dans son jumeau `<x>_doc` du même fichier (graphify #1799)."""
    par_id = {str(n["id"]): n for n in noeuds if n.get("id")}
    renvoi: dict[str, str] = {}
    for nid, n in par_id.items():
        if not nid.endswith("_doc"):
            continue
        nu = par_id.get(nid[:-4])
        if nu is None or not n.get("source_file") or nu.get("source_file") != n.get("source_file"):
            continue
        if n.get("file_type") == "document" and nu.get("file_type") == "document":
            renvoi[nid[:-4]] = nid
    return renvoi


def _membres(he: dict) -> None:
    """Les membres d'une hyperarête sous la clé `nodes`, quel que soit le nom donné (`members`, `node_ids`)."""
    if not isinstance(he.get("nodes"), list):
        for alias in ("members", "node_ids"):
            val = he.get(alias)
            if isinstance(val, list):
                vus: set = set()
                gardes: list = []
                for x in val:
                    try:
                        if x in vus:
                            continue
                        vus.add(x)
                    except TypeError:
                        pass  # un membre non hachable reste ; la vérification des membres l'écartera
                    gardes.append(x)
                he["nodes"] = gardes
                break
    for alias in ("members", "node_ids"):
        he.pop(alias, None)


def _renvoyer(extraction: dict, renvoi: dict[str, str], boucles: bool) -> None:
    """Les arêtes et les hyperarêtes suivent les identifiants renvoyés ; avec `boucles`, une arête que le renvoi
    referme sur elle-même est écartée."""
    if not renvoi:
        return
    gardees = []
    for e in extraction["edges"]:
        s0, t0 = e.get("source"), e.get("target")
        if s0 in renvoi:
            e["source"] = renvoi[s0]
        if t0 in renvoi:
            e["target"] = renvoi[t0]
        if boucles and e.get("source") == e.get("target") and (s0 in renvoi or t0 in renvoi):
            continue
        gardees.append(e)
    extraction["edges"] = gardees
    for he in extraction["hyperedges"]:
        if isinstance(he, dict) and isinstance(he.get("nodes"), list):
            he["nodes"] = [renvoi.get(x, x) for x in he["nodes"]]


def _hachable(x) -> bool:
    try:
        hash(x)
    except TypeError:
        return False
    return True


def construire(extraction: dict, racine: str | None) -> nx.Graph:
    """Le graphe non orienté d'une extraction déjà dédupliquée (voir le docstring du module)."""
    racine = str(Path(racine).resolve()) if racine else None
    for n in extraction["nodes"]:
        if "source" in n and "source_file" not in n:
            n["source_file"] = n.pop("source")
        if n.get("file_type") in (None, ""):
            n["file_type"] = "concept"
        if n["file_type"] not in TYPES_VALIDES:
            n["file_type"] = SYNONYMES_DE_TYPE.get(n["file_type"], "concept")
    for he in extraction["hyperedges"]:
        if isinstance(he, dict):
            _membres(he)

    renvoi = _renvoi_semantique(extraction["nodes"], racine)
    for n in extraction["nodes"]:
        if n.get("id") in renvoi:
            n["id"] = renvoi[n["id"]]
    _renvoyer(extraction, renvoi, boucles=False)
    renvoi = _renvoi_des_jumeaux(extraction["nodes"])
    extraction["nodes"] = [n for n in extraction["nodes"] if n.get("id") not in renvoi]
    _renvoyer(extraction, renvoi, boucles=True)

    G = nx.Graph()
    for n in extraction["nodes"]:
        if "source_file" in n:
            n["source_file"] = source_relative(n["source_file"], racine)
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    ensemble = set(G.nodes())

    # Fantômes : un nœud non AST de même (nom de fichier, étiquette) qu'un nœud AST est le même nœud (graphify
    # #1145) ; deux nœuds AST, ou deux nœuds situés de fichiers différents, sur la même clé la rendent ambiguë.
    situes: dict[tuple[str, str], str] = {}
    ambigues: set[tuple[str, str]] = set()
    for nid in sorted(ensemble):
        d = G.nodes[nid]
        label, sf = str(d.get("label", "")).strip(), str(d.get("source_file", ""))
        base = Path(sf).name if sf else ""
        if not label or not base:
            continue
        ast_ = d.get("_origin") == "ast"
        if d.get("source_location") or ast_:
            cle = (base, label)
            if ast_:
                if cle in situes and G.nodes[situes[cle]].get("_origin") == "ast":
                    ambigues.add(cle)
                situes[cle] = nid
            else:
                tenant = situes.get(cle)
                if tenant is None:
                    situes[cle] = nid
                elif G.nodes[tenant].get("_origin") != "ast" and str(G.nodes[tenant].get("source_file", "")) != sf:
                    ambigues.add(cle)
    fantomes_par_cle: dict[tuple[str, str], str] = {}
    for nid in sorted(ensemble):
        d = G.nodes[nid]
        if d.get("_origin") == "ast":
            continue
        label, sf = str(d.get("label", "")).strip(), str(d.get("source_file", ""))
        base = Path(sf).name if sf else ""
        if not label or not base:
            continue
        cle = (base, label)
        if cle not in ambigues and cle in situes and situes[cle] != nid:
            fantomes_par_cle[cle] = nid
    fantomes = {f: situes[cle] for cle, f in fantomes_par_cle.items()}
    for f in fantomes:
        G.remove_node(f)
        ensemble.discard(f)

    retrouve = {normaliser_identifiant(nid): nid for nid in ensemble}
    for f, canon in fantomes.items():
        retrouve[normaliser_identifiant(f)] = canon
        retrouve[f] = canon
    # L'ancien préfixe d'un nœud (dossier parent seul, ou nom seul) le désigne encore, quand un seul nœud le porte.
    candidats: dict[str, set[str]] = {}
    for nid in ensemble:
        d = G.nodes[nid]
        sf = d.get("source_file")
        if not sf or Path(str(sf)).is_absolute():
            continue
        rel = Path(str(sf))
        neuve = make_id(tige(rel))
        suffixe = ""
        if str(d.get("label", "")) != rel.name and normaliser_identifiant(nid).startswith(neuve):
            suffixe = normaliser_identifiant(nid)[len(neuve) :]
        for ancienne in _anciennes_tiges(rel):
            if ancienne == neuve:
                continue
            alias = ancienne + suffixe
            candidats.setdefault(normaliser_identifiant(alias), set()).add(nid)
            candidats.setdefault(alias, set()).add(nid)
    for alias, qui in candidats.items():
        if len(qui) == 1:
            retrouve.setdefault(alias, next(iter(qui)))

    # Les arêtes, dans un ordre fixe : sur une même paire, la dernière écrite gagne.
    for e in sorted(
        extraction["edges"],
        key=lambda e: (
            str(e.get("source", e.get("from", ""))),
            str(e.get("target", e.get("to", ""))),
            str(e.get("relation", "")),
        ),
    ):
        if "source" not in e and "from" in e:
            e["source"] = e["from"]
        if "target" not in e and "to" in e:
            e["target"] = e["to"]
        if "source" not in e or "target" not in e:
            continue
        src, tgt = e["source"], e["target"]
        if not (_hachable(src) and _hachable(tgt)):
            continue
        if src not in ensemble:
            src = retrouve.get(normaliser_identifiant(src), src)
        if tgt not in ensemble:
            tgt = retrouve.get(normaliser_identifiant(tgt), tgt)
        if src not in ensemble or tgt not in ensemble:
            continue
        attrs = {k: v for k, v in e.items() if k not in ("source", "target", "target_file")}
        for cle in ("weight", "confidence_score"):
            if cle in attrs:
                try:
                    val = float(attrs[cle])
                except (TypeError, ValueError):
                    val = 1.0
                if not math.isfinite(val) or val < 0:
                    val = 1.0
                attrs[cle] = val
        if not attrs.get("source_file"):
            attrs["source_file"] = G.nodes[src].get("source_file") or G.nodes[tgt].get("source_file") or ""
        attrs["source_file"] = source_relative(attrs["source_file"], racine)
        # Une arête entre deux langages de code de familles différentes est un fantôme (graphify #1547, #1749).
        rel_ = attrs.get("relation")
        if rel_ in ("calls", "imports", "imports_from", "references"):
            xs = Path(G.nodes[src].get("source_file") or "").suffix.lower()
            xt = Path(G.nodes[tgt].get("source_file") or "").suffix.lower()
            fs, ft = FAMILLES.get(xs), FAMILLES.get(xt)
            if rel_ == "calls":
                if attrs.get("confidence") == "INFERRED" and xs and xt and fs != ft:
                    continue
            elif fs is not None and ft is not None and fs != ft:
                continue
        attrs["_src"], attrs["_tgt"] = src, tgt
        if G.has_edge(src, tgt):
            deja = G[src][tgt]
            if deja.get("relation") == attrs.get("relation") and deja.get("_src") == tgt and deja.get("_tgt") == src:
                continue  # même relation en sens inverse : la première direction reste (graphify #1061)
            # D'un producteur à l'autre, la dernière écrite remplace la précédente sans garder ses champs : graphify les
            # fusionnait, et une arête du modèle posée sur une arête de structure gardait `_origin` et passait pour
            # déterministe (15.11.2026). Entre deux arêtes du même producteur, la fusion reste : une copie sans
            # citation garde celle de la première. Vider sur place garde la place de l'arête, donc l'ordre du graphe.
            if deja.get("_origin") != attrs.get("_origin"):
                deja.clear()
        G.add_edge(src, tgt, **attrs)

    gardees = []
    for he in extraction["hyperedges"]:
        if isinstance(he, dict) and he.get("source_file"):
            he["source_file"] = source_relative(he["source_file"], racine)
        if isinstance(he, dict) and isinstance(he.get("nodes"), list):
            valides = []
            for m in he["nodes"]:
                if not _hachable(m):
                    continue
                if m not in ensemble and isinstance(m, str):
                    m = retrouve.get(normaliser_identifiant(m), m)
                if m in ensemble:
                    valides.append(m)
            if not valides:
                continue
            if valides != he["nodes"]:
                he["nodes"] = valides
        gardees.append(he)
    if gardees:
        G.graph["hyperedges"] = gardees
    return G


def composer(extractions: list[dict], racine: str | None) -> nx.Graph:
    """Les extractions, dans leur ordre, en un graphe : dédupliquées puis construites. Sur un même identifiant, le
    nœud qui définit l'identifiant l'emporte ; les chemins absolus de source deviennent relatifs à `racine`."""
    noeuds: list[dict] = []
    aretes: list[dict] = []
    hyper: list = []
    for ext in extractions:
        noeuds += ext.get("nodes", [])
        aretes += ext.get("edges", [])
        hyper += ext.get("hyperedges", [])
    noeuds, aretes = dedupliquer(noeuds, aretes)
    return construire({"nodes": noeuds, "edges": aretes, "hyperedges": hyper}, racine)

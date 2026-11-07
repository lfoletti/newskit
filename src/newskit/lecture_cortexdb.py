"""Les passages d'une réponse lus dans le cerveau CortexDB du graphe (CHANTIER_PILOTE_CORTEXDB.md).

Deux recherches au même budget, fusionnées rang par rang, l'encodeur d'abord : l'outil `search_graphrag_lexical`, dont
les graines sont les mots de la question, et le service `SearchGraphRag`, dont les graines viennent de l'encodeur.
Mesuré le 23.10.2026 (mesure de contexte, sans lecteur) : sur un graphe d'éthique, l'outil seul rendait 61 % de morceaux français
pour des questions françaises et des fiches surtout anglaises (fiche citée 42 %) ; l'encodeur seul perdait sur un graphe audio
(73,3 % contre 81,2) ; la fusion tient les deux (72,7 % et 85,2 %, contre 62,6 et 76,4 pour le contexte du moteur), y compris sur le
cerveau léger (une mention par notion), qui sans elle perdait 3,8 points.

Coupé par défaut : `graphes.<nom>.lecture_cortexdb: true` au registre, et le serveur permanent du graphe
(serveur_cortexdb.py), dont l'adresse et le jeton sont dans `cache/cortexdb/<g>.adresse` ; `KG_CORTEXDB_ADRESSE` et
`KG_CORTEXDB_JETON` l'emportent (essais). Toute panne rend None, et `synthese.passages_pour` retombe sur l'index.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from newskit import interroger_graphe as ig

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
COLLECTION_FICHES = "graphrag_chunks"   # la collection par défaut d'ingest_document
DELAI = 20                              # secondes par recherche : une réponse ne doit pas attendre un serveur muet


def fichier_adresse(graphe: str) -> Path:
    return RACINE / "cache" / "cortexdb" / f"{graphe}.adresse"


def lire_adresse(p: Path) -> tuple[str, str] | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d["adresse"], d["jeton"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


CLIENT_PILOTE = RACINE / "cache" / "pilote_cortexdb" / "venv"   # le client de ce Mac ; le serveur l'a dans son venv


def _client():
    try:
        from cortexdb_client import CortexClient  # noqa: PLC0415
    except ImportError:
        sys.path += [str(p) for p in sorted(CLIENT_PILOTE.glob(f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"))]
        from cortexdb_client import CortexClient  # noqa: PLC0415
    return CortexClient


def _proto():
    from cortexdb_client import proto  # noqa: PLC0415
    return proto


def _paragraphes(fiche: str) -> list[tuple[int, str]]:
    return ig._paragraphes(RACINE / fiche)


_INVERSE: dict[str, str] = {}


def _inverse() -> dict[str, str]:
    """Identifiant court → chemin, pour toutes les fiches sous `sources/` (puits_cortexdb.court) ; calculé une fois."""
    if not _INVERSE:
        from newskit import puits_cortexdb as pc  # noqa: PLC0415
        for f in (RACINE / "sources").rglob("*.md"):
            chemin = f.relative_to(RACINE).as_posix()
            _INVERSE[pc.court(chemin)] = chemin
    return _INVERSE


def fusionner(*listes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rang par rang, dans l'ordre des listes ; un texte déjà pris (à la casse et aux accents près) ne revient pas."""
    out, vus = [], set()
    for k in range(max((len(l) for l in listes), default=0)):
        for l in listes:
            if k < len(l):
                cle = ig._plier(l[k][1])[:160]
                if cle not in vus:
                    vus.add(cle)
                    out.append(l[k])
    return out


def situer(morceau: str, paragraphes: list[tuple[int, str]]) -> int:
    """La ligne du paragraphe où le morceau commence ; sinon celui qui partage le plus de ses mots ; sinon le premier
    (0 sans paragraphe). Un renvoi du moteur nomme une fiche et une ligne."""
    if not paragraphes:
        return 0
    debut = ig._plier(morceau)[:60]
    for ligne, texte in paragraphes:
        if debut and debut in ig._plier(texte):
            return ligne
    mots = set(ig._plier(morceau).split())
    meilleur = max(paragraphes, key=lambda p: len(mots & set(ig._plier(p[1]).split())))
    return meilleur[0] if mots & set(ig._plier(meilleur[1]).split()) else paragraphes[0][0]


def au_budget(morceaux: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """Les premiers morceaux tant qu'ils tiennent dans `budget` caractères, jamais moins d'un."""
    out, n = [], 0
    for m in morceaux:
        if out and n + len(m[1]) > budget:
            break
        out.append(m)
        n += len(m[1])
    return out


def passages(client, question: str, budget: int) -> list[tuple[str, int, str]] | None:
    """(fiche, ligne, texte) des morceaux fusionnés, au budget ; les synthèses de la pyramide n'en sont pas (le moteur
    les rend déjà au-dessus des passages). None si une recherche échoue."""
    proto = _proto()
    try:
        args = {"query": question, "retrieval_mode": "graph", "max_context_chars": budget, "max_context_chunks": 30,
                "collection": COLLECTION_FICHES}
        r = client.tools.CallTool(proto.CallToolRequest(name="search_graphrag_lexical", args_json=json.dumps(args)), timeout=DELAI)
        mots = [(x.get("DocumentID") or "", x.get("Content") or "") for x in json.loads(r.result_json or "{}").get("Chunks") or []]
        s = client.graphrag.SearchGraphRag(proto.SearchGraphRagRequest(query=question, collection=COLLECTION_FICHES,
                                                                      retrieval_mode="graph", max_context_chars=budget,
                                                                      max_context_chunks=30), timeout=DELAI)
        sens = [(x.document_id, x.content) for x in s.chunks]
    except Exception as e:  # noqa: BLE001 — un serveur absent ou lent ne doit pas empêcher la réponse
        print(f"  (CortexDB : {type(e).__name__} {str(e)[:120]} ; passages de l'index)", file=sys.stderr)
        return None
    inverse = _inverse()
    # Un cerveau d'avant le 23.10.2026 porte le chemin lui-même ; un identifiant inconnu (une synthèse de la pyramide)
    # n'est pas un passage.
    garder = lambda l: [(d if d.startswith("sources/") else inverse[d], t) for d, t in l  # noqa: E731
                        if t and (d.startswith("sources/") or d in inverse)]
    return [(d, situer(t, _paragraphes(d)), t) for d, t in au_budget(fusionner(garder(sens), garder(mots)), budget)]


def passages_du_graphe(graphe: str, question: str, budget: int) -> list[tuple[str, int, str]] | None:
    """`passages` sur le serveur du graphe ; None sans client, sans adresse ou en panne."""
    if os.environ.get("KG_CORTEXDB_ADRESSE"):
        lu = os.environ["KG_CORTEXDB_ADRESSE"], os.environ.get("KG_CORTEXDB_JETON", "")
    else:
        lu = lire_adresse(fichier_adresse(graphe))
    if not lu:
        print(f"  (CortexDB : aucun serveur pour {graphe} ; passages de l'index)", file=sys.stderr)
        return None
    try:
        with _client().connect(lu[0], token=lu[1]) as c:
            return passages(c, question, budget)
    except Exception as e:  # noqa: BLE001
        print(f"  (CortexDB : {type(e).__name__} {str(e)[:120]} ; passages de l'index)", file=sys.stderr)
        return None

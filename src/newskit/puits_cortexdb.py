#!/usr/bin/env python3
"""Le déversoir vers CortexDB (pilote, CHANTIER_PILOTE_CORTEXDB.md, 22.10.2026) : le moteur garde la veille et
l'extraction, CortexDB stocke et sert. Un lot de la couche 2 et sa fiche deviennent un document (découpé en morceaux par
CortexDB, sans extracteur), des entités rattachées à ces morceaux et des relations qui portent leur provenance. Aucun
appel au modèle : ce qui a été extrait et payé se verse tel quel.

La couche 1 (squelette : auteurs, publications, papiers, dépôts, citations) et la structure des titres (AST, `contains`)
se versent avec la fiche qui les porte (K0 de la dilution de kg-mcp, 27.10.2026). Elles se calculent ici (`CoucheUn`),
sans graphe composé depuis K3 : sur une fiche, le squelette fait foi, et le lot la nomme comme lui.

Forme : `lot_vers_cortexdb` est un calcul pur (testé sans serveur) ; `verser` est la seule fonction qui parle au
serveur ; le client gRPC ne s'importe qu'en elle (venv du pilote : cache/pilote_cortexdb/venv).

    cache/pilote_cortexdb/venv/bin/python newskit.puits_cortexdb --graphe <graphe> --adresse 127.0.0.1:47899 --jeton …
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit.extrait import atteste  # noqa: E402 — la vérification d'extrait de la couche 2
from newskit import fiche  # noqa: E402
from newskit.commun import normaliser_etiquette  # noqa: E402

from newskit import config_sources as _cfg  # noqa: E402

# Le producteur des métadonnées versées porte le nom de l'édition (bloc `edition:` du registre, N2).
_EDITION = _cfg.edition()["nom"]
PRODUCTEUR = f"{_EDITION} couche 2 (claude -p)"
PRODUCTEUR_PYRAMIDE = f"{_EDITION} pyramide (claude -p, contrôle mot à mot)"
PRODUCTEUR_FICHE = f"{_EDITION} fiche sans extraction"
PRODUCTEUR_SQUELETTE = f"{_EDITION} couche 1 (squelette, déterministe)"
PRODUCTEUR_AST = f"{_EDITION} structure des titres (AST, déterministe)"
# `_origin` d'une arête du graphe composé → producteur ; une arête sans `_origin` est de la couche 2, versée par son lot.
PRODUCTEUR_VUES = f"{_EDITION} vues du graphe (carte, sommets, relations ; déterministe)"
EXPLIQUER = 200  # fiches `expliquer` versées, les entités au plus fort degré (mainteneur, 29.10.2026)

PRODUCTEURS_COMPOSES = {"squelette": PRODUCTEUR_SQUELETTE, "ast": PRODUCTEUR_AST}
# Un titre de section a un identifiant propre à sa fiche : par son nom, « Introduction » de mille fiches serait une
# seule entité. Les auteurs, publications et papiers, eux, se fondent par leur nom, d'une fiche à l'autre.
PREFIXE_TITRE = "entity:titre_"
FICHES_SQUELETTE = ("article", "papier")  # les nœuds du squelette qui sont des fiches
RE_PARENTHESE_FINALE = re.compile(r"\s*\([^()]*\)\s*$")  # fusionner_graphe : « … (Ahead of AI, 2024-09-21) »
# Morceaux relus par appel de get_chunks : le client gRPC n'admet que 4 Mo par réponse ; un livre de 1,6 Mo
# (une bibliothèque de PDF, 24.10.2026) rendait 4,4 Mo d'un seul appel.
MORCEAUX_PAR_LECTURE = 200
NOEUDS_PAR_LECTURE = 200  # entités relues par appel de get_nodes, sous la même limite
PREFIXE_ENTITE = "entity:"
COLLECTION_PYRAMIDE = "pyramide"
# CortexDB fait attendre chaque écrivain son tour au plus 30 s, et son point de contrôle du WAL prend ce tour. Au VPS
# (3,4 Mo/s en écritures de 4 Ko), un point de contrôle a dépassé 30 s : la reconstruction complète s'arrêtait après une
# heure (16 et 17.11.2026). L'erreur tombe avant toute écriture : l'appel se renvoie après ces attentes (secondes).
ATTENTES_TOUR = (10, 30, 60)
TOUR_PERDU = "no turn to write"


def appeler_outil(client, nom: str, args: dict) -> dict:
    """Un appel d'outil de CortexDB, renvoyé après une attente quand l'écrivain n'a pas eu son tour."""
    from cortexdb_client import proto  # noqa: PLC0415

    requete = proto.CallToolRequest(name=nom, args_json=json.dumps(args, ensure_ascii=False))
    for attente in ATTENTES_TOUR:
        try:
            return json.loads(client.tools.CallTool(requete).result_json or "{}")
        except Exception as e:  # noqa: BLE001 — grpc.RpcError, sans importer grpc ici
            if TOUR_PERDU not in str(e):  # le texte d'une grpc.RpcError porte ses détails
                raise
            print(f"  {nom} : tour d'écriture non obtenu, nouvel essai dans {attente} s", file=sys.stderr)
            time.sleep(attente)
    return json.loads(client.tools.CallTool(requete).result_json or "{}")


# Inventaire de classe (tests/test_puits_cortexdb.py) : chaque champ d'un lot est versé, ou dit pourquoi il ne l'est pas.
CHAMPS_LOT = {
    "nodes": "entités (et le nœud de la fiche, typé document)",
    "edges": "relations",
    "hyperedges": "hors classe : toujours vides (0 sur 626 lots mesurés, 22.10.2026)",
    "input_tokens": "hors classe : coût de l'extraction, tenu par le registre des coûts",
    "output_tokens": "hors classe : coût de l'extraction, tenu par le registre des coûts",
}
CHAMPS_NOEUD = {
    "id": "métadonnée newskit_id (CortexDB dérive l'identifiant du nom)",
    "label": "nom de l'entité ; la table d'alias s'applique après, par fusion (`fondre_alias`)",
    "file_type": "type de l'entité",
    "source_file": "_source relatif ; document du versement",
    "source_location": "métadonnée",
    "source_url": "métadonnée",
    "captured_at": "métadonnée",
    "author": "métadonnée",
    "contributor": "métadonnée",
    "rationale": "description de l'entité",
    "evidence": "hors classe : citation de la fiche qui fonde le rationale, vérifiée à l'extraction ; jamais versée "
    "(droits : notice d'une fiche réservée, extrait servi de 120 mots au plus)",
}
CHAMPS_ARETE = {
    "source": "from (nom de l'entité source)",
    "target": "to (nom de l'entité cible)",
    "relation": "type de la relation",
    "confidence": "inferred (EXTRACTED : non ; INFERRED, AMBIGUOUS : oui) et métadonnée",
    "confidence_score": "métadonnée",
    "source_file": "_source relatif",
    "source_location": "métadonnée",
    "weight": "métadonnée",
    "contributor": "métadonnée",
    "evidence": "rattachement : choisit le morceau de la relation (`rattacher`) ; le texte cité n'est jamais versé "
    "(droits : notice d'une fiche réservée, extrait servi de 120 mots au plus)",
}
_EN_TETE = "hors classe : dans l'en-tête du document de la fiche"
_COLLECTE = "hors classe : propre à la collecte"
CHAMPS_NOEUD_SQUELETTE = {
    "id": "métadonnée newskit_id",
    "label": "nom de l'entité",
    "kind": "type de l'entité",
    "file_type": "hors classe : `kind` le précise",
    "source_file": "hors classe : _source est la fiche de l'arête",
    "url": "métadonnée",
    "date": "métadonnée",
    "collected": _EN_TETE,
    "plateforme": _EN_TETE,
    "arxiv_id": _EN_TETE,
    "texte": _EN_TETE,
    "version": _EN_TETE,
    "statut": _EN_TETE,
    "remplacee_par": _EN_TETE,
    "partie": _EN_TETE,
    "complet": _COLLECTE,
    "words": _COLLECTE,
    "categories": _COLLECTE,
    "source_corpus": _COLLECTE,
    "audience": _COLLECTE,
    "edition": _COLLECTE,
    # Le fil (newskit.fil) : titre relu, résumé et complément en français, posés à l'en-tête de la fiche.
    "titre_fr": _EN_TETE,
    "resume_fr": _EN_TETE,
    "complement_fr": _EN_TETE,
    "label_fr": "hors classe : affichage en français, posé à la composition d'après la table du graphe (francais.py)",
}
CHAMPS_NOEUD_AST = {
    "id": "identifiant de l'entité (PREFIXE_TITRE + empreinte) et métadonnée newskit_id",
    "label": "nom de l'entité",
    "file_type": "type de l'entité",
    "source_file": "_source",
    "source_location": "métadonnée",
    "_origin": "_producer",
}
CHAMPS_ARETE_COMPOSEE = {
    "source": "from",
    "target": "to",
    "relation": "type de la relation",
    "confidence": "inferred et métadonnée",
    "confidence_score": "métadonnée",
    "contributor": "métadonnée",
    "source_file": "_source ; document du versement",
    "source_location": "métadonnée",
    "weight": "métadonnée",
    "_origin": "_producer",
    "_src": "hors classe : copie de source (networkx)",
    "_tgt": "hors classe : copie de target (networkx)",
}
# Les champs de l'en-tête d'une fiche qui suivent le document (le reste est dans le corps ou propre à la collecte).
ENTETE_VERSEE = (
    "date",
    "url",
    "plateforme",
    "publication",
    "collected",
    "arxiv_id",
    "doi",
    "texte",
    "version",
    "statut",
    "remplacee_par",
    "partie",
    # Le fil (05.11.2026, mainteneur : « verse les titres au cerveau thalamus ») : ils s'arrêtaient au graphe local.
    "titre_fr",
    "resume_fr",
    "complement_fr",
)


def court(chemin: str) -> str:
    """L'identifiant d'un document dans le cerveau : l'empreinte SHA-256 de son chemin relatif, tronquée à 12 caractères
    (48 bits). CortexDB en tire ceux des morceaux (`chunk:<id>:<n>`) et les répète partout : comme voisins dans
    l'instantané HNSW, aux deux bouts de chaque arête et dans leurs cinq index. Le chemin de la fiche faisait 98
    caractères par morceau (un graphe d'éthique, 23.10.2026) ; il reste lisible en métadonnée (`_source`). Une empreinte plutôt
    qu'une table : rien à garder, le passage inverse se recalcule en hachant les fiches (`lecture_cortexdb._inverse`)."""
    return hashlib.sha256(chemin.encode("utf-8")).hexdigest()[:12]


class Collision(Exception):
    """Deux chemins de même empreinte : le versement s'arrête plutôt que de mêler deux fiches."""


def inscrire(vus: dict[str, str], identifiant: str, origine: str) -> None:
    if vus.setdefault(identifiant, origine) != origine:
        raise Collision(f"{identifiant} : {vus[identifiant]} et {origine}")


class LotInvalide(ValueError):
    """Un lot qu'on ne peut pas verser sans mentir sur sa provenance."""


def relatif(chemin: str, racine: Path) -> str:
    p = Path(chemin)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.relative_to(racine).as_posix()
    except ValueError:
        raise LotInvalide(f"fiche hors du dépôt : {chemin}") from None


def de_balisage(libelle: str) -> bool:
    return not re.search(r"\w", libelle or "")


def identifiant_d_entite(nom: str) -> str:
    """L'identifiant que CortexDB tire du nom d'une entité (`graphEntityNodeID`) : minuscules, lettres et chiffres
    gardés, espace, tiret et souligné rendus par un souligné, le reste retiré. Deux noms de même identifiant sont une
    seule entité ; un identifiant déjà formé passe tel quel."""
    if nom.startswith(PREFIXE_ENTITE):
        return nom
    plie = "".join(
        c if c.isalpha() or c.isdecimal() else "_" if c in " -_" else ""
        for c in (x.lower() for x in nom)  # lettre par lettre : pas de sigma final
    )
    return PREFIXE_ENTITE + (plie.strip("_") or "entity")


def groupes_d_alias(table: dict, noms: dict[str, set[str]]) -> dict[str, list[str]]:
    """Pur. alias.json (fondre_doublons.py) va d'identifiant de nœud à identifiant de nœud ; le cerveau nomme ses
    entités. Par libellé de canonique, les noms sous lesquels ses alias ont été versés (`noms` : identifiant de nœud →
    noms versés), triés, sans ceux qui sont déjà l'entité du canonique. Deux cas mesurés le 27.11.2026 sur les 23 alias
    restés au cerveau : le canonique lui-même versé sous un autre libellé que celui de la table rejoint son groupe (9
    alias) ; le libellé que la table donne à un alias y entre aussi, car un nom est une seule entité au cerveau, même
    versée par un autre nœud (14 alias). Une chaîne d'alias mène à son dernier canonique ; une boucle, ou un canonique
    sans libellé connu, ne fait pas de groupe (jamais un nom inventé)."""
    alias, libelles = table.get("alias") or {}, table.get("libelles") or {}
    groupes: dict[str, set[str]] = {}
    gardes: dict[str, str] = {}
    for perdu in alias:
        garde, vus = alias[perdu], {perdu}
        while garde in alias and garde not in vus:
            vus.add(garde)
            garde = alias[garde]
        canon = libelles.get(garde)
        if garde in vus or not canon:
            continue
        gardes[garde] = canon
        verses = {*noms.get(perdu, ()), *([libelles[perdu]] if libelles.get(perdu) else [])}
        autres = {n for n in verses if identifiant_d_entite(n) != identifiant_d_entite(canon)}
        if autres:
            groupes.setdefault(canon, set()).update(autres)
    for garde, canon in gardes.items():
        autres = {n for n in noms.get(garde, ()) if identifiant_d_entite(n) != identifiant_d_entite(canon)}
        if autres:
            groupes.setdefault(canon, set()).update(autres)
    return {canon: sorted(autres) for canon, autres in sorted(groupes.items())}


def fondre_alias(appel, groupes: dict[str, list[str]]) -> dict:
    """Applique la table d'alias au cerveau, après les versements : chaque alias qui a encore un nœud à lui se fond
    dans son canonique (`merge_entities`, CortexDB v2.157.0 : arêtes redirigées, nom noté sur l'entité gardée, qu'un
    versement ultérieur sous ce nom atteint). Le cerveau fait foi sur ce qui reste à fondre : un alias déjà fondu n'a
    plus de nœud, rien n'est tenu à côté. Canonique sans nœud (la table a inversé une paire déjà fondue, ou son lot n'est
    pas versé) : un alias seul reste tel quel, plusieurs se réunissent sous le premier. Une fusion refusée parce qu'une
    entité a disparu entre la lecture et l'écriture se compte et n'arrête rien ; toute autre erreur arrête le passage
    (un serveur sans l'outil ne publie pas un cerveau aux alias épars). `appel(nom, **args)` : un appel d'outil."""
    bilan = {"alias_groupes": len(groupes), "alias_fondus": 0, "alias_refuses": 0}
    ids = list(dict.fromkeys(identifiant_d_entite(n) for canon, autres in groupes.items() for n in (canon, *autres)))
    presents: set[str] = set()
    for k in range(0, len(ids), NOEUDS_PAR_LECTURE):
        presents.update(
            n["id"] for n in appel("get_nodes", node_ids=ids[k : k + NOEUDS_PAR_LECTURE]).get("nodes") or []
        )
    for canon, autres in groupes.items():
        cible = identifiant_d_entite(canon)
        a_fondre = [i for i in dict.fromkeys(map(identifiant_d_entite, autres)) if i in presents]
        if cible not in presents and a_fondre:
            cible, a_fondre = a_fondre[0], a_fondre[1:]
        if not a_fondre:
            continue
        try:
            appel("merge_entities", canonical=cible, aliases=a_fondre)
        except Exception as e:  # noqa: BLE001 — grpc.RpcError, sans importer grpc ici
            if "NOT_FOUND" not in str(e):
                raise
            bilan["alias_refuses"] += 1
            print(f"  !! alias de {cible} non fondus ({', '.join(a_fondre)}) : {e}", file=sys.stderr)
            continue
        presents.difference_update(a_fondre)
        bilan["alias_fondus"] += len(a_fondre)
    return bilan


def chaines(meta: dict) -> dict[str, str]:
    """Les métadonnées telles que CortexDB les lit : des chaînes (v2.136.0 refuse un nombre dans celles d'une relation),
    une liste encodée en JSON, une valeur vide ou absente omise."""
    out = {}
    for k, v in meta.items():
        if v is None or v == "" or v == []:
            continue
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
    return out


def canon_squelette(graphe: dict) -> dict:
    """Pur : les nœuds du squelette dans le graphe composé (ceux qui portent `kind`) : par identifiant, leur libellé et
    leur type ; par titre normalisé, l'identifiant de chaque fiche (`fondre_doublons_de_fiche`)."""
    ids: dict[str, tuple[str, str]] = {}
    titres: dict[str, str] = {}
    for n in graphe.get("nodes") or []:
        if "kind" in n and n.get("label") and n.get("id"):
            ids[n["id"]] = (n["label"], n["kind"])
            if n["kind"] in FICHES_SQUELETTE:
                titres.setdefault(normaliser_etiquette(n["label"]), n["id"])
    return {"ids": ids, "titres": titres}


def du_squelette(n: dict, canon: dict | None) -> tuple[str, str] | None:
    """Le (libellé, type) du squelette pour un nœud de lot qui redécrit l'un de ses nœuds : même identifiant, ou nœud
    document ou paper au titre d'une fiche, au besoin sans sa parenthèse finale. Sinon rien."""
    if not canon:
        return None
    if n["id"] in canon["ids"]:
        return canon["ids"][n["id"]]
    if n.get("file_type") in ("document", "paper"):
        label = n.get("label") or ""
        cible = canon["titres"].get(normaliser_etiquette(label)) or canon["titres"].get(
            normaliser_etiquette(RE_PARENTHESE_FINALE.sub("", label))
        )
        if cible:
            return canon["ids"][cible]
    return None


def lot_vers_cortexdb(lot: dict, entete: dict, corps: str, racine: Path, canon: dict | None = None) -> dict:
    """{document, entites, relations, ecartes, noms} d'un lot : rien n'est écrit, rien n'est lu. Chaque entité porte le
    nom que le lot lui donne : la table d'alias s'applique au cerveau après les versements (`fondre_alias`), et
    n'entre pas dans l'empreinte de la fiche. `canon` : le squelette (`canon_squelette`), qui fait foi sur une fiche.
    `noms` : identifiant de nœud → nom versé, pour `groupes_d_alias` ; sans les nœuds que le squelette nomme."""
    sources = {n["source_file"] for n in lot["nodes"]}
    if len(sources) != 1:
        raise LotInvalide(f"un lot porte une seule fiche : {len(sources)} trouvées")
    source = relatif(sources.pop(), racine)
    ecartes = {"libelle_de_balisage": 0, "arete_sans_extremite": 0}
    nom_de: dict[str, str] = {}
    noms: dict[str, str] = {}
    entites: dict[str, dict] = {}
    for n in lot["nodes"]:
        if de_balisage(n.get("label", "")):
            ecartes["libelle_de_balisage"] += 1
            continue
        sq = du_squelette(n, canon)
        nom, type_ = sq if sq else (n["label"], n.get("file_type") or "concept")
        nom_de[n["id"]] = nom
        if not sq:  # un nœud que le squelette nomme n'est l'alias de rien
            noms[n["id"]] = nom
        e = entites.setdefault(
            nom,
            {
                "name": nom,
                "type": type_,
                "chunk_ids": [],
                "metadata": {
                    "_source": source,
                    "_producer": PRODUCTEUR,
                    "newskit_id": n["id"],
                    **{k: n.get(k) for k in ("source_url", "captured_at", "author", "contributor", "source_location")},
                },
            },
        )
        if n.get("rationale"):
            e["description"] = n["rationale"]
        if nom != n["label"]:
            aussi = e["metadata"].setdefault("aussi", [])
            if n["label"] not in aussi:
                aussi.append(n["label"])
    relations = []
    for a in lot["edges"]:
        if a["source"] not in nom_de or a["target"] not in nom_de:
            ecartes["arete_sans_extremite"] += 1
            continue
        relations.append(
            {
                "from": nom_de[a["source"]],
                "to": nom_de[a["target"]],
                "type": a["relation"],
                "inferred": a.get("confidence") != "EXTRACTED",
                "chunk_ids": [],
                "metadata": {
                    "_source": source,
                    "_producer": PRODUCTEUR,
                    "confidence": a.get("confidence"),
                    **{k: a.get(k) for k in ("confidence_score", "weight", "contributor", "source_location")},
                },
                **({"extrait": a["evidence"]} if a.get("evidence") else {}),
            }
        )
    document = document_de(source, entete, corps, PRODUCTEUR)
    for x in [*entites.values(), *relations]:
        x["metadata"] = chaines(x["metadata"])
    return {
        "document": document,
        "origine": source,
        "entites": list(entites.values()),
        "relations": relations,
        "ecartes": ecartes,
        "noms": noms,
    }


def document_de(source: str, entete: dict, corps: str, producteur: str) -> dict:
    """Le document d'une fiche, le même qu'elle ait un lot ou non : une fiche extraite plus tard garde son identifiant
    et son texte, donc ses vecteurs repris (cerveau_cortexdb.reprendre_vecteurs)."""
    return {
        "document_id": court(source),
        "title": str(entete.get("title") or Path(source).stem),
        "author": str(entete.get("author") or ""),
        "content": corps,
        "source_url": str(entete.get("url") or ""),
        "metadata": chaines(
            {
                "_source": source,
                "_producer": producteur,
                **{k: str(entete[k]) for k in ENTETE_VERSEE if entete.get(k) not in (None, "")},
            }
        ),
    }


# Pas d'image dans le texte versé (J4, décision du mainteneur du 31.10.2026, docs/demarchage/note_juridique.md) : une
# photographie est protégée même sans caractère individuel (LDA art. 2 al. 3bis). Ce qui n'entre pas au cerveau n'en
# sort pas. Le code reste : un extrait de code borné et attribué est une citation ordinaire (mainteneur, 31.10.2026).
_IMAGE_LIEE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\(([^)]*)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMAGE_HTML = re.compile(r"<img\b[^>]*>", re.I)


def sans_image(corps: str) -> str:
    """Le texte sans ses images : Markdown, HTML, et image cliquable, dont le lien reste."""
    t = _IMAGE_LIEE.sub(lambda m: m.group(1), corps)
    return _IMAGE_HTML.sub("", _IMAGE.sub("", t))


def corps_versable(source_reg, entete: dict, corps: str, droits: dict | None = None, chemin: str = "") -> str:
    """Le texte d'une fiche qui entre au cerveau : le sien, ou une notice si elle n'est pas relayée : payante ou
    interne par défaut, ou désaccord de la source ou d'un auteur (règles du porteur, 25.10.2026). Le document, ses
    entités et ses relations entrent quand même : le graphe ne dépend pas de l'accord, et le lecteur suit l'adresse de
    l'original."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    if cfg.reservee(source_reg, entete):
        pourquoi = (
            "billet d'abonnement, non relayé ici"
            if source_reg.abonnement == "payant"
            else "document privé, non relayé ici"
        )
    elif not cfg.fiche_publiable(source_reg, entete, droits):
        pourquoi = "l'auteur ou la source n'en a pas permis la publication ici"
    else:
        # Un article de loi porte en tête ses désignations : formes usuelles, titre français, termes définis, mots
        # propres (designations.py, mainteneur, 06.11.2026 : « je n'aimerais pas que le site fonctionne mieux que le mcp »).
        from newskit import designations  # noqa: PLC0415

        return designations.ligne(chemin) + sans_image(corps)
    # Un refus : la référence et le résumé publié restent (refus.py, mainteneur, 06.11.2026).
    from newskit import refus  # noqa: PLC0415

    lignes = refus.resume(chemin) if chemin and not cfg.reservee(source_reg, entete) else []
    return (
        f"{entete.get('title') or ''}\n{entete.get('author') or ''}\n\nContenu non publié : {pourquoi}. "
        f"Texte à l'adresse d'origine : {entete.get('url') or 'document local, sans adresse publique'}\n"
        + ("\nRésumé publié :\n" + "\n".join(lignes) + "\n" if lignes else "")
    )


def relayeur(droits: dict):
    """La fonction « le contenu de cette fiche (chemin relatif) est-il relayé ? », qui lit chaque en-tête une fois.
    Une fiche absente ne se juge pas : elle n'entre pas au cerveau."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    vus: dict[str, bool] = {}

    def relayee(rel: str) -> bool:
        if rel not in vus:
            chemin = RACINE / rel
            if not chemin.exists():
                vus[rel] = True
            else:
                entete, _ = fiche.lire_fichier(chemin)
                source = cfg.source_du_chemin(rel)
                vus[rel] = not cfg.reservee(source, entete) and cfg.fiche_publiable(source, entete, droits)
        return vus[rel]

    return relayee


def fiche_vers_cortexdb(source: str, entete: dict, corps: str) -> dict:
    """Une fiche du graphe sans lot de la couche 2 : un document seul, sans entité ni relation (un graphe d'ingénierie,
    24.10.2026 : 4 131 fiches sur 8 074 n'étaient pas dans le cerveau, dont toutes celles des questions perdues contre
    la recherche par les mots). Ses morceaux sont cherchés ; ses entités viendront avec son extraction."""
    return {
        "document": document_de(source, entete, corps, PRODUCTEUR_FICHE),
        "origine": source,
        "entites": [],
        "relations": [],
        "ecartes": {},
    }


def fiches_sans_lot(graphe: dict, avec_lot: set[str], racine: Path) -> list[str]:
    """Les fiches (nœuds article ou papier du graphe composé) qu'aucun lot ne porte, en chemins relatifs, triées."""
    fiches = set()
    for n in graphe.get("nodes") or []:
        if n.get("kind") in ("article", "papier") and n.get("source_file"):
            try:
                fiches.add(relatif(str(n["source_file"]), racine))
            except LotInvalide:
                continue
    return sorted(fiches - avec_lot)


def entite_composee(n: dict, source: str) -> dict:
    """L'entité d'un nœud du graphe composé, extrémité d'une arête du squelette ou de l'AST."""
    if "kind" in n:
        return {
            "name": n["label"],
            "type": n["kind"],
            "chunk_ids": [],
            "metadata": chaines(
                {
                    "_source": source,
                    "_producer": PRODUCTEUR_SQUELETTE,
                    "newskit_id": n["id"],
                    "url": n.get("url"),
                    "date": n.get("date"),
                }
            ),
        }
    if n.get("_origin") == "ast":
        return {
            "id": PREFIXE_TITRE + court(n["id"]),
            "name": n["label"],
            "type": n.get("file_type") or "document",
            "chunk_ids": [],
            "metadata": chaines(
                {
                    "_source": source,
                    "_producer": PRODUCTEUR_AST,
                    "newskit_id": n["id"],
                    "source_location": n.get("source_location"),
                }
            ),
        }
    return {
        "name": n["label"],
        "type": n.get("file_type") or "concept",
        "chunk_ids": [],
        "metadata": chaines({"_source": source, "_producer": PRODUCTEUR, "newskit_id": n["id"]}),
    }


def couche1_par_fiche(graphe: dict, racine: Path) -> tuple[dict[str, dict], dict]:
    """Pur : par fiche (chemin relatif), les entités et relations du squelette et de l'AST du graphe composé, et les
    arêtes écartées, comptées. Une entité est désignée par son identifiant s'il en a un, sinon par son nom."""
    noeuds = {n["id"]: n for n in graphe.get("nodes") or [] if n.get("id")}
    par_fiche: dict[str, dict] = {}
    ecartes = {"arete_sans_extremite": 0, "fiche_hors_depot": 0, "boucle": 0}
    for a in graphe.get("links") or []:
        producteur = PRODUCTEURS_COMPOSES.get(a.get("_origin"))
        if producteur is None:
            continue
        # la composition range `source` et `target` dans un ordre à elle : `_src` et `_tgt` gardent le sens de l'arête
        bouts = [noeuds.get(a.get("_src", a["source"])), noeuds.get(a.get("_tgt", a["target"]))]
        if any(b is None or de_balisage(b.get("label", "")) for b in bouts):
            ecartes["arete_sans_extremite"] += 1
            continue
        try:
            source = relatif(str(a.get("source_file") or ""), racine)
        except LotInvalide:
            ecartes["fiche_hors_depot"] += 1
            continue
        de, vers = (entite_composee(b, source) for b in bouts)
        cles = [x.get("id") or x["name"] for x in (de, vers)]
        if cles[0] == cles[1]:
            ecartes["boucle"] += 1
            continue
        x = par_fiche.setdefault(source, {"entites": {}, "relations": []})
        for cle, e in zip(cles, (de, vers)):
            x["entites"].setdefault(cle, e)
        x["relations"].append(
            {
                "from": cles[0],
                "to": cles[1],
                "type": a["relation"],
                "inferred": a.get("confidence") != "EXTRACTED",
                "chunk_ids": [],
                "metadata": chaines(
                    {
                        "_source": source,
                        "_producer": producteur,
                        "confidence": a.get("confidence"),
                        **{k: a.get(k) for k in ("confidence_score", "weight", "contributor", "source_location")},
                    }
                ),
            }
        )
    return {
        s: {"entites": list(x["entites"].values()), "relations": x["relations"]} for s, x in par_fiche.items()
    }, ecartes


RE_BALISAGE = re.compile(r"^[\s*_`#>]+|[\s*_`:]+$")


def nettoyer_titres(extraction: dict) -> int:
    """Retire le balisage markdown des libellés des nœuds de titre de l'AST : une ligne en gras lue comme un titre
    donnait un nœud « **Total Fines** » (12.10.2026 : 651 libellés d'un graphe de conformité, dont « **Real-time remote biometric
    identification…** » que rien ne retrouvait). Rend le nombre de libellés changés."""
    n = 0
    for x in extraction.get("nodes", []):
        l = x.get("label")
        if not isinstance(l, str):
            continue
        propre = RE_BALISAGE.sub("", l.replace("**", "").replace("__", "").replace("`", "")).strip()
        propre = re.sub(r"\s+", " ", propre)
        if propre and propre != l:
            x["label"] = propre
            n += 1
    return n


def fondre_doublons_de_fiche(
    extraction: dict, ids_squelette: set[str], labels_squelette: dict[str, str], nom: str
) -> tuple[int, int]:
    """Retire d'une extraction les nœuds qui redécrivent une fiche du squelette.

    Pourquoi. Le squelette décrit chaque fiche avec son titre et son typage ; la
    passe AST et les lots sémantiques la redécrivent au passage, avec une
    étiquette parfois moins bonne et sans notre typage. La déduplication de
    la composition tranche alors par ressemblance d'étiquette, et conserve
    arbitrairement l'un ou l'autre : vingt-neuf fiches perdaient ainsi leur
    typage, et une sur trois son titre.

    On préfère donc une règle explicite : sur une fiche, le squelette fait foi.
    Les arêtes de l'extraction sont réécrites vers son identifiant, ce qui ne
    perd aucune relation.
    """
    remap: dict[str, str] = {}
    gardes = []
    for n in extraction["nodes"]:
        nid, label = n["id"], n.get("label", "")
        if nid in ids_squelette:
            # Même identifiant que le squelette : la redéclaration est retirée
            # de l'extraction, pour que les attributs du squelette prévalent.
            # Les arêtes gardent l'identifiant, que le squelette fournit.
            continue
        if n.get("file_type") in ("document", "paper"):
            cle = normaliser_etiquette(label)
            cible = labels_squelette.get(cle) or labels_squelette.get(
                normaliser_etiquette(RE_PARENTHESE_FINALE.sub("", label))
            )
            if cible:
                remap[nid] = cible
                continue
        gardes.append(n)
    extraction["nodes"] = gardes

    aretes = []
    for e in extraction["edges"]:
        e["source"] = remap.get(e["source"], e["source"])
        e["target"] = remap.get(e["target"], e["target"])
        if e["source"] != e["target"]:  # la fusion peut créer des boucles
            aretes.append(e)
    boucles = len(extraction["edges"]) - len(aretes)
    extraction["edges"] = aretes
    for h in extraction.get("hyperedges", []):
        h["nodes"] = list(dict.fromkeys(remap.get(x, x) for x in h.get("nodes", [])))
    return len(remap), boucles


class CoucheUn:
    """La couche 1 d'une fiche sans graphe composé : le squelette entier (`extraire_squelette.construire`), préparé une
    fois par passage, puis la structure des titres de chaque fiche (`structure_titres.extraire`). Sur une fiche, le
    squelette fait foi : la structure perd le balisage de ses titres et le nœud qui redécrit la fiche, par les règles de
    la composition, retirée depuis (K3). Le puits de la nuit et le passage continu passent tous deux par ici : une fiche
    inchangée garde son empreinte de l'un à l'autre. `ecartes` compte les arêtes écartées au fil des fiches."""

    def __init__(self, squelette: dict, racine: Path):
        self.racine = racine
        self.noeuds = squelette.get("nodes") or []
        self.par_id = {n["id"]: n for n in self.noeuds}
        self.ids = set(self.par_id)
        self.titres: dict[str, str] = {}
        self.fiches: set[str] = set()
        for n in self.noeuds:
            if n.get("kind") in FICHES_SQUELETTE:
                self.titres.setdefault(normaliser_etiquette(n.get("label", "")), n["id"])
                self.fiches.add(self._source(n))
        self.aretes: dict[str, list[dict]] = {}
        for a in squelette.get("edges") or []:
            self.aretes.setdefault(self._source(a), []).append(a)
        # une arête sans fiche du dépôt n'entre dans aucun versement
        self.ecartes = {"arete_sans_extremite": 0, "fiche_hors_depot": len(self.aretes.pop("", [])), "boucle": 0}

    def _source(self, x: dict) -> str:
        try:
            return relatif(str(x.get("source_file") or ""), self.racine)
        except LotInvalide:
            return ""

    def d_une_fiche(self, source: str, structure: dict | None) -> dict | None:
        """Les entités et relations de couche 1 de la fiche ; None si le squelette ne la connaît pas."""
        if source not in self.fiches:
            return None
        st = {"nodes": [dict(n) for n in (structure or {}).get("nodes") or []]}
        st["edges"] = [dict(a) for a in (structure or {}).get("edges") or []]
        nettoyer_titres(st)
        fondre_doublons_de_fiche(st, self.ids, self.titres, "structure")
        # Un lien de la fiche vers un fichier que le graphe ne porte pas n'est pas une arête : la composition ne la
        # garde pas non plus, et elle n'entre pas au compte des arêtes sans extrémité.
        connus = self.ids | {n["id"] for n in st["nodes"]}
        liens = [a for a in st["edges"] if a.get("relation") != "references" or a["target"] in connus]
        aretes = [*self.aretes.get(source, []), *liens]
        # les seuls nœuds du squelette que la fiche touche : le squelette entier, par fiche, coûterait la nuit
        bouts = dict.fromkeys(i for a in aretes for i in (a["source"], a["target"]) if i in self.par_id)
        compose = {"nodes": [*(self.par_id[i] for i in bouts), *st["nodes"]], "links": aretes}
        par_fiche, ecartes = couche1_par_fiche(compose, self.racine)
        for k, n in ecartes.items():
            self.ecartes[k] += n
        return par_fiche.get(source) or {"entites": [], "relations": []}


def couche1_d_une_fiche(squelette: dict, structure: dict | None, source: str, racine: Path) -> dict | None:
    """La couche 1 d'une seule fiche (`CoucheUn`, qui prépare le squelette une fois pour plusieurs fiches)."""
    return CoucheUn(squelette, racine).d_une_fiche(source, structure)


def squelette_du_graphe(graphe: str) -> dict:
    """Le squelette du graphe, tiré de ses fiches (`extraire_squelette.construire`)."""
    from newskit import extraire_squelette as ex  # noqa: PLC0415

    return ex.construire(graphe)[0]


def structure_de(source: str) -> dict:
    """La structure des titres d'une fiche (`structure_titres.extraire`)."""
    from newskit import structure_titres as st  # noqa: PLC0415

    return st.extraire([RACINE / source], RACINE)


def versement_d_une_fiche(
    source: str,
    lot: dict | None,
    couche1: dict | None,
    canon: dict | None,
    droits: dict | None,
    racine: Path,
) -> dict | None:
    """Le versement d'une fiche (chemin relatif `source`) : son texte ou sa notice (`corps_versable`), les entités et
    relations de son lot de couche 2 s'il existe, sa couche 1 si elle est donnée. None si la fiche n'est plus sur le
    disque ; LotInvalide si son lot ne se lit pas. Le puits de la nuit et le passage continu passent par ici : une
    fiche inchangée garde la même empreinte de l'un à l'autre."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    chemin = racine / source
    if not chemin.exists():
        return None
    entete, corps = fiche.lire_fichier(chemin)
    corps = corps_versable(cfg.source_du_chemin(source), entete, corps, droits, chemin=source)
    if lot:
        v = lot_vers_cortexdb(lot, entete, corps, racine, canon)
    else:
        v = fiche_vers_cortexdb(source, entete, corps)
    completer(v, couche1)
    return v


def completer(v: dict, couche1: dict | None) -> None:
    """Ajoute au versement d'une fiche sa couche 1 : les entités qu'il n'a pas déjà (celle du lot l'emporte, nommée par
    le squelette), et toutes les relations."""
    if not couche1:
        return
    vues = {e.get("id") or e["name"] for e in v["entites"]}
    for e in couche1["entites"]:
        if (e.get("id") or e["name"]) not in vues:
            vues.add(e.get("id") or e["name"])
            v["entites"].append(e)
    v["relations"].extend(couche1["relations"])


def synthese_vers_cortexdb(cid: str, notion: dict, relayee=None) -> dict | None:
    """Une synthèse de la pyramide (synthese/notions.json) : un document de la collection `pyramide`, rattaché à
    l'entité de sa notion ; ses preuves (fiches distinctes, dans l'ordre) en métadonnée. Grade « self_consistent » :
    rédigée d'après des sources et contrôlée mot à mot, jamais une source elle-même. Sans phrase, rien n'est versé.
    `relayee(fiche)` : une phrase qui s'appuie sur une fiche non relayée n'est pas citée, la synthèse sert de carte
    (notion, preuves, sources) ; porteur, 25.10.2026."""
    brutes = [ph for ph in notion.get("phrases") or [] if str(ph.get("texte") or "").strip()]
    if not brutes:
        return None
    phrases = [
        str(ph["texte"]).strip()
        for ph in brutes
        if relayee is None or all(relayee(r["fiche"]) for r in ph.get("refs") or [] if r.get("fiche"))
    ]
    if not phrases:
        phrases = ["(synthèse non citée : ses sources ne sont pas relayées ; suivre ses preuves)"]
    nom = notion["label"]
    preuves = list(
        dict.fromkeys(r["fiche"] for ph in notion["phrases"] for r in ph.get("refs") or [] if r.get("fiche"))
    )
    document = {
        "document_id": court(f"pyramide/{cid}"),
        "title": nom,
        "author": "",
        "collection": COLLECTION_PYRAMIDE,
        "content": "\n".join([nom, *phrases]),
        "source_url": "",
        "metadata": chaines(
            {
                "_producer": PRODUCTEUR_PYRAMIDE,
                "_grade": "self_consistent",
                "newskit_id": cid,
                "preuves": preuves,
                "sources": [court(f) for f in preuves],
                "empreinte": notion.get("empreinte"),
                "degre": notion.get("degre"),
            }
        ),
    }
    entite = {
        "name": nom,
        "type": "concept",
        "chunk_ids": [],
        "metadata": chaines({"_producer": PRODUCTEUR_PYRAMIDE, "newskit_id": cid}),
    }
    return {"document": document, "origine": f"pyramide/{cid}", "entites": [entite], "relations": [], "ecartes": {}}


def _plier(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def morceau_de(noms: list[str], morceaux: list[tuple[str, str]]) -> str | None:
    """Le premier morceau qui nomme l'un des `noms` en mots entiers (sans casse ni accent), sinon le premier."""
    cles = [n for n in map(_plier, noms) if n.strip()]
    for cid, texte in morceaux:
        plie = _plier(texte)
        if any(n in plie for n in cles):
            return cid
    return morceaux[0][0] if morceaux else None


def rattacher(v: dict, morceaux: list[tuple[str, str]]) -> None:
    """Chaque entité à un seul morceau, celui qui la nomme ; chaque relation au morceau qui porte son extrait
    (07.11.2026 : le morceau deviné par le nom d'une extrémité était faux pour la moitié des relations d'un essai sur
    huit fiches), à défaut à celui qui nomme l'une de ses extrémités. L'extrait est retiré de la relation : son texte
    ne part pas au cerveau. Rattacher à tous les morceaux de la fiche répétait chaque lien (un graphe d'éthique, 23.10.2026 : 1 981 466
    mentions pour 55 616 couples fiche–notion, 4 Go d'arêtes sur 5,6)."""
    noms = {}
    for e in v["entites"]:
        aussi = e.get("metadata", {}).get("aussi") or "[]"
        cle = e.get("id") or e["name"]
        noms[cle] = [e["name"], *(json.loads(aussi) if isinstance(aussi, str) else aussi)]
        m = morceau_de(noms[cle], morceaux)
        e["chunk_ids"] = [m] if m else []
    for r in v["relations"]:
        cite = r.pop("extrait", None)
        m = next((cid for cid, texte in morceaux if atteste(cite, texte)), None) if cite else None
        m = m or morceau_de(noms.get(r["from"], [r["from"]]) + noms.get(r["to"], [r["to"]]), morceaux)
        r["chunk_ids"] = [m] if m else []


def verser(client, v: dict, vus: dict[str, str] | None = None) -> dict:
    """Écrit un versement : le document, puis les entités et les relations, chacune rattachée à un morceau. `vus` :
    les identifiants déjà versés au passage ; une collision d'empreintes s'arrête avant toute écriture."""
    if vus is not None:
        inscrire(vus, v["document"]["document_id"], v["origine"])

    def appel(nom: str, **args) -> dict:
        return appeler_outil(client, nom, args)

    doc = v["document"]
    d = appel("ingest_document", **doc)
    morceaux = d.get("chunk_node_ids") or []
    lus = {}
    if v["entites"] or v["relations"]:  # sans rien à rattacher, les morceaux ne se relisent pas
        for k in range(0, len(morceaux), MORCEAUX_PAR_LECTURE):
            paquet = morceaux[k : k + MORCEAUX_PAR_LECTURE]
            lus.update(
                {
                    c["id"]: c.get("content") or ""
                    for c in appel("get_chunks", chunk_ids=paquet, disable_graph=True).get("chunks") or []
                }
            )
    rattacher(v, [(m, lus.get(m, "")) for m in morceaux])
    e = appel("upsert_entities", document_id=doc["document_id"], entities=v["entites"]) if v["entites"] else {}
    r = appel("upsert_relations", document_id=doc["document_id"], relations=v["relations"]) if v["relations"] else {}
    return {
        "morceaux": len(morceaux),
        "entites": len(e.get("entity_node_ids") or []),
        "relations": int(r.get("written") or 0),
    }


def _sortie(commande, G, **args) -> str:
    """Ce qu'une commande d'interroger_graphe imprime : la réponse de kg-mcp, mot pour mot."""
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        commande(G, argparse.Namespace(**args))
    return f.getvalue()


# Chaque vue commence par son titre et ce qu'elle dit : sans eux, th_recall ne la trouvait pas sur les mots qu'on lui
# demande (parcours K4, 31.10.2026).
EN_TETES = {
    "vue/sommets": "# Sommets du graphe\n\nLes entités les plus connectées du graphe, hors auteurs, puis pour "
    "chaque type de nœud.\n\n",
    "vue/relations": "# Relations du graphe\n\nLes types de relations du graphe et leur nombre.\n\n",
}


def en_tete_expliquer(etiquette: str) -> str:
    return (
        f"# Expliquer : {etiquette}\n\nFiche de l'entité {etiquette} : son type, son degré, ses relations et ses "
        f"voisins les plus centraux.\n\n"
    )


def carte_pour_thalamus(carte: str) -> str:
    """La carte de kg-mcp renvoie à `newskit.interroger_graphe`, qu'un agent ne lance pas par Thalamus (parcours K4,
    31.10.2026) : la section « Creuser » est remplacée par les outils de Thalamus, et les renvois au script retirés.
    La carte finit par la consigne « Signaler ce qui manque » (05.11.2026)."""
    from newskit import config_sources as cfg  # noqa: PLC0415 — le nom du cerveau des lacunes signalées

    if "\n## Creuser" in carte:
        carte = carte[: carte.index("\n## Creuser")]
    carte = carte.replace(
        "Pour creuser, `newskit.interroger_graphe`.", "Pour creuser, les outils de Thalamus (fin de la carte)."
    )
    carte = carte.replace(" `interroger_graphe.py --sans-modele` les écarte.", "")
    return (
        carte.rstrip("\n")
        + "\n\n"
        + "\n".join(
            [
                "## Creuser",
                "",
                "Par Thalamus, dans ce cerveau :",
                "",
                "- chercher : `th_recall` (le cerveau nommé dans `brains`) ;",
                "- entrer par un nom : `th_find_nodes`, puis `th_entity` ;",
                "- expliquer une entité : sa fiche « Expliquer : <nom> » (les 200 entités les plus connectées), "
                "par `th_find_nodes` sur ce titre puis `th_get` ;",
                "- voisinage : `th_expand_graph` depuis l'identifiant, en écartant `mentions`, `has_chunk` et `next` "
                "par `relation_policies` ;",
                "- chemin : `th_search_paths` ; depuis une entité très citée, monter `max_expansions` (50 000) ;",
                "- sources : `th_get` d'un passage, `th_descend` d'une synthèse (extraits avec l'adresse d'origine) ;",
                "- passages d'une entité : `th_search_chunks_by_entities` ;",
                f"- sommets : « Sommets du graphe », `th_get` {court('vue/sommets')} ; "
                f"relations : « Relations du graphe », `th_get` {court('vue/relations')}.",
                "",
                # Sans cette consigne, le cerveau des lacunes signalées était relevé à chaque routine et ne recevait rien
                # (05.11.2026) : la forme attendue n'était écrite que dans le code de la relève.
                "## Signaler ce qui manque",
                "",
                f"Quand ce graphe n'a pas su répondre, dites ce qui a manqué : `th_add` dans le cerveau `{cfg.cerveau_des_lacunes()}`.",
                "",
                "- `content` : un objet précis par ligne, 10 au plus (un nom, un texte, un article de loi, un produit ; "
                "jamais une phrase) ;",
                "- `metadata` : `graphe`, le nom de ce graphe, et `question`, la question posée.",
                "",
                "La routine relève ces lignes et va chercher ce qui manque.",
                "",
            ]
        )
    )


def vues_du_graphe(G, carte: str | None, expliquer: int = EXPLIQUER) -> list[dict]:
    """K4 du projet d'alignement (29.10.2026) : ce que kg-mcp sert sur le graphe entier, versé comme documents de la
    pyramide, que Thalamus sert entiers (texte de l'éditeur) : la carte (si elle est sur le disque), les sommets (sans
    type, hors auteurs, puis pour chaque type : un document ne prend pas de paramètre) et les relations. Le texte est
    celui de `sommets` et `relations` de kg-mcp. Une vue n'ajoute ni entité ni relation au graphe.

    S'y ajoute une fiche `expliquer` (texte de kg-mcp) pour chacune des `expliquer` entités au plus fort degré, auteurs
    écartés comme dans `sommets`, à degré égal par identifiant : sous une clé confinée, le moteur sert une entité sans
    type ni description, et ses voisins sans ordre de centralité (KGMCP.md, K4). La fiche nomme le nœud par son
    identifiant : une étiquette partagée ne mène pas au mauvais nœud."""
    from newskit import interroger_graphe as ig  # noqa: PLC0415

    types = sorted({ig._type(G, n) for n in G})
    corps = {
        "vue/carte": carte_pour_thalamus(carte) if carte else carte,
        "vue/sommets": EN_TETES["vue/sommets"]
        + "\n".join(_sortie(ig.cmd_sommets, G, type=t, limite=25) for t in [None, *types]),
        "vue/relations": EN_TETES["vue/relations"] + _sortie(ig.cmd_relations, G),
    }
    titres = {
        "vue/carte": "Carte du graphe",
        "vue/sommets": "Sommets du graphe",
        "vue/relations": "Relations du graphe",
    }
    noeuds: dict[str, str] = {}
    centraux = sorted((n for n in G if not ig.est_auteur(G, n)), key=lambda n: (-G.degree(n), str(n)))[:expliquer]
    for n in centraux:
        o = f"vue/expliquer/{n}"
        etiquette = ig._label(G, n)
        corps[o], titres[o], noeuds[o] = (
            en_tete_expliquer(etiquette) + _sortie(ig.cmd_expliquer, G, noeud=[n]),
            f"Expliquer : {etiquette}",
            n,
        )
    return [
        {
            "document": {
                "document_id": court(o),
                "title": titres[o],
                "author": "",
                "collection": COLLECTION_PYRAMIDE,
                "content": c,
                "source_url": "",
                "metadata": chaines({"_producer": PRODUCTEUR_VUES, **({"noeud": noeuds[o]} if o in noeuds else {})}),
            },
            "origine": o,
            "entites": [],
            "relations": [],
            "ecartes": {},
        }
        for o, c in corps.items()
        if c
    ]


def vues_a_verser(chemin) -> list[dict]:
    """Les vues écrites par cerveau_cortexdb.ecrire_vues. Le puits tourne dans le Python du pilote, sans networkx :
    il ne les calcule pas."""
    return json.loads(Path(chemin).read_text(encoding="utf-8"))


def choisir_lots(candidats: list[Path], retenues: set[str] | None, racine: Path) -> list[Path]:
    """Les lots à verser : un par nom de fichier, le premier venu (celui du graphe d'abord), et, pour un graphe à
    partage fiche par fiche, seulement ceux dont la fiche est retenue (GV7, mainteneur, 2 novembre 2026, « 1 ok mixte »)."""
    out, vus = [], set()
    for p in candidats:
        if p.name in vus:
            continue
        vus.add(p.name)
        if retenues is not None:
            try:
                noeuds = json.loads(p.read_text(encoding="utf-8")).get("nodes") or []
            except (OSError, json.JSONDecodeError):
                noeuds = []
            if not any(relatif(n.get("source_file") or "", racine) in retenues for n in noeuds):
                continue
        out.append(p)
    return out


def lots_du_graphe(graphe: str) -> list[Path]:
    """Les lots du graphe, ceux des graphes qu'il inclut, et ceux des fiches d'autres graphes rangées chez lui ; filtrés
    par son partage s'il en a un."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import extraire_concepts as ec  # noqa: PLC0415
    from newskit import partage_graphe  # noqa: PLC0415

    candidats = sorted((cfg.chemins_du_graphe(graphe)["base"] / "couche2" / "fiches").glob("*.json"))
    for g in cfg.graphes_inclus(graphe):
        candidats += sorted((cfg.chemins_du_graphe(g)["base"] / "couche2" / "fiches").glob("*.json"))
    retenues_d = partage_graphe.fiches_du_graphe(graphe)
    if retenues_d is None:
        return choisir_lots(candidats, None, RACINE)
    decl = partage_graphe.declaration(graphe)
    for r in partage_graphe.ajouts(decl[0], decl[1], cfg.dossiers_du_graphe(graphe)):
        if r in retenues_d:
            ident = ec.identifiant_de_fiche(RACINE / r, fiche.lire_fichier(RACINE / r)[0])
            for g in cfg.graphes():
                p = cfg.chemins_du_graphe(g)["couche2"] / "fiches" / f"{ident}.json"
                if p.exists():
                    candidats.append(p)
                    break
    return choisir_lots(candidats, set(retenues_d), RACINE)


CLE_D_EMPREINTE = ('"newskit_id":', '"contaixt_id":')


def empreinte(v: dict) -> str:
    """L'empreinte d'un versement, avant écriture : document, entités, relations (B15, versement en place). La clé de
    l'identifiant du nœud s'y lit sous son ancien nom (contaixt_id, renommée newskit_id à N5) : le renommage ne fait
    reverser aucun document ; un cerveau prend le nouveau nom au fil des versements qui changent."""
    import hashlib  # noqa: PLC0415

    texte = json.dumps(
        {k: v.get(k) for k in ("document", "entites", "relations")}, ensure_ascii=False, sort_keys=True, default=str
    ).replace(*CLE_D_EMPREINTE)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:24]


# Un changement voulu de la forme d'un versement se livre avec la fonction qui rend sa forme d'avant (mainteneur,
# 27.11.2026) : le passage reconnaît l'ancienne empreinte au relevé, y écrit la nouvelle et n'écrit rien au cerveau.
# Sans elle, chaque document dont la forme a changé serait réécrit, sans que sa fiche ait changé (0.1.85 : 3 288
# documents d'un graphe de gouvernance). Une forme se retire quand le relevé de chaque cerveau est passé.
FORMES_PRECEDENTES: list = []


def empreintes_precedentes(v: dict) -> set[str]:
    """Les empreintes que ce versement avait sous les formes précédentes déclarées."""
    return {empreinte(forme(v)) for forme in FORMES_PRECEDENTES}


class EnPlace:
    """Le versement en place (B15, mainteneur, 2 novembre 2026) : avec les empreintes du cerveau précédent, un versement
    inchangé ne s'écrit pas, un versement changé remplace l'ancien (`delete_document_graph` puis versement), un document
    absent du passage est retiré (`delete_document_graph` puis `knowledge_delete`). Demande CortexDB v2.143.0 au moins
    (D6). Sans empreintes précédentes, tout se verse, comme dans un cerveau neuf. `compter` classe chaque versement de
    la même façon sans rien écrire ni appeler : le compte d'un passage en place, avant de choisir de le faire. Un
    versement dont le relevé porte l'empreinte d'une forme précédente (`FORMES_PRECEDENTES`) est migré : le relevé du
    passage prend son empreinte du jour, le cerveau n'est pas écrit."""

    def __init__(self, client, avant: dict[str, str] | None, bilan: dict, compter: bool = False):
        self.client, self.avant, self.bilan, self.apres, self.compter = client, avant, bilan, {}, compter
        for k in ("inchanges", "migres", "remplaces", "ajoutes", "retires"):
            bilan.setdefault(k, 0)

    def appel(self, nom: str, **args) -> dict:
        return appeler_outil(self.client, nom, args)

    def verser(self, v: dict, vus: dict[str, str]) -> dict | None:
        """Verse `v` si besoin ; rend les compteurs de `verser`, ou None pour un versement inchangé."""
        ident, h = v["document"]["document_id"], empreinte(v)
        self.apres[ident] = h
        if self.avant is not None and self.avant.get(ident) == h:
            inscrire(vus, ident, v["origine"])
            self.bilan["inchanges"] += 1
            return None
        if self.avant is not None and self.avant.get(ident) in empreintes_precedentes(v):
            inscrire(vus, ident, v["origine"])
            self.bilan["migres"] += 1
            return None
        if self.avant is not None and ident in self.avant:
            self.bilan["remplaces"] += 1
            if not self.compter:
                self.appel("delete_document_graph", document_id=ident)
        else:
            self.bilan["ajoutes"] += 1
        return None if self.compter else verser(self.client, v, vus)

    def retirer(self, ident: str) -> None:
        """Retire un document du cerveau : son graphe (morceaux, entités devenues orphelines), puis sa connaissance."""
        self.bilan["retires"] += 1
        if not self.compter:
            self.appel("delete_document_graph", document_id=ident)
            self.appel("knowledge_delete", knowledge_id=ident)

    def retirer_absents(self) -> None:
        for ident in sorted(set(self.avant or {}) - set(self.apres)):
            self.retirer(ident)


def main() -> int:
    import contextlib  # noqa: PLC0415

    from newskit import config_sources as cfg  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--adresse", default="127.0.0.1:47899")
    ap.add_argument("--jeton", help="jeton du serveur (sauf --compter)")
    ap.add_argument("--limite", type=int, default=0, help="n'en verser que N (essai)")
    ap.add_argument("--pyramide", action="store_true", help="verser aussi les synthèses (synthese/notions.json)")
    ap.add_argument("--couche1", action="store_true", help="verser aussi le squelette et la structure des titres (K0)")
    ap.add_argument(
        "--vues",
        type=Path,
        help="verser aussi la carte, les sommets et les relations (K4), calculées par "
        "cerveau_cortexdb.ecrire_vues dans ce fichier",
    )
    ap.add_argument("--empreintes-avant", type=Path, help="empreintes du cerveau précédent : versement en place (B15)")
    ap.add_argument("--empreintes-apres", type=Path, help="où écrire les empreintes de ce passage")
    ap.add_argument(
        "--compter",
        action="store_true",
        help="avec --empreintes-avant : compter ce qu'un passage en place remplacerait, ajouterait et retirerait, "
        "sans serveur et sans rien écrire",
    )
    args = ap.parse_args()
    if args.compter and not args.empreintes_avant:
        ap.error("--compter demande --empreintes-avant")
    if not args.compter and not args.jeton:
        ap.error("--jeton est requis pour verser")
    if args.empreintes_avant and args.limite:
        ap.error("--empreintes-avant et --limite : un versement en place partiel retirerait le reste")
    base = cfg.chemins_du_graphe(args.graphe)["base"]
    alias_p = base / "alias.json"
    table = json.loads(alias_p.read_text(encoding="utf-8")) if alias_p.exists() else {}
    noms: dict[str, set[str]] = {}  # identifiant de nœud → noms versés, pour fondre les alias à la fin
    lots = lots_du_graphe(args.graphe)
    if args.couche1:
        # Le squelette et la structure des titres, fiche par fiche, comme le passage continu (`cerveau_continu`) : le
        # graphe composé donne à une fiche d'autres relations (extrémités rangées, titres fondus, arête gagnée par
        # une fiche ancienne), et la nuit remplaçait presque chaque document versé le jour.
        squelette = squelette_du_graphe(args.graphe)
        graphe = {"nodes": squelette.get("nodes") or []}
        canon = canon_squelette(graphe)
        preparee = CoucheUn(squelette, RACINE)
    else:
        graphe = {}  # sans couche 1, rien ne fait foi sur une fiche
        canon = preparee = None
    calculees: dict[str, int] = {}
    faites: set[str] = set()

    def couche1(source: str) -> dict | None:
        """La couche 1 de la fiche ; rien pour un graphe sans couche 1."""
        if preparee is None:
            return None
        c = preparee.d_une_fiche(source, structure_de(source))
        calculees[source] = len((c or {}).get("relations") or [])
        return c

    def versee(source: str) -> None:
        """La fiche a son document : sa couche 1 entre au bilan."""
        faites.add(source)
        bilan["couche1_relations"] += calculees.pop(source, 0)

    if args.limite:
        lots = lots[: args.limite]
    bilan = {
        "lots": 0,
        "verses": 0,
        "sans_lot": 0,
        "fiche_absente": 0,
        "invalides": 0,
        "morceaux": 0,
        "entites": 0,
        "relations": 0,
        "libelle_de_balisage": 0,
        "arete_sans_extremite": 0,
        "couche1_relations": 0,
        "couche1_sans_document": 0,
    }
    avec_lot: set[str] = set()
    droits = cfg.charger_droits_auteurs()
    relayee = relayeur(droits)
    t0 = time.time()
    vus: dict[str, str] = {}
    if args.compter:
        connexion = contextlib.nullcontext(None)
    else:
        from cortexdb_client import CortexClient  # noqa: PLC0415

        connexion = CortexClient.connect(args.adresse, token=args.jeton)
    with connexion as client:
        avant = json.loads(args.empreintes_avant.read_text(encoding="utf-8")) if args.empreintes_avant else None
        ep = EnPlace(client, avant, bilan, compter=args.compter)

        def versement(v: dict) -> dict:
            return ep.verser(v, vus) or {}

        for k, p in enumerate(lots, 1):
            bilan["lots"] += 1
            lot = json.loads(p.read_text(encoding="utf-8"))
            if not lot.get("nodes"):
                bilan["invalides"] += 1
                continue
            source = relatif(lot["nodes"][0]["source_file"], RACINE)
            avec_lot.add(source)
            try:
                v = versement_d_une_fiche(source, lot, couche1(source), canon, droits, RACINE)
            except LotInvalide as e:
                bilan["invalides"] += 1
                print(f"  !! {p.name} : {e}", file=sys.stderr)
                continue
            if v is None:  # un lot dont la fiche a disparu : compté, jamais versé
                bilan["fiche_absente"] += 1
                continue
            for cle, n in v["ecartes"].items():
                bilan[cle] += n
            for ident, nom in v["noms"].items():
                noms.setdefault(ident, set()).add(nom)
            versee(source)
            for cle, n in versement(v).items():
                bilan[cle] += n
            bilan["verses"] += 1
            if k % 100 == 0:
                print(f"  {k}/{len(lots)} ({time.time() - t0:.0f} s)", flush=True)
        if graphe and not args.limite:
            for source in fiches_sans_lot(graphe, avec_lot, RACINE):
                v = versement_d_une_fiche(source, None, couche1(source), canon, droits, RACINE)
                if v is None:
                    bilan["fiche_absente"] += 1
                    continue
                versee(source)
                for cle, n in versement(v).items():
                    bilan[cle] += n
                bilan["sans_lot"] += 1
                if bilan["sans_lot"] % 500 == 0:
                    print(f"  sans lot {bilan['sans_lot']} ({time.time() - t0:.0f} s)", flush=True)
        if args.pyramide:
            notions = json.loads((base / "synthese" / "notions.json").read_text(encoding="utf-8"))["notions"]
            bilan["syntheses"] = 0
            for cid, notion in notions.items():
                v = synthese_vers_cortexdb(cid, notion, relayee)
                if v:
                    noms.setdefault(cid, set()).add(v["entites"][0]["name"])
                    versement(v)
                    bilan["syntheses"] += 1
        if args.vues:
            vues = vues_a_verser(args.vues)
            for v in vues:
                versement(v)
            bilan["vues"] = len(vues)
        if not args.limite:
            ep.retirer_absents()
            if not args.compter:  # après les versements et les retraits : une entité à fondre a son nœud
                bilan.update(fondre_alias(ep.appel, groupes_d_alias(table, noms)))
            if args.empreintes_apres and not args.compter:
                args.empreintes_apres.write_text(json.dumps(ep.apres, ensure_ascii=False), encoding="utf-8")
    if preparee is not None:
        bilan.update({f"couche1_{k}": n for k, n in preparee.ecartes.items()})
        if not args.limite:  # une couche 1 sans document : sa fiche n'est pas au cerveau
            bilan["couche1_sans_document"] = sum(len(a) for s, a in preparee.aretes.items() if s not in faites)
    bilan["duree_s"] = round(time.time() - t0, 1)
    print(json.dumps(bilan, ensure_ascii=False))
    return 0 if not bilan["invalides"] else 1


if __name__ == "__main__":
    sys.exit(main())

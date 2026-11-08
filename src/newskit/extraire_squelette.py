#!/usr/bin/env python3
"""Squeletteur : construit la couche déterministe du graphe de connaissances.

Aucun appel de modèle de langage, aucune dépendance hors bibliothèque standard.
Le corpus livre déjà ses arêtes (frontmatter, liens sortants, manifestes) ; ce
script les récolte telles quelles, avec la provenance exacte de chacune.

Sortie : graphe/squelette.json, au format d'extraction de graphify
    {"nodes": [{id, label, file_type, source_file, ...}],
     "edges": [{source, target, relation, confidence, source_file, ...}]}

Le script est intégralement rejouable : il relit tout le corpus à chaque
exécution et réécrit sa sortie. Il n'a aucun état incrémental, donc il ne peut
pas dériver silencieusement par rapport au corpus.

Usage :
    python3 -m newskit.extraire_squelette            # écrit graphe/squelette.json
    python3 -m newskit.extraire_squelette --graphe <graphe>   # un autre graphe du registre, sous graphe/<nom>/
    python3 -m newskit.extraire_squelette --stats    # relevé chiffré, sans écriture
    python3 -m newskit.extraire_squelette --valider  # valide via graphify si disponible
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from newskit import fiche  # contrat de frontmatter, source unique de vérité
from newskit.commun import archiver_gz, normaliser_identifiant  # noqa: E402

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

DOSSIERS_CORPUS = ["sources", "recherche"]
SORTIE = RACINE / "graphe" / "squelette.json"

# --- Exclusions, tenues ici pour rester auditables -------------------------
# Hébergeurs d'images. Les fiches Substack portent leurs illustrations comme des
# liens à texte vide ; on les écarte aussi par cette règle (voir liens_du_texte).
DOMAINES_IMAGE = {"substackcdn.com", "media.licdn.com", "substack-post-media.s3.amazonaws.com"}

# Autopromotion des auteurs et pages de service : ce ne sont pas des arêtes de
# connaissance. Mesuré sur le corpus : ~2 500 occurrences.
DOMAINES_PROMO = {
    "linktr.ee",
    "forms.gle",
    "docs.google.com",
    "instagram.com",
    "www.instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "open.spotify.com",
    "amazon.com",
    "www.amazon.com",
    "amzn.to",
    "bit.ly",
    "rb.gy",
    "mng.bz",
    "lnkd.in",
    "t.co",
    "buff.ly",
    "patreon.com",
    "ko-fi.com",
    # AI Secret (graphe d'actualité) : ses liens de suivi et ses publications sœurs,
    # présents dans chaque billet — les nœuds « externes » les plus connectés
    # du graphe étaient ces quatre sites, mesuré le 27.09.2026.
    "go.aisecret.us",
    "bayarealetters.com",
    "roboticsherald.com",
    "marketingsecret.us",
    "thehardwire.com",
}
# Chemins de service Substack et consorts. Les pages sont reconnues par leur
# fin de chemin, les segments d'API par leur présence : `nettoyer` retire le
# slash final, donc un chemin ne finit jamais par « /api/v1/ ».
PAGES_SERVICE = ("/subscribe", "/share", "/comments", "/archive", "/about")
SEGMENTS_SERVICE = ("/api/v1/", "/api/v1")

# Paramètres de suivi à retirer avant tout appariement.
PARAMS_SUIVI = {
    "ref",  # ?ref=<domaine> sur chaque lien sortant d'une lettre Ghost
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "r",
    "si",
    "nd",
    "dlsi",
    "trackingid",
    "triedredirect",
    "action",
    "usp",
    "gi",
    "sa",
    "ust",
    "source",
    "originalsubdomain",
    "trk",
    "feature",
}

# Enveloppes qui masquent la vraie cible.
ENVELOPPES = (
    ("medium.com/r/", "url"),
    ("google.com/url", "q"),
    ("linkedin.com/redir/redirect", "url"),
)

# Une URL répétée au-delà de ce seuil dans tout le corpus est presque toujours
# un lien promotionnel récurrent (mesuré : rb.gy ×266, un formulaire ×519).
SEUIL_REPETITION_PROMO = 50

SUFFIXES_TITRE = {"phd", "ph.d", "ph.d.", "dr", "dr.", "md", "m.d.", "prof", "prof.", "msc", "mba"}

# --- Identifiants : réplique exacte de graphify.ids.normalize_id -----------


normalize_id = normaliser_identifiant


def make_id(*parts: str) -> str:
    """Identifiant canonique, identique à celui que produit graphify."""
    return normalize_id("_".join(p.strip("_.") for p in parts if p))


# --- Lecture des fiches ----------------------------------------------------

# La lecture du frontmatter vit dans fiche.py : le squeletteur ne redéfinit pas
# le schéma, il le consomme. Un seul endroit décrit le contrat.
lire_frontmatter = fiche.lire


RE_LIEN_MD = re.compile(r"\[([^\]]*)\]\((\s*<?)([^)\s]+)>?\s*\)")
RE_URL_NUE = re.compile(r"https?://[^\s<>\"'\)\]]+")


def liens_du_texte(corps: str, premiere_ligne: int):
    """Produit (url, numéro de ligne) pour chaque lien du corps.

    Les liens markdown à texte vide sont des images dans ce corpus (6 834
    occurrences mesurées, dont 6 663 vers substackcdn) : ils sont écartés ici.
    """
    for decalage, ligne in enumerate(corps.split("\n")):
        numero = premiere_ligne + decalage
        reste = ligne
        for m in RE_LIEN_MD.finditer(ligne):
            texte_lien, url = m.group(1).strip(), m.group(3)
            reste = reste.replace(m.group(0), " ")
            if not texte_lien:  # [](...) == image
                continue
            if url.startswith("http"):
                yield url, numero
        for m in RE_URL_NUE.finditer(reste):
            yield m.group(0), numero


# --- Normalisation des liens ----------------------------------------------


def deballer(url: str) -> str:
    """Déballe les enveloppes qui masquent la cible réelle (medium.com/r, etc.)."""
    for motif, param in ENVELOPPES:
        if motif in url:
            valeurs = parse_qs(urlsplit(url).query).get(param)
            if valeurs and valeurs[0].startswith("http"):
                return unquote(valeurs[0])
    return url


def nettoyer(url: str) -> str:
    """Ponctuation finale, fragment, paramètres de suivi, hôte, slash final."""
    url = deballer(url.strip())
    url = url.rstrip(".,;:!?*)…'\"")
    try:
        p = urlsplit(url)
    except ValueError:  # un crochet dans l'adresse (regex citée dans un billet de METR, 03.10.2026) : pas une URL
        return ""
    hote = p.netloc.lower()
    if hote.startswith("www."):
        hote = hote[4:]
    garde = [
        f"{k}={v}"
        for k, v in (kv.split("=", 1) for kv in p.query.split("&") if "=" in kv)
        if k.lower() not in PARAMS_SUIVI
    ]
    requete = "&".join(garde)
    chemin = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{hote}{chemin}" + (f"?{requete}" if requete else "")


def est_exclu(url: str) -> bool:
    p = urlsplit(url)
    hote = p.netloc.lower().removeprefix("www.")
    if hote in DOMAINES_IMAGE or hote in DOMAINES_PROMO:
        return True
    if re.search(r"\.(png|jpe?g|gif|webp|svg|ico)($|\?)", p.path, re.I):
        return True
    if any(p.path.endswith(c) for c in PAGES_SERVICE):
        return True
    return any(s in p.path for s in SEGMENTS_SERVICE)


RE_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
RE_ARXIV_ANCIEN = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([a-z-]+(?:\.[A-Z]{2})?/\d{7})", re.I)
RE_GITHUB = re.compile(r"github\.com/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)", re.I)

NON_DEPOTS = {"orgs", "sponsors", "features", "about", "pricing", "settings", "topics", "collections"}


def canon_arxiv(url: str) -> str | None:
    """Identifiant arXiv nu, versions et extensions retirées.

    Rejette les identifiants impossibles. arXiv est passé à cinq chiffres après
    le point en avril 2015 ; un `2401.1207` est donc un identifiant tronqué dans
    l'article source, et quatre cas réels existent dans le corpus. On ne devine
    pas le chiffre manquant : `2401.1207` pourrait aussi bien désigner
    `2401.12070` que `2401.12071`, et une arête fausse coûte plus cher qu'une
    arête absente.
    """
    m = RE_ARXIV.search(url) or RE_ARXIV_ANCIEN.search(url)
    if not m:
        return None
    aid = m.group(1).lower()
    court = re.match(r"^(\d{2})(\d{2})\.(\d{4})$", aid)
    if court and f"{court.group(1)}{court.group(2)}" >= "1504":
        return None
    return aid


def canon_github(url: str) -> str | None:
    """`owner/repo`, sans /blob /tree /issues /pull ni ponctuation."""
    m = RE_GITHUB.search(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).rstrip(".,;:")
    if owner.lower() in NON_DEPOTS or repo.lower().endswith(".git"):
        return None
    return f"{owner}/{repo}"


RE_SLUG_SUBSTACK = re.compile(r"/p/([^/?#]+)")
RE_SLUG_PULSE = re.compile(r"/pulse/([^/?#]+)")


def slug_article(url: str) -> str | None:
    """Slug d'article, voie d'appariement robuste aux alias de domaine.

    Nécessaire : une lettre Substack mesurée était citée 1 402 fois sous
    `<nom>.substack.com` et 300 fois sous son domaine propre. Apparier sur l'URL entière perdrait 83 % de ces arêtes.
    """
    for regex in (RE_SLUG_SUBSTACK, RE_SLUG_PULSE):
        m = regex.search(url)
        if m:
            return unquote(m.group(1)).lower()
    return None


# --- Auteurs ---------------------------------------------------------------


def normaliser_auteurs(brut: str) -> list[str]:
    """Scinde les co-auteurs et retire les suffixes de titre.

    Le frontmatter porte parfois plusieurs personnes dans une seule chaîne
    ("Tivadar Danka, Alejandro Piad Morffis") et parfois un suffixe qui crée un
    doublon ("Sebastian Raschka, PhD" face à "Sebastian Raschka"). On scinde et
    on retire le suffixe ; on ne fusionne jamais par similarité approximative,
    seule l'égalité après normalisation fusionne.
    """
    brut = fiche.separer_auteur(brut)[0]
    if not brut:
        return []
    # Un « & » de raison sociale ne sépare pas deux personnes (Clyde & Co LLP)
    segments = [
        s.strip()
        for s in re.split(r",| et | and |&(?!\s*(?:Co|Company|Partners|Associates|Sons)\b)|;", brut)
        if s.strip()
    ]
    noms = [
        s
        for s in segments
        if s.lower().rstrip(".").replace(" ", "") not in {x.replace(".", "").replace(" ", "") for x in SUFFIXES_TITRE}
    ]
    return [re.sub(r"\s+", " ", n) for n in noms if len(n) > 1]


# --- Index des manifestes --------------------------------------------------


def construire_index(fiches: dict[Path, dict]) -> dict[str, str]:
    """slug d'article -> identifiant de nœud article.

    Trois voies, parce que les sources n'ont pas la même structure :
    le manifeste LinkedIn est indexé par URL, les manifestes Substack par slug,
    et Medium n'a pas de manifeste (on passe par l'URL du frontmatter).
    """
    index: dict[str, str] = {}
    for chemin, f in fiches.items():
        url = f["champs"].get("url", "")
        if not url or f["arxiv_id"]:
            # Une fiche arXiv n'entre pas dans l'index d'articles. Sans cela, un
            # lien vers un papier serait résolu en `cite_article` au lieu de
            # `cite_papier`, alors que les deux voies désignent le même nœud :
            # seule la relation, donc le sens, changerait.
            continue
        slug = slug_article(nettoyer(url))
        if slug:
            index.setdefault(slug, f["id"])
        if nettoyer(url):
            index.setdefault(nettoyer(url).lower(), f["id"])
    return index


# --- Triplets du who-is-who ------------------------------------------------

RE_TRIPLET = re.compile(r"^-\s*(.+?)\s*->\s*(.+?)\s*->\s*(.+?)\s*(?:\[([A-C](?:/[A-C])?)\])?\s*$")
# Traduction honnête de la certitude vers le vocabulaire de graphify. On retient
# toujours le niveau le plus faible mentionné : A/B donne INFERRED, jamais
# EXTRACTED. Aucun triplet n'est promu au-delà de ce que sa source permet.
CERTITUDE = {"A": "EXTRACTED", "B": "INFERRED", "C": "AMBIGUOUS"}


def triplets_who_is_who(chemin: Path, id_article: str):
    """Produit (sujet, relation, objet, confiance, ligne) depuis la section Relations."""
    dans_section = False
    for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").split("\n"), start=1):
        if ligne.startswith("## "):
            dans_section = "relations" in ligne.lower()
            continue
        if not dans_section:
            continue
        m = RE_TRIPLET.match(ligne.strip())
        if not m:
            continue
        sujet, relation, objet, niveau = m.group(1), m.group(2), m.group(3), m.group(4) or "C"
        pire = sorted(niveau.split("/"))[-1]  # A/B -> B
        yield sujet.strip(), relation.strip(), objet.strip(), CERTITUDE[pire], numero


# --- Construction ----------------------------------------------------------
#
# La construction se lit en cinq temps, dans l'ordre où ils s'exécutent :
#   1. charger le corpus, et donner à chaque fiche son identité ;
#   2. compter les URL, pour repérer la promotion récurrente et les liens
#      adoptés par au moins deux fiches ;
#   3. déclarer les papiers arXiv avant tout lien, pour qu'une citation ne
#      crée pas une coquille à la place d'une fiche complète ;
#   4. pour chaque fiche : son nœud, ses auteurs, sa publication, ses liens ;
#   5. les triplets du who-is-who.
# L'ordre d'insertion des nœuds compte : un nœud est frappé par la première
# fiche qui le cite, et le fichier de sortie est écrit dans cet ordre. Toute
# réorganisation de ces étapes doit laisser `graphe/squelette.json` identique
# à l'octet — c'est le témoin utilisé à chaque refactorisation.


class Squelette:
    """Le graphe en construction : nœuds, arêtes, et le relevé chiffré."""

    def __init__(self) -> None:
        self.noeuds: dict[str, dict] = {}
        self.aretes: list[dict] = []
        self.stats: Counter = Counter()

    def noeud(self, nid: str, label: str, file_type: str, kind: str, source_file: str, **extra) -> str:
        """Déclare un nœud s'il n'existe pas. La première déclaration fait foi."""
        if nid not in self.noeuds:
            self.noeuds[nid] = {
                "id": nid,
                "label": label,
                "file_type": file_type,
                "kind": kind,
                "source_file": source_file,
                **extra,
            }
        return nid

    def arete(self, src: str, dst: str, relation: str, source_file: str, ligne, confiance: str = "EXTRACTED") -> None:
        if src == dst:
            return
        self.aretes.append(
            {
                "source": src,
                "target": dst,
                "relation": relation,
                "confidence": confiance,
                "source_file": source_file,
                "source_location": f"L{ligne}",
                "weight": 1.0,
                "_origin": "squelette",
            }
        )
        self.stats[relation] += 1

    def extraction(self) -> dict:
        return {"nodes": list(self.noeuds.values()), "edges": self.aretes}


def charger_corpus(
    avertissements: list[str], dossiers: list[str] | None = None, fichiers: list[Path] | None = None
) -> dict[Path, dict]:
    """Lit toutes les fiches et donne à chacune son identité de nœud.

    L'identité est le chemin relatif sans extension, exactement ce que graphify
    emploie pour ses nœuds de document : les deux couches désignent la même
    fiche par le même identifiant et fusionnent au lieu de se dupliquer. Une
    fiche arXiv, elle, prend l'identifiant du papier qu'elle décrit, pour que
    le nœud de la fiche et le nœud cité par le corpus ne fassent qu'un.

    Les compléments écrits par un modèle sont lus en dernier : un dépôt cité à
    la fois par une publication et par un complément doit être frappé par la
    publication, sinon « qui atteste ce dépôt ? » désignerait le modèle.
    """
    from newskit import config_sources as cfg  # noqa: PLC0415

    dossiers_modele = tuple(f"sources/{s.dossier}/" for s in cfg.toutes("Modèle"))

    def ordre_de_lecture(c: Path) -> tuple[int, str]:
        rel = str(c.relative_to(RACINE))
        return (1 if rel.startswith(dossiers_modele) else 0, rel)

    # `fichiers` : la liste exacte d'un graphe à partage fiche par fiche (partage_graphe.py, GV4), au lieu de ses dossiers.
    chemins = sorted(
        fichiers
        if fichiers is not None
        else (
            c for d in (dossiers or DOSSIERS_CORPUS) for c in (RACINE / d).rglob("*.md") if not c.name.startswith("_")
        ),
        key=ordre_de_lecture,
    )
    fiches: dict[Path, dict] = {}
    identites: set[str] = set()
    for chemin in chemins:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        champs, premiere = lire_frontmatter(texte)
        if not champs:
            avertissements.append(f"frontmatter absent ou illisible : {chemin.relative_to(RACINE)}")
        identite = str(chemin.relative_to(RACINE).with_suffix(""))
        if identite in identites:
            avertissements.append(f"collision d'identifiant : {identite}")
        identites.add(identite)
        aid_arxiv = str(champs.get("arxiv_id") or "").strip()
        fiches[chemin] = {
            "id": make_id("arxiv", aid_arxiv) if aid_arxiv else make_id(identite),
            "arxiv_id": aid_arxiv,
            "champs": champs,
            "corps": texte.split("\n", premiere - 1)[-1] if premiere > 1 else texte,
            "premiere_ligne": premiere,
            "rel": str(chemin.relative_to(RACINE)),
        }
    return fiches


def compter_citations(fiches: dict[Path, dict]) -> tuple[set[str], dict[str, set[str]]]:
    """Rend (URL promotionnelles récurrentes, fiches citantes par URL).

    Deux comptes distincts, et la distinction compte : le nombre d'occurrences
    repère les liens promotionnels récurrents, le nombre de fiches citantes
    décide de l'adoption d'un lien externe.
    """
    frequence: Counter = Counter()
    citants: dict[str, set[str]] = defaultdict(set)
    for f in fiches.values():
        for url, _ in liens_du_texte(f["corps"], f["premiere_ligne"]):
            propre = nettoyer(url)
            if not propre:
                continue
            frequence[propre] += 1
            citants[propre].add(f["id"])
    promo = {u for u, n in frequence.items() if n > SEUIL_REPETITION_PROMO}
    return promo, citants


def declarer_papiers(g: Squelette, fiches: dict[Path, dict]) -> None:
    """Les fiches arXiv d'abord : une citation rencontrée plus tôt ne doit pas
    créer une coquille à la place de la fiche complète."""
    for chemin, f in fiches.items():
        if f["arxiv_id"]:
            c = f["champs"]
            g.noeud(
                f["id"],
                c.get("title") or chemin.stem,
                "paper",
                "papier",
                f["rel"],
                date=c.get("date", ""),
                collected=c.get("collected", ""),
                url=c.get("url", ""),
                complet=c.get("complet", False),
                plateforme="arXiv",
                words=c.get("words", 0),
                categories=c.get("categories", ""),
                arxiv_id=f["arxiv_id"],
                source_corpus=c.get("source", ""),
            )


def declarer_fiche(g: Squelette, chemin: Path, f: dict, avertissements: list[str]) -> str:
    """Le nœud d'une fiche, ses auteurs et sa publication. Rend son identifiant."""
    c, rel, aid = f["champs"], f["rel"], f["id"]
    titre = c.get("title") or chemin.stem
    if titre == "Medium":
        avertissements.append(f"titre non extrait, à corriger : {rel}")
    est_papier = bool(f["arxiv_id"])
    g.noeud(
        aid,
        titre,
        "paper" if est_papier else "document",
        "papier" if est_papier else "article",
        rel,
        date=c.get("date", ""),
        collected=c.get("collected", ""),
        url=c.get("url", ""),
        complet=c.get("complet", True),
        plateforme=c.get("plateforme", ""),
        audience=c.get("audience", ""),
        words=c.get("words", 0),
        edition=c.get("edition"),
        source_corpus=c.get("source", ""),
        categories=c.get("categories", ""),
        arxiv_id=f["arxiv_id"],
        # Texte de référence : famille, version, statut (en vigueur, remplacée), fiche remplaçante, partie —
        # posés dans l'en-tête par diachronie.annoter_fiches ; absents d'une fiche ordinaire (09.10.2026).
        **{
            k: c[k]
            for k in (
                "texte",
                "version",
                "statut",
                "remplacee_par",
                "partie",
                # le fil (newskit.fil, 03.11.2026) : titre, résumé et complément en français
                "titre_fr",
                "resume_fr",
                "complement_fr",
            )
            if c.get(k)
        },
    )
    g.stats["papier_en_fiche" if est_papier else "article"] += 1

    auteurs = normaliser_auteurs(c.get("author", ""))
    if not auteurs and "sources/" in rel:
        avertissements.append(f"auteur absent : {rel}")
    for nom in auteurs:
        nid = g.noeud(make_id("auteur", nom), nom, "concept", "auteur", rel)
        g.arete(aid, nid, "ecrit_par", rel, 1)
    # Pas d'arête `co_auteur` : elle se dérive de deux `ecrit_par` partageant
    # leur cible, et la matérialiser coûte le carré du nombre d'auteurs — un
    # papier à cent signataires en produirait 4 950. Principe 11 de
    # ARCHITECTURE.md : la dérivation reste désactivée par défaut.

    pub = (c.get("publication") or "").strip()
    if pub:
        nid = g.noeud(
            make_id("publication", pub), pub, "concept", "publication", rel, plateforme=c.get("plateforme", "")
        )
        g.arete(aid, nid, "publie_dans", rel, 1)
    return aid


def relier_liens(
    g: Squelette, f: dict, aid: str, index: dict[str, str], promo: set[str], citants: dict[str, set[str]]
) -> None:
    """Une arête par lien sortant, selon ce que la cible est.

    L'ordre des essais est une décision, pas un hasard. L'appariement interne
    passe avant tout filtre de fréquence : une fiche très citée par le corpus
    est un nœud central, pas du bruit promotionnel — inverser cet ordre coûtait
    599 arêtes. Le filtre de fréquence et la règle des deux documents ne
    s'appliquent qu'aux ressources externes.
    """
    rel = f["rel"]
    vus: set[tuple[str, str]] = set()

    def lier(nid: str, relation: str, ligne) -> None:
        if (aid, nid) not in vus:
            vus.add((aid, nid))
            g.arete(aid, nid, relation, rel, ligne)

    for url_brute, ligne in liens_du_texte(f["corps"], f["premiere_ligne"]):
        url = nettoyer(url_brute)
        g.stats["liens_vus"] += 1
        if not url or est_exclu(url):
            g.stats["liens_exclus"] += 1
            continue

        cible = index.get(slug_article(url) or "") or index.get(url.lower())
        if cible:
            if cible != aid:
                lier(cible, "cite_article", ligne)
            continue

        ida = canon_arxiv(url)
        if ida:
            lier(
                g.noeud(
                    make_id("arxiv", ida), f"arXiv:{ida}", "paper", "papier", rel, url=f"https://arxiv.org/abs/{ida}"
                ),
                "cite_papier",
                ligne,
            )
            continue

        depot = canon_github(url)
        if depot:
            lier(
                g.noeud(make_id("github", depot), depot, "code", "depot", rel, url=f"https://github.com/{depot}"),
                "cite_depot",
                ligne,
            )
            continue

        if url in promo:
            g.stats["liens_exclus"] += 1
            continue
        # Règle des deux documents, empruntée au registre de décision d'Utopia :
        # une référence n'est adoptée que si deux fiches distinctes la citent.
        # Un lien vu une seule fois reste dans le texte, il n'entre pas dans le
        # graphe — cela écarte le bruit sans qu'aucun modèle ait à juger.
        if len(citants[url]) >= 2:
            lier(g.noeud(make_id("externe", url), url, "document", "externe", rel, url=url), "cite_externe", ligne)
        else:
            g.stats["liens_externes_uniques_ecartes"] += 1


def ajouter_who_is_who(g: Squelette) -> None:
    """Les triplets du who-is-who suisse, avec leur certitude traduite sans promotion."""
    wiw = RACINE / "recherche" / "who_is_who_data_science_suisse.md"
    if not wiw.exists():
        return
    rel = str(wiw.relative_to(RACINE))
    for sujet, relation, objet, confiance, ligne in triplets_who_is_who(wiw, make_id(wiw.stem)):
        a = g.noeud(make_id("entite", sujet), sujet, "concept", "entite", rel)
        b = g.noeud(make_id("entite", objet), objet, "concept", "entite", rel)
        g.arete(a, b, normalize_id(relation) or "lie_a", rel, ligne, confiance)
        g.stats["triplet_who_is_who"] += 1


def construire(graphe: str | None = None) -> tuple[dict, dict, list[str]]:
    """Retourne (extraction, statistiques, avertissements).

    Sans `graphe`, le corpus historique (sources/ et recherche/) : c'est le
    graphe principal, et son empreinte doit rester celle des audits. Avec un
    nom, seuls les dossiers que le registre attribue à ce graphe sont lus, et
    les graines exogènes (who-is-who) n'entrent que dans le principal.
    """
    from newskit import config_sources as cfg  # noqa: PLC0415

    avertissements: list[str] = []
    graphe = graphe or cfg.GRAPHE_PRINCIPAL
    # Le registre attribue chaque dossier à un graphe ; le principal ne lit
    # donc plus tout `sources/` mais ses dossiers, plus arXiv et recherche/.
    # Contrôlé le 27.09.2026 : aucune fiche hors de tout graphe, aucune dans deux.
    dossiers = cfg.dossiers_du_graphe(graphe)
    from newskit import partage_graphe  # noqa: PLC0415

    retenues = partage_graphe.fiches_du_graphe(graphe)
    fiches = charger_corpus(avertissements, dossiers, list(retenues.values()) if retenues is not None else None)
    index = construire_index(fiches)
    promo, citants = compter_citations(fiches)

    g = Squelette()
    g.stats["url_promo_recurrentes"] = len(promo)
    declarer_papiers(g, fiches)
    for chemin, f in fiches.items():
        aid = declarer_fiche(g, chemin, f, avertissements)
        relier_liens(g, f, aid, index, promo, citants)
    if graphe == cfg.GRAPHE_PRINCIPAL:
        ajouter_who_is_who(g)

    g.stats.update({f"noeuds_{k}": v for k, v in Counter(n["kind"] for n in g.noeuds.values()).items()})
    g.stats.update({f"confiance_{k}": v for k, v in Counter(e["confidence"] for e in g.aretes).items()})
    g.stats["noeuds_total"] = len(g.noeuds)
    g.stats["aretes_total"] = len(g.aretes)
    return g.extraction(), dict(g.stats), avertissements


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", action="store_true", help="relevé chiffré, sans écriture")
    ap.add_argument("--valider", action="store_true", help="valide via graphify si disponible")
    ap.add_argument("--graphe", help="nom d'un graphe du registre (défaut : le principal)")
    args = ap.parse_args()

    from newskit import config_sources as cfg  # noqa: PLC0415

    sortie = cfg.chemins_du_graphe(args.graphe or cfg.GRAPHE_PRINCIPAL)["squelette"]
    extraction, stats, avertissements = construire(args.graphe)

    largeur = max(len(k) for k in stats)
    for cle in sorted(stats):
        print(f"  {cle:<{largeur}}  {stats[cle]:>7}")

    if avertissements:
        print(f"\n  {len(avertissements)} avertissement(s) :")
        for a in avertissements[:15]:
            print(f"    - {a}")
        if len(avertissements) > 15:
            print(f"    … et {len(avertissements) - 15} de plus")

    if args.valider:
        try:
            from graphify.validate import validate_extraction
        except ImportError:
            print("\n  graphify introuvable ; validation ignorée.", file=sys.stderr)
            return 1
        erreurs = validate_extraction(extraction)
        print(f"\n  validation graphify : {len(erreurs)} erreur(s)")
        for e in erreurs[:10]:
            print(f"    - {e}")
        if erreurs:
            return 1

    if not args.stats:
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(json.dumps(extraction, ensure_ascii=False, indent=1), encoding="utf-8")
        gz = archiver_gz(sortie)
        print(f"\n  écrit : {sortie.relative_to(RACINE)}  (+ {gz.name}, {gz.stat().st_size / 1e6:.1f} Mo, versionné)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

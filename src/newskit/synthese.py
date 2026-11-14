#!/usr/bin/env python3
"""Pyramide de synthèses : répondre au niveau le plus haut qui suffit, descendre jusqu'à la ligne si besoin.

Les mesures du 05.10.2026 (CHANTIER_COUT.md) l'ont montré : ce qui coûte, avec le graphe, ce sont les allers-retours
et les sorties brutes — un agent tâtonne de `chercher` en `expliquer` puis en `passages`, et tout ce qu'il lit reste
dans sa conversation. Ici, des synthèses écrites une fois, hors ligne, à plusieurs profondeurs :

    1  le thème (une communauté de notions)   200 à 400 mots   cite ses notions
    2  la notion                              2 à 6 phrases     cite des paragraphes de fiches, à la ligne
    3  le paragraphe                          (descendre)       la preuve, dans la fiche

Le modèle rédige ; il ne décide pas de ce qui est vrai. Chaque phrase cite ses preuves, et un contrôle MÉCANIQUE
retire toute phrase dont un chiffre, une date, une version ou un nom propre ne se retrouve pas dans ce qu'elle cite :
c'est ce qui garde « zéro invention » à chaque niveau.

    python3 -m newskit.synthese construire --graphe <graphe> --notions 150
    python3 -m newskit.synthese repondre --graphe <graphe> "comment MuQ apprend-il ses représentations ?"
    python3 -m newskit.synthese descendre --graphe <graphe> R3        # ou : "<fiche> <ligne>"
    python3 -m newskit.synthese etat --graphe <graphe>

Sortie : graphe/<nom>/synthese/{notions,themes}.json. Pilote du 05.10.2026, sur un graphe audio.
"""

from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média
from newskit import couts  # noqa: E402
from newskit import diachronie  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

MODELE = media.modele("redaction")  # rédiger d'après des preuves ne demande pas le plus gros modèle
LOT = 6  # notions par appel
PARALLELE = 4  # lots rédigés en même temps (06.10.2026)
ECRITURE_S = 60  # écriture au fil de l'eau des synthèses rédigées, au plus toutes les 60 s (08.10.2026)
PREUVES_PAR_NOTION = 8  # preuves de commentaires (actualité et voisinage de la notion), les plus récentes d'abord
PREUVES_DE_REFERENCE = 2  # en plus : paragraphes de textes de référence, au plus
PREUVES_DE_REFERENCE_EVOLUE = 4  # en plus, quand la notion évolue entre deux versions : les deux versions côte à côte
LONGUEUR_PREUVE = 700
# Principe (porteur, 10.10.2026) : la matière diachronique — textes de référence, versions — est un « en plus », important
# mais secondaire ; elle ne remplace jamais l'actualité ni le voisinage de la notion. Avant : l'ordre des voisins du
# graphe, que chaque fusion d'alias rebat, et pour une notion qui évolue les textes d'abord puis les commentaires du
# plus ancien au plus récent. La nuit des 110 alias (10.10.2026) a réécrit 204 synthèses et en a appauvri 69 : GPAI
# 6 → 3 phrases, nourrie des seules newsletters de 2022-2023 ; détenteur de données 6 → 1, des seuls articles du Data Act.
DEGRE_MIN = 4

_PHRASE_NOTION = {
    "type": "object",
    "required": ["texte", "preuves"],
    "properties": {"texte": {"type": "string"}, "preuves": {"type": "array", "items": {"type": "string"}}},
}
SCHEMA_NOTIONS = {
    "type": "object",
    "required": ["notions"],
    "properties": {
        "notions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "phrases"],
                "properties": {"id": {"type": "string"}, "phrases": {"type": "array", "items": _PHRASE_NOTION}},
            },
        }
    },
}
_PHRASE_THEME = {
    "type": "object",
    "required": ["texte", "notions"],
    "properties": {"texte": {"type": "string"}, "notions": {"type": "array", "items": {"type": "string"}}},
}
SCHEMA_THEME = {
    "type": "object",
    "required": ["titre", "phrases"],
    "properties": {"titre": {"type": "string"}, "phrases": {"type": "array", "items": _PHRASE_THEME}},
}

VIDES = ig.VIDES_PASSAGE | {
    "est",
    "sont",
    "une",
    "un",
    "le",
    "la",
    "les",
    "de",
    "du",
    "des",
    "et",
    "en",
    "au",
    "aux",
    "il",
    "elle",
    "ils",
    "on",
    "ce",
    "cette",
    "ces",
    "son",
    "sa",
    "ses",
    "leur",
    "leurs",
    "comment",
    "pourquoi",
    "quoi",
    "quand",
    "combien",
    "a",
    "à",
    "of",
    "in",
    "to",
    "is",
    "an",
    "the",
    "by",
    "as",
    "are",
    "it",
    "its",
    "or",
    "ou",
    "ne",
    "pas",
    "plus",
    "lors",
    "selon",
}


def dossier(graphe: str) -> Path:
    d = cfg.chemins_du_graphe(graphe)["base"] / "synthese"
    d.mkdir(parents=True, exist_ok=True)
    return d


def charger_graphe(graphe: str):
    return ig.charger(cfg.chemins_du_graphe(graphe)["graphe"])


def mots(texte: str) -> set[str]:
    return {m for m in re.findall(r"[\w.+-]+", ig._plier(texte)) if len(m) >= 2 and m not in VIDES}


# --- Le contrôle mécanique : ce que la phrase avance doit être dans ce qu'elle cite --------------------------

CONTROLE = "2026-10-11"  # version du contrôle : changée, les notions dont toutes les phrases avaient été retirées sont retentées une fois

# Le rédacteur écrit en français d'après des preuves le plus souvent anglaises (ou allemandes, norvégiennes…) et traduit
# les institutions, les sigles et les pays ; le contrôle, qui cherche le mot plié dans la preuve, ne les reconnaissait
# pas. Mesuré le 11.10.2026 sur 30 notions (Opus) : 57 phrases retirées sur 67, 85 faits « absents » dont 8 PME/SME,
# 7 État/State, 7 RGPD/GDPR, 6 Conseil/Council, 6 IA/AI, 5 UE/EU… — des traductions, pas des inventions. Un fait plié
# est accepté s'il est dans la preuve sous l'une de ses formes ; les listes sont des sous-chaînes pliées.
EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "ia": ("ai", "ki"),
    "ue": ("eu",),
    "rgpd": ("gdpr", "dsgvo"),
    "pme": ("sme", "kmu"),
    "etat": ("state", "staat"),
    "etats": ("states", "staaten"),
    "etats-unis": ("united states", "u.s.", "us "),
    "royaume-uni": ("united kingdom", "uk"),
    "conseil": ("council", "rat"),
    "comite": ("committee", "board", "ausschuss"),
    "parlement": ("parliament",),
    "commission": ("commission", "kommission"),
    "assemblee": ("assembly",),
    "ministres": ("ministers",),
    "tribunal": ("court", "gericht"),
    "cour": ("court", "gerichtshof"),
    "cjue": ("cjeu", "court of justice", "ecj", "eugh"),
    "cepd": ("edpb", "european data protection board"),
    "pfpdt": ("fdpic", "edob", "edöb", "commissioner"),
    "prepose": ("commissioner", "beauftragte"),
    "nlpd": ("fadp", "revdsg", "ndsg", "dsg", "dpa "),
    "lpd": ("fadp", "dsg"),
    "suisse": ("switzerland", "swiss", "schweiz"),
    "suisses": ("swiss",),
    "confederation": ("confederation", "bund"),
    "federal": ("federal", "bundes"),
    "federale": ("federal", "bundes"),
    "federaux": ("federal",),
    "allemagne": ("germany", "deutschland"),
    "italie": ("italy", "italia"),
    "espagne": ("spain", "espana"),
    "autriche": ("austria",),
    "pays-bas": ("netherlands",),
    "belgique": ("belgium",),
    "norvege": ("norway", "norge"),
    "suede": ("sweden",),
    "danemark": ("denmark",),
    "irlande": ("ireland",),
    "pologne": ("poland",),
    "grece": ("greece",),
    "chine": ("china", "chinese"),
    "japon": ("japan", "japanese"),
    "coree": ("korea",),
    "inde": ("india",),
    "bruxelles": ("brussels",),
    "londres": ("london",),
    "geneve": ("geneva", "genf"),
    "berne": ("bern",),
    "munich": ("munchen", "munich"),
    "eee": ("eea", "ewr", "eøs", "eos"),
    "ocde": ("oecd",),
    "onu": ("un ", "united nations"),
    "oms": ("who",),
    "oit": ("ilo",),
    "partie": ("party",),
    "parties": ("parties",),
    "convention-cadre": ("framework convention",),
    "reglement": ("regulation", "verordnung"),
    "loi": ("act", "law", "gesetz"),
    "autorite": ("authority", "behorde"),
    "rh": ("hr", "human resources"),
    "dpo": ("dpo", "data protection officer"),
    "aipd": ("dpia",),
    "dpia": ("dpia", "aipd"),
    "office": ("office", "amt"),
    "bureau": ("office", "bureau"),
    "agence": ("agency", "agentur"),
}
DEBUTS = {
    "elle",
    "elles",
    "il",
    "ils",
    "on",
    "nous",
    "vous",
    "ce",
    "cet",
    "cette",
    "ces",
    "cela",
    "celle",
    "celui",
    "ceux",
    "lorsqu",
    "lorsque",
    "quand",
    "selon",
    "dans",
    "pour",
    "par",
    "sur",
    "sous",
    "sans",
    "avec",
    "depuis",
    "apres",
    "avant",
    "ainsi",
    "aussi",
    "mais",
    "puis",
    "enfin",
    "aucun",
    "aucune",
    "chaque",
    "tout",
    "toute",
    "tous",
    "toutes",
    "plusieurs",
    "certains",
    "certaines",
    "autre",
    "autres",
    "deux",
    "trois",
    "quatre",
    "premier",
    "premiere",
    "second",
    "seconde",
    "dernier",
    "derniere",
    "leur",
    "leurs",
    "son",
    "sa",
    "ses",
    "notre",
    "votre",
    "une",
    "les",
    "des",
    "faute",
    "contrairement",
    "parallelement",
    "comme",
    "voir",
    "exemple",
    "cependant",
    "toutefois",
    "neanmoins",
    "ensuite",
    "enfin",
    "elle-meme",
    "lui-meme",
    "seul",
    "seule",
    "seuls",
    "seules",
    "au-dela",
    "peut-etre",
    "en-deca",
    "c-est-a-dire",
}
_ORDINAL = re.compile(r"^(\d+)(?:er|ere|re|e|eme|st|nd|rd|th)$")


def faits(phrase: str) -> set[str]:
    """Les éléments vérifiables d'une phrase : nombres, versions, dates, et mots porteurs d'une majuscule ou d'un
    chiffre (noms propres, sigles, noms de modèles). Les mots communs ne se vérifient pas ainsi."""
    out = set()
    for m in re.findall(r"\d+(?:[.,]\d+)*", phrase):
        out.add(m.replace(",", "."))
    for m in re.findall(r"[\w][\w.+-]*", phrase):
        if (
            any(c.isupper() for c in m[1:]) or any(c.isdigit() for c in m) or (m[0].isupper() and len(m) >= 4)
        ) and not m.isdigit():
            out.add(ig._plier(m.strip(".-")))
    # La première lettre d'une phrase est majuscule sans faire un nom propre : au début (même après un guillemet) ; et
    # après un point, quand le rédacteur a mis deux phrases en une, seulement pour un mot courant (DEBUTS) — « Candice
    # Odgers » en tête de seconde phrase reste un fait à vérifier (11.10.2026).
    premier = re.match(r"[«\"'(\s]*([A-ZÀ-Ý][a-zà-ÿ]+(?:-[a-zà-ÿ]+)*)", phrase)
    if premier:
        out.discard(ig._plier(premier.group(1)))
    for m in re.finditer(r"[.!?;:]\s+[«\"'(]*([A-ZÀ-Ý][a-zà-ÿ]+(?:-[a-zà-ÿ]+)*)", phrase):
        if ig._plier(m.group(1)) in DEBUTS:
            out.discard(ig._plier(m.group(1)))
    return {f for f in out if f}


def fait_present(f: str, s: str) -> bool:
    """Le fait plié `f` se lit-il dans les sources pliées `s` : tel quel, au singulier, sous son ordinal (20e → 20),
    ou par un équivalent d'une autre langue (EQUIVALENTS) ?"""
    if f in s:
        return True
    if len(f) >= 4 and f.endswith("s") and (f[:-1] in s or any(eq in s for eq in EQUIVALENTS.get(f[:-1], ()))):
        return True
    o = _ORDINAL.match(f)
    if o and re.search(r"(?<![\d.])" + o.group(1) + r"(?![\d])", s):
        return True
    for eq in EQUIVALENTS.get(f, ()):
        if eq in s:
            return True
    return False


# Des mots anglais ordinaires, jamais des noms : une phrase qui en garde un hors citation ne se lit pas sans l'original
# (Palantir, 14.11.2026 : « Un listing d'octobre 2026 décrit Palantir comme le maker de Maven […] »).
ANGLAIS = re.compile(
    r"(?<![-\w])(listing|maker|makers|reportedly|allegedly|according|however|whereas|meanwhile|announced|said|says|"
    r"released|launched)(?![-\w])"
)
CITATIONS = re.compile(r"«[^»]*»|\"[^\"]*\"|“[^”]*”")


def illisible(phrase: str) -> list[str]:
    """Les mots anglais ordinaires de la phrase, hors citation (vide : la phrase se lit)."""
    return [f"anglais : {m}" for m in dict.fromkeys(ANGLAIS.findall(CITATIONS.sub(" ", phrase)))]


def verifier(phrase: str, sources: str) -> list[str]:
    """Les faits de la phrase introuvables dans `sources` (vide : la phrase tient)."""
    s = ig._plier(sources).replace(",", ".")
    return sorted(f for f in faits(phrase) if not fait_present(f, s))


# --- Niveau 2 : les notions --------------------------------------------------------------------------------

_MOINDRE: list = []


def moindre(fiche: str) -> bool:
    """La fiche vient-elle d'une source de moindre autorité (autorite: moindre au registre) ?"""
    if not _MOINDRE:
        _MOINDRE.append(tuple(d.rstrip("/") + "/" for d in cfg.dossiers_de_moindre_autorite()))
    return fiche.startswith(_MOINDRE[0]) if _MOINDRE[0] else False


_INDEX_FICHES: dict = {}


def _fiche_de(G, f: str) -> dict:
    """Les attributs du nœud de la fiche `f` (date, statut, texte, version), indexés une fois par graphe chargé."""
    cle = id(G)
    if cle not in _INDEX_FICHES:
        _INDEX_FICHES.clear()
        _INDEX_FICHES[cle] = diachronie.index_fiches(G)
    return _INDEX_FICHES[cle].get(f) or {}


def annoter_preuve(G, p: dict) -> dict:
    """Date et statut d'une preuve, lus du nœud de sa fiche : `quand` (AAAA-MM-JJ) et, pour un texte de référence,
    `statut` (en vigueur, remplacée), `texte`, `version`. Hors empreinte : une synthèse n'est pas réécrite pour ça."""
    d = _fiche_de(G, p["fiche"])
    if d.get("date"):
        p["quand"] = str(d["date"])
    if d.get("statut"):
        p["statut"], p["texte_ref"], p["version"] = (
            str(d["statut"]),
            str(d.get("texte") or ""),
            str(d.get("version") or ""),
        )
    return p


def empreinte_de(G, n, pr: list[dict]) -> str:
    """L'empreinte des preuves : ce qui, changé, fait réécrire la synthèse. Les annotations de date et de statut n'y
    entrent pas (sinon toute la pyramide se réécrirait, 3 889 notions le 09.10.2026) ; le verdict diachronique de
    la notion (évolue entre deux versions, ou n'est plus attestée que par une version remplacée) y entre, pour que
    sa synthèse soit réécrite une fois avec la chronologie."""
    nu = sorted(
        ({k: v for k, v in p.items() if k in ("fiche", "ligne", "texte", "moindre", "pourquoi")} for p in pr),
        key=lambda p: (p["fiche"], p["ligne"], bool(p.get("pourquoi"))),
    )  # l'ordre des preuves n'y entre pas (10.10.2026)
    a = G.nodes[n].get("actualite") or {}
    marque = "|evolue" if a.get("evolue") else ("|remplacee" if a.get("remplacee_seulement") else "")
    return hashlib.sha1((json.dumps(nu, ensure_ascii=False) + marque).encode()).hexdigest()[:16]


def _preuves_de_reference(G, graphe: str | None, n: str, f: str) -> list[dict]:
    """Les preuves d'un article de texte de référence qui atteste la notion sans la nommer mot pour mot (« AI Act
    implementation timeline » n'est pas dans l'article 113) : ce que l'extraction de cet article a dit de la notion
    (le `rationale` de son lot, avec ses dates), puis les deux premiers paragraphes de l'article (09.10.2026)."""
    out: list[dict] = []
    d = _fiche_de(G, f)
    if graphe and d.get("_id"):
        for g in [graphe, *cfg.graphes_inclus(graphe)]:
            lot = cfg.chemins_du_graphe(g)["couche2"] / "fiches" / f"{d['_id']}.json"
            if lot.exists():
                try:
                    noeud = next(
                        (x for x in json.loads(lot.read_text(encoding="utf-8")).get("nodes", []) if x.get("id") == n),
                        None,
                    )
                except Exception:  # noqa: BLE001
                    noeud = None
                if noeud and (noeud.get("rationale") or "").strip():
                    out.append(
                        {
                            "fiche": f,
                            "ligne": 0,
                            "texte": str(noeud["rationale"]).strip()[:LONGUEUR_PREUVE],
                            "pourquoi": True,
                        }
                    )
                break
    corps = [(l, t) for l, t in ig._paragraphes(RACINE / f) if not t.lstrip().startswith(("#", "*"))]
    for l, t in corps[:2]:
        out.append({"fiche": f, "ligne": l, "texte": t[:LONGUEUR_PREUVE]})
    return out


def preuves_de(G, n, graphe: str | None = None) -> list[dict]:
    """Les paragraphes des fiches qui attestent la notion et la nomment, avec leur ligne ; et sa ligne « pourquoi ».
    Chaque preuve est datée (`quand`) et, pour un texte de référence, porte son statut. Pour une notion qui évolue
    entre deux versions d'un texte, les fiches de référence viennent d'abord, par partie puis par version, pour que
    la synthèse puisse décrire le changement (diachronie, 09.10.2026)."""
    label = ig._label(G, n)
    # Les libellés fondus dans ce nœud (alias de langue ou de sigle, `aussi`) attestent la notion autant que le
    # canonique : « Minimisation des données » dans une fiche française prouve « Data minimization » (09.10.2026).
    formes = [label, *(G.nodes[n].get("aussi") or [])]
    motif = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(ig._plier(f)) for f in formes if f) + r")(?![\w])")
    fiches: list[str] = []
    for v in G.neighbors(n):
        f = str((G.get_edge_data(n, v) or {}).get("source_file") or "")
        if f and f not in fiches:
            fiches.append(f)
    f0 = str(G.nodes[n].get("source_file") or "")
    if f0 and f0 not in fiches:
        fiches.insert(0, f0)
    evolue = bool((G.nodes[n].get("actualite") or {}).get("evolue"))
    est_ref = {f: bool(_fiche_de(G, f).get("statut")) for f in fiches}
    # Les commentaires d'abord, par centralité puis par récence (porteur, 10.10.2026 : la récence seule donnait à
    # « minimisation des données » dix mentions en passant dans des comptes rendus d'événements, et une synthèse d'une
    # phrase) : un paragraphe qui nomme la notion plusieurs fois et la développe passe avant un paragraphe qui la cite
    # une fois ; à centralité égale, le plus récent d'abord ; deux paragraphes par fiche au plus, PREUVES_PAR_NOTION en
    # tout. Puis, en plus, les textes de référence par partie et par version (les deux versions côte à côte quand la
    # notion évolue), jusqu'à leur plafond — et jusqu'à PREUVES_PAR_NOTION quand les commentaires manquent (« détenteur
    # de données » n'est nommé que par le Data Act).
    refs = sorted(
        (f for f in fiches if est_ref[f]),
        key=lambda f: (
            (_fiche_de(G, f).get("texte") or ""),
            (_fiche_de(G, f).get("partie") or ""),
            (_fiche_de(G, f).get("version") or ""),
        ),
    )
    plafond_ref = PREUVES_DE_REFERENCE_EVOLUE if evolue else PREUVES_DE_REFERENCE
    candidats = []
    for f in fiches:
        if est_ref[f] or not (RACINE / f).exists():
            continue
        date = str(_fiche_de(G, f).get("date") or "")
        for ligne, texte in ig._paragraphes(RACINE / f):
            if texte.lstrip().startswith("#"):
                continue  # un titre de page n'est pas une preuve (10.10.2026)
            occurrences = len(motif.findall(ig._plier(texte)))
            if occurrences:
                candidats.append((min(occurrences, 3), min(len(texte), 1200) // 400, date, f, ligne, texte))
    candidats.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    out: list[dict] = []
    par_fiche: dict[str, int] = {}
    for _, _, _, f, ligne, texte in candidats:
        if par_fiche.get(f, 0) >= 2:
            continue
        par_fiche[f] = par_fiche.get(f, 0) + 1
        # « moindre » seulement quand il est vrai : l'empreinte des autres preuves ne change pas, et
        # seules les synthèses qui citent une source de moindre autorité sont réécrites.
        out.append(
            {"fiche": f, "ligne": ligne, "texte": texte[:LONGUEUR_PREUVE]} | ({"moindre": True} if moindre(f) else {})
        )
        if len(out) >= PREUVES_PAR_NOTION:
            break
    en_plus: list[dict] = []
    plafond_ref = max(plafond_ref, PREUVES_PAR_NOTION - len(out))
    for f in refs:
        if not (RACINE / f).exists():
            continue
        pr = []
        for ligne, texte in ig._paragraphes(RACINE / f):
            if motif.search(ig._plier(texte)):
                pr.append({"fiche": f, "ligne": ligne, "texte": texte[:LONGUEUR_PREUVE]})
                if len(pr) >= 2:
                    break
        if not pr and evolue:
            # Un article de référence qui atteste la notion sans la nommer : son extraction et son ouverture font preuve.
            pr = _preuves_de_reference(G, graphe, n, f)
        en_plus.extend(pr)
        if len(en_plus) >= plafond_ref:
            break
    out.extend(en_plus[:plafond_ref])
    pourquoi = str(G.nodes[n].get("rationale") or "").strip()
    if pourquoi and f0:
        out.append({"fiche": f0, "ligne": 0, "texte": pourquoi[:LONGUEUR_PREUVE], "pourquoi": True})
    return [annoter_preuve(G, p) for p in out]


_NOMS_TEXTES: dict[str, str] = {}


def nom_du_texte(famille: str) -> str:
    """Le nom déclaré d'un texte de référence (collecte_textes.TEXTES) d'après sa famille : « Council of Europe Framework
    Convention … (CETS No. 225) » pour « coe-ai-convention ». L'étiquette montrait la clé courte, et le rédacteur
    développait le nom de mémoire (« Cyber Resilience Act » pour cra-2024-2847), que le contrôle retirait (11.10.2026)."""
    if not _NOMS_TEXTES:
        from newskit import collecte_textes as ct  # noqa: PLC0415

        for lst in ct.TEXTES.values():
            for t in lst:
                fam = t.get("famille") or t.get("court")
                if fam and (fam not in _NOMS_TEXTES or not t.get("famille")):  # la version en vigueur nomme la famille
                    _NOMS_TEXTES[fam] = str(t.get("nom") or fam)
    return _NOMS_TEXTES.get(famille, famille)


def etiquette_preuve(p: dict) -> str:
    """Ce que le rédacteur sait d'une preuve avant de la lire : sa date et, pour un texte de référence, son statut et
    le nom du texte."""
    if not p.get("quand"):
        return ""
    nom = nom_du_texte(p["texte_ref"]) if p.get("texte_ref") else ""
    if p.get("statut") == diachronie.EN_VIGUEUR:
        return f"({p['quand']}, texte en vigueur{' : ' + nom if nom else ''}) "
    if p.get("statut") == diachronie.REMPLACEE:
        return f"({p['quand']}, version REMPLACÉE{' de ' + nom if nom else ''}, plus en vigueur) "
    return f"({p['quand']}) "


def matiere_de(G, n, preuves: list[dict]) -> str:
    """Ce que le contrôle lit pour les preuves citées : leur texte, leurs dates sous toutes les formes, leur étiquette
    (statut et nom du texte de référence, que le rédacteur voit), et le libellé de la notion avec ses alias. Le
    11.10.2026, « Article 64 GDPR » était retiré pour « 64 » et « RGPD », « Cyber Resilience Act » pour le nom du
    texte lu dans l'étiquette : le contrôle ne doit pas ignorer ce que la consigne montre."""
    formes = [ig._label(G, n), *(G.nodes[n].get("aussi") or [])] if n in G else [str(n)]
    return " ".join([*(p["texte"] + dates_de(p) + " " + etiquette_preuve(p) for p in preuves), *formes])


def dates_de(p: dict) -> str:
    """La date d'une preuve sous les formes qu'une phrase peut reprendre (AAAA-MM-JJ, JJ.MM.AAAA, année), pour que le
    contrôle des faits accepte « depuis le 27.07.2026 » quand la preuve est datée ainsi."""
    q = p.get("quand") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", q):
        return ""
    return f" {q} {q[8:]}.{q[5:7]}.{q[:4]} {q[:4]}"


def choisir_notions(G, combien: int) -> list[str]:
    cands = [
        n for n in G if dg._type(G, n) in dg.TYPES_CONCEPT and not ig.est_auteur(G, n) and G.degree(n) >= DEGRE_MIN
    ]
    return sorted(cands, key=lambda n: -G.degree(n))[:combien]


def nombre_de_fiches(G, n) -> int:
    return len({str((G.get_edge_data(n, v) or {}).get("source_file") or "") for v in G.neighbors(n)} - {""})


def notions_suivantes(G, deja: set[str], combien: int) -> list[str]:
    """Les notions à résumer ensuite : d'abord celles que plusieurs fiches attestent (une synthèse y croise des
    sources, au lieu de reformuler une fiche), puis les plus reliées."""
    cands = [
        n
        for n in G
        if dg._type(G, n) in dg.TYPES_CONCEPT and not ig.est_auteur(G, n) and n not in deja and G.degree(n) >= 2
    ]
    return sorted(cands, key=lambda n: (-min(nombre_de_fiches(G, n), 4), -G.degree(n)))[:combien]


def journal_rejets(graphe: str) -> Path:
    """Le journal des phrases retirées par le contrôle, hors dépôt : une ligne JSON par notion rédigée qui a perdu au
    moins une phrase, avec ses preuves et, pour chaque phrase retirée, les faits introuvables. Sans lui, la nuit du
    10 au 11.10.2026 (705 phrases retirées sur 1 527, 46 %) ne pouvait pas être auditée : `mesurer_rejets.py` le lit."""
    d = RACINE / "cache" / "pyramide_rejets"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{graphe}.jsonl"


def consigne_notions(libelle: str) -> str:
    """La consigne de rédaction des synthèses de notions, sans les preuves."""
    return (
        f"Domaine : « {libelle} ». Pour chaque notion ci-dessous, écris sa synthèse en français : 2 à 6 "
        "phrases courtes et denses — ce que c'est, comment ça marche, ce qui la distingue, et les faits précis "
        "(versions, chiffres, dates, qui affirme quoi). N'emploie QUE ses preuves [E…] : rien de ta mémoire. "
        "Chaque phrase liste les preuves qui la fondent ; recopie à l'identique les noms, chiffres et versions : les noms "
        "d'institutions, de textes, de pays et les sigles s'écrivent comme dans la preuve (SME, FDPIC, Council of "
        "Europe, GDPR), sans les traduire. "
        "Moins de phrases plutôt qu'une phrase incertaine. Une preuve marquée (moindre autorité) vient d'une "
        "source au ton promotionnel : n'en reprends ni les superlatifs ni les généralisations ; un fait qu'elle "
        "seule porte s'écrit comme rapporté par elle (« selon … »), jamais comme acquis. "
        "Chaque preuve est datée entre parenthèses ; un texte de référence y porte son statut. La synthèse porte "
        "d'abord sur l'état actuel de la notion, ses faits récents et son voisinage. Quand les preuves s'étalent "
        "dans le temps ET que la notion a changé (texte remplacé, délai reporté, obligation ajoutée ou retirée, "
        "chiffre révisé), ajoute en complément, en une phrase au plus, l'évolution datée en disant lequel des "
        "états est en vigueur ; une version REMPLACÉE se cite au passé (« la version du 12.07.2024 prévoyait… »), "
        "jamais comme le droit actuel. Quand rien n'a changé, n'ajoute pas de chronologie. "
        "Une preuve qui décrit seulement un document (une page de liste, un sommaire, l'annonce d'un épisode ou d'un "
        "événement) ne fonde aucun fait sur la notion : n'en tire pas de phrase. Un mot anglais ordinaire se traduit "
        "(« listing », « maker », « reportedly ») ; seuls les noms, les sigles et les titres restent tels quels. "
        "Chaque phrase se lit sans l'original.\n\n"
    )


def rediger_notions(graphe: str, G, ids: list[str], modele: str, ecrire=None) -> dict:
    """Rédige les synthèses de `ids` par lots parallèles. `ecrire(resultat_partiel, repondues)` est appelé au fil de
    l'eau (toutes les ECRITURE_S secondes) et à la fin : ce qui est rédigé est écrit, rien ne se perd si le processus
    est tué (08.10.2026 : des tenues de 30 à 50 minutes gardaient tout en mémoire jusqu'au dernier lot). En limite
    d'usage (couts.en_limite), les lots restants ne sont plus tentés. Chaque phrase retirée va au journal des rejets."""
    decl = cfg.graphe_declare(graphe)
    lots, entrees = [], {}
    for n in ids:
        pr = preuves_de(G, n, graphe)
        if pr:
            entrees[n] = pr
    resultat, rejets, total = {}, 0, 0
    ordre = list(entrees)

    def prompt_du_lot(lot: list[str]) -> str:
        blocs = []
        for n in lot:
            pr = entrees[n]
            a = G.nodes[n].get("actualite") or {}
            entete = f"### {n} — « {ig._label(G, n)} »"
            if a.get("evolue"):
                entete += " — NOTION QUI ÉVOLUE : deux versions d'un même texte l'attestent, décris le changement dans l'ordre du temps"
            elif a.get("remplacee_seulement"):
                entete += " — attestée seulement par une version remplacée du texte : dis-le, et date-la"
            blocs.append(
                entete
                + "\n"
                + "\n".join(
                    f"[E{i + 1}]{' (moindre autorité)' if p.get('moindre') else ''} {etiquette_preuve(p)}{p['texte']}"
                    for i, p in enumerate(pr)
                )
            )
        return (
            consigne_notions(decl["libelle"])
            + "\n\n".join(blocs)
            + '\n\nRéponds en JSON : {"notions": [{"id": "<id>", "phrases": [{"texte": "…", "preuves": ["E1", …]}]}]}.'
        )

    # Plusieurs lots en parallèle depuis le 06.10.2026 : un appel à la fois, la tenue d'un graphe audio après un
    # rattrapage de 400 fiches a pris 47 minutes. Chaque lot garde la même consigne et le même contrôle des
    # faits ; seuls les appels se chevauchent, et le fil principal assemble.
    lots = [ordre[k : k + LOT] for k in range(0, len(ordre), LOT)]
    faits_lots, repondues, non_tentes = 0, set(), 0
    dernier_ecrit = time.time()

    def rediger_lot(lot: list[str]) -> dict | None:
        if couts.en_limite():
            return {"non_tente": True}
        return appeler(prompt_du_lot(lot), SCHEMA_NOTIONS, modele, graphe)

    with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
        futurs = [pool.submit(rediger_lot, lot) for lot in lots]
        for fut in as_completed(futurs):
            try:
                rep = fut.result()
            except Exception:  # noqa: BLE001
                rep = None
            faits_lots += 1
            if rep and rep.get("non_tente"):
                non_tentes += 1
                continue
            if ecrire and time.time() - dernier_ecrit >= ECRITURE_S:
                ecrire(resultat, repondues)
                dernier_ecrit = time.time()
            for x in (rep or {}).get("notions", []):
                n = x.get("id")
                if n not in entrees or n in resultat:
                    continue
                repondues.add(n)
                pr, gardees, retirees = entrees[n], [], []
                for ph in x.get("phrases", []):
                    refs = [
                        int(e[1:]) - 1
                        for e in ph.get("preuves", [])
                        if re.fullmatch(r"E\d+", e or "") and 0 < int(e[1:]) <= len(pr)
                    ]
                    total += 1
                    texte = " ".join(str(ph.get("texte") or "").split())
                    manque = (
                        verifier(texte, matiere_de(G, n, [pr[i] for i in refs])) + illisible(texte)
                        if refs
                        else ["(aucune preuve)"]
                    )
                    if not texte or manque:
                        rejets += 1
                        retirees.append({"texte": texte, "preuves": [i + 1 for i in refs], "manque": manque})
                        continue
                    gardees.append(
                        {"texte": texte, "refs": [{"fiche": pr[i]["fiche"], "ligne": pr[i]["ligne"]} for i in refs]}
                    )
                if retirees:
                    with journal_rejets(graphe).open("a", encoding="utf-8") as jr:
                        jr.write(
                            json.dumps(
                                {
                                    "quand": time.strftime("%Y-%m-%d %H:%M"),
                                    "modele": modele,
                                    "notion": n,
                                    "label": ig._label(G, n),
                                    "gardees": len(gardees),
                                    "retirees": retirees,
                                    "preuves": [
                                        {
                                            "fiche": p["fiche"],
                                            "ligne": p["ligne"],
                                            "quand": p.get("quand"),
                                            "statut": p.get("statut"),
                                            "texte_ref": p.get("texte_ref"),
                                            "version": p.get("version"),
                                            "pourquoi": bool(p.get("pourquoi")),
                                            "texte": p["texte"],
                                        }
                                        for p in pr
                                    ],
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                if gardees:
                    resultat[n] = {
                        "label": ig._label(G, n),
                        "degre": G.degree(n),
                        "phrases": gardees,
                        "empreinte": empreinte_de(G, n, pr),
                    }
                    if G.nodes[n].get("actualite"):
                        resultat[n]["actualite"] = G.nodes[n]["actualite"]
            print(
                f"  notions {min(faits_lots * LOT, len(ordre))}/{len(ordre)} — gardées {len(resultat)}, phrases retirées {rejets}/{total}",
                flush=True,
            )
    if ecrire:
        ecrire(resultat, repondues)
    if non_tentes:
        print(
            f"  ARRÊT : limite d'usage atteinte — {non_tentes} lot(s) sur {len(lots)} non tenté(s), "
            f"{non_tentes * LOT} notion(s) au plus attendront le passage suivant",
            file=sys.stderr,
            flush=True,
        )
    return {"notions": resultat, "phrases": total, "retirees": rejets, "repondues": repondues, "non_tentes": non_tentes}


# --- Niveau 1 : les thèmes ---------------------------------------------------------------------------------


def rediger_themes(
    graphe: str,
    G,
    notions: dict,
    modele: str,
    combien: int = 25,
    changees: set[str] | None = None,
    anciens: dict | None = None,
    ecrire=None,
) -> dict:
    """Les thèmes, depuis les synthèses de leurs notions. Avec `changees` et `anciens` (mise à jour), un thème dont
    aucune notion n'a changé est repris tel quel ; les autres sont réécrits. `ecrire(out)` : écriture au fil de l'eau
    après chaque thème rédigé. En limite d'usage, les thèmes restants gardent leur ancienne version."""
    decl = cfg.graphe_declare(graphe)
    out, rejets, total = {}, 0, 0
    for etiquette, membres, _ in dg.communautes(G, combien):
        presentes = [n for n in membres if n in notions][:12]
        if len(presentes) < 3:
            continue
        if (
            changees is not None
            and anciens
            and etiquette in anciens
            and set(anciens[etiquette].get("notions", [])) == set(presentes)
            and not (set(presentes) & changees)
        ):
            out[etiquette] = anciens[etiquette]
            continue
        if couts.en_limite():
            continue
        texte_notions = "\n".join(
            f"[{notions[n]['label']}] " + " ".join(p["texte"] for p in notions[n]["phrases"]) for n in presentes
        )
        prompt = (
            f"Domaine : « {decl['libelle']} ». Voici des synthèses de notions qui forment un même thème du graphe. "
            "Écris la synthèse du THÈME en français : un titre court, puis 3 à 6 phrases — ce qui relie ces notions, "
            "les approches en présence, les faits marquants. N'emploie QUE ces synthèses ; chaque phrase liste les "
            "notions [entre crochets] qui la fondent, à l'identique. Les listes que les notions portent (pratiques, "
            "rubriques, conditions, obligations) priment : reprends-les ; une évolution dans le temps (version remplacée, "
            "délai reporté) tient en une phrase au plus, le détail reste dans la notion.\n\n"
            + texte_notions
            + '\n\nRéponds en JSON : {"titre": "…", "phrases": [{"texte": "…", "notions": ["<nom exact>", …]}]}.'
        )
        rep = appeler(prompt, SCHEMA_THEME, modele, graphe)
        if not rep:
            continue
        par_label = {notions[n]["label"]: n for n in presentes}
        gardees = []
        for ph in rep.get("phrases", []):
            total += 1
            refs = [par_label[l] for l in ph.get("notions", []) if l in par_label]
            texte = " ".join(str(ph.get("texte") or "").split())
            sources = " ".join(
                notions[n]["label"] + " " + " ".join(p["texte"] for p in notions[n]["phrases"]) for n in refs
            )
            if not texte or not refs or verifier(texte, sources):
                rejets += 1
                continue
            gardees.append({"texte": texte, "notions": refs})
        if gardees:
            out[etiquette] = {"titre": str(rep.get("titre") or etiquette), "phrases": gardees, "notions": presentes}
            if ecrire:
                ecrire(out)
        print(f"  thèmes : {len(out)} gardés, phrases retirées {rejets}/{total}", flush=True)
    return {"themes": out, "phrases": total, "retirees": rejets}


def notions_eligibles(G) -> list[str]:
    """Les notions qui méritent une synthèse à l'avance : au moins deux fiches, ou au moins 4 liens (05.10.2026)."""
    return [
        n
        for n in G
        if dg._type(G, n) in dg.TYPES_CONCEPT
        and not ig.est_auteur(G, n)
        and (nombre_de_fiches(G, n) >= 2 or G.degree(n) >= DEGRE_MIN)
    ]


def entrelacer_rares(G, notions: dict, neuves: list[str], graphe: str | None = None) -> list[str]:
    """`neuves` (notions éligibles sans synthèse, dans l'ordre fréquent) entrelacées avec le même ensemble trié par
    rareté : d'abord les notions dont une fiche attestante est couverte par le moins de synthèses existantes (0, puis 1),
    à égalité le degré. Une notion apparaît une fois ; la liste rendue a la même longueur.
    Filtre de pertinence, étage 1 (porteur, 10.10.2026) : n'entre dans la file rare qu'une notion attestée par au moins
    une fiche d'un dossier propre au domaine (cfg.dossiers_propres_au_domaine) ; une notion que seules des sources
    héritées d'un autre graphe attestent (« YOLO-CCG », « Tensorlake SDK » venus des flux d'actualité) reste dans la file
    fréquente, où son degré la place."""
    propres = tuple(cfg.dossiers_propres_au_domaine(graphe)) if graphe else ()
    couverture: dict[str, int] = {}
    for n in notions:
        if n in G:
            for v in G.neighbors(n):
                f = str((G.get_edge_data(n, v) or {}).get("source_file") or "")
                if f:
                    couverture[f] = couverture.get(f, 0) + 1

    def rarete(n: str) -> int:
        fiches = {str((G.get_edge_data(n, v) or {}).get("source_file") or "") for v in G.neighbors(n)} - {""}
        return min((couverture.get(f, 0) for f in fiches), default=10**6)

    def du_domaine(n: str) -> bool:
        if not propres:
            return True
        return any(
            str((G.get_edge_data(n, v) or {}).get("source_file") or "").startswith(propres) for v in G.neighbors(n)
        )

    rares = sorted((n for n in neuves if du_domaine(n)), key=lambda n: (rarete(n), -G.degree(n)))
    out, vus = [], set()
    for a, b in zip(rares, neuves):
        for n in (a, b):
            if n not in vus:
                vus.add(n)
                out.append(n)
    return out + [n for n in neuves if n not in vus]


def mettre_a_jour(
    graphe: str, plafond: int, modele: str, sans_themes: bool = False, seulement: str | None = None
) -> int:
    """La tenue quotidienne d'une pyramide existante : réécrire les synthèses dont les preuves ont changé (fiche
    modifiée, nouvelle fiche qui atteste la notion), rédiger les notions devenues éligibles (au plus `plafond`),
    retirer celles qui ont quitté le graphe, puis refaire les seuls thèmes touchés. Rien à faire sans pyramide."""
    p_n, p_t = dossier(graphe) / "notions.json", dossier(graphe) / "themes.json"
    if not p_n.exists():
        print(f"  {graphe} : pas de pyramide, rien à tenir à jour")
        return 0
    t0 = time.time()
    etat = json.loads(p_n.read_text(encoding="utf-8"))
    notions = etat["notions"]
    G = charger_graphe(graphe)
    parties = [n for n in notions if n not in G]
    for n in parties:
        del notions[n]
    perimees = []
    for n, v in notions.items():
        pr = preuves_de(G, n, graphe)
        if not pr or empreinte_de(G, n, pr) != v.get("empreinte"):
            perimees.append(n)
    # Les synthèses élaguées d'abord, la plus vidée en tête (01.11.2026, partage_graphe.elaguer_pyramide) ; les autres
    # gardent l'ordre du fichier (tri stable).
    perimees.sort(
        key=lambda n: (lambda e: e["apres"] / e["avant"] if e and e.get("avant") else 1.0)(notions[n].get("elaguee"))
    )
    # Une notion rédigée dont toutes les phrases ont été retirées revenait chaque nuit en tête de file, payée à chaque
    # fois (11.10.2026 : 6 notions sur 10 sans phrase gardée) ; elle attend que ses preuves ou le contrôle changent.
    echecs = etat.setdefault("echecs", {})
    for n in [n for n in echecs if n not in G]:
        del echecs[n]
    neuves, retenues = [], 0
    for n in notions_eligibles(G):
        if n in notions:
            continue
        e = echecs.get(n)
        if e and e.get("controle") == CONTROLE and e.get("empreinte") == empreinte_de(G, n, preuves_de(G, n, graphe)):
            retenues += 1
            continue
        neuves.append(n)
    neuves.sort(key=lambda n: (-min(nombre_de_fiches(G, n), 4), -G.degree(n)))
    # Les faits rares d'abord, une fois sur deux (porteur, 10.10.2026 : « je tenterais plutôt la file de la pyramide ») :
    # une notion dont une fiche attestante n'est encore couverte par aucune synthèse (ou par une seule) porte un fait que
    # rien d'autre ne sert au lecteur ; la file par nombre de fiches et degré ne l'atteignait qu'après les notions
    # centrales, que la redondance du corpus retrouve de toute façon. Les deux files s'entrelacent jusqu'au plafond.
    neuves = entrelacer_rares(G, notions, neuves, graphe)
    a_rediger = (perimees + neuves)[:plafond] if plafond else perimees + neuves
    if seulement:
        # Rattrapage ciblé par expression sur le libellé (12.10.2026, notions de l'article 5(1)(h) : présentes au graphe
        # avec leurs preuves, pas encore atteintes par la file) : hors plafond, mais jamais hors éligibilité.
        motif = re.compile(seulement, re.I)
        a_rediger = [n for n in perimees + neuves if motif.search(str(G.nodes[n].get("label") or n))]
        print(
            f"  --notions « {seulement} » : {len(a_rediger)} notion(s) éligible(s) retenue(s) — "
            + ", ".join(str(G.nodes[n].get("label"))[:50] for n in a_rediger[:6]),
            flush=True,
        )
        if not a_rediger:
            return 0
    print(
        f"  {graphe} : {len(notions)} synthèses, {len(parties)} retirée(s) (notion sortie du graphe), "
        f"{len(perimees)} à réécrire (preuves changées), {len(neuves)} notion(s) devenue(s) éligible(s)"
        + (f" ({retenues} en échec, preuves et contrôle inchangés, non retentées)" if retenues else "")
        + " ; "
        f"{len(a_rediger)} rédigée(s) ce passage"
        + (f" (plafond {plafond})" if plafond and len(perimees) + len(neuves) > plafond else ""),
        flush=True,
    )
    if couts.en_limite():
        print(
            f"  ARRÊT : limite d'usage atteinte avant de commencer ({couts.marque_en_cours()}) ; rien n'est rédigé",
            file=sys.stderr,
        )
        return 2
    phrases_avant, retirees_avant = etat.get("phrases", 0), etat.get("retirees", 0)

    def ecrire_notions(partiel: dict, repondues: set) -> None:
        # Une notion réécrite qui ne tient plus le contrôle disparaît ; une notion dont l'appel a échoué garde son
        # ancienne synthèse. Le 06.10.2026, la limite d'usage atteinte en pleine tenue d'un graphe d'actualité a fait perdre
        # 206 synthèses sur 372 : elles étaient retirées avant d'être réécrites, et rien ne venait les remplacer.
        # Appelé au fil de l'eau (ECRITURE_S) puis à la fin : chaque appel réécrit le même état, complet à ce moment.
        for n in repondues:
            notions.pop(n, None)
            if n not in partiel and n in G:
                echecs[n] = {
                    "controle": CONTROLE,
                    "empreinte": empreinte_de(G, n, preuves_de(G, n, graphe)),
                    "quand": time.strftime("%Y-%m-%d"),
                }
            else:
                echecs.pop(n, None)
        notions.update(partiel)
        p_n.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")

    # Une notion sans preuve (plus aucune fiche ne la nomme) n'est pas soumise : elle revenait chaque passage comme
    # « à réécrire » ou « éligible », comptée dans le plafond sans appel (11.10.2026 : 30 bornées, 27 soumises).
    sans_preuve = [n for n in a_rediger if not preuves_de(G, n, graphe)]
    for n in sans_preuve:
        notions.pop(n, None)
        echecs[n] = {
            "controle": CONTROLE,
            "empreinte": empreinte_de(G, n, []),
            "quand": time.strftime("%Y-%m-%d"),
            "sans_preuve": True,
        }
    a_rediger = [n for n in a_rediger if n not in sans_preuve]
    if sans_preuve:
        print(f"  {len(sans_preuve)} notion(s) sans preuve, non soumise(s)")
    r = {"non_tentes": 0}
    if a_rediger:
        r = rediger_notions(graphe, G, a_rediger, modele, ecrire=ecrire_notions)
        etat["phrases"] = phrases_avant + r["phrases"]
        etat["retirees"] = retirees_avant + r["retirees"]
    p_n.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    changees = set(a_rediger) | set(parties)
    if changees and sans_themes:
        # Échantillon (mesure du rendement, 11.10.2026) : les notions seules ; les thèmes touchés attendent la tenue
        # suivante, qui les refait dès qu'une de leurs notions change.
        print(f"  thèmes non refaits (--sans-themes) : {len(changees)} notion(s) changée(s), à la tenue suivante")
    elif changees and not couts.en_limite():
        anciens = json.loads(p_t.read_text(encoding="utf-8"))["themes"] if p_t.exists() else {}

        def ecrire_themes(partiel: dict) -> None:
            # Un thème dont la réécriture a échoué, ou n'a pas encore eu lieu, garde son ancienne version (06.10.2026 :
            # 0 thème sur 23 après la limite d'usage atteinte en pleine tenue).
            p_t.write_text(
                json.dumps({"themes": {**anciens, **partiel}}, ensure_ascii=False, indent=1), encoding="utf-8"
            )

        t = rediger_themes(
            graphe, G, notions, modele, combien=40, changees=changees, anciens=anciens, ecrire=ecrire_themes
        )
        for k, v in anciens.items():
            t["themes"].setdefault(k, v)
        p_t.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    elif changees:
        print("  thèmes non refaits : limite d'usage atteinte, les anciens sont gardés", file=sys.stderr)
    print(f"  fait en {time.time() - t0:.0f} s : {len(notions)} notions synthétisées")
    return 2 if r.get("non_tentes") or couts.en_limite() else 0


def appeler(prompt: str, schema: dict, modele: str, graphe: str | None = None) -> dict | None:
    return te.appeler_claude(prompt, schema, modele, poste="pyramide", graphe=graphe)


def construire(graphe: str, combien: int, modele: str, ajouter: int = 0) -> None:
    G = charger_graphe(graphe)
    t0 = time.time()
    p_n = dossier(graphe) / "notions.json"
    ancien = {"notions": {}, "phrases": 0, "retirees": 0}

    def ecrire(partiel: dict, repondues: set) -> None:
        # Au fil de l'eau, comme mettre-a-jour (22.10.2026 : sans `ecrire`, 20 synthèses d'un graphe de chansons payées 0,61 $ ont
        # été perdues quand l'écriture finale a échoué) ; les compteurs sont ceux d'avant, complets à la fin.
        p_n.write_text(
            json.dumps({**ancien, "notions": {**ancien["notions"], **partiel}}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    if ajouter:
        # Les synthèses existantes sont gardées ; seules les suivantes sont rédigées, puis les thèmes refaits.
        if p_n.exists():
            ancien = json.loads(p_n.read_text(encoding="utf-8"))
        ids = notions_suivantes(G, set(ancien["notions"]), ajouter)
        print(
            f"  {graphe} : {len(ancien['notions'])} synthèse(s) gardée(s), {len(ids)} à ajouter "
            f"(plusieurs fiches d'abord, puis les plus reliées), modèle {modele}",
            flush=True,
        )
        neuf = rediger_notions(graphe, G, ids, modele, ecrire)
        n = {
            "notions": {**ancien["notions"], **neuf["notions"]},
            "phrases": ancien["phrases"] + neuf["phrases"],
            "retirees": ancien["retirees"] + neuf["retirees"],
        }
    else:
        ids = choisir_notions(G, combien)
        print(f"  {graphe} : {len(ids)} notion(s) retenue(s) (degré ≥ {DEGRE_MIN}), modèle {modele}", flush=True)
        n = rediger_notions(graphe, G, ids, modele, ecrire)
    n = {
        k: n[k] for k in ("notions", "phrases", "retirees")
    }  # `repondues` (un ensemble) et `non_tentes` restent en mémoire
    p_n.write_text(json.dumps(n, ensure_ascii=False, indent=1), encoding="utf-8")
    t = rediger_themes(graphe, G, n["notions"], modele, combien=40)
    (dossier(graphe) / "themes.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"  fait en {time.time() - t0:.0f} s : {len(n['notions'])} notions, {len(t['themes'])} thèmes ; phrases retirées "
        f"par le contrôle : {n['retirees']}/{n['phrases']} (notions), {t['retirees']}/{t['phrases']} (thèmes)"
    )


# --- Répondre et descendre -----------------------------------------------------------------------------------


def lire(graphe: str) -> tuple[dict, dict]:
    d = cfg.chemins_du_graphe(graphe)["base"] / "synthese"  # sans le créer : le serveur ne fait que lire
    n = json.loads((d / "notions.json").read_text(encoding="utf-8"))["notions"] if (d / "notions.json").exists() else {}
    t = json.loads((d / "themes.json").read_text(encoding="utf-8"))["themes"] if (d / "themes.json").exists() else {}
    return n, t


PASSAGES = 3
LONGUEUR_PASSAGE = 900
AJOUT_CARTE = 3  # notions de la sous-question de carte ajoutées aux lexicales (cartes.py, 09.10.2026)
TEMPOREL = re.compile(
    r"(?i)\b(quand|depuis|délai|delai|calendrier|date|échéance|echeance|report|vigueur|applicable|"
    r"à partir|jusqu|année|annee|chronolog|évolu|evolu|chang|omnibus|version|20\d\d|when|since|deadline|timeline)"
)


ENCODEUR = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_ENCODEURS: dict = {}
_INDEX: dict = {}


def modele_affine(graphe: str | None) -> Path | None:
    """Le modèle d'encodeur affiné retenu pour ce graphe : cache/encodeur/<graphe>/retenu nomme un dossier de modèle
    (encodeur.py entrainer ; retenu le 10.10.2026 pour un graphe de conformité : modele_3000_2ep, fiche retrouvée 74 → 105 sur
    150 faits rares). Hors dépôt ; absent, l'encodeur de base sert."""
    if not graphe:
        return None
    p = RACINE / "cache" / "encodeur" / graphe / "retenu"
    if p.exists():
        d = p.parent / p.read_text(encoding="utf-8").strip()
        if d.is_dir():
            return d
    return None


class _EncodeurONNX:
    """L'encodeur affiné exporté en ONNX (exporter_onnx.py : pooling moyen et normalisation dans le graphe) : `tokenizers`
    et `onnxruntime` seulement, sans torch — c'est lui que le serveur charge. Même interface que fastembed."""

    def __init__(self, d: Path) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        c = json.loads((d / "config.json").read_text(encoding="utf-8"))
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(int(c.get("max_seq_length") or c["max_length"]))
        self.tok.enable_padding(pad_id=self.tok.token_to_id(c["pad_token"]), pad_token=c["pad_token"])
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.s = ort.InferenceSession(str(d / "model.onnx"), so, providers=["CPUExecutionProvider"])
        self.nom = c.get("nom", d.parent.name)

    def _lot(self, textes: list) -> dict:
        import numpy as np  # noqa: PLC0415

        encs = self.tok.encode_batch(textes)
        return {
            "input_ids": np.array([e.ids for e in encs], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encs], dtype=np.int64),
        }

    def embed(self, textes):
        textes = list(textes)
        for k in range(0, len(textes), 64):
            yield from self.s.run(None, self._lot(textes[k : k + 64]))[0]


class _ReclasseurONNX(_EncodeurONNX):
    """Le modèle croisé exporté (exporter_onnx.py) : un logit par paire (question, passage), même `predict` que CrossEncoder."""

    def __init__(self, d: Path) -> None:
        super().__init__(d)
        c = json.loads((d / "config.json").read_text(encoding="utf-8"))
        self.tok.enable_truncation(int(c["max_length"]), strategy="longest_first")

    LOT = 8  # mesuré le 10.10.2026 (50 candidats × 1 500 car.) : lot 16 3,6 s et +213 Mo d'arène, lot 8 3,3 s et +118 Mo, lot 4 3,5 s et +30 Mo

    def predict(self, paires, batch_size: int = 16, show_progress_bar: bool = False):
        import numpy as np  # noqa: PLC0415

        out: list[float] = []
        batch_size = min(batch_size, self.LOT)
        for k in range(0, len(paires), batch_size):
            out.extend(self.s.run(None, self._lot([tuple(p) for p in paires[k : k + batch_size]]))[0].tolist())
        return np.asarray(out, dtype=np.float32)


MEMOIRE_MODELES = 3 * 1024**3  # en deçà de cette borne (cgroup, MemoryMax de l'unité), les modèles ne se chargent pas :
# mesuré le 15.10.2026, un `repondre` avec les trois modèles culmine à 2,4 Go, 1,8 sans le doublon


def memoire_bornee() -> int | None:
    """La borne mémoire du cgroup du processus (MemoryMax de l'unité systemd), en octets ; None sans borne."""
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(f).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < 1 << 60:
            return int(v)
    return None


def _charger(essais: list) -> object | None:
    """Le premier modèle qui se charge parmi (genre, fabrique) ; torch d'abord sur ce Mac, l'ONNX quand torch manque
    (serveur) ; KG_ONNX=1 met l'ONNX en premier pour le mesurer ici. Sous une borne mémoire de moins de
    MEMOIRE_MODELES (le serveur à MemoryMax=1G, tué par l'OOM le 05.10.2026), rien ne se charge : recherche par les mots."""
    import os  # noqa: PLC0415

    borne = memoire_bornee()
    if borne is not None and borne < MEMOIRE_MODELES:
        print(
            f"  (modèles non chargés : borne mémoire de {borne / 1024**3:.1f} Go, {MEMOIRE_MODELES / 1024**3:.0f} Go requis)",
            file=sys.stderr,
        )
        return None
    if os.environ.get("KG_ONNX"):
        essais = sorted(essais, key=lambda e: e[0] != "onnx")
    for genre, fabrique in essais:
        try:
            return fabrique()
        except Exception as e:  # noqa: BLE001 — sans sentence-transformers (serveur), l'ONNX sert
            print(f"  ({genre} indisponible : {str(e)[:120]})", file=sys.stderr)
    return None


class _EncodeurAffine:
    """Même interface que fastembed (`embed` rend un itérateur de vecteurs) pour un modèle sentence-transformers."""

    def __init__(self, chemin: Path) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.m = SentenceTransformer(str(chemin))
        self.nom = chemin.name

    def embed(self, textes):
        textes = list(textes)
        for k in range(0, len(textes), 256):
            yield from self.m.encode(
                textes[k : k + 256], batch_size=64, normalize_embeddings=True, show_progress_bar=False
            )


def encodeur(graphe: str | None = None):
    """L'encodeur du graphe : le modèle affiné retenu s'il existe (modele_affine), sinon l'encodeur multilingue léger de
    base (fastembed, ONNX, ≈ 220 Mo) ; None quand rien n'est disponible, et la recherche retombe sur les mots.
    KG_MODELES : un dossier où le modèle de base est déjà téléchargé (serveur cloisonné). L'index d'un graphe doit avoir
    été calculé avec le même encodeur que ses questions : `indexer` note le nom du modèle dans synthese/encodeur.txt."""
    cle = graphe or ""
    if cle not in _ENCODEURS:
        enc = None
        affine = modele_affine(graphe)
        if affine:
            essais = [("torch", lambda: _EncodeurAffine(affine))]
            if (affine / "onnx" / "model.onnx").exists():
                essais.append(("onnx", lambda: _EncodeurONNX(affine / "onnx")))
            enc = _charger(essais)
            if enc is None:
                print(f"  (encodeur affiné {affine.name} indisponible ; encodeur de base)", file=sys.stderr)
        if enc is None:
            if "" in _ENCODEURS:
                enc = _ENCODEURS[""]
            else:
                try:
                    import os  # noqa: PLC0415
                    from fastembed import TextEmbedding  # noqa: PLC0415

                    d = os.environ.get("KG_MODELES")
                    enc = TextEmbedding(ENCODEUR, cache_dir=d) if d else TextEmbedding(ENCODEUR)
                except Exception as e:  # noqa: BLE001
                    print(f"  (encodeur indisponible, recherche par les mots : {str(e)[:160]})", file=sys.stderr)
                    enc = None
                _ENCODEURS[""] = enc
        _ENCODEURS[cle] = enc
    return _ENCODEURS[cle]


def indexer(graphe: str) -> None:
    """Les paragraphes des fiches du graphe et leurs vecteurs (synthese/paragraphes.json, vecteurs.npy). Incrémental
    depuis le 12.10.2026 : un paragraphe dont le texte n'a pas changé garde son vecteur (synthese/empreintes_index.json,
    empreinte du texte par ligne, dans l'ordre de paragraphes.json) ; seuls les paragraphes nouveaux sont encodés.
    Mesuré avant : 41 891 paragraphes réencodés chaque nuit pour 100 fiches nouvelles sur 2 322, 3 032 s sous le daemon.
    Tout est recalculé quand l'encodeur change (encodeur.txt) ou quand les fichiers de l'index ne se correspondent pas."""
    import hashlib  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    G = charger_graphe(graphe)
    ids, textes = [], []
    for f in sorted(
        {
            str(d["source_file"])
            for _, d in G.nodes(data=True)
            if d.get("kind") in ("article", "papier") and d.get("source_file")
        }
    ):
        for ligne, texte in ig._paragraphes(RACINE / f):
            if len(texte) >= 120:
                ids.append([f, ligne])
                textes.append(texte[:1200])
    t0 = time.time()
    enc = encodeur(graphe)
    nom = getattr(enc, "nom", ENCODEUR)
    d = dossier(graphe)
    empreintes = [hashlib.sha1(t.encode("utf-8")).hexdigest()[:16] for t in textes]
    anciens, E0 = {}, None
    try:
        if (d / "encodeur.txt").read_text(encoding="utf-8").strip() == nom:
            E0 = np.load(d / "vecteurs.npy")
            h0 = json.loads((d / "empreintes_index.json").read_text(encoding="utf-8"))
            if len(h0) == len(E0):
                anciens = {h: k for k, h in enumerate(h0)}
    except (OSError, ValueError):
        anciens = {}
    nouveaux = [k for k, h in enumerate(empreintes) if h not in anciens]
    E_nouv = np.array(list(enc.embed([textes[k] for k in nouveaux])), dtype=np.float32) if nouveaux else None
    if E_nouv is not None:
        E_nouv /= np.linalg.norm(E_nouv, axis=1, keepdims=True)
    dim = E_nouv.shape[1] if E_nouv is not None else E0.shape[1]
    source = np.array([anciens.get(h, -1) for h in empreintes], dtype=np.int64)
    E = np.empty((len(ids), dim), dtype=np.float16)
    gardes = source >= 0
    if gardes.any():
        E[gardes] = E0[source[gardes]]
    if E_nouv is not None:
        E[~gardes] = E_nouv.astype(np.float16)
    np.save(d / "vecteurs.npy", E)
    (d / "paragraphes.json").write_text(json.dumps(ids), encoding="utf-8")
    (d / "empreintes_index.json").write_text(json.dumps(empreintes), encoding="utf-8")
    (d / "encodeur.txt").write_text(nom, encoding="utf-8")
    print(
        f"  {graphe} : {len(ids)} paragraphes indexés en {time.time() - t0:.0f} s — {len(nouveaux)} encodé(s), "
        f"{int(gardes.sum())} vecteur(s) réemployé(s)"
    )


_INDEX_DATE: dict = {}
_MOTS_INDEX: dict = {}


def index_mots(graphe: str, textes: list[str]):
    """L'index inversé des mots des paragraphes : terme → indices des paragraphes qui le portent (tableaux numpy),
    longueur de chaque paragraphe en termes distincts, longueur moyenne. Mesuré le 10.10.2026 sur le serveur :
    le BM25 qui recomptait chaque paragraphe à chaque question prenait 0,7 s et 300 Mo ; ici quelques millisecondes."""
    if graphe not in _MOTS_INDEX:
        import numpy as np  # noqa: PLC0415

        post: dict[str, list[int]] = {}
        longueurs = np.zeros(len(textes), dtype=np.float64)
        for i, t in enumerate(textes):
            ms = mots(t)
            longueurs[i] = len(ms)
            for m in ms:
                post.setdefault(m, []).append(i)
        _MOTS_INDEX[graphe] = (
            {m: np.array(ix, dtype=np.int32) for m, ix in post.items()},
            longueurs,
            float(longueurs.mean()) if len(textes) else 1.0,
        )
    return _MOTS_INDEX[graphe]


def bm25_inverse(requete: str, ix_mots) -> "np.ndarray":  # noqa: F821 — numpy importé dans les fonctions
    """Les scores BM25 de tous les paragraphes pour la requête, par l'index inversé (mêmes valeurs que `bm25` :
    un terme compte une fois par paragraphe, `mots` rendant un ensemble)."""
    import math  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    post, longueurs, moy = ix_mots
    N = len(longueurs)
    score = np.zeros(N, dtype=np.float64)
    for t in mots(requete):
        ix = post.get(t)
        if ix is None:
            continue
        df = len(ix)
        idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
        score[ix] += idf * 2.2 / (1 + 1.2 * (0.25 + 0.75 * longueurs[ix] / moy))
    return score


def index(graphe: str):
    """(ids, textes, vecteurs) de l'index des paragraphes, ou None s'il manque. Relu quand vecteurs.npy change de date
    (le serveur reçoit l'index par publier_index.py sans redémarrer) ; un index dont les deux fichiers ne se
    correspondent pas (envoi en cours) est ignoré jusqu'au prochain appel."""
    d = cfg.chemins_du_graphe(graphe)["base"] / "synthese"
    try:
        date = (d / "vecteurs.npy").stat().st_mtime
    except OSError:
        date = None
    if graphe not in _INDEX or _INDEX_DATE.get(graphe) != date:
        try:
            import numpy as np  # noqa: PLC0415

            ids = json.loads((d / "paragraphes.json").read_text(encoding="utf-8"))
            E = np.load(d / "vecteurs.npy").astype(np.float32)
            if len(ids) != len(E):
                raise ValueError("index incomplet")
            par = {}
            for f, l in ids:
                if f not in par:
                    par[f] = dict(ig._paragraphes(RACINE / f))
            textes = [par[f].get(l, "")[:1200] for f, l in ids]
            _INDEX[graphe] = (ids, textes, E)
            _MOTS_INDEX.pop(graphe, None)
        except (OSError, ValueError, ImportError):
            _INDEX[graphe] = None
        _INDEX_DATE[graphe] = date
    return _INDEX[graphe]


def bm25(requete: str, docs_mots: list[list[str]], moy: float, df) -> list[float]:
    import math  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415

    N, out = len(docs_mots), []
    for d in docs_mots:
        c = Counter(d)
        s = 0.0
        for t in mots(requete):
            if c[t]:
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * len(d) / moy))
        out.append(s)
    return out


def passages_hybrides(graphe: str, question: str, combien: int) -> list[tuple[str, int, str]] | None:
    """Les paragraphes par fusion des rangs : les mots (BM25) et l'encodeur multilingue. Au pilote du 06.10.2026 (un graphe audio,
    40 questions du banc), le paragraphe cité arrivait dans les trois premiers 22 fois sur 40, contre 18 par les mots
    seuls : les mots attrapent les noms exacts, l'encodeur le sens d'une langue à l'autre. None si l'index ou
    l'encodeur manquent."""
    enc = encodeur(graphe)
    ix = index(graphe) if enc is not None else None  # sans encodeur, l'index (≈ 400 Mo chargé) ne sert à rien
    if not ix or enc is None:
        return None
    import numpy as np  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415

    ids, textes, E = ix
    rang_mots = list(np.argsort(-bm25_inverse(question, index_mots(graphe, textes)))[:200])
    q = np.array(list(enc.embed([question]))[0], dtype=np.float32)
    rang_sens = list(np.argsort(-(E @ (q / np.linalg.norm(q)))))[:200]
    score: dict[int, float] = {}
    for rangs in (rang_mots, rang_sens):
        for r, i in enumerate(rangs):
            score[int(i)] = score.get(int(i), 0.0) + 1.0 / (60 + r)
    out, vus = [], set()
    # Reclassement croisé (porteur, 10.10.2026 : « sélection par ranking ») : quand le registre du graphe le demande
    # (`reclasseur: true`), un modèle croisé multilingue léger relit la question avec chacun des CANDIDATS_RECLASSEUR
    # premiers candidats de la fusion et les reclasse ; les `combien` premiers sont servis. Mesuré sur un graphe de conformité,
    # fiche citée dans les trois extraits : 45 → 75 % (banc), 17 → 24 % (thèmes, limité par le rappel). 3 s par question.
    candidats = CANDIDATS_RECLASSEUR if cfg.graphe_declare(graphe).get("reclasseur") else combien
    for i in sorted(score, key=lambda i: -score[i]):
        cle = ig._plier(textes[i])[:160]
        if cle in vus or not textes[i]:
            continue
        vus.add(cle)
        out.append((ids[i][0], ids[i][1], textes[i]))
        if len(out) >= candidats:
            break
    if candidats > combien:
        rc = reclasseur()
        if rc is not None:
            scores = rc.predict([(question, t_[:1500]) for _, _, t_ in out], batch_size=16, show_progress_bar=False)
            out = [out[j] for j in np.argsort(-np.asarray(scores))]
    return out[:combien]


RECLASSEUR = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # 118 M, multilingue, local
CANDIDATS_RECLASSEUR = 50
_RECLASSEUR: list = []


def reclasseur():
    """Le modèle croisé de reclassement, chargé une fois : CrossEncoder (torch) ici, son exportation ONNX
    (cache/reclasseur/<nom>/onnx, exporter_onnx.py) sur le serveur ; None si rien ne se charge."""
    if not _RECLASSEUR:

        def _torch():
            from sentence_transformers import CrossEncoder  # noqa: PLC0415

            return CrossEncoder(RECLASSEUR, max_length=512)

        essais = [("torch", _torch)]
        onnx = RACINE / "cache" / "reclasseur" / RECLASSEUR.split("/")[-1] / "onnx"
        if (onnx / "model.onnx").exists():
            essais.append(("onnx", lambda: _ReclasseurONNX(onnx)))
        rc = _charger(essais)
        if rc is None:
            print("  (reclasseur indisponible)", file=sys.stderr)
        _RECLASSEUR.append(rc)
    return _RECLASSEUR[0]


def passages_pour(
    graphe: str, question: str, combien: int = PASSAGES, G=None, prioritaires: set[str] | None = None
) -> list[tuple[str, int, str]]:
    """Les paragraphes de fiches les plus pertinents pour la question : (fiche, ligne, texte), le niveau du dessous.
    `prioritaires` : les fiches que la carte du sujet désigne (cartes.py) ; à critères égaux de pertinence, leurs
    paragraphes passent d'abord, le reste du corpus reste le repli (la voie hybride, par l'index, ne les lit pas)."""
    if cfg.lit_cortexdb(graphe):
        from newskit import lecture_cortexdb  # noqa: PLC0415

        lus = lecture_cortexdb.passages_du_graphe(graphe, question, combien * LONGUEUR_PASSAGE)
        if lus is not None:
            return lus
    hybrides = passages_hybrides(graphe, question, combien)
    if hybrides is not None:
        return hybrides
    prioritaires = prioritaires or set()
    termes = sorted(mots(question))
    if not termes:
        return []
    # Les noms propres, sigles et versions sont les mêmes en français et en anglais (les fiches sont surtout en
    # anglais) : quand la question en porte, un passage doit les contenir ; les autres mots départagent. Au banc du
    # 05.10.2026, « latence » ne trouvait pas « latency », mais « AudioStellar 2.0 » ou « Engram » trouvent leur fiche.
    noms = sorted({f for f in faits(question) if not f.replace(".", "").isdigit()})
    G = G if G is not None else charger_graphe(graphe)
    fiches = {
        str(d["source_file"])
        for _, d in G.nodes(data=True)
        if d.get("kind") in ("article", "papier") and d.get("source_file")
    }
    remplacees = {
        str(d["source_file"])
        for _, d in G.nodes(data=True)
        if d.get("statut") == diachronie.REMPLACEE and d.get("source_file")
    }
    # Les textes de référence (1 200 paragraphes de loi pour un graphe de conformité, 09.10.2026) répondent aux mêmes mots que
    # les commentaires et les évinçaient des passages rendus (banc par sujet : 5 questions perdues sur 78). Sauf quand
    # la question nomme un article ou une annexe, au plus un passage vient d'un texte de loi.
    references = {str(d["source_file"]) for _, d in G.nodes(data=True) if d.get("statut") and d.get("source_file")}
    nomme_article = re.search(r"(?i)\b(art(?:icle|\.)?\s*\d+|annexe?\s+[ivx\d]+|§\s*\d+)", question) is not None
    # Au moins un passage de commentaire, le reste peut venir de la loi (17:36 : un plafond de 1 perdait les quatre
    # fonctions du NIST AI RMF, texte de référence sans article nommé) ; article nommé : pas de plafond.
    plafond_ref = combien if nomme_article else max(1, combien - 1)
    trouve = lambda t, plie: re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", plie) is not None  # noqa: E731
    trouves = []
    for f in fiches:
        paras = ig._paragraphes(RACINE / f)
        for k, (ligne, texte) in enumerate(paras):
            # Un paragraphe court annonce souvent ce que le suivant détaille, sans en répéter le nom (05.10.2026 :
            # l'accord ElevenLabs-UMG, dont le contenu est au paragraphe d'après) : on le lit avec sa suite.
            if len(texte) < 500 and k + 1 < len(paras):
                texte = texte + " " + paras[k + 1][1]
            plie = ig._plier(texte)
            presents = {t for t in termes if trouve(t, plie)}
            n_noms = sum(1 for t in noms if trouve(t, plie))
            if noms:
                if n_noms == 0 or n_noms * 2 < len(noms):
                    continue
            elif not presents or len(presents) * 2 < len(termes):
                continue
            # Une version remplacée d'un texte passe après la version en vigueur, à pertinence égale (diachronie).
            # La fiche désignée par la carte ne départage qu'à pertinence égale (noms, puis mots) : un paragraphe qui
            # nomme l'arrêt demandé passe toujours avant un paragraphe d'une fiche de la carte qui ne le nomme pas.
            # À pertinence égale (noms, puis mots), un commentaire passe avant un paragraphe de texte de référence : la
            # matière diachronique est un « en plus », jamais un remplacement de l'actualité (porteur, 10.10.2026).
            trouves.append(
                (
                    n_noms,
                    len(presents),
                    0 if f in references else 1,
                    1 if f in prioritaires else 0,
                    0 if f in remplacees else 1,
                    sum(plie.count(t) for t in presents),
                    f,
                    ligne,
                    texte,
                )
            )
    trouves.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5]), reverse=True)
    trouves = [(a, b, f, l, t) for a, b, _, _, _, _, f, l, t in trouves]
    out, vus, pris_ref = [], set(), 0
    for _, _, f, l, t in trouves:  # le même texte repris par deux fiches ne compte qu'une fois
        cle = ig._plier(t)[:160]
        if cle in vus:
            continue
        if f in references:
            if pris_ref >= plafond_ref:
                continue
            pris_ref += 1
        vus.add(cle)
        out.append((f, l, t))
        if len(out) >= combien:
            break
    return out


def carte_pour(graphe: str, question: str, sans_cartes: bool = False) -> dict | None:
    """La sous-question de carte qui route la question (cartes.py), ou None : sans carte, sous le seuil, ou quand
    `sans_cartes` / KG_SANS_CARTES=1 demande la mesure sans cartes (banc : `--sans-cartes`)."""
    if sans_cartes or os.environ.get("KG_SANS_CARTES") == "1":
        return None
    try:
        from newskit import cartes  # noqa: PLC0415 — cartes importe synthese ; l'import tardif évite le cycle

        return cartes.router(graphe, question)
    except Exception as e:  # noqa: BLE001 — la carte oriente ; son absence ne doit pas empêcher la réponse
        print(f"  (cartes : {type(e).__name__} {str(e)[:120]})", file=sys.stderr)
        return None


def repondre(
    graphe: str,
    question: str,
    combien: int = 3,
    G=None,
    reserve=None,
    garder: bool = True,
    sans_cartes: bool = False,
    masquee=None,
) -> None:
    """Affiche la réponse par la pyramide. `G` : le graphe déjà chargé (serveur) ; `reserve(chemin)` : vrai pour une
    fiche dont le texte ne se redistribue pas (ses extraits sont masqués, ses synthèses restent, comme les notions
    qu'on en tire) ; `garder` : écrire les renvois pour `descendre R<n>` (ligne de commande seulement) ;
    `masquee(chemin)` : vrai pour une fiche dont le contenu n'est pas relayé (payante par défaut, ou désaccord de la
    source ou d'un auteur) : une phrase qui s'appuie sur une seule d'entre elles n'est pas citée, sa notion et ses
    renvois restent comme carte (porteur, 25.10.2026 : « les synthèses ne sont pas citées directement, mais servent
    uniquement de cartes »).
    Avec une carte du sujet (cartes.py, 09.10.2026), la question est d'abord classée parmi les sous-questions des
    cartes : la sous-question retenue donne ses notions avant celles choisies par les mots, sa liste telle quelle, la
    chronologie du sujet, et ses fiches passent d'abord dans les passages ; elle tient lieu de thème. Sous le seuil
    de routage, rien ne change (`sans_cartes` : mesurer l'un contre l'autre)."""
    notions, themes = lire(graphe)
    if not notions:
        print(f"  Pas de synthèses pour {graphe} : interroger par `chercher`, `expliquer` et `passages`.")
        return
    q = mots(question)
    carte = carte_pour(graphe, question, sans_cartes)

    def score(label: str, texte: str) -> float:
        ml, mt = mots(label), mots(texte)
        # Le nom entier, en mots : « ICA » n'est pas dans « musicales ».
        nomme = re.search(r"(?<![\w])" + re.escape(ig._plier(label)) + r"(?![\w])", ig._plier(question))
        return 3 * len(q & ml) + len(q & mt) + (5 if nomme else 0)

    classees = sorted(
        notions.items(), key=lambda kv: -score(kv[1]["label"], " ".join(p["texte"] for p in kv[1]["phrases"]))
    )
    scores = [score(v["label"], " ".join(p["texte"] for p in v["phrases"])) for _, v in classees[:combien]]
    # Pas de remplissage : une notion n'est rendue que si elle vaut au moins la moitié de la meilleure (le 05.10.2026,
    # « représentations » ramenait ICA à côté de MuQ, et la sortie s'allongeait d'autant).
    retenues = [(k, v) for (k, v), sc in zip(classees[:combien], scores) if sc > 0 and sc * 2 >= scores[0]]
    if carte:
        # La carte AJOUTE ses notions aux lexicales, elle ne les remplace pas : mesuré le 09.10.2026 sur 165 questions
        # des bancs, les notions de la carte seules couvraient 0,44 des faits attendus contre 0,51 pour les lexicales,
        # et l'union 0,63. Le thème s'efface à sa place, pour que la sortie ne s'allonge pas.
        deja = {k for k, _ in retenues}
        retenues = (
            retenues + [(n, notions[n]) for n in carte["q"]["notions"] if n in notions and n not in deja][:AJOUT_CARTE]
        )
    refs: list[tuple[str, int]] = []

    def renvoi(r: dict) -> str:
        cle = (r["fiche"], r["ligne"])
        if cle not in refs:
            refs.append(cle)
        return f"R{refs.index(cle) + 1}"

    theme = None
    if carte:
        sq = carte["q"]
        print(f"CARTE — {carte['sujet'][:90]}\n  › {sq['q']}")
        if sq.get("liste"):
            fondee = ", ".join(notions[n]["label"] for n in sq["notions"] if n in notions)
            print(
                f"  LISTE (d'après {fondee[:160]})"
                + (
                    f" — {sq['liste_retires']} élément(s) retiré(s) au contrôle, liste partielle"
                    if sq.get("liste_retires")
                    else ""
                )
                + " :"
            )
            for el in sq["liste"]:
                print(f"   – {el}")
        from newskit import cartes as cartes_mod  # noqa: PLC0415 — import tardif; from newskit import cartes importe synthese

        for p in cartes_mod.peu_couverts_pour(carte, question):
            # Un point que le graphe couvre peu (peu_couverts.py) : dit au lecteur, qui s'abstient plutôt que de broder.
            print(
                f"  PEU COUVERT : le graphe documente mal ce point ({p['origine']}) — « {p['point'][:160]} ». Sans passage qui porte le fait demandé, répondre que le graphe ne le couvre pas ; ne rien déduire."
            )
        if carte.get("chronologie") and TEMPOREL.search(question):
            # La chronologie du sujet ne se rend qu'à une question qui porte sur le temps : sinon elle coûte sans rendre.
            print("  CHRONOLOGIE :")
            for e in carte["chronologie"]:
                print(f"   – {e['date']} : {e['fait']}")
    elif themes:
        if not sans_cartes and os.environ.get("KG_SANS_CARTES") != "1":
            try:
                from newskit import cartes as cartes_mod  # noqa: PLC0415

                for p in cartes_mod.peu_couverts_sans_carte(graphe, question):
                    print(
                        f"PEU COUVERT : le graphe documente mal ce point ({p['origine']}, sujet « {p['sujet'].partition(' : ')[0]} ») — « {p['point'][:160]} ». Sans passage qui porte le fait demandé, répondre que le graphe ne le couvre pas ; ne rien déduire."
                    )
            except Exception:  # noqa: BLE001 — un signal de plus, jamais une panne
                pass
        ids = {k for k, _ in retenues}
        theme = max(
            themes.values(),
            key=lambda t: (
                len(ids & set(t["notions"])),
                len(q & mots(t["titre"] + " " + " ".join(p["texte"] for p in t["phrases"]))),
            ),
        )
        texte_theme = theme["titre"] + " " + " ".join(p["texte"] for p in theme["phrases"])
        if not (ids & set(theme["notions"])) or len(q & mots(texte_theme)) < 2:
            theme = None
    vus: dict[str, bool] = {}

    def tue(ph: dict) -> bool:
        """Une phrase de notion s'appuie-t-elle sur une fiche dont le contenu n'est pas relayé ?"""
        if not masquee:
            return False
        for r in ph.get("refs") or []:
            if r["fiche"] not in vus:
                vus[r["fiche"]] = bool(masquee(RACINE / r["fiche"]))
            if vus[r["fiche"]]:
                return True
        return False

    def theme_tu(ph: dict) -> bool:
        """Une phrase de thème, écrite d'après des notions : tue si l'une d'elles a une phrase tue."""
        return any(tue(x) for n in ph.get("notions") or [] for x in (notions.get(n) or {}).get("phrases") or [])

    if theme:
        print(
            f"THÈME — {theme['titre']}\n  " + " ".join("(…)" if theme_tu(p) else p["texte"] for p in theme["phrases"])
        )
    sortie = (theme["titre"] + " " + " ".join(p["texte"] for p in theme["phrases"])) if theme else ""
    for _, v in retenues:
        print(f"\nNOTION — {v['label']}")
        if v.get("actualite"):
            # Ce que ses attestations disent du présent de la notion (diachronie.py) : dates, texte en vigueur ou
            # version remplacée seulement, évolution entre deux versions.
            print(f"  (actualité : {diachronie.phrase(v['actualite'])})")
        for p in v["phrases"]:
            texte = (
                "(synthèse non citée : une de ses sources n'est pas relayée ; suivre ses renvois)"
                if tue(p)
                else p["texte"]
            )
            print(f"  {texte} [{', '.join(renvoi(r) for r in p['refs'])}]")
        sortie += " " + v["label"] + " " + " ".join(p["texte"] for p in v["phrases"])
    # Descente automatique : quand les synthèses ne couvrent pas la question, les paragraphes de fiches les plus
    # pertinents suivent dans la même sortie. L'agent n'a pas à décider de descendre — au banc du 05.10.2026,
    # Sonnet répondait « je ne sais pas » à 9 questions sur 40 que les fiches résolvaient.
    # Compter les mots couverts ne dit pas si la réponse y est (05.10.2026 : des synthèses reprenaient les mots de
    # la question sans le détail demandé). Les meilleurs passages suivent donc toujours, courts : la synthèse oriente,
    # le passage apporte le chiffre, la date, la citation — dans le même appel.
    if True:
        # KG_PASSAGES : plus d'extraits pour une tentative élargie (second essai du banc par thème, 10.10.2026), 3 sinon.
        combien_p = int(os.environ.get("KG_PASSAGES") or PASSAGES)
        extraits = passages_pour(
            graphe, question, combien_p, G, prioritaires=set(carte["q"].get("references") or []) if carte else None
        )
        if extraits:
            print("\nPASSAGES — extraits des fiches :")
            for f, l, t in extraits:
                r = renvoi({"fiche": f, "ligne": l})
                if reserve and reserve(RACINE / f):
                    print(
                        f"  [{r}] texte non rendu ici (abonnement, document privé ou refus de l'auteur) : lire l'original à son adresse"
                    )
                    continue
                ch = fiche.lire_fichier(RACINE / f)[0] if (RACINE / f).exists() else {}
                marque = (
                    f" (version REMPLACÉE du {ch.get('version')}, plus en vigueur ; voir {ch.get('remplacee_par') or 'la version en vigueur'})"
                    if ch.get("statut") == diachronie.REMPLACEE
                    else ""
                )
                print(
                    f"  [{r}]"
                    + (" (moindre autorité : ton promotionnel, à recouper)" if moindre(f) else "")
                    + marque
                    + " "
                    + (t if len(t) <= LONGUEUR_PASSAGE else t[:LONGUEUR_PASSAGE] + " […]")
                )
        elif not retenues:
            print("  Ni synthèse ni passage ne répondent à cette question.")
            return
    # Une ligne par fiche, ses renvois et ses lignes ensemble (09.10.2026 : avec les cartes, six notions citent leurs
    # fiches ; un chemin complet par renvoi pesait 1 600 caractères par réponse, relus à chaque tour du modèle).
    print(
        "\nRENVOIS — `descendre R<n>` (ou la fiche et la ligne) pour lire le paragraphe cité ; L0 = ligne « pourquoi » du graphe :"
    )
    par_fiche: dict[str, list[str]] = {}
    for i, (f, l) in enumerate(refs):
        par_fiche.setdefault(f, []).append(f"R{i + 1}=L{l}")
    for f, rs in par_fiche.items():
        print(f"  {' '.join(rs)}  {f}" + (" — moindre autorité" if moindre(f) else ""))
    if garder:
        (dossier(graphe) / ".derniers_renvois.json").write_text(json.dumps(refs), encoding="utf-8")


def descendre(graphe: str, cible: list[str], reserve=None) -> None:
    if len(cible) == 1 and re.fullmatch(r"R\d+", cible[0]):
        p = dossier(graphe) / ".derniers_renvois.json"
        refs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        k = int(cible[0][1:]) - 1
        if not 0 <= k < len(refs):
            print("  renvoi inconnu : relancer `repondre`")
            return
        fiche, ligne = refs[k]
    elif len(cible) == 2 and cible[1].lstrip("L").isdigit():
        fiche, ligne = cible[0], int(cible[1].lstrip("L"))
    else:
        print("  descendre R<n>, ou descendre <fiche> <ligne>")
        return
    if reserve and reserve(RACINE / fiche):
        print(f"  {fiche}" + (f" L{ligne}" if ligne else ""))
        print("  texte non rendu ici (abonnement, document privé ou refus de l'auteur) : lire l'original à son adresse")
        return
    paras = ig._paragraphes(RACINE / fiche)
    choisi = next((t for l, t in paras if l == ligne), None) if ligne else None
    print(f"  {fiche}" + (f" L{ligne}" if ligne else ""))
    print("  " + (choisi or "(ligne « pourquoi » du graphe : voir interroger_graphe.py expliquer)"))


def etat(graphe: str) -> None:
    notions, themes = lire(graphe)
    n_ph = sum(len(v["phrases"]) for v in notions.values())
    print(f"  {graphe} : {len(notions)} notions ({n_ph} phrases), {len(themes)} thèmes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("construire")
    p.add_argument("--graphe", required=True)
    p.add_argument("--notions", type=int, default=150)
    p.add_argument("--modele", default=MODELE)
    p.add_argument("--ajouter", type=int, default=0, help="garder les synthèses existantes et en rédiger N de plus")
    p = sub.add_parser("repondre")
    p.add_argument("--graphe", required=True)
    p.add_argument("question", nargs="+")
    p.add_argument(
        "--sans-cartes",
        action="store_true",
        help="sans le routage par la carte du sujet (mesure ; aussi KG_SANS_CARTES=1)",
    )
    p = sub.add_parser("descendre")
    p.add_argument("--graphe", required=True)
    p.add_argument("cible", nargs="+")
    p = sub.add_parser("etat")
    p.add_argument("--graphe", required=True)
    p = sub.add_parser("indexer", help="les vecteurs des paragraphes, pour la recherche par les mots et le sens")
    p.add_argument("--graphe", required=True)
    p = sub.add_parser("mettre-a-jour", help="tenue quotidienne d'une pyramide existante (routine)")
    p.add_argument("--graphe", required=True)
    p.add_argument("--plafond", type=int, default=60)
    p.add_argument("--modele", default=MODELE)
    p.add_argument(
        "--sans-themes",
        action="store_true",
        help="échantillon : rédiger les notions, laisser les thèmes à la tenue suivante",
    )
    p.add_argument(
        "--notions",
        default=None,
        metavar="REGEX",
        help="rattrapage ciblé : les seules notions éligibles dont le libellé répond à l'expression, hors plafond",
    )
    a = ap.parse_args()
    g = cfg.nom_actuel(a.graphe)
    if a.cmd == "construire":
        construire(g, a.notions, a.modele, a.ajouter)
    elif a.cmd == "repondre":
        repondre(g, " ".join(a.question), sans_cartes=a.sans_cartes)
    elif a.cmd == "descendre":
        descendre(g, a.cible)
    elif a.cmd == "indexer":
        indexer(g)
    elif a.cmd == "mettre-a-jour":
        return mettre_a_jour(g, a.plafond, a.modele, sans_themes=a.sans_themes, seulement=a.notions)
    else:
        etat(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())

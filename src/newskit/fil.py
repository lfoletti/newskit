#!/usr/bin/env python3
"""Le fil de la page d'accueil (mainteneur, 03.11.2026 : « récupération fréquente des contenus. création des titres, courts
descriptifs, phrase complément, puis publication sur la page d'accueil »).

Après chaque collecte horaire (collecte_continue.py), les fiches neuves d'un graphe deviennent des billets du fil :
- fiches retenues : versées depuis le dernier passage du fil, publiées depuis FRAICHEUR jours au plus, d'actualité
  (pas une page de registre), rangées dans une rubrique du graphe ;
- un appel groupé : titre, descriptif et doublon, selon la convention du billet (docs/STE_ENQUETE.md, section 6) ;
- le complément : l'angle critique, au graphe seulement (sans web), fondu en dernière phrase au rendu ;
- plafond : FIL_USD_JOUR par jour, coupure stricte ; chaque appel a sa ligne au registre des coûts (poste « fil »).
Sortie : graphe/<graphe>/fil.json, par date de l'article puis heure d'entrée, du plus récent au plus ancien, fil_max billets (FIL_MAX par défaut), et son flux RSS fil.xml.

Usage :
    python3 -m newskit.fil [--graphe <graphe du site>] [--sans-modele]
    python3 -m newskit.fil [--graphe <graphe du site>] --avant-la-lettre     # avant la lettre du jour (routine)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import lettre as lt  # noqa: E402
from newskit import nouvelles as nv  # noqa: E402
from newskit import reglages  # noqa: E402 — les réglages du média

POSTE = "fil"
FRAICHEUR = 2  # jours : une fiche publiée avant n'entre pas au fil
FIL_USD_JOUR = 35.0  # plafond par jour, coupure stricte (5 $ jusqu'au 07.11.2026 ; mainteneur : « 35$ par jour ok »)
JOURS_SANS_PLAFOND = {
    "2026-11-04",
    "2026-11-05",
    "2026-11-06",
}  # mainteneur, 04.11, 05.11 et 06.11.2026 : « lève la limite aujourd'hui », « débloque le plafond aujourd'hui »
PAR_PASSAGE = 25  # billets rédigés par passage, au plus
FIL_MAX = 300  # défaut ; un graphe déclare le sien (fil_max, mainteneur, 11.11.2026)
MOTS_MIN = 120

CONSIGNE = """Tu écris les billets du fil d'actualité d'une veille sur {nom}, en français. Pour chaque fiche, rends
{{"n": numéro, "titre": ..., "texte": ..., "pertinent": vrai ou faux, "doublon_de": numéro d'un billet récent ou -1,
"developpement": ..., "rubrique": ...}}.
- pertinent : faux si la fiche ne porte pas une nouvelle pour un responsable de la gouvernance de l'IA (publicité,
  page d'accueil, entrefilet sans fait, sujet hors champ).
- titre : 16 mots et 90 caractères au plus, tiré de la source seule.
- texte : le chapeau du fait, de 30 à 70 mots, 4 phrases au plus, 2 nombres au plus, hors dates (ceux qui portent le
  fait).
- doublon_de : le numéro d'un billet récent (liste « récents ») qui porte le même événement : la même décision, la
  même publication, la même enquête, le même incident, même vu par une autre source et sous un autre angle ; sinon -1.
  Compare les faits (acteurs, nombres, dates), pas les titres. Lis d'abord les billets dont le numéro est dans
  « proches » : ils partagent des noms ou des nombres avec la fiche.
- developpement : vide, sauf quand doublon_de n'est pas -1 et que la fiche apporte un fait survenu après le billet
  récent (une décision prise, une réaction officielle datée, un chiffre publié ensuite) : ce fait, en une phrase. Le
  titre et le texte disent alors ce fait neuf d'abord. Une fiche qui redit le billet récent a un developpement vide.
- rubrique : celle du fait, exactement un de ces noms : {rubriques}.
Le titre et le texte suivent les règles de la lettre d'information, qui reprend ces billets :
{regles}Rends l'objet {{"billets": [...]}}.

Récents :
{recents}

Fiches :
{fiches}"""

# La relecture adverse des billets, titre et texte ensemble (mainteneur, 05.11.2026 : « certains titres sont difficiles à
# comprendre en français », « arme la relecture des titres et intègre-la au pipeline habituel » ; puis « une relecture
# adverse du texte des billets est nécessaire », « c'est à grouper avec la relecture du titre pour des questions de
# cohérence », « le titre et le texte de chaque billet doivent correspondre aux mêmes standards que les newsletter »).
# La lettre reprend ces billets : ils suivent ses règles (lt.REGLES) et passent son contrôle de forme, sans modèle
# (forme). Son contrôle de preuve, sans modèle, refuse les noms traduits (mesuré le 05.11.2026 : une phrase refusée dans
# 17 billets sur 77, à tort dans tous les cas lus) : ses refus sont des doutes (doutes), donnés à un relecteur adverse
# qui voit la source et tranche. Un billet refusé est réécrit une fois, titre et texte de la même main, et le relecteur
# juge la réécriture. Le champ `tenu` porte le verdict ; un passage : cinq appels au plus.
TITRE_MAX = 90
REECRITURES = 2  # réécritures d'un billet refusé, au plus ; la seconde seulement si le relecteur refuse la première
RELUS_PAR_PASSAGE = 25  # billets jugés par passage, au plus : les neufs, puis ceux d'avant qui n'ont pas de verdict
RELECTEUR = """Tu relis les billets d'un fil d'actualité sur {nom} avant leur parution, en relecteur adverse : tu cherches
ce qui ne tient pas. Le lecteur est un responsable en entreprise, francophone, cultivé, qui n'est ni juriste ni
ingénieur et ne connaît pas le sujet d'avance. Pour chaque billet tu reçois le titre, le texte, la source, et
« a_verifier » : les noms, les nombres et les dates qu'un contrôle automatique n'a pas lus tels quels dans la source.
Rends {{"n": numéro, "absents": [...], "clair": vrai ou faux, "motif": ..., "terme": ..., "pays": ...}}.
- absents : les noms, les nombres, les dates et les faits du titre ou du texte que la source ne dit pas ; liste vide si
  tout s'y lit. Un nom traduit et un nombre écrit en lettres se lisent dans la source (« the House » : « la Chambre » ;
  « seventy-one percent » : « 71 % » ; « Trento » : « Trente »). Le pays d'une institution que la source nomme se lit
  dans la source. Lis d'abord « a_verifier », puis le reste du billet.
- clair : lis d'abord le titre seul, sans le texte. Faux quand le titre ne se comprend pas seul, à la première
  lecture : il garde un mot anglais ou un mot de métier qu'un mot français courant remplace ; il commence par un nom
  que le lecteur ne connaît pas, sans dire qui c'est ; il est écrit en style télégraphique, sans verbe ; on ne sait pas
  qui fait quoi ; il se lit de deux façons. Faux aussi quand le titre et le texte ne disent pas le même fait, ou quand
  le texte garde un mot de métier que le lecteur ne comprend pas. Faux aussi quand
  la première phrase du texte dit seulement qu'une personne a parlé ou qu'un document a paru,
  ou donne le lieu, le format ou les intervenants, alors que la source donne un contenu : ce contenu doit venir en premier.
- motif : ce qui arrête le lecteur, en quinze mots au plus ; vide si le billet est clair.
- terme : la notion dont dépend le sens du titre, quand ce lecteur ne peut pas la connaître et que ni le billet ni la
  source ne l'explique : le nom d'une doctrine, d'une école de pensée, d'une notion religieuse ou savante, une
  expression d'une autre langue (« leadership prophétique »). Recopie-la telle que le billet l'écrit. Chaîne vide
  dans tous les autres cas, donc presque toujours : un mot que la presse économique emploie (déployeur, risque cyber,
  noyau Linux, prépublication), un texte de loi ou un règlement (AI Act, DORA), une organisation, une personne, un
  produit ne sont pas des termes à définir. Une notion que la source explique non plus : « clair » est alors faux, et
  le motif la nomme. Le terme rendu ici ne compte pas dans « clair » ni dans le motif : sa définition est cherchée
  dans une autre source, et le billet le garde.
- pays : le pays, ou l'ensemble de pays, où le fait se passe, quand le fait tient à un pays (une élection, une loi,
  une autorité, un tribunal, une administration, une étude menée dans un pays ou par une université de ce pays) et
  que ni le titre ni le texte ne le dit : ni son nom, ni un adjectif qui le dit (« américain », « fédéral
  allemand »). Écris son nom français, tel qu'il s'écrit après « en », « au » ou « aux » (États-Unis, Indonésie,
  Union européenne). Le pays d'une institution, d'un parlement, d'un parti ou d'une université que la source nomme
  se lit dans la source (« Congress », « the FTC » : États-Unis ; « Universitas Nurul Jadid (ID) » : Indonésie).
  Chaîne vide quand le billet dit déjà le pays, quand le fait ne tient à aucun pays (un produit, un modèle, une
  entreprise présente partout, une norme internationale, une recherche sans terrain ni institution nommée), ou quand
  la source ne permet pas de le dire. Un pays rendu ici est un reproche : le billet sera réécrit.
Rends l'objet {{"billets": [...]}}.

Billets :
{billets}"""
REECRITURE = """Tu réécris des billets d'un fil d'actualité sur {nom}, en français, d'après leur source seule, pour un
responsable en entreprise qui ne connaît pas le sujet d'avance. Pour chaque billet tu reçois le titre et le texte
refusés, les reproches du relecteur et la source. Rends {{"n": numéro, "titre": ..., "texte": ...}}.
- chaque reproche est corrigé ; ce qui n'est pas reproché reste tel quel ;
- le terme « a_definir » reste dans le billet, écrit de la même façon et sans explication : la source ne
  l'explique pas, et sa définition vient d'une autre source, après la réécriture ;
- un reproche « le billet ne dit pas où le fait se passe » : le pays nommé entre dans le titre ou dans la
  première phrase du texte (« aux États-Unis », « en Indonésie », « une université indonésienne ») ;
- un fait reproché comme absent de la source part ;
- titre : 16 mots et {max} caractères au plus ;
- texte : de 30 à 70 mots, 4 phrases au plus, 2 nombres au plus, hors dates.
Les règles de la lettre d'information, qui reprend ces billets :
{regles}Rends l'objet {{"billets": [...]}}.

Billets :
{billets}"""
SCHEMA_RELECTEUR = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "absents", "clair"],
                "properties": {
                    "n": {"type": "integer"},
                    "absents": {"type": "array", "items": {"type": "string"}},
                    "clair": {"type": "boolean"},
                    "motif": {"type": "string"},
                    "terme": {"type": "string"},
                    "pays": {"type": "string"},
                },
            },
        }
    },
}
SCHEMA_REECRITURE = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "titre", "texte"],
                "properties": {"n": {"type": "integer"}, "titre": {"type": "string"}, "texte": {"type": "string"}},
            },
        }
    },
}

SCHEMA = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "titre", "texte", "pertinent", "doublon_de"],
                "properties": {
                    "n": {"type": "integer"},
                    "titre": {"type": "string"},
                    "texte": {"type": "string"},
                    "pertinent": {"type": "boolean"},
                    "doublon_de": {"type": "integer"},
                    "developpement": {"type": "string"},
                    "rubrique": {"type": "string"},
                },
            },
        }
    },
}


def chemin_fil(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "fil.json"


SITE: str | None = None  # le site public de l'édition (registre, bloc `edition:`, clé `lettre_site`), lu à l'écriture


RSS_MAX = 50  # articles du flux, au plus (mainteneur, 06.11.2026 : « une limite à 50 items »)
RUBRIQUES_EN = {
    "Politique publique": "Public policy",
    "Conformité": "Compliance",
    "Éthique": "Ethics",
    "Sécurité": "Security",
    "En entreprise": "In business",
    "Secteurs": "Sectors",
}


def rss(F: dict, site: str, langue: str = "fr", graphe: str | None = None) -> str:
    """Le fil en flux RSS 2.0, un article par billet, du plus récent au plus ancien (mainteneur, 03.11.2026 : « possibilité
    de s'abonner au flux RSS »). La date de l'article : celle de son entrée au fil, à l'heure de Zurich. En anglais
    (mainteneur, 06.11.2026 : « fil rss anglais? ») : les seuls billets tenus en anglais (fil_anglais), sous /en/."""
    from email.utils import format_datetime  # noqa: PLC0415
    from xml.sax.saxutils import escape  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    import datetime as dt  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    zh = ZoneInfo("Europe/Zurich")
    racine = site.rstrip("/") + "/"
    items = []
    for b in F.get("billets") or []:
        if langue == "en":
            en = b.get("en") or {}
            if en.get("tenu") is not True:
                continue
            b = {
                **b,
                "titre": en["titre"],
                "descriptif": en["texte"],
                "definition": None,
                "rubrique": RUBRIQUES_EN.get(b.get("rubrique") or "", b.get("rubrique")),
            }
        if len(items) == RSS_MAX:
            break
        quand = dt.datetime.strptime(b["ajoute"], "%Y-%m-%d %H:%M").replace(tzinfo=zh)
        ident = (
            b.get("url")
            or f"{racine}#{b['ajoute'].replace(' ', 'T')}-{hashlib.sha1(b['titre'].encode()).hexdigest()[:10]}"
        )
        items.append(
            "<item>"
            f"<title>{escape(b['titre'])}</title>"
            + (f"<link>{escape(lien(b, langue)[0])}</link>" if b.get("url") else "")
            + f"<description>{escape(nv.texte(b))}</description>"
            + (f"<category>{escape(b['rubrique'])}</category>" if b.get("rubrique") else "")
            + f"<pubDate>{format_datetime(quand)}</pubDate>"
            f'<guid isPermaLink="{"true" if b.get("url") else "false"}">{escape(ident)}</guid>'
            "</item>"
        )
    langue = "en" if langue == "en" else "fr"
    c = canal(graphe or cfg.graphe_du_site(), langue)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{escape(c['titre'])}</title>"
        f"<link>{escape(racine)}{'en/' if langue == 'en' else ''}</link>"
        f"<description>{escape(c['description'])}</description><language>{langue}</language>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def canal(graphe: str, langue: str) -> dict:
    """Le titre et la description du flux d'un graphe dans une langue : ceux déclarés (`graphes.<g>.fil`), sinon le
    libellé du graphe, sans description."""
    try:
        d = cfg.graphe_declare(graphe)
    except Exception:  # noqa: BLE001 — un graphe non déclaré garde son nom
        d = {}
    return (d.get("fil") or {}).get(langue) or {"titre": d.get("libelle") or graphe, "description": ""}


def ecrire(graphe: str, F: dict) -> None:
    """Le fil (fil.json) et son flux RSS (fil.xml), côte à côte."""
    p = chemin_fil(graphe)
    p.write_text(json.dumps(F, ensure_ascii=False, indent=1), encoding="utf-8")
    p.with_name("fil.xml").write_text(rss(F, SITE or lt._edition()["site"], graphe=graphe), encoding="utf-8")
    p.with_name("fil_en.xml").write_text(rss(F, SITE or lt._edition()["site"], "en", graphe=graphe), encoding="utf-8")


def lire(graphe: str) -> dict:
    p = chemin_fil(graphe)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"billets": [], "vues": [], "depense": {}}


def candidates(graphe: str, vues: set[str], jour: str) -> list[dict]:
    dossiers = [str(d).rstrip("/") + "/" for d in cfg.dossiers_du_graphe(graphe)]
    sortie = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "sources/"],
        cwd=RACINE,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    sortie += subprocess.run(
        ["git", "log", "--since=2 days ago", "--diff-filter=A", "--name-only", "--format=", "--", "sources/"],
        cwd=RACINE,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    chemins = sorted({l.split()[-1] for l in sortie if l.strip().endswith(".md")})
    chemins = [
        c
        for c in chemins
        if c not in vues and not c.rsplit("/", 1)[-1].startswith("_") and any(c.startswith(d) for d in dossiers)
    ]
    fiches = lt.charger_fiches(chemins)
    rubs = cfg.rubriques(graphe)
    out = []
    for c, f in fiches.items():
        if not lt.recente(f, jour, FRAICHEUR) or lt.non_actualite(f) or (f.get("mots") or 9999) < MOTS_MIN:
            continue
        ok, r = admise(f, rubs)
        if ok:
            out.append({**f, "rubrique": r})
    return out


def admise(f: dict, rubs: list[dict]) -> tuple[bool, str | None]:
    """(entre au fil, rubrique du vocabulaire déclaré). Une fiche qui parle d'IA entre sans rubrique de vocabulaire :
    le modèle choisit la sienne (05.11.2026 : 70 fiches récentes sur 71 restaient hors du fil, faute de vocabulaire)."""
    r = lt.rubrique_majoritaire([f], rubs) if rubs else None
    if r or not rubs:
        return True, r
    return bool(lt.RE_IA.search(lt.plier(str(f.get("titre") or "")) + " " + lt.plier(lt._extrait(f, 300)))), None


def rubrique_finale(f: dict, rep: dict, rubs: list[dict]) -> str | None:
    """La rubrique du vocabulaire déclaré ; sinon celle du modèle, si elle est dans la liste du graphe."""
    if f.get("rubrique"):
        return f["rubrique"]
    r = str(rep.get("rubrique") or "").strip()
    return r if r in {x["nom"] for x in rubs} else None


# Toutes les langues, pas seulement les latines (mainteneur, 07.11.2026 : un billet sans sigle menait à une page en coréen ;
# « toutes les langues sont possibles, pas seulement les latines »). Une écriture propre à une langue la donne ; une
# écriture partagée se départage à ses lettres propres (arabe, persan, ourdou ; langues cyrilliques) ; l'alphabet
# latin, aux mots outils. `DETECTION` monte quand la reconnaissance change : un billet resté sans langue est relu une fois.
_ECRITURES = {
    "ko": "\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f",
    "ja": "\u3040-\u30ff",
    "zh": "\u4e00-\u9fff",
    "el": "\u0370-\u03ff",
    "he": "\u0590-\u05ff",
    "ar": "\u0600-\u06ff\u0750-\u077f",
    "cyr": "\u0400-\u04ff",
    "hi": "\u0900-\u097f",
    "bn": "\u0980-\u09ff",
    "pa": "\u0a00-\u0a7f",
    "gu": "\u0a80-\u0aff",
    "ta": "\u0b80-\u0bff",
    "te": "\u0c00-\u0c7f",
    "kn": "\u0c80-\u0cff",
    "ml": "\u0d00-\u0d7f",
    "si": "\u0d80-\u0dff",
    "th": "\u0e00-\u0e7f",
    "lo": "\u0e80-\u0eff",
    "my": "\u1000-\u109f",
    "ka": "\u10a0-\u10ff",
    "am": "\u1200-\u139f",
    "km": "\u1780-\u17ff",
    "hy": "\u0530-\u058f",
}
# Lettres propres, de la plus à la moins sûre : la première trouvée donne la langue.
_LETTRES_ARABE = (("ur", "ٹڈڑںےۓھ"), ("fa", "پچژگکی"))
_LETTRES_CYRILLIQUE = (
    ("be", "ў"),
    ("kk", "әғқңөұүһ"),
    ("uk", "іїєґ"),
    ("mk", "ѓќѕ"),
    ("sr", "ђјљњћџ"),
    ("ru", "ыэ"),
    ("bg", "ъ"),
)
# L'espagnol de reporter n'a ni « la » ni « de » (ses citations se départagent du français) : le fil prend le sien.
_MOTS_LATINS = {
    "es": "el la los las del de que y en por para con una es son se al lo como más su según entre",
    "pt": "o os as do da dos das que não para com uma um é são em no na se ao pelo pela mais e após",
    "pl": "i w z na nie się że do to jest od przez dla jak są oraz po ale który które",
    "sv": "och att det som en är av för med till på inte har den de ett om",
    "da": "og at det som en er af for med til på ikke har den de et om",
    "no": "og at det som en er av for med til på ikke har den de et om ikkje",
    "fi": "ja on ei se että oli ovat kun mutta myös tai joka sen hän",
    "cs": "a v se na je že s z do to jsou pro jako by které který ale",
    "sk": "a v sa na je že s z do to sú pre ako by ktoré ktorý ale",
    "ro": "și în de la cu pe un o este că nu se din pentru sunt care mai",
    "tr": "ve bir bu için ile da de olarak daha çok gibi ama kural",
    "hu": "az és hogy nem egy van meg már csak ez mint vagy volt lesz pedig",
    "id": "dan yang di ke dari ini itu untuk dengan tidak adalah pada akan",
    "ca": "els les per amb és són als aquest aquesta això també dels pel però",
    "hr": "i u je na se da za od su to ne kao iz ali koji",
    "vi": "và của các có là được những cho này trong một không với đã",
}
# Un mot de l'anglais courant ne compte pas pour une autre langue : « a », « to », « for » faisaient passer une page
# anglaise au portugais ou au tchèque.
_ANGLAIS_COURANT = set("a i an to do no on at for by in is it be as or so we he me my am us up go".split())
DETECTION = 4
SIGLES = sorted(
    {
        *(k for k in _ECRITURES if k != "cyr"),
        *(l for l, _ in _LETTRES_ARABE + _LETTRES_CYRILLIQUE),
        *_MOTS_LATINS,
        "fr",
        "en",
        "de",
        "it",
        "es",
        "nl",
    }
)


def _ecriture(t: str) -> str:
    """La langue d'un texte d'écriture non latine, vide s'il est surtout latin."""
    n = {l: len(re.findall(f"[{c}]", t)) for l, c in _ECRITURES.items()}
    if sum(n.values()) <= len(re.findall(r"[A-Za-zÀ-ɏ]", t)):
        return ""
    if n["ja"]:  # le japonais mêle kana et idéogrammes ; le chinois n'a pas de kana
        return "ja"
    l = max(n, key=n.get)
    propres = {"ar": _LETTRES_ARABE, "cyr": _LETTRES_CYRILLIQUE}.get(l)
    if propres:
        return next((x for x, lettres in propres if any(c in t for c in lettres)), "ar" if l == "ar" else "ru")
    return l


def langue_source(f: dict) -> str:
    """Le sigle de la langue d'origine de la source, l'une de `SIGLES` : à l'écriture de son titre et de son texte,
    ou aux mots outils pour l'alphabet latin ; vide quand elle ne se reconnaît pas (mainteneur, 05.11.2026 : « le
    sigle de la langue d'origine »)."""
    from newskit import reporter as rp  # noqa: PLC0415 — la table des mots outils par langue

    tete = f"{f.get('titre') or ''} {(f.get('texte') or '')[:4000]}"
    if e := _ecriture(tete):
        return e
    mots = set(
        re.findall(r"[^\W\d_]+", tete.lower())
    )  # mots distincts : un « e » répété 150 fois ne fait pas une langue
    tables = {**rp._MOTS_LANGUE, **{l: set(m.split()) - _ANGLAIS_COURANT for l, m in _MOTS_LATINS.items()}}
    score = {l: sum(m in ens for m in mots) for l, ens in tables.items()}
    premier, second = sorted(score, key=score.get, reverse=True)[:2]
    return premier if score[premier] >= 3 and score[premier] > score[second] else ""


# Un article publié en plusieurs langues (mainteneur, 07.11.2026 : « quand un blog publie un ticket en plusieurs langues,
# choisis la langue appropriée à mettre en lien ») : la page de la source déclare ses versions (`hreflang`), relevées
# une fois par billet ; chaque page du site met en lien la version dans sa langue, sinon l'anglaise, sinon l'originale.
VERSIONS_PAR_PASSAGE = 20  # pages lues par passage : les 186 billets d'avant se relèvent en une heure environ
RE_LINK = re.compile(r"<link\b[^>]*>", re.I)
RE_ATTR = re.compile(r"""([a-z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


def versions_de(html: str, base: str) -> dict[str, str]:
    """{langue: adresse} des versions déclarées par la page (`<link rel="alternate" hreflang>`) ; vide sinon."""
    from urllib.parse import urljoin  # noqa: PLC0415

    out: dict[str, str] = {}
    for balise in RE_LINK.findall(html or ""):
        a = {m[0].lower(): m[1] or m[2] or m[3] for m in RE_ATTR.findall(balise)}
        l = (a.get("hreflang") or "").split("-")[0].lower()
        if "alternate" not in (a.get("rel") or "").lower() or not re.fullmatch(r"[a-z]{2,3}", l) or not a.get("href"):
            continue
        url = urljoin(base, a["href"].strip())
        if url.startswith(("http://", "https://")):
            out.setdefault(l, url)
    return out


def lien(b: dict, page: str) -> tuple[str, str]:
    """(adresse, langue) de la source mise en lien sur la page de langue `page`."""
    v = b.get("versions") or {}
    for l in (page, "en"):
        if v.get(l):
            return v[l], l
    return b.get("url") or "", b.get("langue") or ""


def _lire_page(url: str) -> tuple[str, str]:
    from newskit import lecture_web as lw  # noqa: PLC0415

    return lw.telecharger(url)


def poser_versions(billets: list[dict], lire=None, plafond: int = VERSIONS_PAR_PASSAGE) -> int:
    """Les billets sans versions relevées reçoivent celles de leur page, `plafond` au plus par passage ; rend leur
    nombre. Une page refusée ou injoignable donne des versions vides : elle n'est pas relue."""
    lire = lire or _lire_page
    sans = [b for b in billets if "versions" not in b and b.get("url")][:plafond]
    for b in sans:
        html, _ = lire(b["url"])
        b["versions"] = versions_de(html, b["url"]) if html else {}
    return len(sans)


def poser_langues(billets: list[dict], charger=None) -> int:
    """Les billets écrits avant la reconnaissance en vigueur (`DETECTION`) reçoivent leur langue ; rend leur nombre.
    Un billet dont la fiche est partie garde la langue qu'il avait, vide s'il n'en avait pas, et n'est plus relu."""
    sans = [b for b in billets if b.get("detection") != DETECTION]
    fiches = (charger or lt.charger_fiches)([b["chemin"] for b in sans if b.get("chemin")]) if sans else {}
    for b in sans:
        f = fiches.get(b.get("chemin"))
        b["langue"], b["detection"] = langue_source(f) if f else b.get("langue", ""), DETECTION
    return len(sans)


def classer(billets: list[dict]) -> list[dict]:
    """Le fil par date de l'article, puis par heure d'entrée pour les articles du même jour (mainteneur, 05.11.2026 :
    « classe par date de l'article »). Rangé par heure d'entrée seule, un article ancien rédigé tard coiffait les
    nouvelles du jour. Un billet sans date va en fin de liste."""
    return sorted(billets, key=lambda b: (str(b.get("date") or ""), str(b.get("ajoute") or "")), reverse=True)


def extrait(f: dict, n: int | None = 1200) -> str:
    try:
        t = (RACINE / f["chemin"]).read_text(encoding="utf-8")
    except OSError:
        return ""
    t = t.split("---", 2)[-1] if t.startswith("---") else t
    return " ".join(t.split())[:n]


def depense_du_jour(F: dict, jour: str) -> float:
    return float((F.get("depense") or {}).get(jour) or 0)


def demander(inv: str, schema: dict, graphe: str, note: str, modele: str | None = None) -> tuple[dict, float]:
    """Un appel au modèle sans outil, réponse au schéma, ligne au registre des coûts ; rend l'objet et son coût. Une
    réponse illisible rend un objet vide."""
    args = [
        *couts.CLAUDE_P,
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        modele or lt.MODELE,
        "--json-schema",
        json.dumps(schema),
    ]
    t0 = time.time()
    r = subprocess.run(args, input=inv, capture_output=True, text=True, timeout=900, cwd=RACINE, check=False)
    env = couts.enregistrer(POSTE, graphe, modele or lt.MODELE, r, time.time() - t0, note=note)
    try:
        brut = env.get("structured_output") or json.loads(
            re.sub(r"^```(?:json)?\s*|\s*```$", "", str(env.get("result") or "{}").strip()) or "{}"
        )
    except ValueError:
        brut = {}
    return (brut if isinstance(brut, dict) else {}), float(env.get("total_cost_usd") or 0)


def _source_de(f: dict) -> str:
    return lt._sources_de({"fiches": [f["chemin"]]}, {f["chemin"]: f})


def forme(titre: str, texte: str, f: dict) -> list[str]:
    """Sans modèle : les écarts d'un billet aux règles de forme de la lettre. Le texte est un chapeau de la une
    (fenêtre de mots, phrases, nombres, groupe nominal, tournure, vocabulaire de la preuve, sigle, formule générique,
    chiffre revendiqué) ; le titre a sa longueur, pas de question, et les mêmes règles."""
    src = _source_de(f)
    ec = lt.ecarts_de_forme(texte, lt.MOTS_MIN, lt.MOTS_MAX, lt.UNE_PHRASES, lt.UNE_NOMBRES)
    sigle = lt.sigle_non_developpe(texte, set())
    if sigle:
        ec.append(f"sigle non développé : {sigle}")
    generique = lt.formule_generique(texte, src)
    if generique:
        ec.append(generique)
    if lt.source_revendiquee(f) and lt.chiffre_sans_attribution(texte):
        ec.append("chiffre revendiqué sans attribution")
    if len(titre) > TITRE_MAX:
        ec.append(f"titre de {len(titre)} caractères, {TITRE_MAX} au plus")
    if "?" in titre:
        ec.append("titre en question")
    du_titre = [
        *lt.ecarts_de_forme(titre, 1, lt.MOTS_TITRE, lt.PHRASES_PAR_PARAGRAPHE, None),
        lt.formule_generique(titre, src),
        lt.refus_periphrase(titre, lt.personne_nommee(texte, src)),
    ]
    return ec + [f"titre : {e}" for e in du_titre if e]


def doutes(titre: str, texte: str, f: dict) -> list[str]:
    """Sans modèle : ce que le contrôle de preuve de la lettre ne lit pas dans la source (noms, sigles, nombres,
    dates), le titre puis chaque phrase lue seule. Un nom traduit y tombe à tort : le relecteur tranche."""
    src = _source_de(f)
    return sorted(
        {
            d
            for ph in [titre, *lt.phrases(texte)]
            for d in (lt.faute_de_preuve(ph, src).removeprefix("absent des sources : ").split(", "))
            if d
        }
    )


def terme_du_billet(terme, titre: str, texte: str) -> str:
    """Le terme à définir que le relecteur nomme, gardé s'il se lit dans le titre ou le texte du billet."""
    t = " ".join(str(terme or "").split())
    return t if t and lt.plier(t) in lt.plier(f"{titre} {texte}") else ""


def juger(
    paires: list[tuple[str, str, dict, dict]], graphe: str, source: int | None = 3000
) -> tuple[dict[int, dict], float]:
    """Le relecteur adverse sur des (titre, texte, billet, fiche) : rend {rang: {absents, clair, motif, terme}} et le
    coût. `terme` : la notion que le lecteur ne connaît pas et que la source n'explique pas. Un rang sans réponse n'a pas de
    verdict."""
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    v, cout = demander(
        RELECTEUR.format(
            nom=nom,
            billets="\n".join(
                json.dumps(
                    {"n": n, "titre": t, "texte": x, "a_verifier": doutes(t, x, f), "source": extrait(b, source)},
                    ensure_ascii=False,
                )
                for n, (t, x, b, f) in enumerate(paires)
            ),
        ),
        SCHEMA_RELECTEUR,
        graphe,
        "relecteur",
    )
    verdicts = {}
    for x in v.get("billets") or []:
        if isinstance(x.get("n"), int) and 0 <= x["n"] < len(paires):
            verdicts[x["n"]] = {
                "absents": [a for a in (" ".join(str(a).split()) for a in x.get("absents") or []) if a],
                "clair": x.get("clair") is not False,
                "motif": " ".join(str(x.get("motif") or "").split()),
                "terme": terme_du_billet(x.get("terme"), *paires[x["n"]][:2]),
                "pays": " ".join(str(x.get("pays") or "").split()),
            }
    return verdicts, cout


def pays_manquant(verdict: dict) -> list[str]:
    """Le reproche d'un billet qui ne dit pas où son fait se passe (N15) : le pays que le relecteur nomme."""
    return [f"le billet ne dit pas où le fait se passe : {verdict['pays']}"] if verdict.get("pays") else []


JOURNAL_REFUS = RACINE / "cache" / "fil_refus.jsonl"  # chaque billet que la relecture n'a pas tenu, avec ses essais


def journaliser_refus(graphe: str, refus: list[dict]) -> None:
    """Chaque billet non tenu, avec chaque essai (titre, texte, reproches), dans cache/fil_refus.jsonl : un billet neuf
    refusé ne paraît pas, et le journal permet d'y revenir (mainteneur, 05.11.2026). Sa fiche est dans `vues` du registre ;
    l'en retirer la fait relire. Jamais bloquant."""
    if not refus:
        return
    try:
        JOURNAL_REFUS.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_REFUS.open("a", encoding="utf-8") as fh:
            for r in refus:
                fh.write(json.dumps({"quand": maintenant(), "graphe": graphe, **r}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  fil : refus non journalisés ({e})", file=sys.stderr)


def relire(billets: list[dict], graphe: str) -> float:
    """La relecture adverse d'un lot, titre et texte ensemble ; rend son coût. Un billet refusé (écart de forme, fait
    absent de la source, titre ou texte qui arrête le lecteur) est réécrit, et le relecteur juge la réécriture ; une
    réécriture qu'il refuse est reprise, REECRITURES fois en tout. Reste un titre et un texte de la même main, parmi
    les essais que le relecteur tient : le moins chargé en écarts de forme, le plus récent à égalité (« ne pas publier
    sans description, même si elle n'est pas totalement conforme », règle de la lettre). Chaque billet jugé reçoit
    `tenu` ; l'ancien titre, l'ancien texte et les reproches restent au billet (`relecture`), les écarts gardés aussi
    (`ecarts`), et la notion que le lecteur ne connaît pas et que la source n'explique pas (`terme` : sa définition
    se cherche ailleurs, la réécriture la garde). Non tenu : ses reproches sont dans `ecarts`, et ses essais au journal des refus. Un billet sans verdict
    reste à relire."""
    fiches = lt.charger_fiches([b["chemin"] for b in billets])
    lot = [(b, fiches[b["chemin"]]) for b in billets if b["chemin"] in fiches]
    refus = []
    for b in billets:
        if b["chemin"] not in fiches:
            b["tenu"], b["ecarts"] = False, ["fiche illisible"]
            refus.append(
                {
                    "chemin": b["chemin"],
                    "url": b.get("url") or "",
                    "essais": [{"titre": b["titre"], "texte": b["descriptif"], "reproches": ["fiche illisible"]}],
                }
            )
    journaliser_refus(graphe, refus)
    if not lot:
        return 0.0
    v1, cout = juger([(b["titre"], b["descriptif"], b, f) for b, f in lot], graphe)
    essais = {n: [(lot[n][0]["titre"], lot[n][0]["descriptif"], x)] for n, x in v1.items()}

    def reproches(n: int, essai: int = -1) -> list[str]:
        titre, texte, x = essais[n][essai]
        r = [*forme(titre, texte, lot[n][1]), *(f"absent de la source : {a}" for a in x["absents"])]
        return r + ([x["motif"] or "peu clair"] if not x["clair"] else []) + pays_manquant(x)

    premiers = {n: reproches(n) for n in essais}
    a_reecrire, sans_verdict = {n: r for n, r in premiers.items() if r}, set()
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    for _ in range(REECRITURES):
        if not a_reecrire:
            break
        r, c = demander(
            REECRITURE.format(
                nom=nom,
                max=TITRE_MAX,
                regles=lt.regles(graphe),
                billets="\n".join(
                    json.dumps(
                        {
                            "n": n,
                            "titre": essais[n][-1][0],
                            "texte": essais[n][-1][1],
                            "reproches": m,
                            **({"a_definir": essais[n][-1][2]["terme"]} if essais[n][-1][2].get("terme") else {}),
                            "source": extrait(lot[n][0], 3000),
                        },
                        ensure_ascii=False,
                    )
                    for n, m in a_reecrire.items()
                ),
            ),
            SCHEMA_REECRITURE,
            graphe,
            "réécriture",
        )
        cout += c
        neufs = {}
        for x in r.get("billets") or []:
            titre, texte = " ".join(str(x.get("titre") or "").split()), " ".join(str(x.get("texte") or "").split())
            if x.get("n") in a_reecrire and titre and texte:
                neufs[x["n"]] = (titre, texte)
        if not neufs:
            break
        rangs = sorted(neufs)
        v, c = juger([(*neufs[n], *lot[n]) for n in rangs], graphe)
        cout += c
        a_reecrire = {}
        for k, n in enumerate(rangs):
            if k not in v:
                sans_verdict.add(n)  # réécrit, sans verdict : à relire au passage suivant
                continue
            essais[n].append((*neufs[n], v[k]))
            if v[k]["absents"] or not v[k]["clair"] or v[k]["pays"]:
                a_reecrire[n] = reproches(n)
    changes = non_tenus = 0
    for n in essais:
        b, f = lot[n]
        if n in sans_verdict:
            continue
        b.pop("ecarts", None)
        mains = [(t, x, v) for t, x, v in reversed(essais[n]) if v["clair"] and not v["absents"]]
        if not mains:
            b["tenu"], b["ecarts"] = False, premiers[n]
            non_tenus += 1
            journaliser_refus(
                graphe,
                [
                    {
                        "chemin": b["chemin"],
                        "url": b.get("url") or "",
                        "essais": [
                            {"titre": t, "texte": x, "reproches": reproches(n, k)}
                            for k, (t, x, _) in enumerate(essais[n])
                        ],
                    }
                ],
            )
            continue
        # La main située d'abord (N15), puis la moins chargée en écarts de forme ; un pays manquant ne perd pas le billet.
        titre, texte, verdict = min(mains, key=lambda m: (bool(m[2]["pays"]), len(forme(m[0], m[1], f))))
        ecarts = forme(titre, texte, f) + pays_manquant(verdict)
        if (titre, texte) != (b["titre"], b["descriptif"]):
            b["relecture"] = {"avant": b["titre"], "texte_avant": b["descriptif"], "motif": " ; ".join(premiers[n])}
            b["titre"], b["descriptif"] = titre, texte
            changes += 1
        b["tenu"] = True
        if ecarts:
            b["ecarts"] = ecarts
        terme = terme_du_billet(verdict.get("terme"), titre, texte)  # celui de la main gardée
        if terme != (b.get("terme") or ""):  # un autre terme : à définir de nouveau
            b.pop("definition", None)
            b.pop("defini", None)
            b.pop("essais_definition", None)
        if terme:
            b["terme"] = terme
        else:
            b.pop("terme", None)
    print(
        f"relecture : {len(v1)} billet(s) jugé(s), {len([r for r in premiers.values() if r])} refusé(s), "
        f"{changes} réécrit(s), {non_tenus} non tenu(s)"
    )
    return cout


# La révision des titres (mainteneur, 9.11.2026 : « l'expression des titres est toujours médiocre... on manque du sens
# commun qui fait qu'un titre fonctionne ou pas ») : trois candidats, puis un juge qui ne voit pas le rédacteur raisonner.
SENS_COMMUN = (
    "Un titre fonctionne quand un lecteur cultivé, qui ne connaît pas le sujet, comprend d'un coup ce qui s'est passé et "
    "pourquoi cela compte, comme devant le titre d'un quotidien français. Il dit le fait, pas qui le rapporte : "
    "l'attribution va dans le texte, sauf quand la source fait elle-même la nouvelle (une autorité ou une personne que le "
    "lecteur connaît). Il nomme seulement les acteurs que le lecteur connaît ; un inconnu se désigne par sa qualité ou "
    "disparaît du titre. Il écrit « agent », jamais « agent d'IA ». Des mots courants, sujet, verbe, complément, sans "
    "calque de l'anglais. Le conditionnel pour un fait que la source ne présente pas comme établi. Rien qui ne soit dans "
    "la source ; pas de question ; {max} caractères au plus."
)
CANDIDATS = (
    "Tu révises les titres d'un fil d'actualité sur {nom}. {sens}\nPour chaque billet, propose trois titres "
    "différents, d'après le texte et la source, qui fonctionnent mieux que le titre en place. Rends "
    '{{"titres": [{{"n": rang, "candidats": [trois titres]}}]}}.\n\nBillets :\n{billets}'
)
JUGE = (
    "Tu choisis les titres d'un fil d'actualité sur {nom}. {sens}\nPour chaque billet, voici le titre en place [0] et "
    "trois propositions [1] à [3]. Choisis celui qui fonctionne le mieux pour le lecteur ; à égalité, garde [0]. Rends "
    '{{"titres": [{{"n": rang, "choix": 0 à 3, "raison": une phrase}}]}}.\n\nBillets :\n{billets}'
)


def _schema_titres(champs: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "titres": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}, **champs},
                    "required": ["n", *champs],
                },
            }
        },
        "required": ["titres"],
    }


SCHEMA_CANDIDATS = _schema_titres({"candidats": {"type": "array", "items": {"type": "string"}}})
SCHEMA_JUGE = _schema_titres({"choix": {"type": "integer"}, "raison": {"type": "string"}})


def titre_refuse(titre: str, source: str) -> str:
    """Sans modèle : pourquoi un titre proposé ne peut pas remplacer le titre en place, ou une chaîne vide."""
    if len(titre) > TITRE_MAX:
        return f"titre de {len(titre)} caractères, {TITRE_MAX} au plus"
    if "?" in titre:
        return "titre en question"
    return lt.faute_de_preuve(titre, source)


def reviser_titres(billets: list[dict], graphe: str) -> float:
    """Trois titres proposés par billet, puis un juge choisit entre le titre en place et les propositions ; rend le coût.
    Le titre choisi remplace le titre en place s'il passe les contrôles sans modèle ; le billet garde la révision
    (`revision_titre` : l'ancien titre, les candidats, le choix, la raison, le refus éventuel)."""
    fiches = lt.charger_fiches([b["chemin"] for b in billets])
    lot = [(b, fiches[b["chemin"]]) for b in billets if b["chemin"] in fiches]
    if not lot:
        return 0.0
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    sens = SENS_COMMUN.format(max=TITRE_MAX)
    srcs = [_source_de(f)[:3000] for _, f in lot]
    r1, cout = demander(
        CANDIDATS.format(
            nom=nom,
            sens=sens,
            billets="\n".join(
                json.dumps(
                    {"n": n, "titre": b["titre"], "texte": b["descriptif"], "source": srcs[n]}, ensure_ascii=False
                )
                for n, (b, _) in enumerate(lot)
            ),
        ),
        SCHEMA_CANDIDATS,
        graphe,
        "titres : candidats",
    )
    cands = {}
    for x in r1.get("titres") or []:
        c = [" ".join(str(t).split()) for t in x.get("candidats") or []]
        if isinstance(x.get("n"), int) and 0 <= x["n"] < len(lot) and len(c) == 3 and all(c):
            cands[x["n"]] = c
    if not cands:
        return cout
    r2, c2 = demander(
        JUGE.format(
            nom=nom,
            sens=sens,
            billets="\n".join(
                json.dumps(
                    {
                        "n": n,
                        "titres": "\n".join(f"[{i}] {t}" for i, t in enumerate([lot[n][0]["titre"], *c])),
                        "texte": lot[n][0]["descriptif"],
                        "source": srcs[n],
                    },
                    ensure_ascii=False,
                )
                for n, c in cands.items()
            ),
        ),
        SCHEMA_JUGE,
        graphe,
        "titres : juge",
    )
    for x in r2.get("titres") or []:
        n, k = x.get("n"), x.get("choix")
        if n not in cands or not isinstance(k, int) or not 0 <= k <= 3:
            continue
        b = lot[n][0]
        titre = [b["titre"], *cands[n]][k]
        rev = {
            "avant": b["titre"],
            "candidats": cands[n],
            "choix": k,
            "raison": " ".join(str(x.get("raison") or "").split()),
        }
        refus = titre_refuse(titre, srcs[n]) if k else ""
        if refus:
            rev["refus"] = refus
        else:
            b["titre"] = titre
        b["revision_titre"] = rev
    return cout + c2


def rediger(lot: list[dict], recents: list[dict], graphe: str) -> tuple[list[dict], float]:
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    inv = CONSIGNE.format(
        nom=nom,
        regles=lt.regles(graphe),
        rubriques=", ".join(r["nom"] for r in cfg.rubriques(graphe) or []) or "(aucune)",
        recents="\n".join(
            f"{i}. {b['titre']} — {str(b.get('descriptif') or '')[:RECENT_MAX]}" for i, b in enumerate(recents)
        )
        or "(aucun)",
        fiches="\n".join(
            json.dumps(
                {
                    "n": i,
                    "editeur": f.get("editeur"),
                    "date": f.get("date"),
                    "titre": f["titre"],
                    "proches": f.get("proches") or [],
                    "source": extrait(f),
                },
                ensure_ascii=False,
            )
            for i, f in enumerate(lot)
        ),
    )
    brut, cout = demander(inv, SCHEMA, graphe, "titres")
    return brut.get("billets") or [], cout


# Le même événement (CHANTIER_NOUVELLES.md, N3 et N4 ; mainteneur, 05.11.2026 : « on ne devrait pas lire la même nouvelle deux
# fois, sauf s'il y a un développement nouveau. dans ce cas le développement devrait faire l'objet de l'actualité »).
# Le 05.11.2026 l'enquête de DNSFilter faisait trois billets : le rédacteur ne voyait que les titres des 60 billets
# récents, et aucun ne nommait DNSFilter. La signature (nouvelles.signature) propose les nouvelles proches, le
# rédacteur lit leur descriptif et décide.
RECENTS = 60  # les nouvelles récentes que le rédacteur voit toujours
RECENT_MAX = 400  # caractères du descriptif d'une nouvelle récente dans la consigne
PAIRES_PAR_PASSAGE = 60  # paires de nouvelles jugées par passage, au rattrapage
RAPPROCHEMENT = """Tu compares des nouvelles d'un fil d'actualité sur {nom}. Chaque paire porte deux nouvelles : « a »,
entrée la première, et « b ». Pour chaque paire rends {{"n": numéro, "lien": "meme" ou "suite" ou "autre",
"developpement": ...}}.
- meme : a et b portent le même événement (la même décision, la même publication, la même enquête, le même incident),
  même vu par deux sources et sous deux angles, et b n'apporte aucun fait survenu après a.
- suite : b porte le même événement et un fait survenu après a (une décision prise, une réaction officielle datée, un
  chiffre publié ensuite). developpement : ce fait, en une phrase.
- autre : deux événements distincts, même s'ils ont un acteur, un éditeur ou un thème en commun.
Compare les faits (acteurs, nombres, dates), pas les titres.
Rends l'objet {{"paires": [...]}}.

Paires :
{paires}"""
SCHEMA_RAPPROCHEMENT = {
    "type": "object",
    "required": ["paires"],
    "properties": {
        "paires": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "lien"],
                "properties": {
                    "n": {"type": "integer"},
                    "lien": {"type": "string", "enum": ["meme", "suite", "autre"]},
                    "developpement": {"type": "string"},
                },
            },
        }
    },
}


# --- La liaison : les nouvelles nées du même événement de fond (N9) ---------------------------------------------------
# Mainteneur, 05.11.2026, sur trois éléments de la lettre nés des élections américaines : « oui pour le regroupement ». Ce
# sont des nouvelles distinctes (« autre » au rapprochement) ; le registre note qu'elles partagent un contexte, et la
# lettre les range sous la plus forte. Mesuré le même jour sur les 78 nouvelles du registre : deux groupes.
LIEES_PAR_PASSAGE = 40  # nouvelles comparées aux autres par passage, au plus
CONTEXTE_JOURS = 8  # les autres nouvelles de la liste : celles des 8 derniers jours (la fraîcheur de la lettre)
TEXTE_LIAISON = 400  # caractères du texte d'une nouvelle à lier ; les autres donnent leur titre
LIAISON = """Tu ranges les nouvelles d'une veille sur « {nom} ». Voici les nouvelles récentes, une par ligne (JSON : n, date,
titre ; le texte d'une nouvelle à lier ; le nom de l'événement d'une nouvelle déjà liée). Pour chaque nouvelle marquée
"a_lier": true, dis si elle est née du même événement de fond qu'une ou plusieurs autres nouvelles de la liste.

Un événement de fond est un fait daté et nommable : une élection, un texte de loi précis, un procès, un incident, une
annonce d'une entreprise, un rapport précis. Deux nouvelles liées sont deux faits distincts que le même événement a
fait naître (le résultat d'un vote et une réaction à ce vote).

Ne lie pas deux nouvelles qui partagent seulement un thème (les centres de données, la sécurité des agents, le droit
d'auteur), un pays, une institution ou un éditeur. Dans le doute, ne lie pas.

Rends l'objet {{"nouvelles": [{{"n": …, "evenement": "le nom de l'événement, daté si possible, ou une chaîne vide",
"avec": [numéros des nouvelles liées, liste vide si aucune]}}]}}, une entrée par nouvelle à lier.

{nouvelles}
"""
SCHEMA_LIAISON = {
    "type": "object",
    "required": ["nouvelles"],
    "properties": {
        "nouvelles": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "evenement", "avec"],
                "properties": {
                    "n": {"type": "integer"},
                    "evenement": {"type": "string"},
                    "avec": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}


def a_lier(F: dict) -> list[dict]:
    """Les nouvelles du registre pas encore comparées aux autres, dans l'ordre du registre."""
    return [b for b in nv.toutes(F) if not b.get("contexte_juge")]


def lier(F: dict, graphe: str) -> float:
    """Les nouvelles nées du même événement de fond reçoivent le même `contexte` (l'identifiant du groupe : le chemin
    de sa première nouvelle) et le nom de cet `evenement`. Un appel par passage, pour LIEES_PAR_PASSAGE nouvelles au
    plus, parmi celles des CONTEXTE_JOURS derniers jours ; une nouvelle est comparée une fois (`contexte_juge`). Un
    lien sans événement nommé n'est pas pris. Sans réponse du modèle, les nouvelles restent à lier. Rend le coût."""
    cibles = a_lier(F)[:LIEES_PAR_PASSAGE]
    if not cibles:
        return 0.0
    import datetime as dt  # noqa: PLC0415

    debut = (dt.date.fromisoformat(maintenant()[:10]) - dt.timedelta(days=CONTEXTE_JOURS)).isoformat()
    a_dire = {id(b) for b in cibles}
    liste = [b for b in nv.toutes(F) if id(b) in a_dire or str(b.get("date") or "")[:10] >= debut]

    def ligne(n: int, b: dict) -> str:
        x = {"n": n, "date": str(b.get("date") or "")[:10], "titre": b["titre"]}
        if id(b) in a_dire:
            x.update(texte=str(b.get("descriptif") or "")[:TEXTE_LIAISON], a_lier=True)
        elif b.get("evenement"):
            x["evenement"] = b["evenement"]
        return json.dumps(x, ensure_ascii=False)

    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    r, cout = demander(
        LIAISON.format(nom=nom, nouvelles="\n".join(ligne(n, b) for n, b in enumerate(liste))),
        SCHEMA_LIAISON,
        graphe,
        "liaison",
    )
    if "nouvelles" not in r:
        return cout
    lies = 0
    for x in r["nouvelles"] or []:
        if not isinstance(x, dict) or not isinstance(x.get("n"), int) or not 0 <= x["n"] < len(liste):
            continue
        b, evenement = liste[x["n"]], " ".join(str(x.get("evenement") or "").split())
        avec = [
            liste[m]
            for m in dict.fromkeys(m for m in x.get("avec") or [] if isinstance(m, int) and 0 <= m < len(liste))
            if liste[m] is not b
        ]
        if id(b) not in a_dire or not evenement or not avec:
            continue
        membres = sorted([b, *avec], key=lambda m: next(i for i, y in enumerate(liste) if y is m))
        tete = next((m for m in membres if m.get("contexte")), None)
        groupe, nom_ = (
            (tete["contexte"], tete.get("evenement") or evenement) if tete else (membres[0]["chemin"], evenement)
        )
        anciens = {m["contexte"] for m in membres if m.get("contexte")} - {groupe}
        for m in [*membres, *(y for y in nv.toutes(F) if y.get("contexte") in anciens)]:
            m["contexte"], m["evenement"] = groupe, nom_
        lies += 1
    for b in cibles:
        b["contexte_juge"] = True
    print(f"liaison : {len(cibles)} nouvelle(s) comparée(s), {lies} liée(s)")
    return cout


def textes_du_registre(F: dict) -> dict[str, str]:
    """{chemin d'une nouvelle: tout ce que le registre en sait} : son titre, son descriptif, son complément, et le
    titre et le texte de ses fiches."""
    toutes = nv.toutes(F)
    fiches = lt.charger_fiches(sorted({c for b in toutes for c in nv.fiches_de(b)}))
    return {
        b["chemin"]: ". ".join(
            [
                *(str(b.get(k) or "") for k in ("titre", "descriptif", "lecture", "developpement")),
                *(
                    f"{fiches[c].get('titre') or ''}. {str(fiches[c].get('texte') or '')[:6000]}"
                    for c in nv.fiches_de(b)
                    if c in fiches
                ),
            ]
        )
        for b in toutes
    }


def proposer(lot: list[dict], F: dict, recents: list[dict], textes: dict[str, str]) -> None:
    """Chaque fiche du lot reçoit `proches` : les numéros, dans `recents`, des nouvelles qui partagent avec elle des noms
    ou des nombres rares. Une nouvelle proche plus ancienne que les récentes s'ajoute à `recents`."""
    signatures = {c: nv.signature(t) for c, t in textes.items()}
    par_chemin = {b["chemin"]: b for b in nv.toutes(F)}
    for f in lot:
        f["proches"] = []
        for c in nv.proches(nv.signature(f"{f.get('titre') or ''}. {str(f.get('texte') or '')[:6000]}"), signatures):
            i = next((i for i, x in enumerate(recents) if x is par_chemin[c]), None)
            if i is None:
                recents.append(par_chemin[c])
                i = len(recents) - 1
            f["proches"].append(i)


def _entree(b: dict) -> str:
    return str(b.get("redige") or b.get("ajoute") or "")


def paires_a_juger(F: dict, textes: dict[str, str]) -> list[tuple[dict, dict]]:
    """Les paires (a, b) de nouvelles proches dont une au moins n'est pas encore rapprochée ; a est entrée la première.
    Une nouvelle et sa suite sont déjà liées, et une paire jugée « autre » est notée (`comparee_a`) : elles ne
    reviennent pas."""
    par_chemin = {b["chemin"]: b for b in nv.toutes(F)}
    signatures = {c: nv.signature(textes.get(c, "")) for c in par_chemin}
    paires = {}
    for c, b in par_chemin.items():
        if b.get("rapproche"):
            continue
        for d in nv.proches(signatures[c], {k: v for k, v in signatures.items() if k != c}):
            x, y = sorted((b, par_chemin[d]), key=lambda n: (_entree(n), n["chemin"]))
            if (
                x.get("suite_de") != y["chemin"]
                and y.get("suite_de") != x["chemin"]
                and x["chemin"] not in (y.get("comparee_a") or [])
            ):
                paires[x["chemin"], y["chemin"]] = (x, y)
    return [paires[k] for k in sorted(paires, key=lambda k: (_entree(paires[k][0]), _entree(paires[k][1]), k))]


def rapprocher(F: dict, graphe: str) -> float:
    """Le rattrapage : les nouvelles du registre qui n'ont pas encore été rapprochées sont comparées à leurs proches,
    PAIRES_PAR_PASSAGE paires par passage. « meme » : la nouvelle entrée la seconde se fond dans la première. « suite » :
    elle devient sa suite, si son développement porte un fait neuf ; sinon elle se fond. « autre » : la paire est notée.
    Une nouvelle est rapprochée quand il ne lui reste aucune paire à juger. Rend le coût."""
    textes = textes_du_registre(F)
    paires = paires_a_juger(F, textes)
    lot, cout, liens = paires[:PAIRES_PAR_PASSAGE], 0.0, {}
    if lot:
        nom = cfg.graphe_declare(graphe).get("libelle") or graphe
        r, cout = demander(
            RAPPROCHEMENT.format(
                nom=nom,
                paires="\n".join(
                    json.dumps(
                        {
                            "n": n,
                            **{
                                cle: {
                                    "titre": x["titre"],
                                    "date": x.get("date"),
                                    "descriptif": x.get("descriptif") or "",
                                }
                                for cle, x in (("a", a), ("b", b))
                            },
                        },
                        ensure_ascii=False,
                    )
                    for n, (a, b) in enumerate(lot)
                ),
            ),
            SCHEMA_RAPPROCHEMENT,
            graphe,
            "rapprochement",
        )
        liens = {x.get("n"): x for x in r.get("paires") or [] if x.get("lien") in ("meme", "suite", "autre")}
    devenue, fondues, suites = {}, 0, 0
    for n, (a, b) in enumerate(lot):
        a, b = devenue.get(id(a), a), devenue.get(id(b), b)
        lien = (liens.get(n) or {}).get("lien")
        if a is b or not lien:
            continue
        dev = " ".join(str(liens[n].get("developpement") or "").split())
        if lien == "suite" and dev and nv.fait_neuf(dev, textes.get(a["chemin"], "")) and not b.get("suite_de"):
            b["suite_de"], b["developpement"] = a["chemin"], dev
            suites += 1
        elif lien == "autre" or b.get("suite_de"):  # deux événements, ou la suite d'un autre : la paire est jugée
            b.setdefault("comparee_a", []).append(a["chemin"])
        else:
            nv.fusionner(F, a, b)
            devenue[id(b)] = a
            fondues += 1
    restent = {id(n) for p in paires_a_juger(F, textes) for n in p}
    for b in nv.toutes(F):
        if id(b) not in restent:
            b["rapproche"] = True
    print(
        f"rapprochement : {len(lot)} paire(s) jugée(s) sur {len(paires)}, {fondues} nouvelle(s) fondue(s), {suites} suite(s)"
    )
    return cout


def completer(billets: list[dict], graphe: str, reste: float) -> float:
    from newskit import angle_critique as ac  # noqa: PLC0415
    from newskit import reporter as rp  # noqa: PLC0415

    # Un jour sans plafond, le reste est infini : le budget du passage reste fini, au prix d'un complément par billet
    # (05.11.2026 : `--max-budget-usd inf` était refusé et les billets paraissaient sans complément).
    reste = min(reste, len(billets) * float(cfg.reporter(graphe)["angle_usd_billet"]))
    budget = rp.Budget(recherches=0, pages=0, usd=reste, lettre_usd=reste)
    chercher = ac.graphe_du(graphe)
    for b in billets:
        if budget.reste_usd() < 0.05:
            break
        f = lt.charger_fiches([b["chemin"]]).get(b["chemin"])
        if not f:
            continue
        try:
            a = ac.angle(
                {"titre": b["titre"], "chapeau": b["descriptif"], "fiche": f}, graphe, budget, chercher, web=False
            )
        except Exception as e:  # noqa: BLE001 — un angle manqué ne bloque pas le fil
            print(f"  angle : {type(e).__name__} sur {b['chemin']}", file=sys.stderr)
            continue
        if a.get("lecture"):
            b["lecture"] = a["lecture"]
    return budget.depense


# La définition d'un terme (mainteneur, 05.11.2026, sur « leadership prophétique » à la une de l'essai : « c'est un sujet très
# intéressant mais il mérite des compléments », « ok pour la proposition »). Le relecteur avait reproché des termes
# inexpliqués ; la réécriture, tenue à la source seule, les avait retirés, et le terme du titre restait sans
# explication. Le terme que le relecteur nomme (`terme`) reçoit une définition cherchée hors de la source du
# billet : le graphe d'abord, le web ensuite, par la marche de l'angle critique. Une phrase, qui nomme le terme,
# prouvée par une citation retrouvée mot pour mot dans une autre source (`definition` : texte, preuves). `defini` dit
# que la recherche est faite, `essais_definition` compte les recherches coupées ; sans définition, la lettre garde la nouvelle en brève (nouvelles.a_completer).
DEFINIS_PAR_PASSAGE = 5  # nouvelles dont le terme est cherché par passage, au plus
DEFINITION_USD = 0.80  # par nouvelle : une écriture d'après le graphe, la recherche web, une écriture d'après les
#                              pages, une relance. Mesuré le 05.11.2026 sur « leadership prophétique » : le budget d'un
#                              angle (0,40 $) coupe la marche avant la seconde écriture.
ESSAIS_DEFINITION = 2  # recherches coupées (plafond, erreur du modèle) qu'une nouvelle peut payer, au plus


def invite_definition(billet: dict, sources: dict[int, dict], graphe_txt: str, raison: str = "") -> str:
    from newskit import angle_critique as ac  # noqa: PLC0415

    relance = (
        f"Ta première réponse a été refusée par le contrôle : {raison}. Réécris-la en tenant chaque règle.\n\n"
        if raison
        else ""
    )
    return (
        relance + "Tu définis un terme pour le lecteur d'un billet d'actualité en français, d'après les seules "
        "sources numérotées. Le lecteur est un responsable en entreprise, cultivé, qui ne connaît pas ce terme. "
        "La source du billet (source 1) emploie le terme sans l'expliquer : la définition vient d'une autre "
        "source.\n"
        f"Écris une seule phrase de {ac.MOTS_LECTURE} mots au plus, qui nomme le terme et dit ce qu'il désigne et "
        "d'où il vient (la discipline, la tradition, le texte ou l'auteur), sans libellé ni ouverture, sans "
        "adjectif d'évaluation. Chaque fait de la phrase a sa preuve : le numéro de sa source et une citation de 6 à 14 mots, "
        "copiée mot pour mot de cette source, dans sa langue. Chaque nombre, date et nom de la phrase figure dans "
        "une citation. Laisse « questions » vide.\n"
        "Si aucune autre source que la source 1 ne définit le terme, rends une lecture vide et aucune preuve : "
        "n'écris rien de mémoire.\n"
        "Un terme peut avoir plusieurs sens. Garde celui de la source 1 : sa tradition, sa discipline, son pays, les "
        "mots qui l'entourent. Si les autres sources le définissent dans un autre sens, rends une lecture vide et "
        "aucune preuve.\n"
        "La phrase est en français : un mot d'une autre langue se traduit, il ne se recopie pas.\n\n"
        f"=== TERME À DÉFINIR\n« {billet['terme']} »\n\n"
        f"=== BILLET\n{billet.get('titre') or ''}\n{billet.get('chapeau') or ''}\n\n"
        f"=== SOURCES\n{ac._sources_texte(sources)}\n\n=== GRAPHE (passages)\n{graphe_txt}\n"
    )


def invite_web_definition(billet: dict, recherches: int, pages: int) -> str:
    return (
        f"Cherche sur le web, en {recherches} recherches au plus, la définition du terme suivant : ce qu'il désigne "
        f"et d'où il vient. Rends au plus {pages} adresses de pages qui le définissent (encyclopédie, article de "
        "recherche, institution), en français si de telles pages existent, sans les résumer.\n\n"
        f"Terme : « {billet['terme']} »\nIl est employé dans ce billet :\n"
        f"{billet.get('titre') or ''}\n{billet.get('chapeau') or ''}\n\n"
        "La source du billet l'emploie dans un sens précis : cherche la définition de ce sens (sa tradition, sa "
        "discipline, son pays), pas un autre emploi des mêmes mots.\n"
        f"Source du billet : {_source_du_billet(billet, SOURCE_RECHERCHE)}\n"
    )


SOURCE_RECHERCHE = 1500  # caractères de la source donnés à la recherche d'une définition
SOURCE_SENS = 3000  # caractères de la source donnés au vérificateur du sens
SCHEMA_SENS = {
    "type": "object",
    "properties": {"meme_sens": {"type": "boolean"}, "motif": {"type": "string"}},
    "required": ["meme_sens", "motif"],
}
VERIFICATEUR_SENS = (
    "Tu vérifies la définition d'un terme, écrite pour un billet d'actualité d'après d'autres pages que la source du "
    "billet. La source emploie le terme dans un sens précis : sa tradition, sa discipline, son pays, les mots qui "
    "l'entourent.\n"
    "Rends « meme_sens » vrai seulement si la définition décrit le terme dans le sens où la source l'emploie. Rends "
    "faux si elle le rattache à une autre tradition, une autre religion, une autre discipline ou un autre pays que "
    "ceux de la source, si elle contredit un indice de la source, ou si le terme a plusieurs sens et que la source ne "
    "permet pas de choisir. « motif » : une phrase qui nomme l'indice de la source et l'écart ; vide quand le sens "
    "est le même.\n\n"
    "=== TERME\n« {terme} »\n\n=== DÉFINITION\n{definition}\n\n=== CITATIONS DE LA DÉFINITION\n{citations}\n\n"
    "=== SOURCE DU BILLET\n{source}\n"
)


def _source_du_billet(billet: dict, n: int) -> str:
    f = billet.get("fiche") or {}
    return f"{f.get('titre') or ''} ({f.get('editeur') or ''})\n{' '.join(str(f.get('texte') or '').split())[:n]}"


def sens_de_la_source(billet: dict, a: dict, budget, graphe: str) -> tuple[bool | None, str]:
    """Le vérificateur adverse d'une définition : (vrai, "") si elle décrit le terme dans le sens où la source du
    billet l'emploie, (faux, motif) sinon, (None, "") sans réponse du modèle. Une définition d'un autre sens (une
    autre tradition, un homonyme) est une erreur publiée : elle n'entre pas au billet."""
    from newskit import reporter as rp  # noqa: PLC0415

    citations = "\n".join(f"« {p.get('citation') or ''} » ({p.get('url') or ''})" for p in a.get("preuves") or [])
    r = rp.appeler(
        VERIFICATEUR_SENS.format(
            terme=billet["terme"],
            definition=a["lecture"],
            citations=citations,
            source=_source_du_billet(billet, SOURCE_SENS),
        ),
        SCHEMA_SENS,
        budget,
        graphe,
        "définition : sens",
        poste=POSTE,
    )
    if r is None:
        return None, ""
    return r.get("meme_sens") is True, " ".join(str(r.get("motif") or "").split())


def controler_definition(r: dict, sources: dict[int, dict], chapeau: str | None = None, terme: str = "") -> str:
    """Pourquoi une définition ne tient pas le contrôle sans modèle (vide : elle tient) : celui de l'angle critique
    (une phrase, 25 mots, chaque citation retrouvée dans sa source, noms et nombres prouvés), puis une preuve au moins
    hors de la source du billet, et le terme nommé dans la phrase."""
    from newskit import angle_critique as ac  # noqa: PLC0415

    raison = ac.controler(r, sources)
    if raison:
        return raison
    if all(p.get("source") == 1 for p in r.get("preuves") or []):
        return "la définition n'a de preuve que dans la source du billet (source 1) : elle vient d'une autre source"
    if terme and lt.plier(terme) not in lt.plier(r.get("lecture") or ""):
        return "la phrase ne nomme pas le terme à définir"
    return ""


def a_definir(F: dict) -> list[dict]:
    """Les nouvelles du registre dont le terme attend sa définition : jamais cherchée."""
    return [b for b in nv.toutes(F) if b.get("terme") and "defini" not in b]


def definir(F: dict, graphe: str, reste: float) -> float:
    """La définition du terme que la source n'explique pas, pour DEFINIS_PAR_PASSAGE nouvelles au plus ; rend le
    coût. Le budget d'une nouvelle : DEFINITION_USD, dans `reste`, avec les recherches et les pages d'un angle (bloc
    `reporter:` du registre). Une définition trouvée passe le vérificateur du sens (sens_de_la_source) : d'un autre sens
    que celui de la source, elle est écartée ; sans verdict, la recherche compte pour coupée. Une recherche faite pose
    `defini` (vrai : `definition` est au billet). Une recherche
    coupée (plafond, limite d'usage, erreur du modèle) laisse la nouvelle à définir au passage suivant ; coupée
    ESSAIS_DEFINITION fois après une dépense, elle est tenue pour faite, sans définition."""
    cibles = a_definir(F)[:DEFINIS_PAR_PASSAGE]
    if not cibles or reste < 0.05:
        return 0.0
    from newskit import angle_critique as ac  # noqa: PLC0415
    from newskit import reporter as rp  # noqa: PLC0415

    bornes = cfg.reporter(graphe)
    reste = min(reste, len(cibles) * DEFINITION_USD)
    fiches = lt.charger_fiches([b["chemin"] for b in cibles])
    chercher = ac.graphe_du(graphe)

    def au_graphe(billet: dict, graphe_: str) -> dict:  # le graphe cherché sur le terme, pas sur le titre
        return chercher({"fiche": {**billet["fiche"], "titre": billet["terme"]}}, graphe_)

    depense, trouvees = 0.0, 0
    for b in cibles:
        f = fiches.get(b["chemin"])
        budget = rp.Budget(
            recherches=int(bornes["angle_recherches"]),
            pages=int(bornes["angle_pages"]),
            usd=DEFINITION_USD,
            lettre_usd=reste,
            depense_lettre=depense,
            ecartes=bornes.get("domaines_ecartes") or (),
        )
        if not f or budget.reste_usd() < 0.05:
            continue
        billet, erreur = {"titre": b["titre"], "chapeau": b["descriptif"], "fiche": f, "terme": b["terme"]}, False
        try:
            a = ac.angle(
                billet,
                graphe,
                budget,
                au_graphe,
                invite_=invite_definition,
                invite_web_=invite_web_definition,
                controle=lambda r, sources, chapeau, t=b["terme"]: controler_definition(r, sources, chapeau, t),
            )
        except Exception as e:  # noqa: BLE001 — une définition manquée ne bloque pas le fil
            print(f"  définition : {type(e).__name__} sur {b['chemin']}", file=sys.stderr)
            a, erreur = {}, True
        if a.get("lecture"):  # N14 : le sens de la source, ou pas de définition
            sens, motif = sens_de_la_source(billet, a, budget, graphe)
            if sens is not True:
                print(f"  définition écartée ({motif or 'sens non vérifié'}) : {b['chemin']}", file=sys.stderr)
                a, erreur = {}, erreur or sens is None
        depense += budget.depense
        if a.get("lecture"):
            b["definition"], trouvees = {"texte": a["lecture"], "preuves": a["preuves"]}, trouvees + 1
        coupee = erreur or bool(budget.notes)
        essais = b.get("essais_definition", 0) + (1 if coupee and budget.depense > 0 else 0)
        if a.get("lecture") or not coupee or essais >= ESSAIS_DEFINITION:
            b["defini"] = bool(a.get("lecture"))
            b.pop("essais_definition", None)
        elif essais:
            b["essais_definition"] = essais
    print(f"définition : {len(cibles)} nouvelle(s) au terme non expliqué, {trouvees} définie(s)")
    return depense


# La pile (mainteneur, 05.11.2026 : « le fil ne devrait jamais s'arrêter, quitte à mettre au point un mécanisme de pile et de
# delay pour combler le manque pendant le verrouillage »). Un billet rédigé attend dans `pile` ; chaque passage en fait
# paraître selon une cadence, routine du graphe en cours ou non. Le 05.11.2026 : 39 billets d'un coup à 07 h, puis
# rien de 08:15 à la fin de la routine, qui arrête la lecture des flux.
PILE_DELAI = 10  # minutes entre deux parutions, tant que la pile est basse
PILE_HORIZON = 180  # minutes : une pile haute paraît plus vite, pour être vide dans ce délai
PILE_PAR_PASSAGE = 3  # parutions par passage, au plus
PANNEAU = 15  # billets que montre le panneau de l'accueil (const PANNEAU de la page)
# La réserve (mainteneur, 05.11.2026 : « les news devraient constituer un buffer un peu avant la mise à jour quotidienne, de
# manière à passer l'intervalle sans discontinuité des publications »). Avant la routine du graphe, les parutions
# s'espacent et la pile se remplit ; pendant que la routine tient le verrou, la pile s'étale jusqu'à sa fin prévue.
PILE_RESERVE = 120  # minutes avant l'heure de la routine où la réserve se fait
PILE_DELAI_RESERVE = 30  # minutes entre deux parutions pendant ce temps
PILE_RESTE_MIN = 30  # minutes qu'une routine en cours a toujours devant elle, pour l'étalement
# Le fil continu (mainteneur, 11.11.2026 : « je ne veux plus lire "tel plafond a été atteint, c'est pourquoi plus rien ne
# paraît depuis 2 heures" » ; un billet au moins toutes les 30 minutes de 06 h à 23 h). La pile garde une réserve de
# PILE_GARDE billets contre les heures sans nouvelle : en dessous, un billet par PILE_GARANTIE minutes le jour, aucun la
# nuit, où la réserve se refait. Le plus récent paraît d'abord : la réserve garde les plus anciens.
PILE_GARDE = 6
PILE_GARANTIE = 30
JOUR_GARANTI = (6, 23)  # heures de début et de fin de la garantie


def maintenant() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def delai_de_parution(n: int) -> float:
    """Les minutes entre deux parutions pour une pile de `n` billets."""
    return min(PILE_DELAI, PILE_HORIZON / max(1, n))


def _instant(t: str):
    import datetime as dt  # noqa: PLC0415

    return dt.datetime.strptime(t, "%Y-%m-%d %H:%M")


def cadence(n: int, quand: str, heure_routine: str, minutes_routine: int, en_routine: bool, passee: bool) -> float:
    """Les minutes entre deux parutions à l'instant `quand`, pour une pile de `n` billets. Pendant la routine du graphe
    (`en_routine`) : la pile s'étale jusqu'à la fin prévue. Avant elle, tant que le passage du jour n'est pas fait
    (`passee`) : une parution par PILE_DELAI_RESERVE, jusqu'à une réserve qui couvre la routine. Sinon : la cadence de
    la pile."""
    import datetime as dt  # noqa: PLC0415

    d, t = delai_de_parution(n), _instant(quand)
    debut = t.replace(hour=int(heure_routine[:2]), minute=int(heure_routine[3:5]))
    fin = debut + dt.timedelta(minutes=minutes_routine)
    if en_routine:
        return max(d, max(PILE_RESTE_MIN, (fin - t).total_seconds() / 60) / max(1, n))
    if not passee and debut - dt.timedelta(minutes=PILE_RESERVE) <= t < fin and n < minutes_routine / PILE_DELAI:
        return max(d, PILE_DELAI_RESERVE)
    return d


def de_jour(quand: str) -> bool:
    """Vrai pendant les heures de la garantie du fil (JOUR_GARANTI)."""
    return JOUR_GARANTI[0] <= _instant(quand).hour < JOUR_GARANTI[1]


def garder(n: int, quand: str, d: float) -> float:
    """La cadence `d`, réserve de la pile comprise : au-dessus de PILE_GARDE billets, `d` ; en dessous, PILE_GARANTIE
    minutes le jour, aucune parution la nuit (math.inf)."""
    if n > PILE_GARDE:
        return d
    return max(d, PILE_GARANTIE) if de_jour(quand) else math.inf


def cadence_du_moment(graphe: str, n: int, quand: str) -> float:
    """La cadence à cet instant, d'après le registre (`reporter.fil_routine_heure`, `fil_routine_minutes`), le verrou des
    routines et le dernier passage du graphe. Pendant la routine du graphe, l'étalement prime sur la réserve."""
    from newskit import routine_hebdo as rh  # noqa: PLC0415

    r = cfg.reporter(graphe)
    en_routine = rh.graphe_en_routine() == graphe
    d = cadence(
        n,
        quand,
        str(r["fil_routine_heure"]),
        int(r["fil_routine_minutes"]),
        en_routine,
        str(rh.debut_du_passage(graphe) or "")[:10] == quand[:10],
    )
    return d if en_routine else garder(n, quand, d)


def fil_max(graphe: str) -> int:
    """Le nombre de billets que garde le fil du graphe : fil_max de sa déclaration, ou FIL_MAX."""
    return int(cfg.graphe_declare(graphe).get("fil_max") or FIL_MAX)


def liberer(F: dict, quand: str, delai: float | None = None, garde: int = FIL_MAX) -> int:
    """Fait paraître les billets de la pile dus à l'instant `quand` ; rend leur nombre. Le plus récent d'abord (date de
    l'article, puis heure de rédaction). `delai` : les minutes entre deux parutions (défaut : la cadence de la pile).
    À dix minutes ou plus : un billet à la fois, jamais de rafale après un silence. Le billet paru prend l'heure de sa
    parution (`ajoute`) et garde celle de sa rédaction (`redige`). `garde` : le nombre de billets du fil."""
    fond = arriere_plan(F, quand, garde)
    pile = F.get("pile") or []
    if not pile:
        return fond
    d, n = delai or delai_de_parution(len(pile)), 1
    if d == math.inf:  # la nuit, la réserve se refait
        return fond
    if F.get("paru_le"):
        n = int((_instant(quand) - _instant(F["paru_le"])).total_seconds() / 60 // d)
        n = min(n, 1 if d >= PILE_DELAI else PILE_PAR_PASSAGE)
    if n <= 0:
        return fond
    pile = classer(pile)
    sortis, F["pile"] = pile[:n], pile[n:]
    _entrer(F, sortis, quand, garde)
    F["paru_le"] = quand
    return fond + len(sortis)


def _entrer(F: dict, sortis: list[dict], quand: str, garde: int = FIL_MAX) -> None:
    """Les billets `sortis` entrent au fil à l'heure `quand` (`ajoute`), avec l'heure de leur rédaction (`redige`)."""
    for b in sortis:
        b["redige"], b["ajoute"] = b.get("ajoute") or quand, quand
    tous = classer(sortis + (F.get("billets") or []))
    F["billets"] = tous[:garde]
    # Un billet qui sort du fil garde son résumé : le refus d'un auteur le lit (refus.py, mainteneur, 06.11.2026).
    for x in tous[garde:]:
        F.setdefault("archive", {})[x["chemin"]] = {k: x[k] for k in ("titre", "descriptif", "en") if x.get(k)}


def arriere_plan(F: dict, quand: str, garde: int = FIL_MAX) -> int:
    """Les billets de la pile qui se rangeraient après le dernier du panneau (article d'une date antérieure) entrent au
    fil tout de suite, sans prendre de tour : la cadence ne sert qu'aux billets que le panneau montre (mainteneur, 06.11.2026 :
    « les autres (qui ne paraissent pas dans le fil) sont ajoutées en background »). Rend leur nombre."""
    billets = classer(F.get("billets") or [])
    if len(billets) < PANNEAU or not F.get("pile"):
        return 0
    seuil = str(billets[PANNEAU - 1].get("date") or "")
    fond = [b for b in F["pile"] if str(b.get("date") or "") < seuil]
    if fond:
        F["pile"] = [b for b in F["pile"] if str(b.get("date") or "") >= seuil]
        _entrer(F, fond, quand, garde)
    return len(fond)


def paraitre(graphe: str, F: dict) -> int:
    """Le pas de la pile à chaque passage du fil : rend le nombre de billets parus."""
    if not F.get("pile"):
        return 0
    from newskit import fil_fiche  # noqa: PLC0415 — fil_fiche importe ce module

    quand = maintenant()
    attente = fil_fiche.en_attente(F, graphe, quand)
    F["pile"] = [b for b in F["pile"] if not any(b is x for x in attente)]
    n = liberer(F, quand, cadence_du_moment(graphe, len(F["pile"]), quand), fil_max(graphe))
    F["pile"] = F["pile"] + attente
    return n


def enrichir(b: dict) -> None:
    """Le titre, le résumé et le complément du fil entrent dans l'en-tête de la fiche (titre_fr, resume_fr,
    complement_fr) ; extraire_squelette les porte au nœud de l'article (mainteneur, 03.11.2026 : « enrichissement du graphe
    avec les courts résumés »). Le corps de la fiche n'est pas touché."""
    from newskit import fiche  # noqa: PLC0415

    p = RACINE / b["chemin"]
    try:
        champs, corps = fiche.lire_fichier(p)
    except OSError:
        return
    champs.update({"titre_fr": b["titre"], "resume_fr": b["descriptif"]})
    if b.get("lecture"):
        champs["complement_fr"] = b["lecture"]
    try:
        fiche.ecrire(p, champs, corps, strict=False)
    except Exception as e:  # noqa: BLE001 — l'en-tête reste tel quel, le billet paraît quand même
        print(f"  en-tête : {type(e).__name__} sur {b['chemin']}", file=sys.stderr)


def a_relire(F: dict) -> list[dict]:
    """Les billets, en attente ou parus, que la relecture n'a pas encore jugés (champ `tenu`)."""
    return [b for b in F["pile"] + F["billets"] if "tenu" not in b]


PART_SECONDAIRE = 0.8  # part du plafond du jour au-delà de laquelle seules la rédaction et l'anglais tournent


def budget_secondaire(F: dict, graphe: str, jour: str) -> float:
    """Le budget du jour qui reste aux tâches secondaires (rapprochement, relecture des billets d'avant, définitions,
    liens, fiches) : elles s'arrêtent à PART_SECONDAIRE du plafond, la rédaction des billets neufs et leur version
    anglaise continuent jusqu'au plafond (mainteneur, 11.11.2026)."""
    return max(0.0, PART_SECONDAIRE * plafond(jour, graphe) - depense_du_jour(F, jour))


def plafond(jour: str, graphe: str) -> float:
    """Le plafond du jour : celui du graphe (fil_usd_jour) ou FIL_USD_JOUR, sauf un jour levé par le mainteneur."""
    if jour in JOURS_SANS_PLAFOND:
        return float("inf")
    return float(cfg.graphe_declare(graphe).get("fil_usd_jour") or FIL_USD_JOUR)


def billets_anglais_a_ecrire(F: dict) -> list:
    """Les billets à écrire en anglais (fil_anglais.a_ecrire), seulement si le média se traduit en anglais
    (`media.traductions` de config.yaml, 07.11.2026) ; aucun sinon, et aucun appel au modèle."""
    from newskit import media  # noqa: PLC0415

    return fil_anglais().a_ecrire(F) if "en" in media.traductions() else []


def fil_anglais():
    from newskit import fil_anglais as fa  # noqa: PLC0415 — fil_anglais importe fil

    return fa


def passer(graphe: str, sans_modele: bool = False, hors_plafond: bool = False) -> int:
    """Un passage du fil. Rend le nombre de fiches candidates restées non lues. `hors_plafond` : la lecture des fiches
    ne s'arrête pas au plafond du jour (lire_le_reste) ; les compléments restent sous le plafond."""
    jour = time.strftime("%Y-%m-%d")
    F = lire(graphe)
    vues = set(F.get("vues") or [])
    F.setdefault("pile", [])
    change = False
    if not sans_modele:  # les billets d'avant : leur langue, et le fil rangé par date de l'article
        poses, ranges = poser_langues(F["billets"] + F["pile"]), classer(F["billets"])
        poses += poser_versions(F["billets"] + F["pile"])
        change = bool(poses or ranges != F["billets"])
        F["billets"] = ranges
        parus = paraitre(graphe, F)  # la pile avance à chaque passage, fiche neuve ou non
        change = change or bool(parus)
        change = bool(nv.rattacher(F, lt.charger_fiches)) or change  # les attaches d'avant reçoivent leur chemin
    cands = sorted(candidates(graphe, vues, jour), key=lambda f: f.get("date") or "", reverse=True)
    anciens = [] if sans_modele else a_relire(F)
    a_rapprocher = not sans_modele and any(not b.get("rapproche") for b in nv.toutes(F))
    sans_lien = a_lier(F)
    sans_definition = a_definir(F)
    sans_anglais = [] if sans_modele else billets_anglais_a_ecrire(F)
    from newskit import fil_fiche  # noqa: PLC0415 — le texte long de la fiche (mainteneur, 06.11.2026)

    sans_fiche = [] if sans_modele else fil_fiche.a_ecrire(F)
    etat = "" if sans_modele else f", {parus} paru(s), {len(F['pile'])} en attente"
    if sans_modele or not (
        cands or anciens or a_rapprocher or sans_lien or sans_definition or sans_anglais or sans_fiche
    ):
        for f in cands[:40]:
            print(" ", f.get("date"), f.get("rubrique"), f["titre"][:90])
        if change:
            ecrire(graphe, F)
        print(f"{len(cands)} fiche(s) candidate(s)" + etat)
        return len(cands)
    print(f"{len(cands)} fiche(s) candidate(s)" + (f", {len(anciens)} billet(s) d'avant à relire" if anciens else ""))
    reste = plafond(jour, graphe) - depense_du_jour(F, jour)
    if (reste < 0.10 and not hors_plafond) or couts.en_limite():
        if change:
            ecrire(graphe, F)
        print(f"plafond du jour atteint ({plafond(jour, graphe)} $) ou limite d'usage : rien de rédigé" + etat)
        return len(cands)
    lot = cands[:PAR_PASSAGE]
    rubs = cfg.rubriques(graphe)
    secondaire = budget_secondaire(F, graphe, jour)
    cout = rapprocher(F, graphe) if a_rapprocher and secondaire >= 0.10 else 0.0  # le même événement se fond
    anciens = a_relire(F) if secondaire - cout >= 0.10 else []  # une nouvelle fondue n'est plus à relire
    recents = F["pile"] + F["billets"][:RECENTS]  # la pile aussi : un doublon s'ajoute au billet qui attend
    textes = textes_du_registre(F) if lot else {}
    proposer(lot, F, recents, textes)
    reps, cout_r = rediger(lot, recents, graphe) if lot else ([], 0.0)
    cout += cout_r
    neufs = []
    for r in reps:
        if not (0 <= r.get("n", -1) < len(lot)):
            continue
        f = lot[r["n"]]
        if not r.get("pertinent") or not r.get("texte"):
            continue
        k, suite = r.get("doublon_de", -1), {}
        if 0 <= k < len(recents):  # le même événement : une source de plus, ou un développement
            dev = " ".join(str(r.get("developpement") or "").split())
            if not (dev and nv.fait_neuf(dev, textes.get(recents[k]["chemin"], ""))):
                nv.attacher(recents[k], f)
                continue
            suite = {"suite_de": recents[k]["chemin"], "developpement": dev}
        rubrique = rubrique_finale(f, r, rubs)
        if rubs and not rubrique:  # hors des rubriques du graphe : pas au fil
            continue
        neufs.append(
            {
                "titre": r["titre"],
                "descriptif": r["texte"],
                "rubrique": rubrique,
                "chemin": f["chemin"],
                "url": f["url"],
                "source": lt._source(f),
                "date": f.get("date"),
                "langue": langue_source(f),
                "detection": DETECTION,
                "ajoute": maintenant(),
                "lecture": "",
                "rapproche": True,
                **suite,
            }
        )
    anciens = anciens[: max(0, RELUS_PAR_PASSAGE - len(neufs))]
    cout += relire(neufs + anciens, graphe)  # avant le complément : il part du billet relu
    neufs = [b for b in neufs if b.get("tenu") is not False]  # sans titre ni texte qui tienne : pas de billet
    cout += reviser_titres(neufs, graphe)  # trois titres, puis un juge (mainteneur, 9.11.2026)
    cout += completer(neufs, graphe, max(0.0, reste - cout))
    for b in neufs + [x for x in anciens if x.get("relecture")]:  # le billet relu entre à la fiche, donc au graphe
        enrichir(b)
    F["pile"] = neufs + F["pile"]  # un billet rédigé attend son tour

    def sec() -> float:  # les tâches secondaires, sous PART_SECONDAIRE du plafond
        return max(0.0, min(reste, secondaire) - cout)

    cout += definir(F, graphe, sec())  # le terme que la source n'explique pas
    cout += lier(F, graphe) if sec() >= 0.10 else 0.0  # les billets neufs avec ceux du registre
    # La version anglaise, d'après la source, jusqu'au plafond comme la rédaction : sans elle, le billet ne paraît pas
    # (publier_fil.aligne ; mainteneur, 11.11.2026, option A).
    cout += fil_anglais().anglais(F, graphe, max(0.0, reste - cout))
    cout += fil_fiche.etoffer(F, graphe, sec())  # le texte long de la fiche, d'après la source
    parus += paraitre(graphe, F)
    # Lue : le modèle a répondu sur la fiche. Une fiche sans réponse (appel échoué) reste à lire : « lue sans
    # nouvelle » veut dire « écartée » pour la lettre (nouvelles.ecartees).
    lues = {lot[r["n"]]["chemin"] for r in reps if 0 <= r.get("n", -1) < len(lot)}
    F["vues"] = sorted(vues | lues)[-5000:]
    F.setdefault("depense", {})[jour] = depense_du_jour(F, jour) + cout
    ecrire(graphe, F)
    print(
        f"{len(neufs)} billet(s) rédigé(s), {len(lot) - len(neufs)} écartée(s) ou fondue(s), {parus} paru(s), "
        f"{len(F['pile'])} en attente, {cout:.2f} $ ({F['depense'][jour]:.2f} $ aujourd'hui)"
    )
    return len(cands) - len(lues)


PASSAGES_AVANT_LETTRE = 20  # passages du fil avant la lettre du jour, au plus (PAR_PASSAGE fiches chacun)
VERROUS = RACINE / "cache"


@contextlib.contextmanager
def verrou(graphe: str):
    """Un passage du fil à la fois par graphe : la collecte continue et la routine, avant la lettre, lisent puis
    écrivent le même fil.json. Le second attend la fin du premier."""
    import fcntl  # noqa: PLC0415

    VERROUS.mkdir(parents=True, exist_ok=True)
    with open(VERROUS / f"fil_{graphe}.verrou", "a+", encoding="utf-8") as v:
        fcntl.flock(v, fcntl.LOCK_EX)
        yield


def lire_le_reste(graphe: str) -> int:
    """Avant la lettre du jour, le fil lit les fiches candidates qui restent, hors plafond du jour (mainteneur, 05.11.2026 :
    « ok pour le plafond, écris-le ») : la lettre prend au registre le titre, la rubrique et les redites de chaque
    nouvelle. La dépense compte dans le jour : le plafond borne le reste de la journée. Rend le nombre de fiches
    restées non lues (limite d'usage, appel échoué, ou plus de PASSAGES_AVANT_LETTRE passages)."""
    reste = None
    for _ in range(PASSAGES_AVANT_LETTRE):
        avant, reste = reste, passer(graphe, hors_plafond=True)
        if reste == 0 or (avant is not None and reste >= avant):
            break
    print(f"avant la lettre : {reste} fiche(s) candidate(s) non lue(s)")
    return reste


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.graphe_du_site())
    ap.add_argument("--sans-modele", action="store_true", help="dire ce qui entrerait, sans appel")
    ap.add_argument(
        "--avant-la-lettre",
        action="store_true",
        help="lire toutes les fiches candidates qui restent, hors plafond du jour",
    )
    return ap.parse_args(argv)


def main() -> int:
    a = arguments()
    with verrou(a.graphe):
        if a.avant_la_lettre:
            lire_le_reste(a.graphe)
        else:
            passer(a.graphe, a.sans_modele)
    return 0


reglages.appliquer(__name__, globals())  # les réglages du média (reglages.py)


if __name__ == "__main__":
    sys.exit(main())

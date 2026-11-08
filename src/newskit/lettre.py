#!/usr/bin/env python3
"""La lettre de la semaine d'un graphe : le courriel des résultats d'une routine, lu comme une lettre (B16, 03.11.2026).

« C'est à considérer plus comme une newsletter que comme un rapport de machine » (mainteneur, 03.11.2026). En tête,
les faits saillants de la semaine, de un à sept, sans titre de section ; puis « Autres faits saillants » (les notions
majeures, une ligne de contexte chacune), les brèves en fin de leur rubrique (03.11.2026) et « Coulisses » à la fin : état du
graphe, banc, coût et santé du passage, seul lieu du vocabulaire de machine. Le lecteur ne voit jamais le mot
« fiche » : il lit « source ».

Tout est calculé sans modèle (note de travail B16, section 3), à partir du dernier passage de `nouveautes.json` (les
notions neuves et les fiches versées, commit B16 « comptes justes »), des fiches versées sur disque et du rapport :

- une notion est classée si elle n'est ni générique, ni un nom d'éditeur, ni un nom de personne ;
  S = 3·F + 1,5·(C − F) + 3·(H − 1) + log2(1 + degré) + 2·T + V ;
- un fait est un groupe de fiches versées (deux notions partagées, une notion citée par exactement deux fiches de deux
  éditeurs, ou le même sujet de l'éclaireur) ; score(fait) = moyenne des trois meilleurs S + 2·(fiches − 1)
  + 2·(éditeurs − 1) + 0,5·min(6, notions − 1) ;
- un fait est retenu s'il dépasse 1,2 fois la médiane des vingt premiers scores de faits, s'il est attesté par deux
  fiches ou deux éditeurs au moins, s'il a une fiche de moins de 120 jours, s'il ne repose pas que sur du contenu payé,
  à deux faits au plus par éditeur ; sept au plus.

Un seul appel au modèle (`rediger`) écrit le titre et le paragraphe de tous les faits retenus, consigné au registre
des coûts, sous un plafond de dépense estimé avant l'appel. Chaque chapeau tient en 20 à 100 mots et ne cite que ce
que disent les fiches (synthese.verifier, mot plié présent dans la preuve) ; sinon il est retiré. Si l'appel échoue,
la lettre part avec les titres et les sources : jamais bloquant.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from html import escape
from urllib.parse import urlparse

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import media  # noqa: E402 — un modèle par rôle, lu au config.yaml du média

from newskit import couts  # noqa: E402
from newskit import nouvelles as nv  # noqa: E402 — le registre des nouvelles (CHANTIER_NOUVELLES.md)

JOURS_RECENT = 120  # une fiche plus ancienne ne fait pas un fait de la semaine
FACTEUR_MEDIANE = 1.2  # un fait retenu dépasse 1,2 fois la médiane des vingt premiers scores
FACTEUR_LETTRE = 0.65  # un fait entre dans la lettre à 0,65 fois la médiane des vingt premiers scores au moins
FAITS_MAX = 7
PAR_EDITEUR = 1  # un fait par éditeur à la une (mainteneur, 03.11.2026 : deux FDA le 1er novembre)
UNE_MIN = 3  # faits à la une au moins, s'il y en a d'éligibles (mainteneur, 03.11.2026)
MOTS_MIN, MOTS_MAX = 30, 70  # le chapeau d'un fait à la une (mainteneur, 04.11.2026 : la une moins développée que les
# billets suivants ; 20 à 40 le 03.11.2026 après le chapeau d'Asana « indigeste »)
MOTS_MAX_ECLAIRAGE = 100  # le chapeau d'un éclairage garde sa longueur : le détail y va
CHAPEAU_PHRASES = 2  # descriptif des rubriques et des brèves : 2 phrases au plus
CHAPEAU_NOMBRES = 2  # et 2 nombres au plus, ceux qui portent le fait
UNE_PHRASES = 4  # chapeau de la une : 4 phrases au plus (mainteneur, 04.11.2026)
UNE_NOMBRES = 2  # et 2 nombres au plus, hors dates : un chiffre lourd rend illisible (mainteneur, 04.11.2026)
MOTS_TITRE = 16
DESCRIPTIF_MIN, DESCRIPTIF_MAX = 20, 40  # le court descriptif d'un autre fait saillant (mainteneur, 03.11.2026)
MOTS_TITRE_BREF = 12
# Le titre d'un billet de rubrique tient sur une ligne : gras 15 px, Helvetica Neue Bold, 7,6 px par caractère au pire
# sur 564 px (620 moins deux marges de 28) → 74 ; 70 avec la marge (mainteneur, 03.11.2026).
TITRE_RUBRIQUE_CARACTERES = 70
MOTS_AUTRE_SOURCE = 300  # texte donné au modèle pour un autre fait saillant
MOTS_BREF_SOURCE = 300  # et pour une brève, développée comme lui (mainteneur, 04.11.2026 ; 80 avant)
AUSSI_MAX = 8  # lignes de « Rubriques »
BREF_PAR_THEME = 5
BREF_MAX = 40
BRUIT = 10  # une notion citée par plus de dix fiches versées est un mot courant, pas une nouveauté
POSTE = "lettre"  # le poste au registre des coûts
MODELE = media.modele("redaction")  # rédiger une brève d'après des sources ne demande pas le plus gros modèle
DELAI = 300
JOURNAL_REFUS = RACINE / "cache" / "lettre_refus.jsonl"  # chaque refus du contrôle, avec sa raison
PLAFOND_USD = 0.50  # dépense estimée au-delà de laquelle l'appel n'est pas tenté
MOTS_PAR_SOURCE = 1200  # texte d'une fiche d'un fait à la une donné au modèle, au plus
JETONS_FIXES = 20_000  # le contexte fixe d'un appel `claude -p` (mesuré, note B16 section 5)
PRIX_ENTREE_PAR_MTOK = 3.0  # Sonnet, entrée ; la sortie (quelques centaines de jetons) est comptée à part
PRIX_SORTIE_PAR_MTOK = 15.0

OFFICIEL = ("europa.eu", "overheid.nl", ".gov", "ilo.org", "admin.ch", "oecd.org")
RE_TEXTE = re.compile(r"(regulation|directive|\bact\b|§|article|\d{4}/\d+|urhg|\bloi\b|règlement|pojk)", re.I)
RE_PAYE = re.compile(r"utm_medium=paid|paid-article|sponsored content", re.I)
RE_PERSONNE = re.compile(
    r"\b(he|she|his|her|is an? (?:\w+ )?(?:journalist|researcher|professor|author|writer|lawyer|"
    r"analyst|scientist|economist|ceo|founder|director|minister|reporter|editor|politician)|"
    r"journalist|researcher|professor|author of|ceo|co-?founder|spokesperson|minister|"
    r"chercheu|journaliste|professeu|auteur|fondateur|ministre|directeur|directrice|personne)\b",
    re.I,
)
MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


# --- Outils -------------------------------------------------------------------------------------------------------


def canonique(url: str) -> str:
    """Une adresse sans segment de langue (/en/, /ko/, /fr-ch/) : deux traductions d'un article font une source."""
    u = urlparse(str(url))
    segs = [x for x in u.path.split("/") if x and not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", x)]
    return (u.netloc.removeprefix("www.") + "/" + "/".join(segs)).rstrip("/") if u.netloc else str(url)


def plier(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s).casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def nombre(n) -> str:
    """82611 → « 82 611 » (espace insécable), 33.23 → « 33,23 »."""
    try:
        if isinstance(n, str):
            n = float(n) if "." in n else int(n)
    except ValueError:
        return str(n)
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.2f}".replace(",", " ").replace(".", ",")
    return f"{int(n):,}".replace(",", " ")


# La lettre anglaise (lettre_anglaise.py, mainteneur, 06.11.2026 : « fais tout l'écosystème ») : html(), texte() et
# objet() d'une lettre de langue « en » rendent ses libellés, ses dates et son pied en anglais.
_LANGUE = ["fr"]
MOIS_ANGLAIS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
JOURS_ANGLAIS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LIBELLES_EN = {
    "À la une": "Top stories",
    "Source": "Source",
    "Éclairage": "Analysis",
    "autres": "more",
    "À retenir": "In brief",
    "Sur le même sujet": "Related",
    "Aussi cette semaine": "Also this week",
    "Aussi aujourd'hui": "Also today",
}


def en_anglais() -> bool:
    return _LANGUE[0] == "en"


def _l(t: str) -> str:
    """Un libellé fixe de la lettre dans sa langue."""
    return LIBELLES_EN.get(t, t) if en_anglais() else t


class _langue:
    """Le rendu d'une lettre dans sa langue, le temps d'un appel."""

    def __init__(self, L: dict):
        self.l = "en" if (L or {}).get("langue") == "en" else "fr"

    def __enter__(self):
        self.avant, _LANGUE[0] = _LANGUE[0], self.l

    def __exit__(self, *a):
        _LANGUE[0] = self.avant


def date_fr(d: str, annee: bool = True) -> str:
    try:
        x = dt.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d)
    if en_anglais():
        return f"{x.day} {MOIS_ANGLAIS[x.month - 1]}" + (f" {x.year}" if annee else "")
    jour = "1er" if x.day == 1 else str(x.day)
    return f"{jour} {MOIS[x.month - 1]}" + (f" {x.year}" if annee else "")


MOIS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def date_courte(d: str) -> str:
    """« mardi 3 nov. 2026 » : la date d'envoi, en tête de la lettre (mainteneur, 03.11.2026)."""
    x = dt.date.fromisoformat(str(d)[:10])
    if en_anglais():
        return f"{JOURS_ANGLAIS[x.weekday()]} {x.day} {MOIS_ANGLAIS[x.month - 1][:3]} {x.year}"
    return f"{JOURS[x.weekday()]} {'1er' if x.day == 1 else x.day} {MOIS_COURTS[x.month - 1]} {x.year}"


PERIODES = {"jour": "Daily", "semaine": "Weekly"}  # la note en haut de la lettre (mainteneur, 03.11.2026)
JOURS_SEMAINE = 8  # la weekly couvre les passages des 8 derniers jours
FRAICHEUR_JOUR = 2  # la daily : publié depuis 2 jours au plus (mainteneur, 03.11.2026)


def etiquette(L: dict) -> str:
    """« MARQUE · DAILY » : la marque, puis le rythme de la lettre."""
    p = PERIODES.get(L.get("periode") or "")
    return f"{L['marque']} · {p}" if p else L["marque"]


def date_entete(L: dict) -> str:
    """La date d'en-tête : le jour d'envoi pour la daily, « du 27 oct. au 3 nov. 2026 » pour la weekly."""
    if L.get("periode") == "semaine" and L.get("debut"):
        a, b = dt.date.fromisoformat(str(L["debut"])[:10]), dt.date.fromisoformat(str(L["envoi"])[:10])
        if en_anglais():
            return f"from {a.day} {MOIS_ANGLAIS[a.month - 1][:3]} to {date_courte(b).split(' ', 1)[1]}"
        return f"du {'1er' if a.day == 1 else a.day} {MOIS_COURTS[a.month - 1]} au {date_courte(b).split(' ', 1)[1]}"
    return date_courte(L["envoi"])


def heure_fr(d: str) -> str:
    """« 2026-11-03 06:37 » → « 3 novembre 2026, 6 h 37 »."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})", str(d))
    return f"{date_fr(m.group(1))}, {int(m.group(2))} h {m.group(3)}" if m else str(d)


def ecart(e: str) -> str:
    """« +1412 » → « +1 412 »."""
    m = re.fullmatch(r"([+-])(\d+)", str(e))
    return f"{m.group(1)}{nombre(int(m.group(2)))}" if m else str(e)


def guillemets(t: str) -> str:
    """« x » : une espace insécable après « et avant », qu'il y ait une espace ordinaire ou aucune (mainteneur,
    03.11.2026). Les guillemets anglais restent tels quels."""
    t = re.sub(r"« ?(?![\u00a0\u202f])", "«\u00a0", t)
    return re.sub(r"(?<![\u00a0\u202f]) ?»", "\u00a0»", t)


def ponctuation(t: str) -> str:
    """Une espace ordinaire ou fine devant « : », « ; », « ! », « ? » devient insécable (mainteneur, 03.11.2026)."""
    return re.sub(r"[ \u202f]+(?=[:;!?](?:\s|$|[\u00a0»<]))", "\u00a0", t)


def ponctuation_fr(t: str) -> str:
    """Un texte français rendu par le modèle : l'espace insécable s'ajoute aussi là où elle manque, devant un signe
    double suivi d'une espace ou de la fin ; « 16:00 », « https:// » et les identifiants restent tels quels."""
    t = ponctuation(t)
    return re.sub(r"(?<=[^\s\u00a0])([:;!?])(?=\s|$)", "\u00a0\\1", t)


def typographie(t: str, langue: str = "fr") -> str:
    """Tout texte publié, en toute langue (mainteneur, 06.11.2026 : « valable pour tout texte publié, pas seulement les textes
    français », puis « pour les : ! et ? aussi. toutes les langues ») : l'espace des guillemets et celle devant « : »,
    « ; », « ! », « ? » sont insécables. En français seulement, elle s'ajoute aussi là où elle manque."""
    t = ponctuation(guillemets(str(t or "")))
    return t if langue == "en" else ponctuation_fr(t)


def page_typographiee(page: str, langue: str = "fr") -> str:
    """`typographie` sur le seul texte du corps d'une page HTML, hors balises, attributs, <script> et <style>."""
    i = page.find("<body")
    tete, corps = (page[:i], page[i:]) if i >= 0 else ("", page)
    morceaux = re.split(r"(<script\b.*?</script>|<style\b.*?</style>)", corps, flags=re.S | re.I)
    f = lambda m: ">" + typographie(m.group(1).replace("&nbsp;", "\u00a0"), langue) + "<"
    return tete + "".join(m if k % 2 else re.sub(r">([^<]+)<", f, m) for k, m in enumerate(morceaux))


def _texte_seul(page: str, f) -> str:
    """Applique `f` au seul texte du corps d'une page HTML, hors balises, attributs et <style>."""
    i = page.find("<body")
    tete, corps = (page[:i], page[i:]) if i >= 0 else ("", page)
    return tete + re.sub(r">([^<]+)<", lambda m: ">" + f(m.group(1)) + "<", corps)


FLECHE = "•"  # l'angle critique sous le billet ; « Notre lecture : » sonnait comme un comité de rédaction (mainteneur, 03.11.2026)


def sans_libelle(t: str) -> str:
    """L'angle sans le libellé « Notre lecture : » des lettres gardées avant le 3.11.2026, majuscule rendue."""
    t = re.sub(r"^\s*Notre lecture\s*[:\u00a0]*\s*", "", str(t)).strip()
    return t[:1].upper() + t[1:]


def lecture_html(t: str) -> str:
    """L'angle critique sous le billet, ouvert par la flèche (&rarr;)."""
    return (
        f'<span style="display:block; margin:4px 0 0 0; padding-left:20px; text-indent:-12px;">'
        f"&bull;&nbsp;&nbsp;{_e(sans_libelle(t))}</span>"
    )


def sans_veuve(t: str) -> str:
    """Une espace insécable entre les deux derniers mots d'un titre html : un mot ne reste jamais seul sur la dernière
    ligne quand le titre passe à la ligne, sur téléphone par exemple (mainteneur, 03.11.2026)."""
    return re.sub(r" (\S+)$", "\u00a0\\1", str(t).rstrip())


def sans_fiche(t: str) -> str:
    """Le lecteur lit « source », jamais « fiche » (mainteneur, 03.11.2026)."""
    return re.sub(
        r"https?://\S+|\b[Ff]iche(?=s?\b)",
        lambda m: m.group(0) if m.group(0).startswith("http") else ("Source" if m.group(0)[0] == "F" else "source"),
        t,
    )


# --- Filtres ------------------------------------------------------------------------------------------------------


def generique(label: str) -> bool:
    """Aucune majuscule ni chiffre après le premier caractère : « Physical harm », « Sustainability »."""
    return not re.search(r"[A-Z0-9§À-Ý]", str(label)[1:])


def nom_d_editeur(label: str, fiches: list[dict]) -> bool:
    """Le libellé est contenu dans le nom de la publication ou l'hôte d'une fiche qui l'atteste (« CERN » de
    « Zenodo (CERN…) »)."""
    l_ = plier(label)
    return bool(l_) and any(l_ in plier(f.get("editeur", "") + " " + f.get("host", "")) for f in fiches)


def nom_de_personne(label: str, rationale: str) -> bool:
    """Heuristique : un libellé en forme de nom propre (deux à quatre mots capitalisés, sans chiffre) dont la
    justification parle d'une personne."""
    mots = str(label).split()
    forme = (
        2 <= len(mots) <= 4 and all(re.fullmatch(r"[A-ZÀ-Ý][\w'’.-]*", m) for m in mots) and not re.search(r"\d", label)
    )
    return bool(forme and rationale and RE_PERSONNE.search(rationale))


def contenu_paye(texte: str) -> bool:
    return bool(RE_PAYE.search(texte or ""))


def recente(fiche: dict, jour: str, jours: int = JOURS_RECENT) -> bool:
    try:
        d = dt.date.fromisoformat(str(fiche.get("date") or "")[:10])
    except ValueError:
        return False
    return 0 <= (dt.date.fromisoformat(jour[:10]) - d).days <= jours


def officielle(host: str) -> bool:
    return any(o in host for o in OFFICIEL)


def lisible(label: str) -> bool:
    """Un libellé qui se lit comme une notion ; sinon (identifiant, liste) la lettre montre le titre de sa source."""
    l_ = str(label)
    if "," in l_ or "§" in l_:
        return False
    if re.search(r"\b(?:Number|No\.?|Nr\.?|n°)\s*\d", l_):
        return False
    return not (re.search(r"\d", l_) and re.fullmatch(r"[A-Z0-9][A-Z0-9 ._/-]*", l_))


# --- Score --------------------------------------------------------------------------------------------------------


def _cite(label: str, texte: str) -> bool:
    """Le texte cite-t-il le libellé, en mot entier (aussi sans sa parenthèse) ? Un libellé qui porte un sigle (deux
    capitales de suite) se cherche tel qu'il s'écrit : « BART » n'est pas cité par « Bart Preneel ». Les autres, pliés."""
    cands = {c for c in (label, re.sub(r"\s*\(.*?\)", "", label).strip()) if len(c) >= 4}
    if re.search(r"[A-Z]{2}", label):
        return any(re.search(r"(?<![\w])" + re.escape(c) + r"(?![\w])", texte) for c in cands)
    t = plier(texte)
    return any(re.search(r"(?<![a-z0-9])" + re.escape(plier(c)) + r"(?![a-z0-9])", t) for c in cands)


def de_l_edition(f: dict) -> bool:
    """Une source de l'édition elle-même : son origine est celle de l'édition (`edition.nom`, les éclairages publiés
    par le reporter), ou son domaine est un domaine propre (`cfg.sites_de_l_edition`). Jamais un éditeur, jamais une
    preuve (03.11.2026)."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    e = cfg.edition()
    nom = str(e.get("nom") or "").lower()
    if nom and str(f.get("origine") or "").lower() == nom:
        return True
    host = str(f.get("host") or "").lower().removeprefix("www.")
    propres = {urlparse(s).netloc.lower().removeprefix("www.") for s in cfg.sites_de_l_edition()} - {""}
    return any(host == d or host.endswith("." + d) for d in propres)


def scorer(notions: list[dict], fiches: dict[str, dict]) -> list[dict]:
    """Chaque notion complétée de F, C, H, T, V, `att` (fiches versées qui l'attestent ou la citent) et S. Une source
    de l'édition (de_l_edition) n'atteste rien."""
    fiches = {k: f for k, f in fiches.items() if not de_l_edition(f)}
    textes = {k: str(f.get("texte", "")) for k, f in fiches.items()}
    out = []
    for n in notions:
        F = [f for f in n.get("fiches") or [] if f in fiches]
        # Une fiche en français cite aussi la notion par son libellé français ou son ancien libellé (03.11.2026).
        formes = list(dict.fromkeys(str(l_) for l_ in [n["label"], n.get("label_fr"), *(n.get("aussi") or [])] if l_))
        att = sorted(set(F) | {k for k, t in textes.items() if any(_cite(l_, t) for l_ in formes)})
        hosts = {fiches[k]["host"] for k in att}
        T = int(any(officielle(fiches[k]["host"]) for k in att) or bool(RE_TEXTE.search(n["label"])))
        V = int(any(fiches[k].get("plateforme") != "Recherche web" for k in att))
        S = (
            3 * len(F)
            + 1.5 * (len(att) - len(F))
            + 3 * (max(1, len(hosts)) - 1)
            + math.log2(1 + int(n.get("degre") or 0))
            + 2 * T
            + V
        )
        out.append(
            {**n, "F": len(F), "Fl": F, "C": len(att), "H": len(hosts), "T": T, "V": V, "att": att, "S": round(S, 2)}
        )
    return out


def classer(notions: list[dict], fiches: dict[str, dict]) -> tuple[list[dict], dict[str, int]]:
    """Les notions classées (sans les génériques, les noms d'éditeurs, les personnes, le bruit), triées par score."""
    ecartees = {"generiques": 0, "editeurs": 0, "personnes": 0}
    classees = []
    for x in scorer(
        [n for n in notions if [f for f in n.get("fiches") or [] if f in fiches and not de_l_edition(fiches[f])]],
        fiches,
    ):
        if generique(x["label"]):
            ecartees["generiques"] += 1
        elif nom_d_editeur(x["label"], [fiches[k] for k in x["att"]]):
            ecartees["editeurs"] += 1
        elif nom_de_personne(x["label"], x.get("rationale") or ""):
            ecartees["personnes"] += 1
        elif x["C"] <= BRUIT:
            classees.append(x)
    classees.sort(key=lambda x: (-x["S"], x["label"]))
    return classees, ecartees


# --- Faits --------------------------------------------------------------------------------------------------------


def editeurs_distincts(chemins: list[str], fiches: dict[str, dict]) -> set[str]:
    """Les éditeurs distincts de fiches : deux fiches d'un même registre ou d'un même éditeur en font un, deux versions
    linguistiques d'un même article aussi (Berg en Dal : deux fiches de l'Algoritmeregister, une source)."""
    return {
        plier(fiches[k]["editeur"] or fiches[k]["host"]) for k in chemins if k in fiches and not de_l_edition(fiches[k])
    }


def grouper(classees: list[dict], fiches: dict[str, dict]) -> list[dict]:
    """Les faits : groupes de fiches versées, triés par score."""
    par: dict[str, str] = {}

    def tr(a: str) -> str:
        while par.setdefault(a, a) != a:
            a = par[a]
        return a

    partage: Counter = Counter()
    for x in classees:
        a = x["att"]
        for i in range(len(a)):
            for j in range(i + 1, len(a)):
                partage[(a[i], a[j])] += 1
    for (a, b), c in partage.items():
        if c >= 2:
            par[tr(b)] = tr(a)
    for x in classees:
        if x["C"] == 2 and x["H"] == 2:
            par[tr(x["att"][1])] = tr(x["att"][0])
    attestantes = {f for x in classees for f in x["Fl"]}
    sujets = defaultdict(list)
    for k in sorted(fiches):
        if fiches[k].get("sujet") and k in attestantes:
            sujets[fiches[k]["sujet"]].append(k)
    for fs in sujets.values():
        for f in fs[1:]:
            par[tr(f)] = tr(fs[0])
    G = defaultdict(list)
    for x in classees:
        G[tr(x["Fl"][0])].append(x)
    faits = []
    for xs in G.values():
        xs.sort(key=lambda x: (-x["S"], x["label"]))
        fs = sorted({f for x in xs for f in x["Fl"]})
        hosts = sorted({fiches[f]["host"] for f in fs})
        docs = len({canonique(fiches[f]["url"]) or f for f in fs})
        eds = len(editeurs_distincts(fs, fiches))
        sc = sum(x["S"] for x in xs[:3]) / min(3, len(xs)) + 4 * (eds - 1) + 0.5 * min(6, len(xs) - 1)
        faits.append(
            {
                "score": round(sc, 2),
                "notions": xs,
                "fiches": fs,
                "hosts": hosts,
                "sources": docs,
                "editeurs": eds,
                "editeur": Counter(fiches[f]["editeur"] for f in fs).most_common(1)[0][0],
                "paye": all(contenu_paye(fiches[f].get("brut", "")) for f in fs),
            }
        )
    faits.sort(key=lambda f: (-f["score"], f["notions"][0]["label"]))
    return faits


def marquer_recents(faits: list[dict], fiches: dict[str, dict], jour: str, jours: int = JOURS_RECENT) -> None:
    """Les fiches récentes de chaque fait. Seules elles comptent pour les deux sources d'un fait à la une et pour sa
    source principale : une fiche de plus de JOURS_RECENT jours ne porte pas un fait à la une (Data Act de Heuking,
    daté du 12 septembre 2025, à la une du 3 novembre 2026 parce que Matheson était récent)."""
    for f in faits:
        rec = [k for k in f["fiches"] if recente(fiches[k], jour, jours)]
        f["recent"], f["recentes"] = bool(rec), rec
        f["editeurs_recents"] = len(editeurs_distincts(rec, fiches))


def eligible(f: dict) -> bool:
    return bool(f.get("recent")) and not f.get("paye")


def selectionner(faits: list[dict]) -> tuple[list[dict], list[dict]]:
    """(retenus, autres éligibles), dans l'ordre du score. Un fait est retenu s'il dépasse FACTEUR_MEDIANE fois la
    médiane des vingt premiers scores, s'il a deux éditeurs récents distincts, s'il est récent et pas payé, à
    PAR_EDITEUR faits au plus par éditeur ; FAITS_MAX au plus."""
    if not faits:
        return [], []
    seuil = FACTEUR_MEDIANE * statistics.median(sorted((f["score"] for f in faits), reverse=True)[:20])
    retenus, autres, par_ed = [], [], Counter()
    for f in faits:
        if not eligible(f):
            continue
        if (
            len(retenus) < FAITS_MAX
            and f["score"] > seuil
            and f.get("editeurs_recents", f.get("editeurs", len(f["hosts"]))) >= 2
            and par_ed[f["editeur"]] < PAR_EDITEUR
        ):
            retenus.append(f)
            par_ed[f["editeur"]] += 1
        else:
            autres.append(f)
    return retenus, autres


def completer_une(
    retenus: list[dict], autres: list[dict], minimum: int = UNE_MIN, groupe=None
) -> tuple[list[dict], list[dict]]:
    """La une d'au moins `minimum` faits (mainteneur, 03.11.2026 : « choisis au moins 3 faits importants par
    semaine ») : les faits établis par deux éditeurs indépendants (selectionner), puis les meilleurs au score des
    autres faits éligibles, déjà frais et hors registre ; un fait sans éditeur hors de l'édition n'y entre pas. Un fait
    pris à une seule source est marqué « rapporté ». Les faits pris sortent des autres.
    Variété (mainteneur, 03.11.2026 : deux FDA à la une du 1er novembre) : un complément ne reprend ni l'éditeur ni la
    rubrique (`groupe`) d'un fait déjà à la une ; la rubrique se relâche seulement si la une resterait incomplète."""
    une = list(retenus)
    for relache in (False, True):
        for f in autres:
            if len(une) >= minimum:
                break
            if f in une or f.get("editeurs_recents", 1) < 1:
                continue
            if any(plier(f["editeur"]) == plier(u["editeur"]) for u in une):
                continue
            if groupe and not relache and any(groupe(f) == groupe(u) for u in une):
                continue
            f["rapporte"] = f.get("editeurs_recents", 1) < 2
            une.append(f)
    return une, [f for f in autres if f not in une]


def titre_avec_lieu(f: dict) -> str:
    """« USA : La FDA propose … » : le lieu devant le titre, partout où le billet paraît (mainteneur, 03.11.2026)."""
    return f"{f['lieu']}\u00a0: {titre_sans_lieu(f)}" if f.get("lieu") else str(f.get("titre") or "")


def titre_sans_lieu(f: dict) -> str:
    """Le titre sans un lieu déjà en tête (« États-Unis : … ») quand le billet porte son lieu à part."""
    t = str(f.get("titre") or "")
    if f.get("lieu"):
        t = re.sub(
            r"^(?:États-Unis|USA|Etats-Unis|UE|Union européenne|Europe|" + re.escape(f["lieu"]) + r")\s*:\s*", "", t
        )
        t = t[:1].upper() + t[1:]
    return t


def promotionnel(f: dict) -> bool:
    """Une fiche publicitaire (webinaire, démonstration, contenu sponsorisé, offre) : jamais dans la lettre (mainteneur,
    03.11.2026 : le webinaire de The Hacker News à la une du 30 octobre). Le titre et le début du texte."""
    from newskit import fiche  # noqa: PLC0415

    return fiche.publicitaire(str(f.get("titre") or ""), str(f.get("texte") or ""))


def _nature(chemin: str) -> str:
    """La nature déclarée de la source d'une fiche (config_sources.NATURES), vide pour un média."""
    try:
        from newskit import config_sources as cfg  # noqa: PLC0415

        src = cfg.source_du_chemin(str(chemin))
    except Exception:  # noqa: BLE001 — registre illisible : un média
        return ""
    return src.nature if src is not None else ""


def non_actualite(source) -> bool:
    """Une fiche (chemin, ou dict qui porte « chemin ») d'un registre ou d'un jeu de données régulier (`nature:
    registre` au registre des sources) : une donnée du graphe, pas une actualité. Elle ne concourt ni à la une, ni aux
    autres faits saillants, ni aux enquêtes du reporter (newskit.reporter l'appelle) ; elle paraît en brève
    seulement si elle annonce un changement du registre lui-même (changement_du_registre). Mainteneur, 03.11.2026."""
    chemin = source.get("chemin", "") if isinstance(source, dict) else str(source)
    return _nature(chemin) == "registre"


RE_REGISTRE = re.compile(
    r"(?<![a-z])(algoritmeregister|registre|register|registry|database|base de donnees|monitor|"
    r"repertoire)"
)
RE_CHANGEMENT = re.compile(
    r"(?<![a-z])(verplicht|obligatoire|obligation|mandatory|ouvre|ouverture|opens?|launch\w*|"
    r"lance\w*|jaarverslag|annual report|rapport annuel|bilan|statisti\w*|cijfers|chiffres|"
    r"now lists|totaal|total|\d[\d  ]* (?:algoritmes|algorithms|algorithmes|incidents))(?![a-z])"
)


def changement_du_registre(f: dict) -> bool:
    """Le titre d'une fiche de registre annonce-t-il un changement du registre lui-même (ouverture, obligation,
    chiffre global publié) ? Il nomme le registre et un mot de changement. Une page d'algorithme (« SafeGPT - Gemeente
    Berg en Dal ») ou le glossaire (« Woordenlijst ») de l'Algoritmeregister, le 1er novembre 2026, n'en annoncent pas."""
    t = plier(str(f.get("titre") or "")).replace("’", "'")
    return bool(RE_REGISTRE.search(t) and RE_CHANGEMENT.search(t))


RE_APPLICATION = re.compile(
    r"(?:appl(?:ies|y|icable)(?: from| since| as of)?|in force(?: since| from)?|"
    r"(?:entered|came|comes|enters) into force(?: on)?|(?:became|becomes) mandatory(?: as of| on)?|"
    r"mandatory (?:as of|from|since)|s'applique(?:nt)? (?:depuis|a partir)(?: du)?|"
    r"applicables? (?:depuis|a partir)(?: du)?|en vigueur (?:depuis|le)|entre(?:e)? en vigueur le|"
    r"gilt seit|in kraft seit)\s+(?:le |on |the |du )?"
)


RE_OBLIGATION = re.compile(
    r"appl|in force|into force|mandat|oblig|vigueur|requir|deadline|must\b|doit|doivent|gilt|pflicht"
)


def _dates_positions(p: str) -> list[tuple[int, dt.date]]:
    """Les dates complètes (avec année) d'un texte plié, avec leur position."""
    out = []
    for m in re.finditer(rf"(?<!\d)(\d{{1,2}})(?:er|st|nd|rd|th)?\.?\s+({_FR}|{_EN})\s+(\d{{4}})", p):
        mo = MOIS_FR.get(m.group(2)) or MOIS_EN.get(m.group(2))
        out.append((m.start(), (int(m.group(3)), mo, int(m.group(1)))))
    for m in re.finditer(rf"({_EN})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", p):
        out.append((m.start(), (int(m.group(3)), MOIS_EN[m.group(1)], int(m.group(2)))))
    for m in re.finditer(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", p):
        out.append((m.start(), (int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    res = []
    for pos, (y, mo, d) in out:
        try:
            res.append((pos, dt.date(y, mo, d)))
        except ValueError:
            pass
    return res


def regle_deja_en_vigueur(f: dict, jour: str, jours: int) -> bool:
    """Un article qui explique une règle déjà en vigueur, sans fait daté de la semaine : une date d'application ou
    d'entrée en vigueur (« applies since 12 September 2026 », « s'applique depuis le 2 août 2026 ») antérieure à la
    fenêtre de fraîcheur, et aucune date dans la fenêtre. Cas réel : le Data Act de Matheson, le 3 novembre 2026."""
    p = plier(f"{f.get('titre') or ''} . {f.get('texte') or ''}").replace("’", "'")
    debut = dt.date.fromisoformat(jour[:10]) - dt.timedelta(days=jours)
    fin = dt.date.fromisoformat(jour[:10])
    dates = _dates_positions(p)
    appliquees = [d for m in RE_APPLICATION.finditer(p) for pos, d in dates if m.end() <= pos <= m.end() + 3]
    # « from 12 September 2026, manufacturers … will be required » : une date de départ dans une phrase d'obligation.
    for pos, d in dates:
        phrase = re.split(r"[.!?]\s", p[max(0, pos - 300) : pos])[-1] + p[pos : pos + 200].split(". ")[0]
        if re.search(
            r"(?:from|since|as of|effective|depuis|a partir du|des le|ab|seit)\s+(?:the\s+|le\s+)?$",
            p[max(0, pos - 30) : pos],
        ) and RE_OBLIGATION.search(phrase):
            appliquees.append(d)
    return any(d < debut for d in appliquees) and not any(debut <= d <= fin for _, d in dates)


def fiche_d_incident(f: dict) -> bool:
    """Une page d'incident (bloc `incidents:` du registre : OECD AI Incidents Monitor, AI Incident Database)."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    u = re.sub(r"^https?://(?:www\.)?", "", str(f.get("url") or "").lower())
    return any(u == p or u.startswith(p + "/") for p in cfg.incidents())


RE_EVENEMENT_PASSE = re.compile(
    r"(?<![a-z])(previously|earlier|last year|in (?:19|20)\d\d|auparavant|precedemment|"
    r"en (?:19|20)\d\d)(?![a-z])"
)


def evenement_date(f: dict, jour: str, jours: int) -> bool:
    """La fiche date-t-elle l'événement lui-même dans la fenêtre ? Une date complète du texte entre jour - jours et
    jour, et aucun renvoi au passé (« previously », « in 2026 ») : la date du premier article cité ne suffit pas."""
    p = plier(str(f.get("texte") or "")).replace("’", "'")
    if RE_EVENEMENT_PASSE.search(p):
        return False
    fin = dt.date.fromisoformat(jour[:10])
    return any(fin - dt.timedelta(days=jours) <= d <= fin for _, d in _dates_positions(p))


def mediane(faits: list[dict]) -> float:
    return statistics.median(sorted((f["score"] for f in faits), reverse=True)[:20]) if faits else 0.0


def admis(f: dict, fiches: dict[str, dict], groupe: str, med: float, rubriques: bool = True) -> bool:
    """Un fait entre-t-il dans la lettre (à la une, autres faits saillants, brèves) ? Une rubrique (un graphe qui en
    déclare : une source sans rubrique reste au graphe), une source principale récente, un score d'au moins
    FACTEUR_LETTRE fois la médiane. Le score seul ne trie pas le hors-sujet (SIMIT 7,47 passe AgentCap 7,0, le
    3 novembre 2026) : c'est la rubrique, qui demande une mention de l'IA (RE_IA) ; le score coupe la traîne."""
    if rubriques and groupe == "Autres":
        return False
    if principale(f, fiches) not in (f.get("recentes") if "recentes" in f else f["fiches"]):
        return False
    return f["score"] >= FACTEUR_LETTRE * med


def principale(fait: dict, fiches: dict[str, dict]) -> str:
    """La source principale d'un fait : récente d'abord, puis officielle, puis celle qui atteste le plus de ses notions."""
    compte = Counter(k for x in fait["notions"] for k in x.get("att") or [] if k in fait["fiches"])
    rec = fait.get("recentes") or fait["fiches"]
    return sorted(fait["fiches"], key=lambda k: (k not in rec, not officielle(fiches[k]["host"]), -compte[k], k))[0]


# --- Paragraphes ----------------------------------------------------------------------------------------------------


def compter_mots(p: str) -> int:
    """Les mots d'un paragraphe ; les signes isolés (« : », « ; », tirets) ne comptent pas."""
    return sum(1 for m in str(p).split() if re.search(r"\w", m))


def controler(paragraphe: str, sources: str, mini: int = MOTS_MIN, maxi: int = MOTS_MAX) -> bool:
    """De `mini` à `maxi` mots, et chaque nom propre, sigle ou nombre présent dans les sources (synthese.verifier)."""
    from newskit import synthese as sy  # noqa: PLC0415

    return mini <= compter_mots(paragraphe) <= maxi and not sy.verifier(paragraphe, sources)


def noms_en_gras(paragraphe: str, sources: str) -> tuple[str, list[str]]:
    """(texte sans balisage, noms à mettre en gras). Le modèle marque les acteurs par **Nom** ; seul un nom présent mot
    pour mot dans une source du fait reste en gras, chaque nom une fois (mainteneur, 03.11.2026)."""
    gras = []
    for m in re.finditer(r"\*\*(.+?)\*\*", str(paragraphe)):
        nom = m.group(1).strip()
        if nom and nom in sources and nom not in gras:
            gras.append(nom)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(paragraphe)).replace("**", ""), gras


def avec_liens(h: str, liens: dict | None) -> str:
    """Les autres sources citées dans le texte, en lien sur leur fragment (mainteneur, 03.11.2026 : « l'autre source doit
    aussi être exprimée en lien »)."""
    for frag, url in (liens or {}).items():
        e = escape(frag, quote=True)
        i = h.find(e)
        if i >= 0 and url:
            h = (
                h[:i]
                + f'<a href="{escape(url, quote=True)}" style="color:inherit; text-decoration:underline;">{e}</a>'
                + h[i + len(e) :]
            )
    return h


def chapeau_html(texte_: str, gras: list[str]) -> str:
    """Le chapeau échappé, puis la première occurrence de chaque nom en <strong>."""
    h = escape(texte_, quote=True)
    for nom in gras:
        e = escape(nom, quote=True)
        i = h.find(e)
        if i >= 0:
            h = h[:i] + f"<strong>{e}</strong>" + h[i + len(e) :]
    return h


def _extrait(f: dict, n: int = MOTS_PAR_SOURCE) -> str:
    return " ".join(str(f.get("texte", "")).split()[:n])


LOIS = (
    ("AI Act", r"ai act|artificial intelligence act|reglement sur l.ia|\bria\b|eu 2024/1689|ki-verordnung"),
    (
        "RGPD",
        r"\bgdpr\b|\brgpd\b|general data protection regulation|reglement general sur la protection des donnees|dsgvo",
    ),
    ("Data Act", r"data act|2023/2854"),
    ("DSA", r"\bdsa\b|digital services act"),
    ("DMA", r"\bdma\b|digital markets act"),
    ("NIS2", r"\bnis ?2\b"),
    ("DORA", r"\bdora\b|digital operational resilience act"),
    ("CRA", r"cyber resilience act"),
)


def textes_de_loi(texte_: str) -> list[str]:
    """Les textes de loi européens nommés dans un texte, dans l'ordre de LOIS."""
    t = plier(texte_)
    return [nom for nom, motif in LOIS if re.search(motif, t)]


def _loi(fa: dict, fiches: dict[str, dict], ks: list[str]) -> str:
    lois = textes_de_loi(" ".join(f"{fiches[k]['titre']} {_extrait(fiches[k], 300)}" for k in ks if k in fiches))
    return f"Texte de loi : {', '.join(lois)}\n" if lois else ""


def _suite(fa: dict) -> str:
    """La consigne d'un billet qui est la suite d'une nouvelle déjà parue (CHANTIER_NOUVELLES.md ; mainteneur, 05.11.2026 :
    « le développement devrait faire l'objet de l'actualité »)."""
    return (
        f"Suite d'une nouvelle déjà parue. Le fait neuf, à dire en premier : « {fa['developpement']} »\n"
        if fa.get("developpement")
        else ""
    )


def _invite(faits: list[dict], fiches: dict[str, dict], autres=(), brefs=()) -> str:
    blocs = []
    for i, fa in enumerate(faits, 1):
        tete = principale(fa, fiches) if all(k in fiches for k in fa["fiches"]) else None
        ordre = ([tete] if tete else []) + [k for k in fa["fiches"] if k != tete]
        srcs = "\n\n".join(
            f"[source {fiches[k]['editeur']}, {fiches[k]['date']}] {fiches[k]['titre']}\n{_extrait(fiches[k])}"
            for k in ordre[:4]
            if k in fiches
        )
        rapporte = (
            f"Fait rapporté, une seule source : écris « selon {fiches[tete]['editeur']} ».\n"
            if fa.get("rapporte") and tete
            else ""
        )
        blocs.append(f"=== FAIT {i}\n{_suite(fa)}{rapporte}{_loi(fa, fiches, ordre[:4])}{srcs}")
    for j, fa in enumerate([*autres, *brefs], 1):
        k = principale(fa, fiches)
        n = MOTS_AUTRE_SOURCE if j <= len(autres) else MOTS_BREF_SOURCE
        genre = "AUTRE" if j <= len(autres) else "BRÈVE"
        blocs.append(
            f"=== {genre} {j}\n{_suite(fa)}{_loi(fa, fiches, [k]) if genre == 'AUTRE' else ''}[source {fiches[k]['editeur']}, {fiches[k]['date']}] {fiches[k]['titre']}\n"
            f"{_extrait(fiches[k], n)}"
        )
    return (
        "Tu écris une lettre d'actualité en français sur la gouvernance de l'IA, d'après ses seules sources.\n"
        "Pour chaque FAIT, le fait est celui de sa première source ; les suivantes l'appuient. "
        f"Un titre d'actualité de {MOTS_TITRE} mots au plus et un chapeau de "
        f"{MOTS_MIN} à {MOTS_MAX} mots au plus, {UNE_PHRASES} phrases au plus, {UNE_NOMBRES} nombres au plus "
        "(ceux qui portent le fait) ; le reste du détail va sur la page de la lettre, jamais dans le chapeau. "
        "Aucun balisage markdown (ni *…*, ni _…_) hors les ** des noms. Dans le chapeau, entoure de ** le nom de "
        "chaque acteur (organisation, autorité, institution, outil, loi nommée), à sa première mention seulement, "
        "écrit comme dans les sources : **EuroHPC**.\n"
        f"Pour chaque AUTRE : un titre en manchette de {MOTS_TITRE} mots et {TITRE_RUBRIQUE_CARACTERES} caractères au "
        "plus, espaces comprises, pour tenir sur une ligne (le lieu ou l'acteur, puis le fait), "
        f"et un descriptif de {DESCRIPTIF_MIN} à {DESCRIPTIF_MAX} mots au plus (qui, quoi, où), mêmes règles que le "
        f"chapeau ({CHAPEAU_PHRASES} phrases au plus, {CHAPEAU_NOMBRES} nombres au plus), sans gras.\n"
        f"Pour chaque BRÈVE : un titre en manchette de {MOTS_TITRE_BREF} mots au plus, et un descriptif comme pour "
        "un AUTRE.\n"
        "Quand un FAIT ou un AUTRE porte une ligne « Texte de loi », le chapeau ou le descriptif rappelle en une phrase "
        "ce que le texte impose sur le point traité (qui doit faire quoi, depuis quand), d'après les sources du bloc "
        "seulement ; les lecteurs ne connaissent pas les enjeux du texte. Si les sources ne le disent pas, n'écris rien "
        "sur ce point, et jamais de phrase sur les sources elles-mêmes.\n"
        + REGLES
        + 'Réponds en JSON : {"faits": [{"n": 1, "titre": "…", "paragraphe": "…"}, …], '
        '"autres": [{"n": 1, "titre": "…", "descriptif": "…"}, …]} ; les AUTRE puis les BRÈVE partagent la '
        "numérotation de « autres ».\n\n" + "\n\n".join(blocs)
    )


SCHEMA = {
    "type": "object",
    "required": ["faits"],
    "properties": {
        "faits": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "titre", "paragraphe"],
                "properties": {"n": {"type": "integer"}, "titre": {"type": "string"}, "paragraphe": {"type": "string"}},
            },
        },
        "autres": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "titre"],
                "properties": {"n": {"type": "integer"}, "titre": {"type": "string"}, "descriptif": {"type": "string"}},
            },
        },
    },
}


def estimer_usd(invite: str, faits: int = FAITS_MAX, autres: int = AUSSI_MAX, brefs: int = BREF_MAX) -> float:
    jetons = JETONS_FIXES + len(invite) / 4
    sortie = 2 * (
        faits * (MOTS_MAX + MOTS_TITRE)
        + autres * (DESCRIPTIF_MAX + MOTS_TITRE)
        + brefs * (DESCRIPTIF_MAX + MOTS_TITRE_BREF)
    )
    return jetons * PRIX_ENTREE_PAR_MTOK / 1e6 + sortie * PRIX_SORTIE_PAR_MTOK / 1e6


def _sources_de(fa: dict, fiches: dict[str, dict]) -> str:
    return " ".join(
        f"{fiches[k]['titre']} {fiches[k]['editeur']} {fiches[k].get('texte', '')}" for k in fa["fiches"] if k in fiches
    )


TOURNURES = (
    "il convient de noter",
    "il convient de souligner",
    "force est de constater",
    "dans un contexte de",
    "a l'heure ou",
    "veritable",
    "crucial",
    "un signal fort",
    "non pas",
    "plus que jamais",
    "change la donne",
    "en somme",
    "en definitive",
    "source muette",
    "les sources ne disent",
    "la source ne precise",
)
# « clé » adjectif : après un nom (« rôle clé », « acteurs clés ») ; « la clé de chiffrement » reste permise.
RE_CLE = re.compile(
    r"(?<![a-z])((?:role|acteur|enjeu|point|element|question|moment|facteur|etape|chiffre|date|"
    r"mesure|secteur|texte|notion)s? cles?)(?![a-z])"
)


def tournure(texte_: str) -> str | None:
    """La première tournure interdite d'un texte (sur mots pliés), sinon None : remplissage, emphase, « non pas X,
    mais Y », phrase sur les sources elles-mêmes (mainteneur, 03.11.2026)."""
    t = plier(str(texte_)).replace("’", "'")
    for e in TOURNURES:
        if re.search(r"(?<![a-z])" + re.escape(e), t):
            return e
    m = RE_CLE.search(t)
    return m.group(1) if m else None


MOTS_PAR_PHRASE = 25  # français simplifié d'après ASD-STE100 (mainteneur, 03.11.2026)
PHRASES_PAR_PARAGRAPHE = 6
ABREVIATIONS = {
    "art",
    "al",
    "cf",
    "etc",
    "p",
    "pp",
    "n",
    "no",
    "vol",
    "m",
    "mm",
    "mme",
    "dr",
    "pr",
    "st",
    "env",
    "janv",
    "fevr",
    "avr",
    "juil",
    "sept",
    "oct",
    "nov",
    "dec",
    "inc",
    "ltd",
    "co",
    "corp",
    "vs",
    "ch",
    "chap",
    "ann",
}
SIGLES_PERMIS = {"RGPD", "UE", "IA", "OCDE"}  # et « AI Act »
MOTS_OUTILS = {
    "selon",
    "pour",
    "depuis",
    "avec",
    "apres",
    "avant",
    "lors",
    "dans",
    "sous",
    "chez",
    "entre",
    "contre",
    "elle",
    "elles",
    "cette",
    "celle",
    "ainsi",
    "puis",
    "mais",
    "donc",
    "enfin",
    "aussi",
}


def phrases(t: str) -> list[str]:
    """Les phrases d'un texte, coupées sur . ! ? suivis d'une espace ou de la fin, sauf après une abréviation
    (« art. 49 »), une initiale (« M. Lane », « U.S. ») ou à l'intérieur d'un nombre (« 3.5 »)."""
    out, debut = [], 0
    for m in re.finditer(r"[.!?…]+(?=\s|$)", t):
        avant = re.search(r"([\w.]+)$", t[debut : m.start()])
        mot = avant.group(1) if avant else ""
        if m.group(0) == "." and (
            plier(mot).rstrip(".") in ABREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.)*[A-Za-z]", mot)
        ):
            continue
        out.append(t[debut : m.end()].strip())
        debut = m.end()
    if t[debut:].strip():
        out.append(t[debut:].strip())
    return out


RE_GROUPE = re.compile(r"(?:(?<![a-z])(?:de|du|des|d')\s*(?:la\s+|l'|les\s+)?[a-z0-9œ-]+\s*){4}")


def groupe_trop_long(t: str) -> bool:
    """Plus de trois noms à la suite dans un groupe nominal : quatre compléments en « de/du/des » d'affilée."""
    return bool(RE_GROUPE.search(plier(t).replace("’", "'")))


def sigle_non_developpe(t: str, connus: set[str]) -> str | None:
    """Le premier sigle (deux capitales ou plus) employé sans être développé à sa première apparition, ni déjà
    développé plus haut dans la lettre (`connus`), ni permis (SIGLES_PERMIS, « AI Act ») ; sinon None. Un sigle suivi
    d'un tiret et d'un chiffre est un nom de modèle (« GPT-5.4 ») ; un sigle collé à un nom propre en fait partie
    (« EuroHPC JU », « AI Gigafactories »), comme un sigle d'un titre anglais en « The » (« The EU AI Act
    Conversations »). Les sigles développés ici s'ajoutent à `connus`."""
    vus = set()
    for m in re.finditer(r"(?<![\w.-])([A-ZÀ-Ý]{2,}[0-9]*)(?![\w-])(?!\.\w)|(?<![\w.-])([A-ZÀ-Ý]{2,})(?=-[a-z])", t):
        x = m.group(1) or m.group(2)
        if x in vus or x in connus or x in SIGLES_PERMIS or (x == "AI" and t[m.end() : m.end() + 4] == " Act"):
            continue
        avant = re.search(r"(\S+)\s+$", t[: m.start()])
        apres = re.match(r"\s+([\w'’-]+)", t[m.end() :])
        if (
            avant
            and (re.fullmatch(r"[A-ZÀ-Ý]\w{3,}", avant.group(1)) or avant.group(1) == "The")
            and plier(avant.group(1)) not in MOTS_OUTILS
        ) or (apres and re.fullmatch(r"[A-ZÀ-Ý][a-zà-ÿ]\w{2,}", apres.group(1))):
            continue  # dans un nom propre : « EuroHPC JU », « AI Gigafactories »
        if t[m.start() - 1 : m.start()] == "(" and t[m.end() : m.end() + 1] == ")" and t[: m.start() - 1].strip():
            vus.add(x)
            continue
        return x
    connus |= vus
    return None


def _sans_developpements(t: str, sources_pliees: str) -> str:
    """Le texte sans les développements de sigles dont le sigle est dans les sources (« Banque des règlements
    internationaux (BIS) ») : le nom développé se traduit, le sigle se vérifie."""

    def f(m):
        return m.group(0) if plier(m.group(2)) not in sources_pliees else " " + m.group(2)

    return re.sub(r"((?:[A-ZÀ-Ýa-zà-ÿ'’-]+\s+){1,8}?)\(([A-ZÀ-Ý]{2,}[0-9]*)\)", f, t)


# STE-Enquête (docs/STE_ENQUETE.md) : le vocabulaire de la preuve, sur mots pliés.
VERBES_INTERDITS = (
    "pretend",
    "pretendent",
    "admet",
    "admettent",
    "reconnait",
    "reconnaissent",
    "revele",
    "revelent",
    "denonce",
    "denoncent",
    "avoue",
    "avouent",
)
RE_VERBE_PASSE = re.compile(
    r"(?<![a-z])((?:a|ont|avait|avaient) (?:pretendu|admis|reconnu|revele|denonce|avoue))(?![a-z])"
)
RE_EVALUATION = re.compile(
    r"(?<![a-z])(historiques?|inedite?s?|majeure?s?|sans precedent|controversee?s?|cruciale?s?|"
    r"cruciaux|revolutionnaires?)(?![a-z])"
)
RE_DATE_RELATIVE = re.compile(
    r"(?<![a-z])(avant-hier|hier|aujourd'hui|demain|la semaine (?:derniere|prochaine)|"
    r"le mois (?:dernier|prochain)|l'an dernier|l'annee (?:derniere|prochaine)|recemment|"
    r"dernierement|ces derniers (?:jours|mois)|cette semaine|ce mois-ci)(?![a-z])"
)


def preuve(texte_: str) -> str | None:
    """La première faute au vocabulaire de la preuve (STE-Enquête), sinon None : un verbe d'attribution interdit
    (« prétend », « révèle »… ; « dit », « écrit », « annonce », « revendique », « selon », « affirme » sont les bons), un adjectif
    d'évaluation hors citation (« historique », « sans précédent »…), une date relative (« hier », « récemment »…)."""
    t = plier(str(texte_)).replace("’", "'")
    m = RE_VERBE_PASSE.search(t)
    if m:
        return f"verbe d'attribution : {m.group(1)}"
    for v in VERBES_INTERDITS:
        if re.search(r"(?<![a-z])" + v + r"(?![a-z])", t):
            return f"verbe d'attribution : {v}"
    hors = re.sub(r"«[^»]*»|\"[^\"]*\"|“[^”]*”", " ", t)
    m = RE_EVALUATION.search(hors)
    if m:
        return f"adjectif d'évaluation : {m.group(1)}"
    m = RE_DATE_RELATIVE.search(t)
    return f"date relative : {m.group(1)}" if m else None


# Les noms français de lieux et d'organisations, et leurs formes dans d'autres langues (pliées), tenus à la main :
# la forme française passe le contrôle des noms quand une forme équivalente est dans les sources. Un sigle étranger
# se cherche en capitales (« BIS », pas l'allemand « bis »).
NOMS_FRANCAIS = {
    "francfort": ("frankfurt",),
    "bruxelles": ("brussels", "brussel"),
    "ocde": ("OECD",),
    "bri": ("BIS", "bank for international settlements"),
    "commission europeenne": ("european commission", "europaische kommission", "europese commissie"),
    "pays-bas": ("netherlands", "nederland"),
    "allemagne": ("germany", "deutschland"),
    "etats-unis": ("united states",),
    "royaume-uni": ("united kingdom",),
    "union europeenne": ("european union",),
    "bce": ("ECB", "european central bank"),
    "banque centrale europeenne": ("european central bank", "ECB"),
    "coree du sud": ("south korea", "korea"),
    "chine": ("china",),
    "suisse": ("switzerland", "schweiz"),
    "londres": ("london",),
    "munich": ("munchen", "munich"),
    "rgpd": ("GDPR", "general data protection regulation", "2016/679"),  # article n° 5, 4.11.2026
}


def avec_noms_francais(sources: str) -> str:
    """Les sources, plus la forme française de chaque nom dont une forme équivalente s'y lit (NOMS_FRANCAIS)."""
    p = plier(sources)

    def lu(e):
        if e.isupper():
            return re.search(r"(?<![\w])" + re.escape(e) + r"(?![\w])", sources)
        return re.search(r"(?<![a-z0-9])" + re.escape(e) + r"(?![a-z0-9])", p)

    ajouts = [fr for fr, eqs in NOMS_FRANCAIS.items() if any(lu(e) for e in eqs)]
    return sources + (" " + " ".join(ajouts) if ajouts else "")


# Exactitude (STE-Enquête, section 5) : chaque montant et chaque date d'un texte figure dans ses sources, sous une
# forme équivalente admise (« 1 Md€ », « EUR 1 billion », « 1 000 millions d'euros » ; « 3 décembre 2026 »,
# « 3 December 2026 », « December 3, 2026 », « 03/12/2026 », « 2026-12-03 »).
_NUM = r"\d{1,3}(?:[,\u00a0\u202f ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_DEV = r"€|\$|£|eur\b|usd\b|gbp\b|chf\b|euros?\b|dollars?\b|livres?\b"
_ECH = r"milliards?\b|mds?\b|bn\b|billions?\b|millions?\b|mio\b|mn\b|thousand\b|mille\b"
RE_MONTANT = re.compile(rf"(?:({_DEV})\s?)?({_NUM})(?:\s?({_ECH}))?(?:\s?(?:d'|d’|de\s)?\s?({_DEV}))?", re.I)
ECHELLES = {
    "milliard": 1e9,
    "md": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "million": 1e6,
    "mio": 1e6,
    "mn": 1e6,
    "thousand": 1e3,
    "mille": 1e3,
}
DEVISES = {
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "£": "GBP",
    "gbp": "GBP",
    "livre": "GBP",
    "chf": "CHF",
}
MOIS_FR = {
    m: i
    for i, m in enumerate(
        (
            "janvier",
            "fevrier",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "aout",
            "septembre",
            "octobre",
            "novembre",
            "decembre",
        ),
        1,
    )
}
MOIS_EN = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
_FR = "|".join(MOIS_FR)
_EN = "|".join(MOIS_EN)
RE_DATE_FR = re.compile(rf"(?<!\d)(\d{{1,2}})(?:er)?\s+({_FR})(?:\s+(\d{{4}}))?(?!\d)")


def _valeur(num: str) -> float:
    n = num.replace("\u00a0", " ").replace("\u202f", " ")
    if re.fullmatch(r"\d{1,3}(?:[, ]\d{3})+", n):
        return float(re.sub(r"[, ]", "", n))
    m = re.fullmatch(r"(\d{1,3}(?:[, ]\d{3})+)([.,]\d+)", n)
    if m:
        return float(re.sub(r"[, ]", "", m.group(1)) + "." + m.group(2)[1:])
    return float(n.replace(" ", "").replace(",", "."))


def _devise(x: str | None) -> str:
    if not x:
        return ""
    x = x.lower().rstrip("s") if x.lower() not in ("$",) else x
    return DEVISES.get(x, DEVISES.get(x.lower(), ""))


def montants(t: str) -> list[tuple[str, float, str]]:
    """(texte, valeur, devise) des montants d'un texte : un nombre avec une échelle ou une devise."""
    out = []
    for m in RE_MONTANT.finditer(t):
        dev, num, ech, dev2 = m.groups()
        if not (dev or ech or dev2):
            continue
        e = ECHELLES.get(re.sub(r"s$", "", (ech or "").lower()), 1.0)
        out.append((m.group(0).strip(), _valeur(num) * e, _devise(dev) or _devise(dev2)))
    return out


def dates_de(t: str) -> set[tuple[int, int, int | None]]:
    """Les dates (jour, mois, année ou None) d'un texte, en français, en anglais ou en chiffres."""
    p = plier(t)
    out = set()
    for d, m, y in RE_DATE_FR.findall(p):
        out.add((int(d), MOIS_FR[m], int(y) if y else None))
    for d, m, y in re.findall(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+({_EN})(?:,?\s+(\d{{4}}))?", p):
        out.add((int(d), MOIS_EN[m], int(y) if y else None))
    for m, d, y in re.findall(rf"({_EN})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?(?!\d)", p):
        out.add((int(d), MOIS_EN[m], int(y) if y else None))
    for y, m, d in re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", p):
        out.add((int(d), int(m), int(y)))
    for a, b, y in re.findall(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)", p):
        out.add((int(a), int(b), int(y)))
        out.add((int(b), int(a), int(y)))  # 09-01-2026 : l'ordre américain aussi
    return {x for x in out if 1 <= x[0] <= 31 and 1 <= x[1] <= 12}


def exactitude(texte_: str, sources: str) -> str | None:
    """« nombre absent des sources : X » ou « date absente des sources : X » pour le premier montant ou la première
    date du texte qu'aucune forme équivalente des sources ne porte ; sinon None."""
    src = montants(sources)
    for brut, v, dev in montants(texte_):
        if not any(abs(v - w) <= 1e-6 * max(v, w, 1) and (not dev or not d2 or dev == d2) for _, w, d2 in src):
            return f"nombre absent des sources : {brut}"
    sd = dates_de(sources)
    annees = set(re.findall(r"(?<!\d)(\d{4})(?!\d)", sources))
    for m in RE_DATE_FR.finditer(plier(texte_)):
        d, mo, y = int(m.group(1)), MOIS_FR[m.group(2)], int(m.group(3)) if m.group(3) else None
        ok = (d, mo, y) in sd if y else any(x[:2] == (d, mo) for x in sd)
        ok = ok or (y is not None and (d, mo, None) in sd and str(y) in annees)
        if not ok:
            return f"date absente des sources : {texte_[m.start() : m.end()]}"  # plier garde les longueurs
    return None


def sans_nombres_ni_dates(t: str) -> str:
    """Le texte sans ses montants ni ses dates, déjà contrôlés par exactitude, pour le contrôle des noms."""
    t = RE_MONTANT.sub(lambda m: " " if any(m.groups()[i] for i in (0, 2, 3)) else m.group(0), t)
    return re.sub(
        rf"(?<!\d)\d{{1,2}}(?:er)?\s+(?:{'|'.join(['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'])})(?:\s+\d{{4}})?",
        " ",
        t,
        flags=re.I,
    )


def source_revendiquee(f: dict) -> bool:
    """Une source où les auteurs présentent leurs propres résultats sans relecture par des pairs (prépublication, page
    d'un fournisseur sur son produit) : sa plateforme ou son domaine est au bloc `revendique:` du registre."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    rv = cfg.revendique()
    host = str(f.get("host") or "").lower().removeprefix("www.")
    return f.get("plateforme") in rv["plateformes"] or any(host == d or host.endswith("." + d) for d in rv["domaines"])


RE_RESULTAT = re.compile(
    r"\d+(?:[,.]\d+)?\s*%|\d+(?:[,.]\d+)?\s*fois(?![a-z])|(?:divise|multiplie)\w*\s+par\s+\d|"
    r"\d\s*[x×](?![a-z0-9])|[x×]\s*\d|points? de pourcentage"
)
RE_ATTRIBUTION = re.compile(r"(?<![a-z])(?:selon|annonc\w*|revendiqu\w*)(?![a-z])")
RE_RAPPORTE = re.compile(r"(?<![a-z])(?:selon|ecri\w*|dit|disent|rapport\w*)(?![a-z])")


def chiffre_sans_attribution(texte_: str) -> str | None:
    """Le premier chiffre de résultat (pourcentage, « N fois », « divise par N ») d'une phrase sans verbe
    d'attribution (selon, annonce, revendique), sinon None. Statut « revendiqué » de STE-Enquête (03.11.2026)."""
    for p in phrases(str(texte_)):
        q = plier(p).replace("’", "'")
        m = RE_RESULTAT.search(q)
        if m and not RE_ATTRIBUTION.search(q):
            return m.group(0)
    return None


MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def nombres(texte_: str) -> list[str]:
    """Les nombres d'un texte, hors dates (« 29 octobre 2026 ») et années seules (« en 2026 »)."""
    t = re.sub(rf"(?<!\d)\d{{1,2}}(?:er)?\s+(?:{'|'.join(MOIS)})(?:\s+\d{{4}})?", " ", str(texte_), flags=re.I)
    t = re.sub(r"(?<![\d.,])(?:19|20)\d{2}(?![\d.,]\d)", " ", t)
    return re.findall(r"\d+(?:[.,\u00a0\u202f ]\d+)*", t)


def nettoyer(texte_: str) -> str:
    """Le balisage markdown restant (*…*, _…_) ôté, et l'élision faite devant une voyelle (« de Asana » →
    « d'Asana ») : des corrections sûres, sans modèle ni relance (mainteneur, 03.11.2026)."""
    t = re.sub(r"(?<!\w)[*_]+([^*_]+?)[*_]+(?!\w)", r"\1", str(texte_)).replace("*", "")
    return re.sub(r"(?<![\w'’])([Dd])e ([AEIOUYÀÂÉÈÊÎÔaeiouyàâéèêîô])", r"\1'\2", t)


def raison(
    texte_: str,
    sources: str,
    mini: int,
    maxi: int,
    connus: set[str] | None = None,
    revendique: bool = False,
    rapporte: bool = False,
    phrases_max: int = PHRASES_PAR_PARAGRAPHE,
    nombres_max: int | None = None,
    mots_phrase: int = MOTS_PAR_PHRASE,
    forme: bool = True,
) -> str:
    """Pourquoi un texte du modèle ne tient pas le contrôle (vide : il tient) : pas de réponse, fenêtre de mots, ou les
    noms, sigles et nombres absents des sources."""
    from newskit import synthese as sy  # noqa: PLC0415

    if not str(texte_).strip():
        return "pas de réponse"
    n = compter_mots(texte_)
    if not mini <= n <= maxi:
        return f"{n} mots, hors de la fenêtre de {mini} à {maxi}"
    ps = phrases(texte_)
    longue = next((compter_mots(p) for p in ps if compter_mots(p) > mots_phrase), 0)
    if forme and longue:  # forme=False : longueur des phrases, leur nombre et les groupes libres
        return f"phrase de {longue} mots"
    if forme and len(ps) > phrases_max:
        return f"paragraphe de {len(ps)} phrases"
    if forme and groupe_trop_long(texte_):
        return "groupe nominal trop long"
    sources = avec_noms_francais(sources)
    tr = tournure(texte_)
    if tr:
        return f"tournure : {tr}"
    pv = preuve(texte_)
    if pv:
        return pv
    if revendique and chiffre_sans_attribution(texte_):
        return "chiffre revendiqué sans attribution"
    if rapporte and not RE_RAPPORTE.search(plier(str(texte_)).replace("’", "'")):
        return "fait rapporté sans attribution"
    if nombres_max is not None and len(nombres(texte_)) > nombres_max:
        return f"{len(nombres(texte_))} nombres, {nombres_max} au plus"
    ex = exactitude(texte_, sources)
    if ex:
        return ex
    a_verifier = sans_nombres_ni_dates(texte_)
    if connus is not None:
        essai = set(connus)
        sg = sigle_non_developpe(texte_, essai)
        if sg:
            return f"sigle non développé : {sg}"
        a_verifier = _sans_developpements(a_verifier, plier(sources))
    manque = sy.verifier(a_verifier, sources)
    if manque:
        return f"absent des sources : {', '.join(manque)}"
    if connus is not None:
        connus |= essai
    return ""
    manque = sy.verifier(texte_, sources)
    return f"absent des sources : {', '.join(manque)}" if manque else ""


# Sans « chercheur » : un titre désigne un chercheur par sa fonction, sans son nom (mainteneur, 06.11.2026).
RE_PERIPHRASE = re.compile(
    r"(?<![a-z])(?:une?|des|le|la|les|l') ?(?:haute? )?(?:dirigeante?|responsable|experte?|fonctionnaire|"
    r"ministre|juge|elue?|banquiere?|gouverneure?|sous-gouverneure?|porte-parole|commissaire|economiste|specialiste|"
    r"officielle?|patronne?|directeur|directrice|presidente?|avocate?|professeure?|analyste)s?(?![a-z])"
)
NON_PRENOMS = {
    "deutsche",
    "banque",
    "bank",
    "commission",
    "union",
    "parlement",
    "conseil",
    "cour",
    "agence",
    "office",
    "autorite",
    "european",
    "europeen",
    "europeenne",
    "ai",
    "open",
    "google",
    "meta",
    "gemeente",
    "federal",
    "national",
    "nationale",
    "international",
    "internationale",
    "selon",
    "le",
    "la",
    "les",
    "l'",
    "un",
    "une",
}
RE_NOM_PERSONNE = re.compile(r"(?<![\w'’])([A-ZÀ-Ý][a-zà-ÿ'’-]+(?: [A-ZÀ-Ý][a-zà-ÿ'’-]+){1,2})(?=,\s*\w|\s+\()")


RE_GENERIQUE = re.compile(
    r"(?<![a-z])((?:des|plusieurs|certain(?:e)?s|divers(?:es)?|de nombreu(?:x|ses)) (?:controles|"
    r"systemes|acteurs|incidents|entreprises|outils|plateformes|services|organisations|pays|"
    r"institutions|portails|sites|mesures|failles|donnees))(?![a-z])"
)
RE_OBJET = re.compile(
    r"(?:including|such as|notably|namely|notamment|dont|comme)\s+((?:the\s+)?[A-Z][\w’'.-]*"
    r"(?:[\s,]+(?:and|et|&|of|de|du|des|the)?\s*[A-Z\w][\w’'.-]*){0,7})"
)


def objet_nomme(sources: str) -> str:
    """L'objet concret que la source donne après « including », « such as », « notamment » (un nom propre d'abord),
    jusqu'à la fin du groupe ; vide si elle n'en donne pas."""
    m = RE_OBJET.search(str(sources))
    return re.split(r"[),;.]", m.group(1))[0].strip() if m else ""


def formule_generique(texte_: str, sources: str) -> str:
    """« des contrôles », « plusieurs incidents » quand la source nomme l'objet et que le texte ne le nomme pas :
    la raison du refus, sinon vide (mainteneur, 03.11.2026 : « des contrôles de sécurité -> lesquels ? »)."""
    m = RE_GENERIQUE.search(plier(str(texte_)))
    obj = objet_nomme(sources) if m else ""
    if not obj or plier(re.split(r"\s+(?:and|et|&)\s+", obj)[0]) in plier(str(texte_)):
        return ""
    return f"formule générique : {m.group(1)} ; la source nomme {obj}"


def periphrase(titre: str) -> bool:
    """Un titre qui désigne une personne par sa fonction (« une dirigeante », « un responsable », « des chercheurs »)."""
    return bool(RE_PERIPHRASE.search(plier(str(titre)).replace("’", "'")))


def refus_periphrase(titre: str, nomme: str) -> str:
    """« périphrase au lieu du nom : X » si le titre désigne par sa fonction la personne X que la source nomme, sans la
    nommer (mainteneur, 03.11.2026 : « qui ? ») ; vide si X est dans le titre : « António Costa invite les dirigeants de
    l'UE » nomme qui agit (mainteneur, 04.11.2026 : le titre anglais de la une, après deux refus de ce genre)."""
    if not nomme or not periphrase(titre) or plier(nomme) in plier(str(titre)):
        return ""
    return f"périphrase au lieu du nom : {nomme}"


def personne_nommee(texte_: str, sources: str) -> str:
    """Le premier nom de personne d'un chapeau ou d'un descriptif, présent dans les sources : deux ou trois mots
    capitalisés suivis d'une apposition (« Sabine Mauderer, première sous-gouverneure »), hors noms d'organisation.
    Vide sinon. STE-Enquête, section 2 : une personne est nommée avec son rôle à sa première mention."""
    for m in RE_NOM_PERSONNE.finditer(str(texte_)):
        nom = m.group(1)
        if plier(nom.split()[0]) in NON_PRENOMS:
            continue
        if plier(nom) in plier(str(sources)):
            return nom
    return ""


def journaliser_refus(graphe: str, essai: int, refus: list[dict]) -> None:
    """Chaque refus du contrôle, avec sa raison, dans cache/lettre_refus.jsonl. Jamais bloquant."""
    if not refus:
        return
    try:
        JOURNAL_REFUS.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_REFUS.open("a", encoding="utf-8") as fh:
            for r in refus:
                fh.write(
                    json.dumps(
                        {
                            "quand": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "graphe": graphe,
                            "essai": essai,
                            **{k: r[k] for k in ("genre", "n", "champ", "raison", "texte")},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError as e:
        print(f"  lettre : refus non journalisés ({e})", file=sys.stderr)


def _appeler(invite: str, graphe: str):
    """(sortie JSON du modèle ou None, coût) ; une ligne au registre des coûts."""
    args = [
        *couts.CLAUDE_P,
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        MODELE,
        "--json-schema",
        json.dumps(SCHEMA),
    ]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=invite, capture_output=True, text=True, timeout=DELAI, cwd=RACINE, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        couts.enregistrer(POSTE, graphe, MODELE, None, time.time() - t0, note=type(e).__name__)
        return None, 0.0
    env = couts.enregistrer(POSTE, graphe, MODELE, r, time.time() - t0)
    cout = float(env.get("total_cost_usd") or 0)
    if r.returncode != 0 or not env or env.get("is_error"):
        return None, cout
    brut = env.get("structured_output") or env.get("result")
    if isinstance(brut, str):
        try:
            brut = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", brut.strip(), flags=re.S))
        except json.JSONDecodeError:
            return None, cout
    return (brut if isinstance(brut, dict) else None), cout


def _revendiquee(fa: dict, fiches: dict) -> bool:
    try:
        k = principale(fa, fiches)
    except Exception:  # noqa: BLE001 — élément sans notions : sa première fiche
        k = next((x for x in fa["fiches"] if x in fiches), None)
    return bool(k) and k in fiches and source_revendiquee(fiches[k])


def faute_de_preuve(phrase: str, sources: str, revendique: bool = False) -> str:
    """La faute de preuve d'une phrase (vide : prouvée) : nombre, date ou nom absent des sources, chiffre revendiqué
    sans attribution. Seule une faute de preuve retire une phrase après le second refus (mainteneur, 03.11.2026)."""
    from newskit import synthese as sy  # noqa: PLC0415

    sources = avec_noms_francais(sources)
    ex = exactitude(phrase, sources)
    if ex:
        return ex
    if revendique and chiffre_sans_attribution(phrase):
        return "chiffre revendiqué sans attribution"
    manque = sy.verifier(_sans_developpements(sans_nombres_ni_dates(phrase), plier(sources)), sources)
    return f"absent des sources : {', '.join(manque)}" if manque else ""


# Le lecteur n'est pas juriste (mainteneur, 04.11.2026 : « notre public n'est pas constitué de juristes. comment donner un
# sens accessible hors du cercle des initiés ? ») ; « un lecteur non-spécialiste mais pas non plus ignorant. c'est le
# jargon spécialisé qui pose problème ». Les règles valent pour chaque chapeau et chaque descriptif.
LECTEUR_NON_INITIE = (
    "Le lecteur est cultivé et suit l'actualité, mais n'est pas spécialiste du domaine : c'est le jargon de métier "
    "qui le perd, pas les notions communes. N'explique pas ce qu'il sait (l'UE, Google, un tribunal, une loi, une "
    "start-up) ; traduis ce que seuls les spécialistes disent. (1) Dis d'abord le conflit ou l'enjeu concret, "
    "qui demande ou reproche quoi à qui (« l'éditeur de presse Like Company reproche à Google d'utiliser ses articles "
    "pour Gemini »). (2) Un terme de procédure ou de métier se remplace par ce qu'il veut dire (« irrecevable » : « la "
    "Cour ne devrait pas répondre » ; « question préjudicielle » : « question posée à la Cour par un juge national »). "
    "(3) Une fonction peu connue se présente par son rôle à sa première mention (« avocat général, chargé de "
    "conseiller la Cour »). (4) Ce qui reste à décider se dit quand la source le dit (« la Cour rendra son arrêt plus "
    "tard »). (5) Pas de numéro d'article, de directive ni d'affaire dans le chapeau ou le descriptif : le nom courant "
    "du texte suffit (« l'AI Act », « le droit des éditeurs de presse »). (6) Les chiffres et les notions d'expert "
    "avec parcimonie : un chiffre porte le fait, les autres se disent par une comparaison exacte tirée des sources "
    "(« 11,2 %, et presque le double chez les grandes entreprises », jamais « 11,2 % et 22,3 % ») : le double, le "
    "triple, la moitié, un sur trois, seulement si le calcul sur les nombres des sources le donne ; une notion d'expert "
    "au plus, dite en mots courants. (7) Ces règles ne permettent rien que les sources ne disent.\n"
)

# Les règles de rédaction de la lettre, en un seul texte. Le rédacteur de la lettre et celui du fil les reçoivent
# (mainteneur, 05.11.2026 : « le titre et le texte de chaque billet doivent correspondre aux mêmes standards que les
# newsletter »).
CONTENU_D_ABORD = (
    "Quand la source rend compte d'un rapport, d'une étude, d'une décision ou d'un débat, le titre et le texte portent ce "
    "qu'ils établissent : résultats, chiffres, exemples, positions. La parution, le format et la liste des intervenants "
    "sont des informations mineures, de contextualisation uniquement.\n"
)
REGLES = (
    LECTEUR_NON_INITIE
    + CONTENU_D_ABORD
    + f"Français simplifié : {MOTS_PAR_PHRASE} mots au plus par phrase ; un sujet par phrase, voix active, présent ou passé composé ; le conditionnel "
    "seulement si la source présente elle-même le fait comme une hypothèse ; un mot, un sens : garde le mot de "
    "la source, sans synonyme pour varier ; pas plus de trois noms à la suite (« la révision de la clause du "
    "règlement de la Commission » est trop long) ; les nombres en chiffres avec leur unité (« 3 000 start-up », "
    "« 6,43 milliards de dollars ») ; un sigle s'écrit en toutes lettres à sa première mention dans la lettre, "
    "le sigle entre parenthèses (« la Bank for International Settlements (BIS) »), sauf AI Act, RGPD, UE, IA, "
    "OCDE.\n"
    "Vocabulaire de la preuve : verbes d'attribution dit, écrit, annonce, revendique, selon, affirme ; jamais "
    "prétend, admet, reconnaît, révèle, dénonce, avoue. Une prépublication ou la page d'un fournisseur sur son produit donne des résultats revendiqués : tout chiffre de résultat s'y attribue (« selon les auteurs », « selon eux », « X annonce », « X revendique »). Aucun adjectif d'évaluation hors citation (historique, "
    "inédit, majeur, sans précédent, controversé, crucial, révolutionnaire). Des dates absolues (« le 2 novembre "
    "2026 »), jamais « hier », « la semaine dernière », « récemment ». Une personne est nommée avec son rôle à "
    "sa première mention (« Sabine Mauderer, vice-présidente de la Deutsche Bundesbank »). Aucune intention "
    "prêtée à un acteur : ce qu'il a fait ou dit.\n"
    # Les règles du titre et du texte, du fil et de la lettre ensemble (mainteneur, 06.11.2026 : « les consignes au
    # rédacteur du fil devraient être les mêmes que celles au rédacteur des lettres »).
    "Titre et texte (chapeau, descriptif ou billet) : le titre dit le résultat qui compte pour le lecteur, en une "
    "phrase simple, sujet, verbe, objet ; il garde la portée de la source et se comprend seul, par un lecteur qui ne "
    "connaît pas le sujet ; un mot français courant remplace le mot anglais ou le mot de métier ; pas de "
    "question. Le titre et le texte disent le même fait, celui de la source. La première phrase du texte dit le fait en mots simples, qui, où, quoi ; "
    "chaque phrase porte un fait ; une source courte donne un texte court, sans remplissage. Le pays où le fait se "
    "passe est nommé dans le titre ou dans la première phrase, quand le fait tient à un pays (une élection, une loi, "
    "une autorité, une étude menée dans un pays). Le titre et le texte nomment l'objet concret que la source donne, "
    "pas sa catégorie (« les portails de Hugging Face et d'Australian Medicare », jamais « des contrôles de "
    "sécurité » ou « des systèmes »).\n"
    "Personnes et attribution : le titre nomme la personne que la source nomme ; une personne ou une organisation "
    "peu connue est suivie de sa fonction ; un chercheur se désigne par sa fonction, sans son nom (« Un chercheur "
    "affirme … »). Un papier de recherche : « Des chercheurs de <organisation> ont publié … », sans la liste de leurs "
    "noms. Celui qui parle porte l'attribution : « Selon Nom (Institution), … ». « Selon <éditeur> » seulement quand "
    "l'éditeur rapporte le fait d'un autre ; jamais quand l'éditeur est la source.\n"
    "Fidélité : rien que la source ne dise ; les noms propres, les sigles et les nombres s'écrivent comme dans les "
    "sources, sauf les formes françaises de la règle suivante ; pas de date si les sources n'en donnent pas ; "
    "l'élision devant une voyelle (« d'Asana », jamais « de Asana »).\n"
    "Noms et fonctions en français : les organisations sous leur forme française (OCDE, BRI, Commission "
    "européenne, Banque centrale européenne), les lieux aussi (Francfort, Bruxelles) ; les fonctions traduites, "
    "accordées comme dans la source (« First Deputy Governor » : « première sous-gouverneure » ou « premier "
    "sous-gouverneur »).\n"
    "Mise en forme : une prose d'information sobre. Pas de liste par trois pour le rythme (une liste reste permise "
    "si la source énumère ces éléments) ; pas de deux-points qui ménagent une révélation ; pas de « non pas X, "
    "mais Y » ; ni « plutôt que », ni « alors que », ni « tandis que » ; pas de slogan, de chute, de métaphore ni de personnification (« le règlement ouvre la porte », "
    "« l'IA frappe à la porte ») ; pas de tournure de remplissage : « il convient de noter », « force est de "
    "constater », « dans un contexte de », « à l'heure où », « véritable », « crucial », « clé » en adjectif "
    "(« rôle clé »), « un signal fort ».\n"
)


def ecarts_de_forme(
    texte_: str, mini: int, maxi: int, phrases_max: int = CHAPEAU_PHRASES, nombres_max: int | None = CHAPEAU_NOMBRES
) -> list[str]:
    """Les écarts de forme d'un texte (fenêtre de mots, phrases trop longues, nombre de phrases, groupe nominal,
    tournure, vocabulaire de la preuve) : ils ne retirent jamais une description, ils départagent les tentatives."""
    ec, n, ps = [], compter_mots(texte_), phrases(texte_)
    if not mini <= n <= maxi:
        ec.append(f"{n} mots, hors de la fenêtre de {mini} à {maxi}")
    ec += [f"phrase de {compter_mots(p)} mots" for p in ps if compter_mots(p) > MOTS_PAR_PHRASE]
    if len(ps) > phrases_max:
        ec.append(f"paragraphe de {len(ps)} phrases")
    if nombres_max is not None and len(nombres(texte_)) > nombres_max:
        ec.append(f"{len(nombres(texte_))} nombres, {nombres_max} au plus")
    if groupe_trop_long(texte_):
        ec.append("groupe nominal trop long")
    tr = tournure(texte_)
    if tr:
        ec.append(f"tournure : {tr}")
    pv = preuve(texte_)
    if pv:
        ec.append(pv)
    return ec


def limites_de_phrases(genre: str, corps: bool) -> int:
    """Phrases au plus : 4 pour le chapeau de la une, 2 pour un descriptif (mainteneur, 04.11.2026)."""
    if not corps:
        return PHRASES_PAR_PARAGRAPHE
    return UNE_PHRASES if genre == "fait" else CHAPEAU_PHRASES


def limites_de_nombres(genre: str, corps: bool) -> int | None:
    if not corps:
        return None
    return UNE_NOMBRES if genre == "fait" else CHAPEAU_NOMBRES


def choisir_description(
    tentatives: list[str],
    sources: str,
    mini: int,
    maxi: int,
    revendique: bool = False,
    rapporte: bool = False,
    phrases_max: int = CHAPEAU_PHRASES,
    nombres_max: int = CHAPEAU_NOMBRES,
) -> tuple[str, str]:
    """Après le second refus (mainteneur, 03.11.2026 : « ne pas publier sans description, même si elle n'est pas totalement
    conforme au template ») : dans chaque tentative, les phrases fautives sur la preuve sont retirées ; une tentative
    sans phrase prouvée, ou sans « selon » pour un fait rapporté, est écartée. Parmi les autres, la moins chargée en
    écarts de forme l'emporte (la plus récente à égalité). Rend (texte, note du journal) ; ("", raison) si aucune ne
    reste : le billet sort de la lettre, jamais de titre seul."""
    meilleure, note = None, "aucune phrase prouvée : billet retiré de la lettre"
    for t in tentatives:
        if not str(t or "").strip():
            continue
        ps = phrases(t)
        gardees = [p for p in ps if not faute_de_preuve(p, sources, revendique)]
        texte_ = " ".join(gardees)
        if not gardees or (rapporte and not RE_RAPPORTE.search(plier(texte_).replace("’", "'"))):
            continue
        ecarts = ecarts_de_forme(texte_, mini, maxi, phrases_max, nombres_max)
        if meilleure is None or len(ecarts) < meilleure[1]:
            retirees = len(ps) - len(gardees)
            meilleure = (
                texte_,
                len(ecarts),
                "gardée malgré la forme : "
                + ("; ".join(ecarts) or "aucun écart")
                + (f" ; {retirees} phrase(s) sans preuve retirée(s)" if retirees else ""),
            )
    if meilleure is None:
        return "", note
    return ponctuation_fr(meilleure[0]), meilleure[2]


def _lire(
    brut: dict, faits: list, autres: list, brefs: list, fiches: dict, connus: set[str] | None = None
) -> tuple[dict, dict, list[dict]]:
    """Les textes qui tiennent le contrôle, et les refus (élément, champ, raison, texte), réponses manquantes comprises."""

    def rang(x, n_max):
        try:
            i = int(x.get("n"))
        except (TypeError, ValueError, AttributeError):
            return None
        return i if 1 <= i <= n_max else None

    rep_f = {i: x for x in brut.get("faits") or [] if (i := rang(x, len(faits)))}
    tous = [*autres, *brefs]
    rep_a = {j: x for x in brut.get("autres") or [] if (j := rang(x, len(tous)))}
    out, titres, refus = {}, {}, []

    connus = set() if connus is None else connus  # les sigles déjà développés, dans l'ordre de la lettre

    def revendiquee(fa: dict) -> bool:
        return _revendiquee(fa, fiches)

    def tenir(genre, n, champ, t, sources, mini, maxi, rev=False, rap=False, nomme=""):
        t = nettoyer(t)
        corps = champ in ("paragraphe", "descriptif")
        r = raison(
            t,
            sources,
            mini,
            maxi,
            connus if corps else None,
            rev,
            rap,  # rev : jamais un titre
            limites_de_phrases(genre, corps),
            limites_de_nombres(genre, corps),
        )
        if not r and champ == "titre" and genre == "autre" and len(t) > TITRE_RUBRIQUE_CARACTERES:
            r = f"titre de {len(t)} caractères, {TITRE_RUBRIQUE_CARACTERES} au plus"
        if not r:
            r = formule_generique(t, sources)
        if not r and champ == "titre":
            r = refus_periphrase(t, nomme)
        if r:
            refus.append({"genre": genre, "n": n, "champ": champ, "raison": r, "texte": t})
        return "" if r else ponctuation_fr(t)

    for i in range(1, len(faits) + 1):
        x, sources = rep_f.get(i) or {}, _sources_de(faits[i - 1], fiches)
        par, gras = noms_en_gras(str(x.get("paragraphe") or "").strip(), sources)
        par_ok = tenir(
            "fait",
            i,
            "paragraphe",
            par,
            sources,
            MOTS_MIN,
            MOTS_MAX,
            revendiquee(faits[i - 1]),
            bool(faits[i - 1].get("rapporte")),
        )
        out[i] = {
            "titre": tenir(
                "fait",
                i,
                "titre",
                str(x.get("titre") or "").replace("**", "").strip(),
                sources,
                1,
                MOTS_TITRE,
                nomme=personne_nommee(par, sources),
            ),
            "paragraphe": par_ok,
            "gras": gras if par_ok else [],
        }
    for j in range(1, len(tous) + 1):
        x, sources = rep_a.get(j) or {}, _sources_de(tous[j - 1], fiches)
        autre = j <= len(autres)
        genre = "autre" if autre else "breve"
        t = tenir(
            genre,
            j,
            "titre",
            str(x.get("titre") or "").replace("**", "").strip(),
            sources,
            1,
            MOTS_TITRE if autre else MOTS_TITRE_BREF,
            nomme=personne_nommee(str(x.get("descriptif") or ""), sources),
        )
        # La brève a son descriptif, comme l'autre fait saillant (mainteneur, 04.11.2026).
        d = tenir(
            genre,
            j,
            "descriptif",
            noms_en_gras(str(x.get("descriptif") or "").strip(), "")[0],
            sources,
            DESCRIPTIF_MIN,
            DESCRIPTIF_MAX,
            revendiquee(tous[j - 1]),
        )
        titres[j] = {"titre": t, "descriptif": d}
    return out, titres, refus


def _consigne_de_relance(refus: list[dict], nf: dict, na: dict) -> str:
    lignes = []
    for r in refus:
        bloc = (
            f"FAIT {nf[r['n']]}"
            if r["genre"] == "fait"
            else f"{'AUTRE' if r['genre'] == 'autre' else 'BRÈVE'} {na[(r['genre'], r['n'])]}"
        )
        lignes.append(
            f"- {bloc}, {r['champ']} refusé : {r['raison']}" + (f" (« {r['texte'][:300]} »)" if r["texte"] else "")
        )
    return (
        "Ta première réponse a été refusée par le contrôle pour les éléments ci-dessous. Réécris-les en tenant la "
        "fenêtre de mots et en n'écrivant que des noms, sigles et nombres présents dans les sources, tels qu'ils y "
        "sont écrits.\n" + "\n".join(lignes) + "\n\n"
    )


def rediger(faits: list[dict], fiches: dict[str, dict], graphe: str, autres=(), brefs=()) -> tuple[dict, dict, float]:
    """({rang: {titre, paragraphe, gras}}, {rang: {titre, descriptif}}, coût en dollars) : un appel au modèle pour la
    lettre entière (faits à la une, autres faits saillants, brèves), consigné au registre des coûts. Chaque refus du
    contrôle est journalisé avec sa raison ; les seuls éléments refusés sont redemandés une fois, raison donnée, si le
    plafond le permet ; un deuxième refus laisse la chaîne vide (repli : le titre de la source). Vide, sans appel, si le
    plafond de dépense estimé est dépassé ou en limite d'usage ; vide si l'appel échoue."""
    autres, brefs = list(autres), list(brefs)
    if not (faits or autres or brefs) or couts.en_limite():
        return {}, {}, 0.0
    invite = _invite(faits, fiches, autres, brefs)
    if estimer_usd(invite) > PLAFOND_USD:
        print(
            f"  lettre : appel au modèle non tenté, {estimer_usd(invite):.2f} $ estimés pour un plafond de "
            f"{PLAFOND_USD:.2f} $",
            file=sys.stderr,
        )
        return {}, {}, 0.0
    brut, cout = _appeler(invite, graphe)
    if brut is None:
        return {}, {}, cout
    connus: set[str] = set()
    out, titres, refus = _lire(brut, faits, autres, brefs, fiches, connus)
    journaliser_refus(graphe, 1, refus)
    if not refus:
        return out, titres, cout
    # La relance : les seuls éléments refusés, renumérotés, la raison donnée.
    rf = sorted({r["n"] for r in refus if r["genre"] == "fait"})
    ra = sorted({r["n"] for r in refus if r["genre"] == "autre"})
    rb = sorted({r["n"] for r in refus if r["genre"] == "breve"})
    tous = [*autres, *brefs]
    nf = {i: k for k, i in enumerate(rf, 1)}
    na = {("autre", j): k for k, j in enumerate(ra, 1)}
    na.update({("breve", j): k for k, j in enumerate(rb, len(ra) + 1)})
    f2, a2, b2 = [faits[i - 1] for i in rf], [tous[j - 1] for j in ra], [tous[j - 1] for j in rb]
    invite2 = _consigne_de_relance(refus, nf, na) + _invite(f2, fiches, a2, b2)
    garde = {(r["genre"], r["n"], r["champ"]) for r in refus}
    if cout + estimer_usd(invite2, len(f2), len(a2), len(b2)) > PLAFOND_USD or couts.en_limite():
        print("  lettre : relance non tentée (plafond de dépense)", file=sys.stderr)
        _departager(out, titres, refus, [], faits, tous, fiches, graphe)
        return out, titres, cout
    brut2, cout2 = _appeler(invite2, graphe)
    cout += cout2
    if brut2 is None:
        _departager(out, titres, refus, [], faits, tous, fiches, graphe)
        return out, titres, cout
    o2, t2, refus2 = _lire(brut2, f2, a2, b2, fiches, connus)
    origine = {"fait": rf, "autre": [*ra, *rb], "breve": [*ra, *rb]}
    refus2 = [{**r2, "n": origine[r2["genre"]][r2["n"] - 1]} for r2 in refus2]
    journaliser_refus(graphe, 2, [r2 for r2 in refus2 if (r2["genre"], r2["n"], r2["champ"]) in garde])
    for k, i in enumerate(rf, 1):
        for champ in ("titre", "paragraphe"):
            if ("fait", i, champ) in garde and o2[k][champ]:
                out[i][champ] = o2[k][champ]
                if champ == "paragraphe":
                    out[i]["gras"] = o2[k]["gras"]
    for k, j in enumerate([*ra, *rb], 1):
        genre = "autre" if k <= len(ra) else "breve"
        for champ in ("titre", "descriptif"):
            if (genre, j, champ) in garde and t2[k][champ]:
                titres[j][champ] = t2[k][champ]
    _departager(
        out,
        titres,
        refus,
        [r2 for r2 in refus2 if (r2["genre"], r2["n"], r2["champ"]) in garde],
        faits,
        tous,
        fiches,
        graphe,
    )
    return out, titres, cout


# Un titre refusé pour sa seule forme (longueur d'une ligne ou en mots, formule générique, périphrase) : le plus court des
# refusés est gardé. Sans cela le repli prenait le titre de la source, en anglais à la une du 05.11.2026 (mainteneur : « j'ai
# encore des titres en anglais, ça ne va pas »).
RE_TITRE_FORME = re.compile(
    r"titre de \d+ caractères, \d+ au plus$|formule générique : |"
    r"\d+ mots, hors de la fenêtre de \d+ à \d+$|périphrase au lieu du nom : "
)


def _departager(
    out: dict, titres: dict, refus1: list[dict], refus2: list[dict], faits: list, tous: list, fiches: dict, graphe: str
) -> None:
    """Un chapeau ou un descriptif encore vide après les essais : choisir_description sur les tentatives refusées ;
    sinon le billet est marqué « retire » (il sort de la lettre). Chaque décision au journal des refus (essai 3)."""
    textes = {}
    for r in [*refus1, *refus2]:  # la plus récente en tête
        if r["champ"] in ("paragraphe", "descriptif"):
            textes.setdefault((r["genre"], r["n"], r["champ"]), []).insert(0, r["texte"])
    notes = []
    courts: dict = {}  # un titre de rubrique refusé pour sa seule longueur : le plus court gardé
    for r in [*refus1, *refus2]:
        if r["champ"] == "titre" and RE_TITRE_FORME.match(r["raison"]) and str(r["texte"]).strip():
            courts.setdefault((r["genre"], r["n"]), []).append(r["texte"])
    for (genre, n), tent in courts.items():
        cible = out.get(n) if genre == "fait" else titres.get(n)
        if cible is not None and not cible.get("titre"):
            cible["titre"] = ponctuation_fr(min(tent, key=len))
            notes.append(
                {
                    "genre": genre,
                    "n": n,
                    "champ": "titre",
                    "texte": cible["titre"],
                    "raison": f"gardé malgré la forme : titre de {len(cible['titre'])} caractères",
                }
            )
    # Un titre encore vide prendrait celui de la source. D'une source étrangère, ce serait un titre étranger : le billet
    # sort de la lettre. Une source en français garde son titre en repli.
    from newskit import fil  # noqa: PLC0415 — fil importe lettre

    for genre, n in dict.fromkeys((r["genre"], r["n"]) for r in [*refus1, *refus2] if r["champ"] == "titre"):
        cible = out.get(n) if genre == "fait" else titres.get(n)
        fa = faits[n - 1] if genre == "fait" else tous[n - 1]
        k = next((x for x in fa["fiches"] if x in fiches), None)
        langue = fil.langue_source(fiches[k]) if k else ""
        if cible is None or cible.get("titre") or langue == "fr":
            continue
        cible["retire"] = True
        notes.append(
            {
                "genre": genre,
                "n": n,
                "champ": "titre",
                "texte": "",
                "raison": f"titre refusé sur le fond, source en « {langue or 'inconnue'} » : billet retiré",
            }
        )
    for (genre, n, champ), tent in textes.items():
        cible = out.get(n) if genre == "fait" else titres.get(n)
        if cible is None or cible.get(champ):
            continue
        fa = faits[n - 1] if genre == "fait" else tous[n - 1]
        sources = _sources_de(fa, fiches)
        mini, maxi = (MOTS_MIN, MOTS_MAX) if genre == "fait" else (DESCRIPTIF_MIN, DESCRIPTIF_MAX)
        une = genre == "fait"
        t, note = choisir_description(
            tent,
            sources,
            mini,
            maxi,
            _revendiquee(fa, fiches),
            bool(fa.get("rapporte")) if une else False,
            UNE_PHRASES if une else CHAPEAU_PHRASES,
            UNE_NOMBRES if une else CHAPEAU_NOMBRES,
        )
        if t:
            cible[champ] = t
            if champ == "paragraphe":
                cible["gras"] = noms_en_gras(t, sources)[1]
        else:
            cible["retire"] = True
        notes.append({"genre": genre, "n": n, "champ": champ, "raison": note, "texte": t})
    journaliser_refus(graphe, 3, notes)


# --- Données ------------------------------------------------------------------------------------------------------


def editeur_de(champs: dict, host: str) -> str:
    pub = re.sub(
        r"\s*\((?:plan de site|page de liste|flux|rss|newsletter)\)\s*$",
        "",
        str(champs.get("publication") or ""),
        flags=re.I,
    ).strip()
    pub = re.split(r"\s+[—–]\s+|\s*\|\s*", pub)[0].strip()  # « BRI — publications, communiqués… » → « BRI »
    return pub or host.removeprefix("www.")


def sujet_de_la_source(source: str) -> str:
    """Le sujet que l'éclaireur cherchait : « … — sujet : X » ou « … — peu couvert : X — question » ; sinon vide."""
    m = re.match(r"trouvé par l.éclaireur — (?:sujet|peu couvert) : (.+)", str(source))
    return re.split(r" — ", m.group(1))[0].strip() if m else ""


def _mot_present(m: str, texte_plie: str) -> bool:
    fin = r"(?![a-z0-9])" if len(m) <= 4 else ""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(m) + fin, texte_plie))


RE_IA = re.compile(
    r"(?<![a-z0-9])(ai|a\.i\.|ia|artificial intelligence|intelligence artificielle|kunstliche intelligenz|"
    r"kunstmatige intelligentie|llms?|(?:large )?language models?|machine learning|deep learning|neural|"
    r"algorithm\w*|algoritm\w*|deepfakes?|chatbots?|chatgpt|\w*gpt\w*|generative|agentic|인공지능)(?![a-z0-9])"
)


def rubrique_de_fiche(f: dict, rubs: list[dict]) -> str | None:
    """La rubrique d'une fiche : celle du sujet déclaré que l'éclaireur cherchait ; sinon celle du sujet dont le
    vocabulaire déclaré (`mots`, chacun compté une fois après pliage) se lit le mieux dans son titre (poids 3) et son
    début (poids 1), à condition que la fiche parle d'IA (RE_IA dans le titre ou le début) ; il faut un mot du titre ou trois du début. Égalité : l'ordre des rubriques. Aucune : None."""
    if not rubs:
        return None
    if f.get("sujet"):
        for r in rubs:
            if any(f["sujet"] == x["fr"] or f["sujet"].startswith(x["fr"][:40]) for x in r["sujets"]):
                return r["nom"]
    titre = plier(str(f.get("titre") or ""))
    debut = plier(_extrait(f, 300))
    if not RE_IA.search(titre + " " + debut):
        return None
    meilleur, score_max = None, 0
    for r in rubs:
        for x in r["sujets"]:
            mots = sorted({plier(m) for m in x.get("mots") or []})
            sc = 3 * sum(_mot_present(m, titre) for m in mots) + sum(_mot_present(m, debut) for m in mots)
            if sc > score_max:
                meilleur, score_max = r["nom"], sc
    return meilleur if score_max >= 3 else None


def actualite_des_rubriques(
    fiches: dict[str, dict], rubs: list[dict], jour: str, jours: int, minimum: int
) -> list[dict]:
    """Par rubrique, dans l'ordre du registre : le nombre de fiches de la semaine dont la publication vérifiée (date
    connue, pas à venir) a au plus `jours` jours, et `court` quand ce nombre est sous `minimum` (mainteneur, 03.11.2026)."""
    c = Counter(rubrique_de_fiche(f, rubs) for f in fiches.values() if recente(f, jour, jours))
    return [{"nom": r["nom"], "n": c.get(r["nom"], 0), "court": c.get(r["nom"], 0) < minimum} for r in rubs]


def alertes_d_actualite(rangs: list[dict], jours: int, minimum: int) -> list[str]:
    """Une alerte par rubrique à court d'actualité."""
    return [
        f"Rubrique à court d'actualité : {r['nom']}, {r['n']} source{'s' if r['n'] > 1 else ''} publiée"
        f"{'s' if r['n'] > 1 else ''} depuis {jours} jours au plus (seuil {minimum})."
        for r in rangs
        if r["court"]
    ]


def rubrique_majoritaire(fiches_: list[dict], rubs: list[dict]) -> str | None:
    """La rubrique la plus fréquente parmi les fiches ; égalité : l'ordre des rubriques ; aucune : None."""
    c = Counter(r for r in (rubrique_de_fiche(f, rubs) for f in fiches_) if r)
    if not c:
        return None
    ordre = [r["nom"] for r in rubs]
    return sorted(c, key=lambda n: (-c[n], ordre.index(n)))[0]


def charger_fiches(chemins: list[str]) -> dict[str, dict]:
    from newskit import fiche  # noqa: PLC0415

    out = {}
    for c in chemins:
        p = RACINE / c
        if not p.exists():
            continue
        champs, corps = fiche.lire_fichier(p)
        url = str(champs.get("url") or "")
        host = urlparse(url).netloc
        prefixe = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
        out[c] = {
            "chemin": c,
            "titre": str(champs.get("title") or p.stem),
            "url": url,
            "host": host,
            "editeur": editeur_de(champs, host),
            "plateforme": str(champs.get("plateforme") or ""),
            "date": (
                ""
                if champs.get("origine_date") == "inconnue"
                else str(champs.get("date") or (prefixe.group(1) if prefixe else ""))
            )[:10],
            "date_modification": str(champs.get("date_modification") or "")[:10],
            "texte": corps,
            "brut": p.read_text(encoding="utf-8", errors="replace"),
            "sujet": sujet_de_la_source(str(champs.get("source") or "")),
            "auteur": str(champs.get("author") or ""),
            "origine": str(champs.get("origine") or ""),
        }
    return out


def _passages(graphe: str) -> list[dict]:
    """Les passages de nouveautes.json, du plus récent au plus ancien ; [] sans fichier lisible."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    p = cfg.chemins_du_graphe(graphe)["nouveautes"]
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("passages") or []
    except (OSError, json.JSONDecodeError):
        return []


def _reunir(dans: list[dict], debut: str) -> dict:
    """Plusieurs passages en un : fiches versées et notions sans doublon, daté du plus récent."""
    vus, nouveaux, fiches = set(), [], []
    for x in dans:
        for f in x.get("fiches_versees") or []:
            if f not in vus:
                vus.add(f)
                fiches.append(f)
    ids = set()
    for x in dans:
        for n in x.get("nouveaux") or []:
            if n.get("id") not in ids:
                ids.add(n.get("id"))
                nouveaux.append(n)
    return {
        "date": dans[0]["date"],
        "debut": debut,
        "fiches_versees": fiches,
        "nouveaux": nouveaux,
        "identifiants_neufs": sum(x.get("identifiants_neufs") or 0 for x in dans),
        "notions_neuves": sum(1 for n in nouveaux if n.get("neuve")),
    }


def passages_de_la_periode(graphe: str, periode: str = "jour", jours: int = JOURS_SEMAINE) -> dict | None:
    """Le passage dont la lettre est faite. La daily réunit tous les passages depuis la lettre précédente (son
    marqueur `heure`), arrêtés compris : une plage d'un jour, quel que soit le nombre de passages (mainteneur, 05.11.2026) ;
    sans lettre précédente, le dernier passage. La weekly réunit les passages des `jours` derniers jours."""
    if periode != "semaine":
        dernier = dernier_passage(graphe)
        if dernier is None:
            return None
        marque = str((lettre_precedente(graphe, dernier["date"]) or {}).get("heure") or "")
        dans = [x for x in _passages(graphe) if "fiches_versees" in x and marque and x["date"] > marque]
        return _reunir(dans, marque) if len(dans) > 1 else dernier
    tous = [x for x in _passages(graphe) if "fiches_versees" in x]
    if not tous:
        return None
    fin = dt.date.fromisoformat(tous[0]["date"][:10])
    debut = fin - dt.timedelta(days=jours - 1)
    return _reunir([x for x in tous if dt.date.fromisoformat(x["date"][:10]) >= debut], debut.isoformat())


def lettre_precedente(graphe: str, jour: str) -> dict | None:
    """La dernière lettre gardée du graphe avant `jour` (lettres/<graphe>/<envoi>.json) ; None sans lettre lisible."""
    d = RACINE / "lettres" / graphe
    avant = sorted(p for p in d.glob("*.json") if p.stem < jour[:10]) if d.is_dir() else []
    if not avant:
        return None
    try:
        return json.loads(avant[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def versees_a_ecrire(graphe: str, passage: dict, periode: str = "jour") -> list[str]:
    """Les fiches versées du passage dont la daily est faite, sans les nouvelles déjà dites ni les fiches écartées par le
    fil (CHANTIER_NOUVELLES.md, N2). Une fiche est dite quand une lettre gardée des 30 derniers jours l'a citée, ou a
    cité une autre source de la même nouvelle. La lettre de la semaine résume la semaine : elle garde tout."""
    versees = list(passage.get("fiches_versees") or [])
    if periode != "jour":
        return versees
    registre = nv.lire(graphe)
    dites = nv.dites(registre, versees, nv.portees(graphe, str(passage.get("date") or "")))
    ecartees = nv.ecartees(registre, versees)
    return [c for c in versees if c not in dites and c not in ecartees]


def une_par_nouvelle(faits: list[dict], registre: dict) -> list[dict]:
    """Une nouvelle ne fait qu'un fait dans une lettre : de deux faits qui portent des sources de la même nouvelle, le
    mieux noté reste. L'ordre des faits est gardé."""

    def nouvelles_de(f: dict) -> set[str]:
        return {(nv.nouvelle_de(registre, c) or {}).get("chemin") or c for c in f["fiches"]}

    prises, gardes = set(), set()
    for i in sorted(range(len(faits)), key=lambda i: -faits[i].get("score", 0)):
        ids = nouvelles_de(faits[i])
        if not ids & prises:
            gardes.add(i)
            prises |= ids
    return [f for i, f in enumerate(faits) if i in gardes]


def dernier_passage(graphe: str) -> dict | None:
    """Le dernier passage de nouveautes.json, s'il porte les fiches versées (format B16) ; sinon None."""
    passages = _passages(graphe)
    return passages[0] if passages and "fiches_versees" in passages[0] else None


def dernier_banc(graphe: str, avant: str = "") -> dict | None:
    from newskit import config_sources as cfg  # noqa: PLC0415

    p = cfg.chemins_du_graphe(graphe)["base"] / "banc_historique.jsonl"
    if not p.exists():
        return None
    meilleur = None
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(l)
        except json.JSONDecodeError:
            continue
        if "graphe" in (e.get("conditions") or {}) and (not avant or str(e.get("horodatage", ""))[:10] <= avant[:10]):
            meilleur = e
    return meilleur


# --- Construction -----------------------------------------------------------------------------------------------------


def _lundi(d: str) -> str:
    x = dt.date.fromisoformat(d[:10])
    return (x - dt.timedelta(days=x.weekday())).isoformat()


def _source(f: dict) -> dict:
    return {"titre": f["titre"], "editeur": f["editeur"], "date": f["date"], "url": f["url"]}


def _source_de_breve(x: dict) -> dict:
    """La source d'une brève, lue dans l'en-tête de sa fiche (éditeur, date)."""
    try:
        f = charger_fiches([x["chemin"]])[x["chemin"]]
        return {**_source(f), "url": x.get("url") or f["url"]}
    except Exception:
        from urllib.parse import urlparse

        return {"titre": x["titre"], "editeur": urlparse(x.get("url", "")).netloc, "date": "", "url": x.get("url", "")}


def _sources_html(s: dict, x: dict) -> str:
    """Toutes les sources d'un billet, chacune avec son lien (mainteneur, 03.11.2026 : « il peut y avoir plusieurs sources
    par billet »)."""
    tous = [_lien(s, _ligne_source(s, 0), "src", LIEN)]
    tous += [_lien(o, _ligne_source(o, 0), "src", LIEN) for o in x.get("sources_plus") or []]
    return " · ".join(tous)


def libelle_affiche(x: dict) -> str:
    """Le libellé qu'on montre : le français quand la composition l'a posé (francais.py, 03.11.2026)."""
    return str(x.get("label_fr") or x["label"])


def source_de_repli(f: dict) -> dict:
    """La source d'un fait dont une fiche manque : sa première notion, en français quand elle l'a, et son éditeur."""
    return {"titre": libelle_affiche(f["notions"][0]), "editeur": f["editeur"], "date": "", "url": ""}


def _affiche(x: dict, f: dict) -> str:
    l_ = libelle_affiche(x)
    return l_ if lisible(l_) else f["titre"]


def breve(t: dict, fiche: dict, chemin: str) -> dict:
    """La brève de la lettre : le titre du modèle (celui de la fiche à défaut), son descriptif, comme un autre fait
    saillant (mainteneur, 04.11.2026), l'adresse et le chemin de la fiche."""
    return {
        "titre": t.get("titre") or fiche["titre"],
        "descriptif": t.get("descriptif") or "",
        "url": fiche["url"],
        "chemin": chemin,
    }


def redigee(textes: dict, titres: dict, a_rediger: bool) -> bool:
    """Vrai si le modèle a écrit les textes de la lettre, ou s'il n'y avait rien à écrire ; faux si la limite d'usage ou
    le plafond l'a fait sauter (mainteneur, 04.11.2026)."""
    return bool(textes or titres) or not a_rediger


def trier_groupes(d: dict, ordre: list[str]) -> dict:
    """Les groupes dans l'ordre des rubriques, les autres dans leur ordre d'arrivée, « Autres » toujours en dernier."""
    return {
        g: d[g]
        for g in sorted(
            d, key=lambda g: (g == "Autres", ordre.index(g) if g in ordre else len(ordre), list(d).index(g))
        )
    }


def construire(
    rapport: str,
    passage: dict,
    fiches: dict[str, dict],
    banc: dict | None = None,
    nom: str = "",
    graphe: str = "",
    rediger_=rediger,
    faits_forces: list[dict] | None = None,
    jour: str | None = None,
    envoi: str | None = None,
    rubriques: list[dict] | None = None,
    aussi_max: int = AUSSI_MAX,
    facteur: float | None = None,
    fraicheur: int | None = None,
    minimum_rubrique: int | None = None,
    une_min: int = UNE_MIN,
    angle_=None,
    nouvelles: dict | None = None,
) -> dict:
    """Le contenu de la lettre, prêt à mettre en page (html, texte, objet). `rubriques` : celles du graphe au registre
    par défaut ; [] pour un graphe qui n'en a pas (les blocs se rangent alors par thème, comme avant B16). `nouvelles` :
    le registre des nouvelles du graphe (nouvelles.lire)."""
    from newskit import notifier_courriel as nc  # noqa: PLC0415

    if rubriques is None:
        from newskit import config_sources as cfg  # noqa: PLC0415

        rubriques = cfg.rubriques(graphe) if graphe else []
    r = nc.lire_rapport(rapport)
    jour = jour or (r.get("date") or passage.get("date") or time.strftime("%Y-%m-%d"))[:10]
    notions = [n for n in passage.get("nouveaux") or [] if n.get("neuve")]
    classees, ecartees = classer(notions, fiches)
    faits = une_par_nouvelle(faits_forces if faits_forces is not None else grouper(classees, fiches), nouvelles or {})
    if fraicheur is None:
        from newskit import config_sources as cfg  # noqa: PLC0415

        fraicheur = cfg.fraicheur_lettre(graphe) if graphe else cfg.FRAICHEUR_LETTRE
    if minimum_rubrique is None:
        from newskit import config_sources as cfg  # noqa: PLC0415

        minimum_rubrique = cfg.fraicheur_min_rubrique(graphe) if graphe else cfg.FRAICHEUR_MIN_RUBRIQUE
    actualite = actualite_des_rubriques(fiches, rubriques, jour, fraicheur, minimum_rubrique) if rubriques else []
    if faits_forces is None:
        marquer_recents(faits, fiches, jour, fraicheur)
    du_graphe, connues = {x["nom"] for x in rubriques}, {}

    def nouvelle(f: dict) -> dict:
        """La nouvelle du fait au registre : celle de sa source principale, sinon celle d'une autre de ses sources."""
        cle = tuple(f["fiches"])
        if cle not in connues:
            ks = [principale(f, fiches), *f["fiches"]] if all(k in fiches for k in f["fiches"]) else f["fiches"]
            connues[cle] = next((b for b in (nv.nouvelle_de(nouvelles or {}, k) for k in ks) if b), None) or {}
        return connues[cle]

    def repris(f: dict) -> dict | None:
        """Le billet du registre que la lettre reprend entier, titre, texte et source : la relecture du fil l'a tenu
        (fil.relire, les règles de la lettre). Sinon la lettre écrit le titre et le texte : jamais de deux mains."""
        b = nouvelle(f)
        complet = b.get("titre") and b.get("descriptif") and (b.get("source") or {}).get("url")
        return b if b.get("tenu") is True and complet else None

    def court(b: dict) -> str:
        """Le texte d'un billet repris dans une rubrique ou en bref : ses premières phrases, puis la définition du
        terme que sa source n'explique pas."""
        return nv.texte({**b, "descriptif": " ".join(phrases(b["descriptif"])[:CHAPEAU_PHRASES])})

    def groupe(f: dict) -> str:
        if rubriques:  # la rubrique de la nouvelle, écrite une fois par le fil ; sinon la règle de la lettre
            if nouvelle(f).get("rubrique") in du_graphe:
                return nouvelle(f)["rubrique"]
            return rubrique_majoritaire([fiches[k] for k in f["fiches"] if k in fiches], rubriques) or "Autres"
        return f["notions"][0].get("theme") or "Autres"

    retenus, autres = selectionner(faits)
    med = mediane(faits) * ((FACTEUR_LETTRE if facteur is None else facteur) / FACTEUR_LETTRE)

    def entre(f: dict) -> bool:
        return (
            all(x in fiches for x in f["fiches"])
            and admis(f, fiches, groupe(f), med, bool(rubriques))
            and not regle_deja_en_vigueur(fiches[principale(f, fiches)], jour, fraicheur)
            and not promotionnel(fiches[principale(f, fiches)])
        )

    def registre(f: dict) -> bool:
        return all(non_actualite(fiches.get(k) or k) for k in f["fiches"])

    def changement(f: dict) -> bool:
        return any(changement_du_registre(fiches[k]) for k in f["fiches"] if k in fiches)

    def incident(f: dict) -> bool:
        return all(k in fiches and fiche_d_incident(fiches[k]) for k in f["fiches"])

    incs = [f for f in [*retenus, *autres] if incident(f) and not registre(f) and entre(f)]
    regs = [f for f in [*retenus, *autres] if registre(f)]
    retenus = [f for f in retenus if not registre(f) and not incident(f)]  # une page d'incident : jamais à la une
    retenus = [f for f in retenus if entre(f)]
    autres = [f for f in autres if entre(f) and not registre(f) and not incident(f)]
    # Les nouvelles nées du même événement de fond se rangent sous la plus forte (mainteneur, 05.11.2026 : « oui pour le
    # regroupement ») : la première du groupe dans l'ordre de la lettre garde sa place, les autres deviennent ses
    # lignes liées, et leur place revient aux faits suivants. Seuls les billets repris du registre : un titre lié est
    # celui du fil, et la tête ne peut pas sortir sur un refus du rédacteur.
    tetes, liees, rangees = {}, {}, set()
    for f in [*retenus, *autres]:
        b = repris(f)
        if not (b and b.get("contexte")):
            continue
        if b["contexte"] not in tetes:
            tetes[b["contexte"]] = f
            continue
        liees.setdefault(id(tetes[b["contexte"]]), []).append(
            {"titre": b["titre"], "source": b["source"], "chemin": b["chemin"]}
        )
        rangees.add(id(f))
    retenus, autres = [f for f in retenus if id(f) not in rangees], [f for f in autres if id(f) not in rangees]

    def avec_liees(x: dict, f: dict) -> dict:
        return {**x, "liees": liees[id(f)]} if id(f) in liees else x

    # Un billet repris dont un terme reste sans définition (nv.a_completer) : une brève au mieux (mainteneur, 05.11.2026,
    # sur « leadership prophétique » à la une : « il mérite des compléments »). Sa place revient au fait suivant.
    def incomplet(f: dict) -> bool:
        return bool(repris(f)) and nv.a_completer(repris(f))

    incomplets = [f for f in [*retenus, *autres] if incomplet(f)]
    retenus, autres = [f for f in retenus if not incomplet(f)], [f for f in autres if not incomplet(f)]
    retenus, autres = completer_une(retenus, autres, une_min, groupe if rubriques else None)
    dates_ = [f for f in incs if evenement_date(fiches[principale(f, fiches)], jour, fraicheur)]
    autres = sorted([*autres, *dates_], key=lambda f: -f["score"])
    regs = [*regs, *(f for f in incs if f not in dates_)]  # non daté : une brève au mieux
    regs = sorted(
        (f for f in regs if (changement(f) or incident(f)) and all(x in fiches for x in f["fiches"]) and entre(f)),
        key=lambda f: -f["score"],
    )
    aussi_f = autres[:aussi_max]
    bref_f = [*autres[aussi_max:], *incomplets, *regs][: BREF_MAX * 2]

    # Les brèves : au plus BREF_PAR_THEME par groupe et BREF_MAX en tout, choisies avant l'appel au modèle.
    brefs, par_g, reste = [], Counter(), 0
    for f in bref_f:
        g = groupe(f)
        if par_g[g] >= BREF_PAR_THEME or len(brefs) >= BREF_MAX:
            reste += 1
            continue
        brefs.append((g, f))
        par_g[g] += 1
    reste += max(0, len(autres) - aussi_max - len(bref_f))
    for f in [*retenus, *aussi_f, *(f for _, f in brefs)]:  # la suite d'une nouvelle parue dit son fait neuf d'abord
        f["developpement"] = nouvelle(f).get("developpement")
    # La lettre n'écrit que ce que le registre n'a pas : les billets du fil sont déjà écrits et relus (mainteneur, 05.11.2026).
    e_une = [f for f in retenus if not repris(f)]
    e_autres = [f for f in [*aussi_f, *(f for _, f in brefs)] if not repris(f)]
    e_brefs = len([f for _, f in brefs if not repris(f)])
    a_ecrire = bool(e_une or e_autres)
    textes, titres, cout = (
        rediger_(e_une, fiches, graphe, e_autres[: len(e_autres) - e_brefs], e_autres[len(e_autres) - e_brefs :])
        if a_ecrire
        else ({}, {}, 0.0)
    )
    ecrit = {id(f): textes.get(i) or {} for i, f in enumerate(e_une, 1)}
    ecrit.update({id(f): titres.get(j) or {} for j, f in enumerate(e_autres, 1)})
    L_faits = []
    for f in retenus:
        b = repris(f)
        if b:
            L_faits.append(
                avec_liees(
                    {
                        "titre": b["titre"],
                        "paragraphe": nv.texte(b),
                        "gras": [],
                        "source": b["source"],
                        "chemin": b["chemin"],
                        "autres": len(b.get("sources_plus") or []),
                        "rubrique": groupe(f) if rubriques else "",
                    },
                    f,
                )
            )
            continue
        k = principale(f, fiches) if all(x in fiches for x in f["fiches"]) else None
        t = ecrit[id(f)]
        if t.get("retire"):
            continue  # aucune phrase prouvée : jamais de titre seul
        src = _source(fiches[k]) if k else source_de_repli(f)
        L_faits.append(
            {
                "titre": t.get("titre") or src["titre"],
                "paragraphe": t.get("paragraphe") or "",
                "gras": t.get("gras") or [],
                "source": src,
                "chemin": k or "",
                "autres": f.get("sources", len(f["fiches"])) - 1,
                "rubrique": groupe(f) if rubriques else "",
            }
        )
    aussi = []
    for f in aussi_f:
        b = repris(f)
        if b:
            aussi.append(
                avec_liees(
                    {
                        "titre": b["titre"],
                        "descriptif": court(b),
                        "groupe": groupe(f),
                        "source": b["source"],
                        "chemin": b["chemin"],
                    },
                    f,
                )
            )
            continue
        k = principale(f, fiches)
        t = ecrit[id(f)]
        if t.get("retire"):
            continue
        aussi.append(
            {
                "titre": t.get("titre") or fiches[k]["titre"],
                "descriptif": t.get("descriptif") or "",
                "groupe": groupe(f),
                "source": _source(fiches[k]),
                "chemin": k,
            }
        )
    bref: dict[str, list[dict]] = {}
    for g, f in brefs:
        b = repris(f)
        if b:
            bref.setdefault(g, []).append(
                avec_liees(
                    {"titre": b["titre"], "descriptif": court(b), "url": b["source"]["url"], "chemin": b["chemin"]}, f
                )
            )
            continue
        k = principale(f, fiches)
        t = ecrit[id(f)]
        if t.get("retire"):
            continue
        bref.setdefault(g, []).append(breve(t, fiches[k], k))
    ordre = [x["nom"] for x in rubriques]

    def trier(d):
        return trier_groupes(d, ordre)

    groupes_aussi: dict[str, list[dict]] = {}
    for x in aussi:
        groupes_aussi.setdefault(x["groupe"], []).append(x)
    deroule = r.get("deroule") or ""
    m_c = re.search(r"coût du passage : (.+)", deroule)
    from newskit import config_sources as cfg_  # noqa: PLC0415

    cfg_graphe = cfg_.graphe_declare(graphe) if graphe else {}
    L = {
        "nom": nom or graphe,
        "graphe": graphe,
        "jour": jour,
        "lundi": _lundi(jour),
        "heure": r.get("date", ""),
        "envoi": (envoi or time.strftime("%Y-%m-%d"))[:10],
        **_edition(),
        "faits": L_faits,
        "aussi": trier(groupes_aussi),
        "bref": trier(bref),
        "bref_reste": reste,
        "ordre": ordre,
        # Rédigée : le modèle a écrit ses textes. Une lettre non rédigée (limite d'usage, plafond) ne part pas
        # (mainteneur, 04.11.2026 : la lettre d'un graphe n'avait qu'un billet non développé).
        "redige": redigee(textes, titres, a_ecrire),
        "lettre_nom": cfg_graphe.get("lettre_nom") or "",
        "lettre_titre": cfg_graphe.get("lettre_titre") or "",
        "lettre_slogan": cfg_graphe.get("lettre_slogan") or "",
        "etat": r.get("etat") or "",
        "fiches": r.get("fiches") or "",
        "neuf": r.get("neuf") or "",
        "noeuds": r.get("noeuds") or "",
        "aretes": r.get("aretes") or "",
        "ecart_noeuds": r.get("ecart_noeuds") or "",
        "ecart_aretes": r.get("ecart_aretes") or "",
        "notions_neuves": len(notions),
        "identifiants": passage.get("identifiants_neufs"),
        "ecartees": ecartees,
        "cout_passage": m_c.group(1).strip() if m_c else "",
        "cout_lettre": cout,
        "banc": banc,
        "sante": [
            lisible_sante(sans_fiche(re.sub(r"^(?:!!|ALERTE\s*:?)\s*", "", a)))
            for a in [*r.get("echecs", []), *r.get("alertes", []), *r.get("assistes", [])]
        ]
        + alertes_d_actualite(actualite, fraicheur, minimum_rubrique),
        "actualite": actualite,
        "actualite_jours": fraicheur,
    }
    if angle_ is not None:  # l'angle critique de chaque billet (newskit.angle_critique)
        angle_(L, fiches, graphe)
    return L


def _actualite(L: dict) -> str:
    """Les fiches fraîches de la semaine par rubrique, pour les Coulisses."""
    return (
        " · ".join(f"{r['nom']} {r['n']}" for r in L["actualite"])
        + f" (publiées depuis {L['actualite_jours']} jours au plus)"
    )


def _edition() -> dict:
    """La marque, le dépôt et le site public de l'édition, lus au registre (bloc `edition:`, clé `lettre_site`)."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    e = cfg.edition()
    return {
        "marque": str(e.get("nom") or "").capitalize(),
        "depot": str(e.get("depot") or "").split("/")[-1],
        "site": str(e.get("lettre_site") or "").rstrip("/"),
    }


def _court(url: str) -> str:
    return re.sub(r"^https?://", "", url)


def rubriques_de(L: dict) -> list[tuple[str, list[dict], list[dict]]]:
    """(rubrique, billets, brèves) dans l'ordre des rubriques : les brèves en fin de leur rubrique, une rubrique de
    brèves seules comprise (mainteneur, 03.11.2026). Une lettre gardée sans « ordre » suit l'ordre de ses sections. Un graphe
    sans rubriques au registre (« ordre » vide) : un seul groupe, « Aussi aujourd'hui » ; ses thèmes ne sont pas des
    rubriques (mainteneur, 04.11.2026 : « rubriques fausses » dans la lettre d'un graphe sans rubriques)."""
    if "ordre" in L and not L["ordre"]:
        billets = [x for v in L["aussi"].values() for x in v]
        breves = [x for v in L["bref"].values() for x in v]
        nom = _l("Aussi cette semaine" if L.get("periode") == "semaine" else "Aussi aujourd'hui")
        return [(nom, billets, breves)] if billets or breves else []
    tous = {g: None for g in [*L["aussi"], *L["bref"]]}
    return [(g, L["aussi"].get(g, []), L["bref"].get(g, [])) for g in trier_groupes(tous, list(L.get("ordre") or []))]


def index(L: dict) -> list[dict]:
    """Les éléments de la lettre dans l'ordre de lecture, numérotés de 1 à n : les faits à la une, puis chaque autre
    fait saillant, puis chaque brève. Le numéro du rendu WIP ; l'index écrit à côté de la lettre (mainteneur, 03.11.2026)."""
    out = [
        {
            "genre": "une",
            "chemin": f.get("chemin", ""),
            "url": f["source"]["url"],
            "titre": f["titre"],
            "rubrique": f.get("rubrique", ""),
        }
        for f in L["faits"]
    ]
    for g, billets, breves in rubriques_de(L):
        out += [
            {
                "genre": "autre",
                "chemin": x.get("chemin", ""),
                "url": x["source"]["url"],
                "titre": x["titre"],
                "rubrique": g,
            }
            for x in billets
        ]
        out += [
            {"genre": "breve", "chemin": x.get("chemin", ""), "url": x["url"], "titre": x["titre"], "rubrique": g}
            for x in breves
        ]
    return [{"ref": i, **e} for i, e in enumerate(out, 1)]


ECLAIRAGES = RACINE / "eclairages"  # les copies publiques des éclairages publiés (reporter.exporter_publie)


def eclairages_publies(graphe: str) -> dict[str, str]:
    """Le sujet de chaque éclairage publié du graphe (fiche et adresse canonique) → l'adresse fixe de sa page."""
    out = {}
    for f in sorted((ECLAIRAGES / graphe).glob("*/*/enquete.json")) if (ECLAIRAGES / graphe).exists() else []:
        try:
            E = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if E.get("etat") != "publié" or not E.get("adresse"):
            continue
        s = E.get("sujet") or {}
        for k in (s.get("chemin"), canonique(s["url"]) if s.get("url") else ""):
            if k:
                out[k] = "https://" + E["adresse"]
    return out


def page_de_la_lettre(L: dict) -> str:
    """L'adresse de la page web de la lettre : <site>/lettres/<graphe>/<envoi> ; vide sans site public."""
    return f"{L['site']}/lettres/{L['graphe']}/{L['envoi']}" if L.get("site") else ""


def liens_de_l_edition(L: dict) -> dict[int, str]:
    """L'éclairage publié de chaque élément de la lettre qui en a un : son titre y mène. La source mène toujours à la
    source (mainteneur, 04.11.2026 : « clic sur lien source ouvre la lettre en ligne, mais devrait plutôt ouvrir la
    source » ; avant, la source menait à l'éclairage ou à l'ancre de la page de la lettre)."""
    ecl = eclairages_publies(L["graphe"])
    out = {}
    for e in index(L):
        lien = ecl.get(e.get("chemin") or "") or (ecl.get(canonique(e["url"])) if e.get("url") else None)
        if lien:
            out[e["ref"]] = lien
    return out


def titre_relie(html_titre: str, url: str) -> str:
    """Le titre d'un billet, lien vers son éclairage quand il en a un ; sinon le titre seul."""
    if not url:
        return html_titre
    return f'<a href="{_e(url)}" style="color:inherit; text-decoration:none;">{html_titre}</a>'


def _ref(n: int) -> str:
    """Le numéro de référence du rendu WIP, dans la marge gauche."""
    return (
        f'<span data-ref="{n}" style="position:absolute; left:-44px; top:2px; width:34px; text-align:right; '
        f'font-family:Menlo,Consolas,monospace; font-size:11px; line-height:16px; color:#9aa0a6;">{n}</span>'
    )


# Corrections de forme au rendu, sur le texte déjà rédigé : aucune rédaction ni appel au modèle (mainteneur, 03.11.2026,
# « on ne peut pas attendre 10-15 minutes pour chaque version »).
NOMS_LONGS = {
    "BRI": ("la ", "Banque des règlements internationaux"),
    "BCE": ("la ", "Banque centrale européenne"),
    "OCDE.AI": ("l'", None),
}
ARCHIVES = ("arXiv", "SSRN", "OpenReview", "medRxiv", "bioRxiv")
_INS = "\u00a0"


def nombres_fr(s: str) -> str:
    """« €380,000 » → « 380 000 € », « 96.2% » → « 96,2 % », « 2.3 » → « 2,3 » ; les versions (GPT-5.4) restent."""
    s = re.sub(r"(?<![\w.,-])(\d{1,3})((?:,\d{3})+)(?![\d])", lambda m: m.group(1) + m.group(2).replace(",", _INS), s)
    s = re.sub(r"(?<![\w.-])(\d+)\.(\d+)(?![\w.])", r"\1,\2", s)
    s = re.sub(r"([€$£])\s?(\d[\d\u00a0,]*)", lambda m: m.group(2) + _INS + m.group(1), s)
    return re.sub(r"(\d)\s?%", r"\1" + _INS + "%", s)


def attribution(s: str, vus: set) -> str:
    """« Selon arXiv, X et Y présentent » → « Les chercheurs X et Y présentent » ; « Selon BRI » → « Selon la Banque
    des règlements internationaux (BRI) » à la première mention."""
    for a in ARCHIVES:
        m = re.match(rf"\s*Selon {re.escape(a)},\s*(.*)", s, flags=re.S)
        if m:
            reste = m.group(1)
            if re.match(r"(?:Des|Les|Le|La|Un|Une|L')\b", reste) is None:
                pluriel = re.match(r"[^,.]*?\bet\b[^,.]*?\b\w+ent\b", reste) is not None
                reste = ("Les chercheurs " if pluriel else "Le chercheur ") + reste
            s = reste[0].upper() + reste[1:]

    def long(m):
        sigle = m.group(2)
        art, nom = NOMS_LONGS[sigle]
        if nom is None:
            return f"{m.group(1)} {art}{sigle.split('.')[0]}"
        if sigle in vus:
            return f"{m.group(1)} {art}{sigle}" if art != "l'" else f"{m.group(1)} l'{sigle}"
        vus.add(sigle)
        return f"{m.group(1)} {art}{nom} ({sigle.split('.')[0]})"

    return re.sub(r"\b([Ss]elon) (" + "|".join(re.escape(k) for k in NOMS_LONGS) + r")\b", long, s)


def sans_coupure(s: str) -> str:
    """« GPT-5.4 » ne se coupe plus après le tiret : trait d'union insécable (U+2011) dans un mot qui porte un chiffre."""
    return re.sub(
        r"(?<=\w)-(?=\w)",
        lambda m: m.group(0),
        re.sub(
            r"\b[\w.]*\d[\w.]*(?:-[\w.]+)+|\b[\w.]+(?:-[\w.]*\d[\w.]*)+", lambda m: m.group(0).replace("-", "\u2011"), s
        ),
    )


def polir(L: dict, html_: bool = False) -> dict:
    """Une copie de la lettre, ses textes corrigés pour la forme."""
    import copy

    L = copy.deepcopy(L)
    if L.get("langue") == "en":  # les corrections de forme du français ne s'appliquent pas à l'anglais
        for x in [
            *(L.get("faits") or []),
            *(x for g in ("aussi", "bref") for v in (L.get(g) or {}).values() for x in v),
        ]:
            for k in ("titre", "paragraphe", "descriptif"):
                if html_ and isinstance(x.get(k), str):
                    x[k] = sans_coupure(x[k])
        return L
    vus: set = set()

    def un(x):
        # « Selon <éditeur de la source> » ne répète pas la ligne de source (mainteneur, 03.11.2026).
        ed = ((x.get("source") or {}).get("editeur") or "").strip()
        if not ed and x.get("chemin") and x.get("url"):
            ed = _source_de_breve(x).get("editeur") or ""
        if not ed and "oecd.ai" in (x.get("url") or ""):
            ed = "OCDE.AI"
        if ed:
            # L'éditeur sous toutes ses formes : « Help Net Security » pour helpnetsecurity.com, « Lawfare » pour
            # lawfaremedia.org : comparé sans espace ni ponctuation, à n'importe quel début de phrase.
            def serre(s_: str) -> str:
                return re.sub(r"[^a-z0-9]", "", plier(s_))

            cle = serre(re.sub(r"\.(com|org|net|ai|eu|io|co\.uk|de|fr|ch)$", "", ed.removeprefix("www.")))

            def meme(nom: str) -> bool:
                n_ = serre(re.sub(r"^(l'|le |la |les )", "", nom.strip()))
                return bool(n_) and (
                    n_ == cle or (len(n_) >= 4 and (n_ in cle or cle in n_)) or (ed.startswith("OCDE") and n_ == "ocde")
                )

            for k in ("paragraphe", "descriptif"):
                if isinstance(x.get(k), str):
                    x[k] = re.sub(
                        r"(^|(?<=[.!?] ))Selon ([^,]{2,50}), ",
                        lambda m: m.group(0) if not meme(m.group(2)) else m.group(1),
                        x[k],
                    )
                    x[k] = re.sub(
                        r"(^|(?<=[.!?] ))([A-Z][\w .'-]{1,40}) écrit que ",
                        lambda m: m.group(0) if not meme(m.group(2)) else m.group(1),
                        x[k],
                    )
                    x[k] = re.sub(
                        r"\s+rapportée? par ([A-Z][\w .'-]{1,40})(?=[ ,.])",
                        lambda m: m.group(0) if not meme(m.group(1)) else "",
                        x[k],
                    )
                    x[k] = re.sub(r"(^|(?<=[.!?] ))([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), x[k])
        # L'éclairage fondu dans le billet, en dernière phrase (mainteneur, 03.11.2026).
        lec = sans_libelle(x.get("lecture") or "").strip()
        if lec:
            k = "paragraphe" if "paragraphe" in x else "descriptif"
            corps = (x.get(k) or "").strip()
            lec = lec[0].upper() + lec[1:]
            x[k] = (corps + ("" if not corps or corps.endswith((".", "!", "?")) else ".") + " " + lec).strip()
            x["lecture"] = ""
        for k in ("titre", "paragraphe", "descriptif", "lecture"):
            if isinstance(x.get(k), str):
                x[k] = nombres_fr(attribution(x[k], vus))
                if html_:
                    x[k] = sans_coupure(x[k])

    for f in L.get("faits") or []:
        un(f)
    for groupe in ("aussi", "bref"):
        for v in (L.get(groupe) or {}).values():
            for x in v:
                un(x)
    return L


def objet(L: dict) -> str:
    """« The Governance daily — mardi 3 nov. 2026 », « The Governance weekly — du 25 oct. au 1er nov. 2026 » ; un graphe
    sans nom de lettre au registre : « Accès aux sites web — Daily du mercredi 4 nov. 2026 »."""
    with _langue(L):
        nom, sorte, quand = entete(L)
    if sorte and banniere(L):
        return f"{nom} {sorte} — {quand}"
    if L.get("periode") == "jour":
        return f"{L['nom']} — Daily du {date_fr(L['envoi'])}"
    if L.get("periode") == "semaine":
        return f"{L['nom']} — Weekly, {date_entete(L)}"
    return f"{L['nom']} — la semaine du {date_fr(L['lundi'], annee=False)}"


def accorder(t: str) -> str:
    """« 2 point(s) » → « 2 points », « 1 point(s) » → « 1 point », « 12 sources dense(s) » → « 12 sources denses » :
    l'accord suit le dernier nombre de la proposition (avant une virgule, un point-virgule, un deux-points) ; sans
    nombre, le pluriel."""

    def f(m):
        avant = re.split(r"[,;:.—(]", t[: m.start()])[-1]
        ns = re.findall(r"\d[\d\u00a0\u202f ]*\d|\d", avant)
        n = int(re.sub(r"\D", "", ns[-1])) if ns else 2
        return m.group(1) + ("s" if n > 1 else "")

    return re.sub(r"(\w+)\(s\)", f, t)


_LISTE_MAX = 3


def _nom_d_url(url: str) -> str:
    """Le nom court d'une source du registre d'après son adresse (« ICO »), sinon son domaine."""
    host = urlparse(url).netloc.removeprefix("www.")
    try:
        from newskit import config_sources as cfg  # noqa: PLC0415

        for x in cfg.charger().get("sources") or []:
            champs = {
                **(getattr(x, "propres", None) or {}),
                **{c: getattr(x, c, "") for c in ("plan", "site", "url", "flux")},
            }
            if any(
                urlparse(str(v or "")).netloc.removeprefix("www.") == host
                for v in champs.values()
                if isinstance(v, str)
            ):
                return re.split(r"\s+[—–]\s+|\s*\(", str(getattr(x, "nom", "") or host))[0].strip() or host
    except Exception:  # noqa: BLE001 — le registre illisible : le domaine
        pass
    return host


def lisible_sante(t: str) -> str:
    """Une ligne « à suivre » de la routine, lisible dans les Coulisses : pas d'URL ni d'erreur HTTP brute (« Source
    muette : ICO (refus 403) »), pas de commande, les accords faits, une liste longue résumée par « et N autres »,
    jamais coupée en plein mot."""
    t = str(t).strip()
    m = re.match(r"(https?://\S+) : HTTP Error (\d{3})\b", t)
    if m:
        code = int(m.group(2))
        quoi = (
            f"refus {code}"
            if code in (401, 403, 429)
            else ("page introuvable" if code in (404, 410) else f"erreur {code}")
        )
        return f"Source muette : {_nom_d_url(m.group(1))} ({quoi})"
    m = re.match(r"(https?://\S+) : .*(?:urlopen error|timed out|Connection)", t)
    if m:
        return f"Source muette : {_nom_d_url(m.group(1))} (injoignable)"
    t = re.sub(r"\.?\s*Liste : python3 .*$", ".", t)
    t = re.sub(r"https?://\S+", lambda u: _nom_d_url(u.group(0)), t)
    t = accorder(t)

    def liste(mm):
        items = [x.strip().rstrip("…").strip() for x in mm.group(2).split(", ") if x.strip().rstrip("…").strip()]
        coupee = mm.group(2).rstrip().endswith("…")
        if len(items) <= _LISTE_MAX and not coupee:
            return mm.group(0)
        tot = re.findall(r"(\d[\d\u00a0\u202f ]*\d|\d) \w+", mm.group(1))
        total = int(re.sub(r"\D", "", tot[-1])) if tot and coupee else len(items)
        reste = max(0, total - _LISTE_MAX)
        return f"{mm.group(1)}({', '.join(items[:_LISTE_MAX])}" + (f" et {nombre(reste)} autres)" if reste else ")")

    t = re.sub(r"([^()]*)\(([^()]*,[^()]*)\)", liste, t, count=1)
    return t


def _verdict(L: dict) -> str:
    ok = L["etat"].startswith("ok")
    n = int(L["neuf"].lstrip("+") or 0) if L["neuf"].lstrip("+").isdigit() else 0
    return (
        ("Passage réussi." if ok else f"Passage : {L['etat']}.")
        + f" {nombre(n)} sources ajoutées, {nombre(L['notions_neuves'])} notions neuves"
        + (f", {len(L['sante'])} point{'s' if len(L['sante']) > 1 else ''} à suivre." if L["sante"] else ".")
    )


def _banc(L: dict) -> str:
    b = (L.get("banc") or {}).get("conditions", {}).get("graphe")
    if not b:
        return ""
    return (
        f"{b.get('n', '?')} questions récentes sur {L['banc'].get('questions', '?')} : "
        f"{round(100 * b['juste'])} % justes, {round(100 * b['faux'])} % fausses, "
        f"{round(100 * b['ne_sait_pas'])} % sans réponse"
    )


def _cout(L: dict) -> str:
    c = accorder(L["cout_passage"].replace(".", ",")) if L["cout_passage"] else "non consigné"
    return c + (f" ; la lettre : {nombre(round(L['cout_lettre'], 2))} $" if L["cout_lettre"] else "")


LIEES = "Sur le même sujet"  # au-dessus des nouvelles nées du même événement, rangées sous un élément


def liees_html(x: dict) -> str:
    """Les nouvelles rangées sous un élément : le titre de chacune, en lien vers sa source, puis son éditeur."""
    if not x.get("liees"):
        return ""
    lignes = "".join(
        f'<div style="margin-top:4px; font-size:14px; line-height:1.45;">{_lien(y["source"], y["titre"], "src", TEXTE)} '
        f'<span style="font-size:13px; color:{DOUX};">{_e(editeur_affiche(y["source"].get("editeur") or ""))}</span></div>'
        for y in x["liees"]
    )
    return f'<div style="margin-top:12px;">{_etiquette(_l(LIEES), "0", 11)}{lignes}</div>'


def liees_texte(x: dict, retrait: str) -> list[str]:
    if not x.get("liees"):
        return []
    return [
        f"{retrait}{_l(LIEES)}{':' if en_anglais() else ' :'}",
        *(
            f"{retrait}  - {y['titre']} ({y['source'].get('editeur') or ''})"
            + (f" — {y['source']['url']}" if y["source"].get("url") else "")
            for y in x["liees"]
        ),
    ]


def editeur_affiche(ed: str) -> str:
    """L'éditeur d'une source tel que la lettre le montre : son nom anglais dans la lettre anglaise quand le registre le
    donne en français (« Commission européenne » → « European Commission », d'après NOMS_FRANCAIS)."""
    if not en_anglais() or not ed:
        return ed
    v = NOMS_FRANCAIS.get(plier(ed).strip())
    return (v[0] if v[0].isupper() else v[0].title()) if v else ed


def _ligne_source(s: dict, autres: int) -> str:
    t = f"{editeur_affiche(s['editeur'])}" + (f", {date_fr(s['date'])}" if s.get("date") else "")
    if en_anglais():
        return t + (f" — and {autres} other source{'s' if autres > 1 else ''}" if autres else "")
    return t + (f" — et {autres} autre{'s' if autres > 1 else ''} source{'s' if autres > 1 else ''}" if autres else "")


# --- Mise en page : texte -------------------------------------------------------------------------------------------


def _titre_txt(t: str) -> list[str]:
    return [t.upper(), "-" * len(t)]


DESINSCRIPTION = "Désinscription"


def _desinscription(L: dict) -> str:
    """L'adresse de désinscription : L["desinscription"] (adresse web ou courriel), sinon un courriel à l'expéditeur."""
    d = L.get("desinscription") or ""
    if not d and L.get("expediteur"):
        from email.utils import parseaddr  # noqa: PLC0415

        d = parseaddr(L["expediteur"])[1]
    if d and "@" in d and not d.startswith(("http", "mailto:")):
        d = f"mailto:{d}?subject={'Unsubscribe' if en_anglais() else DESINSCRIPTION}"
    return d


def pied_html(L: dict) -> str:
    d = _desinscription(L)
    # Le site de la lettre vient du registre (`lettre_site`) : aucun nom d'édition dans le code (mainteneur, 05.11.2026).
    site, nom = _court(L.get("site") or ""), L.get("nom") or ""
    if en_anglais():
        t = _e("You receive this letter because you subscribed to it." + (f" A letter from {site}." if site else ""))
        return t + (
            f'<br><a href="{_e(d)}" style="color:{LIEN}; text-decoration:underline;">Unsubscribe</a>' if d else ""
        )
    signe = f" {nom}, un bulletin de {site}." if nom and site else f" Un bulletin de {site}." if site else ""
    t = _e("Vous recevez ce bulletin parce que vous y êtes inscrit." + signe)
    if d:
        t += f'<br><a href="{_e(d)}" style="color:{LIEN}; text-decoration:underline;">Se désinscrire</a>'
    return t


def pied_texte(L: dict) -> str:
    d = _desinscription(L)
    if en_anglais():
        if d.startswith("mailto:"):
            d = f"write to {d[7:].split('?')[0]} with the subject “Unsubscribe”"
        return "You receive this letter because you subscribed to it." + (f" Unsubscribe: {d}" if d else "")
    if d.startswith("mailto:"):
        d = f"écrire à {d[7:].split('?')[0]} avec l'objet « {DESINSCRIPTION} »"
    return (
        ("Vous recevez ce bulletin parce que vous y êtes inscrit. Se désinscrire : " + d + ".")
        if d
        else "Vous recevez ce bulletin parce que vous y êtes inscrit."
    )


def _meme_source(x: dict) -> str:
    """Les éléments fusionnés avec une proposition du Sélectionneur : même source primaire (mainteneur, 04.11.2026)."""
    return f" ; même source que n° {', '.join(str(r) for r in x['aussi'])}" if x.get("aussi") else ""


def texte(L: dict, wip: bool = False, numeros: bool = False, coulisses: bool = False) -> str:
    with _langue(L):
        t = _texte(L, wip, numeros, coulisses)
    return ponctuation(guillemets(t)) if L.get("langue") == "en" else ponctuation(guillemets(sans_fiche(t)))


def _texte(L: dict, wip: bool = False, numeros: bool = False, coulisses: bool = False) -> str:
    L = polir(L)
    liens = liens_de_l_edition(L)
    refs = iter(range(len(L["faits"]) + 1, 10**6))
    nom, sorte, quand = entete(L)
    if banniere(L):
        o = [f"{nom} {sorte}".strip(), f"• {quand.upper()} •", ""]
    else:
        o = [entete_simple(L), ""]
    if L["faits"]:
        o += [
            "  " + _l("À la une").upper(),
            "  " + "-" * len(_l("À la une")),
        ]  # comme un titre de rubrique (mainteneur, 03.11.2026)
    for i, f in enumerate(L["faits"], 1):
        o.append(
            f"  {i}. {f['rubrique'].upper() + ' · ' if f.get('rubrique') else ''}"
            f"{f['lieu'] + ' : ' if f.get('lieu') else ''}{titre_sans_lieu(f)}"
        )
        if f["paragraphe"]:
            o.append(f"     {f['paragraphe']}")
        if f.get("lecture"):
            o.append(f"     {FLECHE} {sans_libelle(f['lecture'])}")
        o.append(
            f"     Source{':' if en_anglais() else ' :'} {_ligne_source(f['source'], f.get('autres', 0))}"
            + (f" — {f['source']['url']}" if f["source"].get("url") else "")
            + (f"  [{i}]" if numeros else "")
        )
        if liens.get(i):
            o.append(f"     {_l('Éclairage')}{':' if en_anglais() else ' :'} {liens[i]}")
        o += liees_texte(f, "     ")
        o.append("")
    if L["aussi"] or L["bref"]:
        for g, billets, breves in rubriques_de(L):
            o += [f"  {g.upper()}", "  " + "-" * len(g)]
            for x in billets:
                r = next(refs)
                o.append(f"    - {titre_avec_lieu(x)}")
                if x["descriptif"]:
                    o.append(f"      {x['descriptif']}")
                if x.get("lecture"):
                    o.append(f"      {FLECHE} {sans_libelle(x['lecture'])}")
                lien = liens.get(r, "")
                o.append(
                    f"      {_ligne_source(x['source'], 0)}"
                    + (f" — {x['source']['url']}" if x["source"].get("url") else "")
                    + (f"  [{r}]" if numeros else "")
                )
                if lien:
                    o.append(f"      {_l('Éclairage')}{':' if en_anglais() else ' :'} {lien}")
                o += liees_texte(x, "      ")
            for x in breves:  # la brève : même forme que le billet de rubrique (03.11.2026)
                r = next(refs)
                lien = liens.get(r, "")
                o.append(f"    - {titre_avec_lieu(x)}")
                if x.get("descriptif"):
                    o.append(f"      {x['descriptif']}")
                o.append(
                    f"      {_ligne_source(_source_de_breve(x), 0)}"
                    + (f" — {x['url']}" if x.get("url") else "")
                    + (f"  [{r}]" if numeros else "")
                )
                if lien:
                    o.append(f"      {_l('Éclairage')}{':' if en_anglais() else ' :'} {lien}")
                o += liees_texte(x, "      ")
            o.append("")
        if L["bref_reste"]:
            o += [f"  … and {L['bref_reste']} more." if en_anglais() else f"  … et {L['bref_reste']} autres.", ""]
    if wip or coulisses:
        o += _titre_txt("Coulisses")
        o.append(f"  {_verdict(L)}")
        o += [
            f"  Passage du       {heure_fr(L['heure'])}",
            f"  Sources          {nombre(L['fiches'])} ({L['neuf']} ajoutées)",
            f"  Notions neuves   {nombre(L['notions_neuves'])}"
            + (f" sur {nombre(L['identifiants'])} identifiants neufs" if L.get("identifiants") else ""),
            f"  Nœuds            {nombre(L['noeuds'])}"
            + (f" ({ecart(L['ecart_noeuds'])})" if L["ecart_noeuds"] else ""),
            f"  Arêtes           {nombre(L['aretes'])}"
            + (f" ({ecart(L['ecart_aretes'])})" if L["ecart_aretes"] else ""),
        ]
        if L.get("actualite"):
            o.append(f"  Actualité        {_actualite(L)}")
        if _banc(L):
            o.append(f"  Banc             {_banc(L)}")
        o.append(f"  Coût             {_cout(L)}")
        for s in L["sante"]:
            o.append(f"  - {s}")
        if L.get("selection"):  # le Sélectionneur, en mode proposition (mainteneur, 04.11.2026)
            o += ["", "  Sujets d'enquête proposés"]
            o += [
                f"  - n° {x['ref']} {x['titre']} : {x['hypothese']} ({x['raison']})" + _meme_source(x)
                for x in L["selection"]
            ]
        o += ["", "--", f"Rapport complet : graphe/{L['graphe']}/ROUTINE.md du dépôt {L['depot']}."]
        if L["site"]:
            o.append(f"Mesures et graphes : {L['site']}/#qualite · {L['site']}/#graphes")
        o.append("")
    o += ["", "--", pied_texte(L), ""]
    return "\n".join(o)


# --- Mise en page : HTML ---------------------------------------------------------------------------------------------

POLICE = "font-family:'IBM Plex Sans',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
# L'habit de la page d'accueil (mainteneur, 05.11.2026 : « on part avec A avec les numéros sur la ligne des titres ») : le
# fond de la page, deux cartes, une seule encre ; l'ambre au seul « .news » de la marque.
# Mainteneur, 05.11.2026, sur l'essai 12 : « je n'apprécie pas les couleurs rouille des liens [...] ni le liseré à côté des
# liens. simplifie, plus dans le sens de l'essai 7 » : les liens de source sont gris et soulignés, les titres de « Sur le
# même sujet » sont de l'encre du texte, sans filet ni retrait.
FOND, CARTE, TEXTE, DOUX, FILET = "#d5d9df", "#f7f8fa", "#16233b", "#56637a", "#dde1e7"
SECOND, TRAIT, LIEN = "#3c4a60", "#aab2bf", "#56637a"  # le second texte, le trait des cartes, le gris des liens
POINT = "#b4560f"  # l'ambre du « .news » de la marque
ACCENT_FORT, ACCENT_TEXTE, ACCENT_DOUX = "#2a3d5f", "#16233b", "#e4e8ee"
AMBRE, AMBRE_FOND = "#7a4f00", "#fff7e6"  # « Santé du passage », dans les coulisses

# Clair seulement, comme la page d'accueil. Sans ce bloc la lettre reste lisible au téléphone ; avec, les cartes se
# resserrent et les titres se réduisent.
STYLE = """<style>
  @media (max-width: 480px) {
    .carte { padding-left: 16px !important; padding-right: 16px !important; }
    .titre { font-size: 28px !important; }
    .une { font-size: 18px !important; }
    .tuile { width: 47% !important; margin-right: 2% !important; }
  }
</style>"""


def _e(t) -> str:
    return escape(str(t), quote=True)


def _p(
    t: str, taille=15, couleur=TEXTE, cls="t-texte", marge="0 0 8px 0", gras=False, extra="", interligne="1.5"
) -> str:
    return (
        f'<p class="{cls}" style="{POLICE} margin:{marge}; font-size:{taille}px; line-height:{interligne}; color:{couleur};'
        f'{" font-weight:700;" if gras else ""}{extra}">{t}</p>'
    )


def _lien(s: dict, texte_: str, cls="t-fort", couleur=ACCENT_TEXTE) -> str:
    if not s.get("url"):
        return _e(texte_)
    return (
        f'<a class="{cls}" href="{_e(s["url"])}" style="color:{couleur}; text-decoration:underline;">{_e(texte_)}</a>'
    )


def _etiquette(t: str, marge: str = "0", taille: int = 12) -> str:
    """Une étiquette en petites capitales grises : « À la une », une rubrique, « Sur le même sujet »."""
    return (
        f'<div style="{POLICE} margin:{marge}; font-size:{taille}px; line-height:1.3; font-weight:600; '
        f'letter-spacing:.08em; text-transform:uppercase; color:{DOUX};">{_e(t)}</div>'
    )


def _carte(contenu: str) -> str:
    """Une carte de la page d'accueil, dans son rang : liseré marine en haut, trait gris autour."""
    # Mainteneur, 05.11.2026 : « supprime les liserés supérieurs dans les newsletter » : un trait fin tout autour.
    return (
        f'<tr><td class="carte" style="background:{CARTE}; border:1px solid {TRAIT}; '
        f'border-radius:8px; padding:20px 22px 22px 22px; {POLICE} font-size:15px; line-height:1.45; color:{TEXTE};">'
        f"{contenu}</td></tr>"
    )


ESPACE = '<tr><td style="height:20px; font-size:0; line-height:0;">&nbsp;</td></tr>'  # entre deux cartes


# Le compas en marine (daily) et en automne (weekly), le nord et le compas verts des pages d'éclairage, servis sous
# <site>/lettres/ ; deploiement/lettres.py
# les y copie depuis le dossier des images de l'édition.
LOGOS = [
    *(f"{d}_{c}.png" for d in ("nord", "compas") for c in ("vert", "automne")),
    "compas_marine.png",
    *(f"feuille_{cote}_{c}.png" for cote in ("gauche", "droite") for c in ("marine", "automne")),
    "bandeau_lettre_daily.png",
    "bandeau_lettre_weekly.png",
    "bandeau_lettre_tel.png",
]


def logo_url(L: dict) -> str:
    return f"{L['site']}/lettres/" if L.get("site") else ""


# La weekly garde le compas et les feuilles d'automne (images) ; son texte est de la même encre que la daily : la table
# des teintes remplaçait le marine, devenu la couleur de tout le texte.
AUTOMNE = {"#2a3d5f": "#b3501f", "#16233b": "#9a4317"}  # les teintes des images d'automne (compas, feuilles)


# Le nom d'une lettre et son slogan se lisent au registre (`graphes.<nom>.lettre_nom`, `lettre_slogan`). Un graphe sans nom garde
# l'en-tête d'avant, sans bannière (mainteneur, 04.11.2026).
GARAMOND = "font-family:'EB Garamond',Garamond,Georgia,'Times New Roman',serif;"  # le seul titre de la lettre


def _declare(L: dict) -> dict:
    if "lettre_nom" in L:
        return {
            "lettre_nom": L.get("lettre_nom"),
            "lettre_titre": L.get("lettre_titre"),
            "lettre_slogan": L.get("lettre_slogan"),
        }
    if not L.get("graphe"):
        return {}
    from newskit import config_sources as cfg  # noqa: PLC0415

    return cfg.graphe_declare(L["graphe"])


def nom_lettre(L: dict) -> str:
    """Le nom de la lettre du graphe (« The Governance News »), vide s'il n'en a pas au registre."""
    return str(_declare(L).get("lettre_nom") or "")


def titre_lettre(L: dict) -> str:
    """Le titre de la lettre au bandeau et dans l'objet, devant « daily » ou « weekly » (« The Governance », mainteneur,
    05.11.2026) : `lettre_titre` au registre, sinon le nom de la lettre."""
    return str(_declare(L).get("lettre_titre") or "") or nom_lettre(L)


def slogan(L: dict) -> str:
    return str(_declare(L).get("lettre_slogan") or "")


def banniere(L: dict) -> bool:
    """Vrai si la lettre porte la bannière (compas, titre, date) : son graphe a un nom de lettre au registre."""
    return bool(nom_lettre(L))


def entete_simple(L: dict) -> str:
    """L'en-tête d'avant la bannière : « MARQUE - Daily du mercredi 4 nov. 2026 » (mainteneur, 03.11.2026, validé)."""
    p = PERIODES.get(L.get("periode") or "")
    reste = (
        f"Weekly {date_entete(L)}"
        if L.get("periode") == "semaine"
        else (f"{p} du {date_entete(L)}" if p else date_entete(L))
    )
    return f"{str(L['marque']).upper()} - {reste}"


def entete(L: dict) -> tuple[str, str, str]:
    """(« The Governance », « daily », « mardi 3 nov. 2026 ») ; la weekly : (…, « weekly », « du 25 oct. au 1er nov. 2026 »)
    (mainteneur, 03.11.2026)."""
    return titre_lettre(L), (PERIODES.get(L.get("periode") or "") or "").lower(), date_entete(L)


def numero_fin_html(n: int) -> str:
    """Le numéro d'un élément dans la lettre du mainteneur, pour le désigner en approfondissement : en fin de texte,
    après les sources, seulement là (mainteneur, 04.11.2026 : « fais la apparaître seulement à la fin »)."""
    return (
        f'&nbsp;&nbsp;<span style="font-family:Menlo,Consolas,monospace; font-size:12px; color:#7a8088; '
        f'white-space:nowrap;">[{n}]</span>'
    )


def html(L: dict, wip: bool = False, web: bool = False, numeros: bool = False, coulisses: bool = False) -> str:
    """La lettre en HTML de courriel, ses liens d'abord vers les pages de l'édition (liens_de_l_edition) ; `wip` : la page
    de travail, même contenu, un numéro de référence par élément dans la marge gauche (index()) et les liens aux
    sources ; `web` : la page publique de la lettre, une ancre n<numéro> par élément et les liens aux sources."""
    with _langue(L):
        return _html(L, wip, web, numeros, coulisses)


def _html(L: dict, wip: bool = False, web: bool = False, numeros: bool = False, coulisses: bool = False) -> str:
    anglaise = L.get("langue") == "en"
    L = polir(L, html_=True)
    refs = iter(range(len(L["faits"]) + 1, 10**6))
    liens = {} if wip or web or anglaise else liens_de_l_edition(L)  # les éclairages sont en français

    def ancre(n: int) -> str:
        return f'<span id="n{n}"></span>' if web else ""

    apercu = (
        (
            ("In brief: " if anglaise else "À retenir : ")
            + ("; " if anglaise else " ; ").join(titre_avec_lieu(f) for f in L["faits"])
            + "."
        )
        if L["faits"]
        else ""
    )
    rangs = []
    nom, sorte, quand = entete(L)
    # L'en-tête sur le fond nu, au graphisme de la page d'accueil : le compas, la marque du site, le titre en Garamond
    # (« weekly », « daily » en italique), la date entre deux feuilles. Des PNG servis par le site : le courriel ignore
    # le SVG. Garamond reste dans ce seul titre (mainteneur, 05.11.2026 : « garamond devrait se limiter au logo »).
    lu = logo_url(L)
    # Mainteneur, 05.11.2026 : « ok projet daily, mais weekly pas convaincant. pour l'instant même graphisme que daily » : le
    # compas de la weekly est en marine ; les images d'automne restent servies.
    teinte = "marine"
    compas = (
        (
            f'<img src="{_e(f"{lu}compas_{teinte}.png")}" width="38" height="56" alt="" '
            f'style="border:0; outline:none; width:38px; height:56px;">'
        )
        if lu
        else ""
    )
    domaine, _, extension = _court(L.get("site") or "").rstrip("/").rpartition(".")
    marque = (
        (
            f'<div style="{POLICE} margin-top:8px; font-size:12px; font-weight:600; letter-spacing:.18em; '
            f'text-transform:uppercase; color:{SECOND};">{_e(domaine)}<span style="color:{POINT};">.{_e(extension)}</span></div>'
        )
        if domaine
        else ""
    )
    titre = _e(nom) + (f' <i style="font-style:italic; font-weight:500;">{_e(sorte)}</i>' if sorte else "")
    # La date entre deux puces (mainteneur, 05.11.2026 : « remplace les petites feuilles par des bullets ») : un même
    # caractère des deux côtés. Avant : deux feuilles d'aldine en image ; elles restent servies pour les lettres parties.
    # « bullets un peu plus grandes » : 18 px dans une ligne de 12, sans changer la hauteur de la ligne.
    puce = '<span style="font-size:18px; line-height:0; letter-spacing:0; vertical-align:-2px;">•</span>'
    if not banniere(L):
        rangs.append(
            f'<tr><td style="padding:0 8px 20px 8px;"><h1 style="{POLICE} margin:0; '
            f'font-size:18px; line-height:28px; font-weight:800; color:{TEXTE};">{_e(entete_simple(L))}</h1></td></tr>'
        )
    else:
        rangs.append(
            f'<tr><td class="ban" align="center" style="padding:0 8px 24px 8px; text-align:center;">{compas}{marque}'
            f'<h1 class="titre" style="{GARAMOND} margin:6px 0 0 0; font-size:36px; line-height:1.12; font-weight:600; color:{TEXTE};">{titre}</h1>'
            f'<div style="{POLICE} margin-top:8px; font-size:12px; line-height:16px; font-weight:600; letter-spacing:.12em; '
            f'text-transform:uppercase; color:{TEXTE};">{puce}&nbsp;&nbsp;{_e(quand)}&nbsp;&nbsp;{puce}</div></td></tr>'
        )
    # La première carte : « À la une ». Chaque fait porte son numéro sur la ligne de son titre, même ligne de base ; la
    # rubrique est au-dessus, dans la colonne du titre (mainteneur, 05.11.2026 : « aligner les numéros avec les titres, pas
    # les rubriques », « les numéros devraient en tout cas être sans serif », « les titres sans serif »).
    if L["faits"]:
        une = _etiquette(_l("À la une"))
        for i, f in enumerate(L["faits"], 1):
            rub = _etiquette(f["rubrique"], "0", 11) if f.get("rubrique") else ""
            lieu = f"{_e(f['lieu'])} : " if f.get("lieu") else ""  # « USA : La FDA … » (mainteneur, 03.11.2026)
            corps = (
                f'<div class="une" style="{POLICE} margin-top:3px; font-size:20px; line-height:1.25; font-weight:600; color:{TEXTE};">'
                f"{lieu}{titre_relie(sans_veuve(_e(titre_sans_lieu(f))), liens.get(i, ''))}</div>"
            )
            s = f["source"]
            ligne = _lien(
                s, editeur_affiche(s["editeur"]) + (f", {date_fr(s['date'])}" if s.get("date") else ""), "src", LIEN
            )
            if f.get("autres"):
                ligne += _e(_ligne_source({"editeur": ""}, f["autres"]))
            # La source suit le texte (mainteneur, 03.11.2026) ; elle passe à la ligne en bloc quand elle ne tient pas.
            ligne = (
                ancre(i)
                + f'<span style="display:inline-block; font-size:13px; color:{DOUX};">{ligne}</span>'
                + (numero_fin_html(i) if numeros and not wip else "")
            )
            corps += (
                f'<div style="margin-top:8px; font-size:15px; line-height:1.55; color:{TEXTE};">'
                f"{avec_liens(chapeau_html(f['paragraphe'], f.get('gras') or []), f.get('liens'))} {ligne}</div>"
            )
            if f.get("lecture"):
                corps += f'<div style="margin-top:6px; font-size:15px; line-height:1.55; color:{TEXTE};">{lecture_html(f["lecture"])}</div>'
            corps += liees_html(f)
            haut = 16 if i > 1 else 0
            filet = f" border-top:1px solid {FILET};" if i > 1 else ""
            une += (
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:{16 if i > 1 else 12}px;{filet}">'
                f'<tr><td width="34" style="padding:{haut}px 0 0 0; font-size:0; line-height:0;">&nbsp;</td>'
                f'<td valign="top" style="padding:{haut}px 0 0 0;">{rub}</td></tr>'
                f'<tr><td class="num" valign="baseline" width="34" style="{POLICE} padding:3px 0 0 0; font-size:26px; line-height:25px; '
                f'font-weight:600; font-variant-numeric:tabular-nums; color:{TEXTE};">{i}</td>'
                f'<td valign="baseline">{corps}</td></tr></table>'
            )
        rangs.append(_carte(une))
    # La seconde carte : les rubriques, une étiquette en tête de chacune, un filet au-dessus à partir de la deuxième.
    if L["aussi"] or L["bref"]:
        blocs = ""
        for g, xs, breves in rubriques_de(L):
            blocs += (
                _etiquette(g)
                if not blocs
                else f'<div style="margin-top:22px; padding-top:18px; border-top:1px solid {FILET};">{_etiquette(g)}</div>'
            )
            for x in [
                *xs,
                *breves,
            ]:  # les brèves en fin de rubrique, même forme que les billets (mainteneur, 03.11.2026)
                r = next(refs)
                s = x.get("source") or _source_de_breve(x)
                texte_ = avec_liens(_e(x["descriptif"]), x.get("liens")) if x.get("descriptif") else ""
                if x.get("lecture"):
                    texte_ += lecture_html(x["lecture"])
                texte_ += (
                    f' <span style="display:inline-block; font-size:13px; color:{DOUX};">{_sources_html(s, x)}</span>'
                    + (numero_fin_html(r) if numeros and not wip else "")
                )
                blocs += (
                    f'<div style="margin-top:12px;{" position:relative;" if wip else ""}">{_ref(r) if wip else ""}{ancre(r)}'
                    f'<div style="{POLICE} font-size:16px; line-height:1.35; font-weight:600; color:{TEXTE};">{titre_relie(sans_veuve(_e(titre_avec_lieu(x))), liens.get(r, ""))}</div>'
                    f'<div style="margin-top:4px; font-size:15px; line-height:1.55; color:{SECOND};">{texte_}</div>{liees_html(x)}</div>'
                )
        if L["bref_reste"]:
            blocs += f'<div style="margin-top:12px; font-size:13px; color:{DOUX};">{_e(f"… and {L['bref_reste']} more." if anglaise else f"… et {L['bref_reste']} autres.")}</div>'
        if L["faits"]:
            rangs.append(ESPACE)
        rangs.append(_carte(blocs))
    # Coulisses : dans la version de travail et dans la WIP du mainteneur seulement ; la daily finit sur le pied de
    # désinscription (mainteneur, 03.11.2026 ; 04.11.2026 : « la WIP devient la daily numérotée avec une section sur le
    # process en bas de page »). Une carte de plus.
    if wip or coulisses:
        # Coulisses : le seul lieu du vocabulaire de machine.
        tuiles = [
            (nombre(L["notions_neuves"]), "notions neuves"),
            (nombre(L["neuf"].lstrip("+") or 0), "sources ajoutées"),
            (nombre(L["noeuds"]) + (f" {ecart(L['ecart_noeuds'])}" if L["ecart_noeuds"] else ""), "nœuds"),
            (nombre(L["aretes"]) + (f" {ecart(L['ecart_aretes'])}" if L["ecart_aretes"] else ""), "arêtes"),
        ]
        tuiles_h = "".join(
            f'<div class="tuile" style="display:inline-block; width:150px; margin:0 8px 8px 0; padding:10px 12px; '
            f'background:#ffffff; border:1px solid {FILET}; border-radius:8px; vertical-align:top;">'
            f'<div style="{POLICE} font-variant-numeric:tabular-nums; font-size:20px; font-weight:800; color:{TEXTE};">{_e(v)}</div>'
            f'<div style="{POLICE} font-size:12px; color:{DOUX};">{_e(t)}</div></div>'
            for v, t in tuiles
        )
        lignes = [("Passage", heure_fr(L["heure"])), ("Sources", f"{nombre(L['fiches'])} en tout")]
        if L.get("identifiants"):
            lignes.append(
                ("Notions neuves", f"{nombre(L['notions_neuves'])} sur {nombre(L['identifiants'])} identifiants neufs")
            )
        if L.get("actualite"):
            lignes.append(("Actualité", _actualite(L)))
        if _banc(L):
            lignes.append(("Banc", _banc(L)))
        lignes.append(("Coût", _cout(L)))
        table = "".join(
            f'<tr><td style="{POLICE} font-size:13px; color:{DOUX}; padding:4px 14px 4px 0; vertical-align:top; white-space:nowrap;">{_e(a)}</td>'
            f'<td style="{POLICE} font-size:13px; color:{TEXTE}; padding:4px 0;">{_e(b)}</td></tr>'
            for a, b in lignes
        )
        coul = (
            _etiquette("Coulisses")
            + _p(_e(_verdict(L)), 14, marge="12px 0 10px 0")
            + tuiles_h
            + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{table}</table>'
        )
        if L["sante"]:
            sante = "".join(_p(_e(s), 13, TEXTE, "t-texte", "0 0 6px 0") for s in L["sante"])
            coul += (
                f'<div style="margin-top:12px; background:{AMBRE_FOND}; border:1px solid #f0d9a8; border-radius:8px; padding:10px 14px;">'
                + _p("Santé du passage", 13, AMBRE, "t-ambre", "0 0 6px 0", True)
                + sante
                + "</div>"
            )
        if L.get("selection"):  # le Sélectionneur, en mode proposition (mainteneur, 04.11.2026)
            props = "".join(
                _p(
                    f"<b>n° {_e(x['ref'])}</b> {_e(x['titre'])} : {_e(x['hypothese'])} "
                    f'<span style="color:{DOUX};">({_e(x["raison"])}){_e(_meme_source(x))}</span>',
                    13,
                    TEXTE,
                    "t-texte",
                    "0 0 6px 0",
                )
                for x in L["selection"]
            )
            coul += (
                '<div style="margin-top:12px;">'
                + _p("Sujets d'enquête proposés", 13, TEXTE, "t-fort", "0 0 6px 0", True)
                + props
                + "</div>"
            )
        coul += _p(
            f"Rapport complet : graphe/{_e(L['graphe'])}/ROUTINE.md du dépôt {_e(L['depot'])}."
            + (
                f'<br>Mesures et graphes : <a href="{_e(L["site"])}/#qualite" style="color:{LIEN};">'
                f'{_e(_court(L["site"]))}/#qualite</a> · <a href="{_e(L["site"])}/#graphes" '
                f'style="color:{LIEN};">{_e(_court(L["site"]))}/#graphes</a>'
                if L["site"]
                else ""
            ),
            12,
            DOUX,
            "t-doux",
            "14px 0 0 0",
            extra=f" padding-top:12px; border-top:1px solid {FILET};",
        )
        rangs.append(ESPACE)
        rangs.append(_carte(coul))
    # Le pied sur le fond nu, centré.
    rangs.append(
        f'<tr><td class="pied" align="center" style="{POLICE} padding:16px 8px 0 8px; text-align:center; font-size:12px; '
        f'line-height:1.5; color:{DOUX};">{pied_html(L)}</td></tr>'
    )
    # Le fond en ondes de la page d'accueil, une image transparente servie par le site, posée une fois en haut et au
    # centre (mainteneur, 05.11.2026 : « reviens au fonds précédent »). Sans elle, le fond uni.
    # Une image par titre, la boîte à sa largeur (« diminue encore un peu les marges latérales autour du titre »).
    ondes = (
        f" background-image:url({_e(lu)}bandeau_lettre_{'weekly' if L.get('periode') == 'semaine' else 'daily'}.png); background-repeat:no-repeat; background-position:center top;"
        if lu
        else ""
    )
    # Au téléphone, l'image du téléphone (mainteneur, 05.11.2026 : « le pas est correct pour la version phone ») ; un client
    # qui retire la feuille de style garde celle de l'ordinateur.
    tel = (
        f"\n<style>@media (max-width: 480px) {{ .fond {{ background-image:url({_e(lu)}bandeau_lettre_tel.png) !important; }} }}</style>"
        if lu
        else ""
    )
    page = f"""<!DOCTYPE html>
<html lang="{"en" if anglaise else "fr"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{_e(objet(L))}</title>
{STYLE}{tel}
</head>
<body style="margin:0; padding:0; background:{FOND}; color:{TEXTE};">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">{_e(apercu)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{FOND}" style="background-color:{FOND};">
<tr><td align="center" class="fond" style="padding:30px 12px 34px 12px;{ondes}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">
{chr(10).join(rangs)}
</table>
</td></tr></table>
</body></html>
"""
    return (
        _texte_seul(page, lambda t: ponctuation(guillemets(t)))
        if anglaise
        else _texte_seul(page, lambda t: ponctuation(guillemets(sans_fiche(t))))
    )


def lettre_du_graphe(graphe: str, rapport: str, nom: str = "", periode: str = "jour") -> dict | None:
    """La lettre d'un graphe pour une période (« jour » : le dernier passage ; « semaine » : les 8 derniers jours), ou
    None si nouveautes.json n'a pas encore le format B16."""
    passage = passages_de_la_periode(graphe, periode)
    if passage is None:
        return None
    fiches = charger_fiches(versees_a_ecrire(graphe, passage, periode))
    from newskit import notifier_courriel as nc  # noqa: PLC0415

    date = nc.lire_rapport(rapport).get("date") or ""
    from newskit import angle_critique  # noqa: PLC0415

    L = construire(
        rapport,
        passage,
        fiches,
        banc=dernier_banc(graphe, date),
        nom=nom,
        graphe=graphe,
        angle_=angle_critique.annoter,
        nouvelles=nv.lire(graphe),
        **({"fraicheur": FRAICHEUR_JOUR, "minimum_rubrique": 0} if periode == "jour" else {}),
    )
    L["periode"] = periode
    if periode == "semaine":
        L["debut"] = passage.get("debut")
    return L

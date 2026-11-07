#!/usr/bin/env python3
"""Le contrat de fiche du corpus. Source unique de vérité.

Pourquoi ce module existe. Le frontmatter du corpus n'avait pas été conçu : il
était le résidu de trois collecteurs écrits séparément, chacun inventant ses
clés. Il se trouvait régulier par accident, pas par construction. Ce module fait
du frontmatter un contrat explicite, et il le place là où les fiches naissent :
aucun collecteur n'écrit plus une fiche lui-même, tous appellent `ecrire`.

Trois conséquences. La normalisation est visible au point d'import et non
enfouie dans un script de migration. Une source nouvelle ne peut pas introduire
un vocabulaire divergent sans que la validation échoue. Et la migration du
corpus existant applique exactement la même fonction que les importateurs, donc
il n'existe pas deux définitions du schéma qui pourraient diverger.

Le squeletteur ne lit que les champs décrits ici.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --- Le contrat ------------------------------------------------------------

# « Modèle » est une plateforme au même titre que les autres, et c'est
# délibéré : un complément écrit par un modèle est une source, avec son auteur,
# sa date et son autorité propre — plus faible que celle d'un document publié.
# Le nommer plutôt que le fondre dans « Recherche interne » est ce qui permet de
# l'exclure d'une requête, ou de le distinguer dans une réponse.
PLATEFORMES = {"LinkedIn", "Substack", "Medium", "arXiv", "Recherche interne", "Modèle",
               "Bibliothèque locale", "Flux RSS", "Revue",
               # Plan de site et page de liste (07.10.2026) : les voies que l'audit /sap trouve aux sites sans flux.
               "Plan de site", "Page de liste",
               # Graphe financier (27.09.2026) : papiers par requête OpenAlex,
               # dépôts GitHub et du Hub HuggingFace, et les documents d'un
               # dossier local découpés par titre (dollar_fountain/research).
               "OpenAlex", "GitHub", "HuggingFace", "Dossier local",
               # Lien déposé depuis le navigateur par le porteur (03.10.2026, newskit.depots).
               "Dépôt",
               # Page de référence d'une notion du canon (04.10.2026, newskit.combler_canon) : Wikipédia.
               # Source tertiaire : elle ancre une notion, elle ne l'approfondit pas.
               "Référence",
               # Page trouvée par l'éclaireur (04.10.2026, newskit.eclaireur) : la recherche web d'un
               # modèle, dont on garde la page et non la réponse.
               "Recherche web",
               # Lettre d'information reçue par courriel (03.11.2026, newskit.collecte_courriel) : une boîte IMAP
               # lue en lecture seule, pour les sites qui refusent notre robot (Parlement européen).
               "Courriel"}

# Plateformes où un sous-titre existe vraiment. Ailleurs, son absence n'est pas
# un défaut : un article de revue ou un billet de flux n'en a pas.
SOUS_TITRE_ATTENDU = {"Substack", "LinkedIn", "Medium"}

# Noyau obligatoire, dans l'ordre d'écriture. (type, vide toléré)
NOYAU: dict[str, tuple[type, bool]] = {
    "title": (str, False),
    "subtitle": (str, True),
    "author": (str, True),        # vide toléré, signalé : la source ne le donne pas toujours
    "date": (str, False),
    "url": (str, True),
    "plateforme": (str, False),   # vocabulaire fermé, voir PLATEFORMES
    "publication": (str, True),   # vide légitime : billet personnel sans publication
    "words": (int, False),
    "complet": (bool, False),     # False = corps tronqué (paywall)
    "collected": (str, False),
}

# Blocs propres à une source. Leur absence ailleurs n'est pas un défaut.
OPTIONNELS: dict[str, type] = {
    "source": str,                                             # hérité, descriptif
    "edition": int, "likes": int, "comments": int,             # LinkedIn
    "audience": str, "type": str, "wordcount_annonce": int,    # Substack
    "arxiv_id": str, "categories": str, "doi": str, "comment": str,   # arXiv
    "origine": str, "verifie": str,                            # source « Modèle »
    # Bibliothèque locale : documents déjà sur le disque, souvent des PDF.
    # `papier_arxiv` porte l'identifiant du papier quand le document en est un
    # tirage local — et non `arxiv_id`, qui sert d'identité de nœud et ferait
    # entrer deux fiches sous un seul nœud, ce que le contrôle de fraîcheur
    # signalerait comme une perte.
    "pages": int, "chemin_local": str, "papier_arxiv": str, "origine_date": str,
    "date_avant_correction": str,                              # la date d'avant une correction prouvée (03.11.2026)
    "date_modification": str,                                  # <lastmod> d'un plan de site, jamais une publication
    "origine_auteur": str,
    "flux": str,                                               # source « Flux RSS »
    "issn": str, "revue": str,                                 # source « Revue » (Crossref)
    "requete": str, "citations": int,                          # source « OpenAlex »
    "etoiles": int, "langage": str, "sujets": str,             # source « GitHub »
    "genre": str, "telechargements": int,                      # source « HuggingFace » (et likes, comme LinkedIn)
    "fichier": str, "lignes": str, "section": str,             # source « Dossier local »
    "source_manquante": str,                                   # « <code>, <date> » : la source a disparu (404, 410, 451) ; la fiche reste (mainteneur, 01.11.2026)
    "note": str, "depot_id": int,                              # source « Dépôt » : ce que le porteur a dit en déposant
    # Texte de référence (diachronie.py, 09.10.2026) : la famille du texte, la date de sa version, « en vigueur » ou
    # « remplacée », la fiche de la version en vigueur qui remplace celle-ci, et le rang de la partie (art-015).
    "texte": str, "version": str, "statut": str, "remplacee_par": str, "partie": str,
}

ORDRE = list(NOYAU) + list(OPTIONNELS)

# L'identité d'une fiche (CHANTIER_GENERIQUE.md § 4.2, 06.11.2026) : les champs qui disent quel document elle porte.
# `partie` en est : les articles d'un texte découpé partagent l'adresse du texte. `papier_arxiv` n'en est pas (un
# tirage local reste distinct du papier). Un champ nouveau du contrat se classe ici, sinon tests/test_identite.py tombe.
IDENTITE = frozenset({"url", "arxiv_id", "doi", "partie"})
NON_IDENTITE = frozenset({
    "title", "subtitle", "author", "date", "plateforme", "publication", "words", "complet", "collected",
    "source", "edition", "likes", "comments", "audience", "type", "wordcount_annonce", "categories", "comment",
    "origine", "verifie", "pages", "chemin_local", "papier_arxiv", "origine_date", "date_avant_correction",
    "date_modification", "origine_auteur", "flux", "issn", "revue", "requete", "citations", "etoiles", "langage",
    "sujets", "genre", "telechargements", "fichier", "lignes", "section", "source_manquante", "note", "depot_id",
    "texte", "version", "statut", "remplacee_par",
})
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- Dérivation de la plateforme et de la publication ----------------------

# Les domaines propres des publications : bloc `domaines:` du registre (N2), lu une fois. Une publication Substack sur
# son propre domaine prend le nom déclaré ; un domaine Medium est gardé tel quel : c'est un identifiant vérifiable, tiré
# de l'URL, et non un nom d'affichage deviné.
_DOMAINES: dict | None = None


def registre_des_domaines() -> Path:
    """Le registre des sources, que nomment les politiques (N3) : ses domaines servent à dériver une fiche."""
    from newskit import politiques  # noqa: PLC0415
    return politiques.registre_sources()


def _domaines() -> dict:
    global _DOMAINES
    if _DOMAINES is None:
        try:
            import yaml  # noqa: PLC0415
            f = registre_des_domaines()
            d = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("domaines") if f.exists() else None
            if d is None and f.exists():
                from newskit import media  # noqa: PLC0415 — le bloc peut vivre au config.yaml du média (07.11.2026)
                d = media.blocs_pour(f)[0].get("domaines")
        except Exception:  # noqa: BLE001 — un registre illisible : charger le dira, la fiche se dérive sans
            d = None
        _DOMAINES = d if isinstance(d, dict) else {}
    return _DOMAINES


def domaines_substack() -> dict[str, str]:
    v = _domaines().get("substack")
    return dict(v) if isinstance(v, dict) else {}


def domaines_medium() -> set[str]:
    v = _domaines().get("medium")
    return set(v) if isinstance(v, list) else set()


def _hote(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def deduire_plateforme(url: str) -> str:
    """Plateforme d'origine, déduite du domaine de l'URL."""
    h = _hote(url)
    if not h:
        return "Recherche interne"
    if "arxiv.org" in h:
        return "arXiv"
    if "linkedin.com" in h:
        return "LinkedIn"
    if h in domaines_substack() or h.endswith(".substack.com"):
        return "Substack"
    if h in domaines_medium() or h == "medium.com" or h.endswith(".medium.com"):
        return "Medium"
    if h == "github.com":
        return "GitHub"
    if h == "huggingface.co":
        return "HuggingFace"
    return "Recherche interne"


def deduire_publication(url: str, plateforme: str, existante: str = "") -> str:
    """Nom de publication, déduit de l'URL quand la source ne le fournit pas.

    Pour Medium, la valeur est mécaniquement tirée de l'URL : un identifiant
    traçable, pas un nom d'affichage officiel. Un billet personnel, sous
    `medium.com/@auteur/…`, n'a légitimement pas de publication.
    """
    if existante and existante.strip():
        return existante.strip()
    h = _hote(url)
    if plateforme == "arXiv":
        return "arXiv"            # la catégorie primaire est portée par `categories`
    if plateforme == "LinkedIn":
        # Le nom d'une newsletter LinkedIn ne se lit pas dans l'URL de ses
        # éditions ; c'est le registre qui le porte, et le collecteur le passe.
        # Une valeur devinée ici a été fausse dès la deuxième newsletter.
        return ""
    if plateforme == "Substack":
        return domaines_substack().get(h, h)
    if plateforme == "Medium":
        if h in domaines_medium():
            return h
        if h.endswith(".medium.com"):
            return ""                         # profil personnel
        segments = [s for s in urlsplit(url).path.split("/") if s]
        if segments and not segments[0].startswith("@"):
            return segments[0].replace("-", " ").title()
        return ""                             # medium.com/@auteur/…
    return "Recherche interne"


# --- Lecture ---------------------------------------------------------------

RE_FM_LIGNE = re.compile(r"^([a-z_]+):\s*(.*)$")


# --- Identité ----------------------------------------------------------------

RE_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|10\.48550/arxiv\.)(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})", re.I)
RE_DOI_URL = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.I)


class IdentiteDejaVue(Exception):
    """Une fiche neuve dont une clé d'identité est déjà portée par une autre fiche du même graphe."""

    def __init__(self, chemin: Path, existante: Path, cle: str, graphe: str):
        self.chemin, self.existante, self.cle, self.graphe = Path(chemin), Path(existante), cle, graphe
        super().__init__(f"{cle} déjà portée par {existante} (graphe {graphe}) : {Path(chemin).name} non écrite")


class HorsGraphe(ValueError):
    """Une fiche neuve sous la racine des données, dans un dossier qu'aucun graphe ne lit."""


def url_canonique(url: str) -> str:
    """Schéma et hôte en minuscules, barre finale et `utm_*` retirés, rien d'autre : la requête et le fragment
    portent souvent l'identité (`?uri=CELEX:…`, `watch?v=`, `#art_70`)."""
    s = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip("/"), urlencode(q), s.fragment))


def cles_d_identite(champs: dict) -> frozenset:
    """Les clés d'identité d'une fiche : `url:<canonique>` (avec `|partie:` pour un texte découpé),
    `arxiv:<identifiant sans version>` (champ, adresse abs/pdf, DOI 10.48550), `doi:<minuscules>`."""
    k: set[str] = set()
    url = str(champs.get("url") or "").strip()
    if re.match(r"https?://", url, re.I):
        partie = str(champs.get("partie") or "").strip()
        k.add("url:" + url_canonique(url) + (f"|partie:{partie}" if partie else ""))
        if m := RE_ARXIV.search(url):
            k.add("arxiv:" + m.group(1).lower())
        if RE_DOI_URL.match(url):
            k.add("doi:" + RE_DOI_URL.sub("", url).lower())
    if aid := re.sub(r"v\d+$", "", str(champs.get("arxiv_id") or "").strip()):
        k.add("arxiv:" + aid.lower())
    if doi := RE_DOI_URL.sub("", str(champs.get("doi") or "").strip()).lower():
        k.add("doi:" + doi)
        if m := RE_ARXIV.search(doi):
            k.add("arxiv:" + m.group(1).lower())
    return frozenset(k)


def racine_des_donnees() -> Path:
    from newskit import config_sources as cfg  # noqa: PLC0415 — config_sources importe fiche
    return Path(cfg.RACINE)


def dossiers_des_graphes() -> dict[str, list[str]]:
    """Les dossiers lus par chaque graphe, inclusions comprises, relatifs à la racine des données."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    return {g: cfg.dossiers_du_graphe(g) for g in cfg.graphes()}


# Index en mémoire, une fois par processus et par graphe, tenu à jour par chaque écriture : sans cache à périmer.
_INDEX: dict[tuple[str, str], dict[str, Path]] = {}
_CARTE: dict[str, dict[str, list[str]]] = {}
TETE = 4000          # octets lus d'une fiche pour son en-tête


def oublier_index() -> None:
    _INDEX.clear()
    _CARTE.clear()


def _carte(racine: Path) -> dict[str, list[str]]:
    if str(racine) not in _CARTE:
        _CARTE[str(racine)] = dossiers_des_graphes()
    return _CARTE[str(racine)]


def cles_du_fichier(p: Path) -> frozenset:
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return cles_d_identite(lire(f.read(TETE))[0])
    except OSError:
        return frozenset()


def index_du_graphe(graphe: str, racine: Path | None = None) -> dict[str, Path]:
    """clé d'identité → première fiche qui la porte, pour toutes les fiches lues par le graphe."""
    racine = (racine or racine_des_donnees()).resolve()
    cle = (str(racine), graphe)
    if cle not in _INDEX:
        idx: dict[str, Path] = {}
        for d in _carte(racine).get(graphe, []):
            for p in sorted((racine / d).rglob("*.md")):
                for k in cles_du_fichier(p):
                    idx.setdefault(k, p.resolve())
        _INDEX[cle] = idx
    return _INDEX[cle]


def deja_vue(graphe: str, champs: dict) -> Path | None:
    """La fiche du graphe qui porte déjà ce document (une de ses clés d'identité), ou None. Sert de préfiltre : une page
    connue n'est ni relue ni payée avant d'être refusée à l'écriture."""
    idx = index_du_graphe(graphe)
    for k in sorted(cles_d_identite(champs)):
        if (e := idx.get(k)) and e.exists():
            return e
    return None


def identites_du_graphe(graphe: str) -> set[str]:
    """Les clés d'identité portées par les fiches du graphe encore sur le disque."""
    return {k for k, e in index_du_graphe(graphe).items() if e.exists()}


def graphes_du_chemin(chemin: Path) -> list[str] | None:
    """Les graphes qui lisent ce chemin ; None hors de la racine des données (essais, exports)."""
    racine = racine_des_donnees().resolve()
    chemin = Path(chemin).resolve()
    if not chemin.is_relative_to(racine):
        return None
    rel = chemin.relative_to(racine)
    return [g for g, ds in _carte(racine).items() if any(rel.is_relative_to(d) for d in ds)]


def _verifier_identite(chemin: Path, champs: dict, hors_graphe: bool, remplace: Path | None) -> list[str]:
    graphes = graphes_du_chemin(chemin)
    if graphes is None:
        return []
    if not graphes:
        if hors_graphe:
            return []
        raise HorsGraphe(f"{chemin} : aucun graphe ne lit ce dossier (hors_graphe=True pour l'écrire quand même)")
    soi = {Path(chemin).resolve(), *([Path(remplace).resolve()] if remplace else [])}
    for g in graphes:
        idx = index_du_graphe(g)
        for k in sorted(cles_d_identite(champs)):
            e = idx.get(k)
            if e and e not in soi and e.exists():
                raise IdentiteDejaVue(chemin, e, k, g)
    return graphes


def _noter(chemin: Path, champs: dict, graphes: list[str], remplace: Path | None) -> None:
    p = Path(chemin).resolve()
    ancienne = Path(remplace).resolve() if remplace else None
    for g in graphes:
        idx = index_du_graphe(g)
        for k in cles_d_identite(champs):
            if not (idx.get(k) and idx[k] not in (p, ancienne) and idx[k].exists()):
                idx[k] = p


def lire(texte: str) -> tuple[dict, int]:
    """Retourne (champs, numéro de la première ligne du corps).

    Le format est volontairement strict : une clé par ligne, valeurs en JSON.
    Ce n'est pas du YAML général, et c'est délibéré. Une fiche non conforme doit
    être signalée plutôt que silencieusement mal lue.
    """
    if not texte.startswith("---"):
        return {}, 1
    lignes = texte.split("\n")
    champs: dict = {}
    for i, ligne in enumerate(lignes[1:], start=2):
        if ligne.strip() == "---":
            return champs, i + 1
        m = RE_FM_LIGNE.match(ligne)
        if not m:
            continue
        cle, brut = m.group(1), m.group(2).strip()
        try:
            champs[cle] = json.loads(brut) if brut else ""
        except json.JSONDecodeError:
            champs[cle] = brut.strip('"')
    return champs, len(lignes)


def lire_fichier(chemin: Path) -> tuple[dict, str]:
    """Retourne (champs, corps) d'une fiche sur disque."""
    texte = Path(chemin).read_text(encoding="utf-8", errors="replace")
    champs, premiere = lire(texte)
    corps = texte.split("\n", premiere - 1)[-1] if premiere > 1 else texte
    return champs, corps


# --- Normalisation ---------------------------------------------------------


def normaliser(champs: dict, corps: str = "") -> dict:
    """Complète une fiche pour qu'elle respecte le contrat. Idempotente.

    Additive : aucun champ existant n'est écrasé, seuls les manques sont
    comblés. `source`, hérité des premiers collecteurs, est conservé tel quel ;
    il est descriptif et remplacé fonctionnellement par plateforme + publication.
    """
    c = dict(champs)
    url = c.get("url", "") or ""

    c.setdefault("plateforme", deduire_plateforme(url))
    if c["plateforme"] not in PLATEFORMES:
        c["plateforme"] = deduire_plateforme(url)

    c["publication"] = deduire_publication(url, c["plateforme"], c.get("publication", ""))

    if not isinstance(c.get("words"), int) or isinstance(c.get("words"), bool) or c.get("words", 0) <= 0:
        c["words"] = len(corps.split()) if corps else int(c.get("words") or 0)

    if not isinstance(c.get("complet"), bool):
        # Sans indication contraire, une fiche est réputée complète. Seul
        # Substack sait dire l'inverse, et il le dit explicitement.
        c["complet"] = True

    for cle, (attendu, _) in NOYAU.items():
        c.setdefault(cle, "" if attendu is str else (0 if attendu is int else True))

    return c


# --- Contrôle de complétude du corps --------------------------------------

# Les outils de capture de page plafonnent leur sortie et insèrent un marqueur.
# Cinq fiches du corpus en portaient un, tout en se déclarant complètes, dont
# deux qui avaient perdu plus de la moitié de leur texte. Le marqueur est le
# signal fiable ; le compte de mots ne l'est pas, la fiche la plus abîmée étant
# à 0,81 du compte annoncé, soit dans la dispersion normale.
MARQUEURS_TRONCATURE = (
    "output truncated at",
    "[output truncated",
    "... [truncated]",
    "Output too large",
)


def verifier_corps(corps: str, champs: dict) -> tuple[list[str], list[str]]:
    """Retourne (erreurs, avertissements) sur la complétude du corps."""
    erreurs, avertissements = [], []
    for marqueur in MARQUEURS_TRONCATURE:
        if marqueur in corps:
            erreurs.append(f"corps tronqué : le marqueur « {marqueur} » est présent")
            break
    annonce = champs.get("wordcount_annonce") or 0
    mots = len(corps.split())
    if annonce > 0 and champs.get("complet") and mots < 0.80 * annonce:
        avertissements.append(
            f"corps courtaud : {mots} mots pour {annonce} annoncés ({100 * mots // annonce} %)")
    return erreurs, avertissements


# --- Validation ------------------------------------------------------------


def valider(champs: dict) -> tuple[list[str], list[str]]:
    """Retourne (erreurs, avertissements). Une erreur bloque l'écriture."""
    erreurs, avertissements = [], []

    for cle, (attendu, vide_ok) in NOYAU.items():
        if cle not in champs:
            erreurs.append(f"{cle} : absent")
            continue
        v = champs[cle]
        conforme = isinstance(v, attendu) and not (attendu is int and isinstance(v, bool))
        if not conforme:
            erreurs.append(f"{cle} : type {type(v).__name__}, attendu {attendu.__name__}")
            continue
        if isinstance(v, str) and not v.strip():
            # Un sous-titre n'existe que là où la plateforme en propose un. Le
            # signaler sur un article de revue ou un billet de flux noyait le
            # relevé sous 1 799 avertissements sans objet, et un relevé qu'on ne
            # lit plus ne protège de rien.
            if cle == "subtitle" and champs.get("plateforme") not in SOUS_TITRE_ATTENDU:
                continue
            if cle == "date" and champs.get("origine_date") == "inconnue":
                continue          # « date inconnue » : jamais la date de collecte à la place (03.11.2026)
            (avertissements if vide_ok else erreurs).append(f"{cle} : vide")

    for cle in ("date", "collected"):
        v = champs.get(cle)
        if isinstance(v, str) and v and not RE_DATE.match(v):
            erreurs.append(f"{cle} : « {v} » n'est pas au format AAAA-MM-JJ")

    p = champs.get("plateforme")
    if isinstance(p, str) and p and p not in PLATEFORMES:
        erreurs.append(f"plateforme : « {p} » hors vocabulaire {sorted(PLATEFORMES)}")

    if isinstance(champs.get("words"), int) and champs["words"] <= 0:
        avertissements.append("words : nul")

    for cle, v in champs.items():
        if cle in NOYAU:
            continue
        if cle not in OPTIONNELS:
            avertissements.append(f"{cle} : clé hors contrat")
        elif v != "" and not isinstance(v, OPTIONNELS[cle]):
            avertissements.append(f"{cle} : type {type(v).__name__}")

    if champs.get("title") in ("Medium", "Untitled", ""):
        avertissements.append("title : titre non extrait")

    return erreurs, avertissements


# --- Écriture --------------------------------------------------------------


def rendre(champs: dict, corps: str) -> str:
    """Sérialise une fiche : frontmatter dans l'ordre du contrat, puis corps."""
    connues = [(k, champs[k]) for k in ORDRE if k in champs]
    autres = [(k, v) for k, v in champs.items() if k not in ORDRE]
    lignes = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in connues + autres]
    return "---\n" + "\n".join(lignes) + "\n---\n\n" + corps.lstrip("\n").rstrip() + "\n"


RE_PUBLICITE_TITRE = re.compile(r"(?<![a-z])(webinar\w*|webinaire\w*|live demo|virtual event|free trial|essai gratuit|"
                                r"\d+% off|promo code)(?![a-z])")
# Dans le texte, seulement l'appel à s'inscrire ou à acheter : une bannière d'abonnement (« Lexis+ free trial ») ou de
# démonstration en marge d'un article n'en fait pas une publicité.
RE_PUBLICITE = re.compile(r"(?<![a-z])(register (?:now|today)|save your (?:seat|spot)|reserve your (?:seat|spot)|"
                          r"sponsored content|contenu sponsorise|partner content)(?![a-z])")


def _plier(s: str) -> str:
    import unicodedata  # noqa: PLC0415
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


def publicitaire(titre: str, corps: str = "") -> bool:
    """Une publicité (webinaire, démonstration, contenu sponsorisé, offre), lue au titre et au début du texte : elle
    n'entre pas dans le graphe (mainteneur, 03.11.2026 : le webinaire de The Hacker News à la une du 30 octobre)."""
    t = _plier(titre or "")
    if re.search(r"(?<![a-z])(revoir|replay|takeaways|notes from|compte rendu|recap|review of)(?![a-z])", t):
        return False                       # le compte rendu d'un événement passé est un contenu
    return bool(RE_PUBLICITE_TITRE.search(t) or RE_PUBLICITE.search(t + " . " + _plier(str(corps or "")[:400])))


def ecrire(chemin: Path, champs: dict, corps: str, *, strict: bool = True, hors_graphe: bool = False,
           remplace: Path | None = None) -> list[str]:
    """Point d'entrée unique pour créer une fiche. Normalise, valide, écrit.

    Tous les collecteurs passent par ici. C'est ce qui rend la standardisation
    explicite au niveau de l'import plutôt que rattrapée après coup.

    Retourne la liste des avertissements. Lève ValueError si une erreur de
    contrat subsiste et que `strict` est vrai.

    Une création dont une clé d'identité est déjà portée dans un graphe qui lit
    ce chemin lève IdentiteDejaVue (`.existante`) ; `remplace` nomme la fiche
    qu'une création remplace (un déplacement). Une création sous la racine des
    données hors de tout graphe lève HorsGraphe, sauf `hors_graphe=True`. La
    réécriture d'un même chemin reste libre.
    """
    if publicitaire(str(champs.get("title") or ""), re.sub(r"^#[^\n]*\n+", "", corps)):
        return ["publicité : fiche non écrite"]
    c = normaliser(champs, corps)
    erreurs, avertissements = valider(c)
    e2, a2 = verifier_corps(corps, c)
    erreurs += e2; avertissements += a2
    if erreurs and strict:
        raise ValueError(f"{chemin} ne respecte pas le contrat de fiche : " + " ; ".join(erreurs))
    chemin = Path(chemin)
    graphes = [] if chemin.exists() else _verifier_identite(chemin, c, hors_graphe, remplace)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(rendre(c, corps), encoding="utf-8")
    if graphes:
        _noter(chemin, c, graphes, remplace)
    return avertissements

#!/usr/bin/env python3
"""Registre des sources : lecture et validation de sources.yaml.

Pourquoi ce module. Chaque collecteur portait sa propre liste en dur : les
publications Substack dans un dictionnaire, l'URL de la newsletter LinkedIn
dans une constante, les thèmes de veille dans une autre, les dossiers denses
dans une troisième. Ajouter un abonnement demandait donc de retrouver tous les
endroits concernés, et rien ne garantissait qu'ils restaient d'accord. Le
registre est maintenant unique, déclaratif, et validé au chargement : une clé
inconnue, un champ manquant ou une plateforme hors du contrat de fiche échouent
à la lecture, avant toute collecte.

C'est le même principe que `fiche.py` applique au frontmatter, appliqué un cran
plus tôt : à l'entrée des sources plutôt qu'à l'entrée des fiches.

Les crédences. Une publication payante peut nommer une session, qui n'est
qu'un nom d'entrée du trousseau macOS. Aucune valeur secrète ne figure dans le
dépôt. La valeur n'est lue qu'au moment de l'appel réseau qui en a besoin, et
n'est jamais écrite dans un fichier, ni dans un journal, ni dans un rapport.

Usage :
    python3 -m newskit.config_sources            # ce qui est déclaré, crédences comprises
    python3 -m newskit.config_sources verifier   # une session ouvre-t-elle vraiment l'accès ?
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import media  # noqa: E402
from newskit import politiques  # noqa: E402

FICHIER = politiques.registre_sources()  # nommé par deploiement/politiques.yaml (N3)
SOURCES = RACINE / "sources"

from newskit import commun  # noqa: E402  (clé d'un auteur)
from newskit import fiche  # noqa: E402  (vocabulaire fermé des plateformes)

# « interne » vaut pour une source qui n'a pas d'abonnement du tout : elle
# n'est ni gratuite ni payante, elle est produite ici.
ABONNEMENTS = {"gratuit", "payant", "interne"}

# Champs admis pour toute source, puis champs propres à une plateforme. Un
# champ hors de ces listes est une faute de frappe : mieux vaut le dire.
COMMUNS_OBLIGATOIRES = {"cle", "plateforme", "nom", "dossier", "abonnement"}
COMMUNS_FACULTATIFS = {
    "dense",
    "session",
    "assistee",
    "note",
    "graphe",
    "posture",
    "autorite",
    "masque",
    "nature",
    "muette",
}
# La cause d'une source muette, dans un vocabulaire fermé (mainteneur, 03.11.2026 : liste publiable des sources muettes) :
# `muette: {cause: …, le: AAAA-MM-JJ}`, et `code` (le statut HTTP) pour un refus d'accès.
CAUSES_MUETTE = {
    "ne publie pas sur le sujet",
    "refus d'accès",
    "robots.txt bloque",
    "défi anti-robot",
    "flux mort",
    "à passer à l'OCR",
}
# La nature d'une source quand elle n'est pas un média : « registre », un registre ou un jeu de données régulier, dont
# chaque fiche nouvelle est une donnée et pas une actualité (lettre.non_actualite, mainteneur, 03.11.2026).
NATURES = {"registre"}
# Les droits de diffusion (porteur, 25.10.2026). Le contenu est publié sauf désaccord explicite de la source ou d'un de
# ses auteurs, chaque auteur étant démarché à part ; le graphe n'en dépend pas : sans contenu, nœuds et liens restent et
# le lecteur suit l'adresse de l'original (un saut de plus). Seuls les droits sortent du dossier d'admin (« cette
# partie admin ne doit pas être exportée, seulement les droits ») : les portées masquées, `masque` d'une source au
# registre et droits_auteurs.yaml pour les auteurs, écrits par newskit.accords. Statuts, dates, preuves, motifs et
# contacts restent dans le journal local, et ce module ne les lit pas.
PORTEES = ("extraits", "page_auteur")
# La posture d'une source (06.10.2026) : d'où elle parle, pour mesurer l'équilibre des voix d'un graphe
# (mesurer_avance.py, clé « voix » d'avance.json). Une étiquette par source, la dominante.
POSTURES = {
    "critique": "critique de la technique, de l'industrie ou de son discours (chercheurs, essayistes)",
    "militante": "ONG ou campagne qui plaide pour une cause (droits, désarmement, travailleurs)",
    "surete": "sûreté de l'IA, risques extrêmes, alignement",
    "industrie": "écrite depuis l'industrie, par un consortium d'entreprises ou par l'éditeur d'un logiciel (annonces)",
    "defense": "sécurité nationale, militaires, droit des conflits armés vu par les armées",
    "promotion": "promeut une technique ou une idée (transhumanisme, revenu universel…)",
    "institution": "autorité publique, organisation internationale, agence",
    "reflexion": "think tank généraliste",
    "recherche": "recherche académique, revue, blog de laboratoire",
    "vulgarisation": "explique la recherche à un public large (newsletters, blogs pédagogiques)",
    "presse": "journalisme",
    "mixte": "sources de natures diverses (éclaireur, dépôts, canon)",
}


# Le graphe que nourrit une source. Un graphe par usage : la veille
# scientifique et l'actualité quotidienne ne font pas le même graphe, et les
# mélanger noierait le premier sous le second. Le graphe principal garde ses
# chemins historiques (graphe/) ; les autres vivent sous graphe/<nom>/.
class ConfigInvalide(Exception):
    """Le registre est mal formé. Volontairement fatale : rien ne doit tourner ensuite."""


_brut_cache: dict = {}


def _brut(p: Path) -> dict:
    """Le registre tel qu'écrit, lu une fois par processus pour le registre par défaut (le graphe principal se lit à
    l'import, charger valide ensuite le même texte)."""
    st = p.stat()
    cle = (p, st.st_mtime_ns, st.st_size)  # un registre réécrit (accords, deplacer_sources, thèmes) se relit
    if p == FICHIER and cle in _brut_cache:
        return _brut_cache[cle]
    d = _compose(p, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    if p == FICHIER:
        _brut_cache.clear()
        _brut_cache[cle] = d
    return d


def _compose(p: Path, d: dict) -> dict:
    """Le registre `p` complété par le `config.yaml` du média qui le nomme (media.blocs_pour) : `media:` y devient le
    bloc `edition`, et les blocs que le média tient (graphes, bornes) s'y ajoutent. Un bloc tenu par les deux fichiers
    est refusé : une valeur, un seul endroit (07.11.2026)."""
    try:
        blocs, c = media.blocs_pour(p)
    except media.ConfigMediaInvalide as e:
        raise ConfigInvalide(str(e)) from e
    if c is None:
        return d
    if not isinstance(d, dict):
        return d
    doubles = sorted(k for k in blocs if k in d)
    if doubles:
        raise ConfigInvalide(
            f"{p.name} et {c.name} tiennent tous deux {doubles} : un bloc, un seul fichier "
            f"(`media:` de {c.name} remplace `edition:`)"
        )
    return {**d, **blocs}


def _bloc_a_l_import(cle: str) -> dict:
    """Un bloc du registre brut, lu à l'import ; vide si le registre manque ou ne se lit pas : charger dira pourquoi,
    et la routine pourra encore alerter (« sources.yaml invalide »)."""
    try:
        v = _brut(FICHIER).get(cle) if FICHIER.exists() else None
    except (OSError, yaml.YAMLError, AttributeError, ConfigInvalide):
        return {}
    return v if isinstance(v, dict) else {}


def _principal() -> str:
    v = _bloc_a_l_import("edition").get("principal")
    return v if isinstance(v, str) and v else "principal"


# Le graphe des sources sans `graphe:` : nommé par `edition.principal` au registre (N2), « principal » sans lui.
GRAPHE_PRINCIPAL = _principal()
# Fin des connaissances des modèles de langage courants, à la date du dernier relevé (04.10.2026).
# Le but des graphes est d'être en avance sur eux : ce qui est daté après cet horizon est ce qu'un
# modèle ne peut pas savoir. À avancer quand un modèle plus récent sort (mesurer_avance.py le dit).
HORIZON_MODELE = "2026-06-30"
NOM_DE_GRAPHE = media.NOM
# Les noms que des graphes ont portés : un serveur installé avant un renommage les passe encore, et ne doit pas refuser
# de démarrer pour autant. Bloc `anciens_noms:` du registre (N2), validé par charger.
ANCIENS_NOMS = {k: v for k, v in _bloc_a_l_import("anciens_noms").items() if isinstance(k, str) and isinstance(v, str)}


def nom_actuel(nom: str) -> str:
    return ANCIENS_NOMS.get(nom, nom)


PROPRES = {
    "LinkedIn": ({"newsletter", "auteur"}, set()),
    "Substack": (
        {"domaine"},
        {"depuis", "mots_cles"},
    ),  # mots_cles : exigés dans le titre ou le sous-titre   # depuis : AAAA-MM-JJ, les billets antérieurs ne sont pas collectés
    "Medium": (set(), {"mots_cles"}),
    "Modèle": (set(), set()),
    "Bibliothèque locale": ({"chemins"}, set()),
    "Flux RSS": (
        {"flux"},
        {"site", "page", "mots_cles", "doublons_de", "exclure", "entetes", "api_page", "api_champs", "api_segment"},
    ),
    # Trouvées par l'audit d'accès /sap (07.10.2026) : sur 50 sites « sans flux », 23 publiaient un plan de site vivant
    # et 11 une page de liste lisible en HTML statique. `chemins` : expression rationnelle sur l'adresse, qui ne garde
    # que les articles (les plans de site listent aussi les pages de service) ; `jours` : fenêtre sur <lastmod>.
    "Plan de site": ({"plan", "chemins"}, {"site", "mots_cles", "exclure", "entetes", "jours"}),
    "Page de liste": (
        {"liste", "chemins"},
        {"site", "mots_cles", "exclure", "entetes", "lien_motif", "lien_modele", "titre_motif", "corps_motif"},
    ),  # lien_motif et lien_modele : articles ouverts par un script (06.11.2026) ; titre_motif : le titre sur la page de l'article   # exclure : regex sur adresse et titre ; entetes : « navigateur » après un audit /sap
    "Revue": ({"issn"}, {"depuis", "mots_cles"}),
    "arXiv": (set(), set()),
    "Recherche interne": (set(), set()),
    "Recherche web": (set(), set()),  # pages trouvées par l'éclaireur (newskit.eclaireur), un dossier par graphe
    "Référence": (set(), set()),  # pages de référence et dépôts du canon (newskit.combler_canon), un dossier par graphe
    "Dépôt": (set(), set()),  # liens déposés depuis le navigateur (newskit.depots), un dossier par graphe
    # Graphe financier (27.09.2026).
    "OpenAlex": (
        {"requetes"},
        {"jours", "par_requete", "domaines", "mots_cles", "mots_cles_faibles", "ancre_titre"},
    ),  # domaines : liste blanche de « field » OpenAlex ; mots_cles : lexique exigé dans titre ou résumé ; mots_cles_faibles : ne comptent qu'avec ancre_titre dans le titre
    "GitHub": ({"requetes"}, {"jours", "par_requete", "etoiles_min"}),
    "HuggingFace": ({"requetes"}, {"jours", "par_requete", "genres", "activite_min"}),
    # Les documents d'un dossier hors dépôt, découpés par titre en fiches :
    # `racine` absolue, `fichiers` relatifs à elle, « chemin » ou « chemin:niveau ».
    "Dossier local": ({"racine", "fichiers"}, {"niveau"}),
    # Lettres d'information reçues par courriel (03.11.2026) : une source par expéditeur (adresse ou domaine), lue dans
    # une boîte déclarée sous `boites:` ; `site` : l'adresse publique de la publication, pour la liste des muettes.
    "Courriel": ({"expediteur", "boite"}, {"mots_cles", "site"}),
}

# Une boîte aux lettres IMAP (03.11.2026) : hôte, port, utilisateur, dossier. Jamais de mot de passe ici : il vit au
# trousseau macOS, service `<edition>:imap:<boite>` (mot_de_passe_boite). L'utilisateur est l'adresse de la boîte,
# personnelle : `utilisateur_env` nomme à sa place la variable qui la porte, lue hors dépôt (utilisateur_boite).
CHAMPS_BOITE = {"hote": str, "port": int, "dossier": str}
CHAMPS_UTILISATEUR = ("utilisateur", "utilisateur_env")


@dataclass
class Source:
    cle: str
    plateforme: str
    nom: str
    dossier: str
    abonnement: str
    dense: bool = False
    assistee: bool = False
    session: str | None = None
    note: str = ""
    graphe: str = GRAPHE_PRINCIPAL
    posture: str = ""
    autorite: str = ""  # « moindre » : ses affirmations ne fondent rien seules (synthese.py)
    nature: str = ""  # « registre » : un registre ou un jeu de données régulier (NATURES)
    propres: dict = field(default_factory=dict)
    masque: list = field(default_factory=list)  # portées dont le contenu n'est pas publié
    muette: dict = field(default_factory=dict)  # cause déclarée d'un silence : {cause, le, code?} (CAUSES_MUETTE)

    @property
    def payant(self) -> bool:
        return self.abonnement == "payant"

    @property
    def chemin(self) -> Path:
        """Dossier des fiches de cette source."""
        return SOURCES / self.dossier

    @property
    def manifeste(self) -> Path:
        return self.chemin / "_manifest.json"

    def __getattr__(self, nom: str):
        """Les champs propres à la plateforme (domaine, newsletter…) en attributs."""
        try:
            return self.__dict__["propres"][nom]
        except KeyError:
            raise AttributeError(f"{self.cle} : pas de champ « {nom} »") from None


def valider_muette(brut, ou: str) -> dict:
    """`muette: {cause, le, code?}` : une cause de CAUSES_MUETTE, la date de la constatation, le code HTTP d'un refus."""
    if not isinstance(brut, dict):
        raise ConfigInvalide(f"{ou} : muette doit être un bloc {{cause, le}}")
    cause, le = brut.get("cause"), str(brut.get("le") or "")
    if cause not in CAUSES_MUETTE:
        raise ConfigInvalide(f"{ou} : cause muette « {cause} » hors {sorted(CAUSES_MUETTE)}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", le):
        raise ConfigInvalide(f"{ou} : muette.le attend une date AAAA-MM-JJ")
    if set(brut) - {"cause", "le", "code"}:
        raise ConfigInvalide(f"{ou} : muette n'admet que cause, le, code")
    if (cause == "refus d'accès") != ("code" in brut):
        raise ConfigInvalide(f"{ou} : un refus d'accès porte son code HTTP, et seul lui")
    return {"cause": cause, "le": le, **({"code": int(brut["code"])} if "code" in brut else {})}


def valider_boites(brut, ou: str) -> dict:
    """`boites: {<nom>: {hote, port, utilisateur | utilisateur_env, dossier}}` : les quatre champs, typés, et rien
    d'autre (un mot de passe écrit ici serait un secret dans le dépôt)."""
    if brut is None:
        return {}
    if not isinstance(brut, dict):
        raise ConfigInvalide(f"{ou} : boites doit être un bloc {{nom: {{hote, port, utilisateur, dossier}}}}")
    for nom, b in brut.items():
        if not isinstance(b, dict):
            raise ConfigInvalide(f"{ou} : boites.{nom} doit être un bloc")
        if set(b) - set(CHAMPS_BOITE) - set(CHAMPS_UTILISATEUR):
            inconnus = sorted(set(b) - set(CHAMPS_BOITE) - set(CHAMPS_UTILISATEUR))
            raise ConfigInvalide(
                f"{ou} : boites.{nom} : champs inconnus {inconnus} ; le mot "
                f"de passe se dépose au trousseau, jamais au registre"
            )
        if sum(c in b for c in CHAMPS_UTILISATEUR) != 1:
            raise ConfigInvalide(
                f"{ou} : boites.{nom} : utilisateur ou utilisateur_env attendu, l'un des deux seulement"
            )
        for cle, genre in {**CHAMPS_BOITE, next(c for c in CHAMPS_UTILISATEUR if c in b): str}.items():
            v = b.get(cle)
            if not isinstance(v, genre) or isinstance(v, bool) or (genre is str and not v.strip()):
                raise ConfigInvalide(f"{ou} : boites.{nom}.{cle} : {genre.__name__} attendu")
    return brut


def valider_masque(brut, ou: str) -> list[str]:
    """Une liste non vide de portées, chacune une fois, rendue dans l'ordre canonique."""
    if not isinstance(brut, list) or not brut or len(set(brut)) != len(brut) or set(brut) - set(PORTEES):
        raise ConfigInvalide(f"{ou} : masque « {brut} » : une liste non vide, sans double, de {list(PORTEES)}")
    return [p for p in PORTEES if p in brut]


# --- publication du contenu ----------------------------------------------------------------------------------


def publiable(masque, portee: str) -> bool:
    """Une portée est publiée sauf si le masque la nomme."""
    if portee not in PORTEES:
        raise ValueError(f"portée « {portee} » hors {list(PORTEES)} (le graphe n'est pas une portée)")
    return portee not in masque


def contenu_publiable(source: Source | None, portee: str, auteurs, droits: dict | None = None) -> bool:
    """Le contrôle unique avant de servir le contenu d'une fiche : ni sa source ni aucun de ses auteurs ne masque cette
    portée. `source` : None pour une fiche hors registre (veille arXiv). `droits` : droits_auteurs.yaml chargé, par clé
    d'auteur (lu s'il manque)."""
    if not publiable(source.masque if source else [], portee):
        return False
    droits = charger_droits_auteurs() if droits is None else droits
    return all(publiable(droits.get(commun.normaliser_auteur(nom), {}).get("masque", []), portee) for nom in auteurs)


def reservee(source: Source | None, entete: dict) -> bool:
    """Le texte de cette fiche est-il réservé à son abonné ?

    Un abonnement payant est personnel : le billet qu'il ouvre ne se redistribue pas. Une bibliothèque locale tient des
    ouvrages et des papiers que l'utilisateur possède, pas le droit de les diffuser. Sont donc réservées les fiches des
    sources `payant` et `interne`, sauf celles que la publication a elle-même ouvertes à tous (`audience: everyone`,
    que Substack indique billet par billet). Une fiche réservée n'est pas relayée : le lecteur suit l'adresse de sa
    page (porteur, 25.10.2026)."""
    if source is None or source.abonnement not in ("payant", "interne"):
        return False
    return str(entete.get("audience") or "") != "everyone"


def fiche_publiable(source: Source | None, entete: dict, droits: dict | None = None, portee: str = "extraits") -> bool:
    """Le texte d'une fiche peut-il être servi ? Sa source, et chacun des auteurs de son champ `author`."""
    return contenu_publiable(source, portee, commun.separer_auteurs(entete.get("author")), droits)


def source_du_chemin(relatif: str) -> Source | None:
    """La source d'une fiche par son chemin relatif au dépôt (sources/…) : le dossier déclaré le plus long qui la
    contient, parmi toutes les sources. Pas seulement celles d'un graphe : un graphe en sert d'autres (un graphe financier sert les
    dépôts d'un graphe de trading quantitatif, mesuré le 25.10.2026)."""
    trouve = None
    for s in toutes():
        d = f"sources/{s.dossier}".rstrip("/") + "/"
        if relatif.startswith(d) and (trouve is None or len(s.dossier) > len(trouve.dossier)):
            trouve = s
    return trouve


FICHIER_DROITS = politiques.registre_auteurs()  # nommé par deploiement/politiques.yaml (N3)


def charger_droits_auteurs(chemin: Path | None = None) -> dict[str, dict]:
    """Les droits d'auteurs écrits par accords.py, par clé d'auteur : le nom et les portées masquées, rien d'autre.
    Fichier absent : aucun masque."""
    p = chemin or FICHIER_DROITS
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {}
    for cle, brut in (d.get("auteurs") or {}).items():
        ou = f"{p.name}, auteur « {cle} »"
        if not isinstance(brut, dict) or set(brut) != {"nom", "masque"}:
            raise ConfigInvalide(f"{ou} : un bloc {{nom, masque}} et rien d'autre")
        nom = str(brut["nom"]).strip()
        if commun.normaliser_auteur(nom) != cle:
            raise ConfigInvalide(f"{ou} : la clé n'est pas celle du nom « {nom} »")
        out[cle] = {"nom": nom, "masque": valider_masque(brut["masque"], ou)}
    return out


_cache: dict | None = None

# L'identité de l'édition qui se sert du moteur (bloc `edition:` du registre, NEWSKIT.md N2) : son nom (préfixe des
# clés de lecture dans Thalamus, producteur des métadonnées, objet des courriels, agent HTTP), son site, son dépôt et
# le clone de son serveur. Le moteur n'en écrit aucune valeur dans son code.
EDITION = dict(media.IDENTITE)  # les champs et leurs valeurs neutres vivent dans media.py (07.11.2026)


def lire_rubriques(decl: dict, ou: str = "rubriques") -> list[dict]:
    """Les rubriques d'un graphe, dans l'ordre déclaré : [{cle, nom, sujets: [{fr, en, mots}]}]. `decl` : {fichier, ordre},
    le fichier (relatif à la racine des données) tenant la table `themes` (cle, fr, court, sujets). Lève ConfigInvalide
    sur une rubrique inconnue de l'ordre, une rubrique de la table hors de l'ordre, un sujet rangé deux fois. Un seul
    point de passage : la lettre (lettre.py) range chaque fiche par le sujet que cette table lui donne."""
    if not isinstance(decl, dict) or set(decl) != {"fichier", "ordre"} or not isinstance(decl.get("ordre"), list):
        raise ConfigInvalide(f"{ou} : {{fichier, ordre}} attendus")
    chemin = Path(decl["fichier"])
    chemin = chemin if chemin.is_absolute() else RACINE / chemin
    try:
        table = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigInvalide(f"{ou} : {chemin.name} illisible ({type(e).__name__})") from e
    themes = {t.get("cle"): t for t in table.get("themes") or [] if isinstance(t, dict)}
    inconnues = [c for c in decl["ordre"] if c not in themes]
    if inconnues:
        raise ConfigInvalide(f"{ou} : rubrique(s) inconnue(s) {inconnues}")
    hors = [c for c in themes if c not in decl["ordre"]]
    if hors:
        raise ConfigInvalide(f"{ou} : rubrique(s) de {chemin.name} hors de l'ordre {hors}")
    vus: set[str] = set()
    out = []
    for c in decl["ordre"]:
        t = themes[c]
        sujets = []
        for x in t.get("sujets") or []:
            if not isinstance(x, dict) or not x.get("fr"):
                raise ConfigInvalide(f"{ou} : un sujet de « {c} » sans libellé français")
            if x["fr"] in vus:
                raise ConfigInvalide(f"{ou} : le sujet « {x['fr']} » est rangé deux fois")
            vus.add(x["fr"])
            sujets.append(
                {"fr": str(x["fr"]), "en": str(x.get("en") or ""), "mots": [str(m) for m in x.get("mots") or []]}
            )
        out.append({"cle": c, "nom": str(t.get("court") or t.get("fr") or c), "sujets": sujets})
    return out


def rubriques(graphe: str) -> list[dict]:
    """Les rubriques déclarées d'un graphe, ou [] s'il n'en a pas (il garde alors son classement d'avant)."""
    decl = graphe_declare(graphe).get("rubriques")
    return lire_rubriques(decl, f"graphes.{graphe}.rubriques") if decl else []


FRAICHEUR_LETTRE = 8


def fraicheur_lettre(graphe: str) -> int:
    """L'âge maximal, en jours, de la source principale d'un élément de la lettre (fraicheur_lettre_jours, 8 par
    défaut ; mainteneur, 03.11.2026)."""
    try:
        return int(graphe_declare(graphe).get("fraicheur_lettre_jours") or FRAICHEUR_LETTRE)
    except Exception:  # noqa: BLE001 — graphe absent du registre : la valeur par défaut
        return FRAICHEUR_LETTRE


FRAICHEUR_MIN_RUBRIQUE = 5


def fraicheur_min_rubrique(graphe: str) -> int:
    """Le nombre de fiches fraîches de la semaine sous lequel une rubrique est « à court d'actualité »
    (fraicheur_min_rubrique, 5 par défaut ; mainteneur, 03.11.2026)."""
    try:
        v = graphe_declare(graphe).get("fraicheur_min_rubrique")
    except Exception:  # noqa: BLE001 — graphe absent du registre : la valeur par défaut
        return FRAICHEUR_MIN_RUBRIQUE
    return FRAICHEUR_MIN_RUBRIQUE if v is None else int(v)


def edition() -> dict:
    """Le bloc `edition:` du registre sur les valeurs neutres ; les valeurs neutres sans registre lisible."""
    try:
        e = charger().get("edition") or {}
    except ConfigInvalide:
        e = {}
    return {**EDITION, **e}


def commande_maj_serveur() -> str | None:
    """La commande qui fait avancer le serveur tout de suite après un envoi : `media.service_maj` de config.yaml ;
    None sans service déclaré (le serveur se met alors à jour à son heure)."""
    s = edition().get("service_maj")
    return f"sudo systemctl start {s}.service" if s else None


def graphe_du_site() -> str:
    """Le graphe dont le site de l'édition sert le fil, la lettre et la station « Interroger le graphe » : la clé
    `graphe_du_site` du bloc `edition:`, sinon le graphe principal (coupe moteur-édition, 06.11.2026)."""
    e = edition()
    return e.get("graphe_du_site") or e["principal"]


def cerveau_thalamus(graphe: str) -> str:
    """Le cerveau de Thalamus d'un graphe : `cerveau_du_site` du bloc `edition:` pour le graphe du site s'il est
    déclaré, sinon « <nom de l'édition>/<graphe> » (07.11.2026)."""
    e = edition()
    if e.get("cerveau_du_site") and graphe == graphe_du_site():
        return e["cerveau_du_site"]
    return f"{e['nom']}/{graphe}"


def cerveau_des_lacunes() -> str:
    """Le cerveau de Thalamus où un lecteur signale ce qu'un graphe n'a pas su lui dire : `cerveau_lacunes` du bloc
    `edition:`, sinon « lacunes-signalees » (07.11.2026)."""
    return edition().get("cerveau_lacunes") or "lacunes-signalees"


def sites_de_l_edition() -> list[str]:
    """Les sites de l'édition, sans barre finale : celui de la lettre, le site, puis les anciens (`anciens_sites`, un
    site qui a changé de nom). Une page de l'un d'eux n'est ni un éditeur ni une preuve ; une image de lettre y est admise."""
    e = edition()
    bruts = [e.get("lettre_site"), e.get("site"), *str(e.get("anciens_sites") or "").split(",")]
    return list(dict.fromkeys(s for s in (str(b or "").strip().rstrip("/") for b in bruts) if s))


def charger(chemin: Path | None = None) -> dict:
    global _cache
    if _cache is not None and chemin is None:
        return _cache
    p = chemin or FICHIER
    if not p.exists():
        raise ConfigInvalide(f"registre absent : {p}")
    d = _brut(p)
    conf = _valider(d, p)
    if chemin is None:
        _cache = conf
    return conf


def _valider(d: dict, p: Path) -> dict:
    if d.get("version") != 1:
        raise ConfigInvalide(f"{p.name} : version attendue 1, lue {d.get('version')!r}")
    brutes = d.get("sources")
    if not isinstance(brutes, list) or not brutes:
        raise ConfigInvalide(f"{p.name} : « sources » doit être une liste non vide")

    creds = (d.get("credences") or {}).get("sessions") or {}
    vues_cles: set[str] = set()
    vus_dossiers: set[str] = set()
    sources: list[Source] = []

    for i, s in enumerate(brutes):
        ou = f"{p.name}, source #{i + 1}"
        if not isinstance(s, dict):
            raise ConfigInvalide(f"{ou} : bloc attendu, lu {type(s).__name__}")
        cle = s.get("cle", f"#{i + 1}")
        ou = f"{p.name}, source « {cle} »"

        manquants = COMMUNS_OBLIGATOIRES - set(s)
        if manquants:
            raise ConfigInvalide(f"{ou} : champs obligatoires absents : {sorted(manquants)}")

        plat = s["plateforme"]
        if plat not in fiche.PLATEFORMES:
            raise ConfigInvalide(
                f"{ou} : plateforme « {plat} » hors du contrat de fiche "
                f"{sorted(fiche.PLATEFORMES)} ; l'étendre dans newskit.fiche"
            )
        if plat not in PROPRES:
            raise ConfigInvalide(f"{ou} : plateforme « {plat} » sans collecteur déclaré ici")
        oblig, fac = PROPRES[plat]
        manquants = oblig - set(s)
        if manquants:
            raise ConfigInvalide(f"{ou} : champs {plat} absents : {sorted(manquants)}")
        admis = COMMUNS_OBLIGATOIRES | COMMUNS_FACULTATIFS | oblig | fac
        surplus = set(s) - admis
        if surplus:
            raise ConfigInvalide(f"{ou} : champs inconnus {sorted(surplus)} ; admis {sorted(admis)}")

        if s.get("posture") is not None and s["posture"] not in POSTURES:
            raise ConfigInvalide(f"{ou} : posture « {s['posture']} » hors {sorted(POSTURES)}")
        if s.get("autorite") is not None and s["autorite"] != "moindre":
            raise ConfigInvalide(f"{ou} : autorite « {s['autorite']} » : seule « moindre » est admise")
        if s.get("nature") is not None and s["nature"] not in NATURES:
            raise ConfigInvalide(f"{ou} : nature « {s['nature']} » hors {sorted(NATURES)}")
        if s["abonnement"] not in ABONNEMENTS:
            raise ConfigInvalide(f"{ou} : abonnement « {s['abonnement']} » hors {sorted(ABONNEMENTS)}")
        masque = valider_masque(s["masque"], ou) if "masque" in s else []
        muette = valider_muette(s["muette"], ou) if "muette" in s else {}
        if cle in vues_cles:
            raise ConfigInvalide(f"{ou} : clé en double")
        if s["dossier"] in vus_dossiers:
            raise ConfigInvalide(f"{ou} : dossier « {s['dossier']} » déjà pris par une autre source")
        if s.get("session"):
            if s["session"] not in creds:
                raise ConfigInvalide(f"{ou} : session « {s['session']} » absente de credences.sessions")
            if s["abonnement"] != "payant":
                raise ConfigInvalide(f"{ou} : une session n'a de sens que sur un abonnement payant")
        vues_cles.add(cle)
        vus_dossiers.add(s["dossier"])
        graphe = str(s.get("graphe") or GRAPHE_PRINCIPAL)
        if not NOM_DE_GRAPHE.match(graphe):
            raise ConfigInvalide(f"{ou} : nom de graphe « {graphe} » invalide (minuscules, chiffres, tirets)")
        # Depuis le 27.09.2026 (graphe financier), la couche 2 incrémentale et la
        # structure des titres sont produites par graphe : `dense` est admis partout.

        sources.append(
            Source(
                cle=cle,
                plateforme=plat,
                nom=s["nom"],
                dossier=s["dossier"],
                abonnement=s["abonnement"],
                dense=bool(s.get("dense")),
                assistee=bool(s.get("assistee")),
                session=s.get("session"),
                note=s.get("note", ""),
                graphe=graphe,
                posture=str(s.get("posture") or ""),
                autorite=str(s.get("autorite") or ""),
                nature=str(s.get("nature") or ""),
                propres={k: s[k] for k in (oblig | fac) if k in s},
                masque=masque,
                muette=muette,
            )
        )

    # Deux sources d'un même périmètre (un graphe et ceux qu'il inclut) sur une même publication font deux fiches de
    # chaque article (06.11.2026 : une même lettre déclarée deux fois dans un graphe, 116 doublons). Deux graphes
    # séparés peuvent lire la même publication (porteur, 06.11.2026).
    inclut = {g: set((v or {}).get("inclut") or []) for g, v in (d.get("graphes") or {}).items() if isinstance(v, dict)}
    lue_par: dict[tuple, str] = {}
    for s in sources:
        perimetres = {s.graphe} | {h for h, inc in inclut.items() if s.graphe in inc}
        for champ in ("flux", "domaine", "issn"):
            v = str(s.propres.get(champ) or "").strip().lower().rstrip("/")
            for h in sorted(perimetres) if v else ():
                autre = lue_par.setdefault((h, champ, v), s.cle)
                if autre != s.cle:
                    raise ConfigInvalide(
                        f"{p.name}, source « {s.cle} » : même publication que « {autre} » dans le "
                        f"graphe {h} ({champ} « {v} ») : deux fiches de chaque article"
                    )

    boites = valider_boites(d.get("boites"), p.name)
    for s in sources:
        if s.plateforme == "Courriel" and s.propres["boite"] not in boites:
            raise ConfigInvalide(f"{p.name}, source « {s.cle} » : boîte « {s.propres['boite']} » absente de « boites »")

    # Facultative (mainteneur, 01.11.2026) : sans bloc, aucune veille ; la veille du graphe principal existe quand
    # `dossier` est déclaré, et demande alors ses catégories et ses thèmes.
    veille = d.get("veille_arxiv") or {}
    if not isinstance(veille, dict):
        raise ConfigInvalide(f"{p.name} : veille_arxiv doit être un dictionnaire")
    if any(veille.get(c) for c in ("dossier", "themes")):
        for champ in ("dossier", "categories", "themes"):
            if not veille.get(champ):
                raise ConfigInvalide(f"{p.name} : veille_arxiv.{champ} absent ou vide")
    # Une veille par graphe (02.10.2026) : les thèmes d'un autre graphe que le
    # principal sont déclarés sous veille_arxiv.graphes.<nom>, avec leur dossier.
    # Un thème n'appartient qu'à un graphe, et deux graphes n'ont pas le même dossier.
    autres = veille.get("graphes") or {}
    if not isinstance(autres, dict):
        raise ConfigInvalide(f"{p.name} : veille_arxiv.graphes doit être un dictionnaire nom → {{dossier, themes}}")
    vus_themes, vus_dossiers = set(veille.get("themes") or []), {veille["dossier"]} if veille.get("dossier") else set()
    for nom, v in autres.items():
        if not NOM_DE_GRAPHE.match(str(nom)) or nom == GRAPHE_PRINCIPAL or not isinstance(v, dict):
            raise ConfigInvalide(f"{p.name} : veille_arxiv.graphes.{nom} mal formé")
        # `themes` peut être vide : un graphe qui n'a que des papiers choisis (jalons.choisis) ne moissonne rien.
        if set(v) - {"dossier", "themes", "dense", "categories", "graphe"} or not v.get("dossier") or "themes" not in v:
            raise ConfigInvalide(
                f"{p.name} : veille_arxiv.graphes.{nom} : « dossier », « themes », « dense », "
                f"« categories » et « graphe », rien d'autre"
            )
        # `graphe` : le graphe que la veille nourrit, s'il n'est pas sa clé (GV7, 02.11.2026 : deux veilles, un graphe fusionné).
        if "graphe" in v and (
            not isinstance(v["graphe"], str) or not NOM_DE_GRAPHE.match(v["graphe"]) or v["graphe"] == GRAPHE_PRINCIPAL
        ):
            raise ConfigInvalide(
                f"{p.name} : veille_arxiv.graphes.{nom}.graphe : un nom de graphe, autre que le principal"
            )
        if not (v.get("categories") or veille.get("categories")):
            raise ConfigInvalide(f"{p.name} : veille_arxiv.graphes.{nom} : categories absentes, ici et au-dessus")
        if v["dossier"] in vus_dossiers:
            raise ConfigInvalide(f"{p.name} : veille_arxiv.graphes.{nom} : dossier « {v['dossier']} » déjà pris")
        double = vus_themes & set(v["themes"] or [])
        if double:
            raise ConfigInvalide(f"{p.name} : thème(s) déclaré(s) dans deux graphes : {sorted(double)}")
        vus_dossiers.add(v["dossier"])
        vus_themes |= set(v["themes"] or [])

    # Les graphes : libellé et domaine (la phrase qui oriente la couche 2), par nom.
    # Facultatif ; un graphe nommé par une source sans entrée ici prend des défauts.
    graphes_decl = d.get("graphes") or {}
    if not isinstance(graphes_decl, dict):
        raise ConfigInvalide(f"{p.name} : « graphes » doit être un dictionnaire nom → {{libelle, domaine}}")
    ports = [
        v.get("cortexdb_port")
        for v in graphes_decl.values()
        if isinstance(v, dict) and v.get("cortexdb_port") is not None
    ]
    if len(ports) != len(set(ports)):
        raise ConfigInvalide(f"{p.name} : deux graphes partagent un cortexdb_port")
    for nom, v in graphes_decl.items():
        if not NOM_DE_GRAPHE.match(str(nom)) or not isinstance(v, dict):
            raise ConfigInvalide(f"{p.name} : graphes.{nom} mal formé")
        surplus = set(v) - {
            "libelle",
            "domaine",
            "plafond_couche2",
            "stock_couche2",
            "inclut",
            "accueil",
            "fiabilite",
            "reclasseur",
            "modele_pyramide",
            "canon_critique",
            "pyramide_plafond",
            "plafond_couche2_max",
            "etapes",
            "themes_auto",
            "sous_graphe_de",
            "seuil_routage",
            "cortexdb",
            "lecture_cortexdb",
            "cortexdb_port",
            "cortexdb_publie",
            "cartes_plafond",
            "couche1",
            "depots",
            "partage",
            "rubriques",
            "fraicheur_lettre_jours",
            "fraicheur_min_rubrique",
            "lettre_nom",
            "lettre_titre",
            "lettre_slogan",
            "fil",
            "arbre_fiches",
            "fiche_modele",
            "fiche_usd_jour",
        }
        if surplus:
            raise ConfigInvalide(f"{p.name} : graphes.{nom} : champs inconnus {sorted(surplus)}")
        fl = v.get("fil")  # le titre et la description du flux RSS du graphe, par langue (fil.rss)
        if fl is not None and (
            not isinstance(fl, dict)
            or not fl
            or set(fl) - {"fr", "en"}
            or not all(
                isinstance(c, dict)
                and set(c) == {"titre", "description"}
                and all(isinstance(x, str) and x for x in c.values())
                for c in fl.values()
            )
        ):
            raise ConfigInvalide(
                f"{p.name} : graphes.{nom}.fil : {{fr, en}} → {{titre, description}}, des textes non vides"
            )
        cp = v.get("cartes_plafond")
        if cp is not None and (type(cp) is not int or cp < 0):
            raise ConfigInvalide(
                f"{p.name} : graphes.{nom}.cartes_plafond : un entier positif ou nul (cartes de sujets par passage)"
            )
        sg = v.get("sous_graphe_de")
        if sg is not None and (
            sg not in graphes_decl or sg == nom or (graphes_decl.get(sg) or {}).get("sous_graphe_de")
        ):
            raise ConfigInvalide(
                f"{p.name} : graphes.{nom}.sous_graphe_de doit nommer un autre graphe déclaré, lui-même sans parent"
            )
        port = v.get("cortexdb_port")
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
            raise ConfigInvalide(f"{p.name} : graphes.{nom}.cortexdb_port : un port entre 1024 et 65535")
        for cle in ("cortexdb", "lecture_cortexdb", "cortexdb_publie", "couche1", "depots"):
            if not isinstance(v.get(cle, False), bool):
                raise ConfigInvalide(f"{p.name} : graphes.{nom}.{cle} : vrai ou faux")
        et = v.get("etapes")
        if et is not None and (
            not isinstance(et, dict) or set(et) - ETAPES or not all(isinstance(b, bool) for b in et.values())
        ):
            raise ConfigInvalide(
                f"{p.name} : graphes.{nom}.etapes : dictionnaire étape -> vrai/faux, étapes connues {sorted(ETAPES)}"
            )
        # `inclut` : ce graphe lit aussi les dossiers et les lots de couche 2 d'autres graphes
        # (un graphe financier inclut un graphe de trading quantitatif, 04.10.2026). Un seul niveau : un graphe inclus n'inclut rien.
        inclus = v.get("inclut") or []
        if not isinstance(inclus, list) or nom in inclus or any(not NOM_DE_GRAPHE.match(str(g)) for g in inclus):
            raise ConfigInvalide(f"{p.name} : graphes.{nom}.inclut doit être une liste d'autres graphes")
        # `partage` : les fiches des graphes inclus que ce graphe garde, fiche par fiche, d'après un rangement
        # (newskit.partage_graphe ; GV4 de CHANTIER_GOUVERNANCE.md, 01.11.2026). Après le déménagement de GV7
        # (mainteneur, 02.11.2026, « 1 ok mixte »), le graphe a ses sources, et le partage reste son filtre sans `inclut`.
        pt = v.get("partage")
        a_ses_sources = any(isinstance(s, dict) and s.get("graphe") == nom for s in d.get("sources") or [])
        if pt is not None and (
            not isinstance(pt, dict)
            or set(pt) != {"fichier", "garder"}
            or not all(isinstance(x, str) and x for x in pt.values())
            or not (inclus or a_ses_sources)
        ):
            raise ConfigInvalide(
                f"{p.name} : graphes.{nom}.partage : {{fichier, garder}}, deux textes, et des graphes "
                "sous `inclut` ou des sources à lui"
            )
        # `rubriques` (B16, 03.11.2026) : {fichier, ordre}, la table rubrique → sujets du graphe et l'ordre de ses rubriques.
        if v.get("fraicheur_lettre_jours") is not None and (
            not isinstance(v["fraicheur_lettre_jours"], int) or v["fraicheur_lettre_jours"] < 1
        ):
            raise ConfigInvalide(f"{p.name} : graphes.{nom}.fraicheur_lettre_jours : un entier d'au moins 1")
        if v.get("fraicheur_min_rubrique") is not None and (
            not isinstance(v["fraicheur_min_rubrique"], int)
            or isinstance(v["fraicheur_min_rubrique"], bool)
            or v["fraicheur_min_rubrique"] < 0
        ):
            raise ConfigInvalide(f"{p.name} : graphes.{nom}.fraicheur_min_rubrique : un entier positif ou nul")
        if v.get("rubriques") is not None:
            lire_rubriques(v["rubriques"], f"{p.name} : graphes.{nom}.rubriques")
    for nom, v in graphes_decl.items():
        for g in v.get("inclut") or []:
            if (graphes_decl.get(g) or {}).get("inclut"):
                raise ConfigInvalide(f"{p.name} : graphes.{nom} inclut {g}, qui inclut lui-même un graphe")

    # L'éclaireur : ses bornes, communes puis par graphe (clé `graphes`). Tout est facultatif.
    ecl = d.get("eclaireur") or {}
    if not isinstance(ecl, dict):
        raise ConfigInvalide(f"{p.name} : « eclaireur » doit être un dictionnaire de bornes")
    for nom, bornes in [("", ecl), *((g, b) for g, b in (ecl.get("graphes") or {}).items())]:
        if not isinstance(bornes, dict):
            raise ConfigInvalide(f"{p.name} : eclaireur.graphes.{nom} mal formé")
        surplus = set(bornes) - set(ECLAIREUR) - ({"graphes"} if not nom else set())
        if surplus:
            raise ConfigInvalide(
                f"{p.name} : eclaireur{'.graphes.' + nom if nom else ''} : bornes inconnues {sorted(surplus)} "
                f"(admises : {sorted(ECLAIREUR)})"
            )
        for k, v in bornes.items():
            if k != "graphes" and not isinstance(v, type(ECLAIREUR[k])):
                raise ConfigInvalide(
                    f"{p.name} : eclaireur.{k} : {v!r} n'est pas du type attendu ({type(ECLAIREUR[k]).__name__})"
                )

    # Le banc de questions : ses bornes (newskit.banc). Facultatif.
    bornes_banc = d.get("banc") or {}
    if not isinstance(bornes_banc, dict) or set(bornes_banc) - set(BANC):
        raise ConfigInvalide(f"{p.name} : « banc » : bornes admises {sorted(BANC)}")
    for k, v in bornes_banc.items():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ConfigInvalide(f"{p.name} : banc.{k} : {v!r} n'est pas un entier positif")

    # Le poseur de questions : ses bornes, communes puis par graphe (clé `graphes`). Facultatif (newskit.poseur).
    pos = d.get("poseur") or {}
    if not isinstance(pos, dict):
        raise ConfigInvalide(f"{p.name} : « poseur » doit être un dictionnaire de bornes")
    for nom, bornes in [("", pos), *((g, b) for g, b in (pos.get("graphes") or {}).items())]:
        if not isinstance(bornes, dict):
            raise ConfigInvalide(f"{p.name} : poseur.graphes.{nom} mal formé")
        surplus = set(bornes) - set(POSEUR) - ({"graphes"} if not nom else set())
        if surplus:
            raise ConfigInvalide(
                f"{p.name} : poseur{'.graphes.' + nom if nom else ''} : bornes inconnues {sorted(surplus)} "
                f"(admises : {sorted(POSEUR)})"
            )
        for k, v in bornes.items():
            if k != "graphes" and not isinstance(v, type(POSEUR[k])):
                raise ConfigInvalide(
                    f"{p.name} : poseur.{k} : {v!r} n'est pas du type attendu ({type(POSEUR[k]).__name__})"
                )

    # Le reporter (newskit.reporter, « budget ok » du mainteneur, 03.11.2026) : bornes communes puis par graphe.
    rep = d.get("reporter") or {}
    if not isinstance(rep, dict):
        raise ConfigInvalide(f"{p.name} : « reporter » doit être un dictionnaire de bornes")
    for nom, bornes in [("", rep), *((g, b) for g, b in (rep.get("graphes") or {}).items())]:
        if not isinstance(bornes, dict):
            raise ConfigInvalide(f"{p.name} : reporter.graphes.{nom} mal formé")
        surplus = set(bornes) - set(REPORTER) - ({"graphes"} if not nom else set())
        if surplus:
            raise ConfigInvalide(
                f"{p.name} : reporter{'.graphes.' + nom if nom else ''} : bornes inconnues "
                f"{sorted(surplus)} (admises : {sorted(REPORTER)})"
            )
        for k, v in bornes.items():
            if k == "domaines_ecartes":
                if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                    raise ConfigInvalide(f"{p.name} : reporter.domaines_ecartes : une liste de domaines")
            elif k == "verser":
                if not isinstance(v, bool):
                    raise ConfigInvalide(f"{p.name} : reporter.verser : {v!r} n'est pas true ou false")
            elif k != "graphes" and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
                raise ConfigInvalide(f"{p.name} : reporter.{k} : {v!r} n'est pas un nombre positif")

    dom = d.get("domaines")
    if dom is not None and (
        not isinstance(dom, dict)
        or set(dom) - {"substack", "medium"}
        or not (
            isinstance(dom.get("substack", {}), dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in dom.get("substack", {}).items())
        )
        or not (isinstance(dom.get("medium", []), list) and all(isinstance(x, str) for x in dom.get("medium", [])))
    ):
        raise ConfigInvalide(
            f"{p.name} : domaines : substack (domaine → nom de la publication) et medium (liste de domaines)"
        )
    an = d.get("anciens_noms")
    if an is not None and (
        not isinstance(an, dict)
        or not all(
            isinstance(k, str) and isinstance(v, str) and NOM_DE_GRAPHE.match(k) and NOM_DE_GRAPHE.match(v) and k != v
            for k, v in an.items()
        )
    ):
        raise ConfigInvalide(
            f"{p.name} : anciens_noms : un dictionnaire ancien nom → nom actuel, deux noms de graphe distincts"
        )
    rv = d.get("revendique")
    if rv is not None and (
        not isinstance(rv, dict)
        or set(rv) - {"plateformes", "domaines"}
        or not all(isinstance(v, list) and all(isinstance(x, str) and x for x in v) for v in rv.values())
    ):
        raise ConfigInvalide(f"{p.name} : revendique : plateformes et domaines, deux listes de textes")
    inc = d.get("incidents")
    if inc is not None and (not isinstance(inc, list) or not all(isinstance(x, str) and x for x in inc)):
        raise ConfigInvalide(f"{p.name} : incidents : une liste de préfixes domaine/chemin")
    ids = d.get("identifiants")
    if ids is not None:
        if not isinstance(ids, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in ids.items()):
            raise ConfigInvalide(f"{p.name} : identifiants : domaine → expression à un groupe")
        for k, v in ids.items():
            try:
                groupes = re.compile(v).groups
            except re.error as e:
                raise ConfigInvalide(f"{p.name} : identifiants.{k} : expression invalide ({e})") from e
            if groupes != 1:
                raise ConfigInvalide(f"{p.name} : identifiants.{k} : un groupe, l'identifiant de la page")
    ed = d.get("edition")
    if ed is not None:
        if not isinstance(ed, dict) or set(ed) - set(EDITION) or not all(isinstance(v, str) for v in ed.values()):
            raise ConfigInvalide(f"{p.name} : edition : un dictionnaire de textes, clés connues {sorted(EDITION)}")
        for cle in ("nom", "principal"):
            if cle in ed and not NOM_DE_GRAPHE.match(ed[cle]):
                raise ConfigInvalide(f"{p.name} : edition.{cle} : minuscules, chiffres et tirets")
    return {
        "sources": sources,
        "veille_arxiv": veille,
        "credences": d.get("credences") or {},
        "boites": boites,
        "graphes": graphes_decl,
        "eclaireur": ecl,
        "banc": bornes_banc,
        "poseur": pos,
        "reporter": rep,
        "edition": ed or {},
        "revendique": rv or {},
        "identifiants": ids or {},
        "incidents": inc or [],
    }


# --- Accès -----------------------------------------------------------------


def toutes(
    plateforme: str | None = None, abonnement: str | None = None, dense: bool | None = None, graphe: str | None = None
) -> list[Source]:
    out = charger()["sources"]
    if plateforme:
        out = [s for s in out if s.plateforme == plateforme]
    if abonnement:
        out = [s for s in out if s.abonnement == abonnement]
    if dense is not None:
        out = [s for s in out if s.dense is dense]
    if graphe:
        out = [s for s in out if s.graphe == graphe]
    return out


def graphes() -> list[str]:
    """Les graphes déclarés, le principal en tête, puis par ordre alphabétique."""
    conf = charger()
    noms = (
        {s.graphe for s in conf["sources"]}
        | {w.get("graphe") or n for n, w in (conf["veille_arxiv"].get("graphes") or {}).items()}
        | {GRAPHE_PRINCIPAL}
    )
    # Un graphe qui reprend d'autres graphes existe sans source à lui (avant le transfert de leurs sources).
    noms |= {g for g, v in (conf.get("graphes") or {}).items() if isinstance(v, dict) and v.get("inclut")}
    return [GRAPHE_PRINCIPAL] + sorted(noms - {GRAPHE_PRINCIPAL})


# Les bornes de l'éclaireur et leurs défauts (newskit.eclaireur). Le registre les surcharge.
ECLAIREUR = {"actif": True, "recherches": 5, "pages": 10, "mots_min": 200, "themes": 2, "lacunes": 2, "delai": 600}


def eclaireur(nom: str) -> dict:
    """Les bornes de l'éclaireur pour un graphe : défauts, puis clé `eclaireur` du registre, puis sa surcharge."""
    ecl = charger().get("eclaireur") or {}
    return {
        **ECLAIREUR,
        **{k: v for k, v in ecl.items() if k != "graphes"},
        **((ecl.get("graphes") or {}).get(nom) or {}),
    }


# Les bornes du poseur de questions et leurs défauts (newskit.poseur).
POSEUR = {"actif": True, "questions": 12, "sujets": 3, "reposer": 12, "delai": 900}


def poseur(nom: str) -> dict:
    """Les bornes du poseur pour un graphe : défauts, puis clé `poseur` du registre, puis sa surcharge."""
    pos = charger().get("poseur") or {}
    return {**POSEUR, **{k: v for k, v in pos.items() if k != "graphes"}, **((pos.get("graphes") or {}).get(nom) or {})}


# Les bornes du reporter et leurs défauts (newskit.reporter) : par enquête 12 recherches, 12 pages et 3 $ au plus,
# reprises et coût déjà payé compris (mainteneur, 03.11.2026 : « plafond 3$ ») ; 10 $ par lettre, toutes ses enquêtes
# comprises (« 10$ max par lettre »).
# `domaines_ecartes` : sites de classement et agrégateurs de produits, qui ne prouvent rien sur une entreprise ; ni lus,
# ni sources d'un éclairage (relecture de Berg en Dal, 03.11.2026).
REPORTER = {
    "recherches": 12,
    "pages": 12,
    "usd_enquete": 3.0,
    "usd_lettre": 15.0,
    "domaines_ecartes": [],
    "verser": True,  # verser au graphe les sources nouvelles et les lacunes d'une enquête finie (03.11.2026)
    # L'angle critique de chaque billet (newskit.angle_critique, mainteneur, 03.11.2026, « ok ») : 0,40 $ par
    # billet, 5 $ par lettre, 2 recherches et 2 pages par billet ; le plafond de la lettre passe à 15 $.
    "angle_usd_billet": 0.40,
    "angle_usd_lettre": 5.0,
    "angle_recherches": 2,
    "angle_pages": 2,
    # La pile du fil (fil.cadence) : l'heure de la routine du graphe et sa durée prévue, en minutes.
    "fil_routine_heure": "05:00",
    "fil_routine_minutes": 150,
}


def revendique() -> dict[str, set[str]]:
    """Les sources où les auteurs présentent leurs propres résultats, sans relecture par des pairs (bloc `revendique:`
    du registre) : leurs plateformes et leurs domaines (mainteneur, 03.11.2026)."""
    rv = charger().get("revendique") or {}
    return {"plateformes": set(rv.get("plateformes") or []), "domaines": {x.lower() for x in rv.get("domaines") or []}}


def incidents() -> list[str]:
    """Les préfixes (domaine/chemin) des pages d'incident, fiches de registre (bloc `incidents:`, 03.11.2026)."""
    return [x.lower().removeprefix("www.").rstrip("/") for x in charger().get("incidents") or []]


def identifiants() -> dict[str, str]:
    """L'identifiant canonique d'une page, par domaine (bloc `identifiants:` du registre) : une expression à un groupe
    sur l'adresse ; deux adresses du domaine au même identifiant sont la même page (03.11.2026)."""
    return dict(charger().get("identifiants") or {})


def reporter(nom: str) -> dict:
    """Les bornes du reporter pour un graphe : défauts, puis clé `reporter` du registre, puis sa surcharge."""
    rep = charger().get("reporter") or {}
    return {
        **REPORTER,
        **{k: v for k, v in rep.items() if k != "graphes"},
        **((rep.get("graphes") or {}).get(nom) or {}),
    }


# Les bornes du banc de questions et leurs défauts (newskit.banc).
BANC = {
    "questions": 50,
    "externes": 10,
    "fonctionnement": 30,
    "par_lot": 5,
    "delai": 900,
    "renouveler_jours": 0,
    "agnostiques": 0,
    "restitution": 40,
    # Lecture locale des pages (09.10.2026, décision du porteur) : le modèle cherche, nous lisons (lecture_web.py),
    # chaque citation est vérifiée mot pour mot dans la page. false : le modèle cherche et lit lui-même (WebFetch).
    "lecture_locale": True,
}


def banc() -> dict:
    return {**BANC, **(charger().get("banc") or {})}


def graphes_inclus(nom: str) -> list[str]:
    """Les graphes dont `nom` reprend les dossiers et les lots de couche 2 (clé `inclut`)."""
    return list((charger().get("graphes", {}).get(nom) or {}).get("inclut") or [])


def annonce_a_l_accueil(nom: str) -> bool:
    """Un graphe servi est annoncé sur la page d'accueil, sauf `accueil: false` au registre : il reste
    alors accessible par le serveur MCP sans figurer sur la page (un graphe de trading quantitatif, 05.10.2026)."""
    return ((charger().get("graphes") or {}).get(nom) or {}).get("accueil", True) is not False


def mesures_a_l_accueil(nom: str) -> bool:
    """Les mesures d'un graphe annoncé figurent dans l'onglet Fiabilité de la page d'accueil, sauf `fiabilite: false`
    au registre : le graphe reste dans l'onglet Graphes, ses tableaux de fiabilité attendent (porteur, 10.10.2026 :
    un graphe de conformité seul, tant que les autres bancs sont de l'ancienne méthode et leurs routines en pause)."""
    return ((charger().get("graphes") or {}).get(nom) or {}).get("fiabilite", True) is not False


def ordre_d_affichage() -> list[str]:
    """Les graphes dans l'ordre de leur déclaration sous `graphes:` — celui des daemons et de la
    page d'accueil ; un graphe sans déclaration vient ensuite, par ordre alphabétique."""
    decl = charger().get("graphes") or {}
    declares = [g for g in decl if g in graphes()]
    # Un sous-graphe (`sous_graphe_de`) suit son parent, quel que soit son rang de déclaration (09.10.2026).
    racines = [g for g in declares if not (decl[g] or {}).get("sous_graphe_de")]
    ordonnes = []
    for g in racines:
        ordonnes.append(g)
        ordonnes += [h for h in declares if (decl[h] or {}).get("sous_graphe_de") == g]
    ordonnes += [g for g in declares if g not in ordonnes]
    return ordonnes + sorted(set(graphes()) - set(ordonnes))


def parent_de(nom: str) -> str | None:
    """Le graphe dont `nom` est déclaré sous-graphe, ou None."""
    return ((charger().get("graphes") or {}).get(nom) or {}).get("sous_graphe_de")


def dossiers_propres_au_domaine(nom: str) -> list[str]:
    """Les dossiers de fiches dont la source se tient dans le domaine du graphe : toutes celles du graphe, moins les sources
    de presse généraliste (`posture: presse`) et les sources entrées d'elles-mêmes par l'éclaireur (`auto_*`, sans posture
    déclarée : The Register, Al Jazeera…), moins les dossiers hérités d'un autre graphe (ai_news/… réattribués le
    08.10.2026). Sert au filtrage de pertinence de la file rare de la pyramide (porteur, 10.10.2026, étage 1 : par la
    provenance, sans modèle). Mesuré : « Shai-Hulud » et « Tensorlake SDK » venaient d'un billet de The Register."""
    exclus = set()
    for s in toutes(graphe=nom):
        posture = (getattr(s, "posture", "") or "").strip()
        plateforme = (getattr(s, "plateforme", "") or "").lower()
        if (
            posture == "presse"
            or not posture
            or s.dossier.split("/")[-1].startswith("auto_")
            or s.dossier.startswith("ai_news/")
            or plateforme in ("openalex", "arxiv")
        ):  # moissons par mots-clés : le sujet n'y est pas choisi une à une
            exclus.add(f"sources/{s.dossier}")
    # Les dossiers sans source déclarée (veille arXiv, recherche/) sont des moissons aussi : hors de la file rare.
    return [
        d
        for d in dossiers_du_graphe(nom)
        if d not in exclus and not d.startswith(("sources/ai_news/", "sources/arxiv", "recherche"))
    ]


def dossiers_du_graphe(nom: str, propres: bool = False) -> list[str]:
    """Les dossiers de fiches qui nourrissent un graphe, relatifs à la racine.

    Avec ceux des graphes qu'il inclut, sauf `propres=True`.

    Le graphe principal lit aussi `recherche/` — les livrables de veille et
    le who-is-who — et `sources/arxiv`, où vont les papiers qu'il cite. Un
    autre graphe ne lit que les dossiers de ses sources : ses citations
    arXiv restent des nœuds cités, non lus, et ne tirent aucune fiche. S'il a
    sa propre veille arXiv (veille_arxiv.graphes), il lit aussi son dossier.
    """
    if nom not in graphes():
        raise ConfigInvalide(f"graphe inconnu : {nom} (déclarés : {', '.join(graphes())})")
    dossiers = [f"sources/{s.dossier}" for s in toutes(graphe=nom)]
    if nom == GRAPHE_PRINCIPAL:
        veille = charger()["veille_arxiv"]
        arxiv = f"sources/{veille['dossier']}" if veille.get("dossier") else None
        if arxiv and arxiv not in dossiers:
            dossiers.append(arxiv)
        dossiers.append("recherche")
    else:
        for n, v in sorted((charger()["veille_arxiv"].get("graphes") or {}).items()):
            if (v.get("graphe") or n) == nom and f"sources/{v['dossier']}" not in dossiers:
                dossiers.append(f"sources/{v['dossier']}")
    if not propres:
        for g in graphes_inclus(nom):
            dossiers += [d for d in dossiers_du_graphe(g, propres=True) if d not in dossiers]
    return dossiers


def chemins_du_graphe(nom: str) -> dict[str, Path]:
    """Où un graphe écrit : squelette, graphe, statistiques, carte, lots de couche 2,
    structure des titres, rapport de routine et nouveautés du dernier passage.

    Le principal garde ses chemins historiques (ROUTINE.md à la racine, structure
    dans graphify-out/) ; un autre graphe a tout sous graphe/<nom>/.
    """
    principal = nom == GRAPHE_PRINCIPAL
    base = RACINE / "graphe" if principal else RACINE / "graphe" / nom
    return {
        "base": base,
        "squelette": base / "squelette.json",
        "graphe": base / "graphe.json",
        "stats": base / "graphe_stats.md",
        "carte": base / "carte.md",
        "couche2": base / "couche2",
        "structure": (RACINE / "graphify-out" / ".graphify_ast.json") if principal else base / "structure.json",
        "rapport": (RACINE / "ROUTINE.md") if principal else base / "ROUTINE.md",
        "nouveautes": base / "nouveautes.json",
        "muettes_md": base / "sources_muettes.md",
        "muettes_json": base / "sources_muettes.json",
    }


# Les étapes de la routine qu'un graphe peut arrêter ou remettre en service une à une, sans toucher au code ni aux
# daemons (porteur, 08.10.2026 : « architecture granulaire ») : `graphes.<nom>.etapes: {poseur: false, banc: false}`.
# Une étape absente est active. Toutes appellent le modèle, sauf la composition, qui est toujours faite.
ETAPES = {
    "collecte",
    "canon",
    "eclaireur",
    "poseur",
    "couche2",
    "alias",
    "themes",
    "avance",
    "pyramide",
    "cartes",
    "banc",
    "repose",
    "index",
}


def etape_active(nom: str, etape: str) -> bool:
    """Vrai si l'étape tourne pour ce graphe (clé `etapes` du registre ; absente = active). `eclaireur` et `poseur`
    gardent aussi leur propre `actif` dans leurs blocs, plus ancien : les deux sont honorés."""
    if etape not in ETAPES:
        raise ValueError(f"étape inconnue : {etape}")
    return bool((charger().get("graphes", {}).get(nom) or {}).get("etapes", {}).get(etape, True))


def verse_cortexdb(nom: str) -> bool:
    """Vrai si le graphe a son cerveau CortexDB, reconstruit à chaque passage (`graphes.<nom>.cortexdb: true` ; absent =
    non : CortexDB est la base du moteur depuis le 22.10.2026, les graphes y passent un à un, mesure à l'appui)."""
    return bool((charger().get("graphes", {}).get(nom) or {}).get("cortexdb", False))


def port_cortexdb(nom: str) -> int:
    """Le port fixe du serveur CortexDB permanent du graphe (`graphes.<nom>.cortexdb_port`), que les droits de Thalamus
    nomment ; 0 sans port déclaré (un port libre au démarrage)."""
    return int((charger().get("graphes", {}).get(nom) or {}).get("cortexdb_port") or 0)


def lit_cortexdb(nom: str) -> bool:
    """Vrai si les passages d'une réponse se lisent dans le cerveau CortexDB du graphe (`graphes.<nom>.lecture_cortexdb:
    true` ; absent = non). Distinct de `cortexdb` : un cerveau se construit d'abord, se mesure, puis se lit."""
    return bool((charger().get("graphes", {}).get(nom) or {}).get("lecture_cortexdb", False))


def graphe_declare(nom: str) -> dict:
    """Libellé, domaine et plafond de couche 2 d'un graphe, avec des défauts."""
    d = dict(charger().get("graphes", {}).get(nom) or {})
    d.setdefault("libelle", nom)
    d.setdefault("domaine", (edition()["domaine_principal"] or nom) if nom == GRAPHE_PRINCIPAL else nom)
    d.setdefault("plafond_couche2", 6)
    return d


def dossiers_de_moindre_autorite() -> list[str]:
    """Les dossiers (« sources/… ») des sources marquées autorite: moindre."""
    return [f"sources/{s.dossier}" for s in charger()["sources"] if s.autorite == "moindre"]


def une(cle: str) -> Source:
    for s in charger()["sources"]:
        if s.cle == cle:
            return s
    raise ConfigInvalide(f"source « {cle} » absente du registre")


def veille_arxiv() -> dict:
    return charger()["veille_arxiv"]


def veilles_arxiv() -> list[dict]:
    """Une veille arXiv par graphe, le principal en tête : {graphe, dossier, themes, dense, categories}."""
    v = charger()["veille_arxiv"]
    out = (
        [
            {
                "graphe": GRAPHE_PRINCIPAL,
                "dossier": v["dossier"],
                "themes": list(v["themes"]),
                "dense": bool(v.get("dense")),
                "categories": list(v["categories"]),
            }
        ]
        if v.get("dossier")
        else []
    )
    for nom, w in sorted((v.get("graphes") or {}).items()):
        # Un graphe peut suivre d'autres catégories que le principal (cs.CY pour l'éthique).
        out.append(
            {
                "graphe": w.get("graphe") or nom,
                "dossier": w["dossier"],
                "themes": list(w["themes"] or []),
                "dense": bool(w.get("dense")),
                "categories": list(w.get("categories") or v["categories"]),
            }
        )
    return out


def a_une_veille(graphe: str) -> bool:
    """Le graphe a une veille arXiv à moissonner : déclarée au registre, avec au moins un thème."""
    return any(v["graphe"] == graphe and v["themes"] for v in veilles_arxiv())


def dossiers_denses(graphe: str | None = None) -> list[str]:
    """Chemins relatifs des dossiers qui nourrissent la couche 2 d'un graphe.

    Sans nom : ceux du graphe principal, comme avant le 27.09.2026 — les
    appelants historiques (structure des titres, routine) ne visent que lui.
    """
    nom = graphe or GRAPHE_PRINCIPAL
    dossiers = [f"sources/{s.dossier}" for s in toutes(dense=True, graphe=nom)]
    # Les résumés arXiv d'un graphe entrent en couche 2 si sa veille le dit (`dense: true`,
    # 02.10.2026) : sans cela un papier n'est atteint que par son titre et ses auteurs.
    for v in veilles_arxiv():
        if v["graphe"] == nom and v["dense"]:
            dossiers.append(f"sources/{v['dossier']}")
    return dossiers


# --- Crédences -------------------------------------------------------------


def _service(nom: str) -> tuple[str, str]:
    c = charger()["credences"]
    sess = (c.get("sessions") or {}).get(nom)
    if not sess or not sess.get("service"):
        raise ConfigInvalide(f"session « {nom} » : champ service absent du registre")
    compte = (c.get("trousseau") or {}).get("compte", edition()["nom"])
    return compte, sess["service"]


DOSSIER_SESSIONS = Path.home() / ".secrets" / "sessions"


def fichier_de_session(nom: str) -> Path | None:
    """Le fichier d'une session que le registre autorise à vivre hors du trousseau, sinon None.

    La règle reste « au trousseau » : un cookie d'abonnement payant n'a rien à
    faire dans un fichier. Mais un daemon ne lit pas le trousseau de la session
    à l'écran, et une session ouverte par un script (`ouvrir_session.py`) doit
    bien être rangée quelque part où la routine la retrouve sans personne. Une
    session qui le déclare (`fichier: true`, décision de l'utilisateur du
    02.10.2026 pour AI Secret, compte gratuit) vit donc dans
    `~/.secrets/sessions/<nom>`, droits 600, hors du dépôt.
    """
    s = (charger()["credences"].get("sessions") or {}).get(nom) or {}
    return DOSSIER_SESSIONS / nom if s.get("fichier") else None


def fichier_de_session_ou_rien(nom: str) -> Path | None:
    """Comme `fichier_de_session`, mais None aussi pour une session que le registre ne déclare pas."""
    if nom not in (charger()["credences"].get("sessions") or {}):
        return None
    return fichier_de_session(nom)


def ecrire_session_fichier(nom: str, valeur: str) -> Path:
    """Range une session dans son fichier (600, dossier 700). Refuse une session qui ne le déclare pas."""
    chemin = fichier_de_session(nom)
    if chemin is None:
        raise ConfigInvalide(f"session « {nom} » : le registre ne l'autorise pas en fichier (fichier: true)")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(chemin.parent, 0o700)
    tmp = chemin.with_name(chemin.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(valeur.strip() + "\n")
    os.replace(tmp, chemin)
    return chemin


def _session_en_fichier(nom: str) -> str | None:
    chemin = fichier_de_session(nom)
    if chemin is None or not chemin.exists():
        return None
    return chemin.read_text(encoding="utf-8").strip() or None


def etat_session(nom: str) -> str:
    """« absente », « vide » ou « deposee ». Trois états, pas deux.

    Le cas « vide » a été rencontré au premier dépôt : `security
    add-generic-password … -w` demande la valeur à l'écran, et lancé sans
    terminal réel il lit une chaîne vide sans se plaindre. L'entrée existe
    alors, et la lecture rend un code de retour 0 avec zéro caractère. Confondre
    cet état avec une absence ferait dire au tableau « session déposée » pendant
    que le collecteur ne trouve rien : exactement la divergence que ce projet
    refuse ailleurs.
    """
    compte, service = _service(nom)
    if _session_en_fichier(nom):
        return "deposee"
    if (
        subprocess.run(
            ["security", "find-generic-password", "-a", compte, "-s", service], capture_output=True, text=True
        ).returncode
        != 0
    ):
        return "absente"
    r = subprocess.run(
        ["security", "find-generic-password", "-a", compte, "-s", service, "-w"], capture_output=True, text=True
    )
    if r.returncode != 0:
        return "illisible"
    return "deposee" if r.stdout.strip() else "vide"


def session_declaree(nom: str) -> bool:
    """Une session utilisable, c'est-à-dire présente **et** non vide."""
    return etat_session(nom) == "deposee"


def session(nom: str) -> str | None:
    """Valeur de la session, ou None si elle n'est pas déposée.

    Ordre de résolution : variable d'environnement pour un essai ponctuel, puis
    le fichier d'une session qui le déclare (`fichier_de_session`), puis le
    trousseau macOS. La valeur retournée est un en-tête `cookie` complet. Elle
    ne doit jamais être écrite ailleurs que dans l'en-tête d'une requête : ni
    journal, ni rapport, ni fichier autre que celui-là.
    """
    par_env = os.environ.get("SE_SESSION_" + nom.upper())
    if par_env:
        return par_env.strip()
    en_fichier = _session_en_fichier(nom)
    if en_fichier:
        return en_fichier
    compte, service = _service(nom)
    r = subprocess.run(
        ["security", "find-generic-password", "-a", compte, "-s", service, "-w"], capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    v = r.stdout.strip()
    return v or None


def service_boite(nom: str) -> str:
    """L'entrée du trousseau qui porte le mot de passe d'une boîte : `<edition>:imap:<boite>`, le nom de l'édition
    lu au registre (`edition.nom`)."""
    return f"{edition()['nom']}:imap:{nom}"


def boite(nom: str) -> dict:
    """La déclaration d'une boîte (hôte, port, utilisateur, dossier), sans son mot de passe."""
    return dict((charger().get("boites") or {})[nom])


TROUSSEAU_ABSENT = "boîte non configurée : mot de passe absent du trousseau"
TROUSSEAU_VERROUILLE = "trousseau verrouillé (security unlock-keychain)"
CODE_ABSENT = 44  # errSecItemNotFound, tronqué par `security`
CODES_VERROUILLE = (36, 51)  # errSecInteractionNotAllowed (-25308) et errSecAuthFailed, tronqués


def lire_mot_de_passe_boite(nom: str) -> tuple[str | None, str]:
    """(mot de passe, "") lu dans le trousseau macOS, sinon (None, raison) : entrée absente, trousseau verrouillé ou
    interaction refusée, autre code de `security`. La raison ne recopie jamais une sortie de la commande ; la valeur ne
    sert qu'à la connexion IMAP : ni journal, ni fiche, ni manifeste."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service_boite(nom), "-w"], capture_output=True, text=True
        )
    except OSError:
        return None, "lecture du trousseau refusée (commande security introuvable)"
    if r.returncode == 0:
        v = r.stdout.strip()
        return (v, "") if v else (None, TROUSSEAU_ABSENT)
    if r.returncode == CODE_ABSENT:
        return None, TROUSSEAU_ABSENT
    if r.returncode in CODES_VERROUILLE or "user interaction is not allowed" in (r.stderr or "").lower():
        return None, TROUSSEAU_VERROUILLE
    return None, f"lecture du trousseau refusée (code {r.returncode})"


def mot_de_passe_boite(nom: str) -> str | None:
    """Le mot de passe d'application d'une boîte, sinon None (lire_mot_de_passe_boite dit pourquoi)."""
    return lire_mot_de_passe_boite(nom)[0]


def utilisateur_boite(b: dict) -> str | None:
    """L'utilisateur IMAP d'une boîte déclarée : `utilisateur` tel qu'écrit, sinon la valeur de la variable
    `utilisateur_env`, lue dans l'environnement puis dans le fichier hors dépôt (commun.variable_hors_depot) ; None
    si elle manque."""
    if b.get("utilisateur"):
        return str(b["utilisateur"]).strip() or None
    if not b.get("utilisateur_env"):
        return None
    return commun.variable_hors_depot(str(b["utilisateur_env"])) or None


def boite_configuree(nom: str) -> bool:
    """Un mot de passe au trousseau et un utilisateur résolu."""
    return mot_de_passe_boite(nom) is not None and utilisateur_boite(boite(nom)) is not None


def sonde(nom: str) -> tuple[str, str]:
    """URL d'épreuve et marqueur attendu d'une page authentifiée."""
    s = (charger()["credences"].get("sessions") or {}).get(nom) or {}
    return s.get("sonde", ""), s.get("marqueur", "")


def marqueur_absent_sonde(nom: str) -> str:
    """Texte qui ne doit PAS figurer dans la réponse authentifiée, "" si non déclaré.

    Certains sites répondent 200 avec la même page, connecté ou non, et seule
    la présence de l'appel à devenir membre distingue les deux (Ghost).
    """
    s = (charger()["credences"].get("sessions") or {}).get(nom) or {}
    return str(s.get("marqueur_absent") or "")


def taille_min_sonde(nom: str) -> int:
    """Taille minimale de la réponse authentifiée, 0 si non déclarée.

    Pour un billet payant, la seule preuve qu'une session ouvre l'accès est
    que le corps rendu est entier : le même billet, anonyme, revient tronqué
    avec le même code 200. Une taille suffit à distinguer les deux.
    """
    s = (charger()["credences"].get("sessions") or {}).get(nom) or {}
    return int(s.get("taille_min", 0) or 0)


def commande_de_depot(nom: str, presse_papier: bool = False) -> str:
    """La commande de dépôt d'une session.

    Deux formes, parce que la première a un piège. Sans argument après `-w`,
    `security` demande la valeur à l'écran : c'est la forme propre, mais elle
    exige un terminal réel, et lancée sans terminal elle enregistre une valeur
    vide sans le dire. La seconde forme lit le presse-papiers, ce qui marche
    partout et ne fait pas passer la valeur par l'historique du shell.
    """
    # Depuis le 27.09.2026, le dépôt passe par newskit.deposer_session : il
    # déverrouille le trousseau, lit sans le plafond de 1 024 caractères du
    # terminal, contrôle la valeur et remplace l'entrée sans `-U` (qui exige une
    # confirmation graphique). Les deux formes `security` restent documentées
    # dans ce script ; elles ne sont plus proposées ici.
    _service(nom)  # lève si la session n'est pas déclarée
    return f"python3 -m newskit.deposer_session {nom}"


def verdict_de_reponse(
    url: str, url_finale: str, corps: str, marqueur: str, taille_min: int = 0, marqueur_absent: str = ""
) -> str:
    """Ce qu'une réponse 200 dit de la session. Sans réseau, donc testable.

    Un site qui renvoie vers sa page de connexion répond 200 avec le corps de
    cette page : ce n'est pas une session, c'est son absence. Le 27.09.2026,
    Medium a été déclaré « authentifié » sur une page de connexion, parce que
    le marqueur figurait dans l'adresse de retour que cette page recopie. La
    redirection est donc lue avant le marqueur.
    """
    if url_finale and url_finale != url:
        chemin = url_finale.lower()
        if any(m in chemin for m in ("signin", "sign-in", "login", "log-in", "/auth")):
            return "répond, mais pas authentifiée : redirigée vers la page de connexion"
    if marqueur and marqueur not in corps:
        return "répond, mais pas authentifiée"
    if marqueur_absent and marqueur_absent.lower() in corps.lower():
        return f"répond, mais pas authentifiée : la page dit encore « {marqueur_absent} »"
    if taille_min and len(corps) < taille_min:
        return f"répond, mais pas authentifiée : {len(corps)} octets, un billet entier en fait au moins {taille_min}"
    return "authentifiée"


def eprouver(nom: str) -> dict:
    """Une requête authentifiée, et ce qu'elle rend. Aucune valeur de session n'est affichée.

    C'est la seule manière honnête de répondre à « est-ce que ma session
    suffit ? » : la poser à la publication plutôt que d'en décider d'avance.
    """
    import urllib.error
    import urllib.request

    url, marqueur = sonde(nom)
    if not url:
        return {"nom": nom, "verdict": "pas de sonde déclarée"}
    etat = etat_session(nom)
    if etat != "deposee":
        return {
            "nom": nom,
            "verdict": {
                "absente": "session absente du trousseau",
                "vide": "entrée présente mais vide : la saisie interactive n'a rien reçu",
                "illisible": "entrée présente mais refusée à la lecture (droits du trousseau)",
            }[etat],
        }
    v = session(nom)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_6) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept": "text/html,application/json;q=0.9",
            "Accept-Language": "en",
            "Cookie": v,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            corps = r.read(400_000).decode("utf-8", "replace")
            return {
                "nom": nom,
                "code": r.status,
                "url_finale": r.url,
                "taille": len(corps),
                "marqueur": (marqueur in corps) if marqueur else None,
                "verdict": verdict_de_reponse(
                    url, r.url, corps, marqueur, taille_min_sonde(nom), marqueur_absent_sonde(nom)
                ),
            }
    except urllib.error.HTTPError as e:
        # Le corps d'une erreur dit souvent pourquoi (Substack répond en JSON) ;
        # il vient du serveur, pas de la requête, donc rien de secret n'y passe.
        try:
            explication = e.read(300).decode("utf-8", "replace").strip()
        except Exception:
            explication = ""
        verdict = f"refus HTTP {e.code} ({e.reason})"
        if explication:
            verdict += f" — le serveur dit : {explication[:200]}"
        return {"nom": nom, "code": e.code, "verdict": verdict}
    except Exception as e:
        return {"nom": nom, "verdict": f"échec réseau : {e}"}


# --- CLI -------------------------------------------------------------------


def cmd_verifier() -> int:
    """Éprouve chaque session déclarée. Code de sortie non nul si l'une échoue."""
    noms = [s for s in charger()["sources"] if s.session]
    if not noms:
        print("Aucune session déclarée dans le registre.")
        return 0
    mauvais = 0
    for s in noms:
        r = eprouver(s.session)
        ok = r.get("verdict") == "authentifiée"
        mauvais += 0 if ok else 1
        print(f"{'ok  ' if ok else 'ÉCHEC'}  {s.nom}")
        print(f"        session « {s.session} » : {r['verdict']}")
        if "code" in r:
            detail = f"        HTTP {r['code']}"
            if r.get("taille"):
                detail += f", {r['taille']} octets"
            if r.get("url_finale") and r["url_finale"] != sonde(s.session)[0]:
                detail += f", redirigée vers {r['url_finale'][:90]}"
            print(detail)
        if r.get("verdict", "").startswith(("session absente", "entrée présente")):
            print(f"        La déposer, valeur demandée à l'écran :\n          {commande_de_depot(s.session)}")
    return 1 if mauvais else 0


def main() -> int:
    try:
        conf = charger()
    except ConfigInvalide as e:
        print(f"  registre invalide : {e}", file=sys.stderr)
        return 1
    if len(sys.argv) > 1 and sys.argv[1] == "verifier":
        return cmd_verifier()
    if len(sys.argv) > 1 and sys.argv[1] not in ("etat",):
        print(f"  sous-commande inconnue : {sys.argv[1]} (etat | verifier)", file=sys.stderr)
        return 2
    print(f"Registre : {FICHIER.relative_to(RACINE)}\n")
    print(f"{'clé':<28}{'plateforme':<12}{'abon.':<9}{'fiches':>7}  état")
    for s in conf["sources"]:
        n = len([f for f in s.chemin.glob("*.md")]) if s.chemin.exists() else 0
        etat = []
        if s.dense:
            etat.append("dense")
        if s.assistee:
            etat.append("assistée")
        if s.session:
            etat.append(
                "session "
                + {"deposee": "déposée", "absente": "ABSENTE", "vide": "VIDE", "illisible": "ILLISIBLE"}[
                    etat_session(s.session)
                ]
            )
        elif s.payant:
            etat.append("payant sans session")
        print(f"{s.cle:<28}{s.plateforme:<12}{s.abonnement:<9}{n:>7}  {', '.join(etat)}")
    v = conf["veille_arxiv"]
    print(
        f"\nVeille arXiv : {len(v.get('categories') or [])} catégories, {len(v.get('themes') or [])} thèmes"
        + "".join(f" ; {nom} : {len(w['themes'])} thèmes" for nom, w in sorted((v.get("graphes") or {}).items()))
    )
    for s in conf["sources"]:
        if not s.session:
            continue
        e = etat_session(s.session)
        if e == "deposee":
            continue
        if e == "vide":
            print(
                f"\n  Session de {s.nom} : entrée présente mais vide, la saisie "
                f"interactive n'ayant rien reçu. Les deux commandes ci-dessous "
                f"mettent l'entrée à jour."
            )
        else:
            print(f"\n  Session {e} pour {s.nom}. Pour la déposer :")
        print(
            f"    dans un terminal réel (le script déverrouille le trousseau et contrôle la valeur) :\n"
            f"      {commande_de_depot(s.session)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

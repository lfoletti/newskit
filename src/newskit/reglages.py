"""Les réglages propres au média (mainteneur, 11.11.2026 : « la variable FIL_MAX ne devrait pas passer par newskit, mais
se faire en corrigeant le fichier config. c'est la même remarque pour toutes les variables "média-spécifique" »).

Le moteur garde chaque valeur par défaut, en constante de son module. Le bloc `reglages:` du config.yaml du média la
remplace, rangé par module, le nom en minuscules :

    reglages:
      fil: {pile_delai: 10, jour_garanti: [6, 23]}
      lettre: {faits_max: 7, point: "#b4560f"}

Seules les constantes de REGLABLES se règlent. Le réglage garde le type du défaut : un entier pour un entier, un nombre
pour un nombre à virgule, un texte pour un texte, une liste de même longueur pour un tuple. Un nom inconnu ou un type
différent lève ConfigMediaInvalide au chargement du config.yaml (media.charger), qui nomme le réglage.

Chaque module de REGLABLES finit par `reglages.appliquer(__name__, globals())`. Un réglable ne se lit donc que dans le
corps d'une fonction : une valeur par défaut d'argument se figerait à l'import (tests/test_reglages.py le vérifie).
"""

from __future__ import annotations

from newskit import media

# Par module, les constantes propres au média. Les autres constantes sont du moteur (délais techniques, prix des
# modèles, tailles de lots) et ne se règlent pas ici. Les règles de style (mots par phrase, phrases par paragraphe)
# viennent du fichier `regles_redaction` du graphe, pas d'ici.
REGLABLES: dict[str, set[str]] = {
    "fil": {
        "FRAICHEUR",
        "MOTS_MIN",
        "TITRE_MAX",
        "RSS_MAX",
        "PANNEAU",
        "PAR_PASSAGE",
        "RELUS_PAR_PASSAGE",
        "DEFINITION_USD",
        "CONTEXTE_JOURS",
        "PILE_DELAI",
        "PILE_HORIZON",
        "PILE_PAR_PASSAGE",
        "PILE_RESERVE",
        "PILE_DELAI_RESERVE",
        "PILE_GARDE",
        "PILE_GARANTIE",
        "JOUR_GARANTI",
        "PART_SECONDAIRE",
    },
    "fil_fiche": {"ELEMENTS_MIN", "TITRE_MAX", "FICHE_ATTENTE_MAX"},
    "alertes_fil": {
        "PALIER2_SILENCE",
        "PALIER3_SILENCE",
        "REPETITION",
        "ECHECS_FIL",
        "ECHECS_PUBLICATION",
    },
    "publier_fil": {"MAX"},
    "lettre": {
        "FAITS_MAX",
        "UNE_MIN",
        "PAR_EDITEUR",
        "UNE_PHRASES",
        "UNE_NOMBRES",
        "CHAPEAU_PHRASES",
        "CHAPEAU_NOMBRES",
        "MOTS_TITRE",
        "MOTS_TITRE_BREF",
        "TITRE_RUBRIQUE_CARACTERES",
        "MOTS_MAX_ECLAIRAGE",
        "AUSSI_MAX",
        "BREF_PAR_THEME",
        "BREF_MAX",
        "JOURS_SEMAINE",
        "FRAICHEUR_JOUR",
        "POINT",
    },
    "lettre_semaine": {"UNE_MAX", "PAR_RUBRIQUE"},
    "routine_hebdo": {"FENETRE_MUETTE", "PLAFOND_NOTIONS_RAPPORT"},
    "collecte_rss": {"MOTS_MINIMUM"},
}

# Le type de chaque défaut, sans importer les modules (media.charger valide avant tout import du moteur).
_TEXTES = {("lettre", "POINT")}
_NOMBRES = {("fil", "DEFINITION_USD"), ("fil", "PART_SECONDAIRE")}
_PAIRES = {("fil", "JOUR_GARANTI")}


def _entier(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _convertir(court: str, nom: str, v):
    """La valeur réglée, au type du défaut ; ConfigMediaInvalide sinon."""
    ou = f"reglages.{court}.{nom.lower()}"
    if (court, nom) in _TEXTES:
        if isinstance(v, str) and v:
            return v
        raise media.ConfigMediaInvalide(f"{ou} : un texte attendu, lu {v!r}")
    if (court, nom) in _NOMBRES:
        if _entier(v) or isinstance(v, float):
            return float(v)
        raise media.ConfigMediaInvalide(f"{ou} : un nombre attendu, lu {v!r}")
    if (court, nom) in _PAIRES:
        if isinstance(v, list) and len(v) == 2 and all(_entier(x) for x in v):
            return tuple(v)
        raise media.ConfigMediaInvalide(f"{ou} : deux entiers attendus, lu {v!r}")
    if _entier(v):
        return v
    raise media.ConfigMediaInvalide(f"{ou} : un entier attendu, lu {v!r}")


def valider(bloc) -> dict[str, dict[str, object]]:
    """Le bloc `reglages:` vérifié, converti : {module: {CONSTANTE: valeur}}."""
    if not isinstance(bloc, dict):
        raise media.ConfigMediaInvalide(f"reglages : un dictionnaire par module attendu, lu {bloc!r}")
    out: dict[str, dict[str, object]] = {}
    for court, valeurs in bloc.items():
        if court not in REGLABLES:
            raise media.ConfigMediaInvalide(f"reglages.{court} : module inconnu ; admis {sorted(REGLABLES)}")
        if not isinstance(valeurs, dict):
            raise media.ConfigMediaInvalide(f"reglages.{court} : un dictionnaire attendu, lu {valeurs!r}")
        out[court] = {}
        for nom, v in valeurs.items():
            if not isinstance(nom, str) or nom.upper() not in REGLABLES[court]:
                admis = sorted(x.lower() for x in REGLABLES[court])
                raise media.ConfigMediaInvalide(f"reglages.{court}.{nom} : réglage inconnu ; admis {admis}")
            out[court][nom.upper()] = _convertir(court, nom.upper(), v)
    return out


def appliquer(module: str, espace: dict, c: dict | None = None) -> None:
    """Remplace dans `espace` (les globales du module) les constantes que le média règle. `c` : le config.yaml du
    média, lu par media.courant() par défaut."""
    c = media.courant() if c is None else c
    court = module.rsplit(".", 1)[-1]
    espace.update(valider(c.get("reglages") or {}).get(court, {}))

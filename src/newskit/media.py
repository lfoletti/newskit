#!/usr/bin/env python3
"""Le dossier du média : `config.yaml` (NEWSKIT.md, § Le dossier du média ; mainteneur, 6 et 7.11.2026).

Un média est un dossier qui porte un `config.yaml`. Le moteur s'y accroche (commun.RACINE : NEWSKIT_DONNEES, sinon le
dossier courant s'il porte un `config.yaml` de média, sinon le dépôt du code). Le fichier dit :

    version: 1
    media:                       # l'identité du média, l'ancien bloc `edition:` du registre, et :
      nom: apiculture
      langue: fr                 # la langue des consignes et de la publication (le français seul, aujourd'hui)
      traductions: [en]          # les langues traduites (fil et lettre anglais) ; aucune par défaut
      passages: {tous_les: 1, heures: ["05:30"]}   # tous les N jours, aux heures dites (nouveau_graphe, routine)
    modeles:                     # un modèle par rôle ; un rôle absent garde le modèle d'aujourd'hui (DEFAUTS)
      extraction: claude-opus-5-5
    sources: sources.yaml        # le fichier des sources, relatif au dossier
    diffusion: newskit.abonnes   # le module de l'édition qui sert ses abonnés (diffusion.py) ; aucun par défaut
    lettre_aux_abonnes: true     # la daily part aussi aux abonnés, sans validation (notifier_courriel) ; false par défaut
    registre_des_graphes: graphes.json   # libellés et sujets déclarés des graphes, réécrits par le moteur (défaut)
    graphes: {…}                 # et les autres blocs du registre que le média tient lui-même (BLOCS)

Le registre que lit config_sources est la composition des deux fichiers ; un bloc tenu par les deux est refusé (une
valeur, un seul endroit). Sans `config.yaml`, rien ne change.

Usage :
    python3 -m newskit.media          # le média lu, ses rôles et leurs modèles
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from newskit.commun import RACINE  # noqa: E402

FICHIER = RACINE / "config.yaml"

# L'identité du média (l'ancien bloc `edition:` du registre, NEWSKIT.md N2) : son nom (préfixe des clés de lecture
# dans Thalamus, producteur des métadonnées, objet des courriels, agent HTTP), son site, son dépôt, le clone de son
# serveur. Les valeurs neutres ; le moteur n'en écrit aucune autre dans son code.
IDENTITE = {
    "nom": "newskit",
    "site": "",
    "depot": "",
    "clone": "",
    "principal": "principal",
    "domaine_principal": "",
    "launchd": "local.newskit",  # launchd : préfixe des étiquettes des daemons (nouveau_graphe)
    "lettre_site": "",  # le site public cité au pied de la lettre de la semaine (lettre.py, B16)
    "anciens_sites": "",  # les sites d'avant, séparés par des virgules : encore des sites de l'édition
    "graphe_du_site": "",  # le graphe du site (fil, lettre, station) ; vide : le graphe principal
    "robot_contact": "",  # l'adresse que le robot donne aux sites des sources (commun.agent_robot)
    "service_maj": "",  # le service du serveur qui avance son clone et refait les pages ; vide : aucun
    "cerveau_du_site": "",  # le cerveau Thalamus du graphe du site ; vide : « <nom>/<graphe du site> »
    "cerveau_lacunes": "",  # le cerveau Thalamus des lacunes signalées ; vide : « lacunes-signalees »
}
NOM = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")  # un nom de média ou de graphe
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,79}$")  # un nom de service systemd, passé à ssh tel quel
CERVEAU = re.compile(r"^[a-z0-9][a-z0-9/-]{0,62}$")  # un cerveau de Thalamus (« media-site », « media/graphe »)

# Les modèles d'aujourd'hui, par rôle (relevé du 7.11.2026 dans 11 scripts). Un rôle, un travail :
ROLES = {
    "extraction": "lire une fiche entière et en tirer la couche 2 (extraire_concepts)",
    "analyse": "juger un graphe entier : thèmes émergents, sessions de banc (themes_emergents, banc_session)",
    "redaction": "rédiger d'après des preuves : synthèses, lettre, réponses du banc (synthese, lettre, banc)",
    "lecture": "lire et trancher : même notion ?, lecture du banc (fondre_doublons, banc, banc_injection)",
    "traduction": "traduire un libellé ou deux phrases (francais, fondre_doublons)",
    "questions": "écrire les questions d'entraînement de l'encodeur (encodeur)",
}
DEFAUTS = {
    "extraction": "claude-opus-5-5",
    "analyse": "claude-opus-5-5",
    "redaction": "claude-sonnet-5-5",
    "lecture": "claude-sonnet-5-5",
    "traduction": "claude-haiku-5-5",
    "questions": "claude-haiku-5-5",
}
LANGUES = {"fr"}  # les consignes aux modèles sont écrites en français (relevé N2, point 5)
TRADUCTIONS = {"en"}  # fil_anglais et lettre_anglaise
FREQUENCE = (5, 30)  # l'heure par défaut d'un graphe neuf, celle de nouveau_graphe avant le dossier
# Les blocs du registre que config.yaml peut tenir à la place du fichier des sources.
BLOCS = {"graphes", "poseur", "reporter", "eclaireur", "banc", "anciens_noms", "domaines"}
CHAMPS = {
    "version",
    "media",
    "modeles",
    "sources",
    "diffusion",
    "lettre_aux_abonnes",
    "registre_des_graphes",
    "reglages",
} | BLOCS
PROPRES = {
    "langue",
    "traductions",
    "frequence",
    "passages",
}  # les champs de `media:` qui ne sont pas de l'ancien `edition:`


class ConfigMediaInvalide(ValueError):
    pass


def est_un_media(dossier: Path) -> bool:
    """Un dossier porte-t-il un `config.yaml` de média (un bloc `media:`) ? Un `config.yaml` d'un autre outil, non."""
    p = Path(dossier) / "config.yaml"
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(d, dict) and isinstance(d.get("media"), dict)


def charger(chemin: Path = FICHIER) -> dict:
    """Le `config.yaml` vérifié ; {} sans fichier. Une clé inconnue, une langue, un rôle ou une heure hors de ce que le
    moteur sait faire lève ConfigMediaInvalide, qui nomme le champ."""
    chemin = Path(chemin)
    if not chemin.is_file():
        return {}
    try:
        d = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigMediaInvalide(f"{chemin.name} : YAML illisible ({e})") from e
    if not isinstance(d, dict):
        raise ConfigMediaInvalide(f"{chemin.name} : un dictionnaire attendu")
    if d.get("version") != 1:
        raise ConfigMediaInvalide(f"{chemin.name} : version attendue 1, lue {d.get('version')!r}")
    if set(d) - CHAMPS:
        raise ConfigMediaInvalide(f"{chemin.name} : champs inconnus {sorted(set(d) - CHAMPS)} ; admis {sorted(CHAMPS)}")
    m = d.get("media")
    if not isinstance(m, dict):
        raise ConfigMediaInvalide(f"{chemin.name} : media : un dictionnaire attendu")
    _valider_media(m, chemin.name)
    mod = d.get("modeles") or {}
    if not isinstance(mod, dict) or set(mod) - set(ROLES):
        raise ConfigMediaInvalide(
            f"{chemin.name} : modeles : rôles inconnus {sorted(set(mod) - set(ROLES))} ; admis {sorted(ROLES)}"
        )
    for role, v in mod.items():
        if not isinstance(v, str) or not v.strip():
            raise ConfigMediaInvalide(f"{chemin.name} : modeles.{role} : le nom d'un modèle")
    f = d.get("diffusion")
    if f is not None and (
        not isinstance(f, str) or not f.endswith(".py") or Path(f).is_absolute() or ".." in Path(f).parts
    ):
        raise ConfigMediaInvalide(f"{chemin.name} : diffusion : un module .py relatif au dossier du média, lu {f!r}")
    if "lettre_aux_abonnes" in d and not isinstance(d["lettre_aux_abonnes"], bool):
        raise ConfigMediaInvalide(f"{chemin.name} : lettre_aux_abonnes : true ou false, lu {d['lettre_aux_abonnes']!r}")
    r = d.get("registre_des_graphes")
    if r is not None and (not isinstance(r, str) or not r.strip()):
        raise ConfigMediaInvalide(f"{chemin.name} : registre_des_graphes : un chemin de fichier, lu {r!r}")
    if "reglages" in d:  # les réglages propres au média (reglages.py, mainteneur, 11.11.2026)
        from newskit import reglages  # noqa: PLC0415 — reglages importe ce module

        try:
            reglages.valider(d["reglages"])
        except ConfigMediaInvalide as e:
            raise ConfigMediaInvalide(f"{chemin.name} : {e}") from e
    s = d.get("sources")
    if s is not None:
        if not isinstance(s, str) or not s or Path(s).is_absolute() or ".." in Path(s).parts:
            raise ConfigMediaInvalide(f"{chemin.name} : sources : un chemin relatif au dossier du média, lu {s!r}")
    return d


def _valider_media(m: dict, ou: str) -> None:
    inconnus = set(m) - set(IDENTITE) - PROPRES
    if inconnus:
        raise ConfigMediaInvalide(
            f"{ou} : media : champs inconnus {sorted(inconnus)} ; admis {sorted(set(IDENTITE) | PROPRES)}"
        )
    for cle, v in m.items():
        if cle not in PROPRES and not isinstance(v, str):
            raise ConfigMediaInvalide(f"{ou} : media.{cle} : un texte")
    for cle in ("nom", "principal"):
        if cle in m and not NOM.match(m[cle]):
            raise ConfigMediaInvalide(f"{ou} : media.{cle} : minuscules, chiffres et tirets")
    if m.get("service_maj") and not SERVICE.match(m["service_maj"]):
        raise ConfigMediaInvalide(f"{ou} : media.service_maj : lettres, chiffres et « @._- », lu {m['service_maj']!r}")
    for cle in ("cerveau_du_site", "cerveau_lacunes"):
        if m.get(cle) and not CERVEAU.match(m[cle]):
            raise ConfigMediaInvalide(f"{ou} : media.{cle} : minuscules, chiffres et « -/ », lu {m[cle]!r}")
    if "langue" in m and m["langue"] not in LANGUES:
        raise ConfigMediaInvalide(f"{ou} : media.langue : {m['langue']!r} ; le moteur écrit en {sorted(LANGUES)}")
    t = m.get("traductions", [])
    if not isinstance(t, list) or set(t) - TRADUCTIONS:
        raise ConfigMediaInvalide(f"{ou} : media.traductions : une liste parmi {sorted(TRADUCTIONS)}, lu {t!r}")
    if "passages" in m:
        valider_passages(m["passages"], ou, "frequence" in m)
    f = m.get("frequence", {})
    if not isinstance(f, dict) or set(f) - {"heure", "minute"}:
        raise ConfigMediaInvalide(f"{ou} : media.frequence : {{heure, minute}}, lu {f!r} (jour inconnu)")
    for cle, borne in (("heure", 23), ("minute", 59)):
        v = f.get(cle, 0)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= borne:
            raise ConfigMediaInvalide(f"{ou} : media.frequence.{cle} : un entier de 0 à {borne}, lu {v!r}")


_courant: dict | None = None


def courant() -> dict:
    """Le `config.yaml` du média de ce processus, lu une fois."""
    global _courant
    if _courant is None:
        _courant = charger(FICHIER)
    return _courant


def modele(role: str, c: dict | None = None) -> str:
    if role not in ROLES:
        raise KeyError(f"rôle inconnu : {role} ; connus {sorted(ROLES)}")
    c = courant() if c is None else c
    return (c.get("modeles") or {}).get(role) or DEFAUTS[role]


def langue(c: dict | None = None) -> str:
    c = courant() if c is None else c
    return (c.get("media") or {}).get("langue") or "fr"


def traductions(c: dict | None = None) -> list[str]:
    c = courant() if c is None else c
    return list((c.get("media") or {}).get("traductions") or [])


HEURE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def valider_passages(p, ou: str, avec_frequence: bool) -> None:
    """`media.passages` : `tous_les` (jours, entier ≥ 1) et `heures` (liste non vide de « HH:MM ») ; ni autre clé, ni
    `frequence` à côté (deux réponses à la même question)."""
    if avec_frequence:
        raise ConfigMediaInvalide(f"{ou} : media.passages et media.frequence ensemble ; garder passages")
    if not isinstance(p, dict) or set(p) - {"tous_les", "heures"}:
        raise ConfigMediaInvalide(f"{ou} : media.passages : {{tous_les, heures}}, lu {p!r}")
    n = p.get("tous_les", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ConfigMediaInvalide(f"{ou} : media.passages.tous_les : un nombre de jours, entier, 1 au moins ; lu {n!r}")
    h = p.get("heures", ["05:30"])
    if not isinstance(h, list) or not h or not all(isinstance(x, str) and HEURE.match(x) for x in h):
        raise ConfigMediaInvalide(f"{ou} : media.passages.heures : une liste d'heures « HH:MM », lu {h!r}")


def passages(c: dict | None = None) -> tuple[int, list[tuple[int, int]]]:
    """L'intervalle en jours et les heures de départ du jour de passage ; l'ancienne `frequence` vaut un passage par
    jour à son heure."""
    c = courant() if c is None else c
    m = c.get("media") or {}
    if "passages" not in m:
        return 1, [frequence(c)]
    p = m["passages"]
    heures = [tuple(int(x) for x in HEURE.match(h).groups()) for h in p.get("heures", ["05:30"])]
    return int(p.get("tous_les", 1)), heures


def frequence(c: dict | None = None) -> tuple[int, int]:
    c = courant() if c is None else c
    f = (c.get("media") or {}).get("frequence")
    if not f:
        return FREQUENCE
    return int(f.get("heure", 0)), int(f.get("minute", 0))


def _dire_passages(c: dict) -> str:
    n, heures = passages(c)
    quand = "chaque jour" if n == 1 else f"tous les {n} jours"
    return f"{quand} à " + ", ".join(f"{h:02d}:{m:02d}" for h, m in heures)


def registre_des_graphes(c: dict | None = None, racine: Path | None = None) -> Path:
    """Le registre des graphes du média (libellé, résumé, sujets déclarés, réécrits à chaque passage par les thèmes
    émergents) : `registre_des_graphes` de config.yaml (« ~ » développé, sinon relatif au dossier du média), sinon
    `graphes.json` dans le dossier du média. Un config.yaml illisible laisse le défaut (il est signalé ailleurs)."""
    racine = Path(racine or RACINE)
    if c is None:
        try:
            c = courant()
        except ConfigMediaInvalide:
            c = {}
    v = c.get("registre_des_graphes")
    if not v:
        return racine / "graphes.json"
    p = Path(v).expanduser()
    return p if p.is_absolute() else racine / p


def fichier_des_sources(chemin: Path = FICHIER) -> Path | None:
    """Le fichier des sources que nomme `config.yaml` (clé `sources`), relatif à son dossier ; None sans lui."""
    s = charger(chemin).get("sources")
    return Path(chemin).parent / s if s else None


def blocs_pour(registre: Path) -> tuple[dict, Path | None]:
    """Ce que `config.yaml` apporte au registre `registre` : le bloc `edition` (tiré de `media:`, sans ses champs
    propres) et les BLOCS qu'il tient. Le `config.yaml` cherché est le plus proche au-dessus du registre, puis celui de la racine
    des données ; il ne compte que s'il nomme ce registre. ({}, None) sinon."""
    registre = Path(registre).resolve()
    proche = next((p / "config.yaml" for p in registre.parents if est_un_media(p)), None)
    for c in dict.fromkeys(x for x in (proche, FICHIER) if x is not None):
        if not est_un_media(c.parent):
            continue
        d = charger(c)
        s = d.get("sources") or "sources.yaml"
        if (c.parent / s).resolve() != registre:
            continue
        out = {k: d[k] for k in BLOCS if k in d}
        out["edition"] = {k: v for k, v in (d.get("media") or {}).items() if k not in PROPRES}
        return out, c
    return {}, None


def main() -> int:
    try:
        c = courant()
    except ConfigMediaInvalide as e:
        print(f"  {e}", file=sys.stderr)
        return 1
    if not c:
        print(f"  aucun config.yaml sous {RACINE} : les valeurs d'aujourd'hui")
    m = c.get("media") or {}
    print(
        f"  média {m.get('nom', '(sans nom)')} : langue {langue(c)}, traductions {traductions(c) or 'aucune'}, "
        f"passages {_dire_passages(c)}"
    )
    for role, quoi in ROLES.items():
        print(f"  {role:<11} {modele(role, c):<20} {quoi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

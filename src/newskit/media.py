#!/usr/bin/env python3
"""Le dossier du média : `config.yaml` (NEWSKIT.md, § Le dossier du média ; mainteneur, 6 et 7.11.2026).

Un média est un dossier qui porte un `config.yaml`. Le moteur s'y accroche (commun.RACINE : NEWSKIT_DONNEES, sinon le
dossier courant s'il porte un `config.yaml` de média, sinon le dépôt du code). Le fichier dit :

    version: 1
    media:                       # l'identité du média, l'ancien bloc `edition:` du registre, et :
      nom: apiculture
      langue: fr                 # la langue des consignes et de la publication (le français seul, aujourd'hui)
      traductions: [en]          # les langues traduites (fil et lettre anglais) ; aucune par défaut
      frequence: {heure: 5, minute: 30}   # l'heure de la nuit d'un graphe neuf (nouveau_graphe)
    modeles:                     # un modèle par rôle ; un rôle absent garde le modèle d'aujourd'hui (DEFAUTS)
      extraction: claude-opus-5-5
    sources: sources.yaml        # le fichier des sources, relatif au dossier
    diffusion: newskit.abonnes   # le module de l'édition qui sert ses abonnés (diffusion.py) ; aucun par défaut
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
IDENTITE = {"nom": "newskit", "site": "", "depot": "", "clone": "", "principal": "principal", "domaine_principal": "",
           "launchd": "local.newskit",   # launchd : préfixe des étiquettes des daemons (nouveau_graphe)
           "lettre_site": "",               # le site public cité au pied de la lettre de la semaine (lettre.py, B16)
           "anciens_sites": "",             # les sites d'avant, séparés par des virgules : encore des sites de l'édition
           "graphe_du_site": "",            # le graphe du site (fil, lettre, station) ; vide : le graphe principal
           "robot_contact": ""}             # l'adresse que le robot donne aux sites des sources (commun.agent_robot)
NOM = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")    # un nom de média ou de graphe

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
LANGUES = {"fr"}                 # les consignes aux modèles sont écrites en français (relevé N2, point 5)
TRADUCTIONS = {"en"}             # fil_anglais et lettre_anglaise
FREQUENCE = (5, 30)              # l'heure par défaut d'un graphe neuf, celle de nouveau_graphe avant le dossier
# Les blocs du registre que config.yaml peut tenir à la place du fichier des sources.
BLOCS = {"graphes", "poseur", "reporter", "eclaireur", "banc", "anciens_noms", "domaines"}
CHAMPS = {"version", "media", "modeles", "sources", "diffusion"} | BLOCS
PROPRES = {"langue", "traductions", "frequence"}      # les champs de `media:` qui ne sont pas de l'ancien `edition:`


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
        raise ConfigMediaInvalide(f"{chemin.name} : modeles : rôles inconnus {sorted(set(mod) - set(ROLES))} ; "
                                  f"admis {sorted(ROLES)}")
    for role, v in mod.items():
        if not isinstance(v, str) or not v.strip():
            raise ConfigMediaInvalide(f"{chemin.name} : modeles.{role} : le nom d'un modèle")
    f = d.get("diffusion")
    if f is not None and (not isinstance(f, str) or not f.endswith(".py") or Path(f).is_absolute()
                          or ".." in Path(f).parts):
        raise ConfigMediaInvalide(f"{chemin.name} : diffusion : un module .py relatif au dossier du média, lu {f!r}")
    s = d.get("sources")
    if s is not None:
        if not isinstance(s, str) or not s or Path(s).is_absolute() or ".." in Path(s).parts:
            raise ConfigMediaInvalide(f"{chemin.name} : sources : un chemin relatif au dossier du média, lu {s!r}")
    return d


def _valider_media(m: dict, ou: str) -> None:
    inconnus = set(m) - set(IDENTITE) - PROPRES
    if inconnus:
        raise ConfigMediaInvalide(f"{ou} : media : champs inconnus {sorted(inconnus)} ; "
                                  f"admis {sorted(set(IDENTITE) | PROPRES)}")
    for cle, v in m.items():
        if cle not in PROPRES and not isinstance(v, str):
            raise ConfigMediaInvalide(f"{ou} : media.{cle} : un texte")
    for cle in ("nom", "principal"):
        if cle in m and not NOM.match(m[cle]):
            raise ConfigMediaInvalide(f"{ou} : media.{cle} : minuscules, chiffres et tirets")
    if "langue" in m and m["langue"] not in LANGUES:
        raise ConfigMediaInvalide(f"{ou} : media.langue : {m['langue']!r} ; le moteur écrit en {sorted(LANGUES)}")
    t = m.get("traductions", [])
    if not isinstance(t, list) or set(t) - TRADUCTIONS:
        raise ConfigMediaInvalide(f"{ou} : media.traductions : une liste parmi {sorted(TRADUCTIONS)}, lu {t!r}")
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


def frequence(c: dict | None = None) -> tuple[int, int]:
    c = courant() if c is None else c
    f = (c.get("media") or {}).get("frequence")
    if not f:
        return FREQUENCE
    return int(f.get("heure", 0)), int(f.get("minute", 0))


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
    print(f"  média {m.get('nom', '(sans nom)')} : langue {langue(c)}, traductions {traductions(c) or 'aucune'}, "
          f"nuit à {frequence(c)[0]:02d}:{frequence(c)[1]:02d}")
    for role, quoi in ROLES.items():
        print(f"  {role:<11} {modele(role, c):<20} {quoi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

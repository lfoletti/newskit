"""Les procédés d'ingestion d'un graphe, par type de contenu (mainteneur, 15 et 19.11.2026).

« newskit prend en variable une fiche d'extracteur, une convention, une ontologie... tout ce qui spécialise le média » ;
« ce procédé est lié au type de contenu à extraire et au média qui l'accueille ». Le moteur porte le mécanisme ; le
dossier du média porte les consignes et la convention. Un graphe déclare, sous `procedes`, un bloc par type de
contenu :

    procedes:
      papier:                                    # le type de contenu
        contenu: {champ: arxiv_id}               # les fiches dont l'en-tête porte ce champ
        texte:                                   # le texte intégral, coupé à mots_max mots
          integral: true
          adresse: "https://arxiv.org/pdf/{arxiv_id}"   # le PDF, d'après l'en-tête ; un champ `pdf` l'emporte
          mots_max: 45000
        convention: ontologie.yaml               # obligatoire : types de nœuds et relations admis
        passes:                                  # dans l'ordre d'exécution
          lecture: {consigne: procedes/lecture.txt, sortie: corps, mots_min: 500}  # la réponse devient le corps
          procede: {consigne: procedes/procede.txt, forme: etapes}       # la réponse suit une forme du moteur
          architecture: {consigne: procedes/archi.txt, forme: composants, tri: procedes/tri.txt}
        extraction: {volume: procedes/volume.txt}  # la consigne de volume de la couche 2 pour ce contenu
        mentions: true                           # les mentions sans modèle
        appel: lots                              # claude_p (défaut) ou lots de l'API

Une forme pose des nœuds et des relations dont les types doivent figurer dans la convention : sans eux, le procédé
se refuse au chargement (« refus à la création » d'un graphe sans convention, 15.11.2026).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MOTS_MAX = 45000  # mots du texte intégral donnés au modèle (un PDF fait près de 3 jetons par mot)
MOTS_BORNES = (1000, 200000)
APPELS = ("claude_p", "lots")
SORTIES = ("corps",)
# Ce que chaque forme pose au graphe : les types de nœuds et les relations que la convention doit admettre.
FORMES = {
    "etapes": {"types": ("procedure", "step", "variant"), "relations": ("presents", "has_step", "precedes", "follows")},
    "composants": {
        "types": ("model", "component"),
        "relations": ("presents", "has_component", "feeds", "skip", "instance_of"),
    },
}
MENTIONS = ("mentions", "uses")  # du document, d'une étape, vers une notion canonique que son texte nomme
NOM = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")  # un type de contenu, une passe, un type ou une relation
CHAMPS = {"contenu", "texte", "convention", "passes", "extraction", "mentions", "appel"}
PASSE = {"consigne", "sortie", "forme", "tri", "mots_min"}


def _fichier(racine: Path, chemin, ou: str) -> Path:
    """Un fichier du dossier du média, par son chemin relatif."""
    if not isinstance(chemin, str) or not chemin or chemin.startswith("/") or ".." in Path(chemin).parts:
        raise ValueError(f"{ou} : un chemin relatif au dossier du média, lu {chemin!r}")
    p = Path(racine) / chemin
    if not p.is_file():
        raise ValueError(f"{ou} : fichier absent du dossier du média : {chemin}")
    return p


def _ids(liste, ou: str) -> list[str]:
    if not isinstance(liste, list) or not liste:
        raise ValueError(f"{ou} : une liste non vide attendue")
    out = []
    for x in liste:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str):
            raise ValueError(f"{ou} : chaque entrée porte un `id`, lu {x!r}")
        for i in (s.strip() for s in x["id"].split(",")):
            if not NOM.match(i):
                raise ValueError(f"{ou} : identifiant {i!r} : minuscules, chiffres, « _ » et « - »")
            out.append(i)
    return out


def convention(chemin: Path) -> dict:
    """La convention d'un graphe : {nom, canoniques, propres, relations}. Une ligne de relation peut en nommer deux
    (« precedes, follows »). ValueError si elle est illisible ou mal formée."""
    try:
        d = yaml.safe_load(Path(chemin).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"convention illisible ({e})") from e
    if not isinstance(d, dict) or not isinstance(d.get("nom"), str) or not d["nom"].strip():
        raise ValueError("convention : un `nom` attendu")
    noeuds = d.get("noeuds")
    if not isinstance(noeuds, dict):
        raise ValueError("convention : `noeuds` attendu, {canoniques, propres}")
    canoniques = _ids(noeuds.get("canoniques"), "convention : noeuds.canoniques")
    propres = _ids(noeuds.get("propres"), "convention : noeuds.propres")
    if len(set(canoniques) | set(propres)) != len(canoniques) + len(propres):
        raise ValueError("convention : un type de nœud déclaré deux fois")
    relations = _ids(d.get("relations"), "convention : relations")
    return {"nom": d["nom"], "canoniques": set(canoniques), "propres": set(propres), "relations": set(relations)}


def _passe(nom, p, racine: Path, ou: str) -> dict:
    if not isinstance(nom, str) or not NOM.match(nom):
        raise ValueError(f"{ou} : nom de passe {nom!r} : minuscules, chiffres, « _ » et « - »")
    ou = f"{ou}.{nom}"
    if not isinstance(p, dict) or set(p) - PASSE:
        raise ValueError(f"{ou} : {sorted(PASSE)} admis")
    if ("sortie" in p) == ("forme" in p):
        raise ValueError(f"{ou} : `sortie` ou `forme`, l'une des deux")
    if "sortie" in p and p["sortie"] not in SORTIES:
        raise ValueError(f"{ou} : sortie parmi {list(SORTIES)}, lu {p['sortie']!r}")
    if "forme" in p and p["forme"] not in FORMES:
        raise ValueError(f"{ou} : forme parmi {sorted(FORMES)}, lu {p['forme']!r}")
    mots_min = p.get("mots_min", 1)
    if isinstance(mots_min, bool) or not isinstance(mots_min, int) or mots_min < 0:
        raise ValueError(f"{ou}.mots_min : un entier positif ou nul, lu {mots_min!r}")
    return {
        "nom": nom,
        "consigne": _fichier(racine, p.get("consigne"), f"{ou}.consigne"),
        "mots_min": mots_min,
        "sortie": p.get("sortie"),
        "forme": p.get("forme"),
        "tri": _fichier(racine, p["tri"], f"{ou}.tri") if "tri" in p else None,
    }


def _type_de_contenu(nom, v, racine: Path) -> dict:
    if not isinstance(nom, str) or not NOM.match(nom):
        raise ValueError(f"nom de type de contenu {nom!r} : minuscules, chiffres, « _ » et « - »")
    if not isinstance(v, dict) or set(v) - CHAMPS:
        raise ValueError(f"{nom} : champs inconnus {sorted(set(v) - CHAMPS) if isinstance(v, dict) else v!r}")
    c = v.get("contenu")
    if not isinstance(c, dict) or set(c) != {"champ"} or not isinstance(c["champ"], str) or not c["champ"]:
        raise ValueError(f"{nom}.contenu : {{champ: <champ d'en-tête>}} attendu, lu {c!r}")
    if "convention" not in v:
        raise ValueError(f"{nom}.convention : obligatoire, un procédé sans convention se refuse")
    conv = convention(_fichier(racine, v["convention"], f"{nom}.convention"))
    t = v.get("texte", {})
    if not isinstance(t, dict) or set(t) - {"integral", "adresse", "mots_max"}:
        raise ValueError(f"{nom}.texte : {{integral, adresse, mots_max}} attendu, lu {t!r}")
    adresse = t.get("adresse")
    if adresse is not None and (not isinstance(adresse, str) or not re.match(r"^https?://\S+$", adresse)):
        raise ValueError(f"{nom}.texte.adresse : une adresse http(s), champs d'en-tête entre accolades, lu {adresse!r}")
    integral, mots = t.get("integral", True), t.get("mots_max", MOTS_MAX)
    if not isinstance(integral, bool):
        raise ValueError(f"{nom}.texte.integral : vrai ou faux")
    if not isinstance(mots, int) or not MOTS_BORNES[0] <= mots <= MOTS_BORNES[1]:
        raise ValueError(f"{nom}.texte.mots_max : un entier de {MOTS_BORNES[0]} à {MOTS_BORNES[1]}")
    brutes = v.get("passes")
    if not isinstance(brutes, dict) or not brutes:
        raise ValueError(f"{nom}.passes : au moins une passe")
    passes = [_passe(k, p, racine, f"{nom}.passes") for k, p in brutes.items()]
    if sum(p["sortie"] == "corps" for p in passes) > 1:
        raise ValueError(f"{nom}.passes : une seule passe écrit le corps de la fiche")
    types, relations = conv["canoniques"] | conv["propres"], conv["relations"]
    for p in passes:
        if p["forme"]:
            f = FORMES[p["forme"]]
            manque = sorted(set(f["types"]) - types) + sorted(set(f["relations"]) - relations)
            if manque:
                raise ValueError(f"{nom}.passes.{p['nom']} : la forme {p['forme']} demande à la convention {manque}")
    ext = v.get("extraction", {})
    if not isinstance(ext, dict) or set(ext) - {"volume"}:
        raise ValueError(f"{nom}.extraction : {{volume}} attendu, lu {ext!r}")
    volume = _fichier(racine, ext["volume"], f"{nom}.extraction.volume") if "volume" in ext else None
    mentions = v.get("mentions", False)
    if not isinstance(mentions, bool):
        raise ValueError(f"{nom}.mentions : vrai ou faux")
    if mentions and set(MENTIONS) - relations:
        raise ValueError(f"{nom}.mentions : la convention doit admettre {sorted(set(MENTIONS) - relations)}")
    appel = v.get("appel", "claude_p")
    if appel not in APPELS:
        raise ValueError(f"{nom}.appel : parmi {list(APPELS)}, lu {appel!r}")
    return {
        "contenu": {"champ": c["champ"]},
        "texte": {"integral": integral, "adresse": adresse, "mots_max": mots},
        "convention": conv,
        "passes": passes,
        "extraction": {"volume": volume},
        "mentions": mentions,
        "appel": appel,
    }


def declaration(decl, racine: Path) -> dict | None:
    """La déclaration `procedes` d'un graphe, vérifiée et complétée, les chemins résolus dans le dossier du média
    `racine` : {type de contenu: {...}}. None sans déclaration ; ValueError si elle est mal formée."""
    if decl is None:
        return None
    if not isinstance(decl, dict) or not decl:
        raise ValueError("un dictionnaire type de contenu → procédé attendu")
    return {nom: _type_de_contenu(nom, v, Path(racine)) for nom, v in decl.items()}

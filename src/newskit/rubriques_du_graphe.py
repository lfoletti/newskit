"""Les rubriques d'un graphe tirées de ses sujets déclarés (mainteneur, 15.11.2026 : « rubriques à adapter
dynamiquement, maximum 7 »).

Un graphe range d'ordinaire ses billets dans une table de rubriques tenue à la main (`rubriques: {fichier, ordre}`).
Un graphe qui déclare `rubriques: {du_graphe: true, max: 7}` n'en tient pas : ses sujets déclarés (le registre des
graphes, que les thèmes émergents font bouger) sont regroupés en `max` rubriques au plus par un appel au modèle. Le
regroupement est une propriété des sujets : il se demande une fois et se garde avec son empreinte, dans
`rubriques.json` du graphe ; il se refait quand un sujet entre ou sort, ou quand la borne change. Une réponse hors
contrat ne remplace rien : les rubriques d'avant restent.

    python3 -m newskit.rubriques_du_graphe --graphe <graphe>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

MAX = 7  # rubriques au plus, sans `max` au registre
MAX_ADMIS = 12  # au-delà, ce ne sont plus des rubriques
NOM_MAX = 24  # caractères d'un nom de rubrique : il tient dans une pastille du fil
FICHIER = "rubriques.json"
POSTE = "rubriques"

SCHEMA = {
    "type": "object",
    "required": ["rubriques"],
    "properties": {
        "rubriques": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["nom", "en", "mots", "sujets"],
                "properties": {
                    "nom": {"type": "string"},
                    "en": {"type": "string"},
                    "mots": {"type": "array", "items": {"type": "string"}},
                    "sujets": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}

CONSIGNE = """Voici les sujets que suit un graphe de veille, numérotés. Regroupe-les en {maximum} rubriques au plus,
pour le fil d'actualité de ce graphe.

Règles :
- chaque sujet va dans une rubrique et une seule ; une rubrique a au moins un sujet ;
- `nom` : le nom français de la rubrique, un ou deux mots, {nom_max} caractères au plus, sans ponctuation ;
- `en` : le même nom en anglais ;
- `mots` : cinq à dix mots ou expressions, en minuscules, anglais et français, qu'un titre d'article de cette rubrique
  porte d'ordinaire ;
- `sujets` : les numéros des sujets de la rubrique ;
- range les rubriques de la plus large à la plus étroite.

Sujets :
{sujets}
"""


def declaration(decl: object) -> int | None:
    """La borne d'un graphe qui tire ses rubriques de ses sujets ; None pour une table déclarée ou pour rien. Lève
    ValueError sur une déclaration mal formée : `{du_graphe: true}`, avec `max` de 1 à MAX_ADMIS si on le donne."""
    if not isinstance(decl, dict) or "du_graphe" not in decl:
        return None
    if decl["du_graphe"] is not True or set(decl) - {"du_graphe", "max"}:
        raise ValueError("{du_graphe: true, max: <entier>} attendu")
    maximum = decl.get("max", MAX)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= MAX_ADMIS:
        raise ValueError(f"max : un entier de 1 à {MAX_ADMIS}")
    return maximum


def empreinte(sujets: list[str], maximum: int) -> str:
    """Ce dont le regroupement dépend : l'ensemble des sujets, sans leur ordre, et la borne."""
    brut = json.dumps([sorted(sujets), maximum], ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


def _cle(nom: str) -> str:
    plie = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plie).strip("-")


def valider(rep: object, sujets: list[str], maximum: int) -> list[dict]:
    """Les rubriques d'une réponse du modèle, sous la forme de `config_sources.lire_rubriques` :
    [{cle, nom, en, sujets: [{fr, en, mots}]}]. Le vocabulaire d'une rubrique (`mots`) se pose sur son premier sujet :
    c'est par sujet que le classement d'une fiche le lit. Lève ValueError sur une réponse hors contrat."""
    lues = rep.get("rubriques") if isinstance(rep, dict) else None
    if not isinstance(lues, list):
        raise ValueError("réponse illisible")
    if not lues:
        raise ValueError("aucune rubrique")
    if len(lues) > maximum:
        raise ValueError(f"plus de {maximum} rubriques")
    noms: set[str] = set()
    places: set[int] = set()
    out = []
    for r in lues:
        if not isinstance(r, dict) or not isinstance(r.get("mots"), list) or not isinstance(r.get("sujets"), list):
            raise ValueError("réponse illisible")
        nom, en = str(r.get("nom") or "").strip(), str(r.get("en") or "").strip()
        if not nom or not en or not _cle(nom):
            raise ValueError("une rubrique sans nom")
        if len(nom) > NOM_MAX:
            raise ValueError(f"nom trop long : « {nom} »")
        if _cle(nom) in noms:
            raise ValueError(f"le nom « {nom} » paraît deux fois")
        noms.add(_cle(nom))
        if not r["sujets"]:
            raise ValueError(f"la rubrique « {nom} » est sans sujet")
        entrees = []
        for i in r["sujets"]:
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(sujets):
                raise ValueError(f"sujet inconnu : {i!r}")
            if i in places:
                raise ValueError(f"le sujet « {sujets[i]} » est rangé deux fois")
            places.add(i)
            entrees.append({"fr": sujets[i], "en": "", "mots": []})
        entrees[0]["mots"] = [str(m).strip().lower() for m in r["mots"] if str(m).strip()]
        out.append({"cle": _cle(nom), "nom": nom, "en": en, "sujets": entrees})
    if len(places) < len(sujets):
        manque = next(s for i, s in enumerate(sujets) if i not in places)
        raise ValueError(f"le sujet « {manque} » est sans rubrique")
    return out


def _etat(chemin: Path) -> dict:
    try:
        d = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) and isinstance(d.get("rubriques"), list) else {}


def lire(chemin: Path) -> list[dict]:
    """Les rubriques calculées d'un graphe ; [] tant qu'elles ne l'ont pas été, ou si le fichier est illisible."""
    return _etat(chemin).get("rubriques") or []


def calculer(sujets: list[str], maximum: int, appeler: Callable[[str, dict], object], chemin: Path) -> bool:
    """Regroupe les sujets en `maximum` rubriques au plus et les écrit ; rend vrai si elles ont été réécrites. Aucun
    appel sans sujet, ni quand l'empreinte des sujets et de la borne est celle du fichier. Une réponse refusée laisse
    le fichier comme il était."""
    if not sujets:
        return False
    e = empreinte(sujets, maximum)
    if _etat(chemin).get("empreinte") == e:
        return False
    consigne = CONSIGNE.format(
        maximum=maximum, nom_max=NOM_MAX, sujets="\n".join(f"{i}. {s}" for i, s in enumerate(sujets))
    )
    try:
        rubs = valider(appeler(consigne, SCHEMA), sujets, maximum)
    except ValueError as err:
        print(f"  rubriques : réponse refusée ({err}) ; les rubriques d'avant restent")
        return False
    chemin.parent.mkdir(parents=True, exist_ok=True)
    etat = {"empreinte": e, "max": maximum, "date": datetime.now().isoformat(timespec="seconds"), "rubriques": rubs}
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def sujets_declares(graphe: str) -> list[str]:
    from newskit import themes_emergents as te  # noqa: PLC0415 — charge la carte du graphe

    return te.sujets_declares(graphe)


def appeler_modele(graphe: str) -> Callable[[str, dict], object]:
    from newskit import media  # noqa: PLC0415
    from newskit import themes_emergents as te  # noqa: PLC0415

    return lambda consigne, schema: te.appeler_claude(
        consigne, schema=schema, modele=media.modele("analyse"), poste=POSTE, graphe=graphe
    )


def main(argv: list[str] | None = None) -> int:
    from newskit import config_sources as cfg  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    a = ap.parse_args(argv)
    maximum = declaration(cfg.graphe_declare(a.graphe).get("rubriques"))
    if maximum is None:
        print(f"  {a.graphe} : pas de rubriques tirées du graphe (rubriques: {{du_graphe: true}} au registre)")
        return 0
    chemin = cfg.chemins_du_graphe(a.graphe)["base"] / FICHIER
    if calculer(sujets_declares(a.graphe), maximum, appeler_modele(a.graphe), chemin):
        print(f"  {a.graphe} : {len(lire(chemin))} rubrique(s) : " + ", ".join(r["nom"] for r in lire(chemin)))
    else:
        print(f"  {a.graphe} : rubriques inchangées ({len(lire(chemin))})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

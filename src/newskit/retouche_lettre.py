#!/usr/bin/env python3
"""La retouche de la lettre (mainteneur, 03.11.2026) : un appel, après la rédaction et les angles, qui met chaque billet à la
convention du billet (docs/STE_ENQUETE.md, section 6) : titre de la source seule, texte riche et attribué, angle qui
porte un fait neuf, autres sources en lien sur leur fragment, doublons fondus dans le billet plus haut. Une ligne au
registre des coûts (poste lettre, note « retouche »).

Usage :
    python3 -m newskit.retouche_lettre <dossier contenant lettre.json>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from newskit import couts  # noqa: E402
from newskit import lettre as lt  # noqa: E402

CONSIGNE = """Tu retouches les billets d'une lettre de veille en français. Pour chaque billet, rends un objet JSON :
{"n": numéro, "titre": ..., "texte": ..., "angle": ..., "doublon_de": numéro ou -1, "liens": [{"fragment": ..., "url": ...}]}
- titre : donne l'angle, le résultat qui compte pour le lecteur, en mots simples, pas le mécanisme. 70 caractères au plus.
  Le titre et le texte viennent de la source seule (champ source), jamais de l'angle ni de ses preuves : l'angle est notre éclairage, il reste sous le billet.
  Le titre ne prend jamais le fait du complément (angle) : il dit l'angle du texte, le complément garde son fait nouveau.
  Le titre garde la portée de la source : ce qui est protégé, le modèle, le banc d'essai. « Deux chercheurs sécurisent un LLM à 100 % » était faux : seul GPT-5.4 tombe à zéro attaque, sur deux bancs, et c'est l'agent qui est protégé, pas le modèle.
- texte : donne les faits principaux de la source, autant de phrases qu'il faut (chiffres, acteurs, ce qui change) ; un texte d'une phrase est trop pauvre quand la source en dit plus. Retire toute phrase qui n'apporte aucun fait (qui parle, où, quand, déjà dit). Quand une personne parle, elle porte l'attribution dans la phrase du fait : « Selon Sabine Mauderer (Bundesbank), 68 % des entreprises allemandes … » : le nom, puis l'institution entre parenthèses, par son sigle ou son nom court, sans la fonction, jamais une phrase à part « X le dit à Francfort ». Un éditeur qui republie un discours (la BRI pour un discours de banquier central) n'est pas nommé dans le texte. Sinon, garde le texte tel quel. S'il est vide, écris une phrase de 25 mots au plus, tirée de la source, avec « selon » pour une source unique.
- liens : pour chaque autre source que le texte cite (un média, une étude, une annonce), le fragment exact du texte et son adresse, prise dans liens_de_la_source.
- titre : jamais de question ; 70 caractères au plus, espaces compris.
- angle : le complément sous le billet. Il ne répète aucun fait, nombre ou nom déjà dans le texte ou le titre : garde seulement le fait nouveau, en une phrase de 25 mots au plus, tirée de l'angle actuel ou de ses preuves. Exemple : texte « 68 % des entreprises allemandes utilisent l'IA générative ou prévoient de le faire », angle « 68 % des entreprises allemandes utilisent l'IA générative ou le prévoient, mais 7 % seulement l'utilisent à grande échelle » devient « Seules 7 % l'utilisent à grande échelle. » Si rien de nouveau ne reste, rends une chaîne vide. Ni « plutôt que », ni « alors que », ni « tandis que », ni verbe qui juge prêté à un texte (« privilégie », « favorise »).
- doublon_de : le numéro d'un billet plus haut qui traite le même sujet (même événement ou même phénomène, même s'il vient d'une autre source), sinon -1. Le doublon ne sort pas : son fait complète le billet plus haut, dont tu rends le texte complété, avec autant de phrases qu'il faut pour garder chaque fait, et le doublon est fondu dedans.
- interdits partout : « Selon une étude », « Selon un article », « Selon un rapport » (nomme l'auteur ou l'institution, ou dis le fait sans attribution) ; « alors que », « tandis que », « plutôt que » ; « ne … pas X mais Y » ; deux-points de révélation ; question ; slogan ; adjectif d'évaluation.
- lieu : le pays ou la zone où le fait se passe, en nom court : « USA », « UE », « Suisse », « Allemagne », « Royaume-Uni », « Chine », « Japon » ; chaîne vide si le fait n'a pas de lieu (étude générale, produit mondial). Le titre ne répète pas le lieu (pas de « États-Unis : » en tête du titre).
- importance : de 1 à 5, ce que le fait change pour un responsable de la gouvernance de l'IA. 5 : décision, loi, sanction, incident majeur, chiffre neuf d'une institution. 3 : étude ou annonce d'un acteur notable. 1 : anecdote, livre, opinion, portrait, démonstration de produit.
- publicite : true si le billet promeut un produit, un webinaire, un événement ou une offre de son éditeur ; false sinon.
Rends l'objet {"billets": [...]}, un objet par billet.

"""

SCHEMA = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "titre", "texte", "angle", "doublon_de"],
                "properties": {
                    "n": {"type": "integer"},
                    "titre": {"type": "string"},
                    "texte": {"type": "string"},
                    "angle": {"type": "string"},
                    "doublon_de": {"type": "integer"},
                    "importance": {"type": "integer"},
                    "publicite": {"type": "boolean"},
                    "lieu": {"type": "string"},
                    "liens": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["fragment", "url"],
                            "properties": {"fragment": {"type": "string"}, "url": {"type": "string"}},
                        },
                    },
                },
            },
        }
    },
}

LIEN_MD = re.compile(r"\[([^\]]{3,80})\]\((https?://[^)]+)\)")
CLOTURE = re.compile(r"^`{3}(?:json)?\s*|\s*`{3}$")


def _texte_source(x: dict, n: int = 1500) -> tuple[str, str]:
    c = lt.RACINE / (x.get("chemin") or "_")
    if not c.exists():
        return "", ""
    brut = c.read_text(encoding="utf-8")
    s = brut.split("---", 2)[-1] if brut.startswith("---") else brut
    return " ".join(s.split())[:n], brut


def billets(L: dict) -> list[dict]:
    return [*L["faits"], *(x for v in L["aussi"].values() for x in v), *(x for v in L["bref"].values() for x in v)]


def retoucher(L: dict, graphe: str) -> float:
    """Retouche L en place ; rend le coût de l'appel. Sans réponse, la lettre reste telle quelle."""
    items = billets(L)
    if not items or couts.en_limite():
        return 0.0
    lignes = []
    for i, x in enumerate(items):
        src, brut = _texte_source(x)
        lignes.append(
            json.dumps(
                {
                    "n": i,
                    "titre": x.get("titre"),
                    "texte": x.get("paragraphe") or x.get("descriptif") or "",
                    "angle": lt.sans_libelle(x.get("lecture") or ""),
                    "liens_de_la_source": LIEN_MD.findall(brut)[:40],
                    "preuves_de_l_angle": (x.get("preuves") or [])[:4],
                    "source": src,
                },
                ensure_ascii=False,
            )
        )
    args = [
        *couts.CLAUDE_P,
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        lt.MODELE,
        "--json-schema",
        json.dumps(SCHEMA),
    ]
    t0 = time.time()
    try:
        r = subprocess.run(
            args,
            input=CONSIGNE + "\n" + "\n".join(lignes),
            capture_output=True,
            text=True,
            timeout=900,
            cwd=lt.RACINE,
            check=False,
        )
    except subprocess.TimeoutExpired:
        couts.enregistrer(lt.POSTE, graphe, lt.MODELE, None, time.time() - t0, note="retouche : délai")
        return 0.0
    env = couts.enregistrer(lt.POSTE, graphe, lt.MODELE, r, time.time() - t0, note="retouche")
    cout = float(env.get("total_cost_usd") or 0)
    try:
        sortie = env.get("structured_output") or json.loads(CLOTURE.sub("", str(env.get("result") or "").strip()))
        rep = {b["n"]: b for b in sortie["billets"]}
    except (ValueError, KeyError, TypeError):
        return cout
    for i, x in enumerate(items):
        b = rep.get(i)
        if not b:
            continue
        x["titre"] = b["titre"] or x["titre"]
        if b.get("texte"):
            x["paragraphe" if "paragraphe" in x else "descriptif"] = b["texte"]
        x["lecture"] = b.get("angle", "")
        x["lieu"] = b.get("lieu", "")
        if b.get("liens"):
            x["liens"] = {l_["fragment"]: l_["url"] for l_ in b["liens"]}
    sortants = set()
    for i, b in rep.items():
        k = b.get("doublon_de", -1)
        if 0 <= k < i < len(items):
            d = items[i]
            items[k].setdefault("sources_plus", []).append(
                d.get("source") or {"url": d.get("url"), "editeur": "", "date": "", "titre": d["titre"]}
            )
            sortants.add(id(d))
    for i, b in rep.items():
        if b.get("publicite") and i < len(items):
            sortants.add(id(items[i]))
        elif i < len(items):
            items[i]["importance"] = b.get("importance", 3)
    for cle in ("aussi", "bref"):
        L[cle] = {g: v for g, v in ((g, [x for x in v if id(x) not in sortants]) for g, v in L[cle].items()) if v}
    L["faits"] = [x for x in L["faits"] if id(x) not in sortants]
    reordonner_une(L)
    L["cout_lettre"] = (L.get("cout_lettre") or 0) + cout
    return cout


def _editeur(x: dict) -> str:
    return lt.plier(str((x.get("source") or {}).get("editeur") or ""))


SCHEMA_LIEUX = {
    "type": "object",
    "required": ["lieux"],
    "properties": {
        "lieux": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "lieu", "titre"],
                "properties": {"n": {"type": "integer"}, "lieu": {"type": "string"}, "titre": {"type": "string"}},
            },
        }
    },
}


def attribuer_lieux(lettres: list[dict], graphe: str) -> float:
    """Le lieu des billets de la une de lettres déjà retouchées, en un appel ; rend le coût."""
    items = [x for L in lettres for x in L["faits"]]
    if not items:
        return 0.0
    consigne = (
        "Pour chaque billet, donne le pays ou la zone où le fait se passe, en nom court (« USA », « UE », "
        "« Suisse », « Allemagne », « Royaume-Uni », « Chine », « Japon »), ou une chaîne vide si le fait n'a "
        "pas de lieu ; et le titre réécrit sans ce lieu (ni « au Japon », ni « La Suisse » en sujet quand le lieu "
        "le dit déjà : « Suisse » + « Mesures sectorielles et codes de conduite pour l'IA »), même sens, 70 "
        'caractères au plus, sans deux-points. Rends {"lieux": [{"n": numéro, "lieu": ..., "titre": ...}]}.\n\n'
    )
    lignes = [
        json.dumps(
            {
                "n": i,
                "titre": x["titre"],
                "texte": x.get("paragraphe", "")[:400],
                "editeur": (x.get("source") or {}).get("editeur", ""),
            },
            ensure_ascii=False,
        )
        for i, x in enumerate(items)
    ]
    args = [
        *couts.CLAUDE_P,
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
        "--model",
        lt.MODELE,
        "--json-schema",
        json.dumps(SCHEMA_LIEUX),
    ]
    t0 = time.time()
    r = subprocess.run(
        args,
        input=consigne + "\n".join(lignes),
        capture_output=True,
        text=True,
        timeout=600,
        cwd=lt.RACINE,
        check=False,
    )
    env = couts.enregistrer(lt.POSTE, graphe, lt.MODELE, r, time.time() - t0, note="lieux de la une")
    sortie = env.get("structured_output") or json.loads(CLOTURE.sub("", str(env.get("result") or "").strip()))
    for o in sortie["lieux"]:
        if 0 <= o["n"] < len(items):
            items[o["n"]]["lieu"] = o["lieu"]
            if o.get("titre"):
                items[o["n"]]["titre"] = o["titre"]
    return float(env.get("total_cost_usd") or 0)


UNE_MAX = 5  # la une : de 1 à 5 faits, selon ce que les contenus justifient (mainteneur, 03.11.2026)
UNE_SEUIL = 4  # importance qu'il faut pour la une au-delà du premier fait
PAR_RUBRIQUE_UNE = 2


def reordonner_une(L: dict, maximum: int = UNE_MAX, seuil: int = UNE_SEUIL) -> None:
    """La une prend les billets d'importance `seuil` au moins (note du modèle), parmi la une, les rubriques et les
    brèves, `maximum` au plus, un éditeur chacun et PAR_RUBRIQUE_UNE par rubrique ; au moins un, le mieux noté, même
    sous le seuil. « 3 semble parfois artificiel, mais possible d'aller jusqu'à 5 » (mainteneur, 03.11.2026). Les billets
    qui quittent la une passent dans leur rubrique, et l'inverse."""
    pool = [*L["faits"], *(x for v in L["aussi"].values() for x in v)]
    brefs = [(g, x) for g, v in L["bref"].items() for x in v if x.get("descriptif") and x.get("chemin")]
    for g, x in brefs:  # une brève notée plus haut peut monter : elle prend sa source et sa rubrique
        x.setdefault("groupe", g)
        if "source" not in x:
            f = lt.charger_fiches([x["chemin"]]).get(x["chemin"])
            if f:
                x["source"] = lt._source(f)
    pool += [x for _, x in brefs if "source" in x]
    if not pool or not any("importance" in x for x in pool):
        return
    ordre = sorted(range(len(pool)), key=lambda i: (-pool[i].get("importance", 3), i))
    rub = lambda x: x.get("rubrique") or x.get("groupe") or ""  # noqa: E731
    une: list[dict] = []
    for i in ordre:
        x = pool[i]
        if len(une) >= maximum or (une and x.get("importance", 3) < seuil):
            break
        if any(_editeur(x) and _editeur(x) == _editeur(u) for u in une):
            continue
        if sum(rub(x) == rub(u) for u in une) >= PAR_RUBRIQUE_UNE:
            continue
        une.append(x)
    faits, aussi = [], {}
    for x in une:
        if "paragraphe" not in x:
            x["paragraphe"] = x.pop("descriptif", "")
            x["rubrique"] = x.pop("groupe", "")
            x.setdefault("gras", [])
        faits.append(x)
    montees = {id(x) for x in une}
    L["bref"] = {g: v for g, v in ((g, [x for x in v if id(x) not in montees]) for g, v in L["bref"].items()) if v}
    for x in pool:
        if x in une or any(x is y for _, y in brefs):
            continue
        if "descriptif" not in x:
            x["descriptif"] = x.pop("paragraphe", "")
            x["groupe"] = x.get("rubrique", "")
        aussi.setdefault(x["groupe"], []).append(x)
    L["faits"] = faits
    L["aussi"] = lt.trier_groupes(aussi, L.get("ordre") or [])


if __name__ == "__main__":
    d = sys.argv[1]
    L = json.load(open(d + "/lettre.json", encoding="utf-8"))
    print("retouche", round(retoucher(L, L["graphe"]), 3))
    json.dump(L, open(d + "/lettre.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

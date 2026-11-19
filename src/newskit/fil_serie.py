#!/usr/bin/env python3
"""Le billet groupé d'une source prolixe.

Une source qui publie chaque jour plusieurs articles sur un même thème (le commentaire d'un texte, article par
article) noie le fil : chaque article y faisait son billet. Elle déclare sa série au registre, `serie: {theme, heure}`.
Ses fiches attendent alors l'heure dite, puis le fil écrit un seul billet pour le jour : un titre, un chapeau et, pour
chaque article du thème, un titre court, deux ou trois phrases et son lien. Un article hors du thème suit la voie
ordinaire, comme celui de toute source. Les fiches ne changent pas : chaque article entre au graphe.

L'état vit dans le fil, par source : `F["series"][cle] = {"jour", "hors", "repris"}` — le jour du dernier billet
groupé, les fiches jugées hors du thème, l'heure du dernier essai sans billet.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse

from newskit import config_sources as cfg

ARTICLE_MAX = 3000  # caractères de chaque article dans la consigne : le début porte le sujet
TEXTE_MOTS = 90  # mots du texte d'un article, au plus (deux ou trois phrases)
HORS_GARDES = 400  # fiches hors thème retenues par source
PAR_GROUPE = 25  # articles d'un billet groupé, au plus ; les suivants attendent le billet du lendemain
REPRISE = 60  # minutes avant un nouvel essai, après une réponse sans billet
FORMAT = "%Y-%m-%d %H:%M"

_TXT = {"type": "string"}
SCHEMA = {
    "type": "object",
    "required": ["groupe", "articles"],
    "properties": {
        "groupe": {
            "type": "object",
            "required": ["titre", "texte", "titre_en", "texte_en"],
            "properties": {k: _TXT for k in ("titre", "texte", "titre_en", "texte_en", "rubrique")},
        },
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "dans_le_theme"],
                "properties": {
                    "n": {"type": "integer"},
                    "dans_le_theme": {"type": "boolean"},
                    **{k: _TXT for k in ("titre_court", "texte", "titre_court_en", "texte_en")},
                },
            },
        },
    },
}

CONSIGNE = """Tu écris le billet groupé du jour d'un fil d'actualité sur {nom}, en français et en anglais. La source
« {source} » publie une série : {theme}. Les fiches ci-dessous sont ses articles du jour.

Pour chaque fiche, rends {{"n": numéro, "dans_le_theme": vrai ou faux, "titre_court": ..., "texte": ...,
"titre_court_en": ..., "texte_en": ...}}.
- dans_le_theme : vrai si l'article appartient à la série décrite ; faux s'il traite un autre sujet. Faux : laisse
  les autres champs vides.
- titre_court : ce que l'article couvre, en {mots} mots au plus, sans le nom de la source.
- texte : deux ou trois phrases, tirées de la source seule, qui se comprennent sans le titre.
- titre_court_en, texte_en : les mêmes en anglais, écrits d'après la source, pas d'après le français.

Rends aussi "groupe" : {{"titre": ..., "texte": ..., "titre_en": ..., "texte_en": ..., "rubrique": ...}} pour les
seuls articles du thème.
- titre : {max} caractères au plus, sans question ; il nomme la source, dit le nombre d'articles et ce qu'ils couvrent.
- texte : le chapeau, de 30 à 70 mots : ce que la série traite ce jour, et ce que plusieurs articles ont en commun.
- titre_en, texte_en : les mêmes en anglais.
- rubrique : l'une de : {rubriques}.
{regles}
Fiches :
{fiches}
"""


def _etat(F: dict, cle: str) -> dict:
    return (F.get("series") or {}).get(cle) or {}


def partager(cands: list[dict], F: dict) -> tuple[list[dict], dict[str, dict]]:
    """Sépare les fiches candidates : celles de la voie ordinaire, et par source celles d'une série
    ({cle: {"source", "fiches"}}). Une fiche jugée hors du thème de sa série reste ordinaire."""
    ordinaires, en_serie = [], {}
    for f in cands:
        s = cfg.serie_du_chemin(f["chemin"])
        if s is None or f["chemin"] in (_etat(F, s.cle).get("hors") or []):
            ordinaires.append(f)
        else:
            en_serie.setdefault(s.cle, {"source": s, "fiches": []})["fiches"].append(f)
    return ordinaires, en_serie


def dues(en_serie: dict[str, dict], F: dict, quand: str) -> list[str]:
    """Les sources dont le billet groupé est dû à `quand` (« AAAA-MM-JJ HH:MM ») : l'heure de la série est passée,
    le jour n'a pas son billet, et le dernier essai sans billet date de plus de REPRISE minutes."""
    out = []
    for cle, x in en_serie.items():
        e = _etat(F, cle)
        if quand[11:] < x["source"].serie["heure"] or e.get("jour") == quand[:10]:
            continue
        repris = e.get("repris")
        if repris and dt.datetime.strptime(quand, FORMAT) - dt.datetime.strptime(repris, FORMAT) < dt.timedelta(
            minutes=REPRISE
        ):
            continue
        out.append(cle)
    return out


def grouper(fiches: list[dict], source, graphe: str) -> tuple[dict, float]:
    """L'appel au modèle : le classement de chaque fiche dans le thème, son titre court et son texte, et le billet du
    groupe, dans les deux langues. Rend la réponse brute et son coût."""
    from newskit import fil  # noqa: PLC0415 — fil appelle ce module
    from newskit import lettre as lt  # noqa: PLC0415

    inv = CONSIGNE.format(
        nom=cfg.graphe_declare(graphe).get("libelle") or graphe,
        source=source.nom,
        theme=source.serie["theme"],
        mots=10,
        max=fil.TITRE_MAX,
        rubriques=", ".join(r["nom"] for r in cfg.rubriques(graphe) or []) or "(aucune)",
        regles=lt.regles(graphe),
        fiches="\n".join(
            json.dumps(
                {"n": i, "date": f.get("date"), "titre": f["titre"], "source": fil.extrait(f, ARTICLE_MAX)},
                ensure_ascii=False,
            )
            for i, f in enumerate(fiches)
        ),
    )
    return fil.demander(inv, SCHEMA, graphe, "série")


def _dit(x) -> str:
    return " ".join(str(x or "").split())


def adresse(source, fiches: list[dict], jour: str) -> str:
    """L'adresse du billet groupé : le site de la source, avec un fragment propre à la source et au jour. Elle fait
    l'identité du billet et de sa page ; elle ne se lit pas sur le réseau."""
    u = urllib.parse.urlsplit(fiches[0]["url"])
    return f"{u.scheme}://{u.netloc}/#{source.cle}-{jour}"


def billet(rep: dict, fiches: list[dict], source, quand: str, rubs: list[dict]) -> tuple[dict | None, list[dict]]:
    """Le billet groupé d'après la réponse du modèle, et les fiches hors du thème. Sans billet (réponse incomplète,
    champ vide dans une langue, titre trop long, rubrique hors de la liste) : None, et le passage réessaiera. Un seul
    article du thème ne fait pas un groupe : il rejoint la voie ordinaire."""
    from newskit import fil  # noqa: PLC0415

    par_n = {a.get("n"): a for a in rep.get("articles") or [] if isinstance(a, dict)}
    if set(par_n) != set(range(len(fiches))):
        return None, []
    hors = [f for i, f in enumerate(fiches) if not par_n[i].get("dans_le_theme")]
    dans = [(f, par_n[i]) for i, f in enumerate(fiches) if par_n[i].get("dans_le_theme")]
    if len(dans) < 2:
        return None, hors + [f for f, _ in dans]
    g = {k: _dit((rep.get("groupe") or {}).get(k)) for k in ("titre", "texte", "titre_en", "texte_en", "rubrique")}
    articles = [
        {
            "chemin": f["chemin"],
            "url": f["url"],
            "titre": _dit(a.get("titre_court")),
            "texte": _dit(a.get("texte")),
            "en": {"titre": _dit(a.get("titre_court_en")), "texte": _dit(a.get("texte_en"))},
        }
        for f, a in dans
    ]
    dits = [g["titre"], g["texte"], g["titre_en"], g["texte_en"]]
    dits += [x for a in articles for x in (a["titre"], a["texte"], a["en"]["titre"], a["en"]["texte"])]
    titres = (g["titre"], g["titre_en"])
    if not all(dits) or any(len(t) > fil.TITRE_MAX or "?" in t for t in titres):
        return None, hors
    if any(len(t.split()) > TEXTE_MOTS for a in articles for t in (a["texte"], a["en"]["texte"])):
        return None, hors
    rubriques = [f.get("rubrique") for f, _ in dans if f.get("rubrique")]
    rubrique = max(set(rubriques), key=rubriques.count) if rubriques else g["rubrique"]
    if rubs and rubrique not in {r["nom"] for r in rubs}:
        return None, hors
    jour = quand[:10]
    url = adresse(source, [f for f, _ in dans], jour)
    return {
        "titre": g["titre"],
        "descriptif": g["texte"],
        "rubrique": rubrique or None,
        "chemin": f"serie/{source.cle}/{jour}",  # pas de fichier : les étapes qui lisent la source le sautent
        "url": url,
        "versions": {},
        "source": {"titre": source.nom, "editeur": source.nom, "date": jour, "url": url.split("#")[0]},
        "date": jour,
        "langue": fil.langue_source(dans[0][0]),
        "detection": fil.DETECTION,
        "ajoute": quand,
        "lecture": "",
        "rapproche": True,
        "contexte_juge": True,
        "en": {"titre": g["titre_en"], "texte": g["texte_en"], "tenu": True},
        "articles": articles,
    }, hors


def noter(F: dict, cle: str, quand: str, b: dict | None, hors: list[dict]) -> None:
    """L'état de la série après un essai : les fiches hors thème, puis le jour du billet, ou l'heure de l'essai resté
    sans billet."""
    e = F.setdefault("series", {}).setdefault(cle, {})
    e["hors"] = ((e.get("hors") or []) + [f["chemin"] for f in hors])[-HORS_GARDES:]
    if b is not None:
        e["jour"] = quand[:10]
        e.pop("repris", None)
    else:
        e["repris"] = quand


def passer(a_faire: list[str], en_serie: dict[str, dict], F: dict, graphe: str, quand: str, rubs: list[dict]):
    """Écrit le billet groupé de chaque source due et le fait entrer au fil à `quand`, sans passer par la pile : il
    paraît à l'heure de la série. Le titre court et le texte de chaque article entrent à l'en-tête de sa fiche, donc
    au graphe. Rend les fiches lues (celles du billet), les fiches hors thème, à rédiger par la voie ordinaire, et le
    coût."""
    from newskit import fil  # noqa: PLC0415

    lues, hors_theme, cout = set(), [], 0.0
    for cle in a_faire:
        source, fiches = en_serie[cle]["source"], en_serie[cle]["fiches"][:PAR_GROUPE]
        rep, c = grouper(fiches, source, graphe)
        cout += c
        b, hors = billet(rep, fiches, source, quand, rubs)
        noter(F, cle, quand, b, hors)
        hors_theme += hors
        if b is None:
            continue
        fil._entrer(F, [b], quand, fil.fil_max(graphe))
        for a in b["articles"]:
            fil.enrichir({"chemin": a["chemin"], "titre": a["titre"], "descriptif": a["texte"]})
            lues.add(a["chemin"])
    return lues, hors_theme, cout

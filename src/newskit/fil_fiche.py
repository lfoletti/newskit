"""Le texte long de la fiche d'un billet (mainteneur, 06.11.2026 : « fiche d'abord, avec un texte peut-être un peu plus long
et partageable », puis « de 120 à 180 mots c'est bien », puis le 07.11.2026 : « de 80 à 150 mots. instruis l'auteur
de ne pas artificiellement diluer l'information […] si c'est court, c'est court »).

Le billet du fil a un titre et un chapeau de 30 à 70 mots. Sa fiche, au téléphone, montre un texte plus long, de
MOTS_MIN à MOTS_MAX mots, écrit après la relecture du billet, d'après la source et en partant du titre et du chapeau
tenus, aux règles de la lettre (lt.REGLES). Il passe le contrôle de forme sans modèle (longueur, phrases, paragraphes)
puis la relecture adverse du billet (fil.juger : faits absents de la source, clarté). Refusé, il se réécrit avec ses
reproches, jusqu'à ESSAIS essais (mainteneur, 07.11.2026 : « au moins 3 essais ») ; après, la fiche montre le chapeau. Le
champ `fiche` du billet : {texte, tenu: vrai}, ou {tenu: faux, reproches, essais}. Un texte resté sans verdict (appel
échoué) reste à écrire.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from newskit import config_sources as cfg
from newskit import extrait as ex
from newskit import fil
from newskit import lettre as lt
from newskit import publier_fil

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)

MOTS_MIN, MOTS_MAX = 80, 150
PAR_PASSAGE = 15  # textes de fiche écrits par passage, au plus
ESSAIS = 3  # essais d'un texte refusé, ses reproches donnés au suivant (mainteneur, 07.11.2026)
# 30 mots par phrase pour le texte long, 25 ailleurs (mainteneur, 07.11.2026 : 136 phrases de 26 à 31 mots refusées)
MOTS_PAR_PHRASE = 30
# Les textes de fiche ont leur plafond du jour, pris dans celui du fil : les billets neufs passent d'abord (mainteneur,
# 07.11.2026 ; le matin, les réécritures de textes longs avaient vidé le budget du fil sans un billet). F["depense_fiche"].
FICHE_USD_JOUR = 10.0  # 3 $ jusqu'au 08.11.2026 : les fiches à éléments s'écrivent avec Opus (mainteneur)
SOURCE_MAX = 3000  # caractères de la source : ceux que lit aussi le relecteur (fil.juger)

REDACTION = """Tu écris le texte de la fiche des billets d'un fil d'actualité sur {nom}, en français. Le lecteur a lu
le titre et le chapeau ; il ouvre la fiche pour en savoir plus, et peut la partager. Pour chaque billet tu reçois le
titre, le chapeau et la source. Rends {{"n": numéro, "texte": ...}}.
- texte : de {mini} à {maxi} mots, en deux ou trois paragraphes séparés par une ligne vide ;
- le premier paragraphe dit le fait du titre et du chapeau, sans les recopier mot pour mot ;
- les suivants donnent ce que la source ajoute : les acteurs et leur rôle, les chiffres, les étapes, les positions,
  la suite annoncée ; chaque phrase porte un fait de la source ;
- tout vient de la source : aucun nom, aucun nombre, aucun fait qu'elle ne dit pas ;
- n'étire jamais l'information pour allonger le texte : si la source dit peu, le texte est court, près de {mini} mots ;
  aucune redite du chapeau, aucune phrase de contexte général, aucune reformulation d'un fait déjà dit ;
- le texte et le titre disent le même fait ;
- un billet qui porte « a_corriger » a déjà eu un texte refusé pour ces raisons : écris-en un autre qui les évite.
Les règles de la lettre d'information :
{regles}Rends l'objet {{"billets": [...]}}.

Billets :
{billets}"""
SCHEMA = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "texte"],
                "properties": {"n": {"type": "integer"}, "texte": {"type": "string"}},
            },
        }
    },
}


def existe(chemin: str) -> bool:
    return (RACINE / chemin).exists()


def _source_de(f: dict) -> str:
    return fil._source_de(f)


def a_ecrire(F: dict) -> list[dict]:
    """Les billets qui attendent le texte de leur fiche : la pile d'abord, puis les billets de la copie publique ; ni
    refusés par la relecture, ni déjà écrits, et dont la fiche se lit."""
    vus, out = set(), []
    for b in (F.get("pile") or []) + (F.get("billets") or [])[: publier_fil.MAX]:
        f = b.get("fiche")
        fini = f is not None and (f.get("tenu") is not False or f.get("essais", 1) >= ESSAIS)
        if b.get("tenu") is False or fini or b["chemin"] in vus or not existe(b["chemin"]):
            continue
        vus.add(b["chemin"])
        out.append(b)
    return out[:PAR_PASSAGE]


def _net(texte: str) -> str:
    """Le texte, ses paragraphes gardés (ligne vide), les blancs de chaque paragraphe réduits."""
    return "\n\n".join(" ".join(p.split()) for p in str(texte or "").split("\n\n") if p.strip())


def forme(texte: str, f: dict) -> list[str]:
    """Sans modèle : les écarts du texte à sa mesure (mots), aux règles de phrase et de paragraphe de la lettre, et la
    formule générique."""
    ec = []
    mots = len(texte.split())
    if not MOTS_MIN <= mots <= MOTS_MAX:
        ec.append(f"{mots} mots, de {MOTS_MIN} à {MOTS_MAX}")
    for p in texte.split("\n\n"):
        phrases = lt.phrases(p)
        if len(phrases) > lt.PHRASES_PAR_PARAGRAPHE:
            ec.append(f"paragraphe de {len(phrases)} phrases, {lt.PHRASES_PAR_PARAGRAPHE} au plus")
        ec += [
            f"phrase de {len(ph.split())} mots, {MOTS_PAR_PHRASE} au plus"
            for ph in phrases
            if len(ph.split()) > MOTS_PAR_PHRASE
        ]
    generique = lt.formule_generique(texte, _source_de(f))
    if generique:
        ec.append(generique)
    return ec


def refus(b: dict, reproches: list[str]) -> dict:
    """Le refus d'un texte, compté : un refus d'avant les essais (07.11.2026) compte pour un."""
    return {
        "tenu": False,
        "reproches": reproches,
        "essais": (b.get("fiche") or {}).get("essais", 0 if "fiche" not in b else 1) + 1,
    }


# Le format validé le 08.11.2026 (mainteneur : « la fiche met en valeur l'extraction, ce n'est pas une réécriture ») : un
# résumé, une catégorie et des éléments, chacun prouvé par une citation mot pour mot que la page ne montre pas. Il vaut pour
# les graphes qui déclarent `arbre_fiches` (l'arbre des catégories) et pour les seules fiches pas encore écrites.
MODELE_ELEMENTS = "claude-opus-5-5"
ELEMENTS_MIN = 2  # éléments prouvés au moins
REDACTION_ELEMENTS = """Tu rédiges la fiche de recension d'articles pour un fil d'actualité sur {nom}, en français et en anglais.
Tout vient de la source : aucun nom, aucun nombre, aucun fait qu'elle ne dit pas. Pour chaque billet, rends :
- n : son numéro ;
- categorie : une feuille de l'arbre, sous la forme racine:feuille ;
- resume, resume_en : 30 à 70 mots ; le résumé porte ce que l'article apporte de plus que le fait de son titre ;
- elements : un élément par champ de la catégorie ; un champ sans contenu est omis. Pour chacun : champ et champ_en, le nom
  du champ pour un lecteur, d'un ou deux mots ; texte et texte_en, qui nomment qui fait ou affirme quoi, à propos de quoi,
  avec les noms propres, les intitulés de textes, les chiffres et les dates de l'article ; citation, recopiée mot pour mot
  de la source, 300 caractères au plus, qui porte l'élément.
Un élément se comprend seul, sans le titre. L'opinion de l'auteur reste attribuée. Pas de formule générale.
Un billet qui porte « a_corriger » a déjà eu une fiche refusée pour ces raisons : écris-en une autre qui les évite.
Les règles de la lettre d'information :
{regles}Arbre des catégories :
{arbre}
Rends l'objet {{"billets": [...]}}.

Billets :
{billets}"""
_TXT = {"type": "string"}
SCHEMA_ELEMENTS = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "categorie", "resume", "resume_en", "elements"],
                "properties": {
                    "n": {"type": "integer"},
                    "categorie": _TXT,
                    "resume": _TXT,
                    "resume_en": _TXT,
                    "elements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["champ", "champ_en", "texte", "texte_en", "citation"],
                            "properties": {k: _TXT for k in ("champ", "champ_en", "texte", "texte_en", "citation")},
                        },
                    },
                },
            },
        }
    },
}


def arbre(graphe: str) -> dict | None:
    """Les feuilles de l'arbre déclaré par le graphe : {racine:feuille: (nom fr, nom en, champs)} ; None sans arbre."""
    chemin = cfg.graphe_declare(graphe).get("arbre_fiches")
    if not chemin:
        return None
    A = yaml.safe_load((RACINE / chemin).read_text(encoding="utf-8"))
    return {
        f"{r['cle']}:{x['cle']}": (f"{r['fr']} : {x['fr']}", f"{r['en']}: {x['en']}", x.get("champs") or r["champs"])
        for r in A["racines"]
        for x in r["feuilles"]
    }


def corps_de(b: dict) -> str:
    return fil.extrait(b, SOURCE_MAX)


def _valider(x: dict, corps: str, feuilles: dict) -> tuple[dict | None, list[str]]:
    """La fiche d'une réponse du modèle, ses éléments prouvés seuls gardés ; ou None et les reproches."""
    cat = feuilles.get(x.get("categorie"))
    if cat is None:
        return None, [f"catégorie hors de l'arbre : {x.get('categorie')}"]
    if not all(str(x.get(k) or "").strip() for k in ("resume", "resume_en")):
        return None, ["résumé absent dans une langue"]
    bons = [e for e in x.get("elements") or [] if ex.atteste(e["citation"][:300], corps)]
    if len(bons) < ELEMENTS_MIN:
        return None, [f"{len(bons)} éléments prouvés par une citation exacte, {ELEMENTS_MIN} au moins"]
    return {
        "texte": "\n\n".join(e["texte"] for e in bons),
        "resume": x["resume"],
        "categorie": cat[0],
        "elements": [{"champ": e["champ"], "texte": e["texte"], "citation": e["citation"][:300]} for e in bons],
        "en": {
            "resume": x["resume_en"],
            "categorie": cat[1],
            "elements": [{"champ": e["champ_en"], "texte": e["texte_en"]} for e in bons],
        },
        "tenu": True,
    }, []


def _lu(f: dict) -> str:
    """Résumé et éléments d'une langue, pour le relecteur."""
    return "\n".join([f["resume"], *(f"{e['champ']} : {e['texte']}" for e in f["elements"])])


def _reproches(v: dict) -> list[str]:
    return [
        *(f"absent de la source : {a}" for a in v["absents"]),
        *([v["motif"] or "pas clair"] if not v["clair"] else []),
    ]


def _etoffer_elements(F: dict, graphe: str, feuilles: dict) -> float:
    billets = a_ecrire(F)
    fiches = lt.charger_fiches([b["chemin"] for b in billets]) if billets else {}
    lot = [(b, fiches[b["chemin"]]) for b in billets if b["chemin"] in fiches]
    if not lot:
        return 0.0
    corps = [corps_de(b) for b, _ in lot]
    r, cout = fil.demander(
        REDACTION_ELEMENTS.format(
            nom=cfg.graphe_declare(graphe).get("libelle") or graphe,
            regles=lt.REGLES,
            arbre="\n".join(f"- {k} ; champs : {', '.join(v[2])}" for k, v in feuilles.items()),
            billets="\n".join(
                json.dumps(
                    {
                        "n": n,
                        "titre": b["titre"],
                        "source": corps[n],
                        **({"a_corriger": b["fiche"]["reproches"]} if (b.get("fiche") or {}).get("reproches") else {}),
                    },
                    ensure_ascii=False,
                )
                for n, (b, _) in enumerate(lot)
            ),
        ),
        SCHEMA_ELEMENTS,
        graphe,
        "fiche",
        modele=MODELE_ELEMENTS,
    )
    pretes = {}
    for x in r.get("billets") or []:
        n = x.get("n")
        if not isinstance(n, int) or not 0 <= n < len(lot):
            continue
        f, ec = _valider(x, corps[n], feuilles)
        if f is None:
            lot[n][0]["fiche"] = refus(lot[n][0], ec)
        else:
            pretes[n] = f
    if not pretes:
        return cout
    rangs = list(pretes)
    paires = [
        p
        for n in rangs
        for p in ((lot[n][0]["titre"], _lu(pretes[n]), *lot[n]), (lot[n][0]["titre"], _lu(pretes[n]["en"]), *lot[n]))
    ]
    verdicts, c = fil.juger(paires, graphe)
    for k, n in enumerate(rangs):
        fr, en = verdicts.get(2 * k), verdicts.get(2 * k + 1)
        if fr is None or en is None:
            continue  # sans verdict : reste à écrire
        reproches = _reproches(fr) + _reproches(en)
        lot[n][0]["fiche"] = refus(lot[n][0], reproches) if reproches else pretes[n]
    return cout + c


def etoffer(F: dict, graphe: str, reste: float) -> float:
    """Les textes de fiche d'un passage, sous `reste` et sous FICHE_USD_JOUR ; rend leur coût, inscrit au jour."""
    jour = time.strftime("%Y-%m-%d")
    reste = min(reste, FICHE_USD_JOUR - float((F.get("depense_fiche") or {}).get(jour) or 0))
    if reste < 0.10:
        return 0.0
    feuilles = arbre(graphe)
    cout = _etoffer(F, graphe) if feuilles is None else _etoffer_elements(F, graphe, feuilles)
    d = F.setdefault("depense_fiche", {})
    d[jour] = float(d.get(jour) or 0) + cout
    return cout


def _etoffer(F: dict, graphe: str) -> float:
    billets = a_ecrire(F)
    fiches = lt.charger_fiches([b["chemin"] for b in billets]) if billets else {}
    lot = [(b, fiches[b["chemin"]]) for b in billets if b["chemin"] in fiches]
    if not lot:
        return 0.0
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    r, cout = fil.demander(
        REDACTION.format(
            nom=nom,
            mini=MOTS_MIN,
            maxi=MOTS_MAX,
            regles=lt.REGLES,
            billets="\n".join(
                json.dumps(
                    {
                        "n": n,
                        "titre": b["titre"],
                        "chapeau": b.get("descriptif") or "",
                        "source": fil.extrait(b, SOURCE_MAX),
                        **({"a_corriger": b["fiche"]["reproches"]} if (b.get("fiche") or {}).get("reproches") else {}),
                    },
                    ensure_ascii=False,
                )
                for n, (b, _) in enumerate(lot)
            ),
        ),
        SCHEMA,
        graphe,
        "fiche",
    )
    textes = {}
    for x in r.get("billets") or []:
        t = _net(x.get("texte"))
        if isinstance(x.get("n"), int) and 0 <= x["n"] < len(lot) and t:
            textes[x["n"]] = t
    a_juger = []
    for n, t in textes.items():
        ec = forme(t, lot[n][1])
        if ec:
            lot[n][0]["fiche"] = refus(lot[n][0], ec)
        else:
            a_juger.append(n)
    if not a_juger:
        return cout
    verdicts, c = fil.juger([(lot[n][0]["titre"], textes[n], lot[n][0], lot[n][1]) for n in a_juger], graphe)
    cout += c
    for k, n in enumerate(a_juger):
        v = verdicts.get(k)
        if v is None:
            continue  # sans verdict : reste à écrire
        reproches = [
            *(f"absent de la source : {a}" for a in v["absents"]),
            *([v["motif"] or "pas clair"] if not v["clair"] else []),
        ]
        lot[n][0]["fiche"] = refus(lot[n][0], reproches) if reproches else {"texte": textes[n], "tenu": True}
    return cout

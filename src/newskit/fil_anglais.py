"""La version anglaise du fil (mainteneur, 06.11.2026 : « prépare la version EN de la page d'accueil, y compris le fil
d'actualités » ; « les traductions doivent venir des textes originaux, pas du fil. ce serait des dérivés de dérivés...
pas bon pour la qualité »).

Le billet anglais s'écrit d'après la source de la fiche, jamais d'après le billet français : le rédacteur ne voit ni
le titre ni le texte français. Il passe la même relecture adverse que le billet français, en anglais (faits absents de
la source, titre qui ne se comprend pas seul, pays manquant), et un billet refusé est réécrit d'après la source,
REECRITURES fois au plus. Le champ `en` du billet : {titre, texte, tenu: vrai} ; refusé à chaque essai, {tenu: faux,
reproches} et la page anglaise ne le montre pas. Un billet sans réponse du modèle reste à écrire.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from newskit import config_sources as cfg
from newskit import fil
from newskit import lettre as lt
from newskit import publier_fil

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)

PAR_PASSAGE = 25  # billets anglais écrits par passage, au plus
REECRITURES = fil.REECRITURES
TITRE_MAX = fil.TITRE_MAX
MOTS_TITRE = lt.MOTS_TITRE
MOTS_PAR_PHRASE = lt.MOTS_PAR_PHRASE
SOURCE_MAX = 3000  # caractères de la source donnés au rédacteur et au relecteur

LECTEUR = (
    "The reader is a manager in a company, who reads English, is neither a lawyer nor an engineer and does not "
    "know the topic in advance."
)
REGLES = f"""Plain English: {MOTS_PAR_PHRASE} words at most per sentence; one subject per sentence, active voice, present or
past tense; the conditional only when the source itself presents the fact as a hypothesis, or, in a title, for a fact put forward by one source and not established; one word, one meaning: keep
the word of the source, no synonym for variety; numbers in figures with their unit ("3,000 start-ups", "$6.43
billion"); an acronym is spelled out at its first mention, the acronym in brackets, except AI, EU, AI Act, GDPR, OECD.
Attribution in a title: « Anthropic may have crossed … » when the fact matters more than who puts it forward and is not established; « For Tencent's chief AI scientist, AI could soon … » for an opinion; « According to X, … » for a fact X claims; never « X says … ».
Vocabulary of evidence: attribution verbs say, write, announce, claim, according to; never admit, reveal, denounce,
confess, pretend. A preprint or a vendor's page about its own product gives claimed results: every result figure is
attributed ("according to the authors", "the company claims"). No evaluative adjective outside a quotation (historic,
unprecedented, major, landmark, crucial, groundbreaking, controversial). Absolute dates ("on 2 November 2026"), never
"yesterday", "last week", "recently". A person is named with their role at first mention. No intention attributed to
an actor: what they did or said.
Form: sober news prose. No list of three for rhythm (a list stays when the source lists these items); no colon that
stages a revelation; no "not X, but Y"; no slogan, punchline, metaphor or personification ("the law opens the door");
no filler ("it is worth noting", "in a context of", "a strong signal", "key" as an adjective).
"""
REDACTION = (
    """You write the English news items of a news feed on {nom}. """
    + LECTEUR
    + """ Each item comes with its
source only: write from the source, every name, number and fact comes from it. Return {{"n": number, "titre": ...,
"texte": ...}} for each item.
- titre: the result that matters to the reader, in plain words, in one simple sentence with a verb; it keeps the scope
  of the source; it is understood alone, by a reader who does not know the topic; it names the person the source names;
  a little-known person or organisation is followed by its role; no question; {mots} words and {max} characters at most.
- texte: the lead of the fact, 30 to 70 words, 4 sentences at most, 2 numbers at most besides dates; the first sentence
  says the fact in plain words, who, where, what; the country where the fact happens is named in the title or in that
  sentence when the fact belongs to a country (an election, a law, an authority, a study run in a country); each
  sentence carries one fact; a short source gives a short text, without padding. The speaker carries the attribution
  ("According to Name (Institution), ..."). Never "According to <publisher>" when the publisher is the source. A
  research paper: "Researchers X and Y ...".
{regles}Return the object {{"billets": [...]}}.

Items:
{billets}"""
)
RELECTURE = (
    """You review the English items of a news feed on {nom} before they are published, as an adversarial
reviewer: you look for what does not hold. """
    + LECTEUR
    + """ For each item you receive the title, the text, the
source, and "a_verifier": the names, numbers and dates that an automatic check did not read as such in the source.
Return {{"n": number, "absents": [...], "clair": true or false, "motif": ..., "pays": ...}}.
- absents: the names, numbers, dates and facts of the title or the text that the source does not say; an empty list
  when everything is read in it. A number written in words is read in the source ("seventy-one percent": "71%"). The
  country of an institution the source names is read in the source. Read "a_verifier" first, then the rest.
- clair: read the title alone first, without the text. False when the title is not understood alone at first reading:
  it keeps a jargon word that a common word replaces; it starts with a name the reader does not know, without saying
  who it is; it is telegraphic, without a verb; one cannot tell who does what; it reads two ways. False also when the
  title and the text do not tell the same fact, or when the text keeps a jargon word the reader does not understand.
  False also when the first sentence of the text only says that someone spoke or that a document came out, or gives
  the venue, the format or the speakers, while the source gives a content: that content must come first.
- motif: what stops the reader, in fifteen words at most; empty when the item is clear.
- pays: the country, or the group of countries, where the fact happens, when the fact belongs to a country (an
  election, a law, an authority, a court, an administration, a study run in a country or by a university of that
  country) and neither the title nor the text says it, by its name or by an adjective ("American", "German federal").
  Its English name (the United States, Indonesia, the European Union). Empty when the item already says the country,
  when the fact belongs to no country, or when the source does not allow to say it.
Return the object {{"billets": [...]}}.

Items:
{billets}"""
)
REECRITURE = (
    """You rewrite English items of a news feed on {nom}, from their source only. """
    + LECTEUR
    + """ For each
item you receive the refused title and text, the reviewer's objections and the source. Return {{"n": number,
"titre": ..., "texte": ...}}.
- each objection is corrected; what is not objected stays as it is;
- an objection "the item does not say where the fact happens": the country enters the title or the first sentence;
- the title and the text tell the same fact, the one of the source; everything comes from the source: no name, no
  number, no fact that is not in it; a fact objected as absent from the source goes;
- titre: the result that matters to the reader, in one simple sentence with a verb; understood alone; a common word
  replaces the jargon word; a little-known person or organisation is followed by its role, or replaced by it; no
  question; {mots} words and {max} characters at most;
- texte: 30 to 70 words, 4 sentences at most, 2 numbers at most besides dates; the first sentence says the fact, who,
  where, what; the speaker carries the attribution ("According to Name (Institution), ...").
{regles}Return the object {{"billets": [...]}}.

Items:
{billets}"""
)
SCHEMA_TEXTE = fil.SCHEMA_REECRITURE
SCHEMA_RELECTURE = {
    "type": "object",
    "required": ["billets"],
    "properties": {
        "billets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "absents", "clair"],
                "properties": {
                    "n": {"type": "integer"},
                    "absents": {"type": "array", "items": {"type": "string"}},
                    "clair": {"type": "boolean"},
                    "motif": {"type": "string"},
                    "pays": {"type": "string"},
                },
            },
        }
    },
}


def existe(chemin: str) -> bool:
    return (RACINE / chemin).exists()


def a_ecrire(F: dict) -> list[dict]:
    """Les billets qui attendent leur version anglaise : la pile d'abord (ils vont paraître), puis les billets parus de
    la copie publique ; ni refusés par la relecture française, ni déjà écrits, et dont la fiche se lit."""
    vus, out = set(), []
    for b in (F.get("pile") or []) + (F.get("billets") or [])[: publier_fil.MAX]:
        if b.get("tenu") is False or "en" in b or b["chemin"] in vus or not existe(b["chemin"]):
            continue
        vus.add(b["chemin"])
        out.append(b)
    return out[:PAR_PASSAGE]


def _phrases(texte: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘(])", texte.strip()) if p]


def forme(titre: str, texte: str) -> list[str]:
    """Sans modèle : les écarts d'un billet anglais aux règles de forme du billet français."""
    ec = []
    mots = len(texte.split())
    if not lt.MOTS_MIN <= mots <= lt.MOTS_MAX:
        ec.append(f"text of {mots} words, {lt.MOTS_MIN} to {lt.MOTS_MAX}")
    phrases = _phrases(texte)
    if len(phrases) > lt.UNE_PHRASES:
        ec.append(f"{len(phrases)} sentences, {lt.UNE_PHRASES} at most")
    ec += [
        f"sentence of {len(p.split())} words, {MOTS_PAR_PHRASE} at most"
        for p in phrases
        if len(p.split()) > MOTS_PAR_PHRASE
    ]
    if len(titre) > TITRE_MAX:
        ec.append(f"title of {len(titre)} characters, {TITRE_MAX} at most")
    if len(titre.split()) > MOTS_TITRE:
        ec.append(f"title of {len(titre.split())} words, {MOTS_TITRE} at most")
    if "?" in titre:
        ec.append("title is a question")
    return ec


def _textes(r: dict, rangs) -> dict[int, tuple[str, str]]:
    out = {}
    for x in r.get("billets") or []:
        t, x_ = " ".join(str(x.get("titre") or "").split()), " ".join(str(x.get("texte") or "").split())
        if x.get("n") in rangs and t and x_:
            out[x["n"]] = (t, x_)
    return out


def _juger(
    essais: dict[int, tuple[str, str]], lot: list[tuple[dict, dict]], nom: str, graphe: str
) -> tuple[dict, float]:
    rangs = sorted(essais)
    v, cout = fil.demander(
        RELECTURE.format(
            nom=nom,
            billets="\n".join(
                json.dumps(
                    {
                        "n": k,
                        "titre": essais[n][0],
                        "texte": essais[n][1],
                        "a_verifier": fil.doutes(*essais[n], lot[n][1]),
                        "source": fil.extrait(lot[n][0], SOURCE_MAX),
                    },
                    ensure_ascii=False,
                )
                for k, n in enumerate(rangs)
            ),
        ),
        SCHEMA_RELECTURE,
        graphe,
        "relecteur anglais",
    )
    out = {}
    for x in v.get("billets") or []:
        if isinstance(x.get("n"), int) and 0 <= x["n"] < len(rangs):
            out[rangs[x["n"]]] = {
                "absents": [a for a in (" ".join(str(a).split()) for a in x.get("absents") or []) if a],
                "clair": x.get("clair") is not False,
                "motif": " ".join(str(x.get("motif") or "").split()),
                "pays": " ".join(str(x.get("pays") or "").split()),
            }
    return out, cout


def _reproches(titre: str, texte: str, v: dict) -> list[str]:
    return [
        *forme(titre, texte),
        *(f"absent from the source: {a}" for a in v["absents"]),
        *([v["motif"] or "unclear"] if not v["clair"] else []),
        *([f"the item does not say where the fact happens: {v['pays']}"] if v["pays"] else []),
    ]


def anglais(F: dict, graphe: str, reste: float) -> float:
    """Les versions anglaises d'un passage, sous `reste` ; rend leur coût."""
    if reste < 0.10:
        return 0.0
    billets = a_ecrire(F)
    fiches = lt.charger_fiches([b["chemin"] for b in billets]) if billets else {}
    lot = [(b, fiches[b["chemin"]]) for b in billets if b["chemin"] in fiches]
    if not lot:
        return 0.0
    ecrits, cout = ecrire(lot, graphe)
    for n, en in ecrits.items():
        lot[n][0]["en"] = en
    return cout


def ecrire(lot: list[tuple[dict, dict]], graphe: str) -> tuple[dict[int, dict], float]:
    """Les versions anglaises d'un lot de (billet, fiche), d'après la source seule : {rang: {titre, texte, tenu}}, sans
    les billets restés sans verdict ; le fil et la lettre anglaise (lettre_anglaise.py) passent par elle."""
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    r, cout = fil.demander(
        REDACTION.format(
            regles=lt.regles(graphe, "en"),
            nom=nom,
            mots=MOTS_TITRE,
            max=TITRE_MAX,
            billets="\n".join(
                json.dumps(
                    {
                        "n": n,
                        "editeur": f.get("editeur"),
                        "date": b.get("date") or f.get("date"),
                        "titre": f.get("titre"),
                        "source": fil.extrait(b, SOURCE_MAX),
                    },
                    ensure_ascii=False,
                )
                for n, (b, f) in enumerate(lot)
            ),
        ),
        SCHEMA_TEXTE,
        graphe,
        "anglais",
    )
    a_juger = _textes(r, range(len(lot)))
    essais: dict[int, list] = {n: [] for n in a_juger}
    for tour in range(REECRITURES + 1):
        if not a_juger:
            break
        v, c = _juger(a_juger, lot, nom, graphe)
        cout += c
        refuses = {}
        for n, (t, x) in a_juger.items():
            if n in v:
                essais[n].append((t, x, v[n]))
                if v[n]["absents"] or not v[n]["clair"] or v[n]["pays"]:
                    refuses[n] = _reproches(t, x, v[n])
        if not refuses or tour == REECRITURES:
            break
        r, c = fil.demander(
            REECRITURE.format(
                regles=lt.regles(graphe, "en"),
                nom=nom,
                mots=MOTS_TITRE,
                max=TITRE_MAX,
                billets="\n".join(
                    json.dumps(
                        {
                            "n": n,
                            "titre": essais[n][-1][0],
                            "texte": essais[n][-1][1],
                            "reproches": m,
                            "source": fil.extrait(lot[n][0], SOURCE_MAX),
                        },
                        ensure_ascii=False,
                    )
                    for n, m in refuses.items()
                ),
            ),
            SCHEMA_TEXTE,
            graphe,
            "réécriture anglaise",
        )
        cout += c
        a_juger = _textes(r, refuses)
    ecrits = {}
    for n, liste in essais.items():
        if not liste:
            continue  # sans verdict : reste à écrire
        mains = [(t, x, v) for t, x, v in reversed(liste) if v["clair"] and not v["absents"]]
        if not mains:
            ecrits[n] = {"tenu": False, "reproches": _reproches(*liste[0])}
            continue
        t, x, _ = min(mains, key=lambda m: (bool(m[2]["pays"]), len(forme(m[0], m[1]))))
        ecrits[n] = {"titre": t, "texte": x, "tenu": True}
    return ecrits, cout


def arguments(argv: list[str] | None = None):
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--graphe", default=cfg.graphe_du_site())
    return ap.parse_args(argv)


def main() -> None:
    """Un rattrapage hors passage du fil : les versions anglaises qui manquent, sous le plafond du jour et le verrou du
    fil, passage après passage tant qu'il en reste et que le passage avance."""
    import time  # noqa: PLC0415

    a = arguments()
    avant = None
    while True:
        with fil.verrou(a.graphe):
            F, jour = fil.lire(a.graphe), time.strftime("%Y-%m-%d")
            reste = [b["chemin"] for b in a_ecrire(F)]
            if not reste or reste == avant:  # le même lot qu'au passage d'avant : il n'avance plus
                break
            cout = anglais(F, a.graphe, fil.plafond(jour, a.graphe) - fil.depense_du_jour(F, jour))
            F.setdefault("depense", {})[jour] = fil.depense_du_jour(F, jour) + cout
            fil.ecrire(a.graphe, F)
        avant = reste
        tous = (F.get("pile") or []) + (F.get("billets") or [])[: publier_fil.MAX]
        print(
            f"{sum(1 for b in tous if (b.get('en') or {}).get('tenu'))} billet(s) en anglais, "
            f"{sum(1 for b in tous if (b.get('en') or {}).get('tenu') is False)} refusé(s), "
            f"{sum(1 for b in tous if 'en' not in b and b.get('tenu') is not False)} à écrire"
        )


if __name__ == "__main__":
    main()

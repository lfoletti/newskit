#!/usr/bin/env python3
"""L'angle critique de chaque billet de la lettre (mainteneur, 03.11.2026 : « tous les billets donnés en lecture devraient
avoir un angle critique »). Un agent à part du reporter, appelé par la lettre (lettre.lettre_du_graphe).

Pour chaque billet (une, rubriques, brèves) :
- le graphe d'abord (reporter.graphe_d_abord : voisinage, chercher, passages), sur les questions 2 (le précédent) et
  3 (le texte) de STE_ENQUETE.md, section 3 ;
- le web seulement si le graphe ne répond à aucune des questions 1 à 3 : 2 recherches et 2 pages au plus, lues par
  lecture_web.lire ;
- une phrase de 25 mots au plus, rendue sous le billet après la flèche « → » (sans libellé, 03.11.2026) ; chaque fait a une preuve, une citation retrouvée mot pour mot
  dans sa source (lecture_web.citation_dans), et ses nombres, dates et noms figurent dans les preuves (section 5) ;
- les contrôles sans modèle de la lettre ; une relance, puis abandon ;
- sans fait trouvé : pas d'angle ; un billet de rubrique passe en brève, sans phrase de remplacement.
Budget au registre (bloc `reporter:` de sources.yaml) : angle_usd_billet, angle_usd_lettre ; coupure stricte. Chaque
appel a sa ligne au registre des coûts, poste « angle ».
"""

from __future__ import annotations

import sys
from pathlib import Path


import re  # noqa: E402

from newskit import config_sources as cfg  # noqa: E402
from newskit import lecture_web as lw  # noqa: E402
from newskit import lettre as lt  # noqa: E402
from newskit import reporter as rp  # noqa: E402

POSTE = "angle"
MOTS_LECTURE = 25
MOTS_SOURCE = 900  # texte d'une source donné au modèle, au plus
SOURCES_GRAPHE = 4  # fiches du graphe données comme sources, en plus de celle du billet

SCHEMA = {
    "type": "object",
    "required": ["lecture", "preuves", "questions"],
    "properties": {
        "lecture": {"type": "string"},
        "preuves": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source", "citation"],
                "properties": {"source": {"type": "integer"}, "citation": {"type": "string"}},
            },
        },
        "questions": {"type": "array", "items": {"type": "integer"}},
    },
}
SCHEMA_WEB = {
    "type": "object",
    "required": ["urls"],
    "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
}


class Budget(rp.Budget):
    """Le budget d'un billet (angle_usd_billet), dans celui de la lettre (angle_usd_lettre)."""

    def __init__(
        self,
        usd_billet: float,
        usd_lettre: float,
        depense_lettre: float = 0.0,
        recherches: int = 2,
        pages: int = 2,
        ecartes=(),
    ):
        super().__init__(
            recherches=recherches,
            pages=pages,
            usd=usd_billet,
            lettre_usd=usd_lettre,
            depense_lettre=depense_lettre,
            ecartes=ecartes,
        )


def _sources_texte(sources: dict[int, dict]) -> str:
    return "\n\n".join(
        f"[{n}] {s.get('editeur') or ''} — {s.get('titre') or ''} ({s.get('url') or ''})\n"
        + " ".join(str(s.get("texte") or "").split()[:MOTS_SOURCE])
        for n, s in sources.items()
    )


def invite(billet: dict, sources: dict[int, dict], graphe_txt: str, raison: str = "") -> str:
    relance = (
        f"Ta première réponse a été refusée par le contrôle : {raison}. Réécris-la en tenant chaque règle.\n"
        + (
            "Donne le précédent (question 2) ou le texte (question 3) : un fait absent du chapeau, tiré d'une "
            "autre source que celle du billet (source 1) ; sinon rends une lecture vide.\n"
            if raison.startswith("n'apporte rien")
            else ""
        )
        + "\n"
        if raison
        else ""
    )
    return (
        relance + "Tu écris l'angle critique d'un billet d'une lettre d'actualité en français, d'après ses seules "
        "sources numérotées. La critique confronte des faits sourcés, jamais un adjectif ni une opinion.\n"
        "Réponds d'abord à la question 2, le précédent : ce qui existait avant, et ce qui change vraiment ; puis à "
        "la question 3, le texte : ce que la loi ou la règle impose, et ce qui est fait (délai, portée, qui "
        "contrôle). La question 1 (l'annonce et les faits) peut servir si les sources la documentent.\n"
        f"Écris une seule phrase de {MOTS_LECTURE} mots au plus, sans libellé ni ouverture, sans adjectif "
        "d'évaluation (historique, inédit, majeur, crucial…), avec des dates absolues ; pas d'intention prêtée à "
        "un acteur. Chaque fait de la phrase a sa preuve : le numéro de sa source et une citation de 6 à 14 mots, "
        "copiée mot pour mot de cette source, dans sa langue. Chaque nombre, date et nom de la phrase figure dans "
        "une citation. Dans « questions », les numéros des questions auxquelles la phrase répond.\n"
        "Si aucune source ne répond aux questions 1 à 3, rends une lecture vide et aucune preuve : n'écris rien de "
        "mémoire.\n\n"
        f"=== BILLET\n{billet.get('titre') or ''}\n{billet.get('chapeau') or ''}\n\n"
        f"=== SOURCES\n{_sources_texte(sources)}\n\n=== GRAPHE (passages)\n{graphe_txt}\n"
    )


def invite_web(billet: dict, recherches: int, pages: int) -> str:
    return (
        f"Cherche sur le web, en {recherches} recherches au plus, le précédent (ce qui existait avant) et le texte "
        f"(ce que la loi ou la règle impose) du fait suivant. Rends au plus {pages} adresses de pages primaires ou "
        "de presse qui les documentent, sans les résumer.\n\n"
        f"{billet.get('titre') or ''}\n{billet.get('chapeau') or ''}\n"
    )


def _jetons(t: str) -> set[str]:
    """Les nombres (chiffres seuls) et les noms (mots capitalisés hors du premier) d'un texte, pliés."""
    nombres = {re.sub(r"\D", "", n) for n in re.findall(r"\d+(?:[.,\u00a0\u202f ]\d+)*", str(t))}
    mots = re.findall(r"[A-ZÀ-Ý][\w’'.-]{2,}", str(t))
    noms = {lt.plier(m).strip(".") for m in mots[1:] if lt.plier(m) not in lt.MOTS_OUTILS}
    if mots and not str(t).lstrip().startswith(mots[0]):
        noms.add(lt.plier(mots[0]).strip("."))
    return {x for x in nombres | noms if x}


def fait_neuf(lecture: str, chapeau: str, citations_ailleurs: list[str]) -> bool:
    """L'angle porte-t-il un fait absent du chapeau, prouvé par une autre source que celle du billet ? (mainteneur,
    03.11.2026 : « si ça n'apporte rien ça ne devrait pas être présent ».)"""
    neufs = _jetons(lecture) - _jetons(chapeau)
    preuve = " ".join(lt.plier(c) for c in citations_ailleurs)
    chiffres = re.sub(r"\D", "", " ".join(citations_ailleurs))
    return any((x.isdigit() and x in chiffres) or (not x.isdigit() and x in preuve) for x in neufs)


def controler(r: dict, sources: dict[int, dict], chapeau: str | None = None) -> str:
    """Pourquoi une lecture ne tient pas le contrôle sans modèle (vide : elle tient). Avec le chapeau du billet :
    l'angle doit aussi porter un fait neuf, prouvé hors de la source du billet (source 1)."""
    lecture = lt.sans_libelle(r.get("lecture") or "")  # les 25 mots se comptent sans libellé
    n = lt.compter_mots(lecture)
    if n > MOTS_LECTURE:
        return f"{n} mots, {MOTS_LECTURE} au plus"
    if len(lt.phrases(lecture)) > 1:
        return "une seule phrase"
    m = re.search(r"(?<![a-z])(plutot que|tandis que)(?![a-z])", lt.plier(lecture))
    if m:  # angle 8 de la v8 (mainteneur, 03.11.2026)
        return f"tournure : {'plutôt que' if m.group(1) == 'plutot que' else 'tandis que'}"
    tr = lt.tournure(lecture) or lt.preuve(lecture)
    if tr:
        return f"tournure : {tr}" if lt.tournure(lecture) else tr
    preuves = r.get("preuves") or []
    if not preuves:
        return "aucune preuve"
    citations = []
    for p in preuves:
        c, s = str(p.get("citation") or ""), sources.get(p.get("source"))
        if not s or len(c.split()) >= rp.CITATION_MOTS or not lw.citation_dans(c, s):
            return f"citation non retrouvée : « {c[:120]} »"
        citations.append(c)
    faute = lt.faute_de_preuve(lecture, " ".join(citations))
    if faute:
        return faute
    g = lt.formule_generique(lecture, " ".join(str(s.get("texte") or "") for s in sources.values()))
    if g:  # angle 12 de la v8 : « des contrôles de sécurité »
        return g
    ailleurs = [str(p.get("citation") or "") for p in preuves if p.get("source") != 1]
    if chapeau is not None and not fait_neuf(lecture, chapeau, ailleurs):
        return "n'apporte rien : aucun fait neuf, absent du chapeau et prouvé hors de la source du billet"
    return ""


def angle(
    billet: dict,
    graphe: str,
    budget: Budget,
    graphe_,
    lire=lw.lire,
    web: bool = True,
    invite_=None,
    invite_web_=None,
    controle=None,
) -> dict:
    """{lecture, preuves, refus, cout} pour un billet ({titre, chapeau, fiche}) ; lecture vide sans fait prouvé.
    `invite_`, `invite_web_`, `controle` : une autre question posée aux mêmes sources, par la même marche (la
    définition d'un terme, fil.definir) ; par défaut l'angle critique."""
    invite_, invite_web_, controle = invite_ or invite, invite_web_ or invite_web, controle or controler
    depart = budget.depense
    g = graphe_(billet, graphe) or {}
    sources: dict[int, dict] = {1: billet["fiche"]}
    for f in (g.get("fiches") or [])[:SOURCES_GRAPHE]:
        if isinstance(f, dict) and f.get("url") != billet["fiche"].get("url"):
            sources[len(sources) + 1] = f
    texte_g = str(g.get("texte") or "")
    refus: list[str] = []

    def ecrire(raison: str = "") -> dict | None:
        return rp.appeler(invite_(billet, sources, texte_g, raison), SCHEMA, budget, graphe, "angle", poste=POSTE)

    r = ecrire()
    vide = r is not None and not str(r.get("lecture") or "").strip()
    if vide and web and budget.recherches and budget.pages:
        # Le graphe ne répond à aucune des questions 1 à 3 : le web, 2 recherches et 2 pages au plus.
        avant = len(sources)
        w = rp.appeler(
            invite_web_(billet, budget.recherches, budget.pages),
            SCHEMA_WEB,
            budget,
            graphe,
            "angle : recherche",
            outils="WebSearch",
            recherches=budget.recherches,
            poste=POSTE,
        )
        for u in [str(x) for x in (w or {}).get("urls") or []][: budget.pages]:
            if rp.domaine(u) in budget.ecartes:
                continue
            page = lire(u)
            budget.pages_lues += 1
            if page and str(page.get("texte") or "").strip():
                sources[len(sources) + 1] = {**page, "editeur": page.get("editeur") or rp.domaine(u)}
        if len(sources) > avant:
            r = ecrire()
    if r is not None and str(r.get("lecture") or "").strip():
        raison = controle(r, sources, billet.get("chapeau") or "")
        if raison:
            refus.append(raison)
            r = ecrire(raison)  # une relance, raison donnée, puis abandon
            raison2 = (
                controle(r, sources, billet.get("chapeau") or "")
                if r and str(r.get("lecture") or "").strip()
                else "pas de réponse"
            )
            if raison2:
                refus.append(raison2)
                r = None
    ok = r is not None and str(r.get("lecture") or "").strip()
    preuves = [{**p, "url": sources[p["source"]].get("url", "")} for p in (r or {}).get("preuves") or []] if ok else []
    return {
        "lecture": lt.ponctuation_fr(lt.sans_libelle(r["lecture"])) if ok else "",
        "preuves": preuves,
        "refus": refus,
        "cout": round(budget.depense - depart, 4),
    }


def graphe_du(graphe: str):
    """La recherche au graphe d'un billet : reporter.graphe_d_abord, puis les fiches qu'elle nomme, chargées."""
    from newskit import interroger_graphe as ig  # noqa: PLC0415

    G = ig.charger(cfg.chemins_du_graphe(graphe)["graphe"])

    def chercher(billet: dict, graphe_: str) -> dict:
        f = billet["fiche"]
        g = rp.graphe_d_abord(G, {"titre": f.get("titre", ""), "chemin": f.get("chemin", "")}, graphe_)
        fiches = lt.charger_fiches([c for c in g.get("fiches") or [] if c != f.get("chemin")][:SOURCES_GRAPHE])
        return {"texte": g.get("texte", ""), "fiches": list(fiches.values())}

    return chercher


def _billets(L: dict):
    """(genre, rubrique, élément) dans l'ordre de la lettre : la une, puis chaque rubrique, billets puis brèves."""
    for f in L["faits"]:
        yield "une", "", f
    for g, xs, breves in lt.rubriques_de(L):
        for x in xs:
            yield "autre", g, x
        for x in breves:
            yield "breve", g, x


def annoter(L: dict, fiches: dict[str, dict], graphe: str, graphe_=None, lire=lw.lire) -> dict:
    """Ajoute « lecture » et « preuves » aux billets qui en ont une ; un billet de rubrique sans fait passe en brève.
    L["cout_angle"], L["angles"] (tentés, trouvés, non tentés faute de budget). Jamais bloquant."""
    b = cfg.reporter(graphe)
    total, stats, deplacer, refus = 0.0, {"tentes": 0, "trouves": 0, "non_tentes": 0}, [], []
    try:
        chercher = graphe_ or graphe_du(graphe)
    except Exception as e:  # noqa: BLE001 — graphe illisible : pas d'angle, la lettre part
        print(f"  angle : graphe illisible ({type(e).__name__})", file=sys.stderr)
        L["cout_angle"], L["angles"] = 0.0, {**stats, "non_tentes": sum(1 for _ in _billets(L))}
        return L
    for genre, g, x in list(_billets(L)):
        fiche = fiches.get(x.get("chemin") or "")
        if not fiche:
            continue
        budget = Budget(
            b["angle_usd_billet"],
            b["angle_usd_lettre"],
            total,
            int(b["angle_recherches"]),
            int(b["angle_pages"]),
            b.get("domaines_ecartes") or (),
        )
        if budget.reste_usd() <= 0 or total >= b["angle_usd_lettre"]:
            stats["non_tentes"] += 1  # coupure stricte : le billet garde sa place
            continue
        billet = {
            "titre": x.get("titre", ""),
            "chapeau": x.get("paragraphe") or x.get("descriptif") or "",
            "fiche": fiche,
        }
        try:
            r = angle(billet, graphe, budget, chercher, lire=lire)
        except Exception as e:  # noqa: BLE001
            print(f"  angle : billet « {billet['titre'][:40]} » en échec ({type(e).__name__})", file=sys.stderr)
            r = {"lecture": "", "preuves": [], "refus": [], "cout": budget.depense}
        total += float(r.get("cout") or 0)
        stats["tentes"] += 1
        if r.get("refus"):
            refus.append({"titre": billet["titre"], "refus": r["refus"], "garde": bool(r["lecture"])})
        if r["lecture"]:
            stats["trouves"] += 1
            x["lecture"], x["preuves"] = r["lecture"], r.get("preuves") or []
        elif genre == "autre":
            deplacer.append((g, x))
    for g, x in deplacer:  # sans fait trouvé : le billet de rubrique passe en brève
        L["aussi"][g].remove(x)
        if not L["aussi"][g]:
            del L["aussi"][g]
        # La brève garde le développement du billet (lettre du 05.11.2026 : six brèves sur huit en titre seul).
        L["bref"].setdefault(g, []).append(
            {
                "titre": x["titre"],
                "descriptif": x.get("descriptif") or x.get("paragraphe") or "",
                "url": x["source"]["url"],
                "chemin": x.get("chemin", ""),
            }
        )
    L["cout_angle"], L["angles"], L["angles_refus"] = round(total, 4), stats, refus  # aux Coulisses de l'essai
    return L

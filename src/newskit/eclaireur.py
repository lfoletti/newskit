#!/usr/bin/env python3
"""L'éclaireur : chercher sur le web comme le ferait un modèle, et garder ce qu'il trouve.

Un modèle doté de la recherche web part d'une question, lance quelques recherches, lit trois
ou quatre pages, répond — et oublie. Le graphe, lui, attend que ses flux lui apportent les choses.
L'éclaireur copie la stratégie du premier pour nourrir le second (décision du porteur, 04.10.2026) :
chaque jour, par graphe, quelques recherches sur des points précis, et les pages trouvées entrent
au graphe par la voie ordinaire — fiche, provenance, extraction.

Le modèle cherche ; il ne répond pas. Il rend une liste d'adresses, rien d'autre : les pages sont
lues par nos scripts (collecte_rss.corps_de_page), et c'est leur texte qui entre, pas son résumé.

Les points d'un passage, dans cet ordre, jusqu'à la borne `recherches` :
    0. les questions restées sans réponse (lacunes.py) : une recherche du porteur, du skill ou d'un
       utilisateur du serveur que le graphe n'a pas su traiter — le meilleur signal de ce qui manque ;
    1. les thèmes émergents du graphe (themes_emergents.py) — un thème que la carte voit naître
       mérite d'être documenté avant d'être soumis au porteur ;
    2. les notions du canon restées « à trancher » (combler_canon.py) ;
    3. les sujets déclarés, à tour de rôle d'un jour à l'autre : d'abord leurs points peu couverts
       (peu_couverts.py : questions manquées au banc par thème, sous-questions de carte à support mince),
       « quoi de neuf » quand il n'en reste pas.

Bornes, toutes au registre (sources.yaml, clé `eclaireur`, avec une surcharge par graphe) :
    actif        faux : l'éclaireur ne tourne pas pour ce graphe
    recherches   points soumis au modèle, donc recherches web au plus (défaut 5)
    pages        pages retenues au plus par passage (défaut 10)
    mots_min     une page plus courte n'est pas ingérée (défaut 200)
    themes       thèmes émergents documentés au plus par passage (défaut 2)
    lacunes      questions sans réponse cherchées au plus par passage (défaut 2)
    delai        secondes laissées au modèle (défaut 600)

Un domaine trouvé deux fois est une source à suivre : il est étudié (prospecter_source) et le
rapport donne le bloc à déclarer — l'éclaireur découvre, les flux suivent ensuite sans lui.

    python3 -m newskit.eclaireur --graphe <graphe>            # un passage
    python3 -m newskit.eclaireur --graphe <graphe> --controle # les points qui seraient cherchés, sans appel
    python3 -m newskit.eclaireur --etat                        # la mémoire des domaines trouvés

Fiches : sources/eclaireur/<graphe>/ (plateforme « Recherche web »). Mémoire : sources/eclaireur/_sources.json.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import couts  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import lacunes  # noqa: E402
from newskit import peu_couverts  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402
from newskit.extraire_squelette import canon_arxiv  # noqa: E402

DOSSIER = RACINE / "sources" / "eclaireur"
MEMOIRE = DOSSIER / "_sources.json"
SCHEMA = {"type": "object", "required": ["pages"], "properties": {"pages": {"type": "array", "items": {
    "type": "object", "required": ["url", "titre", "point"],
    "properties": {"url": {"type": "string"}, "titre": {"type": "string"}, "date": {"type": "string"},
                   "point": {"type": "integer"}, "pourquoi": {"type": "string"}}}},
    "hors_sujet": {"type": "array", "items": {"type": "integer"}}}}


def domaine_de(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def lire_memoire() -> dict:
    return json.loads(MEMOIRE.read_text(encoding="utf-8")) if MEMOIRE.exists() else {"domaines": {}}


def adresses_connues(graphe: str) -> set[str]:
    """Les adresses (canoniques) et les identifiants arXiv des fiches que le graphe a déjà, sous-dossiers compris :
    l'index d'identité de fiche (06.11.2026 ; avant, un glob d'un seul niveau et la seule barre finale retirée)."""
    vues = set()
    for k in fiche.identites_du_graphe(graphe):
        if k.startswith("url:"):
            vues.add(k.removeprefix("url:").split("|partie:")[0])
        elif k.startswith("arxiv:"):
            vues.add(k)
    return vues


def domaines_suivis(graphe: str) -> list[str]:
    out = set()
    for s in cfg.toutes(graphe=graphe):
        for champ in ("domaine", "site", "flux"):
            v = str(s.propres.get(champ) or "")
            if v:
                out.add(domaine_de(v if "//" in v else "https://" + v))
    return sorted(d for d in out if d)


def points_du_jour(graphe: str, bornes: dict, jour: int | None = None) -> list[tuple[str, str]]:
    """(genre, énoncé) des points à chercher : thèmes émergents, canon à trancher, puis sujets à tour de rôle."""
    base = cfg.chemins_du_graphe(graphe)["base"]
    points: list[tuple[str, str]] = []
    for _, x in lacunes.a_chercher(graphe, bornes.get("lacunes", 0)):
        if "signale" in x.get("origines", []) and x.get("question"):
            # Un manque signalé par un client du serveur : l'objet est le point, la question son contexte (12.10.2026).
            points.append(("lacune", f"{x['termes']} — pour répondre à : {x['question']}"))
        else:
            points.append(("lacune", x.get("question") or x["termes"]))
    # Les sujets neufs (thème émergent entré parmi les sujets) passent en tête NUITS_NEUF nuits (09.10.2026).
    for lib in te.nouveaux_en_cours(graphe)[:2]:
        points.append(("sujet neuf", lib))
    p = base / "themes.json"
    if p.exists():
        candidats = json.loads(p.read_text(encoding="utf-8")).get("candidats", {})
        for c in sorted(candidats.values(), key=lambda c: (-c.get("passages", 0), -c.get("taille", 0)))[:bornes["themes"]]:
            points.append(("thème émergent", f"{c.get('libelle') or c['etiquette']} ({', '.join(c.get('notions', [])[:5])})"))
    p = base / "canon.json"
    if p.exists():
        points += [("canon", t) for t in list(json.loads(p.read_text(encoding="utf-8")).get("a_trancher", {}))[:1]]
    sujets = te.sujets_declares(graphe)
    reste = max(bornes["recherches"] - len(points), 0)
    if sujets and reste:
        debut = ((time.localtime().tm_yday if jour is None else jour) * reste) % len(sujets)
        for k in range(min(reste, len(sujets))):
            s = sujets[(debut + k) % len(sujets)]
            # Le sujet du jour cherche d'abord ses points peu couverts (peu_couverts.py, porteur 10.10.2026) ; « quoi de
            # neuf » seulement quand il n'en a plus (ou tous cherchés depuis moins de REPIT jours).
            pc = peu_couverts.pour_eclaireur(graphe, s)
            points.append(("peu couvert", f"{peu_couverts.tete(s)} — {pc}") if pc else ("sujet", s))
    return points[:bornes["recherches"]]


def chercher(graphe: str, points: list[tuple[str, str]], bornes: dict) -> tuple[list[dict], list[int]]:
    """(pages trouvées par la recherche web, indices des lacunes jugées hors sujet). Vides si le modèle manque."""
    decl = cfg.graphe_declare(graphe)
    prompt = (
        f"Tu es l'éclaireur d'une veille sur « {decl['libelle']} ». Cherche sur le web, puis rends une liste de pages. "
        f"Ne rédige aucune synthèse.\n\nPoints à chercher (une recherche par point, {len(points)} au plus) :\n"
        + "\n".join(f"  P{i}. [{genre}] {enonce}" for i, (genre, enonce) in enumerate(points))
        + f"\n\nPour un [sujet], cherche ce qui a été publié depuis le {cfg.HORIZON_MODELE} : annonces, versions, résultats, "
          "analyses. Pour un [thème émergent] ou un point du [canon], cherche la meilleure page de fond, récente ou non. "
          "Une [lacune] est une question restée sans réponse, ou un objet précis (texte et article, décision, document) "
          "qui a manqué pour répondre à la question donnée après « pour répondre à » : cherche la page qui y répond le "
          "mieux, ou la page de référence qui porte l'objet ; si elle ne relève "
          "pas du domaine de la veille, ne cherche rien et donne son indice dans `hors_sujet`. Un point [peu couvert] "
          "est, pour un sujet suivi, une question ou une notion que la veille documente mal : cherche la page de fond "
          "qui le traite, récente ou non.\n"
          f"Rends au plus {bornes['pages']} pages en tout. Chaque page est un article ou une annonce de première main, en "
          "accès libre, avec du texte : pas de page d'accueil, d'agrégateur, de forum, de vidéo ni de réseau social.\n"
          "Ne propose rien de ces domaines, déjà suivis : " + ", ".join(domaines_suivis(graphe)[:60]) + ".\n"
          'Réponds en JSON : {"pages": [{"url": "…", "titre": "…", "date": "AAAA-MM-JJ", "point": <indice P>, '
          '"pourquoi": "une ligne"}, …], "hors_sujet": [<indices P>]}.')
    args = [*couts.CLAUDE_P, "--no-session-persistence", "--allowedTools", "WebSearch", "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA)] + (["--model", te.MODELE] if te.MODELE else [])
    if couts.en_limite():
        print(f"  !! éclaireur : limite d'usage atteinte, recherche non tentée ({couts.marque_en_cours() or 'ce processus'})",
              file=sys.stderr)
        return [], []
    t0 = time.time()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=bornes["delai"], cwd=RACINE)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        couts.enregistrer("eclaireur", graphe, te.MODELE, None, time.time() - t0, note=type(e).__name__)
        print(f"  !! éclaireur : {type(e).__name__}", file=sys.stderr)
        return [], []
    env = couts.enregistrer("eclaireur", graphe, te.MODELE, r, time.time() - t0)
    if r.returncode != 0:
        print(f"  !! éclaireur : claude -p a rendu {r.returncode} : {(r.stderr or r.stdout)[:160]}", file=sys.stderr)
        return [], []
    try:
        if not env:
            raise json.JSONDecodeError("enveloppe illisible", r.stdout[:50], 0)
        brut = env.get("structured_output") or env.get("result")
        if isinstance(brut, str):
            m = re.search(r"\{.*\}", brut, flags=re.S)
            brut = json.loads(m.group(0)) if m else {}
    except (json.JSONDecodeError, AttributeError):
        return [], []
    brut = brut or {}
    pages = [p for p in brut.get("pages", []) if isinstance(p, dict) and str(p.get("url", "")).startswith("http")]
    return pages, [i for i in brut.get("hors_sujet", []) if isinstance(i, int) and 0 <= i < len(points)]


def date_du_document(meta_date: str, date_lecteur: str, url: str, aujourdhui: str) -> tuple[str, str]:
    """(date du document, origine) : les métadonnées de publication de la page, puis la date du lecteur web, puis la
    date écrite dans l'adresse ; sinon ("", "inconnue"). Jamais la date de collecte, ni celle que le modèle de
    l'éclaireur propose, ni une date lue dans le texte, ni une date à venir (décision du 03.11.2026 : Gleiss Lutz,
    Heuking, DCD étaient mal datés)."""
    def ok(d):
        return bool(fiche.RE_DATE.match(d or "")) and d <= aujourdhui and d >= "1990-01-01"
    if ok(str(meta_date)[:10]):
        return str(meta_date)[:10], "méta de publication"
    if ok(str(date_lecteur)[:10]):
        return str(date_lecteur)[:10], "lecteur web"
    m = re.search(r"/((?:19|20)\d\d)[/-](\d\d)[/-](\d\d)(?:/|\b)", url) or \
        re.search(r"/((?:19|20)\d\d)(\d\d)(\d\d)(?=\D)", url)
    if m and ok(f"{m.group(1)}-{m.group(2)}-{m.group(3)}"):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "adresse"
    return "", "inconnue"


def _date_du_lecteur(url: str) -> str:
    try:
        from newskit import lecture_web  # noqa: PLC0415
        return str(lecture_web.lire(url).get("date") or "")
    except Exception:  # noqa: BLE001 — le lecteur échoue : pas de date
        return ""


def ingerer(graphe: str, page: dict, points: list[tuple[str, str]], bornes: dict, connues: set[str],
            memoire: dict, aujourdhui: str) -> str:
    """Lit la page et écrit sa fiche. Rend la ligne du rapport."""
    from newskit import collecte_rss as cr  # noqa: PLC0415
    from newskit import depots  # noqa: PLC0415
    url = page["url"].strip()
    domaine = domaine_de(url)
    aid = canon_arxiv(url)
    if (url.rstrip("/") in connues or (aid and f"arxiv:{aid}" in connues)
            or fiche.deja_vue(graphe, {"url": url, "arxiv_id": aid or ""})):
        return f"  =   déjà au graphe : {url[:90]}"
    n_mots, corps, mur, meta = cr.corps_de_page(url)
    if mur or n_mots < bornes["mots_min"]:
        return f"  --  écartée ({n_mots} mots{', mur d’abonnés' if mur else ''}) : {url[:80]}"
    titre = (meta.get("titre") or page.get("titre") or domaine).strip()
    meta_date = str(meta.get("date_publication") or "")
    date, origine = date_du_document(meta_date, "" if meta_date else _date_du_lecteur(url), url, aujourdhui)
    i = page.get("point") if isinstance(page.get("point"), int) and 0 <= page["point"] < len(points) else 0
    genre, enonce = points[i]
    dossier = DOSSIER / graphe
    dossier.mkdir(parents=True, exist_ok=True)
    champs = {"title": titre, "subtitle": "", "author": meta.get("author") or meta.get("auteur") or "", "date": date,
              "url": url, "plateforme": "Recherche web", "publication": domaine, "complet": True,
              "collected": aujourdhui, "origine_date": origine,
              "source": f"trouvé par l'éclaireur — {genre} : {enonce[:90]}"}
    if aid:
        # La page d'un papier est ce papier : sans `arxiv_id`, le lien qu'elle porte vers elle-même
        # ferait naître un second nœud, papier, sur la même fiche (contrôle de fraîcheur, 05.10.2026).
        champs["arxiv_id"] = aid
        connues.add(f"arxiv:{aid}")
    # Le nom du fichier est un identifiant : sans date du document, il prend le jour de la collecte.
    try:
        fiche.ecrire(dossier / f"{date or aujourdhui}_{depots.slug(url, titre)}.md", champs, f"# {titre}\n\n{corps}")
    except fiche.IdentiteDejaVue as deja:              # trouvée, mais déjà dans le graphe sous une autre adresse
        connues.add(url.rstrip("/"))
        return f"  =   [{genre}] {titre[:64]} — déjà vue ({deja.cle}, {deja.existante.name})"
    connues.add(url.rstrip("/"))
    d = memoire["domaines"].setdefault(domaine, {"depots": 0, "graphes": [], "etude": None, "etudie_le": None})
    d["depots"] += 1
    d["dernier"] = aujourdhui
    if graphe not in d["graphes"]:
        d["graphes"].append(graphe)
    if d["depots"] >= 2 and not depots.deja_declaree(domaine):
        depots.etudier_source(domaine, memoire, aujourdhui)      # la prospection, gardée trente jours
    return f"  ok  [{genre}] {titre[:64]} — {n_mots} mots — {domaine}"


SEUIL_DECLARATION = 2        # un domaine trouvé deux fois, au flux lisible, est déclaré au registre (porteur, 09.10.2026)


def bloc_source(graphe: str, domaine: str, etude: dict, depots: int, lexique: list[str] | None) -> tuple[str, str]:
    """(clé, bloc YAML) d'une source à déclarer, d'après l'étude de prospecter_source. Nom et mots-clés à relire."""
    from newskit import prospecter_source as ps  # noqa: PLC0415
    slug = re.sub(r"[^a-z0-9]+", "_", domaine.lower()).strip("_")[:32]
    cle = f"auto_{slug}"
    g_ = graphe.replace("-", "_")
    aujourdhui = time.strftime("%Y-%m-%d")
    lignes = [f"  # Déclarée automatiquement le {aujourdhui} par l'éclaireur : {domaine} trouvé {depots} fois par ses recherches pour",
              f"  # {graphe}, flux lisible ({etude.get('mots', 0)} mots par entrée). Nom et mots-clés à relire ; retirer le bloc pour ne plus la suivre.",
              f"  - cle: {cle}"]
    if etude.get("voie") == "Substack":
        lignes += ["    plateforme: Substack", f"    nom: {domaine}", f"    dossier: {g_}/substack/{cle}", f"    domaine: {domaine}",
                   "    abonnement: gratuit"]
    else:
        lignes += ["    plateforme: Flux RSS", f"    nom: {domaine}", f"    dossier: {g_}/rss/{cle}", "    abonnement: gratuit",
                   f"    flux: {etude.get('flux') or etude.get('adresse')}", f"    site: https://{domaine}"]
        if int(etude.get("mots") or 0) < ps.MOTS_MINIMUM:
            lignes.append("    page: true")
    lignes += ["    dense: true", f"    graphe: {graphe}"]
    if lexique:
        lignes.append("    mots_cles:")
        lignes += [f"      - {json.dumps(m, ensure_ascii=False)}" for m in lexique]
    return cle, "\n".join(lignes)


def inserer_source(bloc: str, registre: Path | None = None) -> bool:
    """Insère le bloc en fin de liste `sources:` (avant `veille_arxiv:`) et revalide le registre ; annule si invalide."""
    registre = registre or cfg.FICHIER
    texte = registre.read_text(encoding="utf-8")
    lignes = texte.split("\n")
    i = next((k for k, l in enumerate(lignes) if l.startswith("veille_arxiv:")), None)
    if i is None:
        return False
    j = i
    while j > 0 and not lignes[j - 1].strip():
        j -= 1
    lignes[j:j] = ["", *bloc.split("\n")]
    registre.write_text("\n".join(lignes), encoding="utf-8")
    try:
        if registre == cfg.FICHIER:
            cfg._cache = None
            cfg.charger()
        else:
            cfg.charger(registre)
        return True
    except Exception as e:  # noqa: BLE001 — registre invalide : on remet l'ancien texte
        registre.write_text(texte, encoding="utf-8")
        if registre == cfg.FICHIER:
            cfg._cache = None
        print(f"  !! déclaration annulée, registre invalide : {str(e)[:160]}", file=sys.stderr)
        return False


def declarer_sources(graphe: str, memoire: dict, registre: Path | None = None) -> list[str]:
    """Déclare au registre, pour ce graphe, les domaines trouvés SEUIL_DECLARATION fois dont l'étude a dit OUI (porteur,
    09.10.2026 : « deux fois me semble suffire »). Les mots-clés de la première source du graphe qui en a servent de
    filtre, à relire. Un domaine déjà au registre, ou de la liste JAMAIS, n'est pas redéclaré. Rend les lignes de rapport."""
    from newskit import prospecter_source as ps  # noqa: PLC0415
    aujourdhui = time.strftime("%Y-%m-%d")
    toutes = cfg.toutes()
    flux_connus = {str(s.propres.get("flux") or "").rstrip("/") for s in toutes if s.propres.get("flux")}
    domaines_connus = {domaine_de(str(s.propres.get("site") or s.propres.get("flux") or ("https://" + str(s.propres.get("domaine") or "")) or ""))
                       for s in toutes}
    lexique = next((s.propres["mots_cles"] for s in cfg.toutes(graphe=graphe) if s.propres.get("mots_cles")), None)
    cles = {s.cle for s in toutes}
    out = []
    for dom, d in memoire["domaines"].items():
        e = d.get("etude") or {}
        # Une source déclarée puis retirée du registre par le porteur repart de zéro (09.10.2026) : si l'éclaireur la
        # retrouve deux fois, elle est redéclarée — le retrait n'est pas un refus définitif.
        if d.get("declaree", {}).get("cle") and d["declaree"]["cle"] not in cles:
            d["retiree"] = {"date": aujourdhui, "cle": d["declaree"]["cle"], "fois": d.get("retiree", {}).get("fois", 0) + 1}
            d.pop("declaree"); d["depots"] = 0; d["graphes"] = []
            out.append(f"  source retirée du registre depuis sa déclaration : {dom} — recomptée depuis zéro, redéclarée si retrouvée {SEUIL_DECLARATION} fois")
            continue
        if d.get("depots", 0) < SEUIL_DECLARATION or e.get("verdict") != "OUI" or d.get("declaree") or graphe not in d.get("graphes", []):
            continue
        if any(dom == j or dom.endswith("." + j) for j in ps.JAMAIS):
            continue
        if dom in domaines_connus or (e.get("flux") or "").rstrip("/") in flux_connus:
            d["declaree"] = {"date": aujourdhui, "note": "déjà au registre"}
            continue
        cle, bloc = bloc_source(graphe, dom, e, d["depots"], lexique)
        if inserer_source(bloc, registre):
            d["declaree"] = {"date": aujourdhui, "graphe": graphe, "cle": cle}
            out.append(f"  source déclarée au registre ({graphe}) : {dom} — clé {cle}, trouvée {d['depots']} fois, "
                       f"{e.get('mots', 0)} mots par entrée ; nom et mots-clés à relire dans sources.yaml")
        else:
            out.append(f"  !! {dom} : déclaration refusée par le registre, bloc annulé")
    return out


def passage(graphe: str, controle: bool = False) -> int:
    bornes = cfg.eclaireur(graphe)
    if not bornes["actif"]:
        print(f"  {graphe} : éclaireur inactif (registre)")
        return 0
    if not cfg.toutes("Recherche web", graphe=graphe):
        print(f"  {graphe} : aucune source « Recherche web » au registre, rien n'est cherché")
        return 0
    if not controle:
        # Les lacunes : relever celles du serveur en ligne, puis clore celles auxquelles le graphe répond désormais.
        try:
            lacunes.relever()
        except Exception as e:                           # noqa: BLE001 — sans accès au serveur, on cherche quand même
            print(f"  lacunes du serveur non relevées : {str(e)[:100]}")
        G = te.charger(graphe)
        if G is not None:
            closes = lacunes.rejouer(graphe, G)
            if closes:
                print(f"  {closes} lacune(s) close(s) : le graphe y répond maintenant")
    points = points_du_jour(graphe, bornes)
    if not points:
        print(f"  {graphe} : aucun point à chercher")
        return 0
    for genre, enonce in points:
        print(f"    point  [{genre}] {enonce[:110]}")
    if controle:
        print(f"  (--controle : aucun appel ; bornes {bornes})")
        return 0
    pages, hors = chercher(graphe, points, bornes)
    pages = pages[:bornes["pages"]]
    neufs = [e for g_, e in points if g_ == "sujet neuf"]
    if neufs:
        etat_t = te.lire_etat(graphe)
        for lib in neufs:
            if lib in etat_t["nouveaux"]:
                etat_t["nouveaux"][lib]["nuits"] = etat_t["nouveaux"][lib].get("nuits", 0) + 1
        te.ecrire_etat(graphe, etat_t)
    cles = [lacunes.cle_de(x["termes"]) for _, x in lacunes.a_chercher(graphe, bornes.get("lacunes", 0))]
    lacunes.marquer(graphe, cles, [cles[i] for i in hors if i < len(cles)])        # les lacunes sont les premiers points
    peu_couverts.marquer(graphe, [e.split(" — ", 1)[-1] for g_, e in points if g_ == "peu couvert"])
    aujourdhui = time.strftime("%Y-%m-%d")
    lignes = ingerer_lot(graphe, pages, points, bornes, aujourdhui)
    ecrites = sum(l.startswith("  ok") for l in lignes)
    print(f"  {graphe} : {len(points)} point(s) cherchés, {len(pages)} page(s) rendues, {ecrites} fiche(s) écrite(s)")
    memoire = lire_memoire()
    # Les domaines trouvés deux fois, au flux lisible, entrent au registre d'eux-mêmes (09.10.2026) ; la collecte de la
    # nuit suivante les lit. Ceux qui restent « à suivre » sont ceux dont l'étude n'a pas conclu.
    for ligne in declarer_sources(graphe, memoire):
        print(ligne)
    MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    for domaine, d in memoire["domaines"].items():
        e = d.get("etude") or {}
        if d["depots"] >= SEUIL_DECLARATION and e.get("verdict") == "OUI" and graphe in d["graphes"] and not d.get("declaree"):
            print(f"  source à suivre : {domaine} trouvé {d['depots']} fois, flux lisible ({e.get('mots', 0)} mots) — "
                  f"python3 -m newskit.prospecter_source {domaine}")
    return 0


def relire_et_ecrire(graphe: str, u: str, genre: str, enonce: str, bornes: dict, connues: set[str], aujourdhui: str,
                     ligne: str) -> str:
    """Une page que `corps_de_page` a écartée (trop courte à ses yeux) est relue par `lecture_web.lire` (trafilatura) et
    entre si elle passe la borne des mots ; sinon la ligne « écartée » est rendue telle quelle. Partagé par les liens
    déposés et par le comblement du banc par thème (11.10.2026 : ai-regulation.com, 86 mots pour l'un, 829 pour l'autre)."""
    from newskit import depots  # noqa: PLC0415
    from newskit import lecture_web  # noqa: PLC0415
    lu = lecture_web.lire(u)
    if lu["mots"] < bornes["mots_min"]:
        return ligne
    titre = lu["titre"] or domaine_de(u)
    date, origine = date_du_document("", lu.get("date") or "", u, aujourdhui)
    champs = {"title": titre, "subtitle": "", "author": "", "date": date, "url": u, "plateforme": "Recherche web",
              "publication": domaine_de(u), "complet": True, "collected": aujourdhui, "origine_date": origine,
              "source": f"donné par le porteur — {genre} : {enonce[:90]}"}
    dossier = DOSSIER / graphe
    dossier.mkdir(parents=True, exist_ok=True)
    try:
        fiche.ecrire(dossier / f"{date or aujourdhui}_{depots.slug(u, titre)}.md", champs, f"# {titre}\n\n{lu['texte']}")
    except fiche.IdentiteDejaVue as deja:              # donnée par le porteur, déjà dans le graphe
        connues.add(u.rstrip("/"))
        return f"  =   [{genre}] {titre[:64]} — déjà vue ({deja.cle}, {deja.existante.name})"
    connues.add(u.rstrip("/"))
    return f"  ok  [{genre}] {titre[:64]} — {lu['mots']} mots — {domaine_de(u)} (relue par {lu['voie']})"


def bilan(lignes: list[str]) -> dict[str, int]:
    """Le bilan d'un lot d'ingestion, d'après les lignes d'`ingerer` : écrites, déjà au graphe, écartées."""
    return {"nouvelles": sum(l.startswith("  ok") for l in lignes), "deja_vues": sum(l.startswith("  =") for l in lignes),
            "refusees": sum(l.startswith("  --") for l in lignes)}


def dire_bilan(graphe: str, lignes: list[str]) -> None:
    b = bilan(lignes)
    print(f"  {graphe} : {b['nouvelles']} nouvelle(s), {b['deja_vues']} déjà vue(s), {b['refusees']} refusée(s)")


def ingerer_lot(graphe: str, pages: list, points: list[tuple[str, str]], bornes: dict,
                aujourdhui: str | None = None) -> list[str]:
    """La porte d'un lot de pages (la recherche de la nuit, la deep research d'une naissance) : chaque page passe par
    `ingerer`, qui ne lit que les documents absents de l'index d'identité ; un document écrit dans le lot y entre
    aussitôt, sa seconde forme n'est pas relue. Dit chaque ligne et le bilan (CHANTIER_GENERIQUE.md § 4.2)."""
    aujourdhui = aujourdhui or time.strftime("%Y-%m-%d")
    memoire, connues = lire_memoire(), adresses_connues(graphe)
    lignes = [ingerer(graphe, p if isinstance(p, dict) else {"url": p, "titre": "", "point": 0}, points, bornes,
                      connues, memoire, aujourdhui) for p in pages]
    DOSSIER.mkdir(parents=True, exist_ok=True)
    MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    for ligne in lignes:
        print(ligne)
    dire_bilan(graphe, lignes)
    return lignes


def ingerer_adresses(graphe: str, urls: list[str], genre: str, enonce: str) -> int:
    """Des pages données par le porteur, écrites comme fiches de l'éclaireur, sans recherche ni appel (09.10.2026,
    pages de Jev). `ingerer` lit par `corps_de_page` ; une page qu'il écarte est relue par `lecture_web.lire`
    (trafilatura), et entre si elle passe la borne des mots. Rend le nombre de fiches écrites."""
    bornes, aujourdhui = cfg.eclaireur(graphe), time.strftime("%Y-%m-%d")
    memoire, connues = lire_memoire(), adresses_connues(graphe)
    ecrites, lignes = 0, []
    for u in urls:
        u = u.strip()
        ligne = ingerer(graphe, {"url": u, "titre": "", "point": 0}, [(genre, enonce)], bornes, connues, memoire, aujourdhui)
        if ligne.startswith("  --  écartée"):
            ligne = relire_et_ecrire(graphe, u, genre, enonce, bornes, connues, aujourdhui, ligne)
        ecrites += ligne.startswith("  ok")
        print(ligne)
        lignes.append(ligne)
    MEMOIRE.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    dire_bilan(graphe, lignes)
    return ecrites


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--controle", action="store_true", help="dire les points du jour et les bornes, sans appel")
    ap.add_argument("--etat", action="store_true", help="la mémoire des domaines trouvés")
    ap.add_argument("--ingerer", nargs="+", metavar="URL", help="écrire ces pages comme fiches, sans recherche ni appel")
    ap.add_argument("--genre", default="demande du porteur", help="avec --ingerer : le genre noté dans la fiche")
    ap.add_argument("--enonce", default="", help="avec --ingerer : l'énoncé noté dans la fiche")
    args = ap.parse_args()
    if args.etat:
        for domaine, d in sorted(lire_memoire()["domaines"].items(), key=lambda kv: -kv[1]["depots"]):
            print(f"  {d['depots']:3d}  {domaine}  ({', '.join(d['graphes'])})")
        return 0
    if args.ingerer:
        n = ingerer_adresses(cfg.nom_actuel(args.graphe), args.ingerer, args.genre, args.enonce)
        print(f"  {n} fiche(s) écrite(s) sur {len(args.ingerer)}")
        return 0 if n else 1
    return passage(cfg.nom_actuel(args.graphe), args.controle)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Collecteur arXiv : deux modes, une seule porte, un seul chemin d'écriture.

Pourquoi pas de parseur PDF. arXiv publie ses métadonnées et son résumé de
façon structurée et gratuite. Analyser un PDF pour retrouver un titre qu'une
API donne déjà serait la faute que le corpus documente ailleurs : extraire ce
qu'une source livre déjà. Le texte intégral est un autre sujet, hors de ce
script.

Une seule porte : OAI-PMH (`/oai2`), le protocole de moisson. L'API de
recherche `/api/query` a été retirée le 13.09.2026, sur mesure : notre usage
est de la moisson — huit jours de parutions, des centaines de notices citées —
et arXiv la refuse sur l'API de recherche (429, puis quinze secondes de délai
imposé) tout en la servant sans broncher sur OAI-PMH. Garder une porte qu'on
se fait fermer, pour le plaisir d'avoir un secours, revient à programmer ses
propres échecs.

Deux modes.

    --citations   Les papiers que le corpus cite sans les posséder. Une notice
                  par identifiant (GetRecord), les plus cités d'abord.

    --veille      Ce que le domaine a publié dans la fenêtre. Une moisson par
                  archive (ListRecords sur cs, eess, stat), puis le filtre par
                  thème se fait chez nous : plus de plafond par thème, et un
                  thème ajouté s'applique à une récolte déjà faite.

Sortie : une fiche par papier dans sources/arxiv/, au contrat commun
(voir newskit.fiche), le résumé pour corps, donc `complet: false`.

Politesse : trois secondes entre deux requêtes, et le `Retry-After` d'un 503
est respecté. Comptez cinq minutes pour cent notices citées.

Usage :
    python3 -m newskit.collecte_arxiv --citations
    python3 -m newskit.collecte_arxiv --citations --limite 50
    python3 -m newskit.collecte_arxiv --veille --jours 8
    python3 -m newskit.collecte_arxiv --veille --requete "knowledge graph" --jours 30
    python3 -m newskit.collecte_arxiv --redater      # répare les dates de révision
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit.extraire_squelette import canon_arxiv, est_exclu, liens_du_texte, nettoyer  # noqa: E402

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
_VEILLE = cfg.veille_arxiv()
OUT = RACINE / "sources" / (_VEILLE.get("dossier") or "arxiv")   # papiers cités, même sans veille
DELAI = 3.0          # secondes entre deux requêtes, demandé par arXiv
from newskit.commun import agent_robot  # noqa: E402

# Catégories et thèmes viennent du registre (sources.yaml, section
# veille_arxiv) : les modifier ne demande pas de toucher à ce script.
#
# Deux raisons de les garder peu nombreux, consignées là où on les édite :
# cs.LG seul publie environ deux cents articles par jour, et chaque thème est
# cherché comme phrase exacte dans le résumé, donc une expression non attestée
# dans la littérature ne renvoie rien.
CATEGORIES = list(_VEILLE.get("categories") or [])
THEMES = list(_VEILLE.get("themes") or [])
# Une veille par graphe (02.10.2026) : chaque graphe a ses thèmes et son dossier.
# La moisson est commune, le filtre est appliqué graphe par graphe.
VEILLES = cfg.veilles_arxiv()
DOSSIERS_ARXIV = [RACINE / "sources" / v["dossier"] for v in VEILLES]


# --- Client ----------------------------------------------------------------


def _proxy_annonce() -> str:
    """Dit si un proxy est actif, sans jamais afficher ses identifiants.

    urllib lit `http_proxy` de l'environnement de lui-même. Ce qui est ajouté
    ici, c'est la lisibilité : une collecte qui passe par un tiers doit le dire
    dans sa sortie, sinon on ne sait plus qui a parlé à arXiv. La valeur peut
    contenir un identifiant et un mot de passe : seuls l'hôte et le port sont
    affichés.

    Un mot sur l'usage. Un 429 d'arXiv est une demande de ralentir, et le délai
    de trois secondes entre deux requêtes est ce qu'arXiv demande. Un proxy ne
    supprime pas cette demande, il la contourne : à n'employer que pour un
    blocage résiduel, pas comme régime normal.
    """
    for cle in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        v = os.environ.get(cle)
        if v:
            h = urllib.parse.urlsplit(v)
            return f"{cle}={h.hostname}:{h.port or ''}"

    # Rien dans l'environnement : le registre peut déclarer une crédence
    # `proxy`, déposée au trousseau. Elle est lue ici et posée dans
    # l'environnement du seul processus courant — elle n'est écrite nulle part.
    try:
        if "proxy" in ((cfg.charger()["credences"].get("sessions") or {})):
            v = cfg.session("proxy")
            if v:
                os.environ["http_proxy"] = os.environ["https_proxy"] = v
                h = urllib.parse.urlsplit(v)
                return f"trousseau={h.hostname}:{h.port or ''}"
    except Exception as e:
        print(f"  !! crédence de proxy illisible ({e}) ; requêtes en direct", file=sys.stderr)
    return ""





# --- Client OAI-PMH ---------------------------------------------------------

OAI = "http://export.arxiv.org/oai2"
NS_OAI = {"oai": "http://www.openarchives.org/OAI/2.0/",
          "ar": "http://arxiv.org/OAI/arXiv/"}


def _oai_requete(params: dict, essais: int = 4, delai: int = 180) -> ET.Element | None:
    """Une requête OAI-PMH, avec les reprises que le protocole prévoit.

    Un 503 est la façon dont OAI-PMH dit « plus tard », avec un `Retry-After`
    qui dit combien : on l'attend. Toute autre erreur HTTP est définitive. Une
    erreur réseau est réessayée avec un recul croissant. Rend `None` quand rien
    n'a abouti — et l'appelant doit alors compter un refus, pas une absence.
    """
    url = f"{OAI}?{urllib.parse.urlencode(params)}"
    for essai in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": agent_robot()})
            with urllib.request.urlopen(req, timeout=delai) as r:
                return ET.fromstring(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 503 and essai < essais - 1:
                time.sleep(int(e.headers.get("Retry-After", 20)))
                continue
            print(f"  !! OAI-PMH : HTTP {e.code}", file=sys.stderr)
            return None
        except Exception as e:
            if essai == essais - 1:
                print(f"  !! OAI-PMH : {e}", file=sys.stderr)
                return None
            time.sleep(DELAI * (essai + 2))
    return None


def _oai_notice(aid: str) -> dict | None:
    """Une notice par son identifiant (GetRecord). Même forme que la moisson."""
    racine = _oai_requete({"verb": "GetRecord", "metadataPrefix": "arXiv",
                           "identifier": f"oai:arXiv.org:{aid}"}, delai=60)
    if racine is None:
        return None
    meta = racine.find(".//ar:arXiv", NS_OAI)
    return _notice_oai(meta) if meta is not None else None


def _champ(el, nom: str) -> str:
    """Le texte d'un sous-élément arXiv, espaces repliés, ou une chaîne vide."""
    e = el.find(f"ar:{nom}", NS_OAI)
    return " ".join(e.text.split()) if e is not None and e.text else ""


def mois_de_depot(aid: str) -> str:
    """La date de dépôt qu'un identifiant arXiv porte en lui, au premier du mois.

    `1706.03762` a été déposé en juin 2017 : les quatre premiers chiffres le
    disent, et ils ne changent jamais. C'est la seule date fiable que la notice
    OAI-PMH permette d'établir, parce que son champ `created` est celui de la
    **dernière version** — pour ce même papier, août 2023, sa septième révision.
    Deux cent cinquante fiches avaient été datées ainsi avant qu'un test ne le
    montre. On perd le jour, on garde le mois juste ; pour un graphe à deux
    horloges, un mois vrai vaut mieux qu'un jour faux.
    """
    m = re.match(r"^(?:[a-z-]+(?:\.[a-z]{2})?/)?(\d{2})(\d{2})", aid.lower())
    if not m:
        return ""
    aa, mm = m.group(1), m.group(2)
    if not 1 <= int(mm) <= 12:
        return ""
    return f"{'20' if int(aa) < 90 else '19'}{aa}-{mm}-01"


def _notice_oai(meta) -> dict | None:
    """Un élément <arXiv> d'OAI-PMH vers la forme commune du script."""
    champ = lambda nom: _champ(meta, nom)  # noqa: E731
    auteurs = []
    for a in meta.findall("ar:authors/ar:author", NS_OAI):
        nom = " ".join(x for x in (_champ(a, "forenames"), _champ(a, "keyname")) if x)
        if nom:
            auteurs.append(nom)
    cats = champ("categories")
    if not champ("id"):
        return None
    aid = champ("id").lower()
    return {
        "arxiv_id": aid,
        "version": "",
        "title": champ("title"),
        "summary": champ("abstract"),
        "authors": auteurs,
        # `created` est la date de la dernière version, pas du dépôt : voir
        # `mois_de_depot`. Elle reste disponible comme `updated`.
        "published": mois_de_depot(aid) or champ("created")[:10],
        "updated": (champ("updated") or champ("created"))[:10],
        "primaire": cats.split()[0] if cats else "",
        "categories": cats,
        "doi": champ("doi"),
        "comment": champ("comments"),
    }


CACHE_MOISSON = RACINE / "cache" / "moisson"


def moisson_oai(depuis: str, archives: tuple[str, ...] = ("cs", "eess", "stat"),
                fraicheur_h: int = 24) -> list[dict]:
    """Moissonne les notices déposées depuis une date, par archive.

    OAI-PMH ne sait pas chercher par mots : il moissonne par archive et par
    date, c'est tout ce qu'il propose. D'où le renversement de méthode de la
    veille : au lieu de poser vingt-quatre questions à arXiv, on récolte la
    fenêtre une fois et on filtre chez nous (voir `filtrer_recolte`).

    Le change est avantageux. Aucune requête par thème, donc aucun plafond par
    thème à saturer, et un ajout de thème ne coûte plus rien : il s'applique à
    une récolte déjà faite. Le prix est le volume — environ mille notices par
    page, trois mégaoctets, six secondes.

    La récolte est gardée dans `cache/moisson/`, hors du dépôt, pendant
    `fraicheur_h` heures : c'est ce qui rend la promesse tenue. Une veille
    relancée le jour même pour un thème de plus relit le cache au lieu de
    redemander 28 000 notices à arXiv.
    """
    cache = CACHE_MOISSON / f"{depuis}_{'-'.join(archives)}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < fraicheur_h * 3600:
        notices = json.loads(cache.read_text(encoding="utf-8"))
        print(f"  récolte relue depuis {cache.relative_to(RACINE)} "
              f"({len(notices)} notices, moins de {fraicheur_h} h)")
        return notices
    notices: list[dict] = []
    for archive in archives:
        params = {"verb": "ListRecords", "metadataPrefix": "arXiv",
                  "set": archive, "from": depuis}
        pages = 0
        while True:
            racine = _oai_requete(params)
            if racine is None:
                break
            for meta in racine.findall(".//ar:arXiv", NS_OAI):
                n = _notice_oai(meta)
                if n:
                    notices.append(n)
            pages += 1
            print(f"    moisson {archive} : {pages} page(s), {len(notices)} notice(s)",
                  end="\r", file=sys.stderr)
            jeton = racine.find(".//oai:resumptionToken", NS_OAI)
            if jeton is None or not (jeton.text or "").strip():
                break
            params = {"verb": "ListRecords", "resumptionToken": jeton.text.strip()}
            time.sleep(DELAI)
        print(" " * 60, end="\r", file=sys.stderr)
    if notices:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(notices, ensure_ascii=False), encoding="utf-8")
    return notices


def par_identifiants(ids: list[str]) -> tuple[list[dict], list[str]]:
    """Métadonnées d'une liste d'identifiants, par OAI-PMH, une notice à la fois.

    Plus lent qu'un lot de cent — mais un lot de cent qu'on se fait refuser
    n'est pas plus rapide, il est seulement plus frustrant. Le protocole dit
    quand il est chargé, par un 503 et un `Retry-After` qu'on respecte.
    """
    resultats, echoues = [], []
    for i, aid in enumerate(ids, 1):
        notice = _oai_notice(aid)
        if notice and notice.get("arxiv_id"):
            resultats.append(notice)
        else:
            echoues.append(aid)
        print(f"    {i:>5} / {len(ids)}", end="\r", file=sys.stderr)
        time.sleep(DELAI)
    print(" " * 30, end="\r", file=sys.stderr)
    return resultats, echoues




# --- Écriture --------------------------------------------------------------


def _slug(titre: str) -> str:
    s = re.sub(r"[^\w\s-]", "", titre.lower())
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:60]


def ecrire(meta: dict, *, motif: str, dossier: Path | None = None) -> Path:
    """Écrit une fiche arXiv par le contrat commun.

    Le corps est le résumé, pas le texte intégral : c'est ce qu'arXiv publie en
    accès libre et structuré. `complet` est donc faux, honnêtement, puisque
    l'article lui-même n'est pas là.
    """
    aid = meta["arxiv_id"]
    chemin = (dossier or OUT) / f"{meta['published']}_{aid.replace('/', '_')}_{_slug(meta['title'])}.md"
    champs = {
        "title": meta["title"],
        "subtitle": meta["primaire"],
        "author": ", ".join(meta["authors"]),
        "date": meta["published"],
        "url": f"https://arxiv.org/abs/{aid}",
        "plateforme": "arXiv",
        "publication": "arXiv",
        "complet": False,          # résumé seul, le texte intégral n'est pas collecté
        "collected": time.strftime("%Y-%m-%d"),
        "source": f"arXiv ({motif})",
        "arxiv_id": aid,
        "categories": meta["categories"],
        "doi": meta["doi"],
        "comment": meta["comment"],
        "origine_date": "identifiant arXiv (mois de dépôt)",
    }
    corps = f"# {meta['title']}\n\n*{meta['primaire']} · {', '.join(meta['authors'])}*\n\n## Résumé\n\n{meta['summary']}\n"
    try:
        fiche.ecrire(chemin, champs, corps)
    except fiche.IdentiteDejaVue as deja:              # le papier est déjà dans le graphe par une autre voie
        print(f"  déjà vue : {deja}")
        return deja.existante
    return chemin


def identifiants_connus(dossier: Path | None = None) -> set[str]:
    """Identifiants arXiv déjà présents comme fiche dans un dossier (défaut : sources/arxiv/).

    Par dossier, donc par graphe : un papier cité par le corpus principal et
    retenu par la veille d'un autre graphe a une fiche dans chacun.
    """
    connus = set()
    for f in (dossier or OUT).glob("*.md"):
        champs, _ = fiche.lire_fichier(f)
        if champs.get("arxiv_id"):
            connus.add(champs["arxiv_id"])
    return connus


def identifiants_du_graphe(graphe: str) -> set[str]:
    """Identifiants arXiv qui ont déjà une fiche dans un dossier quelconque du graphe, sous-dossiers compris.

    L'éclaireur garde la page d'un papier qu'il trouve, avec son `arxiv_id`, hors du dossier arXiv : lui
    donner une seconde fiche ferait deux fiches sous un nœud (2302.12173, 19.10.2026 : un graphe de sécurité bloqué
    quatre nuits au contrôle de fraîcheur).
    """
    # L'index d'identité de fiche (06.11.2026) : l'identifiant vient aussi de l'adresse abs/pdf et du DOI 10.48550.
    return {k.removeprefix("arxiv:") for k in fiche.identites_du_graphe(graphe) if k.startswith("arxiv:")}


def identifiants_cites() -> Counter:
    """Identifiants arXiv cités par le corpus, avec le nombre de fiches citantes.

    Réutilise la normalisation du squeletteur : une seule définition de ce
    qu'est un identifiant arXiv canonique, partagée par les deux scripts.
    """
    compte: Counter = Counter()
    # Seul le graphe principal tire ses papiers cités dans le corpus : les
    # autres graphes (actualité) gardent leurs citations comme nœuds non lus,
    # décision du 27.09.2026, mesurée sur AI Secret (un papier par quinzaine).
    dossiers = cfg.dossiers_du_graphe(cfg.GRAPHE_PRINCIPAL)
    for dossier in dossiers:
        for f in sorted((RACINE / dossier).rglob("*.md")):
            if f.name.startswith("_") or f.parent in DOSSIERS_ARXIV:
                continue
            _, corps = fiche.lire_fichier(f)
            vus = set()
            for url_brute, _ in liens_du_texte(corps, 1):
                url = nettoyer(url_brute)
                if est_exclu(url):
                    continue
                aid = canon_arxiv(url)
                if aid:
                    vus.add(aid)
            for aid in vus:
                compte[aid] += 1
    return compte


# --- Modes -----------------------------------------------------------------


def mode_citations(limite: int | None) -> int:
    cites = identifiants_cites()
    # Un papier peut avoir sa fiche hors de la veille : l'éclaireur garde la page d'un papier
    # qu'il trouve. La récupérer ici en ferait une seconde fiche sous le même nœud.
    connus = identifiants_connus().union(*(identifiants_connus(RACINE / d)
                                           for d in cfg.dossiers_du_graphe(cfg.GRAPHE_PRINCIPAL)))
    manquants = [a for a in cites if a not in connus]
    manquants.sort(key=lambda a: (-cites[a], a))      # les plus cités d'abord
    if limite:
        manquants = manquants[:limite]

    print(f"  {len(cites)} papiers cités par le corpus, {len(connus)} déjà en fiche")
    print(f"  {len(manquants)} à récupérer\n")
    if not manquants:
        return 0

    metas, echoues = par_identifiants(manquants)
    obtenus = {m["arxiv_id"] for m in metas}
    for m in metas:
        ecrire(m, motif=f"cité par {cites[m['arxiv_id']]} fiche(s)")

    introuvables = [a for a in manquants if a not in obtenus and a not in echoues]
    print(f"  {len(metas)} fiches écrites dans {OUT.relative_to(RACINE)}")
    if echoues:
        print(f"  ALERTE : {len(echoues)} identifiant(s) non demandés, la requête ayant "
              f"échoué (arXiv bride, ou réseau). Ce ne sont pas des papiers introuvables : "
              f"relancer plus tard.", file=sys.stderr)
    if introuvables:
        print(f"  {len(introuvables)} identifiant(s) qu'arXiv ne connaît pas "
              f"(retiré, ou mal formé) :")
        for a in introuvables[:10]:
            print(f"    - {a}")
    # Une collecte incomplète rend un code non nul : le contrôle de fraîcheur
    # échouera de toute façon plus loin, et mieux vaut que la cause soit dite
    # ici, à l'étape qui l'a rencontrée.
    return 1 if echoues else 0


def filtrer_recolte(recolte: list[dict], themes: list[str], categories: set[str],
                    depuis: str, connus: set[str]) -> tuple[dict, list[str], dict]:
    """Applique les thèmes à une récolte. Rend (retenus, thèmes muets, vus par thème).

    Une notice est retenue si elle est dans une catégorie suivie, porte le
    thème comme phrase exacte dans son titre ou son résumé, a été déposée dans
    la fenêtre, et n'est ni déjà en fiche ni déjà retenue par un thème
    précédent — le premier thème qui la trouve la garde.

    Un thème « muet » n'a trouvé aucune notice dans la fenêtre, connues
    comprises. Ce n'est pas un refus : la récolte a bien eu lieu. C'est une
    expression que la littérature n'emploie pas telle quelle, et qui doit
    sortir du registre si elle reste muette.
    """
    dans_domaine = [n for n in recolte if categories & set(n["categories"].split())]
    retenus: dict[str, dict] = {}
    muets: list[str] = []
    vus: dict[str, int] = {}
    for theme in themes:
        bas = theme.lower()
        trouves = [n for n in dans_domaine if bas in (n["title"] + " " + n["summary"]).lower()]
        vus[theme] = len(trouves)
        if not trouves:
            muets.append(theme)
        for n in trouves:
            # Dans la fenêtre : sur la date de dernière modification, pas sur
            # `published`. Depuis le 13.09, `published` vaut le premier du mois
            # de dépôt ; comparée à un `depuis` au 11, elle rejetait tout papier
            # déposé dans le mois. La veille du 19.09 a rendu zéro fiche sur
            # 14 000 notices moissonnées, et s'est déclarée « ok ».
            dans_fenetre = (n.get("updated") or n["published"]) >= depuis
            if dans_fenetre and n["arxiv_id"] not in connus and n["arxiv_id"] not in retenus:
                retenus[n["arxiv_id"]] = {**n, "_theme": theme}
    return retenus, muets, vus


MUETS = RACINE / "sources" / "arxiv" / "_muets.json"
FENETRES_AVANT_RETRAIT = 2   # un thème muet sur deux fenêtres distantes d'au moins six jours sort du registre


def retirer_themes_muets(muets: list[str], aujourd_hui: str, registre: Path = cfg.FICHIER,
                         etat: Path = MUETS, evalues: set[str] | None = None) -> list[str]:
    """Retire du registre les thèmes muets sur deux fenêtres, et rend leur liste.

    La note de reprise disait « à retirer s'ils le restent sur une seconde
    fenêtre » ; personne ne le faisait, et neuf thèmes muets traînaient dans
    chaque rapport (27.09.2026). Le retrait est fait ici, par une édition de
    la seule ligne du thème dans `sources.yaml` — les commentaires du registre
    sont conservés, un rechargement YAML les perdrait — et dit dans la sortie.
    Un thème qui redevient visible perd son compteur.
    """
    histo = json.loads(etat.read_text(encoding="utf-8")) if etat.exists() else {}
    # Un thème ne perd son compteur que s'il a été éprouvé ce passage-ci : la veille d'un
    # seul graphe (`--graphe`) ne dit rien des thèmes des autres.
    for t in list(histo):
        if t not in muets and (evalues is None or t in evalues):
            del histo[t]
    for t in muets:
        dates = histo.setdefault(t, [])
        if not dates or dates[-1] != aujourd_hui:
            dates.append(aujourd_hui)
    retires = []
    for t, dates in list(histo.items()):
        if len(dates) >= FENETRES_AVANT_RETRAIT:
            d0 = time.mktime(time.strptime(dates[0], "%Y-%m-%d"))
            d1 = time.mktime(time.strptime(dates[-1], "%Y-%m-%d"))
            if d1 - d0 >= 6 * 86400:
                retires.append(t)
    if retires:
        texte = registre.read_text(encoding="utf-8")
        lignes = texte.split("\n")
        for t in retires:
            for i, l in enumerate(lignes):
                if l.strip() == f"- {t}" and l.startswith("    "):
                    lignes[i] = f"    # retiré le {aujourd_hui} par collecte_arxiv.py : muet sur {len(histo[t])} fenêtres ({histo[t][0]} → {histo[t][-1]})"
                    break
            del histo[t]
        registre.write_text("\n".join(lignes), encoding="utf-8")
    etat.parent.mkdir(parents=True, exist_ok=True)
    etat.write_text(json.dumps(histo, indent=1, ensure_ascii=False), encoding="utf-8")
    return retires


def mode_veille(jours: int, themes: list[str] | None = None, graphe: str | None = None) -> int:
    """Ce que le domaine a publié dans la fenêtre : une moisson, puis le filtre.

    Sans `themes`, chaque graphe du registre applique les siens à la même
    moisson et range ses papiers dans son dossier. Avec (--requete), un essai
    sur le seul graphe principal.
    """
    veilles = ([{"graphe": cfg.GRAPHE_PRINCIPAL, "dossier": _VEILLE.get("dossier") or "arxiv", "themes": themes,
                 "categories": CATEGORIES}]
               if themes else [v for v in VEILLES if not graphe or v["graphe"] == graphe])
    if not veilles:
        print(f"  le graphe {graphe} n'a pas de veille arXiv au registre")
        return 0
    depuis = time.strftime("%Y-%m-%d", time.gmtime(time.time() - jours * 86400))
    print(f"  veille sur {sum(len(v['themes']) for v in veilles)} thème(s), papiers déposés depuis {depuis}")
    print(f"  {sum(len(identifiants_connus(RACINE / 'sources' / v['dossier'])) for v in veilles)} papiers déjà en fiche\n")

    recolte = moisson_oai(depuis)
    if not recolte:
        # Un refus n'est pas une absence : une moisson vide est une panne, pas
        # une semaine sans publication.
        print("  ALERTE : moisson vide ; arXiv n'a rien rendu, ce ne sont pas des thèmes "
              "sans résultat. Relancer plus tard.", file=sys.stderr)
        return 1
    print(f"  {len(recolte)} notice(s) moissonnées depuis {depuis}\n")
    ecrites, muets = 0, []
    for v in veilles:
        dossier = RACINE / "sources" / v["dossier"]
        dossier.mkdir(parents=True, exist_ok=True)
        retenus, muets_v, vus = filtrer_recolte(recolte, v["themes"], set(v.get("categories") or CATEGORIES), depuis,
                                                identifiants_connus(dossier) | identifiants_du_graphe(v["graphe"]))
        if len(veilles) > 1:
            print(f"  graphe {v['graphe']} → {dossier.relative_to(RACINE)} : {len(retenus)} nouveau(x)")
        for theme in v["themes"]:
            neufs = sum(1 for n in retenus.values() if n["_theme"] == theme)
            print(f"    {theme:<34} {vus[theme]:>4} vus, {neufs:>4} nouveaux")
        for n in retenus.values():
            ecrire(n, motif=f"veille par moisson, thème « {n['_theme']} »", dossier=dossier)
        ecrites += len(retenus); muets += muets_v
    # Une seule ligne de total : la routine la lit (routine_hebdo.lire_veille).
    print(f"\n  {ecrites} fiche(s) écrite(s) dans "
          + ", ".join(str((RACINE / 'sources' / v['dossier']).relative_to(RACINE)) for v in veilles))
    retires = retirer_themes_muets(muets, time.strftime("%Y-%m-%d"),
                                   evalues={t for v in veilles for t in v["themes"]})
    if retires:
        print(f"  {len(retires)} thème(s) retiré(s) de sources.yaml, muets sur deux fenêtres : "
              f"{', '.join(retires)}")
    encore = [t for t in muets if t not in retires]
    if encore:
        print(f"  {len(encore)} thème(s) muet(s) sur cette fenêtre, retirés d'eux-mêmes s'ils le restent "
              f"à la prochaine : {', '.join(encore)}")
    return 0


def mode_redater() -> int:
    """Redate les fiches dont la date est postérieure de plus d'un mois au dépôt.

    Ces fiches ont été écrites par OAI-PMH avant qu'on sache que `created` est
    la date de la dernière version. La réparation vit ici, dans le script qui a
    produit le défaut, pour rester trouvable. Le nom du fichier porte la date :
    il est renommé avec, l'identité du nœud étant l'identifiant arXiv et non
    le chemin.
    """
    redatees = 0
    for f in sorted(f for d in DOSSIERS_ARXIV for f in d.glob("*.md")):
        champs, corps = fiche.lire_fichier(f)
        aid = str(champs.get("arxiv_id") or "")
        depot = mois_de_depot(aid)
        date = str(champs.get("date") or "")
        if not depot or not date:
            continue
        ecart = (int(date[:4]) - int(depot[:4])) * 12 + int(date[5:7]) - int(depot[5:7])
        if ecart <= 1:
            continue                    # dans le mois du dépôt ou le suivant : plausible
        champs["date"], champs["origine_date"] = depot, "identifiant arXiv (mois de dépôt)"
        nouveau = f.with_name(f"{depot}_{f.name.split('_', 1)[1]}")
        fiche.ecrire(nouveau, champs, corps, remplace=f)      # un déplacement : la fiche garde son identité
        if nouveau != f:
            f.unlink()
        redatees += 1
    print(f"  {redatees} fiche(s) redatée(s) au mois de dépôt")
    return 0


def mode_repartir(controle: bool) -> int:
    """Range chaque fiche de veille dans le dossier du graphe qui porte son thème.

    Écrit pour la coupe du 02.10.2026 (thèmes d'image et de 3D sortis du graphe
    principal), et rejouable : après un thème déplacé d'un graphe à l'autre au
    registre, il remet les fiches à leur place. Le thème se lit dans le champ
    `source` de la fiche, tel que la veille l'a écrit. Une fiche sans thème
    (papier cité) ou d'un thème qui n'est plus au registre ne bouge pas.

    Un papier que le corpus principal cite reste aussi dans sources/arxiv : il
    y est copié, pas déplacé, sinon le graphe principal le réduirait à une
    coquille jusqu'à la prochaine collecte des citations.
    """
    import shutil  # noqa: PLC0415
    par_theme = {t: RACINE / "sources" / v["dossier"] for v in VEILLES for t in v["themes"]}
    cites = identifiants_cites()
    deplacees: Counter = Counter()
    copiees = 0
    for d in DOSSIERS_ARXIV:
        for f in sorted(d.glob("*.md")):
            champs, _ = fiche.lire_fichier(f)
            m = re.search(r"thème « (.+?) »", str(champs.get("source") or ""))
            cible = par_theme.get(m.group(1)) if m else None
            if cible is None or cible == d or (cible / f.name).exists():
                continue
            garde = d == OUT and champs.get("arxiv_id") in cites
            if not controle:
                cible.mkdir(parents=True, exist_ok=True)
                (shutil.copy2 if garde else shutil.move)(str(f), str(cible / f.name))
            copiees += garde
            deplacees[f"{d.relative_to(RACINE)} → {cible.relative_to(RACINE)}"] += 1
    for trajet, n in deplacees.items():
        print(f"  {n} fiche(s) : {trajet}")
    if copiees:
        print(f"  dont {copiees} copiée(s) et non déplacée(s) : le corpus principal les cite")
    if not deplacees:
        print("  chaque fiche de veille est déjà dans le dossier du graphe de son thème")
    if controle:
        print("  (--controle : rien n'a été écrit)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--repartir", action="store_true",
                      help="ranger chaque fiche de veille dans le dossier du graphe de son thème")
    mode.add_argument("--citations", action="store_true", help="papiers déjà cités par le corpus")
    mode.add_argument("--veille", action="store_true", help="papiers récents du domaine")
    mode.add_argument("--redater", action="store_true",
                      help="réparer les fiches datées par une révision et non par le dépôt")
    ap.add_argument("--limite", type=int, help="citations : nombre maximum de papiers à récupérer")
    ap.add_argument("--jours", type=int, default=8, help="veille : fenêtre en jours (défaut 8)")
    ap.add_argument("--requete", action="append", help="veille : thème à interroger (répétable)")
    ap.add_argument("--controle", action="store_true", help="repartir : dire ce qui bougerait, sans rien écrire")
    ap.add_argument("--graphe", help="veille : seulement les thèmes de ce graphe (défaut : tous les graphes)")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    proxy = _proxy_annonce()
    if proxy:
        print(f"  requêtes arXiv via proxy ({proxy})")
    if args.repartir:
        return mode_repartir(args.controle)
    if args.redater:
        return mode_redater()
    if args.citations:
        return mode_citations(args.limite)
    return mode_veille(args.jours, args.requete, args.graphe)


if __name__ == "__main__":
    sys.exit(main())

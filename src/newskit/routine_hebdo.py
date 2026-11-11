#!/usr/bin/env python3
"""Routine hebdomadaire : l'étage qui peut tourner sans personne.

La chaîne n'est pas homogène, et c'est le fait central de sa conception. Les
collecteurs qui passent par une API publique tournent seuls, et une session
déposée au trousseau étend cet étage aux billets Substack réservés. Medium
reste en dehors : sa protection anti-robot refuse les requêtes de script (403)
même munies du cookie, et seule une navigation réelle passe. Une routine qui
prétendrait tout faire échouerait la moitié du temps en silence, ce qui est le
défaut que ce projet passe son temps à corriger.

Cette routine fait donc l'étage automatique, et **signale** ce qui reste à faire
à la main plutôt que de le simuler.

Étage automatique, ici :
    lecture du registre des sources         — bloquante
    collecte LinkedIn, flux RSS, revues, Substack, Medium, arXiv (citations et veille)
    validation du contrat de fiche          — bloquante
    couche 1 du graphe                      — bloquante
    contrôle de fraîcheur                   — bloquant
    composition du graphe, puis la carte
    commit, uniquement si tout est passé, puis envoi au dépôt distant
    résumé par courriel, dans tous les cas, arrêt bloquant compris

Étage assisté, signalé et non fait :
    Medium, et tout Substack payant qu'une session n'aurait pas débloqué
    couche 2, si une fiche dense a changé : treize exécutions de sous-agents

Une étape bloquante qui échoue arrête la routine **avant** le commit, et laisse
le rapport. Rien n'est committé sur un corpus non conforme ou un graphe en
retard : un dépôt qui enregistre sereinement un état faux est pire que pas de
routine du tout.

Usage :
    python3 -m newskit.routine_hebdo
    python3 -m newskit.routine_hebdo --sans-commit   # tout sauf le commit
    python3 -m newskit.routine_hebdo --sans-collecte # rejoue graphe et carte
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

# L'interpréteur des étapes est celui qui lance la routine : c'est lui que
# l'agent launchd désigne, et c'est là que graphify est installé. Un chemin
# codé en dur vers une autre machine a fait échouer la première reprise.
PYTHON = os.environ.get("SE_PYTHON", sys.executable)
RAPPORT = RACINE / "ROUTINE.md"
EMPREINTES = RACINE / "graphe" / "couche2" / "empreintes.json"

# Les fiches sur lesquelles la couche 2 a été extraite. Si l'une change, la
# couche conceptuelle est en retard sur elle. La liste vient du registre
# (sources.yaml, champ `dense`), pour qu'une source neuve ne soit pas oubliée
# ici après avoir été déclarée là-bas.
from newskit import commun  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — le registre des graphes du média
from newskit import couts  # noqa: E402
from newskit import reglages  # noqa: E402 — les réglages du média

DOSSIERS_DENSES = cfg.dossiers_denses()
PYRAMIDE_PLAFOND = 300  # notions rédigées par nuit et par graphe, sauf `pyramide_plafond` au registre (08.10.2026)
PYRAMIDE_PLAFOND_PASSAGE: int | None = None  # --pyramide-plafond : la borne de ce seul passage (porteur, 10.10.2026)
CARTES_PLAFOND = 20  # cartes de sujets dressées par nuit et par graphe, sauf `cartes_plafond` au registre (09.10.2026 ; 0,16 $ la carte)


class Journal:
    """Accumule les lignes du rapport et les affiche au fil de l'eau."""

    def __init__(self) -> None:
        self.lignes: list[str] = []
        self.alertes: list[str] = []
        self.attention: list[str] = []

    def dire(self, texte: str = "") -> None:
        print(texte)
        self.lignes.append(texte)

    def alerte(self, texte: str) -> None:
        self.dire(f"  ÉCHEC  {texte}")
        self.alertes.append(texte)

    def signal(self, texte: str) -> None:
        self.dire(f"  à faire  {texte}")
        self.attention.append(texte)


def cerveau_cortexdb(j: Journal, g: str, etiquette: str) -> None:
    """Le cerveau CortexDB du graphe, reconstruit après la composition, l'index, la pyramide et les cartes (CortexDB est la base du moteur,
    22.10.2026) : lots et synthèses versés tels quels, vecteurs repris ou encodés en local, aucun appel au modèle.
    Seulement pour un graphe engagé au registre (`graphes.<nom>.cortexdb: true`)."""
    if cfg.verse_cortexdb(g):
        lancer(
            j, f"cerveau CortexDB{etiquette}", ["scripts/cerveau_cortexdb.py", "--graphe", g], garder=1, delai=5 * 3600
        )  # 3 h d'attente d'un encodeur au plus


# Les sources qui ne suivent pas une actualité : le canon et les dépôts se remplissent à la demande, une bibliothèque ou
# un dossier local au gré du porteur. Leur silence n'est pas une panne.
SANS_ACTUALITE = {"Référence", "Dépôt", "Bibliothèque locale", "Dossier local"}
FENETRE_MUETTE = 30  # jours sans fiche collectée avant de signaler une source (03.11.2026)


def _declaree_le(src):
    """Le jour où le dossier de la source est né (sa première collecte) ; aujourd'hui s'il n'existe pas encore."""
    import datetime as dt  # noqa: PLC0415

    try:
        st = src.chemin.stat()
    except OSError:
        return dt.date.today()
    return dt.date.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime))


def sources_muettes(graphe: str, aujourd_hui=None, declaree_le=_declaree_le) -> list:
    """Les sources d'actualité du graphe, déclarées depuis plus de FENETRE_MUETTE jours, dont aucune fiche n'a été
    collectée dans cette fenêtre (champ `collected`). 03.11.2026 : 28 sources sur 151 d'un graphe l'étaient, et une
    source qui s'éteint ne produit pas d'erreur, seulement zéro fiche neuve."""
    import datetime as dt  # noqa: PLC0415

    aujourd_hui = aujourd_hui or dt.date.today()
    seuil = (aujourd_hui - dt.timedelta(days=FENETRE_MUETTE)).isoformat()
    out = []
    for s in cfg.toutes(graphe=graphe):
        if s.plateforme in SANS_ACTUALITE or (aujourd_hui - declaree_le(s)).days < FENETRE_MUETTE:
            continue
        recente = False
        for f in s.chemin.glob("*.md") if s.chemin.is_dir() else []:
            with f.open(encoding="utf-8", errors="replace") as h:
                m = re.search(r"^collected:\s*[\"']?(\d{4}-\d{2}-\d{2})", h.read(4000), re.M)
            if m and m.group(1) >= seuil:
                recente = True
                break
        if not recente:
            out.append(s)
    return out


def signaler_sources_muettes(j: Journal, g: str, muettes: list) -> None:
    """Une ligne « à faire » pour les sources muettes du graphe dont la cause n'est pas déclarée au registre, la liste
    résumée au-delà de huit."""
    muettes = [s for s in muettes if not getattr(s, "muette", None)]
    if not muettes:
        return
    noms = ", ".join(s.nom for s in muettes[:8]) + (f" et {len(muettes) - 8} autre(s)" if len(muettes) > 8 else "")
    j.signal(
        f"sources muettes ({g}) : {len(muettes)} source(s) déclarée(s) sans fiche collectée depuis "
        f"{FENETRE_MUETTE} jours — {noms} ; cause à chercher (flux, filtre, page, publication)"
    )


def _fiches_de(src) -> list[tuple[str, str]]:
    """(date, collected) de chaque fiche de la source."""
    out = []
    for f in src.chemin.glob("*.md") if src.chemin.is_dir() else []:
        with f.open(encoding="utf-8", errors="replace") as h:
            tete = h.read(4000)
        d = re.search(r"^date:\s*[\"']?(\d{4}-\d{2}-\d{2})", tete, re.M)
        c = re.search(r"^collected:\s*[\"']?(\d{4}-\d{2}-\d{2})", tete, re.M)
        out.append((d.group(1) if d else "", c.group(1) if c else ""))
    return out


def liste_des_muettes(g: str, sortie_collecte: str = "", aujourd_hui=None, declaree_le=_declaree_le) -> list[dict]:
    """La liste publiable des sources muettes (mainteneur, 03.11.2026) : les muettes du passage, et celles dont la cause est
    déclarée au registre (`muette:`) sans fiche collectée depuis. Une ligne : source, adresse publique, date de la
    dernière fiche, cause (registre, sinon code HTTP du refus lu dans la sortie de la collecte, sinon « à établir »)."""
    import datetime as dt  # noqa: PLC0415

    aujourd_hui = aujourd_hui or dt.date.today()
    detectees = {s.cle for s in sources_muettes(g, aujourd_hui=aujourd_hui, declaree_le=declaree_le)}
    refus = {u: c for u, c in re.findall(r"!! (\S+) : HTTP Error (\d{3})", sortie_collecte)}
    lignes = []
    for s in cfg.toutes(graphe=g):
        fiches = _fiches_de(s)
        declaree = getattr(s, "muette", None) or {}
        revenue = declaree and any(c >= declaree["le"] for _, c in fiches)
        p = s.propres
        # Une lettre reçue par courriel sans mot de passe de boîte au trousseau : muette dès sa déclaration (03.11.2026).
        sans_boite = s.plateforme == "Courriel" and not cfg.boite_configuree(str(p.get("boite") or ""))
        if s.cle not in detectees and (not declaree or revenue) and not sans_boite:
            continue
        acces = [p.get(k) for k in ("flux", "liste", "plan") if p.get(k)]
        if sans_boite:
            cause = "boîte non configurée"
        elif declaree and not revenue:
            cause = declaree["cause"] + (f" ({declaree['code']})" if "code" in declaree else "")
        else:
            code = next((refus[u] for u in acces if u in refus), "")
            cause = f"refus d'accès ({code})" if code else "à établir"
        dates = [d for d, _ in fiches if d and d <= aujourd_hui.isoformat()]
        lignes.append(
            {
                "cle": s.cle,
                "source": s.nom,
                "adresse": str(p.get("site") or (acces[0] if acces else "")),
                "derniere_fiche": max(dates, default=""),
                "cause": cause,
            }
        )
    return lignes


def ecrire_liste_des_muettes(g: str, lignes: list[dict], aujourd_hui=None) -> None:
    """graphe/<g>/sources_muettes.md et .json : publiés avec le graphe, sans chemin de la machine."""
    import datetime as dt  # noqa: PLC0415

    jour = (aujourd_hui or dt.date.today()).isoformat()
    ch = cfg.chemins_du_graphe(g)
    ch["muettes_json"].write_text(
        json.dumps({"graphe": g, "date": jour, "sources": lignes}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md = [
        f"# Sources muettes ({g})",
        "",
        f"État du {jour}.",
        "",
        "| Source | Adresse | Dernière fiche | Cause |",
        "|---|---|---|---|",
    ]
    md += [f"| {l['source']} | {l['adresse']} | {l['derniere_fiche']} | {l['cause']} |" for l in lignes]
    ch["muettes_md"].write_text(commun.masquer_chemins("\n".join(md) + "\n"), encoding="utf-8")


def inactive(j: Journal, g: str, etape: str, libelle: str) -> bool:
    """Vrai, et le dit, quand le registre arrête cette étape pour ce graphe (`graphes.<nom>.etapes`)."""
    if cfg.etape_active(g, etape):
        return False
    j.dire(f"  inactive  {libelle} : arrêtée au registre (graphes.{g}.etapes.{etape})")
    return True


SKILL_KG = Path.home() / ".claude" / "skills" / "kg"


def skill_a_synchroniser(graphes, registre: Path | None = None) -> bool:
    """Vrai quand le skill /kg du poste déclare l'un de ces graphes sous cette racine de données. Sinon (un témoin, une
    autre racine), le synchroniseur recompterait des graphes qui ne sont pas les nôtres, dans un autre dépôt (N1)."""
    try:
        d = json.loads((registre or media.registre_des_graphes()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    d = d.get("graphes", d) if isinstance(d, dict) else {}
    return any(
        isinstance(d.get(g), dict) and Path(str(d[g].get("racine") or "")).expanduser().resolve() == RACINE.resolve()
        for g in graphes
    )


def script_du_moteur(args: list[str]) -> list[str]:
    """Un script nommé « newskit.x » se lit sous CODE, pas sous la racine des données où l'étape tourne (N1)."""
    if args and str(args[0]).startswith("scripts/"):
        return [str(CODE / Path(args[0]).name), *args[1:]]
    return list(args)


def lancer(
    j: Journal,
    libelle: str,
    args: list[str],
    bloquant: bool = False,
    garder: int = 3,
    delai: int = 3600,
    modele: bool = False,
) -> tuple[bool, str]:
    """Exécute une étape et résume sa sortie. Retourne (succès, dernières lignes). `modele` : l'étape appelle le
    modèle ; elle est sautée quand la limite d'usage a été constatée moins de trois heures plus tôt (couts.py), au lieu
    d'enchaîner des appels refusés (08.10.2026 : trois graphes, 2 h d'appels pour rien). Une étape dont le script
    manque est sautée : les modules propres à une édition (collecte_arxiv, depots, diachronie) sont facultatifs
    (témoin sans modules spécifiques, 06.11.2026)."""
    cmd = script_du_moteur(args)
    if cmd and cmd[0].endswith(".py") and not Path(cmd[0]).exists():
        j.dire(f"  sauté   {libelle} : module absent ({Path(cmd[0]).name})")
        return False, ""
    if modele:
        marque = couts.marque_en_cours()
        if marque:
            j.dire(f"  sauté   {libelle} : limite d'usage atteinte — {marque}")
            return False, ""
    t0 = time.time()
    try:
        # PYTHONHASHSEED=0 : la détection de communautés de graphify varie
        # d'un processus à l'autre sans cela (128 communautés, puis 141, sur
        # un graphe identique). Fixé ici plutôt que dans l'agent launchd, pour
        # qu'un lancement à la main donne la même carte.
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        r = subprocess.run([PYTHON, *cmd], cwd=RACINE, capture_output=True, text=True, timeout=delai, env=env)
    except subprocess.TimeoutExpired:
        j.alerte(f"{libelle} : dépassement du délai de {delai // 60} min")
        return False, ""
    sortie = (r.stdout or "") + (r.stderr or "")
    utiles = [l.rstrip() for l in sortie.split("\n") if l.strip()]
    resume = "\n".join(f"      {l}" for l in utiles[-garder:])
    duree = time.time() - t0
    if r.returncode != 0:
        (j.alerte if bloquant else j.dire)(
            f"{libelle} : code {r.returncode} ({duree:.0f} s)" + ("" if bloquant else "  — non bloquant")
        )
        if resume:
            j.dire(resume)
        return False, sortie
    j.dire(f"  ok      {libelle}  ({duree:.0f} s)")
    if resume:
        j.dire(resume)
    return True, sortie


def lire_veille(sortie: str) -> tuple[int, int, int]:
    """(notices moissonnées, fiches écrites, notices vues par les thèmes).

    Une récolte pleine filtrée à vide n'est ni un refus ni une absence : le
    19.09.2026 la veille a moissonné 9 471 notices, écrit zéro fiche et rendu
    « ok ». Mais zéro fiche a deux causes, et une seule est un défaut : si les
    thèmes n'ont *vu* aucune notice, le filtre rejette tout ; s'ils en ont vu
    des centaines, elles étaient déjà en fiche — le 27.09.2026, 600 vues et
    0 écrite au lendemain d'une routine, ce qui n'a rien d'anormal.
    """
    m_notices = re.search(r"(\d+) notice\(s\) moissonnées|\((\d+) notices, moins de", sortie)
    m_ecrites = re.search(r"(\d+) fiche\(s\) écrite\(s\)", sortie)
    notices = int(next((g for g in m_notices.groups() if g), 0)) if m_notices else 0
    ecrites = int(m_ecrites.group(1)) if m_ecrites else 0
    vues = sum(int(n) for n in re.findall(r"(\d+) vus,", sortie))
    return notices, ecrites, vues


def ids_du_graphe(graphe: str | None) -> set[str]:
    """Identifiants des nœuds d'un graphe composé, ou l'ensemble vide s'il n'existe pas."""
    return etat_du_graphe(graphe)[0]


def normaliser_libelle(s: str) -> str:
    """Un libellé comparable d'un passage à l'autre : minuscules, sans accents ni ponctuation (B16, 03.11.2026)."""
    import unicodedata  # noqa: PLC0415

    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def etat_du_graphe(graphe: str | None) -> tuple[set[str], set[str]]:
    """(identifiants, libellés normalisés avec les alias `aussi`) des nœuds d'un graphe composé, lus en une fois.

    Les libellés disent si une notion est neuve : une recomposition (table d'alias, fusion des homonymes) renomme des
    identifiants par centaines sans rien ajouter au graphe (2 novembre 2026 : 1 146 identifiants neufs, 474 notions)."""
    if not graphe:
        return set(), set()
    p = cfg.chemins_du_graphe(graphe)["graphe"]
    if not p.exists():
        return set(), set()
    ids, libelles = set(), set()
    for n in json.loads(p.read_text(encoding="utf-8")).get("nodes", []):
        ids.add(n["id"])
        for lab in [n.get("label") or "", *(n.get("aussi") or [])]:
            if normaliser_libelle(lab):
                libelles.add(normaliser_libelle(lab))
    return ids, libelles


def notions_neuves(
    g: dict, concepts: list[dict], libelles_avant: set[str] | None, versees: set[str] | None
) -> list[dict]:
    """Les concepts neufs qui sont des notions neuves (note B16, section 3), chacun complété de `fiches` (ses fiches
    versées, triées) et de `aussi` :
    - son libellé normalisé et ses alias étaient absents du graphe d'avant ;
    - au moins une fiche versée à ce passage l'atteste (le `source_file` d'une de ses arêtes, ou du nœud) ;
    - la moitié au moins des fiches qui l'attestent sont versées à ce passage.
    `libelles_avant` ou `versees` à None : la condition n'est pas vérifiée (premier passage, appel ancien)."""
    noeuds = {n["id"]: n for n in g.get("nodes", [])}
    ids = {x["id"] for x in concepts}
    attestee: dict[str, set[str]] = {i: set() for i in ids}
    for l in g.get("links", []):
        f = str(l.get("source_file") or "").replace(str(RACINE) + "/", "")
        if not f:
            continue
        for e in (l.get("source"), l.get("target")):
            if e in attestee:
                attestee[e].add(f)
    out = []
    for x in concepts:
        n = noeuds.get(x["id"], {})
        aussi = [str(a) for a in (n.get("aussi") or [])]
        fiches = set(attestee.get(x["id"], set()))
        if n.get("source_file"):
            fiches.add(str(n["source_file"]).replace(str(RACINE) + "/", ""))
        if libelles_avant is not None and any(
            normaliser_libelle(l_) in libelles_avant for l_ in [x.get("label") or "", *aussi] if normaliser_libelle(l_)
        ):
            continue
        neuves = sorted(fiches & versees) if versees is not None else sorted(fiches)
        if versees is not None and (not neuves or 2 * len(neuves) < len(fiches)):
            continue
        out.append({**x, "fiches": neuves, "aussi": aussi})
    return out


def themes_des_noeuds(graphe: str, G) -> dict[str, str]:
    """{id de nœud: thème} — le titre du thème de la pyramide quand sa communauté en a un, sinon l'étiquette de la
    communauté (son nœud le plus central), sur les 40 premières communautés du graphe composé."""
    from newskit import digerer_graphe as dg  # noqa: PLC0415

    p_t = cfg.chemins_du_graphe(graphe)["base"] / "synthese" / "themes.json"
    titres = {}
    if p_t.exists():
        try:
            titres = {
                k: v.get("titre") or k for k, v in json.loads(p_t.read_text(encoding="utf-8")).get("themes", {}).items()
            }
        except (json.JSONDecodeError, AttributeError):
            titres = {}
    out: dict[str, str] = {}
    try:
        for etiquette, membres, _ in dg.communautes(G, 40):
            for n in membres:
                out.setdefault(n, titres.get(etiquette) or etiquette)
    except Exception as e:  # noqa: BLE001 — sans communautés, les notions vont « hors thème »
        print(f"  (thèmes des nouveautés non calculés : {type(e).__name__} {str(e)[:80]})", file=sys.stderr)
        return out
    # Une notion hors des quarante communautés hérite du thème le plus fréquent chez ses voisines (deux pas au plus).
    from collections import Counter  # noqa: PLC0415

    for _ in range(2):
        herites = {}
        for n in G.nodes:
            if n in out or not str(n).startswith("concept_"):
                continue
            c = Counter(out[v] for v in G.neighbors(n) if v in out)
            if c:
                herites[n] = c.most_common(1)[0][0]
        if not herites:
            break
        out.update(herites)
    return out


def ranger_par_rubrique(notions: list[dict], rubs: list[dict]) -> None:
    """La rubrique de chaque notion neuve : celle de la majorité de ses fiches versées (lettre.rubrique_majoritaire, B16),
    à la place de la communauté de la carte, qui rangeait des cyberattaques sous « Claude Code »."""
    if not rubs:
        return
    from newskit import lettre  # noqa: PLC0415

    cache: dict[str, dict] = {}
    for x in notions:
        manquantes = [f for f in x.get("fiches") or [] if f not in cache]
        cache.update(lettre.charger_fiches(manquantes))
        x["rubrique"] = lettre.rubrique_majoritaire([cache[f] for f in x.get("fiches") or [] if f in cache], rubs) or ""


def entree_nouveaute(n: dict, deg: dict[str, int]) -> dict:
    """L'entrée d'un nœud neuf dans nouveautes.json ; avec son libellé français quand la composition l'a posé
    (francais.py, 03.11.2026), que la lettre affiche."""
    e = {
        "id": n["id"],
        "label": str(n.get("label", n["id"]))[:120],
        "kind": n.get("kind") or n.get("file_type") or "",
        "degre": deg.get(n["id"], 0),
        "source": str(n.get("source_file") or "").replace(str(RACINE) + "/", ""),
    }
    if n.get("label_fr"):
        e["label_fr"] = str(n["label_fr"])[:120]
    return e


def nouveautes_du_graphe(
    j: Journal,
    graphe: str,
    ids_avant: set[str],
    libelles_avant: set[str] | None = None,
    versees: set[str] | None = None,
) -> dict | None:
    """Nœuds du graphe absents du passage précédent, écrits dans graphe/<nom>/nouveautes.json.

    C'est la liste que le rapport hebdomadaire de dollar_fountain reprend :
    fiches et concepts entrés depuis le dernier passage, avec source et degré.
    Rend {"notions": [{label, degre, theme}…], "sujets": [sujets entrés ce jour]} pour le haut du rapport et du
    courriel (porteur, 09.10.2026 : les nouvelles notions acceptées au graphe, avec leurs thèmes, en tête).
    Les notions rendues sont les notions neuves (`notions_neuves`) : un identifiant renommé ou une notion qu'aucune
    fiche versée n'atteste n'en est pas une ; `identifiants` garde le compte des concepts aux identifiants neufs (B16).
    """
    p = cfg.chemins_du_graphe(graphe)["graphe"]
    if not p.exists():
        return None
    g = json.loads(p.read_text(encoding="utf-8"))
    deg: dict[str, int] = {}
    for l in g.get("links", []):
        deg[l["source"]] = deg.get(l["source"], 0) + 1
        deg[l["target"]] = deg.get(l["target"], 0) + 1
    horo = time.strftime("%Y-%m-%d %H:%M")
    neufs = sorted(
        (entree_nouveaute(n, deg) for n in g.get("nodes", []) if ids_avant and n["id"] not in ids_avant),
        key=lambda x: (-x["degre"], x["label"]),
    )
    concepts = [x for x in neufs if x["kind"] in ("concept", "rationale")]
    neuves = notions_neuves(g, concepts, libelles_avant, versees)
    par_id = {x["id"]: x for x in neuves}
    rationales = {n["id"]: str(n.get("rationale") or "")[:300] for n in g.get("nodes", []) if n["id"] in par_id}
    for x in concepts:
        x["neuve"] = x["id"] in par_id
        if x["neuve"]:
            x.update(
                fiches=par_id[x["id"]]["fiches"], aussi=par_id[x["id"]]["aussi"], rationale=rationales.get(x["id"], "")
            )
    del g
    if concepts:
        pass
        from newskit import themes_emergents as te  # noqa: PLC0415

        G = te.charger(graphe)
        themes = themes_des_noeuds(graphe, G) if G is not None else {}
        for x in concepts:
            x["theme"] = themes.get(x["id"], "")
    sortie = cfg.chemins_du_graphe(graphe)["nouveautes"]
    hist = json.loads(sortie.read_text(encoding="utf-8")) if sortie.exists() else {"passages": []}
    notions = [x for x in concepts if x["neuve"]]
    try:
        ranger_par_rubrique(notions, cfg.rubriques(graphe))
    except Exception as e:  # noqa: BLE001 — sans rubriques, les notions gardent leur thème
        print(f"  (rubriques des nouveautés non calculées : {type(e).__name__} {str(e)[:80]})", file=sys.stderr)
    hist["passages"] = (
        [
            {
                "date": horo,
                "identifiants_neufs": len(concepts),
                "notions_neuves": len(notions),
                "fiches_versees": sorted(versees or ()),
                "nouveaux": neufs,
            }
        ]
        + hist["passages"]
    )[:60]
    sortie.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    if not ids_avant:
        j.dire("  ok      nouveautés : premier passage, état de référence pris")
        return None
    j.dire(
        f"  ok      nouveautés : {len(neufs)} nœud(s) nouveau(x), dont {len(concepts)} concept(s) "
        f"et {len(notions)} notion(s) neuve(s) (libellé inconnu, fiche versée)"
    )
    for x in notions[:12]:
        j.dire(f"            {x['degre']:3d}  {x['label'][:80]}" + (f"  — {x['theme'][:50]}" if x.get("theme") else ""))
    # Les sujets entrés ce jour (thèmes émergents acceptés, à la main ou d'eux-mêmes).
    p_th = cfg.chemins_du_graphe(graphe)["base"] / "themes.json"
    sujets = []
    if p_th.exists():
        try:
            nouveaux = json.loads(p_th.read_text(encoding="utf-8")).get("nouveaux", {})
            sujets = [
                {"libelle": lib, "notions": v.get("notions", [])}
                for lib, v in nouveaux.items()
                if v.get("date") == time.strftime("%Y-%m-%d")
            ]
        except json.JSONDecodeError:
            pass
    return {"notions": notions, "sujets": sujets, "identifiants": len(concepts)}


def empreintes_denses(graphe: str | None = None) -> dict[str, str]:
    """Empreinte du corps de chaque fiche dense, pour savoir si la couche 2 vieillit."""
    from newskit import fiche  # noqa: PLC0415

    out: dict[str, str] = {}
    for d in cfg.dossiers_denses(graphe) if graphe else DOSSIERS_DENSES:
        for f in sorted(
            (RACINE / d).rglob("*.md")
        ):  # rglob : les textes de référence sont en sous-dossiers (09.10.2026)
            if f.name.startswith("_"):
                continue
            _, corps = fiche.lire_fichier(f)
            rel = str(f.relative_to(RACINE))
            out[rel] = hashlib.sha256(corps.encode("utf-8")).hexdigest()[:16]
    # Trié par chemin, et non dans l'ordre du registre : réordonner une source
    # au registre ne doit pas produire un diff de 106 lignes sans un seul
    # changement d'empreinte, comme observé la première fois.
    return {k: out[k] for k in sorted(out)}


def retard_couche2(g: str) -> tuple[list[str], list[str]]:
    """Les fiches denses du graphe dont l'empreinte diffère de celle tenue par la couche 2, et parmi elles les nouvelles.
    Un graphe à partage (GV4) ne compte que ses fiches retenues : les autres ne sont jamais extraites pour lui."""
    from newskit import partage_graphe  # noqa: PLC0415

    empreintes_apres = empreintes_denses(None if g == cfg.GRAPHE_PRINCIPAL else g)
    retenues = partage_graphe.fiches_du_graphe(g)
    if retenues is not None:
        empreintes_apres = {f: h for f, h in empreintes_apres.items() if f in retenues}
    p_empreintes = EMPREINTES if g == cfg.GRAPHE_PRINCIPAL else cfg.chemins_du_graphe(g)["couche2"] / "empreintes.json"
    tenues = json.loads(p_empreintes.read_text(encoding="utf-8")) if p_empreintes.exists() else {}
    en_retard = [f for f, h in empreintes_apres.items() if tenues.get(f) != h]
    return en_retard, [f for f in en_retard if f not in tenues]


PLAFOND_PAR_GROUPE = 12


def provenance(champs: dict) -> str:
    """Le groupe d'une fiche dans le rapport : sa source, et pour la veille arXiv son thème.

    Le champ `source` porte déjà la provenance telle que le collecteur l'a
    écrite ; on la lit au lieu de la deviner à partir du chemin.
    """
    src = champs.get("source") or ""
    m = re.search(r"thème « (.+?) »", src)
    if m:
        return f"arXiv — veille « {m.group(1)} »"
    if src.startswith("arXiv (cité"):
        return "arXiv — papiers cités par le corpus"
    if src.startswith("arXiv"):
        return "arXiv"
    if src.startswith("flux RSS de "):
        return src.removeprefix("flux RSS de ")
    pub = champs.get("publication") or ""
    plat = champs.get("plateforme") or ""
    return f"{plat} — {pub}" if pub and plat else (pub or plat or "autre")


def fiches_versees(dossiers: list[str] | None = None, depuis: str | None = None) -> set[str]:
    """Les chemins (relatifs) des fiches ajoutées depuis le dernier commit, bornés aux dossiers du graphe : la même
    définition que `fiches_nouvelles`, celle du commit que la routine va faire (B16). Avec `depuis` (le passage
    précédent du graphe), aussi les fiches ajoutées par un commit depuis : la routine d'un autre graphe commite tout
    par `git add -A` (05.11.2026 : la routine d'un graphe a commité 188 fiches, dont celles d'un autre graphe de la nuit)."""
    r = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "sources", "recherche"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=False,
    )
    lignes = [(l[:2].strip(), l[3:].strip()) for l in r.stdout.splitlines()]
    if depuis:
        r = subprocess.run(
            [
                "git",
                "log",
                f"--since={depuis}",
                "--diff-filter=A",
                "--name-only",
                "--format=",
                "--",
                "sources",
                "recherche",
            ],
            cwd=RACINE,
            capture_output=True,
            text=True,
            check=False,
        )
        lignes += [("A", l.strip()) for l in r.stdout.splitlines() if l.strip()]
    out = set()
    for code, chemin in lignes:
        if code not in ("??", "A") or not chemin.endswith(".md") or Path(chemin).name.startswith("_"):
            continue
        if dossiers is not None and not any(chemin.startswith(d.rstrip("/") + "/") for d in dossiers):
            continue
        if (RACINE / chemin).exists():
            out.add(chemin)
    return out


def chemins_modifies(sortie_z: str) -> list[str]:
    """Les chemins de `git status --porcelain -z` : modifiés, nouveaux, effacés, et les deux côtés d'un renommage."""
    out, morceaux, i = [], sortie_z.split("\0"), 0
    while i < len(morceaux):
        e = morceaux[i]
        i += 1
        if len(e) < 4:
            continue
        out.append(e[3:])
        if e[0] in "RC" and i < len(morceaux) and morceaux[i]:
            out.append(morceaux[i])
            i += 1
    return out


def fichiers_du_commit(graphe: str, chemins: list[str]) -> list[str]:
    """Les chemins que la routine de `graphe` commite : ceux de ses dossiers, de graphe/<g>/ et lettres/<g>/ (le graphe
    et ses inclus), et les fichiers communs ; jamais ce qui n'appartient qu'à un autre graphe (mainteneur, 05.11.2026 :
    « chaque routine commite seulement les fichiers de son graphe » ; la routine d'un graphe commitait les fiches
    d'un autre graphe par `git add -A`)."""

    def zone(g: str) -> list[str]:
        return [d.rstrip("/") + "/" for d in cfg.dossiers_du_graphe(g)] + [f"graphe/{g}/", f"lettres/{g}/"]

    miens = {graphe, *cfg.graphes_inclus(graphe)}
    a_moi = [z for g in miens for z in zone(g)]
    aux_autres = [z for g in cfg.graphes() if g not in miens for z in zone(g)]
    return [c for c in chemins if any(c.startswith(z) for z in a_moi) or not any(c.startswith(z) for z in aux_autres)]


def versees_du_graphe(graphe: str) -> set[str]:
    """Les fiches versées du passage : ajoutées depuis le passage précédent du graphe, commitées ou non, sans celles
    qu'un passage réussi a déjà versées. Le commit d'un passage suit la date écrite dans nouveautes.json : sans ce
    retrait, « depuis » reprenait toutes ses fiches (05.11.2026). Un passage arrêté n'a rien versé au graphe : ses
    fiches restent à verser."""
    p = cfg.chemins_du_graphe(graphe)["nouveautes"]
    try:
        passages = json.loads(p.read_text(encoding="utf-8")).get("passages") or []
    except (OSError, ValueError):
        passages = []
    deja = {f for x in passages if not x.get("echec") for f in x.get("fiches_versees") or []}
    return fiches_versees(cfg.dossiers_du_graphe(graphe), depuis=debut_du_passage(graphe)) - deja


def debut_du_passage(graphe: str) -> str | None:
    """La date du passage précédent du graphe (nouveautes.json, arrêté ou non) ; None sans passage."""
    p = cfg.chemins_du_graphe(graphe)["nouveautes"]
    try:
        passages = json.loads(p.read_text(encoding="utf-8")).get("passages") or []
    except (OSError, ValueError):
        return None
    return str(passages[0].get("date") or "") or None if passages else None


def passage_arrete(j: Journal, graphe: str) -> None:
    """Un passage arrêté avant son commit écrit quand même ses fiches versées dans nouveautes.json, sans nœuds (le
    graphe est en retard) : la lettre du jour part avec les nouvelles du jour, pas avec celles du passage de la veille
    (05.11.2026, décision du mainteneur). Le passage suivant reverse ces fiches ; la lettre écarte celles déjà envoyées."""
    sortie = cfg.chemins_du_graphe(graphe)["nouveautes"]
    hist = json.loads(sortie.read_text(encoding="utf-8")) if sortie.exists() else {"passages": []}
    versees = sorted(versees_du_graphe(graphe))
    hist["passages"] = (
        [
            {
                "date": time.strftime("%Y-%m-%d %H:%M"),
                "echec": True,
                "identifiants_neufs": 0,
                "notions_neuves": 0,
                "fiches_versees": versees,
                "nouveaux": [],
            }
        ]
        + hist["passages"]
    )[:60]
    sortie.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    j.dire(f"  ok      nouveautés : passage arrêté, {len(versees)} fiche(s) du jour pour la lettre")


def compte_des_versees(nouvelles: dict[str, list[dict]]) -> str:
    """La colonne « nouvelles fiches » : les fiches versées depuis le dernier commit, pas l'écart compté depuis le début
    du passage — un passage qui reprend un passage arrêté avant son commit disait « +0 » pour 87 fiches (02.11.2026)."""
    return f"+{sum(len(v) for v in nouvelles.values())}"


def fiches_nouvelles(dossiers: list[str] | None = None) -> tuple[dict[str, list[dict]], int]:
    """Les fiches ajoutées depuis le dernier commit, groupées par provenance, et le
    nombre de fiches modifiées (aperçus complétés, dates réparées).

    Lu dans git : c'est la seule définition honnête de « nouveau cette
    semaine » — ce que le commit de la routine va enregistrer, ni plus ni moins.

    `dossiers` borne la liste aux fiches du graphe traité. Sans cela, une routine
    qui suit l'échec d'une autre annonce les fiches restées en plan : le 05.10.2026,
    les thèmes de veille d'un graphe d'ingénierie sont apparus dans le courriel d'un graphe audio.
    """
    from newskit import fiche  # noqa: PLC0415

    r = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "sources", "recherche"],
        cwd=RACINE,
        capture_output=True,
        text=True,
    )
    groupes: dict[str, list[dict]] = {}
    modifiees = 0
    for l in r.stdout.splitlines():
        code, chemin = l[:2], l[3:].strip()
        if not chemin.endswith(".md") or Path(chemin).name.startswith("_"):
            continue
        if dossiers is not None and not any(chemin.startswith(d.rstrip("/") + "/") for d in dossiers):
            continue
        if code.strip() in ("M", "MM", "AM"):
            modifiees += 1
            continue
        if code.strip() not in ("??", "A"):
            continue
        try:
            champs, _ = fiche.lire_fichier(RACINE / chemin)
        except Exception:
            champs = {"title": Path(chemin).stem}
        groupes.setdefault(provenance(champs), []).append(
            {
                "date": str(champs.get("date") or "")[:10],
                "titre": str(champs.get("title") or Path(chemin).stem),
                "url": str(champs.get("url") or ""),
            }
        )
    for g in groupes.values():
        g.sort(key=lambda f: f["date"], reverse=True)
    ordre = sorted(groupes, key=lambda k: (k.startswith("arXiv"), k.lower()))
    return {k: groupes[k] for k in ordre}, modifiees


def controle_fraicheur(j: Journal, graphe: str = cfg.GRAPHE_PRINCIPAL) -> bool:
    """Le nombre de fiches d'un graphe doit égaler le nombre de ses nœuds de fiche.

    Pour le principal, un nœud de fiche est un article ou un papier : ses
    papiers cités sont tous collectés. Un autre graphe ne collecte pas ses
    citations arXiv — elles restent des nœuds `papier` sans fiche, non lus —
    donc ses articles se comparent aux fiches, plus les papiers de sa propre
    veille arXiv s'il en a une : ceux-là portent un `arxiv_id`, une citation
    non lue n'en porte pas.
    """
    sq = cfg.chemins_du_graphe(graphe)["squelette"]
    if not sq.exists():
        j.alerte(f"contrôle de fraîcheur ({graphe}) : squelette absent")
        return False
    d = json.loads(sq.read_text(encoding="utf-8"))
    if graphe == cfg.GRAPHE_PRINCIPAL:
        noeuds = sum(1 for n in d["nodes"] if n.get("kind") in ("article", "papier"))
    else:
        noeuds = sum(
            1 for n in d["nodes"] if n.get("kind") == "article" or (n.get("kind") == "papier" and n.get("arxiv_id"))
        )
    # Un graphe à partage (GV4) ne garde que ses fiches retenues : la couche 1 n'en fait pas d'autres nœuds.
    from newskit import partage_graphe  # noqa: PLC0415

    retenues = partage_graphe.fiches_du_graphe(graphe)
    fiches = (
        len(retenues)
        if retenues is not None
        else sum(
            1
            for dossier in cfg.dossiers_du_graphe(graphe)
            for f in (RACINE / dossier).rglob("*.md")
            if not f.name.startswith("_")
        )
    )
    etiquette = "" if graphe == cfg.GRAPHE_PRINCIPAL else f" ({graphe})"
    if fiches != noeuds:
        # Deux fiches d'un même document font un seul nœud : l'écart les nomme, avec la commande qui les fond
        # (19.10.2026, 2302.12173 : quatre nuits bloquées sans cause dite ; CHANTIER_GENERIQUE.md § 4.2).
        from newskit import doublons_d_identite as dd  # noqa: PLC0415

        groupes = dd.etat([graphe])
        cause = ""
        if groupes:
            noms = " ; ".join(" = ".join([g.garde or g.fiches[0], *(g.retirees or g.fiches[1:])]) for g in groupes[:3])
            cause = (
                f" ; {len(groupes)} document(s) en plusieurs fiches : {noms}{' …' if len(groupes) > 3 else ''}"
                f" (python3 -m newskit.doublons_d_identite --fondre)"
            )
        j.alerte(
            f"contrôle de fraîcheur{etiquette} : {fiches} fiches pour {noeuds} nœuds, écart {fiches - noeuds}{cause}"
        )
        return False
    j.dire(f"  ok      contrôle de fraîcheur{etiquette} : {fiches} fiches, autant de nœuds")
    return True


def pousser() -> None:
    """Envoie le commit vers le dépôt distant, s'il y en a un.

    Une sauvegarde qui dépend d'un geste manuel hebdomadaire n'est pas une
    sauvegarde. L'échec n'est pas bloquant — la machine peut être hors ligne, et
    le commit local est déjà fait — mais il est dit : un envoi qui échoue en
    silence laisse croire à une copie distante qui n'existe pas.
    """
    distants = subprocess.run(["git", "remote"], cwd=RACINE, capture_output=True, text=True).stdout.split()
    if not distants:
        print("Pas de dépôt distant, rien à envoyer.")
        return
    r = subprocess.run(["git", "push", "--quiet"], cwd=RACINE, capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        print(f"Poussé vers {distants[0]}.")
        publier_en_ligne()
    else:
        print(f"ALERTE : envoi vers {distants[0]} impossible : {(r.stderr or r.stdout or '').strip()[:200]}")


def publier_en_ligne() -> None:
    """Dès que le passage est poussé, le serveur de l'édition (`edition.site`) tire le dépôt et se redémarre : la page
    d'accueil (volumes, bancs, profondeur par sujet) montre les résultats de la nuit sans attendre l'heure de sa mise à
    jour automatique (porteur, 09.10.2026 : « dès que de nouveaux résultats arrivent »). Même accès que depots.py
    (~/.secrets/vps_kg.env) ; jamais bloquant : sans accès, le serveur se met à jour seul à l'heure."""
    acces_p = Path.home() / ".secrets" / "vps_kg.env"
    if not acces_p.exists():
        print("Serveur en ligne : accès non configuré, mise à jour à l'heure.")
        return
    try:  # l'index et les modèles exportés (hors dépôt) partent avant le pull
        from newskit import publier_index  # noqa: PLC0415

        publier_index.publier(COURRIEL_GRAPHE[1:] or None)
    except Exception as e:  # noqa: BLE001
        print(f"Index en ligne : non envoyé ({type(e).__name__} {str(e)[:80]}).")
    try:  # les graphes hors du dépôt (24.10.2026 ; rien tant que git les suit)
        from newskit import publier_graphes  # noqa: PLC0415

        publier_graphes.publier(COURRIEL_GRAPHE[1:] or None)
    except Exception as e:  # noqa: BLE001
        print(f"Graphes en ligne : non envoyés ({type(e).__name__} {str(e)[:80]}).")
    # Les cerveaux CortexDB (hors dépôt) du passage : le serveur les met en place sous ses gardiens (kg-cortexdb@<g>) ;
    # seul un cerveau dont le bilan a changé devient prêt (publier_cerveau, 23.10.2026).
    try:
        from newskit import publier_cerveau  # noqa: PLC0415

        graphes = COURRIEL_GRAPHE[1:] or None  # un passage complet : tous les graphes engagés
        if graphes is None or any(cfg.verse_cortexdb(g) for g in graphes):
            publier_cerveau.publier(graphes)
    except Exception as e:  # noqa: BLE001
        print(f"Cerveaux en ligne : non envoyés ({type(e).__name__} {str(e)[:80]}).")
    commande = cfg.commande_maj_serveur()
    if commande is None:
        print("Serveur en ligne : aucun service de mise à jour déclaré (media.service_maj), il se mettra à jour seul.")
        return
    try:
        acces = {
            k.strip(): v.strip().strip("\"'")
            for k, v in (
                l.split("=", 1)
                for l in acces_p.read_text(encoding="utf-8").splitlines()
                if "=" in l and not l.lstrip().startswith("#")
            )
        }
        subprocess.run(
            [
                "ssh",
                "-i",
                str(Path(acces["VPS_CLE"]).expanduser()),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
                f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}",
                commande,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        print("Serveur en ligne : mis à jour, la page montre ce passage.")
    except Exception as e:  # noqa: BLE001
        print(
            f"Serveur en ligne : mise à jour non déclenchée ({type(e).__name__} {str(e)[:80]}), il la fera à l'heure."
        )


COURRIEL_GRAPHE: list[str] = []  # ["--graphe", nom] en passage limité à un graphe


SANS_COURRIEL = False  # --sans-courriel : le passage court qui suit un lien déposé a déjà le sien


def fil_avant_la_lettre() -> None:
    """Avant la lettre du jour, le fil du graphe lit les fiches candidates qui restent, hors plafond du jour (mainteneur,
    05.11.2026) : la lettre prend ses titres, ses rubriques et ses redites au registre des nouvelles (newskit.fil
    --avant-la-lettre). Un graphe sans fil n'est pas concerné. Jamais bloquant."""
    from newskit import nouvelles as nv  # noqa: PLC0415

    g = COURRIEL_GRAPHE[1] if COURRIEL_GRAPHE else cfg.GRAPHE_PRINCIPAL
    if not nv.chemin_du_registre(g).exists():
        return
    try:
        r = subprocess.run(
            [PYTHON, str(CODE / "fil.py"), "--graphe", g, "--avant-la-lettre"],
            cwd=RACINE,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        print("Fil avant la lettre :", ((r.stdout or "").strip().splitlines() or [f"code {r.returncode}"])[-1])
    except subprocess.TimeoutExpired:
        print("Fil avant la lettre : délai de 1800 s dépassé.")


# Un appel « angle » par billet, l'un après l'autre (~12 s chacun) : près de 10 min le 6.11.2026, plus de 600 s le
# 7.11.2026, et la lettre n'était pas partie (B16 : la lettre fait des appels au modèle).
DELAI_COURRIEL = 1800


def courriel(commiter: bool = True) -> None:
    """Résumé du rapport par courriel, si le registre le déclare. Jamais bloquant. La lettre envoyée, gardée sous
    lettres/ après le commit du passage, est commitée et poussée à part (commiter_lettres), sauf en --sans-commit."""
    if SANS_COURRIEL:
        return
    fil_avant_la_lettre()
    try:
        r = subprocess.run(
            [PYTHON, str(CODE / "notifier_courriel.py"), *COURRIEL_GRAPHE, "--envoyer"],
            cwd=RACINE,
            capture_output=True,
            text=True,
            timeout=DELAI_COURRIEL,
        )
        sortie = ((r.stdout or "") + (r.stderr or "")).strip()
        print(sortie if sortie else ("Courriel envoyé." if r.returncode == 0 else "Courriel : échec sans message."))
    except subprocess.TimeoutExpired:  # 05.11.2026 : la routine finissait sur une trace ; la lettre a pu partir
        print(f"Courriel : délai de {DELAI_COURRIEL} s dépassé.")
    if commiter:
        try:
            commiter_lettres(RACINE)
        except Exception as e:  # noqa: BLE001 — la lettre est partie ; sa page attendra le passage suivant
            print(f"Lettre gardée non commitée ({type(e).__name__} {str(e)[:80]}).")


def commiter_lettres(racine: Path) -> bool:
    """Commite les seules lettres gardées sous lettres/ (jamais `git add -A`) et pousse : la passe horaire du VPS en
    fait les pages /lettres/<graphe>/<envoi> (deploiement/lettres.py, 03.11.2026). Rend True si un commit a eu lieu."""
    neuves = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "lettres"],
        cwd=racine,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not neuves:
        return False
    subprocess.run(["git", "add", "--", "lettres"], cwd=racine, check=True)
    noms = [l[3:] for l in neuves.splitlines()]
    r = subprocess.run(
        ["git", "commit", "-q", "-m", f"lettre envoyée : {', '.join(noms)[:180]}", "--", "lettres"],
        cwd=racine,
        capture_output=True,
        text=True,
    )  # identité et signature : git config du dépôt
    if r.returncode != 0:
        print(f"Lettre gardée non commitée : {(r.stderr or '').strip()[:200]}")
        return False
    print(f"Lettre gardée commitée ({len(noms)} fichier(s)).")
    pousser()
    return True


def notifier(titre: str, texte: str) -> None:
    """Notification macOS, pour que l'étage assisté ne soit pas oublié."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{texte}" with title "{titre}"'], capture_output=True, timeout=10
        )
    except Exception:
        pass


PLAFOND_NOTIONS_RAPPORT = 40  # nouvelles notions nommées en tête du rapport, au plus


def noter_passage_inclus(graphe: str, v: dict, par: str, date: str | None = None) -> None:
    """Une ligne d'historique dans le rapport d'un graphe inclus (graphe/<nom>/ROUTINE.md), écrite par la routine du
    graphe qui l'inclut et le recompose : un graphe financier recompose un graphe de trading quantitatif chaque matin et n'écrivait que son propre rapport,
    un graphe de trading quantitatif restait affiché « dernier passage 06.10 » sur la page (porteur, 15.10.2026). Le rapport d'un graphe financier n'est
    pas cloné sur le serveur : la date doit vivre dans le dossier du graphe servi. L'en-tête est créé s'il manque."""
    date = date or time.strftime("%Y-%m-%d %H:%M")
    rapport = cfg.chemins_du_graphe(graphe)["rapport"]
    ligne = f"| {date} | ok (routine de {par}) | {v.get('fiches', '—')} | {v.get('noeuds', '—')} | {v.get('aretes', '—')} | — | 0 |"
    texte = rapport.read_text(encoding="utf-8") if rapport.exists() else ""
    entete = "| date | état | fiches | nœuds | arêtes | nouvelles fiches | à faire |\n|---|---|---|---|---|---|---|\n"
    if entete not in texte:
        texte = (
            "# Routine hebdomadaire\n\nProduit par `newskit.routine_hebdo`. L'étage automatique n'appelle aucun modèle.\n"
            "Une étape bloquante qui échoue arrête la routine avant le commit.\n\n## Historique\n\n" + entete + texte
        )
    debut = texte.index(entete) + len(entete)
    histo = [l for l in texte[debut:].split("\n") if l.startswith("| 20")]
    if histo and histo[0].startswith(f"| {date} |"):
        return
    fin = texte[debut:]
    reste = fin.split("\n", len(histo))[-1] if histo else fin
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_text(texte[:debut] + "\n".join([ligne, *histo][:25]) + "\n" + reste, encoding="utf-8")


def actualite_du_passage(graphe: str, versees: set[str], jour: str | None = None) -> tuple[list[dict], int, int]:
    """Les fiches versées du passage comptées par rubrique dans la fenêtre de fraîcheur (lettre.actualite_des_rubriques)."""
    from newskit import lettre  # noqa: PLC0415

    jour = jour or time.strftime("%Y-%m-%d")
    jours, minimum = cfg.fraicheur_lettre(graphe), cfg.fraicheur_min_rubrique(graphe)
    rubs = cfg.rubriques(graphe)
    if not rubs:
        return [], jours, minimum
    return (
        lettre.actualite_des_rubriques(lettre.charger_fiches(sorted(versees)), rubs, jour, jours, minimum),
        jours,
        minimum,
    )


def rapport_du_passage(
    j: Journal,
    etat: str,
    chiffres: dict[str, str],
    nouvelles: dict[str, list[dict]] | None,
    modifiees: int,
    autres: dict[str, dict[str, int]] | None,
    nouveautes: dict | None,
    graphe: str,
    versees: set[str],
) -> None:
    """La fin du passage : l'actualité par rubrique des fiches versées, puis le rapport (ecrire_rapport)."""
    try:
        actualite = actualite_du_passage(graphe, versees)
    except Exception as e:  # noqa: BLE001 — le rapport s'écrit sans cette section
        j.dire(f"  (actualité par rubrique non calculée : {type(e).__name__} {str(e)[:80]})")
        actualite = None
    ecrire_rapport(j, etat, chiffres, nouvelles, modifiees, autres, nouveautes, actualite)


def ecrire_rapport(
    j: Journal,
    etat: str,
    chiffres: dict[str, str],
    nouvelles: dict[str, list[dict]] | None = None,
    modifiees: int = 0,
    autres: dict[str, dict[str, int]] | None = None,
    nouveautes: dict | None = None,
    actualite: tuple[list[dict], int, int] | None = None,
) -> None:
    """Rapport lisible en trente secondes, avec l'historique des exécutions.

    La section « Nouvelles fiches » est ce que le courriel de fin de passage
    détaille : une ligne par fiche, groupée par provenance, plafonnée. Pas le
    déroulé, qui reste en bas pour qui veut le lire.
    """
    date = time.strftime("%Y-%m-%d %H:%M")
    ligne = (
        f"| {date} | {etat} | "
        + " | ".join(chiffres.get(k, "—") for k in ("fiches", "noeuds", "aretes", "neuf"))
        + f" | {len(j.attention)} |"
    )

    ancien = RAPPORT.read_text(encoding="utf-8") if RAPPORT.exists() else ""
    histo = [l for l in ancien.split("\n") if l.startswith("| 20")][:25]

    corps = [
        "# Routine hebdomadaire",
        "",
        "Produit par `newskit.routine_hebdo`. L'étage automatique n'appelle aucun modèle.",
        "Une étape bloquante qui échoue arrête la routine avant le commit.",
        "",
        "## Historique",
        "",
        "| date | état | fiches | nœuds | arêtes | nouvelles fiches | à faire |",
        "|---|---|---|---|---|---|---|",
        ligne,
        *histo,
        "",
        f"## Dernière exécution — {date}",
        "",
    ]
    # En tête (porteur, 09.10.2026) : les notions entrées au graphe à ce passage, groupées par thème, et les sujets
    # entrés. Une ligne « - » par thème : c'est ce que le courriel reprend tel quel.
    if nouveautes is not None:
        notions = nouveautes.get("notions") or []
        corps += [f"### Nouvelles notions ({len(notions)})", ""]
        if nouveautes.get("identifiants") is not None:
            corps += [
                (
                    f"Notions neuves : libellé absent du graphe d'avant et attesté par une fiche versée à ce passage ; "
                    f"{nouveautes['identifiants']} identifiant(s) neuf(s) en tout, renommages compris."
                ),
                "",
            ]
        for s_ in nouveautes.get("sujets") or []:
            corps.append(
                f"- Sujet entré au graphe : « {s_['libelle']} »"
                + (f" — {', '.join(s_['notions'][:5])}" if s_.get("notions") else "")
            )
        if not notions:
            corps.append("- Aucune notion nouvelle à ce passage.")
        par_theme: dict[str, list[dict]] = {}
        for x in sorted(notions, key=lambda x: -x["degre"])[:PLAFOND_NOTIONS_RAPPORT]:
            th = (x["rubrique"] or "Autres") if "rubrique" in x else (x.get("theme") or "hors thème")
            par_theme.setdefault(th, []).append(x)
        for th, xs in sorted(
            par_theme.items(), key=lambda kv: (kv[0] in ("hors thème", "Autres"), -sum(x["degre"] for x in kv[1]))
        ):
            corps.append(
                f"- {th} : " + ", ".join(f"{x['label']} ({x['degre']})" for x in xs)
            )  # entiers : une coupe tombait en plein mot (03.11.2026)
        if len(notions) > PLAFOND_NOTIONS_RAPPORT:
            corps.append(
                f"- … et {len(notions) - PLAFOND_NOTIONS_RAPPORT} notion(s) de moindre degré (graphe/<nom>/nouveautes.json)"
            )
        corps.append("")
    # Actualité par rubrique (mainteneur, 03.11.2026) : les fiches versées dont la publication vérifiée est dans la
    # fenêtre de fraîcheur de la lettre ; sous le seuil du registre, une alerte.
    if actualite and actualite[0]:
        from newskit import lettre  # noqa: PLC0415

        rangs, jours, minimum = actualite
        corps += [f"### Actualité par rubrique (publication vérifiée de moins de {jours} jours)", ""]
        corps += [f"- {x['nom']} : {x['n']}" for x in rangs]
        corps += [f"- {a}" for a in lettre.alertes_d_actualite(rangs, jours, minimum)] + [""]
    if j.attention:
        corps += ["### Ce qui demande une session assistée", ""]
        corps += [f"- {a}" for a in j.attention] + [""]
        corps += [
            "Liste détaillée à jour : `python3 -m newskit.routine_assistee etat`.",
            "Pour consigner le résultat d'une session assistée, erreurs comprises :",
            "`python3 -m newskit.routine_assistee consigner <resultats.json>`.",
            "",
        ]
    if j.alertes:
        corps += ["### Échecs bloquants", ""]
        corps += [f"- {a}" for a in j.alertes] + [""]
    # Les alertes non bloquantes du déroulé (refus, flux illisible, session qui
    # ne débloque pas) : dites en clair, sans obliger à lire le déroulé.
    alertes = []
    for l in j.lignes:
        t = l.strip()
        if t.startswith(("ALERTE", "!!", "REFUS")) and t not in alertes:
            alertes.append(t)
    if alertes:
        corps += ["### Alertes", ""] + [f"- {a[:220]}" for a in alertes[:10]] + [""]
    if autres:
        corps += ["### Autres graphes", ""]
        corps += [
            f"- {g} : {v['fiches']} fiches, {v['noeuds']} nœuds, {v['aretes']} arêtes" for g, v in autres.items()
        ] + [""]
    if nouvelles is not None:
        total = sum(len(v) for v in nouvelles.values())
        corps += [f"### Nouvelles fiches ({total})", ""]
        if not nouvelles:
            corps += ["Aucune fiche neuve cette semaine.", ""]
        for groupe, fiches in nouvelles.items():
            corps += [f"**{groupe} ({len(fiches)})**", ""]
            for f in fiches[:PLAFOND_PAR_GROUPE]:
                titre = f["titre"].replace("[", "(").replace("]", ")")
                corps.append(f"- {f['date']} — [{titre}]({f['url']})" if f["url"] else f"- {f['date']} — {titre}")
            if len(fiches) > PLAFOND_PAR_GROUPE:
                corps.append(f"- … et {len(fiches) - PLAFOND_PAR_GROUPE} de plus")
            corps.append("")
        if modifiees:
            corps += [f"{modifiees} fiche(s) existante(s) mise(s) à jour (aperçu complété, date réparée).", ""]
    corps += ["### Déroulé", "", "```", *j.lignes, "```", ""]
    RAPPORT.write_text(commun.masquer_chemins("\n".join(corps)), encoding="utf-8")  # sans chemin de la machine


VERROU = RACINE / "cache" / "routine.verrou"
ATTENTE_VERROU = 12 * 3600  # 3 h jusqu'au 04.10.2026 : un passage de gros import en tient plusieurs à lui seul


VERROU_COLLECTE = RACINE / "cache" / "collecte_continue.verrou"  # tenu par la collecte continue pendant ses flux


def attendre_la_collecte(chemin: Path | None = None) -> None:
    """Attend la fin d'une lecture des flux par la collecte continue, qui ne tient plus le verrou des routines
    (05.11.2026) : deux lectures des mêmes flux écriraient les mêmes manifestes."""
    import fcntl  # noqa: PLC0415

    chemin = chemin or VERROU_COLLECTE
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        fcntl.flock(f, fcntl.LOCK_UN)


def graphe_en_routine(chemin: Path = VERROU) -> str | None:
    """Le graphe dont la routine tient le verrou, None si le verrou est libre. Lire n'efface rien (mode « a+ »)."""
    import fcntl  # noqa: PLC0415

    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            return f.read().strip() or "?"
        fcntl.flock(f, fcntl.LOCK_UN)
        return None


def prendre_le_verrou(chemin: Path = VERROU, attente: int = ATTENTE_VERROU, graphe: str = ""):
    """Une routine à la fois dans le dépôt. Rend le fichier verrouillé, à garder ouvert.

    Trois daemons y écrivent depuis le 02.10.2026 (principal le lundi, un graphe financier et
    un graphe d'actualité chaque jour), et chacun finit par `git add -A` : deux passages
    simultanés se voleraient l'index et commiteraient les fichiers à moitié
    écrits de l'autre. Un passage qui en trouve un autre attend son tour ; au-delà
    de douze heures il renonce et le dit, plutôt que d'attendre un processus bloqué.
    """
    import fcntl  # noqa: PLC0415

    chemin.parent.mkdir(parents=True, exist_ok=True)
    f = open(chemin, "a+", encoding="utf-8")  # noqa: SIM115 — gardé ouvert ; « a+ » : attendre n'efface pas le nom écrit
    debut, dit = time.time(), 0.0
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.seek(0)
            f.truncate()
            f.write(graphe)  # la collecte continue lit quel graphe tourne (05.11.2026)
            f.flush()
            return f
        except OSError:
            if time.time() - debut >= attente:
                print(
                    f"  ARRÊT : une autre routine tient le verrou depuis plus de {attente // 60} min, "
                    f"passage abandonné",
                    file=sys.stderr,
                )
                sys.exit(1)
            if time.time() - dit >= 600:  # le dire tout de suite, puis toutes les dix minutes
                print(f"  une autre routine tourne : attente du verrou ({chemin.name})", flush=True)
                dit = time.time()
            time.sleep(15)


def _marque_du_passage(graphe: str) -> Path:
    return RACINE / "cache" / "dernier_passage" / graphe


def dernier_passage(graphe: str) -> datetime.date | None:
    """Le jour du dernier passage du graphe (cache/dernier_passage/<graphe>), ou None."""
    try:
        return datetime.date.fromisoformat(_marque_du_passage(graphe).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def noter_le_passage(graphe: str, jour: datetime.date | None = None) -> None:
    f = _marque_du_passage(graphe)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text((jour or datetime.date.today()).isoformat() + "\n", encoding="utf-8")


def jour_de_passage(graphe: str, jour: datetime.date | None = None) -> bool:
    """Vrai si un passage planifié part ce jour : aucun passage encore, le même jour (une autre heure du même
    passage), ou au moins `tous_les` jours depuis le dernier."""
    jour = jour or datetime.date.today()
    d = dernier_passage(graphe)
    if d is None:
        return True
    ecart = (jour - d).days
    return ecart <= 0 or ecart >= media.passages()[0]


def exiger_le_temoin() -> int | None:
    """La routine ne part pas sur un témoin rouge, d'un autre jour ou d'un autre commit (CHANTIER_GENERIQUE.md § 4.3 :
    le témoin tourne à 00:30 et pose cache/temoin_vert). Dans le témoin lui-même (TEMOIN_REEL), rien n'est exigé.
    Rend le code de sortie d'un refus, ou None."""
    if os.environ.get("TEMOIN_REEL"):
        return None
    from newskit import temoin  # noqa: PLC0415

    ok, dit = temoin.verdict(RACINE, temoin.commit_du_code(), time.strftime("%Y-%m-%d"))
    if dit:
        print(f"{'' if ok else 'ROUTINE ARRÊTÉE : '}{dit}", file=sys.stderr if not ok else sys.stdout)
    return None if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sans-commit", action="store_true")
    ap.add_argument("--sans-collecte", action="store_true")
    ap.add_argument(
        "--jours",
        type=int,
        default=3,
        help="fenêtre de veille arXiv (défaut 3 : passage quotidien, week-end et jour manqué couverts)",
    )
    ap.add_argument(
        "--plafond",
        type=int,
        help="fiches extraites à ce passage, au plus (défaut : la règle du registre). "
        "Sert au passage court qui suit un lien déposé : seules les dernières arrivées.",
    )
    ap.add_argument("--sans-courriel", action="store_true", help="ne pas envoyer le courriel de fin de passage")
    ap.add_argument(
        "--pyramide-plafond",
        type=int,
        help="notions rédigées par la pyramide à ce passage, au plus (défaut : le registre)",
    )
    ap.add_argument(
        "--planifie",
        action="store_true",
        help="lancé par la tâche planifiée : un jour hors de "
        "l'intervalle du média (media.passages.tous_les), s'arrêter",
    )
    ap.add_argument(
        "--graphe",
        help="passage limité à un graphe du registre : ses sources, sa couche 1, "
        "sa couche 2, sa composition, son rapport (graphe/<nom>/ROUTINE.md)",
    )
    args = ap.parse_args()

    global RAPPORT, SANS_COURRIEL, PYRAMIDE_PLAFOND_PASSAGE
    SANS_COURRIEL = args.sans_courriel
    PYRAMIDE_PLAFOND_PASSAGE = args.pyramide_plafond
    if args.graphe:
        args.graphe = cfg.nom_actuel(args.graphe)  # un daemon installé sous un ancien nom continue de tourner
    seul = args.graphe if args.graphe and args.graphe != cfg.GRAPHE_PRINCIPAL else None
    if seul:
        if seul not in cfg.graphes():
            print(f"graphe inconnu : {seul} (déclarés : {', '.join(cfg.graphes())})", file=sys.stderr)
            return 1
        RAPPORT = cfg.chemins_du_graphe(seul)["rapport"]
        COURRIEL_GRAPHE[:] = ["--graphe", seul]
    # Interrupteur de pause (porteur, 08.10.2026 : « la moitié de mon forfait hebdomadaire en 24 heures ») : tant que
    # cache/routines.pause existe, aucune routine ne tourne — ni collecte, ni extraction, ni pyramide, ni banc. Les
    # daemons launchd démarrent, lisent ce fichier et s'arrêtent. Reprendre : supprimer le fichier.
    # Reprise graphe par graphe (porteur, 09.10.2026 : « uniquement compliance et quant ») : une ligne
    # `sauf: <graphe>, <graphe>` dans le fichier de pause laisse tourner les routines nommées (le nom du daemon,
    # ou l'un des graphes qu'il inclut) ; les autres restent arrêtées.
    pause = RACINE / "cache" / "routines.pause"
    if pause.exists():
        texte = pause.read_text(encoding="utf-8")
        m = re.search(r"^sauf\s*:\s*(.+)$", texte, flags=re.M)
        exceptes = {x.strip() for x in m.group(1).split(",") if x.strip()} if m else set()
        concernes = {args.graphe or cfg.GRAPHE_PRINCIPAL, *cfg.graphes_inclus(args.graphe or cfg.GRAPHE_PRINCIPAL)}
        if not (exceptes & concernes):
            print(f"Routines en pause ({pause.relative_to(RACINE)}) : {texte.strip()[:200]}")
            return 0
        print(f"Routines en pause, sauf {', '.join(sorted(exceptes))} : ce passage tourne")
    if args.planifie and not jour_de_passage(seul or cfg.GRAPHE_PRINCIPAL):
        n = media.passages()[0]
        print(
            f"Pas de passage aujourd'hui : un tous les {n} jours (media.passages), le dernier le "
            f"{dernier_passage(seul or cfg.GRAPHE_PRINCIPAL)}"
        )
        return 0
    if (refus := exiger_le_temoin()) is not None:
        return refus
    verrou = prendre_le_verrou(graphe=seul or cfg.GRAPHE_PRINCIPAL)
    noter_le_passage(seul or cfg.GRAPHE_PRINCIPAL)
    attendre_la_collecte()
    j = Journal()
    debut_passage = time.strftime("%Y-%m-%d %H:%M:%S")  # borne basse du registre des coûts pour ce passage
    j.dire(f"Routine du graphe {seul or cfg.GRAPHE_PRINCIPAL} — {debut_passage[:16]}")
    # Un graphe qui en inclut d'autres les traite d'abord, dans le même passage : un graphe financier collecte,
    # extrait et compose un graphe de trading quantitatif, puis se compose par-dessus (04.10.2026). Un seul daemon, un ordre sûr.
    graphes = [*cfg.graphes_inclus(seul), seul] if seul else [cfg.GRAPHE_PRINCIPAL]
    for g_ in graphes:  # un graphe neuf n'a pas encore de dossier (témoin, N4, 06.11.2026)
        cfg.chemins_du_graphe(g_)["base"].mkdir(parents=True, exist_ok=True)
    j.dire()
    ids_avant, libelles_avant = etat_du_graphe(seul)
    nouveautes: dict | None = None

    # Les fiches comptées sont celles du graphe traité (avec ceux qu'il inclut), pas tout le corpus.
    def fiches_du_graphe() -> int:
        return sum(
            1
            for dossier in cfg.dossiers_du_graphe(seul or cfg.GRAPHE_PRINCIPAL)
            for f in (RACINE / dossier).rglob("*.md")
            if not f.name.startswith("_")
        )

    # --- Collecte, non bloquante : un site injoignable ne doit pas empêcher
    #     de valider et de recomposer ce qui est déjà là.
    # Les sessions qu'un script sait ouvrir (Ghost : lien par courriel, boîte dédiée)
    # sont éprouvées avant la collecte, et rouvertes si elles ne passent plus.
    # Non bloquant : sans boîte configurée, la collecte se fait sans session et le dit.
    if not args.sans_collecte:
        lancer(
            j,
            "sessions ouvertes par script",
            ["scripts/ouvrir_session.py", "--si-besoin", *(["--graphe", seul] if seul else [])],
            garder=3,
        )
    if not args.sans_collecte and seul:
        # Un graphe seul : ses collecteurs, filtrés par le registre. Les sources
        # propres au principal (LinkedIn, Substack, Medium, veille arXiv) ne
        # tournent pas ici.
        for g in graphes:
            e = "" if g == seul else f" ({g})"
            j.dire(f"Collecte ({g})")
            sorties = ""
            for libelle, plateformes, script, garder in (
                ("flux RSS", ("Flux RSS",), "collecte_rss.py", 8),
                ("OpenAlex", ("OpenAlex",), "collecte_openalex.py", 4),
                ("GitHub et HuggingFace", ("GitHub", "HuggingFace"), "collecte_depots.py", 4),
                ("dossier local", ("Dossier local",), "collecte_dossier_local.py", 4),
                ("courriel", ("Courriel",), "collecte_courriel.py", 4),
            ):
                # Lancé seulement si le graphe a une telle source : un graphe financier n'a plus de flux (un graphe de trading quantitatif les porte).
                if any(cfg.toutes(p, graphe=g) for p in plateformes):
                    sorties += lancer(j, libelle + e, [f"scripts/{script}", "--graphe", g], garder=garder)[1]
            # Ce qu'un graphe indépendant peut avoir en propre (graphe ethique-ia, 02.10.2026) :
            # des publications Substack, une bibliothèque de PDF, sa veille arXiv.
            if cfg.toutes("Substack", graphe=g):
                lancer(j, "Substack" + e, ["scripts/collecte_substack.py", "--update", "--graphe", g], garder=6)
            if cfg.toutes("Bibliothèque locale", graphe=g):
                lancer(j, "bibliothèque locale" + e, ["scripts/collecte_pdf_local.py", "--graphe", g], garder=4)
            if cfg.a_une_veille(g):
                lancer(
                    j,
                    "arXiv, veille" + e,
                    ["scripts/collecte_arxiv.py", "--veille", "--graphe", g, "--jours", str(args.jours)],
                    garder=4,
                )
            signaler_sources_muettes(j, g, sources_muettes(g))
            ecrire_liste_des_muettes(g, liste_des_muettes(g, sorties))
            j.dire()
    elif not args.sans_collecte:
        # Le graphe principal, chaque jour depuis le 04.10.2026 (il passait le lundi, et recomposait
        # alors tous les graphes : chacun a maintenant son daemon). Ses collecteurs ne visent que lui.
        j.dire("Collecte")
        p = cfg.GRAPHE_PRINCIPAL
        if time.localtime().tm_wday == 0:
            # LinkedIn le lundi seulement : les lettres sont hebdomadaires, et une connexion
            # automatique par jour expose le compte à un blocage pour rien.
            lancer(j, "LinkedIn", ["scripts/collecte_linkedin.py", "--update"])
        else:
            j.dire("  --      LinkedIn : le lundi seulement")
        lancer(j, "Substack", ["scripts/collecte_substack.py", "--update", "--graphe", p])
        if cfg.toutes("Medium"):  # retirée du registre le 11.10.2026 ; rien à lancer sans source déclarée
            lancer(j, "Medium", ["scripts/collecte_medium.py", "--update"], garder=6)
        # Avant de collecter : une source qui s'éteint ne produit pas d'erreur,
        # elle produit zéro fiche neuve, ce qui ressemble à une semaine calme.
        lancer(j, "santé des sources", ["scripts/prospecter_source.py", "--sante"], garder=4)
        _, sortie_flux = lancer(j, "flux RSS", ["scripts/collecte_rss.py", "--graphe", p], garder=8)
        lancer(j, "revues (Crossref)", ["scripts/collecte_revue.py"], garder=3)
        lancer(j, "OpenAlex", ["scripts/collecte_openalex.py", "--graphe", p], garder=4)
        lancer(j, "GitHub et HuggingFace", ["scripts/collecte_depots.py", "--graphe", p], garder=4)
        lancer(j, "dossier local", ["scripts/collecte_dossier_local.py", "--graphe", p], garder=4)
        if cfg.toutes("Courriel", graphe=p):  # lettres reçues par courriel (03.11.2026)
            lancer(j, "courriel", ["scripts/collecte_courriel.py", "--graphe", p], garder=4)
        lancer(j, "arXiv, papiers cités", ["scripts/collecte_arxiv.py", "--citations"])
        ok_veille, sortie_veille = (
            lancer(
                j,
                "arXiv, veille",
                ["scripts/collecte_arxiv.py", "--veille", "--graphe", p, "--jours", str(args.jours)],
                garder=4,
            )
            if cfg.a_une_veille(p)
            else (False, "")
        )  # facultative au registre (01.11.2026)
        # Le contrôle est bon marché : si la moisson a rendu des notices et
        # qu'aucune fiche n'en sort, soit tout était déjà en fiche, soit le
        # filtre rejette tout. Voir `lire_veille`.
        if ok_veille:
            notices, ecrites, vues = lire_veille(sortie_veille)
            if notices > 1000 and ecrites == 0:
                if vues == 0:
                    j.signal(
                        f"veille : {notices} notices moissonnées, aucune vue par un thème — "
                        f"le filtre rejette tout, vérifier collecte_arxiv.filtrer_recolte"
                    )
                else:
                    j.dire(f"      rien de neuf : {vues} notices vues par les thèmes, toutes déjà en fiche")
        signaler_sources_muettes(j, p, sources_muettes(p))
        ecrire_liste_des_muettes(p, liste_des_muettes(p, sortie_flux))
        j.dire()

    # Le canon, chaque jour (combler_canon.py) : ce que la mesure de la veille a trouvé absent reçoit une
    # source réelle — papier par son titre, dépôt par son nom, page de référence — avant l'extraction,
    # pour que la notion soit dans le graphe le jour même. Non bloquant.
    if not args.sans_collecte:
        # Les liens que le porteur a déposés depuis le navigateur (extension « Ajouter à veille ») : relevés
        # sur le serveur et ingérés, pour tous les graphes, à chaque routine. Sans dépôt en attente, c'est une
        # requête SSH et rien d'autre.
        lancer(j, "liens déposés", ["scripts/depots.py"], garder=8)
        # Les textes de référence changent (porteur, 09.10.2026) : la veille des versions (diachronie.py) compare
        # les versions déclarées aux versions consolidées en ligne (Cellar de l'UE, Fedlex), sans modèle. Une
        # version nouvelle est un « à faire » : elle se déclare et se relit, elle n'entre pas d'elle-même.
        try:
            import newskit.collecte_textes as collecte_textes  # noqa: PLC0415

            textes = collecte_textes.TEXTES
        except ModuleNotFoundError:  # module propre à l'édition, facultatif (témoin, 06.11.2026)
            textes = {}
        for g in graphes:
            if textes.get(g):
                etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
                _, sortie_v = lancer(
                    j,
                    f"versions des textes de référence{etiquette}",
                    ["scripts/diachronie.py", "--graphe", g, "--versions"],
                    garder=2,
                    delai=900,
                )
                for ligne_v in sortie_v.splitlines():
                    if "À FAIRE" in ligne_v:
                        j.signal(ligne_v.strip().removeprefix("À FAIRE : "))
        for g in graphes:
            etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
            if not inactive(j, g, "canon", f"canon{etiquette}"):
                lancer(j, f"canon{etiquette}", ["scripts/combler_canon.py", "--graphe", g], garder=24, modele=True)
            # L'éclaireur : quelques recherches web sur les thèmes émergents, le canon à trancher et les
            # sujets du jour ; les pages trouvées sont ingérées. Bornes au registre (clé `eclaireur`).
            if not inactive(j, g, "eclaireur", f"éclaireur{etiquette}"):
                lancer(j, f"éclaireur{etiquette}", ["scripts/eclaireur.py", "--graphe", g], garder=20, modele=True)
            # Le poseur : des questions de praticien posées au graphe ; la page d'une question manquée est ingérée,
            # et la question reposée les jours suivants (07.10.2026). Avant la couche 1 : ses fiches sont lues ce soir.
            if not inactive(j, g, "poseur", f"poseur de questions{etiquette}"):
                lancer(
                    j,
                    f"poseur de questions{etiquette}",
                    ["scripts/poseur.py", "--graphe", g],
                    garder=20,
                    delai=3600,
                    modele=True,
                )
        # Les pages du canon, de l'éclaireur et des dépôts arrivent après la collecte des papiers cités :
        # un papier qu'elles citent serait un nœud sans fiche jusqu'au passage suivant, et le contrôle de
        # fraîcheur échouerait d'ici là (05.10.2026, une page d'OpenAI citant arXiv 2211.02011).
        if cfg.GRAPHE_PRINCIPAL in graphes:
            lancer(j, "arXiv, papiers cités des pages ingérées", ["scripts/collecte_arxiv.py", "--citations"])
        j.dire()
    elif cfg.GRAPHE_PRINCIPAL in graphes:
        # Même sans collecte, un canon passé à part (reprise du 06.10.2026) a pu écrire des pages qui citent des papiers :
        # sans leurs fiches, le contrôle de fraîcheur échoue (deux dépôts GitHub, trois papiers cités). Une seconde quand
        # rien ne manque.
        lancer(j, "arXiv, papiers cités (sans collecte)", ["scripts/collecte_arxiv.py", "--citations"])

    # --- Contrôles bloquants
    j.dire("Contrôles")
    ok, _ = lancer(j, "registre des sources", ["scripts/config_sources.py"], bloquant=True, garder=16)
    if not ok:
        j.dire("\nArrêt : le registre des sources est invalide, la collecte n'a pas de base.")
        ecrire_rapport(j, "ÉCHEC registre", {})
        courriel()
        notifier(cfg.edition()["nom"], "Routine arrêtée : sources.yaml invalide")
        return 1

    ok, _ = lancer(j, "contrat de fiche", ["scripts/valider_fiches.py"], bloquant=True)
    if not ok:
        j.dire("\nArrêt avant le commit : le corpus ne respecte pas son contrat.")
        ecrire_rapport(j, "ÉCHEC contrat", {})
        courriel()
        notifier(cfg.edition()["nom"], "Routine arrêtée : contrat de fiche non respecté")
        return 1

    # Un graphe par usage (registre, champ `graphe`) : le principal d'abord,
    # puis les autres, chacun avec sa couche 1 et son contrôle de fraîcheur,
    # tous deux bloquants — un graphe en retard, quel qu'il soit, arrête le commit.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        ok, _ = lancer(
            j, f"couche 1 du graphe{etiquette}", ["scripts/extraire_squelette.py", "--graphe", g], bloquant=True
        )
        if not ok or not controle_fraicheur(j, g):
            j.dire("\nArrêt avant le commit : le graphe est en retard sur le corpus.")
            ecrire_rapport(j, "ÉCHEC fraîcheur", {})
            passage_arrete(j, seul or cfg.GRAPHE_PRINCIPAL)
            courriel()
            notifier(cfg.edition()["nom"], "Routine arrêtée : graphe en retard sur le corpus")
            return 1
    j.dire()

    # --- Composition et carte
    j.dire("Graphe")
    # Couche 2 incrémentale : les concepts des fiches denses nouvelles ou
    # modifiées, par Claude (abonnement). Non bloquante — sans Claude Code ou
    # sans réseau, les fiches restent en retard et le rapport le dit — mais
    # avant la composition, pour que les nouveaux lots entrent dans le graphe.
    # Couche 2 et structure des titres par graphe, pour ceux qui ont des fiches
    # denses (graphe financier depuis le 27.09.2026). Sans cette étape, un clone
    # neuf compose un graphe amputé de 1 500 nœuds sans le dire.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if not cfg.dossiers_denses(g) or inactive(j, g, "couche2", f"couche 2 incrémentale{etiquette}"):
            continue
        # Le délai suit le travail (04.10.2026) : le passage extrait toutes les arrivées du jour plus dix du
        # stock, donc 250 fiches un jour de gros import. Une heure n'y suffisait pas, et ce qui restait
        # retombait au rythme du stock. Deux minutes par fiche annoncée, dix de marge, une heure au moins.
        etat_c2 = subprocess.run(
            [PYTHON, str(CODE / "extraire_concepts.py"), "--graphe", g, "--etat"],
            cwd=RACINE,
            capture_output=True,
            text=True,
            timeout=600,
        ).stdout
        m_c2 = re.search(r"(\d+) fiche\(s\) dense\(s\) en retard(?:, (\d+) traitées)?", etat_c2)
        a_extraire = int(m_c2.group(2) or m_c2.group(1)) if m_c2 else 0
        borne = ["--plafond", str(args.plafond)] if args.plafond else []
        lancer(
            j,
            f"couche 2 incrémentale{etiquette}",
            ["scripts/extraire_concepts.py", "--graphe", g, *borne],
            garder=6,
            delai=max(3600, min(a_extraire, args.plafond or a_extraire) * 120 + 600),
            modele=True,
        )
        ok, _ = lancer(
            j, f"structure des titres{etiquette}", ["scripts/extraire_structure.py", "--graphe", g], garder=1
        )
        # Le journal des événements de veille (NEWSKIT.md, N3) : auteurs nouveaux, fiches d'auteurs connus.
        lancer(j, f"journal des événements{etiquette}", ["scripts/evenements.py", "--graphe", g], garder=1)
        if not ok:
            j.signal(f"structure des titres{etiquette} non produite : le graphe est composé sans elle")
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        # Les doublons de notions (traduction, sigle, variante) : candidats par l'encodeur local, tranchés par Sonnet,
        # gardés dans alias.json et appliqués par la composition qui suit (fondre_doublons.py, 09.10.2026).
        if (cfg.chemins_du_graphe(g)["base"] / "graphe.json.gz").exists() and not inactive(
            j, g, "alias", f"doublons de notions{etiquette}"
        ):
            lancer(
                j,
                f"doublons de notions{etiquette}",
                ["scripts/fondre_doublons.py", "--graphe", g],
                garder=3,
                modele=True,
            )
        _, sortie_c = lancer(j, f"composition{etiquette}", ["scripts/fusionner_graphe.py", "--graphe", g], garder=3)
        for ligne_c in sortie_c.splitlines():  # un alias sans cible se relit : il est dit au rapport (06.11.2026)
            if "alias sans cible" in ligne_c:
                j.signal(f"composition{etiquette} : {ligne_c.strip()}")
        # Le passe-partout de chaque source (paragraphes répétés d'une fiche à l'autre), recalculé sur le corpus du jour et
        # sauté par toute lecture des fiches : passages, preuves, index (porteur, 10.10.2026). Sans appel, quelques secondes.
        lancer(j, f"passe-partout{etiquette}", ["scripts/passe_partout.py", "--graphe", g], garder=1)
        lancer(j, f"carte du domaine{etiquette}", ["scripts/digerer_graphe.py", "--graphe", g], garder=1)
        # L'index des paragraphes (mots et vecteurs, synthese.py indexer) se rafraîchit chaque nuit là où il existe : une
        # vingtaine de minutes de calcul local pour un graphe de conformité, aucun appel. Il se crée à la main la première fois.
        if (cfg.chemins_du_graphe(g)["base"] / "synthese" / "vecteurs.npy").exists() and not inactive(
            j, g, "index", f"index des paragraphes{etiquette}"
        ):
            lancer(
                j,
                f"index des paragraphes{etiquette}",
                ["scripts/synthese.py", "indexer", "--graphe", g],
                garder=1,
                delai=2 * 3600,
            )

    versees_du_passage = versees_du_graphe(seul or cfg.GRAPHE_PRINCIPAL)
    nouveautes = nouveautes_du_graphe(j, seul or cfg.GRAPHE_PRINCIPAL, ids_avant, libelles_avant, versees_du_passage)
    # Thèmes émergents : les communautés de la carte qu'aucun sujet déclaré ne couvre, soumises au
    # porteur après deux passages (themes_emergents.py). Un candidat retenu est un « à faire » :
    # il paraît dans le courriel, et n'entre au skill que par `accepter`.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if inactive(j, g, "themes", f"thèmes émergents{etiquette}"):
            continue
        lancer(j, f"thèmes émergents{etiquette}", ["scripts/themes_emergents.py", "--graphe", g], garder=4, modele=True)
        p_themes = cfg.chemins_du_graphe(g)["base"] / "themes.json"
        # Un candidat tenu trois passages entre de lui-même parmi les sujets déclarés (porteur, 09.10.2026), et le banc
        # par thème lui compose son sous-jeu de dix questions ; `themes_auto: false` au registre pour revenir à la
        # proposition seule. Se défait : python3 -m newskit.themes_emergents --graphe <g> retirer "<libellé>".
        if p_themes.exists() and cfg.graphe_declare(g).get("themes_auto", True):
            ok_auto, sortie_auto = lancer(
                j,
                f"thèmes émergents, entrée automatique{etiquette}",
                ["scripts/themes_emergents.py", "--graphe", g, "auto"],
                garder=1,
            )
            m_auto = re.search(r"(\d+) sujet\(s\) ajouté\(s\) de lui-même(?: : (.*))?", sortie_auto or "")
            if m_auto and int(m_auto.group(1)):
                for lib in [x.strip() for x in (m_auto.group(2) or "").split(";") if x.strip()]:
                    j.signal(
                        f"thème émergent{etiquette} entré parmi les sujets : « {lib} ». "
                        f'Retirer : python3 -m newskit.themes_emergents --graphe {g} retirer "{lib[:40]}"'
                    )
                    lancer(
                        j,
                        f"banc par thème, sous-jeu « {lib[:40]} »{etiquette}",
                        ["scripts/banc_themes.py", "--graphe", g, "--composer", "--themes", lib],
                        garder=2,
                        modele=True,
                    )
        if p_themes.exists():
            pass
            from newskit import themes_emergents as te  # noqa: PLC0415

            # La chaîne d'approfondissement des sujets neufs, où elle en est.
            for lib, v in te.lire_etat(g)["nouveaux"].items():
                if v.get("statut") == "en cours":
                    j.dire(
                        f"  sujet neuf{etiquette} « {lib[:60]} » (depuis le {v.get('date')}) : éclaireur passage {v.get('nuits', 0)}/{te.NUITS_NEUF}, "
                        f"arXiv {len(v.get('arxiv') or [])} expression(s), banc par thème : pas encore tenu"
                    )
            for n, (_, c) in enumerate(te.retenus(json.loads(p_themes.read_text(encoding="utf-8"))), 1):
                j.signal(
                    f"thème émergent{etiquette} : « {c.get('libelle') or c['etiquette']} » — {c['taille']} notions, "
                    f"{c['passages']} passages ({', '.join(c['notions'][:4])}) ; entre de lui-même à "
                    f"{te.MIN_PASSAGES_AUTO}. Accepter : python3 -m newskit.themes_emergents --graphe {g} accepter {n}"
                )

    # L'avance du graphe sur un modèle de langage, chaque jour (mesurer_avance.py, deux appels à claude -p) :
    # part de son canon que le graphe porte, notions récentes qu'il ignore, rendement de chaque source.
    # Le but fixé le 04.10.2026 est d'être plus complet et plus à jour que lui ; cela se suit ici.
    if not args.sans_collecte:  # à chaque passage complet (04.10.2026) ; pas au passage court d'un dépôt
        for g in graphes:
            etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
            if inactive(j, g, "avance", f"avance sur un modèle{etiquette}"):
                continue
            lancer(
                j,
                f"avance sur un modèle{etiquette}",
                ["scripts/mesurer_avance.py", "--graphe", g],
                garder=40,
                modele=True,
            )
            p_avance = cfg.chemins_du_graphe(g)["base"] / "avance.json"
            if p_avance.exists():
                m = json.loads(p_avance.read_text(encoding="utf-8"))
                if (
                    m.get("date") == time.strftime("%Y-%m-%d")
                    and m.get("completude") is not None
                    and m["completude"] < 0.9
                ):
                    absentes = [n for s in m["sujets"] for n in s["absentes"]]
                    j.signal(
                        f"socle{etiquette} : {m['completude']:.0%} du socle de connaissances d'un LLM — {len(absentes)} notion(s) "
                        f"qu'il tient pour essentielles manquent au graphe ({', '.join(absentes[:8])}…). "
                        f"Liste : python3 -m newskit.mesurer_avance --graphe {g} --etat"
                    )

    # La pyramide de synthèses, standard de réponse depuis le 05.10.2026 : tenue à jour chaque jour pour les graphes
    # qui en ont une (synthèses aux preuves changées réécrites, notions devenues éligibles rédigées). Sans plafond du
    # 06.10 au 08.10.2026 : une nuit a soumis 6 415 notions pour un graphe d'éthique et 7 435 pour un graphe de sécurité, toutes refusées par la
    # limite d'usage. Depuis : PYRAMIDE_PLAFOND notions par nuit (ou `pyramide_plafond` au registre, par graphe) — un
    # retard se résorbe en quelques nuits au lieu d'une, et la facture d'une nuit est bornée.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if (cfg.chemins_du_graphe(g)["base"] / "synthese" / "notions.json").exists() and not inactive(
            j, g, "pyramide", f"pyramide de synthèses{etiquette}"
        ):
            plafond_p = str(PYRAMIDE_PLAFOND_PASSAGE or cfg.graphe_declare(g).get("pyramide_plafond", PYRAMIDE_PLAFOND))
            lancer(
                j,
                f"pyramide de synthèses{etiquette}",
                ["scripts/synthese.py", "mettre-a-jour", "--graphe", g, "--plafond", plafond_p],
                garder=3,
                delai=6 * 3600,
                modele=True,
            )

    # Les cartes des sujets déclarés (cartes.py, 09.10.2026) : une carte par sujet, réécrite quand les synthèses de ses
    # notions ont changé, au plus CARTES_PLAFOND par nuit (`cartes_plafond` au registre). Elles tiennent lieu de thème dans
    # `repondre` ; tenues après la pyramide, avant le banc, pour que le banc mesure les cartes du jour.
    from newskit import themes_emergents as te  # noqa: PLC0415

    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if (
            (cfg.chemins_du_graphe(g)["base"] / "synthese" / "notions.json").exists()
            and te.sujets_declares(g)
            and not inactive(j, g, "cartes", f"cartes des sujets{etiquette}")
        ):
            plafond_c = str(cfg.graphe_declare(g).get("cartes_plafond", CARTES_PLAFOND))
            lancer(
                j,
                f"cartes des sujets{etiquette}",
                ["scripts/cartes.py", "rediger", "--graphe", g, "--plafond", plafond_c],
                garder=3,
                delai=3600,
                modele=True,
            )

    # Le cerveau CortexDB vient après tout ce qu'il verse : lots, composition, index, pyramide et cartes du jour. Construit
    # avant la pyramide jusqu'au 02.11.2026, il publiait chaque nuit les synthèses de la veille.
    for g in graphes:
        cerveau_cortexdb(j, g, "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})")

    # Les points peu couverts (peu_couverts.py, porteur 10.10.2026) : par sujet, ce que le canon, le banc par thème, les
    # lacunes et les cartes disent mal couvert, remonté à la carte, à l'éclaireur et à la page ; sans appel.
    peu_couverts_lignes: list[str] = []
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if te.sujets_declares(g) and not inactive(j, g, "cartes", f"points peu couverts{etiquette}"):
            ok, sortie_pc = lancer(
                j, f"points peu couverts{etiquette}", ["scripts/peu_couverts.py", "--graphe", g], garder=1
            )
            m_pc = [l for l in sortie_pc.splitlines() if "peu couvert" in l]
            if ok and m_pc:
                peu_couverts_lignes.append(m_pc[-1])

    # Le renouvellement du banc, quand il est dû (registre : banc.renouveler_jours) : les pages des questions extérieures
    # manquées sont ingérées, leur jeu retiré et tiré à neuf, le banc posé (07.10.2026). Une seconde quand rien n'est dû.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if inactive(j, g, "banc", f"banc, renouvellement{etiquette}"):
            continue
        lancer(
            j,
            f"banc, renouvellement{etiquette}",
            ["scripts/banc.py", "--graphe", g, "--cycle-du-jour"],
            garder=4,
            delai=3 * 3600,
            modele=True,
        )
        # Le banc par thème (profondeur du socle), quand il est dû : première mesure, mesure de plus de 30 jours, ou
        # thème nouveau au jeu (entré de lui-même ou accepté). Une ligne quand rien n'est dû.
        if (cfg.chemins_du_graphe(g)["base"] / "themes_banc.json").exists():
            ok_bt, sortie_bt = lancer(
                j,
                f"banc par thème{etiquette}",
                ["scripts/banc_themes.py", "--graphe", g, "--poser-du-jour"],
                garder=16,
                delai=2 * 3600,
                modele=True,
            )
            # Après une pose : les pages des questions manquées entrent au graphe, les questions sont remplacées
            # (banc_themes.combler) ; la pose suivante mesurera les remplaçantes.
            if ok_bt and "profondeur :" in (sortie_bt or ""):
                lancer(
                    j,
                    f"banc par thème, comblement{etiquette}",
                    ["scripts/banc_themes.py", "--graphe", g, "--combler"],
                    garder=6,
                    delai=3600,
                    modele=True,
                )

    # Les questions récentes du banc reposées au graphe seul, chaque nuit où le graphe, sa pyramide ou ses cartes ont
    # changé (porteur, 10.10.2026 : « la repose devrait faire partie de la routine, après la mise à jour du graphe et des
    # cartes ») : le chiffre publié « Graphe » suit la composition de la nuit ; « LLM seul » et « LLM + web » mesurent le
    # modèle et attendent le renouvellement. banc.py ne repose rien à un graphe inchangé, ni à un banc de l'ancienne
    # méthode. Coût mesuré le 10.10.2026 sur un graphe de conformité : 50 questions, Opus par la pyramide, de l'ordre de 2 à 3 $.
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        if inactive(j, g, "repose", f"banc, questions récentes reposées{etiquette}"):
            continue
        lancer(
            j,
            f"banc, questions récentes reposées{etiquette}",
            ["scripts/banc.py", "--graphe", g, "--reposer-recentes"],
            garder=3,
            delai=3600,
            modele=True,
        )

    # L'acuité : la synthèse de l'écart avec un modèle (banc, canon, avance, fraîcheur, corroboration,
    # lacunes), sans appel — elle lit ce qui vient d'être mesuré. Sa ligne va au bilan et au courriel.
    acuites: list[str] = []
    for g in graphes:
        etiquette = "" if g == cfg.GRAPHE_PRINCIPAL else f" ({g})"
        ok, sortie_ac = lancer(j, f"acuité{etiquette}", ["scripts/acuite.py", "--graphe", g], garder=1)
        m_ac = re.search(r"^  \S+ — (.+)$", sortie_ac, flags=re.M)
        if ok and m_ac:
            acuites.append(f"  acuité {g} : {m_ac.group(1)}")

    # Le skill /kg annonce le volume du graphe : il périme à chaque
    # régénération, donc il se resynchronise ici, une fois la carte refaite.
    # Puis le dépôt en archive la copie : le skill vit hors du dépôt, et sans
    # cette recopie son histoire ne serait consignée nulle part.
    sync = SKILL_KG / "synchroniser.py"
    if sync.exists() and skill_a_synchroniser(graphes):
        lancer(j, "skill /kg, volume annoncé", [str(sync)], garder=2)
        lancer(j, "skill /kg, archive au dépôt", ["scripts/archiver_skill.py"], garder=2)
    j.dire()

    # --- Chiffres et signaux
    for l in peu_couverts_lignes:
        acuites.append("  peu couverts " + l.strip())
    apres_fiches = fiches_du_graphe()
    nouvelles, modifiees = fiches_nouvelles(cfg.dossiers_du_graphe(seul or cfg.GRAPHE_PRINCIPAL))
    versees = compte_des_versees(nouvelles)
    gr = json.loads(cfg.chemins_du_graphe(seul or cfg.GRAPHE_PRINCIPAL)["graphe"].read_text(encoding="utf-8"))
    chiffres = {
        "fiches": str(apres_fiches),
        "noeuds": str(len(gr.get("nodes", []))),
        "aretes": str(len(gr.get("links", []))),
        "neuf": versees,
    }
    # Les graphes inclus, traités dans ce passage : volume chacun, pour le rapport et le courriel.
    autres: dict[str, dict[str, int]] = {}
    for g in graphes[:-1]:
        ch = cfg.chemins_du_graphe(g)
        if ch["graphe"].exists():
            d = json.loads(ch["graphe"].read_text(encoding="utf-8"))
            autres[g] = {
                "fiches": sum(
                    1
                    for dossier in cfg.dossiers_du_graphe(g)
                    for f in (RACINE / dossier).rglob("*.md")
                    if not f.name.startswith("_")
                ),
                "noeuds": len(d.get("nodes", [])),
                "aretes": len(d.get("links", [])),
            }
    j.dire("Bilan")
    j.dire(f"  {apres_fiches} fiches ({versees} depuis le dernier commit)")
    j.dire(f"  graphe : {chiffres['noeuds']} nœuds, {chiffres['aretes']} arêtes")
    # Le coût du passage, depuis le registre des appels au modèle (couts.py, 08.10.2026) ; le résumé par jour
    # (graphe/couts.json) part dans le commit.
    conso = couts.lignes(depuis=debut_passage)
    j.dire(f"  coût du passage : {couts.phrase(conso)}")
    try:
        couts.resumer()
    except Exception as e:  # noqa: BLE001
        j.dire(f"  (résumé des coûts non écrit : {type(e).__name__} {str(e)[:80]})")
    if couts.marque_en_cours():
        j.signal(
            f"limite d'usage atteinte pendant le passage ({couts.marque_en_cours()}) : les étapes au modèle ont été "
            "sautées ou abrégées, elles reprennent au passage suivant"
        )
    for l_ac in acuites:
        j.dire(l_ac)
    for g, v in autres.items():
        j.dire(f"  graphe {g} : {v['fiches']} fiches, {v['noeuds']} nœuds, {v['aretes']} arêtes")
    j.dire()

    # La couche 2 est-elle à jour ? Les empreintes sont tenues par
    # extraire_concepts.py, fiche par fiche, quand l'extraction a réussi : ce
    # qui reste différent ici a échoué ou dépassé le plafond du passage, et
    # sera repris au suivant. La routine ne les réécrit plus elle-même — c'est
    # ce qui faisait disparaître un retard après une seule semaine (27.09.2026).
    changees, denses_neuves = [], []
    for g in graphes:
        en_retard, neuves = retard_couche2(g)
        changees += en_retard
        denses_neuves += neuves
    j.dire("Étage assisté")
    if changees:
        j.signal(
            f"couche 2 en retard sur {len(changees)} fiche(s) dense(s), "
            f"dont {len(denses_neuves)} nouvelle(s) : l'extraction incrémentale a échoué ou "
            f"dépassé son plafond, elle reprendra au prochain passage"
        )
        for f in changees[:6]:
            j.dire(f"           {f}")
    else:
        j.dire("  ok      couche 2 à jour sur les fiches denses")
    # La liste de travail est calculée par l'étage assisté, pas répétée ici :
    # une seule définition de ce qui reste à faire, donc pas de divergence.
    try:
        from newskit.routine_assistee import liste_de_travail

        for t in liste_de_travail():
            (j.dire if t.startswith("  ") else j.signal)(t.replace("**", ""))
    except Exception as e:
        j.signal(
            f"liste de travail indisponible ({e}) ; voir state:{cfg.edition()['nom']}:current dans le cerveau coding"
        )
    j.dire()

    rapport_du_passage(
        j, "ok", chiffres, nouvelles, modifiees, autres, nouveautes, seul or cfg.GRAPHE_PRINCIPAL, versees_du_passage
    )
    for g, v in (autres or {}).items():
        noter_passage_inclus(g, v, seul or cfg.GRAPHE_PRINCIPAL)

    # --- Commit, seulement maintenant
    if args.sans_commit:
        j.dire("Commit ignoré (--sans-commit).")
        courriel(commiter=False)
        return 0
    modifies = fichiers_du_commit(
        seul or cfg.GRAPHE_PRINCIPAL,
        chemins_modifies(
            subprocess.run(
                ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
                cwd=RACINE,
                capture_output=True,
                text=True,
            ).stdout
        ),
    )
    if not modifies:
        print("Rien de neuf, aucun commit.")
        courriel()
        return 0
    message = (
        f"routine du graphe {seul or cfg.GRAPHE_PRINCIPAL} {time.strftime('%Y-%m-%d')}\n\n"
        f"{apres_fiches} fiches ({versees}), "
        f"graphe {chiffres['noeuds']} nœuds et {chiffres['aretes']} arêtes"
        + "".join(f" ; {g} {v['noeuds']} nœuds" for g, v in autres.items())
        + ".\n"
        "Contrat et fraîcheur vérifiés avant ce commit.\n"
        + (f"Couche 2 en retard sur {len(changees)} fiche(s) dense(s).\n" if changees else "")
    )
    for k in range(0, len(modifies), 500):  # les fichiers du graphe seulement, par lots (longueur de commande)
        subprocess.run(["git", "add", "-A", "--", *modifies[k : k + 500]], cwd=RACINE, check=False)
    r = subprocess.run(
        ["git", "commit", "-q", "-m", message],  # identité : git config du dépôt
        cwd=RACINE,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=RACINE, capture_output=True, text=True
        ).stdout.strip()
        print(f"Commit {sha}.")
        pousser()
    else:
        print(f"Commit impossible : {(r.stderr or '').strip()[:200]}")

    if changees:
        notifier(cfg.edition()["nom"], f"{versees} fiches. Couche 2 en retard sur {len(changees)}.")
    courriel()
    return 0


reglages.appliquer(__name__, globals())  # les réglages du média (reglages.py)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Combler le canon : donner une source réelle à ce qu'un modèle tient pour essentiel et que le graphe n'a pas.

`mesurer_avance.py` rend, par sujet, les notions du canon du modèle que le graphe ne porte pas.
Ce script en fait une liste de courses, chaque jour (décision du porteur, 04.10.2026). Le modèle
désigne ; il ne renseigne pas. Rien de ce qu'il dit n'entre dans le graphe : le contenu vient du
document récupéré, avec sa provenance.

    1. rapprocher   une notion « absente » est souvent là sous un autre nom (« Disney principled
                    BRDF » / « Disney BSDF »). Une question au modèle par passage ; un nom
                    rapproché est noté comme alias, et rien n'est collecté.
    2. classer      papier, dépôt, produit ou notion — dans la même réponse.
    3. résoudre     par une recherche, jamais par un identifiant donné de mémoire : arXiv par
                    titre, GitHub par nom, Wikipédia pour les produits et les notions.
    4. vérifier     le titre du papier commence par le nom ; le dépôt porte ce nom et a des étoiles ;
                    la page existe, n'est pas une homonymie et parle bien de la notion.
    5. inscrire     un papier devient un jalon du graphe (graphe/<nom>/canon.json, lu par
                    choisir_jalons.py) ; un dépôt et une page deviennent des fiches de la source
                    « Référence » du graphe (sources/canon/<nom>), extraites comme les autres.

Ce qui ne se résout pas en deux passages est rangé « à trancher » et paraît dans le rapport.
Le socle doit rester mince : PLAFOND notions par passage.

    python3 -m newskit.combler_canon --graphe <graphe>            # un passage
    python3 -m newskit.combler_canon --graphe <graphe> --etat     # alias, résolus, à trancher
    python3 -m newskit.combler_canon --graphe <graphe> --controle # ce qui serait fait, sans écrire
    python3 -m newskit.combler_canon --graphe <graphe> --plafond 0  # toutes les notions absentes, d'un coup

État : graphe/<nom>/canon.json. Limite connue : la complétude se mesure contre le modèle
interrogé ; on comble ce que ce modèle juge essentiel, pas un canon absolu.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit.commun import contact  # noqa: E402  (le pool « poli » d'OpenAlex)
from newskit import digerer_graphe as dg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import themes_emergents as te  # noqa: E402

PLAFOND = 20  # notions traitées par passage
ESSAIS = 2  # passages sans résolution avant « à trancher »
ETOILES_MIN = 300
MOTS_MIN, MOTS_MAX = 80, 1200
# Wikipédia demande un User-Agent qui dit comment joindre l'auteur ; sans lui, et sans pause, elle a rendu
# 113 refus (HTTP 429) au passage du 05.10.2026.
AGENT = f"{cfg.edition()['nom']}/1.0 (veille personnelle ; {cfg.edition()['site']} ; combler_canon.py)"  # bloc `edition:` (N2)
LOT_MODELE = 40  # notions classées par appel au modèle : 252 d'un coup restaient sans réponse
FAMILLES = ("papier", "depot", "produit", "notion")

SCHEMA = {
    "type": "object",
    "required": ["notions"],
    "properties": {
        "notions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "alias", "famille", "requete"],
                "properties": {
                    "i": {"type": "integer"},
                    "alias": {"type": ["integer", "null"]},
                    "famille": {"type": "string", "enum": list(FAMILLES)},
                    "requete": {"type": "string"},
                    "depot": {"type": ["string", "null"]},
                },
            },
        }
    },
}


def chemin_etat(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "canon.json"


def lire_etat(graphe: str) -> dict:
    p = chemin_etat(graphe)
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for k in ("alias", "papiers", "references", "depots", "a_trancher", "essais"):
        d.setdefault(k, {})
    return d


def papiers_du_canon(graphe: str) -> list[str]:
    """Les identifiants arXiv que le canon a fait entrer : jalons d'office (lu par choisir_jalons.py)."""
    return list(lire_etat(graphe)["papiers"])


def source_reference(graphe: str):
    return next(iter(cfg.toutes("Référence", graphe=graphe)), None)


class Refus(Exception):
    """Le service n'a pas répondu (limite de débit, réseau, erreur HTTP). Ce n'est pas une absence : le
    05.10.2026, GitHub bridé a fait noter « sans suite » Spleeter, Open-Unmix, Chromaprint et MSAF, qui
    seraient partis « à trancher » au passage suivant sans avoir jamais été cherchés."""


_JETON_GITHUB: list[str] = []


def jeton_github() -> str:
    """GITHUB_TOKEN, sinon celui de `gh` s'il est connecté : trente recherches par minute au lieu de dix."""
    if not _JETON_GITHUB:
        j = os.environ.get("GITHUB_TOKEN", "")
        if not j:
            try:
                j = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                j = ""
        _JETON_GITHUB.append(j)
    return _JETON_GITHUB[0]


def lire_url(url: str, accept: str = "application/json", delai: int = 30) -> str:
    """Le texte d'une adresse. Un « plus tard » (429, 503) est attendu puis redemandé, trois fois au plus ;
    ce qui échoue encore est un Refus. Wikipédia reçoit une pause d'une seconde avant chaque requête."""
    entetes = {"User-Agent": AGENT, "Accept": accept}
    if url.startswith("https://api.github.com/") and jeton_github():
        entetes["Authorization"] = f"Bearer {jeton_github()}"
    hote = urllib.parse.urlsplit(url).netloc
    for essai in range(4):
        if hote.endswith("wikipedia.org"):
            time.sleep(1)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=entetes), timeout=delai) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and essai < 3:
                attente = e.headers.get("Retry-After", "")
                time.sleep(min(int(attente), 120) if attente.isdigit() else 10 * (essai + 1) ** 2)
                continue
            raise Refus(f"{hote} : {e}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Refus(f"{hote} : {e}") from e
    raise Refus(f"{hote} : toujours refusé après trois reprises")


def plat(texte: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def nomme(terme: str, titre: str) -> bool:
    """Le terme figure dans le titre, mot pour mot (« Magic3D » dans « Magic3D: High-Resolution… »)."""
    t = plat(terme)
    return bool(t) and re.search(rf"(^| ){re.escape(t)}( |$)", plat(titre)) is not None


def intitule(terme: str, titre: str) -> bool:
    """Le titre du papier EST le terme, ou commence par lui suivi d'un deux-points (« Magic3D: High-Resolution… »).
    Qu'il le contienne ne suffit pas : le premier passage (04.10.2026) a pris « Stochastic Poisson Surface
    Reconstruction » pour la reconstruction de Poisson. Qu'il commence par lui non plus : le 05.10.2026, six
    dérivés et homonymes sont passés — « SigLIP-HD by… », « Grounding DINO 1.5: », « Mask2Former for Video… »,
    « 2DGS-R: », « LGM-Net: », « Product Quantization for Surface Soil Similarity ». Le papier qui fonde un
    nom l'annonce, puis s'explique après le deux-points."""
    t, ti = plat(terme), plat(titre)
    if not t:
        return False
    if ti == t:
        return True
    tete = titre.split(":", 1)[0] if ":" in titre else None
    return tete is not None and plat(tete) == t


def candidats(terme: str, etiquettes: list[str], combien: int = 6) -> list[str]:
    """Les étiquettes du graphe qui partagent le plus de racines avec le terme : alias possibles."""
    r = te.mots(terme, 2)
    notes = [(len(r & te.mots(e, 2)), e) for e in etiquettes]
    return [e for n, e in sorted((x for x in notes if x[0]), key=lambda x: (-x[0], len(x[1])))[:combien]]


# --- Résolution, par recherche -----------------------------------------------------------------


def titre_attendu(requete: str, titre: str) -> bool:
    """Le titre trouvé est celui que le modèle a donné (au sous-titre près)."""
    r, t = plat(requete), plat(titre)
    return len(r.split()) >= 5 and (t == r or t.startswith(r + " ") or r.startswith(t + " "))


def chercher_arxiv(terme: str, requete: str) -> tuple[str, str] | None:
    """(identifiant, titre) du papier, ou None. Quand le modèle a donné un vrai titre (cinq mots au moins), c'est
    lui qui fait foi : un sigle a des homonymes qui passent la règle du deux-points — « PGD: A Large-scale
    Professional Go Dataset » pour l'attaque PGD, « LIRA: Lifelong Image Restoration » pour l'attaque LiRA
    (05.10.2026). Sinon, le titre doit être le nom, ou le nom suivi d'un deux-points."""
    par_titre = len(plat(requete or "").split()) >= 5
    for q in (
        (f'ti:"{requete}"',)
        if par_titre
        else (f'ti:"{terme}"', f'all:"{requete}"' if requete and requete != terme else "")
    ):
        if not q:
            continue
        x = lire_url(
            "https://export.arxiv.org/api/query?max_results=25&search_query=" + urllib.parse.quote(q),
            accept="application/atom+xml",
        )
        time.sleep(3)  # courtoisie demandée par arXiv
        suivies = set(cfg.veille_arxiv().get("categories") or [])
        for e in re.findall(r"<entry>(.*?)</entry>", x, flags=re.S):
            m = re.search(r"<id>http://arxiv.org/abs/([^<v]+)", e)
            titre = " ".join((re.search(r"<title>(.*?)</title>", e, flags=re.S) or [0, ""])[1].split())
            # Dans les catégories de la veille : « MINC » (matières) avait ramené un papier de topologie
            # algébrique dont le titre commence par « Minc's » (04.10.2026).
            du_domaine = not suivies or bool(suivies & set(re.findall(r'<category term="([^"]+)"', e)))
            juste = titre_attendu(requete, titre) if par_titre else intitule(terme, titre)
            if m and du_domaine and juste:
                return m.group(1), titre
    return None


def chercher_github(terme: str, officiel: str | None = None) -> dict | None:
    """Le dépôt de ce projet, au-dessus d'ETOILES_MIN, ou None. D'abord le dépôt officiel « propriétaire/nom »
    que le modèle a désigné, vérifié sur GitHub ; à défaut, le dépôt qui porte exactement ce nom. Plus de
    dépôt suffixé : la règle a pris « angristan/wireguard-install » pour WireGuard (05.10.2026)."""
    if officiel and re.fullmatch(r"[\w.-]+/[\w.-]+", officiel.strip()):
        try:
            it = json.loads(
                lire_url("https://api.github.com/repos/" + officiel.strip(), accept="application/vnd.github+json")
            )
        except Refus as e:
            if "404" not in str(e):
                raise
            it = None
        time.sleep(1)
        if (
            it
            and it.get("stargazers_count", 0) >= ETOILES_MIN
            and plat(terme).replace(" ", "") in plat(it["full_name"]).replace(" ", "")
        ):
            return it
    d = json.loads(
        lire_url(
            "https://api.github.com/search/repositories?sort=stars&order=desc&per_page=5&q="
            + urllib.parse.quote(f"{terme} in:name"),
            accept="application/vnd.github+json",
        )
    )
    time.sleep(2.5 if jeton_github() else 7)  # trente recherches par minute avec jeton, dix sans
    t = plat(terme).replace(" ", "")
    for it in d.get("items", []):
        if it["stargazers_count"] >= ETOILES_MIN and plat(it["name"]).replace(" ", "") == t:
            return it
    return None


OPENALEX = "https://api.openalex.org/works"


def chercher_openalex(terme: str, requete: str) -> dict | None:
    """{titre, url, texte, date, auteur, publication} d'un papier HORS arXiv, par son titre, ou None.

    Les fondations de bien des domaines sont des articles de revue ou de conférence sans prépublication
    (Longuet-Higgins et Lee 1984, Janata 2012, Covers80) : arXiv ne les a pas, le canon les laissait « sans
    suite » (05.10.2026). OpenAlex les recense avec leur résumé. Plus strict qu'arXiv : le titre doit être
    exactement le titre officiel que le modèle a donné ; et un résumé assez long pour faire une fiche — une
    notice sans texte n'apporte rien au graphe."""
    from newskit.collecte_openalex import resume  # noqa: PLC0415

    d = json.loads(
        lire_url(
            OPENALEX
            + "?"
            + urllib.parse.urlencode(
                {
                    "search": requete or terme,
                    "per_page": 10,
                    "mailto": contact(),
                    "select": "id,doi,title,publication_year,publication_date,authorships,primary_location,"
                    "abstract_inverted_index,cited_by_count",
                }
            )
        )
    )
    time.sleep(1)
    r = plat(requete or "")
    bons = []
    for w in d.get("results", []):
        titre = " ".join(str(w.get("title") or "").split())
        tt = plat(titre)
        if not tt:
            continue
        # Le titre exact donné par le modèle, pas « Nom : … » : dans les revues, « Space Syntax: Some
        # Inconsistencies » est une critique, pas la fondation (05.10.2026).
        juste = len(r) >= 12 and (
            tt == r or (len(r.split()) >= 5 and (tt.startswith(r + " ") or r.startswith(tt + " ")))
        )
        texte = resume(w.get("abstract_inverted_index"), MOTS_MAX)
        if juste and len(texte.split()) >= MOTS_MIN:
            bons.append((w.get("cited_by_count") or 0, w, titre, texte))
    if not bons:
        return None
    _, w, titre, texte = max(bons, key=lambda x: x[0])  # le plus cité, à titre égal
    auteurs = ", ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:6] if a.get("author"))
    revue = (((w.get("primary_location") or {}).get("source")) or {}).get("display_name") or "OpenAlex"
    url = w.get("doi") or w.get("id") or ""
    return {
        "titre": titre,
        "url": url,
        "texte": texte,
        "date": str(w.get("publication_date") or f"{w.get('publication_year')}-01-01")[:10],
        "auteur": auteurs,
        "publication": revue,
        "mention": f"Source : {url} ({revue}, {w.get('publication_year')}) — résumé par OpenAlex",
        "origine": f"OpenAlex, papier hors arXiv du canon « {terme} »",
    }


def chercher_wikipedia(terme: str, requete: str) -> dict | None:
    """{titre, url, texte, date} de la page qui traite la notion, ou None (absente, homonymie, hors sujet)."""
    api = "https://en.wikipedia.org/w/api.php?format=json&"

    def lire_page(titre: str) -> tuple[dict, bool]:
        """(page, venue d'une redirection) ; une page d'homonymie ou trop courte rend {}."""
        p = json.loads(
            lire_url(
                api
                + urllib.parse.urlencode(
                    {
                        "action": "query",
                        "prop": "extracts|pageprops|info",
                        "explaintext": 1,
                        "exsectionformat": "plain",
                        "redirects": 1,
                        "inprop": "url",
                        "titles": titre,
                    }
                )
            )
        )
        q = p.get("query", {})
        page = next(iter(q.get("pages", {}).values()), {})
        texte = (page.get("extract") or "").strip()
        if "missing" in page or "disambiguation" in (page.get("pageprops") or {}) or len(texte.split()) < MOTS_MIN:
            return {}, False
        return {
            "titre": page["title"],
            "url": page.get("fullurl", ""),
            "texte": texte,
            "date": str(page.get("touched") or "")[:10] or time.strftime("%Y-%m-%d"),
        }, bool(q.get("redirects")) and not any(r.get("tofragment") for r in q["redirects"])

    # Un sigle que Wikipédia redirige vers sa page est cette page : « STFT » → Short-time Fourier transform,
    # rédaction humaine et non rapprochement de mots (05.10.2026). Pas une redirection vers une section :
    # « img2img » mène à une section de Stable Diffusion, qui n'est pas une page sur la notion.
    # Une redirection ne suffit pas seule : « Windsurf » (l'éditeur) mène à Windsurfing, un sigle à « Water »
    # (05.10.2026). La page d'arrivée doit porter le nom officiel que le modèle a donné.
    # Quand le modèle a donné un nom plus précis que le terme, c'est lui qui fait foi : la page du terme nu est
    # souvent un homonyme — « Ampere » l'unité, « Turing » le mathématicien, au lieu des architectures de
    # GPU « Ampere (microarchitecture) », « Turing (microarchitecture) » (06.10.2026).
    precise = bool(requete) and plat(requete) != plat(terme)
    if precise:
        page, _ = lire_page(requete)
        if page and plat(page["titre"]) == plat(requete):
            return page
    else:
        page, redirige = lire_page(terme)
        if page and (nomme(terme, page["titre"]) or (redirige and requete and plat(requete) == plat(page["titre"]))):
            return page
    s = json.loads(
        lire_url(
            api
            + urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": requete or terme, "srlimit": 3})
        )
    )
    for hit in s.get("query", {}).get("search", []):
        page, _ = lire_page(hit["title"])
        # Le titre de la page doit nommer la notion, ou être exactement son nom officiel. Ses racines dans le
        # chapeau ne suffisent pas : « img2img » avait ramené Riffusion, « intrinsic decomposition » la
        # transformée de Hilbert-Huang.
        if page and (
            (nomme(terme, page["titre"]) and not precise) or (requete and plat(requete) == plat(page["titre"]))
        ):
            return page
    return None


# --- Écriture ----------------------------------------------------------------------------------


def ecrire_reference(src, terme: str, page: dict) -> str:
    mots = page["texte"].split()
    corps = "\n".join(
        [
            f"# {page['titre']}",
            "",
            " ".join(mots[:MOTS_MAX]) if len(mots) > MOTS_MAX else page["texte"],
            "",
            page.get("mention") or f"Source : {page['url']} (Wikipédia, CC BY-SA)",
            "",
        ]
    )
    champs = {
        "title": page["titre"],
        "subtitle": "",
        "author": page.get("auteur") or "Wikipedia contributors",
        "date": page["date"],
        "url": page["url"],
        "plateforme": "Référence",
        "publication": page.get("publication") or "Wikipedia",
        "complet": len(mots) <= MOTS_MAX,
        "collected": time.strftime("%Y-%m-%d"),
        "source": page.get("origine") or f"Wikipédia, notion du canon « {terme} »",
    }
    chemin = src.chemin / f"{page['date']}_{te.cle_de(page['titre'])[:70] or 'reference'}.md"
    src.chemin.mkdir(parents=True, exist_ok=True)
    try:
        fiche.ecrire(chemin, champs, corps)
    except fiche.IdentiteDejaVue as deja:  # la page est déjà une fiche du graphe : la notion est couverte
        return str(deja.existante.relative_to(Path(RACINE).resolve()))
    return str(chemin.relative_to(RACINE))


def ecrire_depot(src, terme: str, it: dict) -> str:
    from newskit import collecte_depots as cd  # noqa: PLC0415

    lisezmoi = cd.readme(f"https://raw.githubusercontent.com/{it['full_name']}/HEAD/README.md")
    sujets = ", ".join(it.get("topics") or [])
    corps = "\n".join(
        [
            f"# {it['full_name']}",
            "",
            f"{it.get('description') or ''}",
            "",
            f"Étoiles : {it['stargazers_count']} · langage : {it.get('language') or '?'} · sujets : "
            f"{sujets or '—'} · créé le {it['created_at'][:10]}, mis à jour le {it['pushed_at'][:10]}",
            "",
            lisezmoi or "*Pas de README lisible : la fiche se réduit à la description du dépôt.*",
            "",
            f"Dépôt : {it['html_url']}",
            "",
        ]
    )
    champs = {
        "title": it["full_name"],
        "subtitle": "",
        "author": it["owner"]["login"],
        "date": it["created_at"][:10],
        "url": it["html_url"],
        "plateforme": "GitHub",
        "publication": src.nom,
        "complet": bool(lisezmoi),
        "collected": time.strftime("%Y-%m-%d"),
        "source": f"GitHub, notion du canon « {terme} »",
        "etoiles": int(it["stargazers_count"]),
        "langage": it.get("language") or "",
        "sujets": sujets,
    }
    chemin = src.chemin / f"{it['created_at'][:10]}_{cd.slug(it['full_name'])}.md"
    src.chemin.mkdir(parents=True, exist_ok=True)
    try:
        fiche.ecrire(chemin, champs, corps)
    except fiche.IdentiteDejaVue as deja:  # la page est déjà une fiche du graphe : la notion est couverte
        return str(deja.existante.relative_to(Path(RACINE).resolve()))
    return str(chemin.relative_to(RACINE))


# --- Passage -----------------------------------------------------------------------------------


def file_du_canon(sujets: list[dict], justes: dict[str, float]) -> list[str]:
    """Les notions absentes dans l'ordre où les chercher. Avec la mesure par thème, les sujets faibles d'abord, chacun en
    entier (09.10.2026). Sans elle (un graphe qui naît), les sujets à tour de rôle : le 22.10.2026, la file d'un graphe de chansons
    alignait les notions sujet après sujet, et quatre passages n'ont pas atteint le cinquième sujet."""
    listes = [
        list(s.get("absentes", [])) + list((s.get("critique") or {}).get("absentes", []))
        for s in (sorted(sujets, key=lambda s: justes.get(s.get("sujet"), 0.75)) if justes else sujets)
    ]
    if justes:
        ordre = [n for l in listes for n in l]
    else:
        ordre = [l[k] for k in range(max((len(l) for l in listes), default=0)) for l in listes if k < len(l)]
    return list(dict.fromkeys(ordre))


def a_traiter(graphe: str, etat: dict, plafond: int = PLAFOND) -> list[str]:
    p = cfg.chemins_du_graphe(graphe)["base"] / "avance.json"
    if not p.exists():
        return []
    faits = (
        set(etat["alias"])
        | set(etat["references"])
        | set(etat["depots"])
        | set(etat["papiers"].values())
        | set(etat["a_trancher"])
    )
    p_h = cfg.chemins_du_graphe(graphe)["base"] / "themes_banc_historique.jsonl"
    justes: dict[str, float] = {}
    if p_h.exists():
        try:
            m = json.loads(p_h.read_text(encoding="utf-8").splitlines()[-1])
            justes = {th: (v.get("ensemble") or {}).get("juste", 0.75) for th, v in (m.get("par_theme") or {}).items()}
        except (json.JSONDecodeError, IndexError):
            justes = {}
    reste = [
        n for n in file_du_canon(json.loads(p.read_text(encoding="utf-8")).get("sujets", []), justes) if n not in faits
    ]
    return reste[:plafond] if plafond else reste


def passage(graphe: str, controle: bool = False, plafond: int = PLAFOND) -> int:
    te.POSTE, te.GRAPHE_COURANT = "canon", graphe  # registre des coûts
    etat = lire_etat(graphe)
    termes = a_traiter(graphe, etat, plafond)
    if not termes:
        print(f"  {graphe} : rien à combler (pas de mesure, ou canon déjà traité)")
        return 0
    G = te.charger(graphe)
    etiquettes = [str(G.nodes[n].get("label") or n) for n in G if dg._type(G, n) != "auteur"] if G is not None else []
    cands = [candidats(t, etiquettes) for t in termes]
    decl = cfg.graphe_declare(graphe)
    juges: dict[int, dict] = {}
    for debut in range(0, len(termes), LOT_MODELE):
        lot, cands_lot = termes[debut : debut + LOT_MODELE], cands[debut : debut + LOT_MODELE]
        rep = te.appeler_claude(
            f"Domaine : « {decl['libelle']} ». Pour chaque notion ci-dessous, trois réponses.\n"
            "`alias` : si l'un des noms proposés désigne EXACTEMENT la même chose (même méthode, même produit, même "
            "format — pas un parent ni une variante), son indice ; sinon null.\n"
            "`famille` : « papier » (méthode ou modèle issu d'un article de recherche), « depot » (projet ou bibliothèque "
            "à code ouvert), « produit » (logiciel ou service commercial), « notion » (concept général).\n"
            "`requete` : les mots à chercher pour trouver sa référence — le nom officiel complet, en anglais ; pour un "
            "papier, le titre exact de l'article qui l'a introduit.\n"
            "`depot` : pour un projet à code ouvert, son dépôt GitHub OFFICIEL « propriétaire/nom » (pas un script "
            "d'installation ni un dérivé) ; sinon null.\n\n"
            + "\n".join(
                f"  {i}. {t}  — noms proposés : " + (" ; ".join(f"[{k}] {c}" for k, c in enumerate(cs)) or "aucun")
                for i, (t, cs) in enumerate(zip(lot, cands_lot))
            )
            + '\n\nRéponds en JSON : {"notions": [{"i": <indice>, "alias": <indice ou null>, "famille": "…", "requete": "…", "depot": "…"}, …]}.',
            SCHEMA,
        )
        if not rep:
            print(
                f"  {graphe} : le modèle n'a pas répondu pour les notions {debut + 1} à {debut + len(lot)}, "
                f"elles attendent le passage suivant",
                file=sys.stderr,
            )
            continue
        for x in rep.get("notions", []):
            if isinstance(x.get("i"), int) and 0 <= x["i"] < len(lot):
                juges[debut + x["i"]] = {**x, "i": debut + x["i"]}
    if not juges:
        print(f"  {graphe} : le modèle n'a pas répondu, rien n'est fait", file=sys.stderr)
        return 1
    src = source_reference(graphe)
    a_arxiv = any(v["graphe"] == graphe for v in cfg.veilles_arxiv())
    bilan = {"alias": 0, "papier": 0, "hors_arxiv": 0, "depot": 0, "reference": 0, "sans": 0, "refus": 0}
    for i, terme in enumerate(termes):
        x = juges.get(i)
        if not x:
            continue
        a = x.get("alias")
        if isinstance(a, int) and 0 <= a < len(cands[i]):
            etat["alias"][terme] = cands[i][a]
            bilan["alias"] += 1
            print(f"    alias      {terme}  =  {cands[i][a]}")
            continue
        if controle:
            print(f"    à chercher {terme}  ({x['famille']} ; « {x['requete']} »)")
            continue
        famille, requete, fait, refus = x["famille"], x.get("requete") or terme, False, []
        if famille == "papier" and a_arxiv:
            try:
                r = chercher_arxiv(terme, requete)
            except Refus as e:
                r = None
                refus.append(str(e))
            if r:
                etat["papiers"][r[0]] = terme
                bilan["papier"] += 1
                fait = True
                print(f"    papier     {terme}  →  arXiv {r[0]}  {r[1][:70]}")
        if not fait and famille == "papier" and src:
            try:
                page = chercher_openalex(terme, requete)
            except Refus as e:
                page = None
                refus.append(str(e))
            if page:
                etat["references"][terme] = ecrire_reference(src, terme, page)
                bilan["hors_arxiv"] += 1
                fait = True
                print(f"    hors arXiv {terme}  →  {page['titre'][:70]} ({page['publication'][:30]})")
        if not fait and famille == "depot" and src:
            try:
                it = chercher_github(terme, x.get("depot"))
            except Refus as e:
                it = None
                refus.append(str(e))
            if it:
                etat["depots"][terme] = ecrire_depot(src, terme, it)
                bilan["depot"] += 1
                fait = True
                print(f"    dépôt      {terme}  →  {it['full_name']} ({it['stargazers_count']} étoiles)")
        if not fait and src:
            try:
                page = chercher_wikipedia(terme, requete)
            except Refus as e:
                page = None
                refus.append(str(e))
            if page:
                etat["references"][terme] = ecrire_reference(src, terme, page)
                bilan["reference"] += 1
                fait = True
                print(f"    référence  {terme}  →  {page['titre']}")
        if not fait and refus:
            # Un service qui refuse n'a rien dit de la notion : pas d'essai compté, elle revient au passage suivant.
            bilan["refus"] += 1
            print(f"    REFUSÉ     {terme}  ({famille}) — {refus[0][:90]}")
            continue
        if not fait:
            etat["essais"][terme] = etat["essais"].get(terme, 0) + 1
            bilan["sans"] += 1
            if etat["essais"][terme] >= ESSAIS:
                etat["a_trancher"][terme] = f"{famille} : aucune source vérifiable trouvée en {ESSAIS} passages"
            print(f"    sans suite {terme}  ({famille})")
        if not controle:  # au fil de l'eau : une notion résolue est écrite avant que la suivante soit cherchée
            chemin_etat(graphe).write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    if not controle:
        chemin_etat(graphe).write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
        if bilan["papier"]:
            # Les papiers retenus deviennent des jalons : leur fiche est écrite et la liste recalculée.
            subprocess.run(
                [sys.executable, str(CODE / "choisir_jalons.py"), "--graphe", graphe],
                cwd=RACINE,
                capture_output=True,
                text=True,
                timeout=900,
            )
    print(
        f"  {graphe} : {len(termes)} notion(s) du canon traitées — {bilan['alias']} déjà là sous un autre nom, "
        f"{bilan['papier']} papier(s), {bilan['hors_arxiv']} hors arXiv, {bilan['depot']} dépôt(s), {bilan['reference']} page(s) de référence, "
        f"{bilan['sans']} sans suite ; {len(etat['a_trancher'])} à trancher"
        + (f" ; {bilan['refus']} REFUSÉE(S) par un service, reprise(s) au passage suivant" if bilan["refus"] else "")
    )
    # Un refus n'est pas une absence : le code de sortie le dit, comme pour les collecteurs.
    return 1 if bilan["refus"] else 0


def afficher(graphe: str) -> None:
    e = lire_etat(graphe)
    print(
        f"  {graphe} : {len(e['alias'])} alias, {len(e['papiers'])} papier(s), {len(e['depots'])} dépôt(s), "
        f"{len(e['references'])} référence(s), {len(e['a_trancher'])} à trancher"
    )
    for t, motif in e["a_trancher"].items():
        print(f"    à trancher : {t} — {motif}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--etat", action="store_true")
    ap.add_argument("--controle", action="store_true", help="rapprocher et classer, sans rien chercher ni écrire")
    ap.add_argument(
        "--plafond",
        type=int,
        default=PLAFOND,
        help=f"notions traitées par passage (défaut {PLAFOND} ; 0 : toutes, pour un passage à la main)",
    )
    args = ap.parse_args()
    graphe = cfg.nom_actuel(args.graphe)
    if args.etat:
        afficher(graphe)
        return 0
    return passage(graphe, args.controle, args.plafond)


if __name__ == "__main__":
    sys.exit(main())

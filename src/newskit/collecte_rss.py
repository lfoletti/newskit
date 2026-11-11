#!/usr/bin/env python3
"""Collecte des sources à flux RSS ou Atom déclarées au registre.

Pourquoi ce collecteur existe. Les thèmes image, plans d'architecture et
matières ne vivent pas sur Substack : ils vivent sur des blogs de studio, de
laboratoire et de conférence — SIGGRAPH, 80 Level, Parametric Architecture, les
blogs de Meta, d'Apple ou de DeepMind. Tous exposent un flux, c'est-à-dire une
interface conçue pour être lue par une machine, stable et sans anti-robot. Le
corpus n'en tirait rien faute de collecteur.

Ce que le collecteur retient, et ce qu'il avoue. Un flux complet porte le texte
entier de l'article dans `content:encoded` : la fiche est alors complète. Les
autres varient énormément, et c'est mesuré plutôt que supposé — The Gradient
rend 4 000 mots par entrée, Parametric Architecture 1 082, Apple 80, tandis que
DeepMind et Hugging Face n'en rendent **aucun**. Pour ces derniers, la source
déclare `page: true` et le collecteur lit la page de l'article, comme le fait
déjà le collecteur LinkedIn sur des pages publiques : 1 984 mots pour DeepMind,
2 142 pour Hugging Face. Une entrée qui reste sous le seuil après cela est un
entrefilet : elle est comptée et signalée, non ingérée. Une fiche de quarante
mots n'est pas un document, et le corpus n'a pas à s'en encombrer.

Usage :
    python3 -m newskit.collecte_rss [--source <cle>] [--limite N]
Sortie : sources/<dossier>/<date>_<slug>.md + _manifest.json
"""

from __future__ import annotations

import argparse
import gzip
import html as htmlmod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit.commun import ENTETES_NAVIGATEUR, agent_robot, html_to_md  # noqa: E402
from newskit import sante_sources as ss  # noqa: E402
from newskit import reglages  # noqa: E402 — les réglages du média

MOTS_MINIMUM = 120  # sous ce seuil, l'entrée est un entrefilet et n'entre pas
MOTS_TEXTE = 300  # au-dessus, et sans marque de coupure, c'est un texte entier
# Marques d'un flux qui coupe son texte. Leur absence ne prouve rien à elle
# seule, d'où le seuil de mots qui l'accompagne.
COUPURES = re.compile(r"(lire la suite|read more|continue reading|\[…\]|\[\.\.\.\])\s*$", re.I)
PAUSE_PAGE = 1.0  # secondes entre deux pages lues chez le même hôte

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}


def _decompresser(octets: bytes, encodage: str | None) -> bytes:
    """Rend les octets en clair, que le serveur ait compressé ou non.

    Pourquoi c'est nécessaire. Google sert le flux DeepMind compressé en gzip
    tout en l'annonçant `text/xml`, sans qu'on l'ait demandé par
    `Accept-Encoding` — et `urllib`, contrairement à `curl`, ne décompresse
    jamais de lui-même. Le parseur recevait alors l'en-tête gzip comme premier
    octet et rendait « not well-formed (invalid token): line 1, column 0 »,
    ce qui se lisait à tort comme un flux cassé chez l'éditeur.

    On se fie aux octets plutôt qu'à l'en-tête : le nombre magique est sûr,
    l'en-tête ment ici.
    """
    if octets[:2] == b"\x1f\x8b":
        return gzip.decompress(octets)
    if (encodage or "").lower() == "deflate":
        return zlib.decompress(octets, -zlib.MAX_WBITS)
    return octets


# La collecte horaire (03.11.2026) relit les flux toutes les heures : chaque flux est demandé sous condition (ETag,
# Last-Modified), un 304 rend un flux vide, sans charger l'éditeur. Les pages d'articles ne sont pas concernées.
VALIDATEURS = RACINE / "cache" / "flux_validateurs.json"
FLUX_VIDE = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
_validateurs: dict | None = None


def _valid() -> dict:
    global _validateurs
    if _validateurs is None:
        try:
            _validateurs = json.loads(VALIDATEURS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _validateurs = {}
    return _validateurs


def _garder_validateurs(url: str, entetes) -> None:
    v = {k: entetes.get(k) for k in ("ETag", "Last-Modified") if entetes.get(k)}
    if v:
        _valid()[url] = v
        try:
            VALIDATEURS.parent.mkdir(parents=True, exist_ok=True)
            VALIDATEURS.write_text(json.dumps(_valid(), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


HUBS = RACINE / "cache" / "flux_hubs.json"


def _noter_hub(url: str, octets: bytes, lien: str) -> None:
    """Le hub WebSub qu'un flux déclare (<link rel="hub">, ou l'en-tête Link), pour mesurer ce que le push couvrirait."""
    m = re.search(rb'<(?:atom:)?link[^>]+rel=["\']hub["\'][^>]*>', octets[:20000]) or None
    hub = ""
    if m:
        h = re.search(rb'href=["\']([^"\']+)', m.group(0))
        hub = h.group(1).decode("utf-8", "replace") if h else ""
    elif 'rel="hub"' in lien:
        hub = lien.split(">")[0].lstrip("<")
    try:
        d = json.loads(HUBS.read_text(encoding="utf-8")) if HUBS.exists() else {}
        d[url] = hub
        HUBS.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _maintenant() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _ouvrir(url: str, req, flux: bool = False) -> bytes:
    with urllib.request.urlopen(req, timeout=45) as r:
        octets = _decompresser(r.read(), r.headers.get("Content-Encoding"))
        if flux:
            _garder_validateurs(url, r.headers)
            _noter_hub(url, octets, r.headers.get("Link") or "")
        return octets


def _imiter(url: str) -> bytes:
    """La réponse lue avec l'empreinte d'un navigateur par accesspath (le lecteur web), ou b"" ."""
    from newskit import lecture_web  # noqa: PLC0415

    if not lecture_web.accesspath_py().exists():
        print(f"  ~~ imitation impossible : accesspath introuvable ({lecture_web.accesspath_py()})", file=sys.stderr)
        return b""
    return lecture_web.imiter(url).encode("utf-8")


# L'audit d'accès d'un hôte qui échoue (sante_sources.py) : sap probe d'accesspath, sans navigateur.
_AUDIT = (
    "import asyncio, json, sys\nfrom accesspath.probe import probe\n"
    "r = asyncio.run(probe(sys.argv[1])).to_dict()\nt = r.get('tier1_discovery') or {}\n"
    "sys.stdout.write('\\nSAP ' + json.dumps({'tier': r.get('chosen_tier'), 'tls': (r.get('tls_fingerprint') or {})"
    ".get('verdict'), 'rationale': r.get('rationale'), 'rss': [f.get('endpoint') for f in t.get('rss_feeds') or []"
    " if f.get('status') == 200]}))\n"
)


def _auditer(url: str) -> dict:
    import subprocess  # noqa: PLC0415
    from newskit import lecture_web  # noqa: PLC0415

    py = lecture_web.accesspath_py()
    if not py.exists():
        print(f"  ~~ audit sap impossible : accesspath introuvable ({py})", file=sys.stderr)
        return {}
    try:
        r = subprocess.run(
            [str(py), "-c", _AUDIT, url],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(py.parents[2]),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  ~~ audit sap impossible : {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
        return {}
    lignes = [l for l in r.stdout.splitlines() if l.startswith("SAP ")]
    try:
        if lignes:
            return json.loads(lignes[-1][4:])
    except ValueError:
        pass
    print(
        f"  ~~ audit sap impossible : code {r.returncode}, {(r.stderr.strip().splitlines() or [''])[-1][:150]}",
        file=sys.stderr,
    )
    return {}


def _lire_pour_reparer(url: str, imiter: bool) -> bytes:
    if imiter:
        return _imiter(url)
    try:
        return _ouvrir(url, urllib.request.Request(url, headers={"User-Agent": agent_robot()}))
    except Exception:  # noqa: BLE001
        return b""


def _utilisable(octets: bytes, flux: bool) -> bool:
    if not flux:
        return len(octets) >= 1000
    if not octets.strip():
        return False
    try:
        return bool(entrees(octets))
    except ET.ParseError:
        return bool(entrees_tolerantes(octets))


def _alerter(objet: str, corps: str) -> None:
    try:
        from newskit import notifier_courriel as nc  # noqa: PLC0415

        nc.envoyer(objet, corps)
    except Exception as e:  # noqa: BLE001 — une alerte manquée ne fait pas tomber la collecte
        print(f"  ~~ alerte non envoyée : {type(e).__name__}", file=sys.stderr)


def _robots_exclut_ia(url: str) -> bool:
    from newskit import lecture_web  # noqa: PLC0415

    return lecture_web.robots_exclut_ia(url)


def _echec(url: str, flux: bool) -> None:
    ss.echec(
        url,
        _maintenant(),
        flux=flux,
        auditer=_auditer,
        lire=_lire_pour_reparer,
        robots_exclut_ia=_robots_exclut_ia,
        alerter=_alerter,
        utilisable=_utilisable,
    )


def recuperer(url: str, essais: int = 3, navigateur: bool = False, accept: str | None = None) -> bytes | None:
    """Les octets d'une adresse, ou None. L'hôte au repos n'est pas lu ; l'hôte réparé par imitation se lit par
    imitation ; un flux remplacé se lit à sa nouvelle adresse (sante_sources.py)."""
    flux = accept is None
    repos = ss.en_repos(url, flux, _maintenant())
    if repos:
        print(f"  !! {url} : au repos jusqu'à {repos} (audit sap sans voie)", file=sys.stderr)
        return None
    lue = ss.adresse(url)
    if ss.imite(ss.hote(lue)):
        octets = _imiter(lue)
        if octets:
            ss.succes(lue, _maintenant(), flux)
            return octets
        print(f"  !! {lue} : imitation sans réponse", file=sys.stderr)
        _echec(lue, flux)
        return None
    for i in range(essais):
        try:
            req = urllib.request.Request(
                lue,
                headers={
                    **ENTETES_NAVIGATEUR,
                    "Accept": accept or "application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                if navigateur
                else {
                    "User-Agent": agent_robot(),
                    "Accept": accept or "application/rss+xml,application/atom+xml,application/xml",
                },
            )
            if flux:
                v = _valid().get(lue) or {}
                if v.get("ETag"):
                    req.add_header("If-None-Match", v["ETag"])
                if v.get("Last-Modified"):
                    req.add_header("If-Modified-Since", v["Last-Modified"])
            octets = _ouvrir(lue, req, flux)
            ss.succes(lue, _maintenant(), flux)
            return octets
        except urllib.error.HTTPError as e:
            if flux and e.code == 304:
                ss.succes(lue, _maintenant(), flux)
                return FLUX_VIDE
            if i == essais - 1:
                print(f"  !! {lue} : {e}", file=sys.stderr)
                _echec(lue, flux)
                return None
            time.sleep(3 * (i + 1))
        except Exception as e:
            if i == essais - 1:
                print(f"  !! {lue} : {e}", file=sys.stderr)
                _echec(lue, flux)
                return None
            time.sleep(3 * (i + 1))
    return None


# Ce qu'une page rend à la place de son texte quand elle est réservée aux
# membres (Ghost, 27.09.2026, aisecret.us) : un texte court, et l'un de ces
# appels. Sans session, la fiche serait un entrefilet ; avec une session qui
# ne passe plus, c'est une anomalie à dire, pas une semaine calme.
MUR_MEMBRES = re.compile(
    r"(?:this (?:story|post|deep dive|article|issue) is for (?:paid |premium )?(?:members|subscribers)"
    r"|create a free account to continue|become a (?:paid |premium )?member|members only"
    r"|upgrade to (?:paid|premium|continue)|subscribe to continue|for paid subscribers"
    r"|this (?:content|post) is (?:for|available to) (?:paid|paying|premium))",
    re.I,
)


def mur_membres(texte: str) -> bool:
    return bool(MUR_MEMBRES.search(texte))


def date_de_publication(html: str) -> str:
    """La date de publication que la page déclare elle-même, AAAA-MM-JJ, sinon "" : JSON-LD datePublished, puis les
    balises méta de publication (article:published_time, citation_publication_date…), puis le <time datetime> de
    l'article. Jamais une date lue dans le texte : « applicable since 12 September 2025 » datait l'article de Heuking du
    1er septembre 2026, « 1 August 2024 » celui de Gleiss Lutz du 23 février 2026 (03.11.2026)."""
    for m in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            d = json.loads(m)
        except json.JSONDecodeError:
            continue
        objets = d.get("@graph", [d]) if isinstance(d, dict) else d if isinstance(d, list) else []
        for x in objets:
            if isinstance(x, dict) and re.match(r"\d{4}-\d{2}-\d{2}", str(x.get("datePublished") or "")):
                return str(x["datePublished"])[:10]
    for nom in (
        "article:published_time",
        "citation_publication_date",
        "datePublished",
        "citation_date",
        "DC.date.issued",
        "dcterms.date",
    ):
        m = re.search(
            rf'<meta[^>]+(?:name|property|itemprop)="{re.escape(nom)}"[^>]+content="([^"]+)"', html, re.I
        ) or re.search(rf'<meta[^>]+content="([^"]+)"[^>]+(?:name|property|itemprop)="{re.escape(nom)}"', html, re.I)
        if m and re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", m.group(1)):
            return m.group(1)[:10].replace("/", "-")
    # Le <time datetime> de l'article : celui dont la classe le dit (« article-intro__date », « entry-date »,
    # « post-date »…) ; sinon celui de l'article quand la page n'en a qu'un ; sinon le seul <time> de publication. Plusieurs dates sans celle de l'article : les
    # cartes « à lire aussi » (DCD, 03.11.2026 : 2026-11-02 au lieu du 14.02.2025), aucune.
    temps = re.findall(r"<time\b([^>]*)>", html, re.I)

    def jour(attrs):
        m = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', attrs)
        return m.group(1) if m else ""

    for attrs in temps:
        if jour(attrs) and re.search(r'class="[^"]*(?:article|entry|post|published|byline)[^"]*"', attrs, re.I):
            return jour(attrs)
    arts = re.findall(r"<article\b.*?</article>", html, re.S | re.I)
    if len(arts) == 1:
        m = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})', arts[0], re.I)
        if m:
            return m.group(1)
    pub = {
        jour(a) for a in temps if jour(a) and not re.search(r'itemprop="(?:startDate|endDate|dateModified)"', a, re.I)
    }
    return pub.pop() if len(pub) == 1 else ""


def date_de_l_adresse(url: str) -> str:
    """La date écrite dans l'adresse d'une page (/2026/10/28/, /2026-10-28-f853 des incidents de l'OCDE.AI,
    /20261102-…, /04042023-… du PFPDT), sinon ""."""
    m = re.search(r"/((?:19|20)\d\d)[/-](\d\d)[/-](\d\d)(?=[/-]|\b)", url) or re.search(
        r"/((?:19|20)\d\d)(\d\d)(\d\d)(?=\D)", url
    )
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # JJMMAAAA en tête du dernier segment : le PFPDT (edoeb.admin.ch/fr/04042023-utilisation-de-chatgpt…, 03.11.2026).
    m = re.search(r"/(\d\d)(\d\d)((?:19|20)\d\d)-[^/]*$", url)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def date_de_l_entree(date_du_flux: str, meta: dict, url: str) -> str:
    """La date d'une entrée lue sur sa page : celle du flux, puis la méta de publication de la page, puis l'adresse,
    puis une date lue dans le texte (meta_de_page) ; sinon "". Les incidents de l'OCDE.AI, sans méta, étaient datés du
    jour de leur collecte (03.11.2026) ; leur adresse porte la date de l'incident."""
    return (date_du_flux or meta.get("date_publication") or date_de_l_adresse(url) or meta.get("date") or "")[:10]


def date_de_plan(meta: dict, url: str) -> tuple[str, str]:
    """(date, origine) d'une fiche de plan de site : la date de publication de la page (ses métadonnées), sinon celle
    de l'adresse, sinon ("", "inconnue"). Jamais le <lastmod> du plan, une date « modifié le » ni une date du texte :
    Zivver (Algoritmeregister) était daté du jour de collecte, SafeGPT de « Laatst gewijzigd op 1 november 2026 »
    (mainteneur, 03.11.2026)."""
    if meta.get("date_publication"):
        return meta["date_publication"][:10], "méta de publication"
    if date_de_l_adresse(url):
        return date_de_l_adresse(url), "adresse"
    return "", "inconnue"


def meta_de_page(html: str) -> dict:
    """Titre, date de publication, auteur et mots-clés d'une page, lus dans son ld+json.

    C'est ce que le flux fournit d'ordinaire ; une page venue du plan de site
    n'a rien d'autre. Ghost et WordPress écrivent tous deux ce bloc.
    """
    meta = {"titre": "", "date": "", "auteur": "", "mots_cles": ""}
    for m in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            d = json.loads(m)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and "@graph" in d:
            d = next((x for x in d["@graph"] if isinstance(x, dict) and "headline" in x), d)
        if not isinstance(d, dict) or not d.get("headline"):
            continue
        meta["titre"] = " ".join(str(d["headline"]).split())
        meta["date"] = str(d.get("datePublished") or d.get("dateCreated") or "")[:10]
        aut = d.get("author")
        if isinstance(aut, list):
            aut = aut[0] if aut else None
        if isinstance(aut, dict):
            meta["auteur"] = str(aut.get("name") or "")
        elif isinstance(aut, str):
            meta["auteur"] = aut
        kw = d.get("keywords")
        meta["mots_cles"] = ", ".join(kw) if isinstance(kw, list) else str(kw or "")
        break
    if not meta.get("date"):
        # Les balises méta de date, dans l'ordre de fiabilité : celles des pages académiques (RePEc, revues), puis
        # celles de l'Open Graph, puis Dublin Core (07.10.2026 : les working papers du FMI repris sur RePEc).
        for nom in (
            "citation_publication_date",
            "citation_date",
            "article:published_time",
            "datePublished",
            "DC.date.issued",
            "DC.Date",
            "dcterms.date",
        ):
            m = re.search(
                rf'<meta[^>]+(?:name|property)="{re.escape(nom)}"[^>]+content="([^"]+)"', html, re.I
            ) or re.search(rf'<meta[^>]+content="([^"]+)"[^>]+(?:name|property)="{re.escape(nom)}"', html, re.I)
            if m and re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", m.group(1)):
                meta["date"] = m.group(1)[:10].replace("/", "-")
                break
    if not meta.get("date"):
        # Date écrite en toutes lettres dans la page (Quantpedia : « 22.August 2025 »)
        MOIS = {
            m: i
            for i, m in enumerate(
                (
                    "january",
                    "february",
                    "march",
                    "april",
                    "may",
                    "june",
                    "july",
                    "august",
                    "september",
                    "october",
                    "november",
                    "december",
                ),
                1,
            )
        }
        # d'abord l'élément de classe « date », puis toute la page (la date est
        # souvent après 60 Ko de feuilles de style en ligne)
        zone = re.search(r'class="[^"]*\bdate\b[^"]*"[^>]*>(.{0,200}?)</(?:div|span|time|p)>', html, re.S | re.I)
        # Le texte visible seulement : les scripts portent des dates sans rapport (Meta AI, 07.10.2026 : une date de
        # 2020 dans le JavaScript, pour un billet du 9 juillet 2026 écrit « July 9, 2026 »).
        visible = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        texte = re.sub(r"<[^>]+>", " ", (zone.group(1) + " ") if zone else "") + re.sub(r"<[^>]+>", " ", visible)
        NOMS = "January|February|March|April|May|June|July|August|September|October|November|December"
        europe = re.search(rf"\b(\d{{1,2}})\.?\s*({NOMS})\s+(\d{{4}})\b", texte, re.I)
        amerique = re.search(rf"\b({NOMS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", texte, re.I)
        # la première qui paraît dans le texte (l'élément « date » vient en tête)
        if europe and (not amerique or europe.start() <= amerique.start()):
            meta["date"] = f"{europe.group(3)}-{MOIS[europe.group(2).lower()]:02d}-{int(europe.group(1)):02d}"
        elif amerique:
            meta["date"] = f"{amerique.group(3)}-{MOIS[amerique.group(1).lower()]:02d}-{int(amerique.group(2)):02d}"
    meta["date_publication"] = date_de_publication(html)
    if not meta["titre"]:
        m = re.search(r"<title>([^<]*)</title>", html, re.I)
        if m:
            meta["titre"] = htmlmod.unescape(" ".join(m.group(1).split()))
    return meta


def plan_de_site(xml: str, depuis: str) -> list[dict]:
    """Les billets d'un `sitemap-posts.xml` modifiés depuis `depuis`, plus récents en tête.

    `lastmod` est une date de modification, toujours postérieure ou égale à la
    publication : filtrer dessus garde tout ce qui a pu paraître dans la
    fenêtre, et c'est la date lue dans la page qui tranche ensuite.
    """
    out = []
    for bloc in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", bloc)
        mod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", bloc)
        if not loc:
            continue
        lastmod = (mod.group(1) if mod else "")[:10]
        if depuis and lastmod and lastmod < depuis:
            continue
        out.append(
            {
                "titre": "",
                "lien": loc.group(1).strip(),
                "date": "",
                "auteur": "",
                "corps": "",
                "complet": False,
                "lastmod": lastmod,
            }
        )
    out.sort(key=lambda d: d["lastmod"], reverse=True)
    return out


def entrees_du_plan_de_site(src, depuis: str) -> list[dict]:
    """Les billets du plan de site : `sitemap-posts.xml` (Ghost, WordPress), sinon
    `sitemap.xml`, qui est souvent un INDEX de sous-plans (Quantpedia, Pionex,
    Bitget, 3Commas, Robot Wealth — 27.09.2026) : on les suit, en préférant
    ceux qui parlent de billets, et on retire les pages qui ne sont pas des
    articles (catégories, étiquettes, auteurs)."""
    site = (getattr(src, "site", "") or src.flux).rstrip("/")
    site = re.sub(r"/(rss|feed)/?$", "", site)
    plans = []

    def xml_de(url):
        """Le plan s'il est bien du XML : WordPress sert une page HTML en 200 pour un nom inconnu."""
        b = recuperer(url)
        t = b.decode("utf-8", "replace") if b else ""
        # All in One SEO (WordPress) met les adresses en CDATA : <loc><![CDATA[…]]></loc> (06.10.2026,
        # Practical Ethics, Just Security, Noema, Roosevelt : leur archive restait vide).
        t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t, flags=re.S)
        return t if ("<urlset" in t[:3000] or "<sitemapindex" in t[:3000]) else ""

    for nom in ("sitemap-posts.xml", "sitemap.xml", "sitemap_index.xml", "post-sitemap.xml"):
        t = xml_de(f"{site}/{nom}")
        if t:
            plans.append(t)
            break
    if not plans:
        # un blog sous /blog/ a parfois son propre plan
        racine = re.sub(r"^(https?://[^/]+).*$", r"\1", site)
        t = xml_de(f"{racine}/sitemap.xml") if racine != site else ""
        if not t:
            return []
        plans.append(t)
    out, vus = [], set()
    while plans:
        xml = plans.pop(0)
        if "<sitemapindex" in xml[:2000]:
            sous = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
            # Un index de centaines de sous-plans (NME : 329, du plus ancien au plus récent, 06.10.2026) : ne
            # garder que ceux modifiés depuis la date demandée, les plus récents d'abord ; les 25 premiers
            # étaient les plus vieux, et la date les écartait tous.
            dates = dict(re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>\s*<lastmod>\s*([^<\s]+)", xml))
            if dates and depuis:
                sous = sorted(
                    (u for u in sous if dates.get(u, "9999") >= depuis),
                    key=lambda u: dates.get(u, "9999"),
                    reverse=True,
                )
            # les sous-plans de billets d'abord (wp-sitemap-posts-post, sitemap-posttype-post,
            # post-sitemap) ; sinon tout, hors taxonomies et pages de service
            pref = [
                u
                for u in sous
                if re.search(r"posts?-post|posttype-post|post-sitemap|/posts?[-.]", u.rsplit("/", 1)[-1], re.I)
            ]
            autres = [
                u
                for u in sous
                if not re.search(r"taxonom|categor|tag|author|page|product|member|root", u.rsplit("/", 1)[-1], re.I)
            ]
            for u in (pref or autres or sous)[:25]:
                if u in vus:
                    continue
                vus.add(u)
                t = xml_de(u)
                if t:
                    plans.append(t)
            continue
        prefixe = site + "/" if re.search(r"/blog$", site) else ""
        for e in plan_de_site(xml, depuis):
            lien = e["lien"]
            if re.search(r"/(category|categories|tag|tags|author|authors|page)/", lien) or lien.rstrip("/") == site:
                continue
            # éditions traduites (/zh/, /ja/, /es-es/…) et pages hors du blog quand le site est un blog
            if re.search(r"://[^/]+/(?:blog/)?[a-z]{2}(?:-[a-z]{2})?/", lien, re.I) and not re.search(
                r"://[^/]+/(?:blog/)?en(?:-[a-z]{2})?/", lien, re.I
            ):
                continue
            if prefixe and not lien.startswith(prefixe):
                continue
            if lien not in vus:
                vus.add(lien)
                out.append(e)
    out.sort(key=lambda d: d["lastmod"], reverse=True)
    return out


def corps_de_page(
    url: str,
    cookie: str | None = None,
    navigateur: bool = False,
    titre_motif: str | None = None,
    corps_motif: str | None = None,
) -> tuple[int, str, bool, dict]:
    """Lit la page d'un article et en rend le texte principal.

    Heuristique volontairement courte : on retire ce qui n'est jamais du corps
    (script, style, navigation, pied de page), puis on garde le plus long bloc
    `article`, `main` ou `entry-content`. Pas de modèle, pas de dépendance :
    c'est la même conversion HTML vers markdown que les autres collecteurs.
    Une session déclarée au registre est présentée en `Cookie`, lue au moment
    de la requête et nulle part ailleurs.
    """
    repos = ss.en_repos(url, False, _maintenant())
    if repos:
        print(f"  !! page {url[:70]} : pages de l'hôte au repos jusqu'à {repos}", file=sys.stderr)
        return 0, "", False, {}
    try:
        if ss.imite(ss.hote(url)) and not cookie:  # hôte réparé par imitation (sante_sources.py)
            html = _imiter(url).decode("utf-8", "replace")
            if not html:
                raise OSError("imitation sans réponse")
        else:
            en_tetes = (
                dict(ENTETES_NAVIGATEUR) if navigateur else {"User-Agent": agent_robot(), "Accept-Language": "en"}
            )
            if cookie:
                en_tetes["Cookie"] = cookie
            html = _ouvrir(url, urllib.request.Request(url, headers=en_tetes)).decode("utf-8", "replace")
    except Exception as e:
        print(f"  !! page {url[:70]} : {e}", file=sys.stderr)
        _echec(url, False)
        return 0, "", False, {}
    ss.succes(url, _maintenant(), False)
    meta = meta_de_page(html)
    trouve = re.search(titre_motif, html, re.S | re.I) if titre_motif else None
    if trouve:  # le titre sur la page même, quand <title> est celui du site (06.11.2026)
        meta["titre"] = " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", trouve.group(1))).split())
    # Le mur des membres se cherche sur toute la page, avant d'en retirer les
    # blocs de structure : chez Ghost il est dans un <aside>, hors du bloc
    # article, et l'aperçu de 176 mots d'un Deep Dive d'AI Secret était passé
    # pour un texte entier (27.09.2026).
    sans_scripts = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    mur = mur_membres(re.sub(r"<[^>]+>", " ", sans_scripts))
    voulu = re.search(corps_motif, sans_scripts, re.S | re.I) if corps_motif else None
    if voulu:  # le bloc du texte désigné au registre, où qu'il soit (MSIT : dans un <form>, 06.11.2026)
        md = html_to_md(voulu.group(1))
        return len(md.split()), md, mur, meta
    html = re.sub(r"<(script|style|nav|header|footer|aside|form)\b.*?</\1>", " ", html, flags=re.S | re.I)
    blocs = [b for _, b in re.findall(r"<(article|main)\b[^>]*>(.*?)</\1>", html, re.S | re.I)]
    blocs += re.findall(
        r'<div[^>]*class="[^"]*(?:entry-content|post-content|article-body|single-blog-post|single-post|blog-post|post-body|article-content)[^"]*"[^>]*>(.*?)</div>',
        html,
        re.S | re.I,
    )
    # Colonne d'article dont le bloc se referme trop tôt (Quantpedia : la classe
    # est sur la cellule, le texte suit dans des <p> jusqu'à la colonne latérale) :
    # on lit de la classe jusqu'au premier signe de fin d'article.
    for m in re.finditer(
        r'<div[^>]*class="[^"]*(?:single-blog-post|single-post|post-body|article-content)[^"]*"[^>]*>', html, re.I
    ):
        fin = re.search(
            r'</article>|<footer\b|class="[^"]*(?:sidebar|large-4|related|comments|post-navigation)',
            html[m.end() :],
            re.I,
        )
        blocs.append(html[m.end() : m.end() + (fin.start() if fin else 60000)])
    if not blocs:
        # Page écrite à la main, sans bloc d'article (nicholas.carlini.com, 03.10.2026) : on en
        # garde les paragraphes, les intertitres, le code et les citations, dans l'ordre — ce
        # qu'un menu ou une colonne latérale ne contient pas.
        corps = re.search(r"<body\b[^>]*>(.*)</body>", html, re.S | re.I)
        morceaux = (
            re.findall(r"<(p|h[2-4]|pre|blockquote)\b[^>]*>.*?</\1>", corps.group(1), re.S | re.I) if corps else []
        )
        if corps and len(morceaux) >= 5:
            blocs = [
                "\n".join(
                    m.group(0)
                    for m in re.finditer(r"<(p|h[2-4]|pre|blockquote)\b[^>]*>.*?</\1>", corps.group(1), re.S | re.I)
                )
            ]
    if not blocs:
        return 0, "", mur, meta
    md = html_to_md(max(blocs, key=lambda b: len(re.sub("<[^>]+>", "", b))))
    return len(md.split()), md, mur, meta


def _texte(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _auteur_de(el) -> str:
    """Le nom d'un auteur. Atom range nom, adresse et courriel dans des sous-balises : leur texte mis bout à bout les colle."""
    fils = {x.tag.split("}")[-1]: _texte(x) for x in el}
    texte = fils.get("name") or _texte(el)
    return " ".join(f"{texte} <{fils['email']}>".split()) if fils.get("email") else " ".join(texte.split())


def _date(brut: str) -> str:
    """Rend une date AAAA-MM-JJ. Les flux mêlent RFC 822 et ISO 8601."""
    brut = (brut or "").strip()
    if not brut:
        return ""
    try:
        return parsedate_to_datetime(brut).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", brut)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


RE_ITEM = re.compile(rb"<(item|entry)\b.*?</\1>", re.S | re.I)
RE_BALISE = re.compile(r"<(?:\w+:)?{}\b[^>]*>(.*?)</(?:\w+:)?{}>", re.S | re.I)


def adresse_sure(lien: str) -> str:
    """L'adresse d'un article telle qu'une requête l'accepte : espaces et caractères hors ASCII encodés, un encodage
    déjà fait gardé (Sixth Tone écrit ses adresses avec des espaces, 06.11.2026)."""
    return urllib.parse.quote(lien, safe=":/?#[]@!$&'()*+,;=%~")


def entrees_tolerantes(brut: bytes) -> list[dict]:
    """Lecture de secours d'un flux mal formé, bloc par bloc.

    Parametric Architecture sert un flux avec un attribut dupliqué, que tout
    analyseur XML strict refuse en entier. Refuser la source pour autant
    reviendrait à la perdre pour un défaut qui ne touche qu'une balise. On
    relit donc chaque élément isolément, par expression régulière, ce qui est
    assez pour les cinq champs dont la fiche a besoin — et le rapport dit que
    la lecture a été tolérante.
    """

    def champ(bloc: str, nom: str) -> str:
        m = (
            RE_BALISE.format
            if False
            else re.search(rf"<(?:\w+:)?{nom}\b[^>]*>(.*?)</(?:\w+:)?{nom}>", bloc, re.S | re.I)
        )
        if not m:
            return ""
        v = re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip())
        return v.strip()

    out = []
    for m in RE_ITEM.finditer(brut):
        bloc = m.group(0).decode("utf-8", "replace")
        corps = champ(bloc, "encoded") or champ(bloc, "content")
        d = {
            "titre": " ".join(re.sub("<[^>]+>", "", champ(bloc, "title")).split()),
            "lien": adresse_sure(re.sub("<[^>]+>", "", champ(bloc, "link")).strip()),
            "date": _date(champ(bloc, "pubDate") or champ(bloc, "published")),
            "auteur": " ".join(re.sub("<[^>]+>", "", champ(bloc, "creator")).split()),
            "corps": corps or champ(bloc, "description"),
            "complet": bool(corps),
        }
        if d["titre"] and d["lien"]:
            out.append(d)
    return out


def entrees(brut: bytes) -> list[dict]:
    """Normalise RSS et Atom en une seule forme. Les deux coexistent dans nos sources."""
    racine = ET.fromstring(brut)
    out = []
    for item in racine.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d = {"titre": "", "lien": "", "date": "", "auteur": "", "corps": "", "complet": False}
        for enfant in item:
            t = enfant.tag.split("}")[-1]
            if t == "title":
                d["titre"] = " ".join(_texte(enfant).split())
            elif t == "link":
                # Atom : plusieurs <link> par entrée ; seul rel="alternate" (ou sans rel) est l'article.
                # WordPress termine par rel="replies", le flux des commentaires — qui l'emportait
                # (03.10.2026 : vingt billets d'Olivier Ezratty lus comme des entrefilets).
                rel = (enfant.get("rel") or "alternate").lower()
                if rel == "alternate" or not d["lien"]:
                    d["lien"] = adresse_sure((enfant.get("href") or _texte(enfant) or "").strip())
            elif t in ("pubDate", "published", "updated", "date"):
                d["date"] = d["date"] or _date(_texte(enfant))
            elif t in ("creator", "author", "name"):
                a = _auteur_de(enfant)
                if a and not d["auteur"]:
                    d["auteur"] = a
            elif t == "encoded":  # content:encoded, flux complet
                d["corps"], d["complet"] = _texte(enfant), True
            elif t == "content" and not d["complet"]:
                d["corps"], d["complet"] = _texte(enfant), True
            elif t in ("description", "summary") and not d["corps"]:
                d["corps"] = _texte(enfant)
        if d["titre"] and d["lien"]:
            out.append(d)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def titres_de(cle_source: str) -> set[str]:
    """Titres déjà collectés par une autre source, pour ne pas les reprendre.

    Un auteur qui publie à la fois sur sa lettre et sur son site nous donne deux
    fois le même texte sous deux URL. Quarante des cent cinquante billets du
    site de Sebastian Raschka sont déjà dans Ahead of AI. Les collecter
    doublerait le texte au corpus et, pire, créerait deux nœuds de même
    étiquette que la déduplication de graphify fusionnerait à sa façon — c'est
    ainsi que vingt-neuf fiches avaient été perdues.

    Le rapprochement est déclaré au registre, source par source, plutôt que
    deviné : deux articles distincts peuvent légitimement porter le même titre.
    """
    from newskit import config_sources as cfg  # noqa: PLC0415

    try:
        src = cfg.une(cle_source)
    except Exception:
        print(f"  !! doublons_de : source « {cle_source} » inconnue", file=sys.stderr)
        return set()
    if not src.manifeste.exists():
        return set()
    return {_norm(v.get("title", "")) for v in json.loads(src.manifeste.read_text(encoding="utf-8")).values()}


def retenu_par_mots_cles(titre: str, mots: list[str]) -> bool:
    """Un flux large ne devient une source que filtré.

    Le flux « intelligence artificielle » de blog.google sert aussi bien des
    notes de recherche que « 3 ways to prep for your next big race with
    Search ». Sans filtre, la veille collecte du marketing produit.
    """
    if not mots:
        return True
    # Bordé d'espaces : un mot-clé « AI » retient aussi le mot qui ouvre ou ferme le titre (03.11.2026, CICR, IEEE).
    bas = f" {titre.lower()} "
    return any(m.lower() in bas for m in mots)


def page_par_api(src, lien: str) -> tuple[int, str, bool, dict]:
    """L'article lu par l'API du site plutôt que par sa page, pour un site dont les pages sont construites dans le
    navigateur (Europol, 07.10.2026 : une application React qui demande chaque page à `/cms/api/node?url=<chemin>` ; le
    point d'accès se lit dans son code JavaScript). Registre : `api_page`, l'adresse avec {chemin} ou {url} ;
    `api_champs`, le nom des champs du JSON — titre, date (texte ISO ou nombre de secondes), corps (une liste de
    champs HTML mis bout à bout), un champ imbriqué s'écrivant « a.b ». `{segment}` : le dernier segment de l'adresse,
    transformé par `api_segment` (`majuscules`, `remplacer`) ; salle de presse de la Commission, 03.11.2026 :
    « ip_26_2303 » → « IP/26/2303 ». La requête demande du JSON. Rend comme corps_de_page : (mots, markdown, mur, méta)."""
    parties = urllib.parse.urlsplit(lien)
    chemin = parties.path + (f"?{parties.query}" if parties.query else "")
    regle = getattr(src, "api_segment", None) or {}
    segment = parties.path.rstrip("/").split("/")[-1]
    segment = segment.upper() if regle.get("majuscules") else segment
    for a, b in (regle.get("remplacer") or {}).items():
        segment = segment.replace(a, b)
    adresse = (
        str(src.api_page)
        .replace("{chemin}", urllib.parse.quote(chemin))
        .replace("{url}", urllib.parse.quote(lien, safe=""))
        .replace("{segment}", urllib.parse.quote(segment, safe=""))
    )
    champs = {
        "titre": "title",
        "date": "published",
        "corps": ["introduction", "body"],
        **(getattr(src, "api_champs", None) or {}),
    }
    try:
        b = recuperer(
            adresse,
            essais=2,
            navigateur=getattr(src, "propres", {}).get("entetes") == "navigateur",
            accept="application/json",
        )
        d = json.loads(b.decode("utf-8", "replace")) if b else {}
    except (json.JSONDecodeError, AttributeError):
        d = {}
    if not isinstance(d, dict) or not d:
        return 0, "", False, {"titre": "", "date": "", "auteur": "", "mots_cles": ""}

    def champ(nom):
        v = d
        for k in str(nom).split("."):
            v = v.get(k) if isinstance(v, dict) else None
        return v

    corps = "\n\n".join(
        html_to_md(htmlmod.unescape(str(champ(c) or "")))
        for c in ([champs["corps"]] if isinstance(champs["corps"], str) else champs["corps"])
        if champ(c)
    )
    date = champ(champs["date"]) or ""
    if isinstance(date, (int, float)) or str(date).isdigit():
        date = time.strftime("%Y-%m-%d", time.gmtime(int(date)))
    meta = {"titre": str(champ(champs["titre"]) or ""), "date": str(date)[:10], "auteur": "", "mots_cles": ""}
    return len(corps.split()), corps, False, meta


def entrees_plan_quotidien(src, limite: int) -> list[dict]:
    """La collecte ordinaire d'une source « Plan de site » : les adresses du plan (et de ses sous-plans) qui ressemblent
    à des articles (`chemins`) et ont changé dans les `jours` derniers jours, les plus récentes d'abord. Sans <lastmod>,
    l'ordre du plan est gardé ; le manifeste écarte ensuite ce qui est déjà en fiche."""
    nav = getattr(src, "propres", {}).get("entetes") == "navigateur"
    chemins = re.compile(str(src.chemins), re.I)
    depuis = time.strftime("%Y-%m-%d", time.gmtime(time.time() - int(getattr(src, "jours", 0) or 10) * 86400))

    def xml_de(url):
        b = recuperer(url, navigateur=nav)
        t = b.decode("utf-8", "replace") if b else ""
        t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t, flags=re.S)
        return t if ("<urlset" in t[:3000] or "<sitemapindex" in t[:3000]) else ""

    plans, vus_plans, out, vus = [src.plan], set(), [], set()
    while plans and len(vus_plans) < 40:
        u = plans.pop(0)
        if u in vus_plans:
            continue
        vus_plans.add(u)
        xml = xml_de(u)
        if "<sitemapindex" in xml[:2000]:
            sous = re.findall(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+))?", xml)
            # les sous-plans récents d'abord ; sans date, tous, dans l'ordre
            sous = [(s, d) for s, d in sous if not d or d[:10] >= depuis]
            plans += [s for s, _ in sorted(sous, key=lambda x: x[1] or "", reverse=True)]
            continue
        for e in plan_de_site(xml, depuis):
            if chemins.search(e["lien"]) and e["lien"] not in vus:
                vus.add(e["lien"])
                out.append(e)
    out.sort(key=lambda d: d["lastmod"], reverse=True)
    return out[:limite] if limite else out


def entrees_page_de_liste(src, limite: int) -> list[dict]:
    """La collecte d'une source « Page de liste » : les liens de la page qui ressemblent à des articles (`chemins`), dans
    l'ordre de la page (le plus récent en tête, d'ordinaire), avec le texte du lien pour titre provisoire."""
    nav = getattr(src, "propres", {}).get("entetes") == "navigateur"
    b = recuperer(src.liste, navigateur=nav)
    if not b:
        return []
    page = b.decode("utf-8", "replace")
    chemins = re.compile(str(src.chemins), re.I)
    out, vus = [], set()
    # Un index JSON plutôt qu'une page (Adobe, 07.10.2026 : les sites bâtis sur Adobe Experience Manager publient
    # /query-index.json, la liste de tous les articles avec chemin, titre et date). Dates : texte ISO, secondes
    # depuis 1970, ou numéro de série de tableur (jours depuis le 30.12.1899).
    if page.lstrip().startswith("{"):
        try:
            lignes = json.loads(page).get("data") or []
        except (json.JSONDecodeError, AttributeError):
            lignes = []

        def jour(x):
            s = str(x or "")
            if re.fullmatch(r"\d{5}(\.\d+)?", s):
                return time.strftime("%Y-%m-%d", time.gmtime((float(s) - 25569) * 86400))
            if re.fullmatch(r"\d{9,10}", s):
                return time.strftime("%Y-%m-%d", time.gmtime(int(s)))
            return s[:10]

        for l in lignes:
            lien = urllib.parse.urljoin(src.liste, str(l.get("path") or l.get("url") or ""))
            if not chemins.search(lien) or lien in vus:
                continue
            vus.add(lien)
            out.append(
                {
                    "titre": str(l.get("title") or ""),
                    "lien": lien,
                    "date": jour(l.get("date") or l.get("lastModified")),
                    "auteur": str(l.get("author") or ""),
                    "corps": "",
                    "complet": False,
                    "lastmod": jour(l.get("lastModified")),
                }
            )
        out.sort(key=lambda e: e["date"], reverse=True)
        return out[:limite] if limite else out
    for href, texte in re.findall(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', page, flags=re.S | re.I):
        lien = urllib.parse.urljoin(src.liste, htmlmod.unescape(href.strip()))
        if not chemins.search(lien) or lien.rstrip("/") == src.liste.rstrip("/"):
            continue
        titre = " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", texte)).split())
        titre = titre if len(titre) > 12 else ""
        if lien in vus:
            # Une carte d'article porte souvent deux liens : l'image d'abord, sans texte, puis le titre (Meta AI).
            for e in out:
                if e["lien"] == lien and len(titre) > len(e["titre"]):
                    e["titre"] = titre
            continue
        vus.add(lien)
        out.append(
            {"titre": titre, "lien": lien, "date": "", "auteur": "", "corps": "", "complet": False, "lastmod": ""}
        )
    propres = getattr(src, "propres", {}) or {}
    if propres.get("lien_motif") and propres.get("lien_modele"):
        # Des articles ouverts par un script (onclick="fn_detail(1316)", MSIT, 06.11.2026) : l'identifiant fait l'adresse.
        for ident in re.findall(str(propres["lien_motif"]), page):
            lien = str(propres["lien_modele"]).replace("{1}", ident)
            if chemins.search(lien) and lien not in vus:
                vus.add(lien)
                out.append(
                    {"titre": "", "lien": lien, "date": "", "auteur": "", "corps": "", "complet": False, "lastmod": ""}
                )
    return out[:limite] if limite else out


# Segments de fin qui ne nomment pas l'article (07.11.2026 : chez blog.pebblous.ai le dernier segment est la langue,
# et trois articles anglais du même jour s'écrivaient dans 2026-11-07_en.md).
SEGMENTS_MUETS = frozenset({"index.html", "index.htm", "index.php", "index", "amp"})


def slug(url: str, titre: str) -> str:
    segs = (
        [urllib.parse.unquote(x) for x in urllib.parse.urlsplit(url).path.split("/") if x] if url else []
    )  # adresse encodée (06.11.2026)
    langue = fiche.langue_de_l_adresse(url) if url else ""
    segs = [x for x in segs if not fiche._langue_du_segment(x) and x.lower() not in SEGMENTS_MUETS]
    bout = (segs[-1] if segs else "") + (f"-{langue}" if langue and segs else "")
    # Une adresse à requête (view.do?…nttSeqNo=1316, MSIT, 06.11.2026) : son dernier segment est le même pour tous.
    base = titre if titre and urllib.parse.urlsplit(url).query else (bout or titre)
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:80] or "article"


def ordonner_versions(items: list[dict]) -> list[dict]:
    """Les versions d'un même article (fiche.cle_d_article) se suivent, la française d'abord, puis l'anglaise, à la
    place de la première venue ; l'ordre des articles ne change pas. La première écrite l'emporte (fiche.ecrire refuse
    les autres)."""
    rang = {"fr": 0, "en": 1, "": 1.5}  # sans segment : la version par défaut du site
    groupes: dict[str, list[dict]] = {}
    for i, it in enumerate(items):
        lien = it.get("lien") or ""
        groupes.setdefault(fiche.cle_d_article(lien) if lien.startswith("http") else f"#{i}", []).append(it)
    return [
        it
        for g in groupes.values()
        for it in sorted(g, key=lambda x: rang.get(fiche.langue_de_l_adresse(x.get("lien") or ""), 2))
    ]


def nom_libre(dossier: Path, nom: str, lien: str) -> str:
    """Le nom de la fiche, suivi d'une empreinte de l'adresse s'il est déjà pris par un autre article."""
    if not (dossier / nom).exists():
        return nom
    import hashlib  # noqa: PLC0415

    return f"{nom[:-3]}-{hashlib.sha1(lien.encode()).hexdigest()[:6]}.md"


def traiter_source(src, limite: int, echecs: list[str], archive_depuis: str = "") -> dict:
    """Collecte une source de flux. Rend le décompte, ou ajoute à `echecs`.

    Le décompte distingue ce qui a été écrit de ce qui a été écarté, et
    pourquoi : entrefilet, hors mots-clés, doublon d'une autre source. Chacun
    de ces écarts est une décision du registre, et le rapport la rend lisible.
    """
    compte = {
        "lues": 0,
        "neuves": 0,
        "partielles": 0,
        "entrefilets": 0,
        "hors_sujet": 0,
        "doublons": 0,
        "murs": 0,
        "hors_fenetre": 0,
        "tolerante": False,
    }
    src.chemin.mkdir(parents=True, exist_ok=True)
    manifeste = json.loads(src.manifeste.read_text(encoding="utf-8")) if src.manifeste.exists() else {}

    mode = {"Plan de site": "plan", "Page de liste": "liste"}.get(getattr(src, "plateforme", ""), "flux")
    if mode != "flux" and not archive_depuis:
        items = entrees_plan_quotidien(src, limite) if mode == "plan" else entrees_page_de_liste(src, limite)
        if not items:
            echecs.append(
                f"{src.nom} : {'plan de site' if mode == 'plan' else 'page de liste'} injoignable, vide, ou rien de récent"
            )
            return compte
    elif archive_depuis:
        # L'archive : le plan de site liste tout ce que le flux ne montre plus.
        # Chaque page est lue ; sa date de publication décide de la fenêtre.
        items = entrees_du_plan_de_site(src, archive_depuis)
        if not items:
            echecs.append(f"{src.nom} : plan de site injoignable ou vide")
            return compte
        print(f"  {src.nom} : {len(items)} billet(s) au plan de site modifiés depuis {archive_depuis}")
    else:
        brut = recuperer(src.flux, navigateur=getattr(src, "propres", {}).get("entetes") == "navigateur")
        if not brut:
            echecs.append(f"{src.nom} : flux injoignable")
            return compte
        try:
            items = entrees(brut)
        except ET.ParseError as e:
            items = entrees_tolerantes(brut)
            compte["tolerante"] = True
            if not items:
                echecs.append(f"{src.nom} : flux illisible ({e})")
                return compte
            print(f"  {src.nom} : flux mal formé ({e}), lecture tolérante")
        if limite:
            items = items[:limite]  # les flux servent le plus récent en tête
    compte["lues"] = len(items)

    lit_la_page = bool(getattr(src, "page", False)) or bool(archive_depuis) or mode != "flux"
    cookie = None
    if src.session:
        cookie = cfg.session(src.session)
        if cookie:
            print(f"  {src.nom} : session « {src.session} » présentée pour les pages")
        else:
            print(
                f"  {src.nom} : session « {src.session} » déclarée mais {cfg.etat_session(src.session)} ; "
                f"les pages réservées resteront des entrefilets",
                file=sys.stderr,
            )
    mots_cles = list(getattr(src, "mots_cles", []) or [])
    # `exclure` : expression régulière, sans égard à la casse, sur l'adresse et le titre. Pour les
    # flux qui mêlent au sujet des ordres du jour, des pages de service ou une seconde langue
    # (03.10.2026 : LINC publie chaque article en anglais puis en français).
    exclure = re.compile(str(getattr(src, "exclure", "") or ""), re.I) if getattr(src, "exclure", "") else None
    deja_vus: set[str] = set()
    for autre in getattr(src, "doublons_de", []) or []:
        deja_vus |= titres_de(autre)

    for it in ordonner_versions(items):
        if it["lien"] in manifeste:
            continue
        if not it["titre"] and not archive_depuis and mode == "flux":
            continue
        # En archive, le plan de site ne donne pas de titre : le filtre par
        # mots-clés s'applique plus bas, une fois la page lue (27.09.2026 :
        # 367 billets de 3Commas écartés « hors mots-clés » sur un titre vide).
        if it["titre"] and not retenu_par_mots_cles(it["titre"], mots_cles):
            compte["hors_sujet"] += 1
            continue
        if exclure and (exclure.search(it["lien"] or "") or exclure.search(it["titre"] or "")):
            compte["hors_sujet"] += 1
            continue
        if deja_vus and _norm(it["titre"]) in deja_vus:
            compte["doublons"] += 1
            continue

        corps_md = html_to_md(htmlmod.unescape(it["corps"]))
        mots = len(corps_md.split())
        # Un texte est un texte, quelle que soit la balise qui l'a porté :
        # Magenta sert 2 857 mots dans `description`.
        complet = bool(
            (it["complet"] and mots >= MOTS_MINIMUM) or (mots >= MOTS_TEXTE and not COUPURES.search(corps_md[-120:]))
        )
        if mots < MOTS_MINIMUM and lit_la_page:
            if getattr(src, "propres", {}).get("api_page"):
                n_page, md_page, mur, meta = page_par_api(src, it["lien"])
            else:
                motifs = {k: v for k in ("titre_motif", "corps_motif") if (v := getattr(src, "propres", {}).get(k))}
                n_page, md_page, mur, meta = corps_de_page(
                    it["lien"], cookie, navigateur=getattr(src, "propres", {}).get("entetes") == "navigateur", **motifs
                )
            time.sleep(PAUSE_PAGE)
            it["titre"] = it["titre"] or meta.get("titre", "")
            if mode == "plan":
                it["date"], it["origine_date"] = date_de_plan(meta, it["lien"])
            else:
                it["date"] = date_de_l_entree(it["date"], meta, it["lien"])
            it["auteur"] = it["auteur"] or meta.get("auteur", "")
            if archive_depuis and it["date"] and it["date"] < archive_depuis:
                compte["hors_fenetre"] += 1
                continue
            if archive_depuis and not retenu_par_mots_cles(it["titre"], mots_cles):
                compte["hors_sujet"] += 1  # le titre n'était connu qu'après lecture
                continue
            if mur and n_page < MOTS_TEXTE:
                compte["murs"] += 1
                if cookie:
                    # La session est présentée et la page se ferme quand même :
                    # session expirée, ou palier payant que le compte ne couvre pas.
                    print(f"  !! réservé malgré la session : {it['lien'][:80]}", file=sys.stderr)
            if n_page > mots:
                # Une page lue n'est un texte entier qu'au-delà de MOTS_TEXTE et
                # sans mur : entre 120 et 300 mots, c'est un aperçu, qui entre
                # comme tel (complet: false), pas comme un article.
                corps_md, mots = md_page, n_page
                complet = n_page >= MOTS_TEXTE and not mur
        if mots < MOTS_MINIMUM:
            compte["entrefilets"] += 1
            continue

        if fiche.publicitaire(it["titre"], corps_md):
            compte["publicites"] = compte.get("publicites", 0) + 1
            continue
        aujourdhui = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        date = it["date"] or date_de_l_adresse(it["lien"]) or ("" if mode == "plan" else aujourdhui)
        # Une date à venir est celle d'un événement annoncé, pas de la publication (CGarchitect, 07.10.2026 : un salon
        # du 16 octobre) : on prend le jour de la collecte, comme l'éclaireur.
        if date[:10] > aujourdhui:
            date = "" if mode == "plan" else aujourdhui
        if mode == "plan" and not date:
            it["origine_date"] = "inconnue"
        nom = nom_libre(src.chemin, f"{date or aujourdhui}_{slug(it['lien'], it['titre'])}.md", it["lien"])
        champs = {
            "title": it["titre"],
            "subtitle": "",
            "author": it["auteur"],
            "date": date,
            "url": it["lien"],
            "plateforme": getattr(src, "plateforme", "Flux RSS"),
            "publication": src.nom,
            "complet": complet,
            "collected": time.strftime("%Y-%m-%d"),
            "source": {
                "Plan de site": f"plan de site de {src.nom}",
                "Page de liste": f"page de liste de {src.nom}",
            }.get(getattr(src, "plateforme", ""), f"flux RSS de {src.nom}"),
            "flux": getattr(src, "plan", "") or getattr(src, "liste", "") or src.flux,
        }
        if mode == "plan":
            # La date de publication, son origine, et à part la date de modification (<lastmod>), qui n'en est jamais une.
            champs["origine_date"] = it.get("origine_date") or "inconnue"
            if it.get("lastmod"):
                champs["date_modification"] = it["lastmod"][:10]
        try:
            fiche.ecrire(src.chemin / nom, champs, f"# {it['titre']}\n\n{corps_md}")
        except fiche.IdentiteDejaVue as deja:  # une autre fiche du graphe porte déjà ce document
            print(f"  déjà vue : {deja}")
            continue
        except ValueError as e:
            echecs.append(f"{src.nom} : {str(e)[:100]}")
            continue
        manifeste[it["lien"]] = {"file": nom, "title": it["titre"], "date": date, "complet": complet, "words": mots}
        compte["neuves"] += 1
        compte["partielles"] += 0 if complet else 1

    src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
    compte["total"] = len(manifeste)
    compte["resumes"] = sum(1 for v in manifeste.values() if not v.get("complet"))
    return compte


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    ap.add_argument("--limite", type=int, default=40, help="entrées les plus récentes par flux (0 = tout ; défaut 40)")
    ap.add_argument(
        "--archive",
        metavar="DEPUIS",
        help="remonter l'archive par le plan de site (sitemap-posts.xml) depuis cette "
        "date AAAA-MM-JJ ; exige --source, lit chaque page",
    )
    args = ap.parse_args()
    if args.archive and not args.source:
        ap.error("--archive exige --source : on ne remonte pas toutes les archives d'un coup")
    if args.archive and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.archive):
        ap.error("--archive attend une date AAAA-MM-JJ")

    sources = [
        s
        for p in ("Flux RSS", "Plan de site", "Page de liste")
        for s in cfg.toutes(p, graphe=args.graphe)
        if not args.source or s.cle == args.source
    ]
    if not sources:
        print("  aucune source « Flux RSS » au registre, rien à collecter.")
        return 0

    echecs: list[str] = []
    total_neuf = total_partiel = 0
    for src in sources:
        c = traiter_source(src, args.limite, echecs, args.archive or "")
        if "total" not in c:
            continue
        total_neuf += c["neuves"]
        total_partiel += c["partielles"]
        print(
            f"{src.nom} : {c['lues']} entrée(s) lue(s), {c['neuves']} neuve(s), "
            f"{c['total']} au total, {c['resumes']} en résumé, {c['entrefilets']} entrefilet(s)"
            + (f", {c['hors_sujet']} hors mots-clés" if c["hors_sujet"] else "")
            + (f", {c['murs']} page(s) réservée(s) aux membres" if c.get("murs") else "")
            + (f", {c['hors_fenetre']} avant la date demandée" if c.get("hors_fenetre") else "")
            + (f", {c['doublons']} doublon(s) d'une autre source" if c["doublons"] else "")
            + ("  [lecture tolérante]" if c["tolerante"] else "")
        )

    print(f"\n  {total_neuf} fiche(s) neuve(s), dont {total_partiel} en résumé (le flux ne porte pas le texte entier)")
    if echecs:
        # Un refus n'est pas une absence : ce qui n'a pas pu être lu est dit,
        # et le code de sortie le porte.
        print("  ALERTE :", file=sys.stderr)
        for e in echecs:
            print(f"    {e}", file=sys.stderr)
        return 1
    return 0


reglages.appliquer(__name__, globals())  # les réglages du média (reglages.py)


if __name__ == "__main__":
    sys.exit(main())

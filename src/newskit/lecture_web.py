#!/usr/bin/env python3
"""Lire une page web nous-mêmes et n'en garder que la substance, avant de la donner au modèle.

Mesure du 09.10.2026 sur 14 pages lues par le modèle pour le banc : les balises et scripts font 85 % du HTML, les
menus, pieds de page et liens annexes au moins 24 % du texte visible ; le texte principal pèse 11 % du brut. Quand le
modèle cherche ET lit lui-même (`WebFetch` dans `claude -p`), nous ne voyons ni ce qu'il reçoit ni ce qu'il paie par
page, et chaque tour relit tout. Ici : `trafilatura` extrait le texte principal (texte entier, sans plafond : un
plafond perdrait du contenu, décision du porteur), notre extracteur maison sert de repli, et une citation annoncée par
le modèle est vérifiée MOT POUR MOT dans le texte avant d'être gardée — ce que la voie « le modèle lit » ne permet pas.

    python3 -m newskit.lecture_web <url> [<url>…]      # ce que le modèle recevrait : titre, date, mots, début du texte
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from newskit.commun import CODE, ENTETES_NAVIGATEUR, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

DELAI = 25
# Nous téléchargeons la page nous-mêmes (03.11.2026, décision du porteur) : `trafilatura.fetch_url` envoyait ses
# propres en-têtes, et l'ancien jeu ENTETES (agent et langue seuls) n'était pas utilisé. Un seul profil de navigateur,
# complet et cohérent, celui du dépôt (`commun.ENTETES_NAVIGATEUR` : agent, Accept, sec-ch-ua, Sec-Fetch-*) ; un
# en-tête isolé ou des profils mélangés sont eux-mêmes un signal de robot.
ENTETES = ENTETES_NAVIGATEUR
REFUS = {401, 403, 429, 451, 503}
# Un mur anti-robot servi en 200 : marques de Cloudflare, DataDome, PerimeterX, Imperva, Akamai, AWS WAF. Cherchées seulement
# dans le titre et le code d'une page courte (un article qui en parle reste un article).
RE_MUR = re.compile(
    r"<title>\s*(just a moment|attention required|access denied|pardon our interruption|"
    r"un instant)|/cdn-cgi/challenge-platform/|cf-chl-|captcha-delivery\.com|px-captcha|"
    r"_incapsula_resource|awsWafCookieDomainList|gokuProps",
    re.I,
)
MOTS_MUR = 200
MOTS_LUE = 80  # sous 80 mots, une page n'est pas lue : « page trop courte »


def _obtenir(url: str, entetes: dict | None = None) -> tuple[bytes, str, str, str]:
    """(octets, type, jeu de caractères, raison) ; raison non vide si la page est refusée ou injoignable."""
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict(entetes or ENTETES)), timeout=DELAI) as r:
            from newskit import collecte_rss as cr  # noqa: PLC0415

            octets = cr._decompresser(r.read(), r.headers.get("Content-Encoding"))
            return octets, r.headers.get_content_type() or "", r.headers.get_content_charset() or "utf-8", ""
    except urllib.error.HTTPError as e:
        return b"", "", "", f"refusée ({e.code})" if e.code in REFUS else f"téléchargement en échec ({e.code})"
    except Exception as e:  # noqa: BLE001
        return b"", "", "", f"téléchargement en échec ({type(e).__name__})"


def _decoder(octets: bytes, jeu: str) -> str:
    try:
        return octets.decode(jeu, "replace")
    except LookupError:
        return octets.decode("utf-8", "replace")


def _mur(html: str) -> bool:
    visible = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I))
    return len(visible.split()) < MOTS_MUR and bool(RE_MUR.search(html))


def telecharger(url: str) -> tuple[str, str]:
    """(html, raison) : la page, téléchargée avec le profil ENTETES ; html vide et raison dite si elle est refusée
    (401, 403, 429, 451, 503, mur anti-robot) ou injoignable. Ne lève jamais."""
    octets, _, jeu, raison = _obtenir(url)
    if raison:
        return "", raison
    html = _decoder(octets, jeu)
    return ("", "refusée (mur anti-robot)") if _mur(html) else (html, "")


# EUR-Lex par la voie officielle (03.11.2026, décision du porteur) : la page d'EUR-Lex (ELI ou legal-content) sert un
# mur AWS WAF à tout client sans JavaScript (26 mots lus pour le règlement 2024/1689). Le dépôt Cellar de l'Office des
# publications rend l'acte entier en XHTML par négociation de contenu, sans mur : 92 410 mots pour l'AI Act.
CELLAR = "https://publications.europa.eu/resource/celex/"
TYPES_ELI = {
    "reg": "R",
    "reg_impl": "R",
    "reg_del": "R",
    "dir": "L",
    "dir_impl": "L",
    "dir_del": "L",
    "dec": "D",
    "dec_impl": "D",
    "dec_del": "D",
}
LANGUES = {"en": "eng", "fr": "fra", "de": "deu", "it": "ita", "es": "spa", "nl": "nld", "pt": "por", "pl": "pol"}


def celex_de(url: str) -> tuple[str, str] | None:
    """(numéro CELEX, langue en trois lettres) d'une adresse d'EUR-Lex, ou None."""
    import urllib.parse  # noqa: PLC0415

    u = urllib.parse.unquote(url or "")
    if not re.match(r"https?://(www\.)?eur-lex\.europa\.eu/", u, re.I):
        return None
    m = re.search(r"/legal-content/([A-Z]{2})/", u)
    langue = LANGUES.get(m.group(1).lower(), "eng") if m else "eng"
    m = re.search(r"/oj/([a-z]{3})\b", u)
    langue = m.group(1) if m else langue
    m = re.search(r"CELEX:(\d[0-9A-Z()]+)", u, re.I)
    if m:
        return m.group(1).upper(), langue
    m = re.search(r"/eli/([a-z_]+)/(\d{4})/(\d+)/oj", u)
    if m and m.group(1) in TYPES_ELI:
        return f"3{m.group(2)}{TYPES_ELI[m.group(1)]}{int(m.group(3)):04d}", langue
    return None


def _texte_pdf(octets: bytes) -> str:
    """Le texte d'un PDF, extrait localement par pypdf (déjà installé), une page après l'autre."""
    import io  # noqa: PLC0415
    import pypdf  # noqa: PLC0415

    lecteur = pypdf.PdfReader(io.BytesIO(octets))
    return "\n".join((pg.extract_text() or "").strip() for pg in lecteur.pages).strip()


def _plier(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


# Notes de bas de page et lignes parasites (03.11.2026, décision du porteur). Le mémo Sullivan & Cromwell sur les
# gigafactories : 4 361 mots lus, dont 1 097 de notes ([1] … [51], « Tender Specifications, p. 26 ») et une ligne
# « Subscribe to stay current on S&C Insights. ». Les notes ne se jettent pas : elles vont dans `notes`, où
# `citation_dans` les lit encore ; les appels « [7] » du corps sortent ; un paragraphe de fond ne sort jamais.
RE_APPEL = re.compile(r"\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]")
RE_NOTE_CROCHET = re.compile(r"^\s*\[\d{1,3}\]\s*\S")
RE_NOTE_POINT = re.compile(r"^\s*\d{1,3}\.\s+\S")
RE_TITRE_NOTES = re.compile(
    r"^\W*(foot ?notes?|end ?notes?|notes?|references|sources|notes de bas de page|fussnoten|fu(ss|ß)noten|anmerkungen|"
    r"endnoten|voetnoten|noten|note|notas|referencias|referenze)\W*$",
    re.I,
)
MOTS_PARASITE = 25  # une ligne d'abonnement : moins de 25 mots
MOTS_PARTAGE = 8  # une ligne de partage : un bouton, quelques mots (« Teilen der Bevölkerung… » est du fond)
RE_ABONNEMENT = re.compile(  # sur le texte plié (`_plier`) : minuscules, sans accents
    r"^(subscribe|sign up|signup|join our (mailing list|newsletter)|get our newsletter|stay (current|up to date|informed)|"
    r"abonnez vous|abonne toi|s abonner|inscrivez vous|recevez notre (lettre|newsletter)|"
    r"(jetzt |newsletter )?abonnieren|melden sie sich|registrieren sie sich|"
    r"abonneer|schrijf je in|meld je aan|"
    r"iscriviti|abbonati|"
    r"suscribete|suscribase|suscribirse|registrate)\b"
    r"|\b(newsletter|nieuwsbrief|boletin|lettre d information)\b.*\b(sign up|subscribe|abonn|inscri|iscriv|suscri|aanmeld)"
    r"|\b(sign up|subscribe|abonn|inscri|iscriv|suscri|aanmeld)\w*\b.*\b(newsletter|nieuwsbrief|boletin)\b"
)
RE_PARTAGE = re.compile(
    r"^(share( this| on| via| article|$)|follow us|partager|partagez|suivez nous|teilen|diesen artikel teilen|"
    r"artikel teilen|folgen sie uns|delen|deel dit|deel dit artikel|volg ons|condividi|seguici|comparte|compartir|"
    r"siguenos)\b"
)


def _parasite(ligne: str) -> bool:
    """Une ligne d'abonnement ou de partage, en six langues ; courte, jamais un paragraphe de fond."""
    n = len(ligne.split())
    if n == 0 or n >= MOTS_PARASITE:
        return False
    p = _plier(ligne)
    return bool(RE_ABONNEMENT.search(p)) or (n <= MOTS_PARTAGE and bool(RE_PARTAGE.search(p)))


def nettoyer(texte: str) -> tuple[str, str]:
    """(corps, notes) : le bloc final de notes ([n] ou « n. » sous un titre de notes, ou « n. » quand le corps porte
    des appels [k]) passe dans `notes` ; les appels [k] et les lignes parasites sortent du corps."""
    lignes = [l for l in (texte or "").split("\n") if not _parasite(l)]
    i = len(lignes)
    while i > 0 and not lignes[i - 1].strip():
        i -= 1
    fin, debut, crochets = i, i, 0
    while debut > 0:
        l = lignes[debut - 1]
        if not l.strip():
            debut -= 1
            continue
        if RE_NOTE_CROCHET.match(l):
            crochets += 1
        elif not RE_NOTE_POINT.match(l):
            break
        debut -= 1
    while debut < fin and not lignes[debut].strip():
        debut += 1
    bloc = lignes[debut:fin]
    titre = debut > 0 and bool(RE_TITRE_NOTES.match(lignes[debut - 1].strip()))
    avant = "\n".join(lignes[:debut])
    if bloc and (crochets == len([l for l in bloc if l.strip()]) or titre or RE_APPEL.search(avant)):
        if titre:
            debut -= 1
        notes = "\n".join(lignes[debut:fin]).strip()
        corps = lignes[:debut] + lignes[fin:]
    else:
        notes, corps = "", lignes
    corps_txt = RE_APPEL.sub("", "\n".join(corps))
    corps_txt = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", corps_txt))
    return re.sub(r"\n{3,}", "\n\n", corps_txt).strip(), notes


# Blocs que trafilatura laisse (03.11.2026, décision du porteur). Mesure du reporter : cinq citations étaient dans le
# HTML mais pas dans le texte lu — valeurs d'une fiche de l'algoritmeregister (« Begindatum » sans sa date, « Laatst
# gewijzigd… »), titres d'une liste de résultats, un décret de normattiva coupé par un `</html>` au milieu de la page,
# une audition du Bundestag dans un onglet. On reprend les blocs feuilles de la page (texte propre d'un p, li, dt, dd,
# td, th, titre, div…) qui manquent au texte, chacun après le dernier bloc que trafilatura a gardé (une clé et sa valeur
# restent voisines) ; ni menus, en-têtes, pieds, ni bloc avant le premier gardé (il n'a pas d'ancre), ni hors de <main>.
BALISES_BLOC = {
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "main",
    "blockquote",
    "pre",
    "figure",
    "figcaption",
    "caption",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "br",
}
BALISES_OTEES = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "template",
    "svg",
    "button",
    "select",
    "iframe",
)


def _reparer(html: str) -> str:
    """Un `</body>` ou `</html>` au milieu de la page (normattiva) arrête lxml, donc trafilatura : on les retire."""
    return re.sub(r"</(body|html)\s*>", "", html or "")


def _feuilles(html: str) -> list[str]:
    """Le texte propre de chaque bloc de la zone principale (<main>, sinon le plus long <article>, sinon la page), dans
    l'ordre du document : le texte du bloc et de ses éléments en ligne, sans celui de ses blocs enfants."""
    import lxml.html  # noqa: PLC0415

    try:
        doc = lxml.html.document_fromstring(html)
    except Exception:  # noqa: BLE001
        return []
    for e in doc.xpath("//" + "|//".join(BALISES_OTEES)):
        if e.getparent() is None:
            continue
        e.drop_tree()
    zone = (doc.xpath("//main") or [None])[0]
    if zone is None:
        articles = doc.xpath("//article")
        zone = max(articles, key=lambda a: len(a.text_content())) if articles else doc

    def bloc(e) -> bool:
        return isinstance(e.tag, str) and e.tag in BALISES_BLOC

    def propre(e) -> str:
        morceaux = [e.text or ""]
        for c in e:
            if not isinstance(c.tag, str) or (bloc(c) and c.tag != "br"):
                morceaux.append(c.tail or "")
            else:
                morceaux += [" " if c.tag == "br" else c.text_content(), c.tail or ""]
        return re.sub(r"\s+", " ", "".join(morceaux)).strip()

    return [t for e in zone.iter() if (bloc(e) or e is zone) for t in [propre(e)] if t]


def rattraper(html: str, texte: str) -> tuple[str, int]:
    """(texte complété, nombre de blocs repris) : les blocs de la page absents du texte, insérés après le dernier bloc
    gardé qui les précède ; rien avant le premier bloc gardé (en-tête), ni un mot seul sans chiffre (bouton)."""
    lignes = texte.split("\n")
    pl = [_plier(l) for l in lignes]
    # Une adresse de courriel ou web ne vaut pas un nom lu : « gemeente@reimerswaal.nl » faisait passer « Gemeente
    # Reimerswaal », le nom de la fiche, pour déjà présent (Algoritmeregister, 03.11.2026).
    plie = " ".join(_plier(re.sub(r"\S+@\S+|https?://\S+|www\.\S+", " ", l)) for l in lignes)
    ajouts: dict[int, list[str]] = {}
    vus: set[str] = set()
    ancre, pos = None, 0
    for f in _feuilles(html):
        p = _plier(f)
        if not p:
            continue
        if p in plie:
            tete = " ".join(p.split()[:4])
            j = next((k for k in range(pos, len(pl)) if tete in pl[k]), None)
            j = ancre if j is None else j  # jamais en arrière : la carte d'une fiche voisine ramenait l'ancre en tête
            if j is not None:
                ancre, pos = j, max(pos, j)
            continue
        if p in vus:
            continue
        if len(p.split()) < 2 and not re.search(r"\d", p):
            continue  # un mot seul : un bouton, un onglet
        vus.add(p)
        ajouts.setdefault(ancre, []).append(f)
    ajouts.pop(None, None)  # avant le premier bloc gardé : un en-tête, sans ancre
    if not ajouts:
        return texte, 0
    out = []
    for k, l in enumerate(lignes):
        out.append(l)
        out.extend(ajouts.get(k, []))
    return "\n".join(out), sum(len(v) for v in ajouts.values())


# InfoCuria (Cour de justice de l'Union) est une application JavaScript : sa page de document est vide sans navigateur.
# L'application lit le texte à l'API de documents ; le lecteur l'appelle directement (audit sap du 04.11.2026, mainteneur :
# « fais le /sap sur infocuria »). /tabs/document/<juridiction>/<année>/<procédure>/<type>/<fichier-format>.
INFOCURIA_API = "https://infocuriaws.curia.europa.eu/blob/"


def infocuria_api(url: str) -> str | None:
    """L'adresse de l'API qui rend le document d'une page InfoCuria, ou None."""
    m = re.match(
        r"https?://infocuria\.curia\.europa\.eu/tabs/document/([^/]+)/(\d{4})/([^/]+)/[^/]+/([^/?#]+)", url or ""
    )
    if not m:
        return None
    juridiction, annee, procedure, fichier = m.groups()
    fichier = re.sub(r"-(?=[^-]*$)", ".", fichier)
    voie = "download-file" if fichier.lower().endswith(".pdf") else "download-file-html"
    return f"{INFOCURIA_API}{voie}/{juridiction}/{annee}/{procedure.replace('-', '_')}/{fichier}"


MOIS_EN = {
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


def fiche_infocuria(texte: str) -> dict:
    """Titre, date et éditeur d'un document InfoCuria, lus dans son en-tête (« OPINION OF ADVOCATE GENERAL SZPUNAR
    delivered on 3 November 2026 … Case C-250/25 … »). La coquille de l'application n'en porte aucun."""
    tete = " ".join(texte.split()[:80])
    out = {"editeur": "Cour de justice de l'Union européenne"}
    m = re.search(r"delivered on (\d{1,2}) ([A-Za-z]+) (\d{4})", tete)
    if m and m.group(2).lower() in MOIS_EN:
        out["date"] = f"{m.group(3)}-{MOIS_EN[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    genre = re.search(r"(OPINION OF ADVOCATE GENERAL|JUDGMENT OF THE COURT|ORDER OF THE COURT)\s+([A-ZÀ-Ý\-]+)?", tete)
    lignes = [x.strip() for x in texte.split("\n")[:40] if x.strip()]
    numero = re.search(r"Case\s+(C|T)\s*[\u2011‑-]\s*(\d+/\d+)", tete)
    parties = next((f"{lignes[i - 1]} v {lignes[i + 1]}" for i in range(1, len(lignes) - 1) if lignes[i] == "v"), "")
    if genre:
        t = genre.group(1).capitalize().replace("advocate general", "Advocate General")
        if genre.group(1).startswith("OPINION") and genre.group(2):
            t += " " + genre.group(2).capitalize()
        if numero:
            t += f", Case {numero.group(1)}-{numero.group(2)}"
        out["titre"] = t + (f", {parties}" if parties else "")
    return out


def lire(url: str) -> dict:
    """Comme `_lire`, et la fiche d'un document InfoCuria lue dans son texte."""
    p = _lire(url)
    if infocuria_api(url) and p.get("texte"):
        p.update(fiche_infocuria(p["texte"]))
    return p


def editeur_declare(meta) -> str:
    """Le nom que la page déclare (`og:site_name`, lu par trafilatura), ou "" : trafilatura rend le domaine quand la
    page ne déclare rien, et le domaine n'est pas un nom d'éditeur."""
    nom = str(getattr(meta, "sitename", "") or "").strip()
    hote = str(getattr(meta, "hostname", "") or "").lower().removeprefix("www.")
    return (
        "" if not nom or nom.lower().removeprefix("www.") == hote or re.fullmatch(r"[\w.-]+\.[a-z]{2,}", nom) else nom
    )


def _lire(url: str) -> dict:
    """{url, titre, date, texte, notes, mots, voie} — texte principal de la page, notes de bas de page à part ; texte
    vide si rien n'est lisible."""
    titre = date = editeur = ""
    texte = ""
    voie = ""
    try:
        import trafilatura  # noqa: PLC0415
        from trafilatura.settings import use_config  # noqa: PLC0415

        conf = use_config()
        celex = celex_de(url)
        if celex:
            octets, genre, jeu, raison = _obtenir(
                CELLAR + celex[0], {**ENTETES, "Accept": "application/xhtml+xml", "Accept-Language": celex[1]}
            )
        else:
            octets, genre, jeu, raison = _obtenir(infocuria_api(url) or url)
        if raison:
            return {"url": url, "titre": "", "date": "", "texte": "", "notes": "", "mots": 0, "voie": raison}
        if "pdf" in genre or octets[:5] == b"%PDF-":
            texte, _ = nettoyer(_texte_pdf(octets))
            return {
                "url": url,
                "titre": "",
                "date": "",
                "texte": texte,
                "notes": "",
                "mots": len(texte.split()),
                "voie": "pdf (pypdf)"
                if len(texte.split()) >= MOTS_LUE
                else f"page trop courte (pdf, {len(texte.split())} mots)",
            }
        brut = _decoder(octets, jeu)
        if not celex and _mur(brut):
            return {
                "url": url,
                "titre": "",
                "date": "",
                "texte": "",
                "notes": "",
                "mots": 0,
                "voie": "refusée (mur anti-robot)",
            }
        brut = _reparer(brut)
        if brut:
            texte = (
                trafilatura.extract(
                    brut, include_comments=False, include_tables=True, favor_recall=True, url=url, config=conf
                )
                or ""
            )
            texte, repris = rattraper(brut, texte) if texte else (texte, 0)
            meta = trafilatura.extract_metadata(brut, default_url=url)
            if meta:
                titre, date, editeur = meta.title or "", meta.date or "", editeur_declare(meta)
            if celex:  # le titre de Cellar est un nom de fichier (« L_202401689EN.000101.fmx.xml »)
                m = re.search(r'<p class="oj-doc-ti"[^>]*>(.*?)</p>', brut, re.S)
                titre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else titre
            voie = (f"eur-lex (cellar {celex[0]})" if celex else "trafilatura") + (
                f" + {repris} bloc(s) repris" if repris else ""
            )
    except Exception as e:  # noqa: BLE001
        voie = f"trafilatura absente ou en échec ({type(e).__name__})"
    if len(texte.split()) < MOTS_LUE:
        # Repli : notre extracteur (balises article/main/entry-content, méta de date), sans dépendance.
        try:
            from newskit import collecte_rss as cr  # noqa: PLC0415

            n_mots, t2, mur, meta2 = cr.corps_de_page(url)
            if n_mots > len(texte.split()) and not mur:
                texte, voie = t2, "corps_de_page"
                titre = titre or str((meta2 or {}).get("titre") or "")
                date = date or str((meta2 or {}).get("date") or "")
        except Exception:  # noqa: BLE001
            pass
    texte, notes = nettoyer(texte)
    if len(texte.split()) < MOTS_LUE:
        voie = f"page trop courte ({len(texte.split())} mots" + (f", {voie})" if voie else ")")
    return {
        "url": url,
        "titre": titre.strip()[:200],
        "date": date[:10],
        "texte": texte,
        "notes": notes,
        "mots": len(texte.split()),
        "voie": voie,
        **({"editeur": editeur} if editeur else {}),
    }


# --- Une page refusée n'est pas négligée (mainteneur, 04.11.2026) ------------------------------------------------------------
# « l'utilisation de sap devrait être intégrée à la routine normale du reporter, et ne pas négliger un contenu aussi
# facilement » ; « imitation si robots.txt l'admet ». Voies, dans l'ordre de sap (accesspath) : l'imitation de
# l'empreinte d'un navigateur par accesspath, seulement si robots.txt n'exclut pas les robots d'IA ; la copie
# d'archive de la Wayback Machine ; sinon un geste humain (`geste` vrai), que le reporter consigne.
ROBOTS_IA = (
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "anthropic-ai",
    "GPTBot",
    "ChatGPT-User",
    "CCBot",
    "PerplexityBot",
    "Google-Extended",
)
# L'interpréteur d'accesspath : d'abord l'environnement installé hors de ~/Documents, que macOS refuse aux daemons
# (06.11.2026, 23 h 37 : « Operation not permitted » sur .venv/pyvenv.cfg) ; sinon la copie de travail d'accesspath.
# ACCESSPATH_PY, posé, l'emporte (tests).
ACCESSPATH_CANDIDATS = (
    Path.home() / "Agents" / "accesspath-env" / "bin" / "python",
    Path.home() / "Documents" / "accesspath" / ".venv" / "bin" / "python",
)
ACCESSPATH_PY: Path | None = None


def accesspath_py() -> Path:
    if ACCESSPATH_PY is not None:
        return ACCESSPATH_PY
    return next((c for c in ACCESSPATH_CANDIDATS if c.exists()), ACCESSPATH_CANDIDATS[-1])


_IMITER = (
    "import asyncio, sys\nfrom accesspath import _http as h\n"
    "r = asyncio.run(h.aimpersonate_get(sys.argv[1], total_timeout=30))\n"
    "sys.stdout.write(r.content.decode('utf-8', 'replace') if r.status_code == 200 else '')\n"
)


def robots_exclut_ia(url: str) -> bool:
    """Vrai si le robots.txt du site interdit cette adresse à l'un des robots d'IA (ROBOTS_IA). Sans robots.txt
    lisible : faux (rien n'est exclu)."""
    import urllib.parse  # noqa: PLC0415
    import urllib.robotparser  # noqa: PLC0415

    u = urllib.parse.urlsplit(url)
    octets, _, jeu, raison = _obtenir(f"{u.scheme}://{u.netloc}/robots.txt")
    if raison or not octets:
        return False
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(_decoder(octets, jeu).splitlines())
    return any(not rp.can_fetch(a, url) for a in ROBOTS_IA)


def _page_de_html(url: str, brut: str, voie: str) -> dict:
    texte, titre, date, editeur = "", "", "", ""
    try:
        import trafilatura  # noqa: PLC0415

        texte = (
            trafilatura.extract(_reparer(brut), include_comments=False, include_tables=True, favor_recall=True, url=url)
            or ""
        )
        meta = trafilatura.extract_metadata(brut, default_url=url)
        if meta:
            titre, date, editeur = meta.title or "", meta.date or "", editeur_declare(meta)
    except Exception:  # noqa: BLE001
        pass
    texte, notes = nettoyer(texte)
    return {
        "url": url,
        "titre": titre.strip()[:200],
        "date": (date or "")[:10],
        "texte": texte,
        "notes": notes,
        "mots": len(texte.split()),
        "voie": voie,
        **({"editeur": editeur} if editeur else {}),
    }


def imiter(url: str) -> str:
    """Le HTML de la page lu par accesspath avec l'empreinte d'un navigateur (curl_cffi), ou "" (refus, échec,
    accesspath absent)."""
    py = accesspath_py()
    if not py.exists():
        return ""
    try:
        r = subprocess.run(
            [str(py), "-c", _IMITER, url],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(py.parents[2]),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def copie_archive(url: str) -> str:
    """L'adresse brute (« id_ ») de la copie la plus proche à la Wayback Machine, ou ""."""
    import json  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415

    octets, _, jeu, raison = _obtenir("https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe=""))
    if raison or not octets:
        return ""
    try:
        snap = (json.loads(_decoder(octets, jeu)).get("archived_snapshots") or {}).get("closest") or {}
    except ValueError:
        return ""
    if not snap.get("available") or not snap.get("timestamp"):
        return ""
    return f"https://web.archive.org/web/{snap['timestamp']}id_/{url}"


def lire_autrement(url: str, raison: str) -> dict:
    """Une page refusée (`raison`), relue par les voies de secours ; `geste` vrai si aucune ne la rend lisible."""
    essais = [raison]
    if robots_exclut_ia(url):
        essais.append("robots.txt exclut les robots d'IA : pas d'imitation")
    else:
        html = imiter(url)
        if html:
            p = _page_de_html(url, html, "imitation de navigateur (accesspath)")
            if p["mots"] >= MOTS_LUE:
                return p
        essais.append("imitation de navigateur sans texte")
    a = copie_archive(url)
    if a:
        octets, _, jeu, r = _obtenir(a)
        if not r and octets:
            p = _page_de_html(url, _decoder(octets, jeu), f"copie d'archive ({a})")
            if p["mots"] >= MOTS_LUE:
                return p
        essais.append("copie d'archive illisible")
    else:
        essais.append("aucune copie d'archive")
    return {
        "url": url,
        "titre": "",
        "date": "",
        "texte": "",
        "notes": "",
        "mots": 0,
        "voie": " ; ".join(essais),
        "geste": True,
    }


# Tri des paragraphes pour le modèle, sans modèle (03.11.2026, décision du porteur). Le texte lu reste entier (`lire`,
# sans plafond) ; ce tri choisit seulement ce que voit le modèle : les paragraphes qui recoupent le plus les mots des
# questions, plus le titre, le premier paragraphe et tout paragraphe dont un nombre ou une date est proche d'un mot des
# questions, rendus dans l'ordre d'origine jusqu'au budget de mots. Mesures du 03.11.2026 sur les trois dossiers du
# reporter (CHANTIER_LECTEUR_WEB.md). Avec le lecteur complété (blocs repris, EUR-Lex par Cellar), 36 citations se
# retrouvent dans le texte entier ; le tri en garde, sans termes du graphe, 27 à 1 000 mots, 32 à 2 000, 34 à 3 000,
# 35 (97 %) à 4 000 ; avec les termes, 28, 34, 34, 34, et 35 seulement à 8 000. D'où 4 000, le plus petit budget qui
# garde au moins 95 % des citations ; la perte restante est l'AI Act entier (92 409 mots).
BUDGET_EXTRAIT = 4000
PROCHE = 10  # un nombre « proche » : à dix mots au plus d'un mot des questions
MOTS_PARAGRAPHE = 12  # le premier paragraphe : la première ligne d'au moins douze mots
MOTS_VIDES = set(
    """
a an and are as at be been but by can could did do does for from had has have how if in into is it its may might not
of on or our should so than that the their them then there these they this those to was we were what when where which
who whom whose why will with would yes no also more most other such only very about after before over under between
au aux avec ce ces cet cette dans de des du elle elles en est et etre eu il ils je la le les leur leurs lui mais me
meme ne ni nous on ont ou par pas plus pour qu que quel quelle quelles quels qui sa sans se ses si son sont sur ta te
tes toi ton tous tout tres tu un une vers vos votre vous y ete etait sera fait faut chez entre sous depuis comme
der die das den dem des ein eine einer eines einem und oder ist sind war wird werden mit fur von zu zum zur auf aus
bei im ins nach nicht noch nur auch als wie wer was wann warum welche welcher dass sich es sie er wir ihr
het een en van op te dat die zijn voor met niet aan om ook als bij door naar wordt worden hoe wat wie waarom welke
il lo gli della delle dei degli nel nella per con non che sono una uno come anche piu dal dalla al alla
el los las del para por con una uno como mas pero sus esta este estos estas son fue ser hay que cual cuales cuando
""".split()
)
RE_NOMBRE = re.compile(r"\d")


def _cles(texte: str) -> list[str]:
    """Les mots pliés d'un texte, mots vides retirés ; un mot long se réduit à ses cinq premières lettres (« candidats »
    et « candidature », « procédures » et « procedures » se rejoignent d'une langue à l'autre)."""
    out = []
    for m in _plier(texte).split():
        if m.isdigit():
            out.append(m)
            continue
        if len(m) < 3 or m in MOTS_VIDES:
            out.append("")
            continue
        out.append(m[:5])
    return out


# Tri bilingue (03.11.2026, décision du porteur) : les questions du reporter sont en français, les pages en anglais,
# en allemand, en italien. Les libellés anglais des notions du graphe qui touchent les questions (les noms propres
# des questions, cherchés comme `interroger_graphe.py chercher`, et leurs voisines) s'ajoutent aux mots des questions.
TERMES_PAR_NOM = 3  # notions retenues par nom propre des questions (les plus reliées)
VOISINES = 6  # voisines retenues par notion (les plus reliées)
MOTS_LIBELLE = 5  # un libellé plus long est une affaire, pas une notion (« Biancardi c. Italie… »)
RE_NOM_PROPRE = re.compile(
    r"(?<![\w-])(?:[A-Z][\w&/.-]*[A-Za-z0-9]|[A-Z]{2,})(?:[ -](?:[A-Z][\w&/.-]*[A-Za-z0-9]|"
    r"[A-Z]{2,}|\d[\w/]*))*"
)
MOTS_DE_PHRASE = {
    "qui",
    "que",
    "quel",
    "quels",
    "quelle",
    "quelles",
    "quand",
    "combien",
    "comment",
    "pourquoi",
    "est",
    "le",
    "la",
    "les",
    "l",
    "un",
    "une",
    "des",
    "du",
    "de",
    "depuis",
    "y",
    "cela",
    "ce",
    "a",
    "il",
    "elle",
    "sujet",
    "hypothese",
    "questions",
    "question",
    "selon",
    "sources",
    "source",
    "en",
    "et",
    "ou",
    "si",
}


def termes_du_graphe(questions: list[str], graphe: str | Path) -> list[str]:
    """Les libellés des notions du graphe (ni auteurs, ni fiches) que nomment les questions, et ceux de leurs voisines
    les plus reliées. Un graphe absent ou illisible ne donne aucun terme."""
    try:
        from newskit import interroger_graphe as ig  # noqa: PLC0415

        chemin = (
            Path(graphe) if str(graphe).endswith(".json") or "/" in str(graphe) else ig._graphe_ou_chemin(str(graphe))
        )
        from networkx.readwrite import json_graph  # noqa: PLC0415
        import json  # noqa: PLC0415

        G = json_graph.node_link_graph(json.loads(chemin.read_text(encoding="utf-8")), edges="links")
    except Exception:  # noqa: BLE001
        return []
    notion = {n for n, d in G.nodes(data=True) if not d.get("kind")}
    par_libelle: dict[str, list[str]] = {}
    for n in notion:
        par_libelle.setdefault(_plier(str(G.nodes[n].get("label") or n)), []).append(n)
    noms = []
    for q in questions or []:
        for m in RE_NOM_PROPRE.finditer(q):
            mots = _plier(m.group(0)).split()
            while mots and mots[0] in MOTS_DE_PHRASE:  # « La Bundesnetzagentur » en tête de phrase
                mots.pop(0)
            while mots and mots[-1] in MOTS_DE_PHRASE:
                mots.pop()
            if mots and re.search(r"[a-z]{2}", " ".join(mots)):
                # Un mot seul et ordinaire (« Italie ») ne se cherche qu'à l'identique ; un sigle, une marque ou un nom
                # de plusieurs mots (« EuroHPC », « KI-MIG », « AI Act ») aussi dans les libellés qui le contiennent.
                large = len(mots) > 1 or bool(re.search(r"[A-Z].*[A-Z]|\d", m.group(0)))
                noms.append((" ".join(mots), large))
    termes: list[str] = []

    def ajouter(n):
        lib = str(G.nodes[n].get("label") or n)
        if lib not in termes:
            termes.append(lib)

    for nom, large in dict.fromkeys(noms):
        trouves = par_libelle.get(nom) or (
            [
                n
                for lib, ns in par_libelle.items()  # le nom exact d'abord
                if len(lib.split()) <= MOTS_LIBELLE and f" {nom} " in f" {lib} "
                for n in ns
            ]
            if large
            else []
        )
        for n in sorted(trouves, key=lambda x: -G.degree(x))[:TERMES_PAR_NOM]:
            ajouter(n)
            for v in sorted((v for v in G.neighbors(n) if v in notion), key=lambda x: -G.degree(x))[:VOISINES]:
                ajouter(v)
    return termes


def extrait_utile(
    texte: str, questions: list[str], budget_mots: int = BUDGET_EXTRAIT, termes: list[str] | None = None
) -> str:
    """Les paragraphes de `texte` utiles aux `questions` (et aux `termes` du graphe), dans l'ordre d'origine, en
    `budget_mots` mots au plus (le titre et le premier paragraphe entrent toujours). Un texte qui tient dans le budget
    est rendu tel quel."""
    if len((texte or "").split()) <= budget_mots:
        return texte
    lignes = [l for l in texte.split("\n") if l.strip()]
    q = {c for c in _cles(" ".join((questions or []) + (termes or []))) if c and not c.isdigit()}
    garde: set[int] = {0} if lignes else set()
    premier = next((i for i, l in enumerate(lignes) if len(l.split()) >= MOTS_PARAGRAPHE), None)
    if premier is not None:
        garde.add(premier)
    total = sum(len(lignes[i].split()) for i in garde)
    rangs = []
    for i, l in enumerate(lignes):
        if i in garde:
            continue
        cles = _cles(l)
        score = len(q.intersection(cles))
        if not score:
            continue
        pos_q = [k for k, c in enumerate(cles) if c in q]
        pos_n = [k for k, c in enumerate(cles) if c and RE_NOMBRE.search(c)]
        proche = any(abs(a - b) <= PROCHE for a in pos_n for b in pos_q)
        rangs.append((not proche, -score, i))
    for _, _, i in sorted(rangs):
        n = len(lignes[i].split())
        if total + n <= budget_mots:
            garde.add(i)
            total += n
    return "\n".join(lignes[i] for i in sorted(garde))


def lire_plusieurs(urls: list[str]) -> list[dict]:
    """Les pages lisibles (au moins 80 mots), dans l'ordre donné."""
    out = []
    for u in urls:
        p = lire(u)
        if p["mots"] >= MOTS_LUE:
            out.append(p)
    return out


def citation_dans(citation: str, texte: str | dict) -> bool:
    """La citation figure-t-elle mot pour mot dans le texte (ponctuation, casse et accents neutralisés) ? Une citation
    de moins de six mots ne prouve rien. `texte` peut être une page de `lire` : son texte, puis ses notes, chacun à
    part (une citation ne chevauche pas la coupure)."""
    c = _plier(re.sub(r"\((https?://[^)\s]+)\)\s*$", "", citation or ""))
    if len(c.split()) < 6:
        return False
    morceaux = [texte.get("texte") or "", texte.get("notes") or ""] if isinstance(texte, dict) else [texte or ""]
    return any(c in _plier(m) for m in morceaux if m)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for u in sys.argv[1:]:
        p = lire(u)
        print(f"{p['url']}\n  {p['titre']!r}  {p['date']}  {p['mots']} mots  ({p['voie']})\n  {p['texte'][:400]!r}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Une page par fiche, à une adresse lisible (mainteneur, 13.11.2026 : « l'URL est à formater en langage naturel pour le
SEO avec un fonctionnement scalable. Le titre de l'URL doit être celui de la fiche, pas du site » ; « la diffusion du
lien met en avant le contenu de la fiche »).

Adresse : /<langue>/<titre de la fiche en minuscules, sans accents, mots joints par des tirets>-<identifiant>.
L'identifiant est celui du site (FNV-1a 32 bits de l'adresse de la source, en base 36) : le serveur retrouve la fiche
par lui seul, d'une seule règle, quel que soit le nombre de fiches ; un titre réécrit change l'adresse, l'ancienne mène
encore à la fiche et son lien canonique nomme la nouvelle.

Pour chaque billet de la copie publique (publier_fil), le Mac dépose sur le serveur, dans DOSSIER_VPS :
- <langue>/<id>.head : l'en-tête de la page (titre, description, lien canonique, liens entre les langues, aperçu
  og: et twitter:, données structurées NewsArticle) ; noindex pour un billet sans fiche développée (mainteneur,
  13.11.2026 : « ok pour 1 et 2 ») ;
- <langue>/<id>.html : la fiche en HTML simple, lisible sans script (lynx, robots) : un h1, le chapeau, la date,
  un h2 par élément, la source ;
- cartes/marque-<langue>.png : la carte d'aperçu, 1200 × 630, le groupe logo du média seul (mainteneur, 13.11.2026 :
  « la carte de l'article devrait se limiter au groupe logo » ; dans un fil réduit, un titre dans l'image devient
  illisible, le réseau l'écrit dessous) ; une par langue, la même pour toutes les fiches ; dessinée par Pillow ;
- plan_fiches.xml : le plan des fiches développées, celles sorties du registre comprises.
Les fichiers restent sur le serveur : une fiche sortie du fil garde sa page. La page elle-même est celle du site, qui
inclut l'en-tête et l'article de la fiche (inclusion côté serveur, réglée par le média).

Configuration du média (config.yaml) :
    pages_fiches:
      marque: <fichier YAML de la marque de la carte, relatif au dossier du média>
La marque (chemins relatifs à son fichier) :
    logo: <SVG de la marque en points : viewBox et cercles>
    polices: {nom: [<ttf>, <graisse>], sous: [...]}
    couleurs: {fond, marque, accent, discret}
    nom: [<nom>, <suffixe en couleur d'accent>]
    textes: {fr: {sous}, en: {sous}}

Usage :
    python3 -m newskit.pages_fiches [--graphe <graphe du site>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import re
import functools
import subprocess
import sys
import tarfile
import time
import unicodedata
from pathlib import Path

import yaml
from PIL import Image, ImageColor, ImageDraw, ImageFont

from newskit.commun import RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)
from newskit import publier_fil  # noqa: E402 — la copie publique et l'accès au serveur
from newskit import reglages  # noqa: E402 — les réglages du média

LANGUES = ("fr", "en")
SLUG_MAX = 90  # caractères du texte de l'adresse, coupé entre deux mots
VERSION_CARTE = 10  # signes de l'empreinte de la carte dans son adresse
DESCRIPTION_MAX = 160  # la description qu'un moteur de recherche affiche
DOSSIER_VPS = "/var/www/kg-mcp/fiches"
LARGEUR, HAUTEUR = 1200, 630
SUR = 2  # la carte se dessine au double puis se réduit : bords des points et des lettres lissés
LOGO_H = 116  # hauteur de la marque à l'échelle 1 (le bandeau du site, au double)
LOGO_ECHELLE = 2.4  # le groupe logo grandi : lisible dans un fil réduit à 400 px
NOM_DECALE = 60 / 58  # le nom commence à 60 px du bord de la marque haute de 58 px (bandeau du site, 9.11.2026)
MARQUE_CLES = {"logo", "polices", "couleurs", "nom", "textes"}
POLICES = {"nom", "sous"}
COULEURS = {"fond", "marque", "accent", "discret"}
TEXTES = {"sous"}
MOIS = {
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}
LOCALES = {"fr": "fr_FR", "en": "en_US"}
D_APRES = {"fr": "D’après", "en": "Based on"}
LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss", "ø": "o", "Ø": "o", "đ": "d"})


def ident(url: str) -> str:
    """L'identifiant du site : FNV-1a 32 bits sur les points de code de l'adresse, en base 36 (le script du site)."""
    h = 2166136261
    for c in url:
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    chiffres = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    while True:
        h, r = divmod(h, 36)
        s = chiffres[r] + s
        if not h:
            return s


def slug(titre: str) -> str:
    """Le texte de l'adresse : minuscules, sans accents, mots joints par des tirets, coupé entre deux mots."""
    t = unicodedata.normalize("NFKD", str(titre).translate(LIGATURES))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    mots = re.findall(r"[a-z0-9]+", t)
    s = ""
    for m in mots:
        if len(s) + len(m) + (1 if s else 0) > SLUG_MAX:
            break
        s = f"{s}-{m}" if s else m
    return s or (mots[0][:SLUG_MAX] if mots else "")


def _titre(p: dict, langue: str) -> str:
    return p["titre"] if langue == "fr" else p["titre_en"]


def _chapeau(p: dict, langue: str) -> str:
    return p["descriptif"] if langue == "fr" else p["descriptif_en"]


def adresse(p: dict, langue: str) -> str:
    """/<langue>/<texte du titre>-<identifiant>."""
    return f"/{langue}/{slug(_titre(p, langue)) or 'fiche'}-{ident(p['url'])}"


def developpee(p: dict, langue: str) -> bool:
    """Vrai si la fiche est développée dans cette langue (résumé ou éléments) : seule celle-là s'indexe."""
    s = "" if langue == "fr" else "_en"
    return bool(p.get("resume" + s) or p.get("elements" + s))


def date_longue(d: str, langue: str) -> str:
    try:
        x = dt.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d)
    return f"{x.day} {MOIS[langue][x.month - 1]} {x.year}"


def description(texte: str) -> str:
    """Le chapeau, coupé entre deux mots sous DESCRIPTION_MAX caractères."""
    t = " ".join(str(texte).split())
    if len(t) <= DESCRIPTION_MAX:
        return t
    coupe = t[: DESCRIPTION_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return coupe + "…"


def carte_url(langue: str, site: str, version: str = "") -> str:
    """L'adresse de la carte ; `version` la change quand la carte change (les réseaux gardent une image par adresse)."""
    return f"{site}/fiches/cartes/marque-{langue}.png" + (f"?v={version}" if version else "")


def version_carte(png: bytes) -> str:
    """La version de la carte : l'empreinte de l'image même."""
    return hashlib.sha256(png).hexdigest()[:VERSION_CARTE]


def _e(x: str) -> str:
    return html.escape(str(x), quote=True)


def tete(p: dict, langue: str, site: str, version: str = "") -> str:
    """L'en-tête de la page de la fiche : titre, description, canonique, langues, aperçu, données structurées."""
    titre, chap = _titre(p, langue), description(_chapeau(p, langue))
    ici, image = site + adresse(p, langue), carte_url(langue, site, version)
    media = {"@type": "Organization", "name": site.split("://", 1)[-1], "url": site + "/"}
    donnees = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": titre,
        "description": chap,
        "inLanguage": langue,
        "datePublished": p.get("date", ""),
        "dateModified": p.get("ajoute") or p.get("date", ""),
        "image": [image],
        "mainEntityOfPage": ici,
        "isBasedOn": p.get("lien_" + langue) or p.get("url", ""),
        # Le média signe la synthèse qu'il édite ; l'auteur de l'article d'origine reste cité comme source.
        "author": media,
        "publisher": media,
    }
    ld = json.dumps(donnees, ensure_ascii=False).replace("<", "\\u003c")
    lignes = [
        # Le site d'abord, dans l'onglet (mainteneur, 13.11.2026) ; le titre suit, propre à chaque page pour les moteurs.
        f"<title>{_e(media['name'])} · {_e(titre)}</title>",
        f'<meta name="description" content="{_e(chap)}">',
        f'<meta name="author" content="{_e(media["name"])}">',
        *([] if developpee(p, langue) else ['<meta name="robots" content="noindex, follow">']),
        f'<link rel="canonical" href="{_e(ici)}">',
        *(f'<link rel="alternate" hreflang="{lg}" href="{_e(site + adresse(p, lg))}">' for lg in LANGUES),
        f'<link rel="alternate" hreflang="x-default" href="{_e(site + adresse(p, "en"))}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:site_name" content="{_e(site.split("://", 1)[-1])}">',
        f'<meta property="og:locale" content="{LOCALES[langue]}">',
        f'<meta property="og:title" content="{_e(titre)}">',
        f'<meta property="og:description" content="{_e(chap)}">',
        f'<meta property="og:url" content="{_e(ici)}">',
        f'<meta property="og:image" content="{_e(image)}">',
        f'<meta property="og:image:width" content="{LARGEUR}">',
        f'<meta property="og:image:height" content="{HAUTEUR}">',
        f'<meta property="article:published_time" content="{_e(p.get("date", ""))}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_e(titre)}">',
        f'<meta name="twitter:description" content="{_e(chap)}">',
        f'<meta name="twitter:image" content="{_e(image)}">',
        f'<script type="application/ld+json">{ld}</script>',
    ]
    return "\n".join(lignes) + "\n"


def article(p: dict, langue: str, site: str) -> str:
    """La fiche en HTML simple, lisible sans script : un h1, le chapeau, la date, la source, un h2 par élément."""
    s = "" if langue == "fr" else "_en"
    lien = p.get("lien_" + langue) or p.get("url", "")
    morceaux = [
        f'<article lang="{langue}">',
        f"<h1>{_e(_titre(p, langue))}</h1>",
        f"<p>{_e(_chapeau(p, langue))}</p>",
        f'<p><time datetime="{_e(p.get("date", ""))}">{_e(date_longue(p.get("date", ""), langue))}</time> · '
        f'{D_APRES[langue]} <a href="{_e(lien)}">{_e(p.get("editeur") or lien)}</a></p>',
    ]
    if p.get("resume" + s):
        morceaux.append(f"<p>{_e(p['resume' + s])}</p>")
    for e in p.get("elements" + s) or []:
        morceaux.append(f"<h2>{_e(e['champ'])}</h2>")
        morceaux.extend(f"<p>{_e(x)}</p>" for x in str(e["texte"]).split("\n\n") if x.strip())
    morceaux.append("</article>")
    return "\n".join(morceaux) + "\n"


class MarqueInvalide(ValueError):
    pass


def charger_marque(chemin: Path) -> dict:
    """La marque de la carte, vérifiée : polices (chemin absolu, graisse), couleurs RVB, nom, sous-titres, logo."""
    chemin = Path(chemin)
    try:
        m = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise MarqueInvalide(f"{chemin.name} : illisible ({e})") from e
    if not isinstance(m, dict) or set(m) != MARQUE_CLES:
        raise MarqueInvalide(f"{chemin.name} : {sorted(MARQUE_CLES)} attendus")
    base = chemin.parent
    if not isinstance(m["polices"], dict) or set(m["polices"]) != POLICES:
        raise MarqueInvalide(f"{chemin.name} : polices : {sorted(POLICES)} attendues")
    polices = {}
    for k, v in m["polices"].items():
        if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and (base / v[0]).is_file()):
            raise MarqueInvalide(f"{chemin.name} : polices.{k} : [fichier existant, graisse], lu {v!r}")
        polices[k] = (str(base / v[0]), v[1])
    if not isinstance(m["couleurs"], dict) or set(m["couleurs"]) != COULEURS:
        raise MarqueInvalide(f"{chemin.name} : couleurs : {sorted(COULEURS)} attendues")
    try:
        couleurs = {k: ImageColor.getrgb(str(v)) for k, v in m["couleurs"].items()}
    except ValueError as e:
        raise MarqueInvalide(f"{chemin.name} : couleurs : {e}") from e
    if not (isinstance(m["nom"], list) and len(m["nom"]) == 2 and all(isinstance(x, str) for x in m["nom"])):
        raise MarqueInvalide(f"{chemin.name} : nom : [nom, suffixe] attendus")
    t = m["textes"]
    if (
        not isinstance(t, dict)
        or set(t) != set(LANGUES)
        or any(not isinstance(t[lg], dict) or set(t[lg]) != TEXTES for lg in LANGUES)
    ):
        raise MarqueInvalide(f"{chemin.name} : textes : {sorted(TEXTES)} pour {list(LANGUES)}")
    try:
        svg = (base / str(m["logo"])).read_text(encoding="utf-8")
    except OSError as e:
        raise MarqueInvalide(f"{chemin.name} : logo illisible ({e})") from e
    vb = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
    points = [
        (float(c.group(1)), float(c.group(2)), float(c.group(3)))
        for c in re.finditer(r'<circle cx="([0-9.]+)" cy="([0-9.]+)" r="([0-9.]+)"', svg)
    ]
    if not vb or not points:
        raise MarqueInvalide(f"{chemin.name} : logo : un SVG avec viewBox et cercles attendu")
    return {
        "polices": polices,
        "couleurs": couleurs,
        "nom": m["nom"],
        "textes": {lg: {k: str(v) for k, v in t[lg].items()} for lg in LANGUES},
        "logo": (float(vb.group(1)), float(vb.group(2)), points),
    }


@functools.lru_cache(maxsize=64)
def _police(chemin: str, graisse, taille: int):
    """La police à cette taille ; une police variable reçoit sa graisse, les autres axes restent à leur valeur."""
    f = ImageFont.truetype(chemin, taille)
    try:
        axes = f.get_variation_axes()
    except OSError:  # police statique
        return f
    f.set_variation_by_axes([graisse if a["name"] in (b"Weight", "Weight") else a["default"] for a in axes])
    return f


def _base(f, ligne: float) -> float:
    """La ligne de base dans une boîte de hauteur `ligne`, comme CSS : interligne partagé dessus et dessous."""
    asc, desc = f.getmetrics()
    return (ligne - (asc + desc)) / 2 + asc


def _largeur(texte: str, f, interlettre: float) -> float:
    return sum(f.getlength(c) for c in texte) + interlettre * max(len(texte) - 1, 0)


def _ecrire(d, x: float, base: float, texte: str, f, couleur, interlettre: float = 0.0, contour=None) -> float:
    """Le texte lettre à lettre (l'espacement des lettres du site) ; `contour` : (épaisseur, couleur) du halo."""
    for c in texte:
        if contour:
            d.text((x, base), c, font=f, fill=contour[1], anchor="ls", stroke_width=contour[0], stroke_fill=contour[1])
        else:
            d.text((x, base), c, font=f, fill=couleur, anchor="ls")
        x += f.getlength(c) + interlettre
    return x


def dessiner(langue: str, marque: dict) -> bytes:
    """La carte d'aperçu, en PNG LARGEUR × HAUTEUR : le groupe logo du bandeau du site (points, nom, sous-titre dans la
    langue), grandi et centré sur le fond."""
    k, c, P = SUR, marque["couleurs"], marque["polices"]
    W, H = LARGEUR * k, HAUTEUR * k
    im = Image.new("RGB", (W, H), c["fond"])
    d = ImageDraw.Draw(im)
    g = LOGO_ECHELLE
    lw, lh, points = marque["logo"]
    haut_logo = LOGO_H * g
    e = haut_logo / lh
    tn, ts = 25 * g, 23 * g  # le nom et le sous-titre du bandeau, à la même échelle
    f_nom, f_sous = _police(*P["nom"], round(tn * k)), _police(*P["sous"], round(ts * k))
    nom, suffixe, sous = marque["nom"][0].upper(), marque["nom"][1].upper(), marque["textes"][langue]["sous"].upper()
    i_nom, i_sous = 0.18 * tn * k, 0.1 * ts * k
    # Le groupe centré : du point le plus à gauche à la fin de la plus longue ligne, du haut au bas de la marque.
    gauche = min(cx - r for cx, _, r in points) * e
    texte = max(_largeur(nom + suffixe, f_nom, i_nom), _largeur(sous, f_sous, i_sous)) / k
    decale = NOM_DECALE * lh * e
    x0 = (LARGEUR - (decale + texte - gauche)) / 2 - gauche
    haut = (HAUTEUR - haut_logo) / 2
    for cx, cy, r in points:
        d.ellipse(
            ((x0 + (cx - r) * e) * k, (haut + (cy - r) * e) * k, (x0 + (cx + r) * e) * k, (haut + (cy + r) * e) * k),
            fill=c["marque"],
        )
    # Le nom et le sous-titre, comme le bandeau : capitales espacées, suffixe en couleur d'accent, halo couleur de fond.
    xn = (x0 + decale) * k
    haut_bloc = (haut + (haut_logo - (tn * 1.25 + ts * 1.3)) / 2 + 2 * g) * k
    b_nom = haut_bloc + _base(f_nom, tn * 1.25 * k)
    b_sous = haut_bloc + tn * 1.25 * k + _base(f_sous, ts * 1.3 * k)
    halo = (round(3 * g * k), c["fond"])
    _ecrire(d, xn, b_nom, nom + suffixe, f_nom, None, i_nom, halo)
    _ecrire(d, xn, b_sous, sous, f_sous, None, i_sous, halo)
    x = _ecrire(d, xn, b_nom, nom, f_nom, c["marque"], i_nom)
    _ecrire(d, x, b_nom, suffixe, f_nom, c["accent"], i_nom)
    _ecrire(d, xn, b_sous, sous, f_sous, c["discret"], i_sous)
    sortie = io.BytesIO()
    im.resize((LARGEUR, HAUTEUR), Image.LANCZOS).save(sortie, "PNG", optimize=True)
    return sortie.getvalue()


def items(F: dict) -> list[dict]:
    """Les billets publiés (publier_fil), typographie faite, sans la limite de la copie publique : chacun a sa page."""
    publies = (publier_fil.aligne(p) for p in publier_fil._champs(F))
    return [publier_fil._typo(p) for p in publies if p is not None]


def _plan(index: dict, site: str) -> bytes:
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for i in sorted(index, key=lambda k: (index[k]["date"], k), reverse=True):
        x = index[i]
        if not x["indexee"]:
            continue
        alternes = "".join(f'<xhtml:link rel="alternate" hreflang="{lg}" href="{_e(site + x[lg])}"/>' for lg in LANGUES)
        for lg in LANGUES:
            lignes.append(f"<url><loc>{_e(site + x[lg])}</loc><lastmod>{_e(x['date'])}</lastmod>{alternes}</url>")
    lignes.append("</urlset>")
    return ("\n".join(lignes) + "\n").encode()


def _archive(fichiers: dict[str, bytes]) -> bytes:
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as t:
        for nom, donnees in sorted(fichiers.items()):
            info = tarfile.TarInfo(nom)
            info.size, info.mode, info.mtime = len(donnees), 0o644, int(time.time())
            t.addfile(info, io.BytesIO(donnees))
    return tampon.getvalue()


def deposer(billets: list[dict], site: str, cache: Path, rendre, envoyer) -> str:
    """Dépose les pages des fiches nouvelles ou changées ; rend une ligne pour le journal.

    `cache` : l'index des fiches déposées ({id: empreinte, adresses, date, indexée}), qui fait aussi le plan.
    `rendre(langue)` : les octets PNG de la carte de la langue, ou None ; leur empreinte versionne l'adresse de la
    carte dans chaque en-tête, une carte changée refait donc toutes les pages. `envoyer(archive)` : "" si l'archive
    est déposée, sinon l'erreur. Une fiche n'entre à l'index qu'une fois déposée : un échec se refait au passage
    suivant."""
    try:
        index = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    pngs = {lg: rendre(lg) for lg in LANGUES}
    if not all(pngs.values()):
        return "carte non rendue : rien de déposé"
    versions = {lg: version_carte(pngs[lg]) for lg in LANGUES}
    fichiers, neuf = {}, {}
    for p in billets:
        i = ident(p["url"])
        pages = {lg: (tete(p, lg, site, versions[lg]), article(p, lg, site)) for lg in LANGUES}
        empreinte = hashlib.sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if (index.get(i) or {}).get("empreinte") == empreinte:
            continue
        for lg in LANGUES:
            fichiers[f"{lg}/{i}.head"] = pages[lg][0].encode()
            fichiers[f"{lg}/{i}.html"] = pages[lg][1].encode()
        neuf[i] = {
            "empreinte": empreinte,
            **{lg: adresse(p, lg) for lg in LANGUES},
            "date": str(p.get("date", ""))[:10],
            "indexee": developpee(p, "fr") and developpee(p, "en"),
        }
    if not neuf:
        return "inchangé"
    for lg in LANGUES:  # deux petites images : elles partent avec chaque dépôt
        fichiers[f"cartes/marque-{lg}.png"] = pngs[lg]
    fichiers["plan_fiches.xml"] = _plan({**index, **neuf}, site)
    erreur = envoyer(_archive(fichiers))
    if erreur:
        return f"échec du dépôt des pages : {erreur}"
    index.update(neuf)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    return f"{len(neuf)} fiche(s) déposée(s)"


def envoyer_ssh(archive: bytes, env: Path = publier_fil.ENV) -> str:
    """Dépose l'archive dans DOSSIER_VPS par une connexion ssh ; "" si c'est fait, sinon l'erreur."""
    v = publier_fil.acces(env)
    if v is None:
        return f"sans accès au VPS ({env})"
    distant = f"set -e; mkdir -p {DOSSIER_VPS}; tar -C {DOSSIER_VPS} -xf -"
    r = subprocess.run(
        [
            "ssh",
            "-i",
            v["VPS_CLE"],
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            f"{v['VPS_UTILISATEUR']}@{v['VPS_HOTE']}",
            distant,
        ],
        input=archive,
        capture_output=True,
        check=False,
    )
    return f"ssh {r.returncode} : {r.stderr.decode(errors='replace').strip()[:160]}" if r.returncode else ""


def publier(graphe: str) -> str:
    """Les pages des fiches du graphe du site, déposées ; rien si le média ne déclare pas pages_fiches."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import fil  # noqa: PLC0415
    from newskit import media  # noqa: PLC0415

    decl = media.courant().get("pages_fiches")
    site = str(cfg.edition().get("lettre_site") or "").rstrip("/")
    if not decl or not site:
        return "sans pages de fiches (pages_fiches ou lettre_site absent)"
    marque = charger_marque(RACINE / decl["marque"])
    F = json.loads(fil.chemin_fil(graphe).read_text(encoding="utf-8"))
    return deposer(
        items(F),
        site,
        RACINE / "cache" / f"pages_fiches_{graphe}.json",
        lambda langue: dessiner(langue, marque),
        envoyer_ssh,
    )


def main(argv: list[str] | None = None) -> int:
    from newskit import config_sources as cfg  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.graphe_du_site())
    print(publier(ap.parse_args(argv).graphe))
    return 0


reglages.appliquer(__name__, globals())  # les réglages du média (reglages.py)


if __name__ == "__main__":
    sys.exit(main())

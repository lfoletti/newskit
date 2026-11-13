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
- cartes/<id>-<langue>.png : la carte d'aperçu, 1200 × 630, dessinée par Pillow d'après la marque du média (carte A,
  13.11.2026 ; Chrome sans écran échouait sous le daemon) ;
- plan_fiches.xml : le plan des fiches développées, celles sorties du registre comprises.
Les fichiers restent sur le serveur : une fiche sortie du fil garde sa page. La page elle-même est celle du site, qui
inclut l'en-tête et l'article de la fiche (inclusion côté serveur, réglée par le média).

Configuration du média (config.yaml) :
    pages_fiches:
      marque: <fichier YAML de la marque de la carte, relatif au dossier du média>
La marque (chemins relatifs à son fichier) :
    logo: <SVG de la marque en points : viewBox et cercles>
    polices: {titre: [<ttf>, <graisse>], nom: [...], sous: [...], texte: [...]}
    couleurs: {fond, titre, marque, accent, discret, source, filet}
    nom: [<nom>, <suffixe en couleur d'accent>]
    textes: {fr: {sous, pied, d_apres}, en: {sous, pied, d_apres}}

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
MARGE, HAUT, BAS = 72, 56, 52  # marges de la carte (carte A, 13.11.2026)
LOGO_H = 116  # hauteur de la marque sur la carte (le bandeau du site, au double)
NOM_DECALE = 60 / 58  # le nom commence à 60 px du bord de la marque haute de 58 px (bandeau du site, 9.11.2026)
TITRE_TAILLES = (62, 56, 50, 44)  # le titre se réduit jusqu'à tenir en TITRE_LIGNES lignes
TITRE_LIGNES = 4
TITRE_LARGEUR = 1040
MARQUE_CLES = {"logo", "polices", "couleurs", "nom", "textes"}
POLICES = {"titre", "nom", "sous", "texte"}
COULEURS = {"fond", "titre", "marque", "accent", "discret", "source", "filet"}
TEXTES = {"sous", "pied", "d_apres"}
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


def carte_url(p: dict, langue: str, site: str, version: str = "") -> str:
    """L'adresse de la carte ; `version` la change quand la carte change (les réseaux gardent une image par adresse)."""
    return f"{site}/fiches/cartes/{ident(p['url'])}-{langue}.png" + (f"?v={version}" if version else "")


def version_carte(champs: dict, empreinte_marque: str = "") -> str:
    """La version de la carte : ce qu'elle montre et la marque qui la dessine."""
    donnees = json.dumps([champs, empreinte_marque], ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(donnees).hexdigest()[:VERSION_CARTE]


def _e(x: str) -> str:
    return html.escape(str(x), quote=True)


def tete(p: dict, langue: str, site: str, version: str = "") -> str:
    """L'en-tête de la page de la fiche : titre, description, canonique, langues, aperçu, données structurées."""
    titre, chap = _titre(p, langue), description(_chapeau(p, langue))
    ici, image = site + adresse(p, langue), carte_url(p, langue, site, version)
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
        "publisher": {"@type": "Organization", "name": site.split("://", 1)[-1], "url": site + "/"},
    }
    ld = json.dumps(donnees, ensure_ascii=False).replace("<", "\\u003c")
    lignes = [
        f"<title>{_e(titre)} · {_e(site.split('://', 1)[-1])}</title>",
        f'<meta name="description" content="{_e(chap)}">',
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


def champs_carte(p: dict, langue: str, rubriques_en: dict | None = None) -> dict:
    """Ce que la carte d'aperçu montre : le titre de la fiche, la rubrique et la date dans la langue, la source."""
    rub = p.get("rubrique", "")
    if langue == "en":
        rub = (rubriques_en or {}).get(rub, rub)
    return {
        "titre": _titre(p, langue),
        "rubrique": rub,
        "date": date_longue(p.get("date", ""), langue),
        "source": p.get("editeur", ""),
    }


class MarqueInvalide(ValueError):
    pass


def charger_marque(chemin: Path) -> dict:
    """La marque de la carte, vérifiée : polices (chemin absolu, graisse), couleurs RVB, nom, textes, points du logo."""
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


def _cap(f) -> float:
    return -f.getbbox("H", anchor="ls")[1]


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


def couper(texte: str, f, largeur: float) -> list[str]:
    """Les lignes du texte sous `largeur`, coupées entre deux mots ; un mot plus long reste entier."""
    lignes: list[str] = []
    for mot in str(texte).split():
        if lignes and f.getlength(lignes[-1] + " " + mot) <= largeur:
            lignes[-1] += " " + mot
        else:
            lignes.append(mot)
    return lignes


def titre_carte(titre: str, marque: dict, place: float) -> tuple[int, list[str]]:
    """(taille, lignes) du titre : la plus grande taille qui tient en TITRE_LIGNES lignes et dans `place` (px)."""
    for taille in TITRE_TAILLES:
        lignes = couper(titre, _police(*marque["polices"]["titre"], taille * SUR), TITRE_LARGEUR * SUR)
        if len(lignes) <= TITRE_LIGNES and len(lignes) * taille * 1.08 <= place:
            return taille, lignes
    if len(lignes) > TITRE_LIGNES:
        lignes = lignes[: TITRE_LIGNES - 1] + [" ".join(lignes[TITRE_LIGNES - 1 :])]
    return taille, lignes


def dessiner(champs: dict, langue: str, marque: dict) -> bytes:
    """La carte d'aperçu A (13.11.2026), en PNG LARGEUR × HAUTEUR : en haut la marque et le nom comme le bandeau du
    site, la rubrique et la date calées sur le haut du nom ; le titre au milieu ; la source et le pied sous un filet."""
    k, c, P, T = SUR, marque["couleurs"], marque["polices"], marque["textes"][langue]
    W, H = LARGEUR * k, HAUTEUR * k
    im = Image.new("RGB", (W, H), c["fond"])
    d = ImageDraw.Draw(im)
    # La marque : son point le plus à gauche sur la marge, comme le titre (13.11.2026 : « aligner groupe logo à gauche »).
    lw, lh, points = marque["logo"]
    e = LOGO_H / lh
    x0 = MARGE - min(cx - r for cx, _, r in points) * e
    for cx, cy, r in points:
        d.ellipse(
            ((x0 + (cx - r) * e) * k, (HAUT + (cy - r) * e) * k, (x0 + (cx + r) * e) * k, (HAUT + (cy + r) * e) * k),
            fill=c["marque"],
        )
    # Le nom et le sous-titre, comme le bandeau : capitales espacées, suffixe en couleur d'accent, halo couleur de fond.
    f_nom, f_sous, f_txt = _police(*P["nom"], 25 * k), _police(*P["sous"], 23 * k), _police(*P["texte"], 19 * k)
    xn = (x0 + NOM_DECALE * lh * e) * k
    haut_bloc = (HAUT + (LOGO_H - (25 * 1.25 + 23 * 1.3)) / 2 + 2) * k
    b_nom = haut_bloc + _base(f_nom, 25 * 1.25 * k)
    b_sous = haut_bloc + 25 * 1.25 * k + _base(f_sous, 23 * 1.3 * k)
    nom, suffixe, sous = marque["nom"][0].upper(), marque["nom"][1].upper(), T["sous"].upper()
    i_nom, i_sous = 0.18 * 25 * k, 0.1 * 23 * k
    halo = (3 * k, c["fond"])
    _ecrire(d, xn, b_nom, nom + suffixe, f_nom, None, i_nom, halo)
    _ecrire(d, xn, b_sous, sous, f_sous, None, i_sous, halo)
    x = _ecrire(d, xn, b_nom, nom, f_nom, c["marque"], i_nom)
    _ecrire(d, x, b_nom, suffixe, f_nom, c["accent"], i_nom)
    _ecrire(d, xn, b_sous, sous, f_sous, c["discret"], i_sous)
    # La rubrique et la date, à droite, le haut de leurs capitales sur celui du nom (13.11.2026).
    rd, i_rd = f"{champs['rubrique']} · {champs['date']}", 0.02 * 19 * k
    b_rd = b_nom - _cap(f_nom) + _cap(f_txt)
    _ecrire(d, W - MARGE * k - _largeur(rd, f_txt, i_rd), b_rd, rd, f_txt, c["discret"], i_rd)
    # Le pied : un filet, la source à gauche, le pied du média à droite.
    filet = H - (BAS + 19 * 1.3 + 18 + 1) * k
    d.rectangle((MARGE * k, filet, W - MARGE * k, filet + k - 1), fill=c["filet"])
    b_pied = filet + (1 + 18) * k + _base(f_txt, 19 * 1.3 * k)
    _ecrire(d, MARGE * k, b_pied, f"{T['d_apres']} {champs['source']}", f_txt, c["source"])
    _ecrire(d, W - MARGE * k - _largeur(T["pied"], f_txt, 0), b_pied, T["pied"], f_txt, c["discret"])
    # Le titre, centré entre le haut et le pied.
    tete_bas = (HAUT + LOGO_H) * k
    taille, lignes = titre_carte(champs["titre"], marque, (filet - tete_bas) / k - 48)
    f_t, lh = _police(*P["titre"], taille * k), taille * 1.08 * k
    y = tete_bas + ((filet - tete_bas) - len(lignes) * lh) / 2
    for ligne in lignes:
        d.text((MARGE * k, y + _base(f_t, lh)), ligne, font=f_t, fill=c["titre"], anchor="ls")
        y += lh
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


def deposer(
    billets: list[dict],
    site: str,
    cache: Path,
    rendre,
    envoyer,
    rubriques_en: dict | None = None,
    empreinte_marque: str = "",
) -> str:
    """Dépose les pages des fiches nouvelles ou changées ; rend une ligne pour le journal.

    `cache` : l'index des fiches déposées ({id: empreinte, adresses, date, indexée}), qui fait aussi le plan.
    `rendre(champs, langue)` : les octets PNG de la carte, ou None ; `empreinte_marque` : une marque changée refait
    toutes les cartes. `envoyer(archive)` : "" si l'archive est déposée, sinon
    l'erreur. Une fiche n'entre à l'index qu'une fois déposée : un échec se refait au passage suivant."""
    try:
        index = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    fichiers, neuf, sans_carte = {}, {}, 0
    for p in billets:
        i = ident(p["url"])
        cartes = {lg: champs_carte(p, lg, rubriques_en) for lg in LANGUES}
        pages = {
            lg: (tete(p, lg, site, version_carte(cartes[lg], empreinte_marque)), article(p, lg, site), cartes[lg])
            for lg in LANGUES
        }
        empreinte = hashlib.sha256(
            json.dumps([pages, empreinte_marque], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if (index.get(i) or {}).get("empreinte") == empreinte:
            continue
        pngs = {lg: rendre(pages[lg][2], lg) for lg in LANGUES}
        if not all(pngs.values()):
            sans_carte += 1
            continue
        for lg in LANGUES:
            fichiers[f"{lg}/{i}.head"] = pages[lg][0].encode()
            fichiers[f"{lg}/{i}.html"] = pages[lg][1].encode()
            fichiers[f"cartes/{i}-{lg}.png"] = pngs[lg]
        neuf[i] = {
            "empreinte": empreinte,
            **{lg: adresse(p, lg) for lg in LANGUES},
            "date": str(p.get("date", ""))[:10],
            "indexee": developpee(p, "fr") and developpee(p, "en"),
        }
    rate = f" ; {sans_carte} carte(s) non rendue(s)" if sans_carte else ""
    if not neuf:
        return f"inchangé{rate}" if not sans_carte else rate.lstrip(" ;")
    fichiers["plan_fiches.xml"] = _plan({**index, **neuf}, site)
    erreur = envoyer(_archive(fichiers))
    if erreur:
        return f"échec du dépôt des pages : {erreur}{rate}"
    index.update(neuf)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    return f"{len(neuf)} fiche(s) déposée(s){rate}"


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


def rubriques_anglaises(graphe: str) -> dict[str, str]:
    """{rubrique française: anglaise}, d'après les racines de l'arbre des fiches du graphe ; {} sans arbre."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    chemin = cfg.graphe_declare(graphe).get("arbre_fiches")
    if not chemin:
        return {}
    A = yaml.safe_load((RACINE / chemin).read_text(encoding="utf-8")) or {}
    return {str(r["fr"]): str(r.get("en") or r["fr"]) for r in A.get("racines") or []}


def publier(graphe: str) -> str:
    """Les pages des fiches du graphe du site, déposées ; rien si le média ne déclare pas pages_fiches."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import fil  # noqa: PLC0415
    from newskit import media  # noqa: PLC0415

    decl = media.courant().get("pages_fiches")
    site = str(cfg.edition().get("lettre_site") or "").rstrip("/")
    if not decl or not site:
        return "sans pages de fiches (pages_fiches ou lettre_site absent)"
    chemin = RACINE / decl["marque"]
    marque = charger_marque(chemin)
    empreinte = hashlib.sha256(chemin.read_bytes()).hexdigest()
    F = json.loads(fil.chemin_fil(graphe).read_text(encoding="utf-8"))
    return deposer(
        items(F),
        site,
        RACINE / "cache" / f"pages_fiches_{graphe}.json",
        lambda champs, langue: dessiner(champs, langue, marque),
        envoyer_ssh,
        rubriques_anglaises(graphe),
        empreinte,
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

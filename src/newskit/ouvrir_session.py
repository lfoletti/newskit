#!/usr/bin/env python3
"""Ouvre une session par script, pour les sites qui se connectent par un lien envoyé par courriel.

Ghost n'a pas de mot de passe : pour entrer, on donne une adresse, le site y
envoie un lien, et suivre ce lien pose le cookie de session
(`ghost-members-ssr`, valable environ six mois). À la main, cela voulait dire
ouvrir un navigateur, copier l'en-tête `cookie` et le déposer au trousseau —
et le refaire à chaque expiration, depuis une session ouverte à l'écran.

Substack fait de même (`ouverture: substack`) : le message porte un bouton
« Verify email » dont le lien, suivi de redirection en redirection, pose
`connect.sid` sur le domaine propre de la publication — c'est ce cookie-là,
et non `substack.sid` sur substack.com, qui ouvre les billets payants.
Mesuré le 02.10.2026 sur Ahead of AI : 290 499 octets pour un billet payant
avec lui, 21 899 sans. Le message arrive dans le dossier Spam de la boîte
(classé ainsi par l'hébergeur) : la boîte de réception et le Spam sont lus.

Un seul message suffit pour tout Substack. Le même lien pose aussi
`substack.sid` sur substack.com : c'est la session du **compte**, et
substack.com sait en dériver celle d'une publication sans rien envoyer
(`/sign-in?for_pub=…` renvoie vers le domaine propre avec un jeton). Le compte
est donc rangé à part (`substack_compte`), et chaque publication s'ouvre
d'abord par dérivation ; le message n'est redemandé que si le compte ne passe
plus, et toujours pour la publication de référence, la seule dont
l'expéditeur doit être transféré vers la boîte.

Ce script fait la même chose sans personne :

    1. il demande le lien au site pour l'adresse de la boîte dédiée ;
    2. il attend le message dans cette boîte (IMAP) et y lit le lien ;
    3. il suit le lien et garde le cookie de session ;
    4. il éprouve la session par la sonde du registre ;
    5. il la range dans `~/.secrets/sessions/<nom>` (600), où un daemon la lit.

La boîte est **dédiée** (décision de l'utilisateur, 02.10.2026) : le script ne
lit jamais une boîte personnelle. Quand le compte du site est rattaché à une
autre adresse (`adresse:` au registre), le lien y est envoyé, et un filtre de
transfert côté messagerie le fait suivre vers la boîte dédiée — le script ne
lit toujours que celle-ci. Ses accès sont dans `~/.secrets/boite_sessions.env` :

    IMAP_HOTE=mail.infomaniak.com
    IMAP_UTILISATEUR=sessions@exemple.ch
    IMAP_MOT_DE_PASSE=…
    # facultatif : IMAP_PORT=993, ADRESSE=… si l'adresse du compte diffère de l'identifiant

Une session ne s'ouvre ainsi que si le registre le dit (`ouverture: ghost`,
`site`, `fichier: true` dans credences.sessions.<nom>).

Usage :
    python3 -m newskit.ouvrir_session <session>            # ouvre, éprouve, range
    python3 -m newskit.ouvrir_session <session> --creer    # première fois : crée le compte gratuit à cette adresse
    python3 -m newskit.ouvrir_session --si-besoin [--graphe <graphe>]   # n'ouvre que les sessions qui ne passent plus
    python3 -m newskit.ouvrir_session <session> --etat     # éprouve la session rangée, n'appelle rien d'autre

Aucune valeur n'est affichée : ni le lien, ni le cookie, ni le mot de passe de la boîte.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import html
import http.cookiejar
import imaplib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import commun  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402

BOITE = Path.home() / ".secrets" / "boite_sessions.env"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
COOKIES_GHOST = ("ghost-members-ssr", "ghost-members-ssr.sig")
COOKIES_SUBSTACK = ("connect.sid", "substack.sid")
COMPTE_SUBSTACK = "substack_compte"  # la session du compte, au registre
DOSSIERS_BOITE = ("INBOX", "Spam", "Junk")  # un message de connexion est parfois classé indésirable
ATTENTE = 180  # secondes à attendre le message
PAS = 10


class Echec(Exception):
    """Une étape n'a pas abouti ; le message dit laquelle et pourquoi, sans rien de secret."""


# --- Registre et boîte -----------------------------------------------------


def declaration(nom: str) -> dict:
    s = (cfg.charger()["credences"].get("sessions") or {}).get(nom)
    if not s:
        raise Echec(f"session « {nom} » absente du registre")
    if s.get("ouverture") not in ("ghost", "substack") or not s.get("site") or not s.get("fichier"):
        raise Echec(
            f"session « {nom} » : le registre ne déclare pas d'ouverture par script "
            f"(ouverture: ghost ou substack, site, fichier: true)"
        )
    if s["ouverture"] == "substack" and not s.get("publication"):
        raise Echec(f"session « {nom} » : `publication` (le sous-domaine Substack) manque au registre")
    return s


def lire_boite(chemin: Path = BOITE) -> dict:
    if not chemin.exists():
        raise Echec(f"boîte dédiée non configurée : {chemin} absent (IMAP_HOTE, IMAP_UTILISATEUR, IMAP_MOT_DE_PASSE)")
    conf = {}
    for l in chemin.read_text(encoding="utf-8").splitlines():
        if "=" in l and not l.lstrip().startswith("#"):
            k, v = l.split("=", 1)
            conf[k.strip()] = v.strip().strip("\"'")
    manquants = [k for k in ("IMAP_HOTE", "IMAP_UTILISATEUR", "IMAP_MOT_DE_PASSE") if not conf.get(k)]
    if manquants:
        raise Echec(f"{chemin} : {', '.join(manquants)} manquant(s)")
    conf.setdefault("IMAP_PORT", "993")
    conf.setdefault("ADRESSE", conf["IMAP_UTILISATEUR"])
    return conf


# --- Ghost -----------------------------------------------------------------


def _requete(url: str, donnees: dict | None = None, ouvreur=None) -> tuple[int, str]:
    corps = json.dumps(donnees).encode("utf-8") if donnees is not None else None
    site = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    req = urllib.request.Request(
        url,
        data=corps,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en",
            "Origin": site,
            "Referer": site + "/",
            **({"Content-Type": "application/json"} if corps is not None else {}),
        },
    )
    try:
        with ouvreur.open(req, timeout=30) if ouvreur else urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(600).decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise Echec(f"{urlsplit(url).netloc} injoignable : {e.reason}") from e


def demander_lien(site: str, adresse: str, creer: bool) -> None:
    """Demande à Ghost d'envoyer le lien de connexion (ou d'inscription) à l'adresse."""
    code, jeton = _requete(f"{site}/members/api/integrity-token/")
    if code != 200 or not jeton.strip():
        raise Echec(f"jeton d'intégrité refusé par {site} (HTTP {code})")
    donnees = {"email": adresse, "emailType": "signup" if creer else "signin", "integrityToken": jeton.strip()}
    if creer:
        donnees["name"] = ""
    code, corps = _requete(f"{site}/members/api/send-magic-link/", donnees)
    if code not in (200, 201):
        # Le corps d'une erreur Ghost dit pourquoi (compte inconnu, trop de demandes) ; il vient du site.
        raison = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", corps))[:200]
        if "cf-" in corps.lower() or "cloudflare" in corps.lower():
            raison = "Cloudflare demande une vérification de navigateur à cette requête"
        raise Echec(f"le site refuse d'envoyer le lien (HTTP {code}) : {raison}")


def demander_lien_substack(site: str, publication: str, adresse: str) -> None:
    """Demande à Substack d'envoyer le message de connexion pour cette publication."""
    code, corps = _requete(
        "https://substack.com/api/v1/email-login",
        {
            "email": adresse,
            "redirect": site + "/",
            "for_pub": publication,
            "captcha_response": None,
            "can_create_user": False,
        },
    )
    if code != 200:
        raison = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", corps))[:200]
        if "captcha" in corps.lower():
            raison = "Substack demande un captcha à cette requête"
        raise Echec(f"Substack refuse d'envoyer le message de connexion (HTTP {code}) : {raison}")


def lien_substack_du_message(brut: bytes, site: str = "") -> str | None:
    """Le lien du bouton de connexion d'un message Substack, ou None. Sans réseau, donc testable.

    Le lien est celui d'un suiveur de clics (email.mg-….substack.com/c/…) : opaque,
    donc reconnu à son texte — « Verify email », « Sign in » — et à son expéditeur.
    Une lettre de la publication vient de la même adresse et n'a pas ce bouton.
    """
    msg = email.message_from_bytes(brut, policy=email.policy.default)
    if "substack.com" not in str(msg.get("From", "")).lower():
        return None
    for partie in msg.walk():
        if partie.get_content_type() != "text/html":
            continue
        try:
            source = partie.get_content()
        except (LookupError, UnicodeDecodeError):
            continue
        for href, texte in re.findall(r"<a[^>]*href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>", source):
            libelle = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(texte))).strip().lower()
            if re.fullmatch(r"(verify email|sign in( now)?|log in|continue)", libelle):
                lien = html.unescape(href)
                if urlsplit(lien).netloc.endswith("substack.com"):
                    return lien
    return None


def lien_du_message(brut: bytes, site: str) -> str | None:
    """Le lien de connexion Ghost contenu dans un message, ou None. Sans réseau, donc testable."""
    msg = email.message_from_bytes(brut, policy=email.policy.default)
    hote = re.escape(urlsplit(site).netloc)
    motif = re.compile(r"https?://" + hote + r"/[^\s\"'<>\]\)]*[?&]token=[^\s\"'<>\]\)]+")
    for partie in msg.walk():
        if partie.get_content_maintype() != "text":
            continue
        try:
            texte = partie.get_content()
        except (LookupError, UnicodeDecodeError):
            continue
        m = motif.search(html.unescape(texte))
        if m:
            return m.group(0)
    return None


def attendre_lien(boite: dict, site: str, depuis: float, attente: int = ATTENTE, lire=None) -> str:
    """Le lien du premier message arrivé après `depuis` qui en porte un pour ce site.

    `lire(brut, site)` reconnaît le lien dans un message (défaut : celui de Ghost).
    """
    lire = lire or lien_du_message
    fin = time.time() + attente
    derniere_erreur = ""
    while time.time() < fin:
        try:
            with imaplib.IMAP4_SSL(boite["IMAP_HOTE"], int(boite["IMAP_PORT"])) as im:
                im.login(boite["IMAP_UTILISATEUR"], boite["IMAP_MOT_DE_PASSE"])
                jour = time.strftime("%d-%b-%Y", time.gmtime(depuis - 86400))
                for dossier in DOSSIERS_BOITE:
                    if im.select(f'"{dossier}"')[0] != "OK":
                        continue  # ce dossier n'existe pas chez cet hébergeur
                    _, donnees = im.search(None, "SINCE", jour)
                    for num in reversed(donnees[0].split()[-25:]):
                        _, contenu = im.fetch(num, "(BODY.PEEK[])")
                        brut = contenu[0][1]
                        msg = email.message_from_bytes(brut, policy=email.policy.default)
                        try:
                            recu = parsedate_to_datetime(msg["Date"]).timestamp()
                        except (TypeError, ValueError):
                            recu = depuis
                        if recu < depuis - 120:
                            continue  # un lien d'une demande précédente : périmé
                        lien = lire(brut, site)
                        if lien:
                            im.store(num, "+FLAGS", "\\Seen")
                            return lien
        except imaplib.IMAP4.error as e:
            raise Echec(f"la boîte dédiée refuse la connexion IMAP : {str(e)[:120]}") from e
        except OSError as e:
            derniere_erreur = str(e)[:120]
        time.sleep(PAS)
    raise Echec(
        f"aucun message de {urlsplit(site).netloc} dans la boîte après {attente} s"
        + (f" (dernière erreur réseau : {derniere_erreur})" if derniere_erreur else "")
        + " : le compte existe-t-il à cette adresse ? (--creer pour le créer)"
    )


def suivre_lien(lien: str, site: str = "", attendus: tuple[str, ...] = COOKIES_GHOST) -> str:
    """Suit le lien de connexion, redirections comprises, et rend l'en-tête `cookie` de la session.

    Seuls les cookies posés pour le domaine du site sont gardés : Substack en pose
    aussi sur substack.com, qui n'ouvrent pas une publication à domaine propre.
    """
    pot = http.cookiejar.CookieJar()
    ouvreur = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(pot))
    code, _ = _requete(lien, ouvreur=ouvreur)
    en_tete = en_tete_du_pot(pot, site, attendus)
    if not en_tete:
        raise Echec(
            f"le lien a répondu {code} sans poser le cookie de session ({attendus[0]}) : lien périmé ou déjà utilisé"
        )
    garder_compte_substack(pot)
    return en_tete


def en_tete_du_pot(pot, site: str, attendus: tuple[str, ...]) -> str:
    """L'en-tête `cookie` des cookies attendus posés pour le domaine du site, "" si le premier manque."""
    hote = urlsplit(site).netloc
    cookies = {c.name: c.value for c in pot if c.name in attendus and (not hote or hote.endswith(c.domain.lstrip(".")))}
    if attendus[0] not in cookies:
        return ""
    return "; ".join(f"{n}={cookies[n]}" for n in attendus if n in cookies)


def garder_compte_substack(pot) -> None:
    """Range la session du compte Substack quand un lien vient de la poser, si le registre la déclare."""
    sid = next((c.value for c in pot if c.name == "substack.sid" and c.domain.lstrip(".") == "substack.com"), "")
    if sid and cfg.fichier_de_session_ou_rien(COMPTE_SUBSTACK):
        cfg.ecrire_session_fichier(COMPTE_SUBSTACK, f"substack.sid={sid}")


def deriver_substack(site: str, publication: str) -> str:
    """La session d'une publication tirée de celle du compte, sans message. "" si le compte ne passe plus."""
    compte = cfg.session(COMPTE_SUBSTACK) if cfg.fichier_de_session_ou_rien(COMPTE_SUBSTACK) else None
    if not compte or not compte.startswith("substack.sid="):
        return ""
    pot = http.cookiejar.CookieJar()
    pot.set_cookie(
        http.cookiejar.Cookie(
            0,
            "substack.sid",
            compte.split("=", 1)[1],
            None,
            False,
            ".substack.com",
            True,
            True,
            "/",
            True,
            True,
            None,
            False,
            None,
            None,
            {},
        )
    )
    ouvreur = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(pot))
    from urllib.parse import quote  # noqa: PLC0415

    _requete(
        f"https://substack.com/sign-in?redirect={quote(site + '/', safe='')}&for_pub={publication}", ouvreur=ouvreur
    )
    return en_tete_du_pot(pot, site, ("connect.sid",))


# --- Enchaînement ----------------------------------------------------------


def ouvrir(nom: str, creer: bool = False, attente: int = ATTENTE) -> str:
    s = declaration(nom)
    boite = lire_boite()
    site = s["site"].rstrip("/")
    depuis = time.time()
    # L'adresse du compte, si elle n'est pas celle de la boîte : le site écrit alors au
    # compte, et un filtre de transfert fait suivre le message vers la boîte dédiée.
    adresse = commun.adresse(s.get("adresse")) or boite["ADRESSE"]
    if s["ouverture"] == "substack":
        if creer:
            raise Echec("--creer ne vaut que pour Ghost : un abonnement Substack se prend sur le site")
        # D'abord par le compte, sans message ; le compte est rouvert par la publication de
        # référence s'il ne passe plus, puis la dérivation est rejouée.
        ref_nom = (cfg.charger()["credences"].get("sessions") or {}).get(COMPTE_SUBSTACK, {}).get("reference")
        ref = declaration(ref_nom) if ref_nom else s
        for essai in (1, 2):
            cookie = deriver_substack(site, s["publication"])
            if cookie:
                cfg.ecrire_session_fichier(nom, cookie)
                verdict = cfg.eprouver(nom).get("verdict", "?")
                if verdict == "authentifiée":
                    print(f"  {nom} : session dérivée du compte Substack, sans message — sonde : {verdict}")
                    return verdict
                if essai == 2:
                    raise Echec(
                        f"session dérivée du compte, mais la sonde dit : {verdict} "
                        f"(l'abonnement couvre-t-il cette publication ?)"
                    )
            if essai == 2:
                raise Echec("le compte Substack vient d'être rouvert, mais ne donne pas de session à cette publication")
            depuis = time.time()
            demander_lien_substack(
                ref["site"].rstrip("/"), ref["publication"], commun.adresse(ref.get("adresse")) or adresse
            )
            print(
                f"  {nom} : compte Substack à rouvrir — message demandé pour {ref['publication']}, "
                f"attente (au plus {attente} s)"
            )
            suivre_lien(
                attendre_lien(boite, ref["site"], depuis, attente, lien_substack_du_message),
                ref["site"].rstrip("/"),
                COOKIES_SUBSTACK,
            )
        lire, attendus = lien_substack_du_message, COOKIES_SUBSTACK  # jamais atteint : la boucle rend ou lève
    else:
        demander_lien(site, adresse, creer)
        lire, attendus = lien_du_message, COOKIES_GHOST
    print(
        f"  {nom} : lien {'d’inscription' if creer else 'de connexion'} demandé à {urlsplit(site).netloc}, "
        f"attente du message (au plus {attente} s)"
    )
    cookie = suivre_lien(attendre_lien(boite, site, depuis, attente, lire), site, attendus)
    cfg.ecrire_session_fichier(nom, cookie)
    verdict = cfg.eprouver(nom).get("verdict", "?")
    noms = ", ".join(c.split("=", 1)[0] for c in cookie.split("; "))
    print(f"  {nom} : session rangée ({noms}) — sonde : {verdict}")
    if verdict != "authentifiée":
        raise Echec(f"la session ouverte ne passe pas la sonde : {verdict}")
    return verdict


def sessions_ouvrables(graphe: str | None) -> list[str]:
    """Les sessions à ouverture déclarée, présentées par une source (du graphe, s'il est donné)."""
    decl = cfg.charger()["credences"].get("sessions") or {}
    noms = []
    for src in cfg.toutes(graphe=graphe) if graphe else cfg.toutes():
        if src.session and (decl.get(src.session) or {}).get("ouverture") and src.session not in noms:
            noms.append(src.session)
    return noms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nom", nargs="?", help="session du registre (credences.sessions)")
    ap.add_argument("--creer", action="store_true", help="créer le compte gratuit à l'adresse de la boîte dédiée")
    ap.add_argument("--si-besoin", action="store_true", help="n'ouvrir que les sessions qui ne passent plus leur sonde")
    ap.add_argument("--graphe", help="avec --si-besoin : seulement les sessions des sources de ce graphe")
    ap.add_argument("--etat", action="store_true", help="éprouver la session rangée, sans rien ouvrir")
    ap.add_argument("--attente", type=int, default=ATTENTE, help=f"secondes à attendre le message (défaut {ATTENTE})")
    args = ap.parse_args()

    if not args.nom and not args.si_besoin:
        ap.error("donner une session, ou --si-besoin")
    noms = [args.nom] if args.nom else sessions_ouvrables(args.graphe)
    if not noms:
        print("  aucune session à ouverture par script" + (f" pour le graphe {args.graphe}" if args.graphe else ""))
        return 0
    echecs = 0
    for nom in noms:
        try:
            if args.etat or args.si_besoin:
                verdict = cfg.eprouver(nom).get("verdict", "?")
                if args.etat or verdict == "authentifiée":
                    print(f"  {nom} : {verdict}")
                    continue
                print(f"  {nom} : {verdict} — ouverture")
            ouvrir(nom, creer=args.creer, attente=args.attente)
        except (Echec, cfg.ConfigInvalide) as e:
            echecs += 1
            print(f"  !! {nom} : {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

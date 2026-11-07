#!/usr/bin/env python3
"""Collecte des lettres d'information reçues par courriel, déclarées au registre (plateforme « Courriel »).

Pourquoi ce collecteur existe (mainteneur, 03.11.2026). Des sites refusent notre robot, le Parlement européen le
premier, mais envoient leurs lettres à qui s'abonne. Une boîte dédiée reçoit ces lettres ; le collecteur la lit.

Ce qu'il garantit.
- Lecture seule : le dossier est ouvert par EXAMINE, chaque message lu par BODY.PEEK[] ; aucun message n'est marqué
  lu, déplacé ou supprimé. Seuls `imaplib` et `email` de la bibliothèque standard.
- Le mot de passe d'application vit au trousseau macOS (`<edition>:imap:<boite>`) et ne sert qu'à la connexion : il
  n'est ni écrit, ni journalisé, ni dans une fiche. L'adresse de la boîte peut rester hors dépôt (`utilisateur_env`).
  Sans l'un ou l'autre, la source dit « boîte non configurée » et la routine continue.
- Une fiche par message, même quand la lettre regroupe plusieurs communiqués : l'objet pour titre ; pour adresse le
  lien « voir dans le navigateur » sans paramètres de suivi, sinon un identifiant stable dérivé du Message-ID ; pour
  date l'en-tête Date (origine_date « courriel »), jamais le jour de la collecte.
- Le texte vient de la partie HTML, nettoyée par la voie du lecteur web (newskit.lecture_web : trafilatura, blocs
  repris, lignes parasites), sinon de la partie texte. En sortent les liens de désabonnement, les pixels de suivi, les
  paramètres de suivi des liens, l'adresse du destinataire et les pieds de page d'abonnement.
- Les messages cherchés sont ceux reçus depuis la dernière collecte, gardée dans le _manifest.json de la source.

Usage :
    python3 -m newskit.collecte_courriel [--source <cle>] [--graphe <nom>]
Sortie : sources/<dossier>/<date>_<slug>.md + _manifest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import email
import hashlib
import html as htmlmod
import imaplib
import json
import re
import sys
import urllib.parse
from email import policy
from email.utils import getaddresses, parsedate_to_datetime

from newskit.commun import CODE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit import lecture_web as lw  # noqa: E402
from newskit.collecte_rss import retenu_par_mots_cles, slug  # noqa: E402
from newskit.commun import html_to_md, masquer_chemins  # noqa: E402

FABRIQUE = imaplib.IMAP4_SSL
DELAI = 60  # secondes, connexion IMAP
MARGE_JOURS = 1  # SINCE porte sur la date de réception, au jour près, dans le fuseau du serveur
MOIS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NON_CONFIGUREE = "boîte non configurée"

# Paramètres de suivi d'un lien : noms exacts et préfixes, sans égard à la casse.
SUIVI = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "yclid",
    "twclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "_hsenc",
    "_hsmi",
    "__hstc",
    "__hssc",
    "__hsfp",
    "hsctatracking",
    "oly_enc_id",
    "oly_anon_id",
    "vero_id",
    "vero_conv",
    "rb_clickid",
    "s_cid",
    "trk",
    "trkcampaign",
    "ss_source",
    "ss_campaign_id",
    "ml_subscriber",
    "ml_subscriber_hash",
    "sc_channel",
    "sc_campaign",
    "cmpid",
    "wt_mc",
    "wt.mc_id",
    "et_rid",
    "et_cid",
    "elqtrackid",
    "elqtrack",
    "ref_src",
    "spm",
}
PREFIXES_SUIVI = ("utm_", "pk_", "mtm_", "hsa_", "matomo_", "piwik_", "mc_", "_hs", "oly_", "vero_", "elq")
RE_URL = re.compile(r"https?://[^\s<>()\"'\]]+")

# Les liens et lignes de service d'une lettre, sur le texte plié (lecture_web._plier : minuscules, sans accents).
RE_DESABONNEMENT = re.compile(
    r"unsubscrib|desabonn|desinscri|desinscription|abmelden|abbestell|disiscriv|cancella iscrizione|darse de baja|"
    r"cancelar (la )?suscripcion|uitschrijv|afmelden|opt out|manage (your )?(subscription|preferences|email)|"
    r"update (your )?(subscription|preferences)|email preferences|gerer (mes|vos) (preferences|abonnements?)|"
    r"modifier (mes|vos) (preferences|abonnements?)|preferences d abonnement|einstellungen verwalten"
)
RE_NAVIGATEUR = re.compile(
    r"view (this )?(e ?mail |message |newsletter )?(in|on) (your |a )?(web )?browser|view (it )?online|"
    r"read (this |it )?online|open in (your )?browser|having trouble (viewing|reading)|"
    r"voir (ce (message|courriel|mail) )?(dans|sur) (le|votre|un) navigateur|voir (la )?version (en ligne|web)|"
    r"afficher (dans|sur) (le|votre) navigateur|version (en ligne|web|navigateur)|lire en ligne|"
    r"im browser (anzeigen|ansehen|offnen|lesen)|online (ansehen|lesen|version)|webversion|"
    r"visualizza (nel|sul) browser|ver en (el )?navegador|bekijk (in|online)"
)
RE_PIED = re.compile(
    r"(this|the) (e ?mail|message|newsletter) (was|has been) sent to|you (are )?receiv(e|ed|ing) this|"
    r"you are subscribed|you subscribed|you have subscribed|why did i get this|"
    r"ce (message|courriel|mail|e ?mail) (a ete|vous a ete|vous est) (envoye|adresse)|"
    r"vous (recevez|avez recu) (ce|cette|ces)|vous etes (abonne|inscrit)|"
    r"diese (e ?mail|nachricht) wurde an|sie erhalten (diese|diesen)|"
    r"questa (e ?mail|email) e stata inviata|ricevi questa|este (correo|mensaje) (fue|ha sido) enviado|"
    r"deze (e ?mail|nieuwsbrief) is verzonden|" + RE_DESABONNEMENT.pattern
)
MOTS_PIED = 45  # une ligne de pied de page : courte ; un paragraphe de fond ne sort jamais
RE_SIGNATURE = re.compile(r"^-- ?$")


def aujourdhui() -> str:
    return dt.date.today().isoformat()


# --- Liens et adresses -------------------------------------------------------


def sans_suivi(url: str) -> str:
    """L'adresse sans ses paramètres de suivi (utm_*, fbclid, mc_eid…) ; le reste de la requête et l'ancre restent."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not p.query:
        return url
    garde = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in SUIVI and not k.lower().startswith(PREFIXES_SUIVI)
    ]
    return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(garde, safe="/:@,")))


def _formes(adresses: set[str]) -> list[str]:
    """Une adresse telle qu'elle peut paraître dans un message : en clair, encodée dans une URL."""
    out = set()
    for a in adresses:
        a = a.strip().lower()
        if "@" in a:
            out |= {a, urllib.parse.quote(a, safe=""), a.replace("@", "%40"), a.replace("@", "&#64;")}
    return sorted(out, key=len, reverse=True)


def retirer_adresses(texte: str, adresses: set[str]) -> str:
    for forme in _formes(adresses):
        texte = re.sub(re.escape(forme), "", texte, flags=re.I)
    return texte


def contient_adresse(texte: str, adresses: set[str]) -> bool:
    bas = urllib.parse.unquote(texte or "").lower()
    return any(f in bas or f in (texte or "").lower() for f in _formes(adresses))


def identifiant(message_id: str) -> str:
    """L'identifiant stable d'un message sans lien public : une empreinte de son Message-ID, qui ne dit rien de son
    destinataire (certains Message-ID portent l'adresse)."""
    return "urn:courriel:" + hashlib.sha256(message_id.strip().encode("utf-8")).hexdigest()[:24]


# --- HTML --------------------------------------------------------------------


def _texte_balise(fragment: str) -> str:
    return lw._plier(htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def lien_navigateur(html: str, adresses: set[str]) -> str:
    """Le lien « voir dans le navigateur » de la lettre, sans paramètres de suivi ; "" s'il manque ou s'il désigne le
    destinataire (un lien qui porte son adresse n'est pas public)."""
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html or "", re.S | re.I):
        h = re.search(r"""href\s*=\s*["']([^"']+)["']""", m.group(1), re.I)
        if not h or not RE_NAVIGATEUR.search(_texte_balise(m.group(2))):
            continue
        url = htmlmod.unescape(h.group(1)).strip()
        if url.lower().startswith(("http://", "https://")) and not contient_adresse(url, adresses):
            return sans_suivi(url)
    return ""


def _pixel(attrs: str) -> bool:
    taille = [int(x) for x in re.findall(r"""(?:width|height)\s*[=:]\s*["']?\s*(\d+)""", attrs, re.I)]
    return (
        (bool(taille) and min(taille) <= 3)
        or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs, re.I))
        or bool(re.search(r"""src\s*=\s*["'][^"']*(?:pixel|beacon|/open|/track|ouvert)""", attrs, re.I))
    )


def nettoyer_html(html: str, adresses: set[str]) -> str:
    """La lettre sans ce qui n'est pas son contenu : pixels de suivi, liens de désabonnement et de préférences, lien
    « voir dans le navigateur » ; les liens restants sans paramètres de suivi ; les tableaux de mise en page en blocs ;
    l'adresse du destinataire retirée."""
    h = re.sub(r"<img\b([^>]*)/?>", lambda m: "" if _pixel(m.group(1)) else m.group(0), html or "", flags=re.I)

    def lien(m):
        attrs, texte = m.group(1), m.group(2)
        href = re.search(r"""href\s*=\s*["']([^"']+)["']""", attrs, re.I)
        cible = lw._plier(urllib.parse.unquote(htmlmod.unescape(href.group(1)))) if href else ""
        plie = _texte_balise(texte)
        if RE_DESABONNEMENT.search(plie) or RE_DESABONNEMENT.search(cible) or RE_NAVIGATEUR.search(plie):
            return ""
        if href:
            propre = sans_suivi(htmlmod.unescape(href.group(1)))
            attrs = attrs[: href.start(1)] + htmlmod.escape(propre, quote=True) + attrs[href.end(1) :]
        return f"<a{attrs}>{texte}</a>"

    h = re.sub(r"<a\b([^>]*)>(.*?)</a>", lien, h, flags=re.S | re.I)
    # Une lettre se met en page par tableaux : lus comme tableaux, ses paragraphes deviendraient une seule cellule.
    h = re.sub(r"<(/?)(?:table|tbody|thead|tfoot|tr|td|th)\b[^>]*>", r"<\1div>", h, flags=re.I)
    return retirer_adresses(h, adresses)


def texte_du_html(html: str, url: str = "") -> str:
    """La voie du lecteur web (lecture_web.lire) sur un HTML déjà en main : trafilatura, blocs repris, nettoyage ;
    l'extracteur maison en repli."""
    brut = lw._reparer(html)
    texte = ""
    try:
        import trafilatura  # noqa: PLC0415
        from trafilatura.settings import use_config  # noqa: PLC0415

        texte = (
            trafilatura.extract(
                brut,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
                url=url or None,
                config=use_config(),
            )
            or ""
        )
        if texte:
            texte, _ = lw.rattraper(brut, texte)
    except Exception:  # noqa: BLE001
        texte = ""
    if len(texte.split()) < lw.MOTS_LUE:
        repli = html_to_md(brut)
        if len(repli.split()) > len(texte.split()):
            texte = repli
    return texte


# --- Texte -------------------------------------------------------------------


def nettoyer_texte(texte: str, adresses: set[str]) -> str:
    """Le corps sans pied de page d'abonnement ni signature, les liens sans suivi, sans l'adresse du destinataire, puis
    le nettoyage du lecteur web (lignes d'abonnement et de partage, notes)."""
    lignes = []
    for l in (texte or "").replace("\r\n", "\n").split("\n"):
        if RE_SIGNATURE.match(l):
            break  # « -- » : la signature, jusqu'à la fin
        if len(l.split()) < MOTS_PIED and (RE_PIED.search(lw._plier(l)) or RE_NAVIGATEUR.search(lw._plier(l))):
            continue
        lignes.append(l)
    t = RE_URL.sub(lambda m: sans_suivi(m.group(0)), "\n".join(lignes))
    t = retirer_adresses(t, adresses)
    t = re.sub(r"\(\s*\)", "", t)
    corps, notes = lw.nettoyer(t)
    return (corps + ("\n\n" + notes if notes else "")).strip()


# --- Message -----------------------------------------------------------------


def date_d_entete(valeur: str | None) -> str:
    """AAAA-MM-JJ de l'en-tête Date, au jour écrit par l'expéditeur (son fuseau) ; "" si absente, illisible ou à venir."""
    if not valeur:
        return ""
    try:
        d = parsedate_to_datetime(str(valeur))
    except (TypeError, ValueError, IndexError):
        return ""
    jour = d.date().isoformat()
    return "" if jour > aujourdhui() else jour


def adresse_de(valeur) -> str:
    return (getaddresses([str(valeur or "")]) or [("", "")])[0][1].strip().lower()


def de_l_expediteur(adresse: str, expediteur: str) -> bool:
    """`expediteur` : une adresse exacte, ou un domaine (et ses sous-domaines)."""
    e = expediteur.strip().lower()
    if "@" in e:
        return adresse == e
    dom = adresse.rsplit("@", 1)[-1]
    return dom == e.lstrip("@") or dom.endswith("." + e.lstrip("@"))


def destinataires(msg, boite: dict) -> set[str]:
    champs = [
        str(v)
        for k in ("To", "Cc", "Bcc", "Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To")
        for v in (msg.get_all(k) or [])
    ]
    return {a.lower() for _, a in getaddresses(champs) if "@" in a} | {(cfg.utilisateur_boite(boite) or "").lower()} - {
        ""
    }


def lire_message(brut: bytes, boite: dict) -> dict:
    """{titre, auteur, date, origine_date, url, cle, expediteur, texte} d'un message brut."""
    msg = email.message_from_bytes(brut, policy=policy.default)
    adresses = destinataires(msg, boite)
    mid = str(msg.get("Message-ID") or "").strip() or "|".join(
        str(msg.get(k) or "") for k in ("Date", "From", "Subject")
    )
    nom = (getaddresses([str(msg.get("From") or "")]) or [("", "")])[0][0]
    titre = re.sub(r"\s+", " ", str(msg.get("Subject") or "")).strip()
    url, texte = "", ""
    partie = msg.get_body(preferencelist=("html",))
    if partie is not None:
        html = partie.get_content()
        url = lien_navigateur(html, adresses)
        texte = nettoyer_texte(texte_du_html(nettoyer_html(html, adresses), url), adresses)
    if len(texte.split()) < lw.MOTS_LUE:
        partie = msg.get_body(preferencelist=("plain",))
        if partie is not None:
            t = nettoyer_texte(partie.get_content(), adresses)
            if len(t.split()) > len(texte.split()):
                texte = t
    date = date_d_entete(msg.get("Date"))
    return {
        "titre": retirer_adresses(titre, adresses),
        "auteur": retirer_adresses(nom, adresses).strip(),
        "date": date,
        "origine_date": "courriel" if date else "inconnue",
        "url": url or identifiant(mid),
        "cle": identifiant(mid).rsplit(":", 1)[1],
        "expediteur": adresse_de(msg.get("From")),
        "texte": texte,
    }


# --- IMAP --------------------------------------------------------------------


def _imap_date(jour: str) -> str:
    d = dt.date.fromisoformat(jour)
    return f"{d.day:02d}-{MOIS[d.month - 1]}-{d.year}"


def _dossier(nom: str) -> str:
    return nom if re.fullmatch(r"[\w./-]+", nom) else '"' + nom.replace("\\", "\\\\").replace('"', '\\"') + '"'


def messages(conn, dossier: str, expediteur: str, depuis: str) -> list[bytes]:
    """Les messages bruts du dossier, ouvert en lecture seule (EXAMINE), lus sans les marquer (BODY.PEEK[])."""
    typ, _ = conn.select(_dossier(dossier), readonly=True)
    if typ != "OK":
        raise imaplib.IMAP4.error(f"dossier « {dossier} » illisible")
    critere = (["SINCE", _imap_date(depuis)] if depuis else []) + ["FROM", '"' + expediteur.replace('"', "") + '"']
    typ, data = conn.uid("SEARCH", None, *critere)
    if typ != "OK":
        raise imaplib.IMAP4.error("recherche refusée")
    out = []
    for uid in (data[0] or b"").split():
        typ, d = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ == "OK":
            out += [x[1] for x in d if isinstance(x, tuple) and len(x) > 1 and isinstance(x[1], bytes)]
    return out


def _manifeste(src) -> dict:
    try:
        m = json.loads(src.manifeste.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    m.setdefault("messages", {})
    return m


def traiter_source(src, boite: dict, mdp: str, echecs: list[str], fabrique=None) -> dict:
    """Collecte une source. Rend le décompte ; une panne va dans `echecs`, sans le mot de passe."""
    compte = {"lus": 0, "neuves": 0, "autres_expediteurs": 0, "hors_sujet": 0, "vides": 0}
    manifeste = _manifeste(src)
    depuis = manifeste.get("derniere_collecte") or ""
    if depuis:
        depuis = (dt.date.fromisoformat(depuis) - dt.timedelta(days=MARGE_JOURS)).isoformat()
    expediteur = str(src.propres["expediteur"])
    conn = None
    try:
        conn = (fabrique or FABRIQUE)(boite["hote"], int(boite["port"]), timeout=DELAI)
        conn.login(cfg.utilisateur_boite(boite) or "", mdp)
        bruts = messages(conn, boite["dossier"], expediteur.lstrip("@"), depuis)
    except (imaplib.IMAP4.error, OSError) as e:
        echecs.append(
            f"{src.nom} : boîte {boite['hote']} illisible ({type(e).__name__} : {str(e).replace(mdp, '***')[:120]})"
        )
        return compte
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
    mots_cles = list(src.propres.get("mots_cles") or [])
    jour = aujourdhui()
    for brut in bruts:
        compte["lus"] += 1
        m = lire_message(brut, boite)
        if not de_l_expediteur(m["expediteur"], expediteur):
            compte["autres_expediteurs"] += 1
            continue
        if m["cle"] in manifeste["messages"]:
            continue
        if not retenu_par_mots_cles(m["titre"], mots_cles):
            compte["hors_sujet"] += 1
            continue
        if not m["texte"].strip():
            compte["vides"] += 1
            continue
        nom = f"{m['date'] or jour}_{slug('', m['titre'])[:60]}-{m['cle'][:6]}.md"
        champs = {
            "title": m["titre"] or "(sans objet)",
            "subtitle": "",
            "author": m["auteur"],
            "date": m["date"],
            "url": m["url"],
            "plateforme": "Courriel",
            "publication": src.nom,
            "complet": True,
            "collected": jour,
            "source": f"courriel de {src.nom}",
            "origine_date": m["origine_date"],
        }
        try:
            src.chemin.mkdir(parents=True, exist_ok=True)
            fiche.ecrire(src.chemin / nom, champs, f"# {champs['title']}\n\n{m['texte']}")
        except fiche.IdentiteDejaVue as deja:  # une autre fiche du graphe porte déjà ce document
            print(f"  déjà vue : {deja}")
            continue
        except ValueError as e:
            echecs.append(f"{src.nom} : {str(e)[:100]}")
            continue
        manifeste["messages"][m["cle"]] = {
            "file": nom,
            "title": champs["title"],
            "date": m["date"],
            "url": m["url"],
            "complet": True,
            "words": len(m["texte"].split()),
        }
        compte["neuves"] += 1
    manifeste["derniere_collecte"] = jour
    src.chemin.mkdir(parents=True, exist_ok=True)
    src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
    compte["total"] = len(manifeste["messages"])
    return compte


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    args = ap.parse_args()
    sources = [s for s in cfg.toutes("Courriel", graphe=args.graphe) if not args.source or s.cle == args.source]
    if not sources:
        print("  aucune source « Courriel » au registre, rien à collecter.")
        return 0
    echecs: list[str] = []
    for src in sources:
        nom_boite = src.propres["boite"]
        mdp, raison = cfg.lire_mot_de_passe_boite(nom_boite)
        if not mdp:
            # Non bloquant : entrée absente, trousseau verrouillé ou lecture refusée, chacun sa raison.
            print(f"  {src.nom} : {raison} (entrée {cfg.service_boite(nom_boite)})")
            continue
        b = cfg.boite(nom_boite)
        if not cfg.utilisateur_boite(b):
            # Non bloquant non plus : la variable qui porte l'adresse de la boîte n'est pas définie.
            print(
                f"  {src.nom} : {NON_CONFIGUREE} ({b.get('utilisateur_env')} absente de l'environnement et de "
                f"{masquer_chemins(str(cfg.commun.FICHIER_CONTACT))})"
            )
            continue
        c = traiter_source(src, b, mdp, echecs)
        del mdp
        if "total" in c:
            print(
                f"{src.nom} : {c['lus']} message(s) lu(s), {c['neuves']} neuf(s), {c['total']} au total"
                + (f", {c['autres_expediteurs']} d'un autre expéditeur" if c["autres_expediteurs"] else "")
                + (f", {c['hors_sujet']} hors mots-clés" if c["hors_sujet"] else "")
                + (f", {c['vides']} sans texte" if c["vides"] else "")
            )
    if echecs:
        print("  ALERTE :", file=sys.stderr)
        for e in echecs:
            print(f"    {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

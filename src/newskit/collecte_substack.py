#!/usr/bin/env python3
"""Collecte des publications Substack déclarées au registre (API publique /api/v1/).

Les publications, leur domaine et le statut de l'abonnement viennent de
`sources.yaml` : ajouter une publication ne demande aucune modification ici.

Les billets réservés aux abonnés payants ne sont rendus qu'en aperçu par l'API
publique (`complet: false`). Si la source déclare une session (voir la section
`credences` du registre), la requête la présente et l'API rend alors le texte
entier. La valeur de la session est lue au moment de la requête et ne sert qu'à
l'en-tête `Cookie` : elle n'est écrite dans aucun fichier, aucun journal, aucun
rapport. Un billet payant qui reste un aperçu malgré une session est signalé
comme une anomalie, non passé sous silence.

La collecte est incrémentale par nature : un billet déjà complet n'est pas
redescendu. `--update` est accepté pour cette raison, et `--tout` force la
reprise intégrale d'une publication.

Usage : python3 -m newskit.collecte_substack [--update | --tout] [--sans-session]
Sortie : sources/<dossier>/<date>_<slug>.md + sources/<dossier>/_manifest.json
"""

import json, sys, time, urllib.parse, urllib.request
from pathlib import Path
from newskit import config_sources as cfg
from newskit import fiche
from newskit.commun import UA_NAVIGATEUR as UA, html_to_md

ECHECS = []  # requêtes qui ne sont pas passées : un refus n'est pas une absence


def get(url, cookie=None, tries=3):
    en_tetes = {"User-Agent": UA, "Accept": "application/json"}
    if cookie:
        en_tetes["Cookie"] = cookie
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=en_tetes)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if i == tries - 1:
                ECHECS.append(f"{url} : {e}")
                print(f"  !! {url}: {e}", file=sys.stderr)
                return None
            time.sleep(3 * (i + 1))


def archive(domain, cookie=None, depuis=""):
    """Les billets de l'archive, du plus récent au plus ancien. `depuis` (AAAA-MM-JJ) : on s'arrête
    au premier billet antérieur — une lettre d'actualité n'a pas à livrer dix ans d'archives."""
    # Pagination par curseur depuis le 06.11.2026 : `offset` rend 400, la réponse est {posts, nextCursor}.
    posts, curseur = [], None
    while True:
        suite = f"&cursor={urllib.parse.quote(curseur, safe='')}" if curseur else ""
        reponse = get(f"https://{domain}/api/v1/archive?sort=new&limit=25{suite}", cookie)
        page = (reponse or {}).get("posts") or [] if isinstance(reponse, dict) else []
        neufs = [x for x in page if x.get("id") not in {y.get("id") for y in posts}]
        if not neufs:
            break  # une page qui revient (curseur perdu à une redirection) : fin
        posts += neufs
        if depuis and (page[-1].get("post_date") or "9999")[:10] < depuis:
            break
        curseur = reponse.get("nextCursor")
        if not curseur or len(posts) > 2000:
            break
    return [p for p in posts if not depuis or (p.get("post_date") or "9999")[:10] >= depuis]


def collected_a_garder(chemin, corps_nouveau):
    """La date de collecte d'une fiche dont le corps n'a pas changé.

    Les aperçus des publications gratuites sont redemandés à chaque passage,
    au cas où un billet s'ouvrirait. Quand rien n'a changé, réécrire la fiche
    avançait `collected` au jour même : la seconde horloge bougeait à vide,
    sur 145 fiches, chaque semaine. Rend la date existante si le corps est
    identique, None sinon.
    """
    if not chemin.exists():
        return None
    champs, corps_actuel = fiche.lire_fichier(chemin)
    if corps_actuel.strip() == corps_nouveau.strip() and champs.get("collected"):
        return champs["collected"]
    return None


def write(src, meta, post):
    body = html_to_md(post.get("body_html") or "")
    words = len(body.split())
    complet = not (post.get("audience") == "only_paid" and (words < 0.6 * (post.get("wordcount") or 0)))
    d = meta["post_date"][:10]
    fn = src.chemin / f"{d}_{meta['slug'][:80]}.md"
    fm = {
        "title": meta["title"],
        "subtitle": meta.get("subtitle") or "",
        "author": ", ".join(b.get("name") or "" for b in (post.get("publishedBylines") or [])),
        "date": d,
        "url": meta["canonical_url"],
        "publication": src.nom,
        "source": "Substack (abonnement)",
        "audience": meta.get("audience"),
        "type": meta.get("type"),
        "wordcount_annonce": meta.get("wordcount"),
        "words": words,
        "complet": complet,
        "collected": time.strftime("%Y-%m-%d"),
    }
    fm["plateforme"] = "Substack"
    corps = "# " + meta["title"] + "\n\n" + (f"*{fm['subtitle']}*\n\n" if fm["subtitle"] else "") + body
    ancienne = collected_a_garder(fn, corps)
    if ancienne:
        return fn.name, complet, words, False  # rien n'a changé, la fiche n'est pas réécrite
    fiche.ecrire(fn, fm, corps)  # contrat commun : voir newskit.fiche
    return fn.name, complet, words, True


def main():
    tout = "--tout" in sys.argv  # forcer la reprise intégrale
    sans_session = "--sans-session" in sys.argv
    anomalies = 0
    # `--graphe <nom>` : seulement les publications de ce graphe (routine d'un graphe seul).
    graphe = sys.argv[sys.argv.index("--graphe") + 1] if "--graphe" in sys.argv else None
    # `--source <clé>` : une seule publication (essai d'une source neuve).
    seule = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else None
    for src in cfg.toutes("Substack", graphe=graphe):
        if seule and src.cle != seule:
            continue
        cookie = None
        if src.session and not sans_session:
            cookie = cfg.session(src.session)
            if cookie:
                print(f"{src.nom}: session « {src.session} » présentée")
            else:
                print(
                    f"{src.nom}: session « {src.session} » déclarée mais absente du trousseau ; "
                    f"les billets payants resteront en aperçu",
                    file=sys.stderr,
                )
        src.chemin.mkdir(parents=True, exist_ok=True)
        mf = src.manifeste
        manifest = json.loads(mf.read_text()) if mf.exists() else {}
        depuis = str(src.propres.get("depuis") or "")
        posts = archive(src.domaine, cookie, depuis)
        # `mots_cles` (06.10.2026) : une publication qui parle de tout (Farrell : sanctions, Cour suprême,
        # littérature) n'entre que par ses billets du sujet, lus au titre et au sous-titre.
        mots = [m.lower() for m in (src.propres.get("mots_cles") or [])]
        if mots:
            avant = len(posts)
            posts = [
                m
                for m in posts
                if any(k in f" {m.get('title') or ''} {m.get('subtitle') or ''} ".lower() for k in mots)
            ]
            print(f"{src.nom}: {avant - len(posts)} billet(s) hors mots-clés écartés")
        print(f"{src.nom}: {len(posts)} posts dans l'archive" + (f" depuis le {depuis}" if depuis else ""))
        inchangees = 0
        for meta in posts:
            slug = meta["slug"]
            if (
                not tout
                and slug in manifest
                and manifest[slug].get("complet")
                and (src.chemin / manifest[slug]["file"]).exists()
            ):
                continue
            post = get(f"https://{src.domaine}/api/v1/posts/{slug}", cookie)
            time.sleep(0.7)
            if not post:
                continue
            try:
                fn, complet, words, ecrite = write(src, meta, post)
            except fiche.IdentiteDejaVue as deja:  # le même billet, déjà venu par une autre source du graphe
                print(f"  déjà vue : {deja}")
                continue
            inchangees += 0 if ecrite else 1
            manifest[slug] = {
                "file": fn,
                "title": meta["title"],
                "date": meta["post_date"][:10],
                "audience": meta.get("audience"),
                "complet": complet,
                "words": words,
                "url": meta["canonical_url"],
            }
            mf.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
            # Une session présentée qui ne débloque pas un billet payant est une
            # anomalie : session expirée, ou abonnement qui ne couvre pas ce billet.
            if cookie and not complet:
                anomalies += 1
                print(f"  !! aperçu malgré la session : {meta['canonical_url']}", file=sys.stderr)
        inc = sum(1 for v in manifest.values() if not v["complet"])
        etiquette = "payant" if src.payant else "abonnement gratuit : limite, pas une tâche"
        print(
            f"  -> {len(manifest)} fiches, {inc} en aperçu seulement ({etiquette})"
            + (f", {inchangees} aperçu(s) redemandé(s) sans changement, non réécrit(s)" if inchangees else "")
        )
    if anomalies:
        print(
            f"  ALERTE : {anomalies} billet(s) payant(s) restent en aperçu malgré une session. "
            f"Éprouver la session : python3 -m newskit.config_sources verifier",
            file=sys.stderr,
        )
    if ECHECS:
        # Une archive qui ne répond pas rend zéro billet, ce qui ressemble à une
        # semaine sans publication. Le dire plutôt que de laisser croire.
        print(
            f"  ALERTE : {len(ECHECS)} requête(s) n'ont pas abouti ; ce ne sont pas des billets absents.",
            file=sys.stderr,
        )
        for e in ECHECS[:5]:
            print(f"    {e}", file=sys.stderr)
    return 1 if (anomalies or ECHECS) else 0


if __name__ == "__main__":
    sys.exit(main())

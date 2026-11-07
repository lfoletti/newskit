#!/usr/bin/env python3
"""Collecte des éditions des newsletters LinkedIn déclarées au registre (pages publiques /pulse/).

L'URL de la newsletter, le nom de la publication, le dossier de sortie et le
suffixe d'auteur des URLs /pulse/ viennent de `sources.yaml` : ajouter une
newsletter ne demande aucune modification ici.

Usage :
  python3 -m newskit.collecte_linkedin            # crawl complet (BFS sur les liens croisés)
  python3 -m newskit.collecte_linkedin --update   # ne récupère que les URLs absentes du manifeste

Sortie : sources/<dossier>/<date>_<slug>.md  (frontmatter + corps)
         sources/<dossier>/_manifest.json
"""
import json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

from newskit import fiche

from newskit import config_sources as cfg

from newskit.commun import RACINE  # noqa: E402 — la racine des données (N1)
RAW = RACINE / "cache" / "linkedin_raw_html"   # cache des pages brutes, hors de sources/
from newskit.commun import UA_NAVIGATEUR as UA  # noqa: E402
PULSE_RE = re.compile(r"https://www\.linkedin\.com/pulse/[A-Za-z0-9%._-]+")

ECHECS: list[str] = []      # requêtes qui ne sont pas passées : un refus n'est pas une absence


def fetch(url, tries=3, cache=False):
    key = RAW / (re.sub(r"[^A-Za-z0-9]+", "_", url.rsplit("/", 1)[1]) + ".html")
    if cache and key.exists(): return key.read_text(encoding="utf-8")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode("utf-8", "replace")
                if cache: RAW.mkdir(parents=True, exist_ok=True); key.write_text(txt, encoding="utf-8")
                return txt
        except Exception as e:
            if i == tries - 1:
                ECHECS.append(f"{url} : {e}")
                print(f"  !! échec {url}: {e}", file=sys.stderr); return None
            time.sleep(3 * (i + 1))

# `html_to_md` a longtemps vécu ici, importé par trois autres collecteurs. Il
# est dans `commun.py` désormais ; les noms restent exposés ici pour que rien
# de ce qui l'importait ne casse.
from newskit.commun import html_to_md, nettoyer_href as clean_href  # noqa: E402,F401


def parse(url, page, auteur_re):
    ld = None
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1))
            if d.get("@type") == "Article": ld = d; break
        except json.JSONDecodeError: pass
    if not ld: return None
    m = re.search(r'<article class="article-main[^"]*"[^>]*>(.*?)</article>', page, re.S)
    body = html_to_md(m.group(1)) if m else ""
    # retirer l'en-tête "auteur / date" et le bloc final "Loading" éventuel
    body = re.sub(r"^.*?Published [A-Z][a-z]{2} \d{1,2}, \d{4}\s*", "", body, count=1, flags=re.S)
    body = re.sub(r"\n#+ Recommended by LinkedIn\n(?:\s*\[[^\]]*\]\([^)]*\)\s*)+", "\n", body)
    body = re.sub(r"^(\[\s*\+ Follow\s*\]\([^)]*\)\s*)+", "", body).strip()
    ed = re.search(r"\bEdition\s+(\d+)", (ld.get("headline") or "") + " " + body[:2000])
    return {
        "url": ld.get("url", url), "title": ld.get("name"), "subtitle": ld.get("headline"),
        "date": (ld.get("datePublished") or "")[:10], "edition": int(ed.group(1)) if ed else None,
        "author": (ld.get("author") or {}).get("name"), "likes": next((i["userInteractionCount"] for i in ld.get("interactionStatistic", []) if "Like" in i["interactionType"]), None),
        "comments": ld.get("commentCount"), "body": body,
        "author_links": sorted(u for u in set(PULSE_RE.findall(page)) if auteur_re.search(u)),
    }

def slugify(u, auteur_re): return re.sub("-" + auteur_re, "", u.rsplit("/", 1)[1])

def write(rec, src):
    """Écrit une fiche via le contrat commun (voir newskit.fiche).

    Le collecteur ne compose plus le frontmatter lui-même : il fournit les
    champs qu'il connaît, et `fiche.ecrire` normalise, valide et sérialise. Une
    divergence de vocabulaire échoue ici, à l'import, plutôt que plus tard dans
    le graphe.
    """
    fn = src.chemin / f"{rec['date']}_{slugify(rec['url'], src.auteur)}.md"
    champs = {k: rec[k] for k in ("title", "subtitle", "date", "edition", "author", "url", "likes", "comments")}
    champs["plateforme"] = "LinkedIn"
    champs["publication"] = src.nom
    champs["complet"] = True
    champs["source"] = f"newsletter LinkedIn « {src.nom} »"
    champs["collected"] = time.strftime("%Y-%m-%d")
    corps = f"# {rec['title']}\n\n*{rec['subtitle']}*\n\n{rec['body']}"
    fiche.ecrire(fn, champs, corps)
    return fn.name

def collecter(src, update):
    auteur_re = re.compile(src.auteur)     # suffixe auteur dans les URLs /pulse/ (parfois tronqué)
    src.chemin.mkdir(parents=True, exist_ok=True)
    MANIFEST = src.manifeste
    SEED = src.chemin / "_seed_urls.txt"   # slugs relevés à la main (liste complète obtenue via navigateur connecté)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    page = fetch(src.newsletter) or ""
    queue = [u for u in sorted(set(PULSE_RE.findall(page))) if auteur_re.search(u)]
    if SEED.exists():
        queue += ["https://www.linkedin.com/pulse/" + l.strip() for l in SEED.read_text().splitlines() if l.strip()]
    if update: queue += list(manifest)   # repartir aussi des articles connus pour découvrir les nouveaux
    seen = set()
    while queue:
        url = queue.pop(0)
        if url in seen: continue
        seen.add(url)
        if update and url in manifest:
            queue += [u for u in manifest[url].get("author_links", []) if u not in seen]; continue
        print("->", url); pg = fetch(url, cache=True); time.sleep(1.5)
        if not pg: continue
        rec = parse(url, pg, auteur_re)
        if not rec: print("  !! pas de JSON-LD Article", file=sys.stderr); continue
        try:
            rec["file"] = write(rec, src)
        except fiche.IdentiteDejaVue as deja:          # la même édition, déjà venue par une autre source du graphe
            print(f"  déjà vue : {deja}"); continue
        manifest[url] = {k: rec[k] for k in ("file","title","date","edition","likes","comments","author_links")}
        queue += [u for u in rec["author_links"] if u not in seen]
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{src.nom} : {len(manifest)} éditions dans le manifeste")

def main():
    """Rend un code non nul si une page n'a pas pu être lue.

    Sans cela, une newsletter injoignable rend « 71 éditions au manifeste » —
    le chiffre de la semaine passée — et la routine affiche « ok ». Une source
    muette et une source refusée ne doivent pas se ressembler.
    """
    update = "--update" in sys.argv
    for src in cfg.toutes("LinkedIn"):
        collecter(src, update)
    if ECHECS:
        print(f"  ALERTE : {len(ECHECS)} page(s) non lue(s) ; ce ne sont pas des éditions "
              f"absentes, mais des requêtes qui n'ont pas abouti.", file=sys.stderr)
        for e in ECHECS[:5]:
            print(f"    {e}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__": sys.exit(main())

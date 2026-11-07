#!/usr/bin/env python3
"""Collecte des dépôts récents du domaine : GitHub et le Hub HuggingFace.

Deux plateformes du registre, un collecteur, parce que la question est la
même : quels bots, outils, modèles et jeux de données paraissent sur nos
sujets ? Chaque source déclare des requêtes ; chaque passage lit ce qui a été
créé (GitHub) ou modifié (HuggingFace) dans la fenêtre, au-dessus d'un seuil
d'activité, et écrit une fiche par dépôt nouveau. Le corps de la fiche est le
README ou la carte du modèle, tel quel, plafonné : c'est ce qui donne à la
couche 2 quelque chose à lire au lieu d'un nom.

GitHub, sans jeton : dix requêtes de recherche par minute, d'où une pause
entre les requêtes. HuggingFace : API publique du Hub, sans jeton.

Registre :
    « GitHub »       requetes, jours (30), par_requete (8), etoiles_min (20)
    « HuggingFace »  requetes, jours (30), par_requete (6), genres [models, datasets, papers],
                     activite_min (5 : likes + téléchargements)

Usage :
    python3 -m newskit.collecte_depots [--source <cle>] [--graphe <nom>] [--jours N]
Sortie : sources/<dossier>/<date>_<depot>.md + _manifest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402
from newskit.commun import agent_robot  # noqa: E402

CORPS_MAX = 14_000


def lire(url: str, accept: str | None = None, texte: bool = False):
    entetes = {"User-Agent": agent_robot()}
    if accept:
        entetes["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=entetes), timeout=40) as r:
        brut = r.read()
    return brut.decode("utf-8", "replace") if texte else json.loads(brut)


def readme(url: str) -> str:
    try:
        t = lire(url, texte=True)
    except Exception:
        return ""
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"^---\n.*?\n---\n", "", t, flags=re.S)          # carte HF : en-tête YAML retiré
    t = t.strip()
    return (t[:CORPS_MAX].rsplit("\n", 1)[0] + "\n\n[…coupé]") if len(t) > CORPS_MAX else t


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:80] or "depot"


def ecrire(src, manifeste: dict, e: dict, echecs: list[str]) -> bool:
    nom = f"{e['date']}_{slug(e['titre'])}.md"
    corps = [f"# {e['titre']}", "", e["entete"], ""]
    corps += [e["corps"], ""] if e["corps"] else ["*Pas de README ni de carte lisible : la fiche se réduit "
                                                  "à la description du dépôt.*", ""]
    corps += [f"Dépôt : {e['url']}", ""]
    champs = {"title": e["titre"], "subtitle": "", "author": e["auteur"], "date": e["date"], "url": e["url"],
              "plateforme": src.plateforme, "publication": src.nom, "complet": bool(e["corps"]),
              "collected": time.strftime("%Y-%m-%d"), "source": f"{src.plateforme}, requête « {e['requete']} »",
              "requete": e["requete"], **e["champs"]}
    try:
        fiche.ecrire(src.chemin / nom, champs, "\n".join(corps))
    except fiche.IdentiteDejaVue as deja:   # une autre fiche du graphe porte déjà ce document
        print(f"  déjà vue : {deja}")
        return False
    except ValueError as err:
        echecs.append(f"{src.nom} : {str(err)[:90]}")
        return False
    manifeste[e["url"]] = {"file": nom, "title": e["titre"], "date": e["date"], "complet": bool(e["corps"])}
    return True


def github(src, depuis: str, manifeste: dict, echecs: list[str]) -> tuple[int, int]:
    vus = ecrits = 0
    par = int(getattr(src, "par_requete", 8) or 8)
    etoiles_min = int(getattr(src, "etoiles_min", 20) or 20)
    for q in src.requetes:
        u = (f"https://api.github.com/search/repositories?q={urllib.parse.quote(f'{q} created:>{depuis}')}"
             f"&sort=stars&order=desc&per_page={par}")
        try:
            d = lire(u, accept="application/vnd.github+json")
        except Exception as e:
            echecs.append(f"{src.nom} : requête « {q} » : {type(e).__name__}: {str(e)[:60]}")
            time.sleep(7)
            continue
        for it in d.get("items", []):
            vus += 1
            if it["stargazers_count"] < etoiles_min or it["html_url"] in manifeste:
                continue
            corps = readme(f"https://raw.githubusercontent.com/{it['full_name']}/HEAD/README.md")
            sujets = ", ".join(it.get("topics") or [])
            e = {"titre": it["full_name"], "url": it["html_url"], "date": it["created_at"][:10],
                 "auteur": it["owner"]["login"], "requete": q, "corps": corps,
                 "entete": f"{it.get('description') or ''}\n\nÉtoiles : {it['stargazers_count']} · "
                           f"langage : {it.get('language') or '?'} · sujets : {sujets or '—'} · "
                           f"créé le {it['created_at'][:10]}, mis à jour le {it['pushed_at'][:10]}",
                 "champs": {"etoiles": int(it["stargazers_count"]), "langage": it.get("language") or "",
                            "sujets": sujets}}
            if ecrire(src, manifeste, e, echecs):
                ecrits += 1
        time.sleep(7)
    return vus, ecrits


def huggingface(src, depuis: str, manifeste: dict, echecs: list[str]) -> tuple[int, int]:
    vus = ecrits = 0
    par = int(getattr(src, "par_requete", 6) or 6)
    activite_min = int(getattr(src, "activite_min", 5) or 5)
    genres = list(getattr(src, "genres", None) or ["models", "datasets", "papers"])
    for q in src.requetes:
        for genre in genres:
            try:
                if genre == "papers":
                    items = lire("https://huggingface.co/api/papers/search?q=" + urllib.parse.quote(q))[:par]
                else:
                    items = lire(f"https://huggingface.co/api/{genre}?search={urllib.parse.quote(q)}"
                                 f"&sort=lastModified&direction=-1&limit={par}")
            except Exception as e:
                echecs.append(f"{src.nom} : {genre} « {q} » : {type(e).__name__}: {str(e)[:60]}")
                continue
            for it in items:
                vus += 1
                if genre == "papers":
                    pap = it.get("paper") or {}
                    pid = pap.get("id") or it.get("id") or ""
                    url = f"https://huggingface.co/papers/{pid}"
                    date = (it.get("publishedAt") or pap.get("publishedAt") or "")[:10]
                    if not pid or url in manifeste or (date and date < depuis):
                        continue
                    e = {"titre": it.get("title") or pap.get("title") or pid, "url": url, "date": date or depuis,
                         "auteur": ", ".join(a.get("name", "") for a in (pap.get("authors") or [])[:6]),
                         "requete": q, "corps": it.get("summary") or pap.get("summary") or "",
                         "entete": f"Papier HuggingFace · arXiv:{pid} · votes : {pap.get('upvotes', '?')}",
                         "champs": {"genre": "paper", "likes": int(pap.get("upvotes") or 0), "telechargements": 0,
                                    "papier_arxiv": pid}}
                else:
                    url = f"https://huggingface.co/{'datasets/' if genre == 'datasets' else ''}{it['id']}"
                    date = (it.get("lastModified") or it.get("createdAt") or "")[:10]
                    likes, dl = int(it.get("likes") or 0), int(it.get("downloads") or 0)
                    if url in manifeste or (date and date < depuis) or likes + dl < activite_min:
                        continue
                    corps = readme(url + "/raw/main/README.md")
                    e = {"titre": f"{it['id']} ({genre[:-1]})", "url": url, "date": date or depuis,
                         "auteur": it.get("author") or it["id"].split("/")[0], "requete": q, "corps": corps,
                         "entete": f"HuggingFace {genre[:-1]} · likes : {likes} · téléchargements : {dl} · "
                                   f"étiquettes : {', '.join((it.get('tags') or [])[:10]) or '—'}",
                         "champs": {"genre": genre[:-1], "likes": likes, "telechargements": dl}}
                if ecrire(src, manifeste, e, echecs):
                    ecrits += 1
            time.sleep(1)
    return vus, ecrits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    ap.add_argument("--jours", type=int, help="fenêtre, en jours (défaut : celle du registre)")
    args = ap.parse_args()
    sources = [s for s in cfg.toutes("GitHub", graphe=args.graphe) + cfg.toutes("HuggingFace", graphe=args.graphe)
               if not args.source or s.cle == args.source]
    if not sources:
        print("  aucune source « GitHub » ni « HuggingFace » au registre, rien à collecter.")
        return 0
    echecs: list[str] = []
    for src in sources:
        src.chemin.mkdir(parents=True, exist_ok=True)
        manifeste = json.loads(src.manifeste.read_text(encoding="utf-8")) if src.manifeste.exists() else {}
        jours = args.jours or int(getattr(src, "jours", 30) or 30)
        depuis = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
        vus, ecrits = (github if src.plateforme == "GitHub" else huggingface)(src, depuis, manifeste, echecs)
        src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {src.nom} : {vus} dépôt(s) lu(s) depuis le {depuis}, {ecrits} fiche(s) neuve(s), "
              f"{len(manifeste)} au total")
    for e in echecs:
        print(f"  !! {e}", file=sys.stderr)
    # Un refus n'est pas une absence : ce qui n'a pas pu être lu se lit dans le code de sortie (comme collecte_rss).
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

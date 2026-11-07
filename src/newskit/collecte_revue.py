#!/usr/bin/env python3
"""Collecte des revues scientifiques déclarées au registre, par Crossref.

Pourquoi Crossref et pas l'éditeur. *Automation in Construction* est publiée par
Elsevier : ScienceDirect refuse les requêtes de script et le texte intégral est
sous péage. Le contourner serait à la fois interdit et malhonnête. Crossref, lui,
est le registre public des DOI : titres, auteurs, dates, DOI, et les résumés
quand l'éditeur les y dépose — ce qui, pour Elsevier, est irrégulier. On obtient
donc une **couche bibliographique**, pas le texte, et la fiche le dit : sans
résumé, elle porte `complet: false`.

Ce que cela vaut pour le graphe. Un papier sans résumé apporte quand même son
titre, ses auteurs, sa date et son DOI : de quoi relier une revue à ses auteurs,
situer un sujet dans le temps, et donner à l'utilisateur le DOI qu'il ouvrira
avec son accès institutionnel. C'est peu et c'est vrai, ce qui vaut mieux que
beaucoup et faux.

Le filtre par mots-clés n'est pas un luxe : une revue compte des milliers
d'articles, dont la plupart ne touchent pas l'apprentissage automatique. Sans
filtre, la veille deviendrait un miroir de la revue.

**À vérifier avant de déclarer une revue.** Que l'éditeur dépose bien ses
résumés chez Crossref. Automation in Construction a été essayée puis retirée
pour cette raison : 1 508 articles retenus, zéro résumé, donc des fiches
réduites à un titre et un DOI. La commande qui tranche en cinq secondes :

    curl -s "https://api.crossref.org/journals/<ISSN>/works?rows=5&select=abstract"

Usage :
    python3 -m newskit.collecte_revue [--source <cle>] [--depuis AAAA] [--max N]
Sortie : sources/<dossier>/<date>_<doi-aplati>.md + _manifest.json
"""

from __future__ import annotations

import argparse
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

API = "https://api.crossref.org/journals/{issn}/works"
from newskit.commun import agent_robot  # noqa: E402
PAUSE = 1.0          # Crossref demande d'être joignable et raisonnable
PAR_PAGE = 100


def demander(url: str, essais: int = 4) -> dict | None:
    for i in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": agent_robot()})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == essais - 1:
                print(f"  !! {e} sur {url[:90]}", file=sys.stderr)
                return None
            time.sleep(PAUSE * (i + 2) * 3)
    return None


def resume(brut: str) -> str:
    """Un résumé Crossref arrive en JATS. On garde le texte, on jette le balisage."""
    if not brut:
        return ""
    t = re.sub(r"<jats:title[^>]*>.*?</jats:title>", " ", brut, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()


def date_de(it: dict) -> str:
    for cle in ("published-print", "published-online", "issued", "created"):
        parts = ((it.get(cle) or {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            a = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 1
            j = int(parts[2]) if len(parts) > 2 else 1
            return f"{a:04d}-{min(max(m,1),12):02d}-{min(max(j,1),28):02d}"
    return ""


def retenu(titre: str, mots_cles: list[str]) -> bool:
    if not mots_cles:
        return True
    bas = titre.lower()
    return any(m.lower() in bas for m in mots_cles)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--depuis", type=int, help="année de départ (défaut : celle du registre)")
    ap.add_argument("--max", type=int, default=0, help="plafond de fiches écrites (0 = sans plafond)")
    args = ap.parse_args()

    sources = [s for s in cfg.toutes("Revue") if not args.source or s.cle == args.source]
    if not sources:
        # Aucune source déclarée n'est pas une panne : le collecteur est piloté
        # par le registre, et le registre a le droit d'être vide de revues.
        print("  aucune source « Revue » au registre, rien à collecter.")
        return 0

    echecs = []
    for src in sources:
        src.chemin.mkdir(parents=True, exist_ok=True)
        mf = src.manifeste
        manifeste = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        depuis = args.depuis or int(getattr(src, "depuis", 0) or 0)
        mots_cles = list(getattr(src, "mots_cles", []) or [])

        params = {"rows": PAR_PAGE, "cursor": "*",
                  "select": "DOI,title,author,abstract,issued,published-print,"
                            "published-online,created,container-title,type,URL"}
        if depuis:
            params["filter"] = f"from-pub-date:{depuis}-01-01"

        vus = ecrits = sans_resume = 0
        while True:
            d = demander(API.format(issn=src.issn) + "?" + urllib.parse.urlencode(params))
            if not d:
                echecs.append(f"{src.nom} : interrogation interrompue")
                break
            msg = d["message"]
            items = msg.get("items", [])
            if not items:
                break
            for it in items:
                vus += 1
                doi = (it.get("DOI") or "").strip()
                titre = " ".join((it.get("title") or [""])[0].split())
                if not doi or not titre or doi in manifeste:
                    continue
                if not retenu(titre, mots_cles):
                    continue
                date = date_de(it)
                if not date:
                    continue
                res = resume(it.get("abstract", ""))
                auteurs = ", ".join(
                    " ".join(x for x in (a.get("given"), a.get("family")) if x)
                    for a in it.get("author", []))
                plat = re.sub(r"[^a-z0-9]+", "-", doi.lower()).strip("-")[:80]
                corps = [f"# {titre}", ""]
                corps += [res, ""] if res else [
                    "*Crossref ne porte pas de résumé pour cet article : la fiche est "
                    "bibliographique. Le texte intégral s'ouvre par le DOI, avec un accès "
                    "à la revue.*", ""]
                corps += [f"DOI : [{doi}](https://doi.org/{doi})", ""]
                champs = {
                    "title": titre, "subtitle": "", "author": auteurs, "date": date,
                    "url": f"https://doi.org/{doi}", "plateforme": "Revue",
                    "publication": src.nom, "complet": bool(res),
                    "collected": time.strftime("%Y-%m-%d"),
                    "source": f"Crossref, {(it.get('container-title') or [src.nom])[0]}",
                    "doi": doi, "issn": src.issn, "revue": src.nom,
                    "type": it.get("type", ""),
                }
                try:
                    fiche.ecrire(src.chemin / f"{date}_{plat}.md", champs, "\n".join(corps))
                except fiche.IdentiteDejaVue as deja:   # une autre fiche du graphe porte déjà ce document
                    print(f"  déjà vue : {deja}")
                    continue
                except ValueError as e:
                    echecs.append(f"{src.nom} : {str(e)[:90]}")
                    continue
                manifeste[doi] = {"file": f"{date}_{plat}.md", "title": titre,
                                  "date": date, "complet": bool(res)}
                ecrits += 1
                sans_resume += 0 if res else 1
                if args.max and ecrits >= args.max:
                    break
            mf.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
            if args.max and ecrits >= args.max:
                break
            suite = msg.get("next-cursor")
            if not suite or len(items) < PAR_PAGE:
                break
            params["cursor"] = suite
            time.sleep(PAUSE)

        print(f"{src.nom} : {vus} article(s) vus depuis {depuis or 'l’origine'}, "
              f"{ecrits} retenu(s) et écrit(s), dont {sans_resume} sans résumé, "
              f"{len(manifeste)} au total")
    if echecs:
        print("  ALERTE :", file=sys.stderr)
        for e in echecs:
            print(f"    {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

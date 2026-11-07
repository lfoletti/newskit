#!/usr/bin/env python3
"""Collecte de papiers par requête OpenAlex, pour les sources « OpenAlex » du registre.

OpenAlex est le registre ouvert des publications : titres, auteurs, dates, DOI,
revue, résumés (en index inversé, reconstitués ici) et lien d'accès libre. Une
source déclare des requêtes plein texte et une fenêtre en jours ; chaque
passage lit ce qui est paru depuis, en anglais, et écrit une fiche par
travail nouveau. Le pool « poli » (paramètre mailto) est obligatoire : sans
lui, la deuxième requête rend 429.

Ce que vaut une fiche : le résumé quand OpenAlex l'a, sinon une fiche
bibliographique marquée `complet: false`, comme pour Crossref.

Registre, plateforme « OpenAlex » :
    requetes      liste de requêtes plein texte
    jours         fenêtre de publication (défaut 30)
    par_requete   travaux lus par requête (défaut 8)
    domaines      facultatif : liste blanche d'identifiants de domaine OpenAlex
                  (primary_topic.field, ex. 20 = Economics, Econometrics…) ;
                  la recherche plein texte d'OpenAlex est lâche et
                  ramène des travaux hors sujet (30.09.2026 : aflatoxine,
                  frittage, commerce Chine-Afrique pour des requêtes
                  financières). Un travail sans domaine est alors écarté.
    mots_cles     facultatif : lexique du domaine ; un travail n'est gardé que
                  si son titre ou son résumé contient au moins un de ces
                  termes (sous-chaînes, sans casse). Écarte l'économie générale
                  que la recherche plein texte ramène encore.
    mots_cles_faibles  facultatif : termes génériques (governance, standard,
                  regulation…) qui ne gardent un travail que si son titre porte
                  aussi un terme de `ancre_titre` ; mots entiers, pluriel en -s
                  admis. Mesuré le 13.10.2026 sur un graphe de conformité : 42 fiches sur
                  142 sans aucun terme d'IA, entrées par « governance » (20),
                  « standard » (16), « regulation » (8, dont « dysregulation »).
    ancre_titre   termes du domaine exigés dans le titre pour qu'un mot faible
                  compte (mots entiers, sans casse).

Usage :
    python3 -m newskit.collecte_openalex [--source <cle>] [--graphe <nom>] [--jours N]
Sortie : sources/<dossier>/<date>_<doi-aplati>.md + _manifest.json
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
from newskit.commun import agent_robot, contact  # noqa: E402

API = "https://api.openalex.org/works"


def demander(url: str, essais: int = 3) -> dict | None:
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": agent_robot()}), timeout=40) as r:
                return json.load(r)
        except Exception as e:
            if i == essais - 1:
                print(f"  !! {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


def resume(inv: dict | None, mots: int = 400) -> str:
    """OpenAlex stocke les résumés en index inversé : on les reconstitue."""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for mot, idxs in inv.items():
        for i in idxs:
            pos[i] = mot
    return " ".join(pos[k] for k in sorted(pos)[:mots])


def institutions(w: dict, n: int = 3) -> str:
    """Les institutions des auteurs d'un travail et le code de leur pays (« Universitas Nurul Jadid (ID) »), dans
    l'ordre des auteurs, sans doublon, `n` au plus : ce qui situe une étude dont le résumé ne nomme pas de pays (N16)."""
    vues: list[str] = []
    for a in w.get("authorships") or []:
        for i in a.get("institutions") or []:
            nom, code = " ".join(str(i.get("display_name") or "").split()), str(i.get("country_code") or "").upper()
            x = f"{nom} ({code})" if nom and code else nom
            if x and x not in vues:
                vues.append(x)
    return ", ".join(vues[:n])


def mots_entiers(termes) -> re.Pattern | None:
    """Un motif qui reconnaît chaque terme en mot entier (pluriel en -s admis), sans casse."""
    termes = [str(t).strip() for t in (termes or []) if str(t).strip()]
    if not termes:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in termes) + r")s?(?![a-z0-9])", re.I)


def dans_le_sujet(titre: str, texte: str, lexique: list[str], faibles: re.Pattern | None,
                  ancre: re.Pattern | None) -> bool:
    """Un mot-clé du lexique suffit (sous-chaîne) ; un mot faible ne compte qu'avec l'ancre dans le titre."""
    if not lexique and not faibles:
        return True
    if any(m in texte.lower() for m in lexique):
        return True
    return bool(faibles and faibles.search(texte) and (ancre is None or ancre.search(titre)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    ap.add_argument("--jours", type=int, help="fenêtre, en jours (défaut : celle du registre)")
    args = ap.parse_args()
    sources = [s for s in cfg.toutes("OpenAlex", graphe=args.graphe) if not args.source or s.cle == args.source]
    if not sources:
        print("  aucune source « OpenAlex » au registre, rien à collecter.")
        return 0
    echecs: list[str] = []
    for src in sources:
        src.chemin.mkdir(parents=True, exist_ok=True)
        manifeste = json.loads(src.manifeste.read_text(encoding="utf-8")) if src.manifeste.exists() else {}
        jours = args.jours or int(getattr(src, "jours", 30) or 30)
        depuis = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
        par = int(getattr(src, "par_requete", 8) or 8)
        domaines = [str(int(x)) for x in (getattr(src, "domaines", None) or [])]
        filtre_dom = f",primary_topic.field.id:{'|'.join(domaines)}" if domaines else ""
        lexique = [str(m).lower() for m in (getattr(src, "mots_cles", None) or [])]
        faibles = mots_entiers(getattr(src, "mots_cles_faibles", None))
        ancre = mots_entiers(getattr(src, "ancre_titre", None))
        hors_sujet = 0
        vus = ecrits = sans_resume = 0
        for q in src.requetes:
            u = (f"{API}?search={urllib.parse.quote(q)}&per-page={par}&sort=publication_date:desc"
                 f"&filter=from_publication_date:{depuis},language:en{filtre_dom}&mailto={contact()}")
            d = demander(u)
            if d is None:
                echecs.append(f"{src.nom} : requête « {q} » sans réponse")
                continue
            for w in d.get("results", []):
                vus += 1
                doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
                loc = (w.get("best_oa_location") or {}) or {}
                pdf = loc.get("pdf_url") or loc.get("landing_page_url") or ""
                url = f"https://doi.org/{doi}" if doi else (pdf or w.get("id") or "")
                titre = " ".join((w.get("title") or "").split())
                if not url or not titre or url in manifeste:
                    continue
                date = w.get("publication_date") or f"{w.get('publication_year', '')}-01-01"
                # un travail « à paraître » porte une date future : la fiche prend la date du jour
                date = min(date, time.strftime("%Y-%m-%d"))
                res = resume(w.get("abstract_inverted_index"))
                if not dans_le_sujet(titre, titre + " " + res, lexique, faibles, ancre):
                    hors_sujet += 1
                    continue
                auteurs = ", ".join((a.get("author") or {}).get("display_name", "")
                                    for a in (w.get("authorships") or [])[:6])
                revue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
                plat = re.sub(r"[^a-z0-9]+", "-", (doi or titre).lower()).strip("-")[:80]
                corps = [f"# {titre}", ""]
                corps += [res, ""] if res else ["*OpenAlex ne porte pas de résumé pour ce travail : "
                                                "la fiche est bibliographique.*", ""]
                corps += [f"Revue : {revue or '?'} · citations : {w.get('cited_by_count', 0)} · "
                          f"requête : « {q} »", ""]
                if institutions(w):
                    corps += [f"Institutions des auteurs : {institutions(w)}", ""]
                if doi:
                    corps += [f"DOI : [{doi}](https://doi.org/{doi})", ""]
                if pdf:
                    corps += [f"Accès libre : {pdf}", ""]
                champs = {
                    "title": titre, "subtitle": "", "author": auteurs, "date": date, "url": url,
                    "plateforme": "OpenAlex", "publication": revue or "OpenAlex", "complet": bool(res),
                    "collected": time.strftime("%Y-%m-%d"), "source": f"OpenAlex, requête « {q} »",
                    "doi": doi, "revue": revue, "requete": q, "citations": int(w.get("cited_by_count") or 0),
                }
                try:
                    fiche.ecrire(src.chemin / f"{date}_{plat}.md", champs, "\n".join(corps))
                except fiche.IdentiteDejaVue as deja:   # une autre fiche du graphe porte déjà ce document
                    print(f"  déjà vue : {deja}")
                    continue
                except ValueError as e:
                    echecs.append(f"{src.nom} : {str(e)[:90]}")
                    continue
                manifeste[url] = {"file": f"{date}_{plat}.md", "title": titre, "date": date, "complet": bool(res)}
                ecrits += 1
                sans_resume += 0 if res else 1
            time.sleep(2)
        src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {src.nom} : {vus} travail(aux) lu(s) depuis le {depuis}, {ecrits} fiche(s) neuve(s)"
              f"{f', dont {sans_resume} sans résumé' if sans_resume else ''}"
              f"{f', {hors_sujet} écarté(s) hors lexique' if hors_sujet else ''}, {len(manifeste)} au total")
    for e in echecs:
        print(f"  !! {e}", file=sys.stderr)
    # Un refus n'est pas une absence : ce qui n'a pas pu être lu se lit dans le code de sortie (comme collecte_rss).
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

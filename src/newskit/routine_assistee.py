#!/usr/bin/env python3
"""Étage assisté de la routine : ce qui demande le navigateur.

Pourquoi cet étage existe séparément. Le navigateur ne se pilote pas depuis un
travail planifié : l'extension Chrome est conduite par une session vivante, et
il n'y a pas de chemin sans tête. Cet étage se fait donc dans une session, et
son rôle ici est double : dire précisément quoi faire, et consigner ce qui a été
fait ou a échoué dans le même rapport que l'étage automatique.

Une remarque sur les identifiants de session. Les sessions du navigateur sont
persistantes, ce qui suffit : une fois la page ouverte, l'API de la publication
rend le texte entier. Ce script ne lit ni ne stocke aucun cookie de session.
Extraire un identifiant de session du trousseau pour le déposer en clair dans un
fichier rendrait la collecte automatique, mais au prix de manipuler une
crédence : ce n'est pas un arbitrage à prendre sans le demander.

Sous-commandes :
    etat                  la liste de travail, précise et ordonnée
    consigner <fichier>   verse le résultat d'une session assistée dans ROUTINE.md

Le fichier de résultat est un JSON de la forme :
    {"faits": [{"quoi": "...", "detail": "..."}],
     "echecs": [{"quoi": "...", "erreur": "..."}]}

Usage :
    python3 -m newskit.routine_assistee etat
    python3 -m newskit.routine_assistee consigner /tmp/resultats.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

RAPPORT = RACINE / "ROUTINE.md"
EMPREINTES = RACINE / "graphe" / "couche2" / "empreintes.json"


def apercus_recuperables() -> list[dict]:
    """Aperçus payants que l'abonnement couvre, donc récupérables au navigateur.

    Un aperçu sur une publication où l'abonnement est gratuit n'est pas une
    tâche : c'est une limite. On ne l'inscrit pas dans une liste de travail,
    sinon la liste ne se vide jamais et cesse d'être lue.
    """
    out = []
    for src in cfg.toutes("Substack", abonnement="payant"):
        if not src.manifeste.exists():
            continue
        for slug, v in json.loads(src.manifeste.read_text(encoding="utf-8")).items():
            if not v.get("complet"):
                out.append({"publication": src.nom, "cle": src.cle, "slug": slug,
                            "session": src.session,
                            "titre": v.get("title", ""), "date": v.get("date", ""),
                            "url": v.get("url", ""), "mots": v.get("words", 0)})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def apercus_hors_abonnement() -> dict[str, int]:
    """Ce qui reste incomplet faute d'abonnement payant. Une limite, pas une tâche."""
    out: dict[str, int] = {}
    for src in cfg.toutes("Substack", abonnement="gratuit"):
        if not src.manifeste.exists():
            continue
        n = sum(1 for v in json.loads(src.manifeste.read_text(encoding="utf-8")).values()
                if not v.get("complet"))
        if n:
            out[src.nom] = n
    return out


def tronquees() -> list[str]:
    """Fiches portant encore un marqueur de troncature : défaut de capture."""
    mauvaises = []
    for d in ("sources", "recherche"):
        for f in (RACINE / d).rglob("*.md"):
            if f.name.startswith("_"):
                continue
            _, corps = fiche.lire_fichier(f)
            e, _a = fiche.verifier_corps(corps, {})
            if e:
                mauvaises.append(str(f.relative_to(RACINE)))
    return mauvaises


def couche2_en_retard() -> list[str]:
    """Fiches denses dont le corps a changé depuis la dernière extraction."""
    if not EMPREINTES.exists():
        return []
    import hashlib
    connues = json.loads(EMPREINTES.read_text(encoding="utf-8"))
    retard = []
    for rel, h in connues.items():
        p = RACINE / rel
        if not p.exists():
            continue
        _, corps = fiche.lire_fichier(p)
        if hashlib.sha256(corps.encode("utf-8")).hexdigest()[:16] != h:
            retard.append(rel)
    return retard


def liste_de_travail() -> list[str]:
    """Les tâches, en clair, dans l'ordre où les faire. Utilisée par les deux étages."""
    taches: list[str] = []

    # Une session déclarée au registre mais jamais déposée : la déclaration
    # seule ne collecte rien, et le dire ici évite de le découvrir dans six mois
    # sur un billet payant manquant.
    for src in cfg.toutes():
        if src.session and not cfg.session_declaree(src.session):
            etat = cfg.etat_session(src.session)
            taches.append(f"**Déposer la session de {src.nom}** "
                          + ("(entrée présente mais vide : la saisie interactive n'a "
                             "rien reçu)." if etat == "vide" else f"({etat}).")
                          + " La valeur attendue est l'en-tête `cookie` d'une requête "
                            "authentifiée, copié depuis les outils de développement. "
                            "Rien ne passe par l'historique du shell :")
            taches.append("  - dans un terminal réel, trousseau déverrouillé par le script : "
                          "`" + cfg.commande_de_depot(src.session) + "`")

    ap = apercus_recuperables()
    if ap:
        avec_session = {a["publication"] for a in ap if a.get("session")}
        taches.append(f"**{len(ap)} billet(s) payant(s) à compléter au navigateur.** "
                      "La session étant persistante, ouvrir la page suffit : l'API de la "
                      "publication rend alors le texte entier. Procédure dans JOURNAL.md, "
                      "session 6, puis `newskit.recuperer_tronquee`.")
        if avec_session:
            taches.append("  - une session est enregistrée pour "
                          f"{', '.join(sorted(avec_session))} et n'a pas suffi : "
                          "l'éprouver par `python3 -m newskit.config_sources verifier`, "
                          "la redéposer si elle a expiré")
        for a in ap[:8]:
            taches.append(f"  - {a['date']} · {a['titre'][:58]} · {a['url']}")

    tr = tronquees()
    if tr:
        taches.append(f"**{len(tr)} fiche(s) portent un marqueur de troncature.** "
                      "La capture a été plafonnée : relever la capacité de lecture et reprendre.")
        for t in tr[:6]:
            taches.append(f"  - {t}")

    r2 = couche2_en_retard()
    if r2:
        taches.append(f"**Couche 2 en retard sur {len(r2)} fiche(s) dense(s).** "
                      "La relancer coûte autant d'exécutions de sous-agents que de lots : "
                      "décider, plutôt que d'enchaîner.")
        for f in r2[:6]:
            taches.append(f"  - {f}")

    # Sources dont la collecte demande une navigation réelle. Ce n'est pas une
    # interdiction : le site s'ouvre normalement dans le navigateur connecté.
    # C'est un chemin qui ne se planifie pas, les requêtes de script ayant été
    # refusées (403) lors de la collecte initiale. Si la session déposée suffit,
    # `config_sources.py verifier` le dira, et la source pourra rejoindre
    # l'étage automatique.
    for src in cfg.toutes():
        if not src.assistee:
            continue
        taches.append(f"**{src.nom} : collecte au navigateur.** "
                      + (src.note.strip().rstrip(".") if src.note
                         else "relever les nouveautés et capturer")
                      + ". Les requêtes de script ont été refusées (403) lors de la "
                        "collecte initiale, d'où le navigateur.")
        if src.session:
            taches.append("  - une session est déclarée : "
                          "`python3 -m newskit.config_sources verifier` dit si elle ouvre "
                          "un chemin sans navigateur, auquel cas la collecte se planifie")
    return taches


def cmd_etat(_args) -> int:
    print("Liste de travail de l'étage assisté\n")
    for t in liste_de_travail():
        print("  " + t if t.startswith("  ") else "- " + t)
    hors = apercus_hors_abonnement()
    if hors:
        print("\nLimites, pas des tâches : abonnement gratuit, aperçu seul")
        for nom, n in sorted(hors.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4} billet(s)  {nom}")
    return 0


def cmd_consigner(args) -> int:
    """Verse le résultat d'une session assistée dans le rapport, erreurs en tête."""
    p = Path(args.fichier)
    if not p.exists():
        print(f"  Fichier de résultat absent : {p}", file=sys.stderr)
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    faits, echecs = d.get("faits", []), d.get("echecs", [])
    date = time.strftime("%Y-%m-%d %H:%M")

    bloc = [f"\n## Session assistée — {date}", ""]
    if echecs:
        bloc += [f"### {len(echecs)} échec(s)", ""]
        bloc += [f"- **{e.get('quoi','?')}** — {e.get('erreur','')}" for e in echecs] + [""]
    if faits:
        bloc += [f"### {len(faits)} action(s) menée(s)", ""]
        bloc += [f"- {f.get('quoi','?')}" + (f" — {f['detail']}" if f.get("detail") else "")
                 for f in faits] + [""]
    if not faits and not echecs:
        bloc += ["Rien à signaler.", ""]
    reste = liste_de_travail()
    bloc += ["### Reste à faire", ""] + ["  " + t if t.startswith("  ") else "- " + t
                                         for t in reste] + [""]

    ancien = RAPPORT.read_text(encoding="utf-8") if RAPPORT.exists() else "# Routine hebdomadaire\n"
    RAPPORT.write_text(ancien.rstrip() + "\n" + "\n".join(bloc), encoding="utf-8")
    print(f"  Consigné dans {RAPPORT.relative_to(RACINE)} : "
          f"{len(faits)} action(s), {len(echecs)} échec(s).")
    if echecs:
        print("  ALERTE : des échecs figurent au rapport.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("etat").set_defaults(fn=cmd_etat)
    p = sub.add_parser("consigner"); p.add_argument("fichier"); p.set_defaults(fn=cmd_consigner)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

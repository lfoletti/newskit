#!/usr/bin/env python3
"""Créer un graphe ex nihilo, ou dire ce qui manque à un graphe pour être complet.

Un graphe tient en sept pièces, chacune à sa place ; ce script les pose toutes d'un coup (ou dit lesquelles
manquent), pour qu'un nouveau graphe ne dépende plus de la mémoire de qui l'a fait la fois précédente
(porteur, 08.10.2026 : « architecture granulaire »). Les pièces :

  1. le registre (`config.yaml` du média, sinon `sources.yaml`) : bloc `graphes.<nom>` (libellé, domaine, bornes) — imprimé, à coller : le registre
     est commenté à la main et ne se réécrit pas par programme ;
  2. ses dossiers : `graphe/<nom>/couche2/fiches/`, `sources/<nom_>/` ;
  3. le registre du skill /kg (`~/.claude/skills/kg/graphes.json`) : libellé, chemins, outil, sujets déclarés — c'est
     lui que lisent l'éclaireur, le poseur, la mesure d'avance et les thèmes émergents ;
  4. au moins une source déclarée pour lui dans `sources.yaml` (`graphe: <nom>`) ;
  5. sa tâche planifiée (launchd), qui lance `-m newskit.routine_hebdo` sur le média : sous `taches/`, prête à poser
     dans /Library/LaunchDaemons ; pour un média qui a son installateur des daemons, sous
     `scripts/launchd_<nom_>.plist.exemple`, à ajouter à `TACHES` de `deploiement/installer_daemons_mac.sh` ;
  6. pour un média qui a un installateur de serveur : son nom dans `GRAPHES` (deploiement/installer_vps.sh) ;
  7. ses premiers passages : routine `--sans-commit`, pyramide `synthese.py construire`, banc `banc.py --preparer`.

Arrêter ou remettre une étape de la routine pour un graphe : `graphes.<nom>.etapes: {poseur: false, …}` au registre
(config_sources.ETAPES). Arrêter le graphe entier : retirer son daemon (`--retirer` ou ANCIENS de l'installateur).

    python3 -m newskit.nouveau_graphe ai-exemple --libelle "…" --domaine "…" --sujets "sujet 1 ; sujet 2" --heure 5 --minute 30
    python3 -m newskit.nouveau_graphe ai-exemple --controle        # les pièces présentes et manquantes, rien d'écrit
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import media  # noqa: E402 — l'heure de la nuit du média

SKILL = Path.home() / ".claude" / "skills" / "kg" / "graphes.json"
MODELE_PLIST = CODE / "launchd_graphe.plist.modele"   # un modèle à champs, sans nom de graphe (N2)
INSTALLATEUR = RACINE / "deploiement" / "installer_daemons_mac.sh"
INSTALLATEUR_VPS = RACINE / "deploiement" / "installer_vps.sh"


def nom_dossier(nom: str) -> str:
    return nom.replace("-", "_")


def pieces(nom: str) -> list[tuple[bool, str, str]]:
    """(présente, pièce, remède) pour chaque pièce du graphe."""
    out = []
    decl = cfg.charger().get("graphes", {}).get(nom)
    out.append((bool(decl), f"registre : graphes.{nom} (libellé, domaine)", f"coller le bloc imprimé par ce script dans {fichier_des_graphes().name}"))
    base = cfg.chemins_du_graphe(nom)["base"]
    out.append(((base / "couche2" / "fiches").is_dir(), f"dossiers : {base.relative_to(RACINE)}/couche2/fiches, sources/{nom_dossier(nom)}",
                "relancer sans --controle"))
    skill = json.loads(SKILL.read_text(encoding="utf-8")) if SKILL.exists() else {}
    e = skill.get(nom) or {}
    out.append((bool(e.get("sujets")), f"skill /kg : {SKILL.name} → {nom} avec ses sujets déclarés "
                f"({len(e.get('sujets') or [])} sujet(s))", "relancer avec --sujets « a ; b ; c »"))
    try:
        n_sources = len(cfg.toutes(graphe=nom))
    except Exception:  # noqa: BLE001
        n_sources = 0
    out.append((n_sources > 0, f"sources déclarées pour {nom} au registre : {n_sources}",
                "déclarer des sources (`graphe: " + nom + "`) ; prospecter d'abord : newskit.prospecter_source"))
    out += pieces_sources(nom)
    plist = chemin_plist(nom)
    out.append((plist.exists(), f"daemon : {plist.relative_to(RACINE)}", "relancer sans --controle (il est écrit)"))
    if INSTALLATEUR.exists():                       # les pièces des installateurs, pour le média qui les porte
        installe = f"{etiquette_launchd(nom)}|" in INSTALLATEUR.read_text(encoding="utf-8")
        out.append((installe, f"daemon : ligne de {INSTALLATEUR.name} (TACHES), puis sudo zsh {INSTALLATEUR.relative_to(RACINE)}",
                    f'ajouter "{etiquette_launchd(nom)}|$DEPOT/scripts/{plist.name}" à TACHES'))
    else:
        pose = Path("/Library/LaunchDaemons") / f"{etiquette_launchd(nom)}.plist"
        out.append((pose.exists(), f"tâche planifiée posée : {pose}",
                    f"sudo cp {plist} {pose} ; sudo launchctl bootstrap system {pose} (Linux : une ligne de cron)"))
    if INSTALLATEUR_VPS.exists():
        vps = re.search(r"^GRAPHES=.*\b" + re.escape(nom) + r"\b", INSTALLATEUR_VPS.read_text(encoding="utf-8"), flags=re.M)
        out.append((bool(vps), f"serveur : {nom} dans la liste GRAPHES de {INSTALLATEUR_VPS.name} (défaut de --graphes)",
                    "ajouter le nom à GRAPHES= et redéployer (deploiement/installer_vps.sh)"))
    out.append(((base / "graphe.json.gz").exists() or (base / "graphe.json").exists(), "premier graphe composé",
                f"python3 -m newskit.routine_hebdo --graphe {nom} --sans-commit"))
    out.append(((base / "synthese" / "notions.json").exists(), "pyramide de synthèses",
                f"python3 -m newskit.synthese construire --graphe {nom} --notions 150"))
    out.append(((base / "banc_questions.json").exists(), "jeu de questions du banc", f"python3 -m newskit.banc --graphe {nom} --preparer"))
    return out


SOURCES_PROPRES = (("eclaireur", "Recherche web", "Éclaireur — recherche web", "de l'éclaireur"),
                   ("canon", "Référence", "Références du canon", "du canon"))


def blocs_sources(nom: str) -> str:
    """Les sources de l'éclaireur et du canon, à coller sous `sources:` : un graphe né par la recherche web n'a pas
    d'autre source, et sans elles config_sources ne le connaît pas (un graphe de chansons, 22.10.2026)."""
    return "".join(f"  - cle: {cle}_{nom_dossier(nom)}\n    posture: mixte\n    plateforme: {plateforme}\n"
                   f"    nom: {libelle} ({nom})\n    dossier: {cle}/{nom}\n    abonnement: gratuit\n    dense: true\n"
                   f"    graphe: {nom}\n\n" for cle, plateforme, libelle, _ in SOURCES_PROPRES)


def pieces_sources(nom: str) -> list[tuple[bool, str, str]]:
    try:
        dossiers = {s.dossier for s in cfg.toutes(graphe=nom)}
    except Exception:  # noqa: BLE001
        dossiers = set()
    return [(f"{cle}/{nom}" in dossiers, f"source {qui} : {cle}/{nom}", "coller le bloc de sources imprimé par ce script")
            for cle, _, _, qui in SOURCES_PROPRES]


def bloc_registre(nom: str, libelle: str, domaine: str, plafond: int) -> str:
    return (f"  {nom}:\n    libelle: {libelle}\n    domaine: >-\n      {domaine}\n    plafond_couche2: {plafond}\n"
            f"    # modele_pyramide: claude-opus-5-5   # si une réponse fausse coûte cher\n"
            f"    # canon_critique: true                # mesurer aussi les voix critiques\n"
            f"    # etapes: {{poseur: false, banc: false}}   # arrêter une étape sans toucher au code\n")


def chemin_plist(nom: str) -> Path:
    """La tâche planifiée du graphe : sous scripts/, à champs, pour un média qui a son installateur des daemons ;
    sinon sous taches/, prête à poser dans /Library/LaunchDaemons."""
    if INSTALLATEUR.exists():
        return RACINE / "scripts" / f"launchd_{nom_dossier(nom)}.plist.exemple"
    return RACINE / "taches" / f"{etiquette_launchd(nom)}.plist"


def ecrire_plist(nom: str, libelle: str, heure: int, minute: int) -> Path:
    """Le daemon launchd du graphe, d'après le modèle à champs ; l'étiquette prend le préfixe et le nom de l'édition
    (bloc `edition:` du registre). Il lance le moteur installé (`-m newskit.routine_hebdo`) avec l'interpréteur qui le
    porte, sur le dossier du média (NEWSKIT_DONNEES). Avec un installateur des daemons, __DEPOT__, __MAISON__ et
    __UTILISATEUR__ restent pour lui ; sans, ils sont remplis ici."""
    cible = chemin_plist(nom)
    cible.parent.mkdir(parents=True, exist_ok=True)
    e = cfg.edition()
    champs = {"__ETIQUETTE__": f"{e['launchd']}.{e['nom']}-{nom}", "__GRAPHE__": nom, "__LIBELLE__": libelle,
              "__DATE__": time.strftime("%d.%m.%Y"), "__HEURE__": str(heure), "__MINUTE2__": f"{minute:02d}",
              "__MINUTE__": str(minute), "__DOSSIER__": nom_dossier(nom), "__PYTHON__": sys.executable}
    if not INSTALLATEUR.exists():
        (RACINE / "cache").mkdir(parents=True, exist_ok=True)       # le journal de la tâche y est écrit
        champs.update({"__DEPOT__": str(RACINE), "__MAISON__": str(Path.home()), "__UTILISATEUR__": getpass.getuser()})
    t = MODELE_PLIST.read_text(encoding="utf-8")
    for k, v in champs.items():
        t = t.replace(k, v)
    cible.write_text(t, encoding="utf-8")
    return cible


def ecrire_skill(nom: str, libelle: str, resume: str, sujets: list[str]) -> None:
    skill = json.loads(SKILL.read_text(encoding="utf-8")) if SKILL.exists() else {}
    e = skill.get(nom) or {}
    e.update({"libelle": e.get("libelle") or f"{libelle} (dépôt {cfg.edition()['nom']}, graphe à part)",
              "racine": str(RACINE), "carte": f"graphe/{nom}/carte.md", "graphe": f"graphe/{nom}/graphe.json",
              "outil": f"-m newskit.interroger_graphe --graphe {nom}", "python": sys.executable,
              "architecture": "ARCHITECTURE.md", "resume": e.get("resume") or resume or libelle.lower(),
              "routine": f"-m newskit.routine_hebdo --graphe {nom}", "rapport": f"graphe/{nom}/ROUTINE.md"})
    if sujets:
        e["sujets"] = sujets
    e.setdefault("sujets", [])
    skill[nom] = e
    SKILL.parent.mkdir(parents=True, exist_ok=True)
    SKILL.write_text(json.dumps(skill, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def etiquette_launchd(nom: str) -> str:
    """L'étiquette launchd du daemon d'un graphe : `<media.launchd>.<media.nom>-<graphe>` (com.exemple.media-ai-x)."""
    e = cfg.edition()
    return f"{e['launchd']}.{e['nom']}-{nom}"


def fichier_des_graphes() -> Path:
    """Le fichier qui tient le bloc `graphes:` : le config.yaml du média s'il en a un, sinon le registre des sources."""
    return media.FICHIER if media.courant() else cfg.FICHIER


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Les options ; l'heure de la nuit par défaut est celle du média (`media.frequence` de config.yaml, 07.11.2026)."""
    heure, minute = media.frequence()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nom", help="nom du graphe : minuscules, chiffres, tirets (ai-exemple)")
    ap.add_argument("--libelle", help="libellé lisible (« Conformité IA — AI Act, RGPD, nLPD, normes »)")
    ap.add_argument("--domaine", help="le domaine en anglais, pour l'extraction (une à trois lignes)")
    ap.add_argument("--resume", help="une ligne en français pour le skill /kg (défaut : le libellé)")
    ap.add_argument("--sujets", help="sujets déclarés, séparés par « ; » (éclaireur, poseur, avance, banc)")
    ap.add_argument("--plafond", type=int, default=40, help="fiches extraites par passage (défaut 40)")
    ap.add_argument("--heure", type=int, default=heure, help=f"heure de la nuit (défaut du média : {heure})")
    ap.add_argument("--minute", type=int, default=minute, help=f"minute (défaut du média : {minute})")
    ap.add_argument("--controle", action="store_true", help="dire les pièces présentes et manquantes, n'écrire rien")
    return ap.parse_args(argv)


def main() -> int:
    a = arguments()
    nom = a.nom
    if not cfg.NOM_DE_GRAPHE.match(nom):
        print(f"  nom invalide : {nom} (minuscules, chiffres, tirets, 2 à 41 caractères)", file=sys.stderr)
        return 2
    if nom in cfg.ANCIENS_NOMS:
        print(f"  {nom} est l'ancien nom de {cfg.ANCIENS_NOMS[nom]}", file=sys.stderr)
        return 2
    if not a.controle:
        if not a.libelle or not a.domaine:
            print("  --libelle et --domaine sont requis pour créer (ou --controle pour seulement regarder)", file=sys.stderr)
            return 2
        base = cfg.chemins_du_graphe(nom)["base"]
        (base / "couche2" / "fiches").mkdir(parents=True, exist_ok=True)
        (RACINE / "sources" / nom_dossier(nom)).mkdir(parents=True, exist_ok=True)
        sujets = [s.strip() for s in (a.sujets or "").split(";") if s.strip()]
        ecrire_skill(nom, a.libelle, a.resume or "", sujets)
        plist = ecrire_plist(nom, a.libelle, a.heure, a.minute)
        print(f"  écrit : {base.relative_to(RACINE)}/couche2/fiches, sources/{nom_dossier(nom)}, {SKILL}, {plist.relative_to(RACINE)}")
        print(f"\n  À coller dans {fichier_des_graphes().name}, sous `graphes:` (l'ordre de déclaration est celui des daemons et de la page) :\n")
        print(bloc_registre(nom, a.libelle, a.domaine, a.plafond))
        print("  À coller dans sources.yaml, sous `sources:` (la recherche web et le canon nourrissent le graphe dès sa naissance) :\n")
        print(blocs_sources(nom))
    print(f"  Pièces du graphe {nom} :")
    manquantes = 0
    for presente, piece, remede in pieces(nom):
        manquantes += not presente
        print(f"    {'✓' if presente else '✗'}  {piece}" + ("" if presente else f"\n         → {remede}"))
    print(f"  {manquantes} pièce(s) manquante(s)" if manquantes else "  graphe complet")
    return 0


if __name__ == "__main__":
    sys.exit(main())

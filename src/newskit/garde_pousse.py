#!/usr/bin/env python3
"""Le garde du hook pre-push : refuse un secret dans ce qui part (mainteneur, 01.11.2026, point 4 de la lecture critique de
sécurité). Lit sur l'entrée standard un diff (`git log -p`, et le message de chaque commit précédé de
« +++ (message de commit) »), écrit sur l'erreur standard ce qu'il trouve sans jamais montrer la valeur, et sort en 1.

Deux contrôles :
- les valeurs réelles des fichiers ~/.secrets/*.env (ou GARDE_SECRETS), dans tout le diff, corpus compris ;
- les motifs de clés connus, hors du corpus (`sources/`, `graphe/`) : les fiches de sécurité citent des clés d'exemple.
Bibliothèque standard seule : le hook le lance par `python3 -I`."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MOTIFS = [
    ("clé Anthropic", re.compile(r"sk-ant-[A-Za-z0-9]{2,8}-[A-Za-z0-9_\-]{20,}")),
    ("clé OpenAI", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("jeton GitHub", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}")),
    ("clé AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("clé privée", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("jeton Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("clé Google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
]
CORPUS = ("sources/", "graphe/")
# Une adresse (URL, domaine, IP) de configuration est publique (01.11.2026 : TH_GATEWAY, IMAP_HOTE et l'IP du VPS
# paraissent dans les manuels ; le DNS les donne) ; elle compte si son nom la dit secrète (URL de ping d'un battement).
ADRESSE = re.compile(r"https?://\S+|[\d.]+|[a-z0-9.-]+\.[a-z]{2,}")
NOM_SECRET = re.compile(r"KEY|SECRET|TOKEN|PASS|MOT|MDP|SEL|CLE|JETON|BATTEMENT", re.I)
LONGUEUR_MIN = 10


def valeurs_secretes(dossier: Path) -> list[tuple[str, str, str]]:
    """(fichier, variable, valeur) des fichiers .env du dossier ; ni un chemin, ni une valeur courte, ni une adresse
    publique de configuration. Une adresse courriel compte : rien de personnel dans un fichier suivi."""
    vues = []
    for f in sorted(Path(dossier).glob("*.env")):
        for ligne in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ligne)
            if not m:
                continue
            v = m.group(2).strip().strip("'\"")
            publique = ADRESSE.fullmatch(v) and not NOM_SECRET.search(m.group(1))
            if len(v) >= LONGUEUR_MIN and not v.startswith(("/", "~", "$")) and not publique:
                vues.append((f.name, m.group(1), v))
    return vues


def fuites(lignes: list[str], valeurs: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """(chemin, ce qui a été trouvé) pour chaque ligne ajoutée qui porte un secret ; jamais la valeur."""
    trouve, chemin = [], ""
    for l in lignes:
        if l.startswith("+++ "):
            chemin = l[4:].removeprefix("b/").strip()
            continue
        if not l.startswith("+"):
            continue
        texte = l[1:]
        for fichier, var, v in valeurs:
            if v in texte:
                trouve.append((chemin, f"valeur de {var} ({fichier})"))
        if not chemin.startswith(CORPUS):
            trouve += [(chemin, nom) for nom, rx in MOTIFS if rx.search(texte)]
    return list(dict.fromkeys(trouve))


def main() -> int:
    dossier = Path(os.environ.get("GARDE_SECRETS") or Path.home() / ".secrets")
    valeurs = valeurs_secretes(dossier) if dossier.is_dir() else []
    r = fuites(sys.stdin.buffer.read().decode("utf-8", errors="replace").splitlines(), valeurs)
    for chemin, quoi in r:
        print(f"pre-push : secret refusé dans {chemin} : {quoi}", file=sys.stderr)
    return 1 if r else 0


if __name__ == "__main__":
    sys.exit(main())

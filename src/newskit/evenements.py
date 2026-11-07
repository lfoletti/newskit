"""Le journal des événements de veille d'un graphe (NEWSKIT.md, N3 ; mainteneur, 06.11.2026 : « ok pour 1 et B »).

Le moteur l'écrit à chaque passage, l'édition le lit (le démarchage : qui écrire, et quoi citer). Un fichier par graphe,
`graphe/<g>/evenements.jsonl`, une ligne par événement, ajoutée, jamais réécrite. Deux sortes à N3 :

- `auteur_nouveau` : un auteur que le graphe n'avait jamais vu ;
- `fiche_auteur_connu` : une fiche nouvelle d'un auteur déjà vu.

Les autres sortes prévues (source devenue payante ou muette, lacune comblée, thème émergent) viendront à N7.

Le premier passage enregistre les auteurs et les fiches déjà là (`evenements_etat.json`) sans écrire d'événement :
sans lui, tout le corpus passerait pour nouveau.

    python3 -m newskit.evenements --graphe <g>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from newskit import commun
from newskit import config_sources as cfg
from newskit import fiche

JOURNAL = "evenements.jsonl"
ETAT = "evenements_etat.json"


def observer(entrees, base: Path, quand: str) -> int:
    """Compare les fiches (`chemin`, `author`, `url`) à ce que le graphe connaît, ajoute au journal un événement par
    auteur d'une fiche nouvelle, et rend le nombre d'événements écrits. Au premier passage : enregistre seulement."""
    base = Path(base)
    etat_f = base / ETAT
    premier = not etat_f.exists()
    etat = {"auteurs": [], "fiches": []} if premier else json.loads(etat_f.read_text(encoding="utf-8"))
    auteurs, fiches = set(etat["auteurs"]), set(etat["fiches"])
    nouveaux: list[dict] = []
    for e in entrees:
        if e["chemin"] in fiches:
            continue
        fiches.add(e["chemin"])
        for nom in commun.separer_auteurs(e.get("author")):
            cle = commun.normaliser_auteur(nom)
            sorte = "fiche_auteur_connu" if cle in auteurs else "auteur_nouveau"
            auteurs.add(cle)
            if not premier:
                nouveaux.append({"quand": quand, "sorte": sorte, "auteur": nom, "fiche": e["chemin"],
                                 "url": e.get("url") or ""})
    base.mkdir(parents=True, exist_ok=True)
    if nouveaux:
        with (base / JOURNAL).open("a", encoding="utf-8") as f:
            for ev in nouveaux:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    tmp = etat_f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"auteurs": sorted(auteurs), "fiches": sorted(fiches)}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(etat_f)
    return len(nouveaux)


def lire(base: Path, depuis: str = "") -> list[dict]:
    """Les événements du journal, ceux dont la date est au moins `depuis` (texte « AAAA-MM-JJ[ HH:MM] »)."""
    f = Path(base) / JOURNAL
    if not f.exists():
        return []
    evs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [e for e in evs if e["quand"] >= depuis]


def fiches_du_graphe(g: str):
    """Les fiches propres au graphe, depuis le disque : chemin relatif à `sources/`, auteur, adresse."""
    for d in cfg.dossiers_du_graphe(g, propres=True):
        if not d.startswith("sources/"):
            continue
        for chemin in sorted((cfg.RACINE / d).rglob("*.md")):
            if chemin.name.startswith("_"):
                continue
            champs, _ = fiche.lire_fichier(chemin)
            yield {"chemin": chemin.relative_to(cfg.SOURCES).as_posix(), "author": champs.get("author"),
                   "url": champs.get("url")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--graphe", required=True)
    a = ap.parse_args(argv)
    base = cfg.chemins_du_graphe(a.graphe)["base"]
    premier = not (base / ETAT).exists()
    n = observer(fiches_du_graphe(a.graphe), base, time.strftime("%Y-%m-%d %H:%M"))
    print("premier passage : auteurs et fiches enregistrés, aucun événement" if premier
          else f"{n} événement(s) ajouté(s) à {base / JOURNAL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

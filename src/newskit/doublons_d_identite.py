#!/usr/bin/env python3
"""Les doublons d'identité déjà sur le disque : deux fiches d'un même graphe qui portent le même document (même adresse
canonique, même identifiant arXiv, même DOI ; `fiche.cles_d_identite`). CHANTIER_GENERIQUE.md § 4.2, 06.11.2026.

    doublons_d_identite.py --etat      les groupes, la fiche gardée de chacun, les fiches à retirer
    doublons_d_identite.py --fondre    retire les fiches en trop (git les garde : `git checkout` les rend)
    doublons_d_identite.py --manifestes    retire des manifestes de flux les entrées dont la fiche porte un autre article

La fiche gardée est lue par tous les graphes qui lisaient les autres (sinon un graphe perdrait le document), puis
déjà extraite (son lot de couche 2 reste valable ; celui d'une fiche retirée tombe à la composition, `lot_orphelin`),
puis la plus ancienne (date, collecte, chemin). Entre versions de langue d'un article (clé `article:`), la française
passe d'abord, puis l'anglaise. Un groupe sans telle fiche n'est pas fondu : il est dit, à trancher.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from newskit import fiche  # noqa: E402


@dataclass
class Groupe:
    fiches: list[str]  # chemins relatifs à la racine des données
    cles: list[str]
    garde: str | None = None
    retirees: list[str] = field(default_factory=list)


def extraites() -> set[str]:
    """Les fiches déjà extraites en couche 2, d'après les empreintes de chaque graphe."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    vues: set[str] = set()
    for g in cfg.graphes():
        c2 = cfg.chemins_du_graphe(g).get("couche2")
        p = c2 / "empreintes.json" if c2 else None
        if p and p.exists():
            vues |= set(json.loads(p.read_text(encoding="utf-8")))
    return vues


# Versions de langue d'un article (clé `article:`, mainteneur, 07.11.2026) : la française reste, sinon l'anglaise, avant
# l'extraction faite et l'ancienneté.
RANG_LANGUE = {"fr": 0, "en": 1, "": 1.5}  # sans segment : la version par défaut du site


def _meme_page(a: str, b: str) -> bool:
    """La même page : la même adresse canonique, ou le même dernier segment significatif dans la même langue
    (un site sert /en/x et /index.php/en/x ; un autre a changé de domaine). Le segment entier, sans la coupe à 80 caractères
    des noms de fiche."""
    from urllib.parse import unquote, urlsplit  # noqa: PLC0415

    def bout(u: str) -> str:
        segs = [unquote(x).lower() for x in urlsplit(u).path.split("/") if x]
        segs = [
            x
            for x in segs
            if not fiche._langue_du_segment(x) and x not in {"index.html", "index.htm", "index.php", "index", "amp"}
        ]
        return (segs[-1] if segs else "") + "|" + fiche.langue_de_l_adresse(u)

    return fiche.url_canonique(a) == fiche.url_canonique(b) or (bout(a) == bout(b) and bout(a) != "|")


def reparer_manifestes(sources: Path, plan: bool = False) -> int:
    """Retire des manifestes de flux (dossiers `rss`) les entrées qui partagent leur fiche avec une autre et dont la fiche
    porte une autre adresse ; rend leur nombre. Un nom de fiche tiré du seul segment de langue (2026-11-07_en.md, 07.11.2026) était écrasé par l'article
    suivant du même jour : l'article revient à la collecte suivante s'il est encore au flux. Une entrée dont la fiche est
    partie reste : elle n'est pas recollectée."""
    n = 0
    for m in sorted(sources.rglob("_manifest.json")):
        if "rss" not in m.relative_to(sources).parts:
            continue
        try:
            man = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        def url(f: Path) -> str:
            with open(f, encoding="utf-8", errors="replace") as h:
                return str(fiche.lire(h.read(fiche.TETE))[0].get("url") or "")

        par_fichier: dict[str, list[str]] = {}
        for u, v in man.items():
            if isinstance(v, dict) and v.get("file") and (m.parent / v["file"]).exists():
                par_fichier.setdefault(v["file"], []).append(u)
        # Seule une fiche partagée a été écrasée : une entrée seule sur sa fiche est la même page, redirigée ou
        # autrement écrite. Entre entrées partagées, reste celle qui est l'adresse de la fiche.
        faux = [u for f, us in par_fichier.items() if len(us) > 1 for u in us if not _meme_page(u, url(m.parent / f))]
        n += len(faux)
        if faux and not plan:
            for u in faux:
                del man[u]
            m.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    return n


def _rel(p: Path, racine: Path) -> str:
    return str(Path(p).resolve().relative_to(racine))


def etat(graphes_vus: list[str] | None = None) -> list[Groupe]:
    """Les groupes de doublons, de tous les graphes ou des seuls `graphes_vus`."""
    racine = fiche.racine_des_donnees().resolve()
    carte = fiche.dossiers_des_graphes()
    # Union des fiches liées par une clé commune, graphe par graphe : deux graphes sans lien ne font pas un doublon.
    parent: dict[str, str] = {}

    def chef(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cles_de: dict[str, set[str]] = {}
    for g, dossiers in carte.items():
        if graphes_vus is not None and g not in graphes_vus:
            continue
        porteurs: dict[str, list[str]] = {}
        for d in dossiers:
            for p in sorted((racine / d).rglob("*.md")):
                for k in fiche.cles_du_fichier(p):
                    porteurs.setdefault(k, []).append(_rel(p, racine))
        for k, ps in porteurs.items():
            ps = sorted(set(ps))
            if len(ps) < 2:
                continue
            for x in ps:
                cles_de.setdefault(x, set()).add(k)
                parent[chef(x)] = chef(ps[0])
    membres: dict[str, list[str]] = {}
    for x in cles_de:
        membres.setdefault(chef(x), []).append(x)
    deja = extraites()

    def graphes(x: str) -> set[str]:
        return {g for g, ds in carte.items() if any(Path(x).is_relative_to(d) for d in ds)}

    def champs(x: str) -> dict:
        with open(racine / x, encoding="utf-8", errors="replace") as f:
            return fiche.lire(f.read(fiche.TETE))[0]

    def anciennete(x: str) -> tuple:
        c = champs(x)
        return (str(c.get("date") or "9999"), str(c.get("collected") or "9999"), x)

    def rang(x: str) -> int:
        return RANG_LANGUE.get(fiche.langue_de_l_adresse(str(champs(x).get("url") or "")), 2)

    groupes = []
    for xs in sorted(membres.values()):
        xs = sorted(xs)
        tous = set().union(*(graphes(x) for x in xs))
        candidates = [x for x in xs if graphes(x) >= tous]
        g = Groupe(xs, sorted(set().union(*(cles_de[x] for x in xs))))
        if candidates:
            g.garde = min(candidates, key=lambda x: (rang(x), x not in deja, anciennete(x)))
            g.retirees = [x for x in xs if x != g.garde]
        groupes.append(g)
    return groupes


def fondre() -> tuple[list[str], list[Groupe]]:
    """Retire les fiches en trop ; rend (retirées, groupes à trancher)."""
    racine = fiche.racine_des_donnees().resolve()
    retirees, a_trancher = [], []
    for g in etat():
        if not g.garde:
            a_trancher.append(g)
            continue
        for x in g.retirees:
            (racine / x).unlink()
            retirees.append(x)
    fiche.oublier_index()
    return retirees, a_trancher


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--etat", action="store_true")
    m.add_argument("--fondre", action="store_true")
    m.add_argument("--manifestes", action="store_true", help="retirer des manifestes de flux les entrées écrasées")
    m.add_argument("--manifestes-plan", action="store_true", help="les compter sans rien changer")
    a = ap.parse_args()
    if a.manifestes or a.manifestes_plan:
        n = reparer_manifestes(fiche.racine_des_donnees() / "sources", plan=a.manifestes_plan)
        print(
            f"{n} entrée(s) de manifeste dont la fiche porte un autre article"
            + (" (plan)" if a.manifestes_plan else " retirée(s)")
        )
        return 0
    if a.fondre:
        retirees, a_trancher = fondre()
        for x in retirees:
            print(f"  retirée  {x}")
        print(f"{len(retirees)} fiche(s) retirée(s), {len(a_trancher)} groupe(s) à trancher")
        for g in a_trancher:
            print(f"  à trancher  {', '.join(g.fiches)}  ({', '.join(g.cles)})")
        return 0
    groupes = etat()
    for g in groupes:
        print(
            f"  {g.garde or 'À TRANCHER'}"
            + "".join(f"\n      − {x}" for x in g.retirees)
            + f"\n      ({', '.join(g.cles)})"
        )
    print(
        f"{len(groupes)} groupe(s), {sum(len(g.retirees) for g in groupes)} fiche(s) à retirer, "
        f"{sum(1 for g in groupes if not g.garde)} à trancher"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

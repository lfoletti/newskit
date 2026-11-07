"""La commande `newskit` : un module du moteur par sous-commande (noms anglais ; les noms français restent acceptés).

    newskit                      les commandes, une ligne chacune
    newskit run --graph g        le passage d'un graphe (newskit.routine_hebdo ; ancien nom : routine --graphe)
    newskit new-graph <nom>      fait naître un graphe, ou dit ce qui lui manque (--check)
    newskit query --graph g search "…"   interroge le graphe, sans appel au modèle
    newskit costs --by graph     la dépense des sept derniers jours
    newskit test                 essaie le moteur sur un graphe d'exemple, sans frais ; un passage ne part qu'après un essai réussi
    newskit media                le média lu : langue, traductions, modèles par rôle
    newskit <module> [options]   tout module qui se lance seul (python3 -m newskit.<module>)
"""

from __future__ import annotations

import ast
import pkgutil
import runpy
import sys
from importlib import resources

import newskit

ALIAS = {"routine": "routine_hebdo", "nouveau-graphe": "nouveau_graphe", "graphe": "interroger_graphe"}
COURTES = {"test": ["temoin", "nuit"]}      # une commande courte pour une commande avec ses arguments (07.11.2026)

# Les commandes en anglais (07.11.2026) : le nom, puis les options et leurs valeurs, traduits avant le module ; les
# noms français restent acceptés (la première édition et ses daemons les appellent).
ANGLAIS = {"run": "routine_hebdo", "new-graph": "nouveau_graphe", "query": "interroger_graphe", "costs": "couts"}
OPTIONS = {"--graph": "--graphe", "--check": "--controle", "--label": "--libelle", "--domain": "--domaine",
           "--summary": "--resume", "--subjects": "--sujets", "--cap": "--plafond", "--hour": "--heure",
           "--by": "--par", "--since": "--depuis", "--limit": "--limite", "--length": "--longueur",
           "--no-commit": "--sans-commit", "--no-email": "--sans-courriel", "--no-collect": "--sans-collecte",
           "--days": "--jours", "--no-model": "--sans-modele"}
VALEURS = {"--par": {"step": "poste", "graph": "graphe", "model": "modele", "day": "jour"}}
SOUS_COMMANDES = {"interroger_graphe": {"search": "chercher", "neighbours": "voisinage", "path": "chemin",
                                        "explain": "expliquer", "note": "fiche", "top": "sommets", "gap": "lacune"}}


def traduire(nom: str, args: list[str]) -> list[str]:
    """Les options anglaises d'une commande en leurs noms français. Une option ne se traduit qu'à sa place d'option,
    une valeur qu'après son option, une sous-commande qu'en premier argument libre : un terme cherché reste tel quel."""
    out, attend, sous = [], None, SOUS_COMMANDES.get(nom, {})
    libres = 0
    for a in args:
        if attend is not None:
            out.append(VALEURS.get(attend, {}).get(a, a))
            attend = None
            continue
        if a.startswith("--") and (libres == 0 or not sous):
            cle, egal, val = a.partition("=")
            cle = OPTIONS.get(cle, cle)
            if egal:
                out.append(f"{cle}={VALEURS.get(cle, {}).get(val, val)}")
            else:
                out.append(cle)
                if cle in VALEURS or cle in {"--graphe", "--libelle", "--domaine", "--resume", "--sujets", "--plafond",
                                             "--heure", "--depuis", "--limite", "--longueur", "--jours"}:
                    attend = cle
            continue
        if a.startswith("--") and sous:
            cle, egal, val = a.partition("=")
            out.append(OPTIONS.get(cle, cle) + (egal + val if egal else ""))
            if not egal and OPTIONS.get(cle, cle) in {"--limite", "--longueur", "--type"}:
                attend = OPTIONS.get(cle, cle)
            continue
        out.append(sous.get(a, a) if libres == 0 else a)
        libres += 1
    return out


def commandes() -> dict[str, str]:
    """Les modules qui se lancent seuls (un bloc `if __name__ == "__main__"`), et la première ligne de leur aide."""
    out = {}
    for m in pkgutil.iter_modules(newskit.__path__):
        if m.ispkg or m.name in {"cli", "__main__"}:
            continue
        src = resources.files(newskit).joinpath(f"{m.name}.py").read_text(encoding="utf-8")
        arbre = ast.parse(src)
        if not any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in arbre.body):
            continue
        doc = (ast.get_docstring(arbre) or "").strip().splitlines()
        out[m.name] = doc[0] if doc else ""
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "aide"}:
        print(__doc__.strip(), "\n")
        for nom, doc in commandes().items():
            print(f"  {nom:<28} {doc[:90]}")
        return 0
    if argv[0] in {"--version", "-V"}:
        print(f"newskit {newskit.__version__}")
        return 0
    if argv[0] in COURTES:
        argv = [*COURTES[argv[0]], *argv[1:]]
    nom = ANGLAIS.get(argv[0]) or ALIAS.get(argv[0], argv[0].replace("-", "_"))
    if nom not in commandes():
        print(f"newskit : commande inconnue « {argv[0]} » ; `newskit` les liste", file=sys.stderr)
        return 2
    sys.argv = [f"newskit {argv[0]}", *traduire(nom, argv[1:])]
    try:
        runpy.run_module(f"newskit.{nom}", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

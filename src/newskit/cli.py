"""La commande `newskit` : un module du moteur par sous-commande.

    newskit                      les commandes, une ligne chacune
    newskit routine --graphe g   la nuit d'un graphe (newskit.routine_hebdo)
    newskit test                 essaie le moteur sur un graphe d'exemple, sans frais ; un passage ne part qu'après un essai réussi
    newskit temoin nuit          le même essai, sous son ancien nom
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
    nom = ALIAS.get(argv[0], argv[0].replace("-", "_"))
    if nom not in commandes():
        print(f"newskit : commande inconnue « {argv[0]} » ; `newskit` les liste", file=sys.stderr)
        return 2
    sys.argv = [f"newskit {argv[0]}", *argv[1:]]
    try:
        runpy.run_module(f"newskit.{nom}", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

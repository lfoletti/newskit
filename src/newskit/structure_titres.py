# Adapté de graphify 0.9.20 (graphify/extractors/markdown.py, graphify/extract.py, graphify/extractors/resolution.py),
# sous licence MIT :
#
# Copyright (c) 2026 Safi Shamsi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""La structure des titres d'un ensemble de fiches Markdown, sans modèle.

Chaque fiche donne un nœud `document` à son nom de fichier, un nœud par titre (`#` à `######`), une arête `contains`
du parent vers chaque titre, et une arête `references` vers chaque document lié (`[texte](autre.md)`,
`[[autre]]`, `[réf]: autre.md`). Les identifiants sont ceux de graphify 0.9.20, qui faisait ce travail jusqu'à la
version 0.1.49 : le chemin relatif à la racine sans extension, suivi du titre, normalisés (`commun.normaliser_identifiant`).
Deux identifiants qui se confondent d'une fiche à l'autre sont départagés par le chemin de leur fiche, puis, s'ils
se confondent encore, par un sel tiré de ce chemin. Sortie au format d'extraction : `nodes`, `edges`, `_origin: ast`.

Seul le chemin Markdown de l'extracteur de graphify est repris : les fiches sont des fichiers `.md`.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from newskit.commun import normaliser_identifiant

RE_LIEN = re.compile(r"(?<!\!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)")
RE_REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")
RE_WIKILIEN = re.compile(r"(?<!\!)\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
RE_TITRE = re.compile(r"^(#{1,6})\s+(.+)")
EXTENSIONS_LIABLES = {".md", ".mdx", ".qmd", ".markdown", ".rst", ".txt"}


def make_id(*parties: str) -> str:
    """Un identifiant de nœud à partir de ses parties : jointes par `_`, débarrassées de leurs `_` et `.` de bord,
    puis normalisées."""
    return normaliser_identifiant("_".join(p.strip("_.") for p in parties if p))


def tige(chemin: Path) -> str:
    """Le chemin sans son extension, préfixe des identifiants d'une fiche et de ses titres ; vide sans nom."""
    if not chemin.name:
        return ""
    return chemin.with_suffix("").as_posix()


def cible_du_lien(brut: str, dossier: Path) -> Path | None:
    """Le chemin d'un document lié, ou None : adresse externe, ancre seule, image ou fichier qui n'est pas un
    document. L'ancre et les paramètres sont retirés ; une cible sans extension est un `.md` voisin."""
    cible = brut.strip().split("#", 1)[0].split("?", 1)[0].strip()
    if not cible:
        return None
    bas = cible.lower()
    if "://" in cible or bas.startswith(("mailto:", "tel:", "//", "data:")):
        return None
    suffixe = Path(cible).suffix.lower()
    if suffixe == "":
        cible, suffixe = cible + ".md", ".md"
    if suffixe not in EXTENSIONS_LIABLES:
        return None
    candidat = Path(cible)
    if not candidat.is_absolute():
        candidat = dossier / candidat
    return Path(os.path.normpath(str(candidat)))


def extraire_fiche(chemin: Path) -> dict:
    """Nœuds et arêtes d'une fiche, aux identifiants tirés de son chemin tel qu'il est donné (absolu ici) ; une fiche
    illisible rend une extraction vide avec son erreur."""
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}
    prefixe, source = tige(chemin), str(chemin)
    noeuds: list[dict] = []
    aretes: list[dict] = []
    vus: set[str] = set()

    def noeud(nid: str, label: str, ligne: int) -> None:
        if nid not in vus:
            vus.add(nid)
            noeuds.append(
                {
                    "id": nid,
                    "label": label,
                    "file_type": "document",
                    "source_file": source,
                    "source_location": f"L{ligne}",
                }
            )

    def arete(src: str, tgt: str, relation: str, ligne: int) -> None:
        aretes.append(
            {
                "source": src,
                "target": tgt,
                "relation": relation,
                "confidence": "EXTRACTED",
                "source_file": source,
                "source_location": f"L{ligne}",
                "weight": 1.0,
            }
        )

    fid = make_id(source)
    noeud(fid, chemin.name, 1)
    lies: set[str] = set()

    def lien(brut: str, ligne: int) -> None:
        cible = cible_du_lien(brut, chemin.parent)
        if cible is None:
            return
        tid = make_id(str(cible))
        if tid == fid or tid in lies:
            return
        lies.add(tid)
        arete(fid, tid, "references", ligne)

    pile: list[tuple[int, str]] = []
    dans_le_code = False
    for i, ligne in enumerate(texte.splitlines(), start=1):
        if ligne.strip().startswith("```"):
            dans_le_code = not dans_le_code
            continue
        if dans_le_code:
            continue
        for m in RE_LIEN.finditer(ligne):
            lien(m.group(1), i)
        for m in RE_WIKILIEN.finditer(ligne):
            lien(m.group(1), i)
        ref = RE_REFERENCE.match(ligne)
        if ref:
            lien(ref.group(1), i)
        titre = RE_TITRE.match(ligne)
        if titre:
            niveau, libelle = len(titre.group(1)), titre.group(2).strip()
            hid = make_id(prefixe, libelle)
            if hid in vus:
                hid = make_id(prefixe, libelle, str(i))
            noeud(hid, libelle, i)
            while pile and pile[-1][0] >= niveau:
                pile.pop()
            arete(pile[-1][1] if pile else fid, hid, "contains", i)
            pile.append((niveau, hid))
    return {"nodes": noeuds, "edges": aretes}


def _cle_de_source(source: str, racine: Path) -> str:
    try:
        return str(Path(source).resolve().relative_to(racine))
    except ValueError:
        return source


def departager(noeuds: list[dict], aretes: list[dict], racine: Path) -> None:
    """Un identifiant porté par des nœuds de fiches différentes est suffixé, sur chacun, par le chemin de sa fiche ;
    deux chemins que la normalisation confond prennent en plus un sel de six caractères tiré du chemin. Les arêtes
    d'une fiche suivent l'identifiant de cette fiche."""
    par_id: dict[str, list[dict]] = {}
    for n in noeuds:
        par_id.setdefault(n["id"], []).append(n)
    nouveaux: dict[tuple[str, str], str] = {}
    for ancien, groupe in par_id.items():
        cles = {_cle_de_source(n["source_file"], racine) for n in groupe}
        if len(groupe) < 2 or len(cles) < 2:
            continue
        naifs = {c: make_id(c, ancien) for c in cles}
        comptes: dict[str, int] = {}
        for nid in naifs.values():
            comptes[nid] = comptes.get(nid, 0) + 1
        for n in groupe:
            cle = _cle_de_source(n["source_file"], racine)
            if comptes[naifs[cle]] > 1:
                nid = make_id(cle, ancien, hashlib.sha1(cle.encode("utf-8")).hexdigest()[:6])
            else:
                nid = naifs[cle]
            nouveaux[(ancien, cle)] = nid
            n["id"] = nid
    if not nouveaux:
        return
    for e in aretes:
        cle = _cle_de_source(e["source_file"], racine)
        e["source"] = nouveaux.get((e["source"], cle), e["source"])
        e["target"] = nouveaux.get((e["target"], cle), e["target"])


def extraire(fichiers: list[Path], racine: Path) -> dict:
    """La structure des titres des fiches données (chemins absolus sous `racine`), dans leur ordre : identifiants et
    `source_file` relatifs à la racine, nœuds et arêtes marqués `_origin: ast`."""
    racine = racine.resolve()
    noeuds: list[dict] = []
    aretes: list[dict] = []
    for f in fichiers:
        r = extraire_fiche(f)
        noeuds += r["nodes"]
        aretes += r["edges"]
    # Les identifiants tirés du chemin absolu prennent la forme relative à la racine : celui de la fiche, puis le
    # préfixe de ses titres (seulement sur les nœuds de cette fiche).
    renommes: dict[str, str] = {}
    prefixes: dict[Path, list[tuple[str, str]]] = {}
    for f in fichiers:
        neuf = make_id(tige(f.relative_to(racine)))
        for ancien in (make_id(str(f)), make_id(str(f.resolve()))):
            if ancien != neuf:
                renommes[ancien] = neuf
        anciens = [make_id(tige(f))]
        if make_id(tige(f.resolve())) not in anciens:
            anciens.append(make_id(tige(f.resolve())))
        prefixes[f.resolve()] = [(a, neuf) for a in anciens if a != neuf]
    for n in noeuds:
        n["id"] = renommes.get(n["id"], n["id"])
    for e in aretes:
        e["source"] = renommes.get(e["source"], e["source"])
        e["target"] = renommes.get(e["target"], e["target"])
    titres: dict[str, str] = {}
    for n in noeuds:
        nid = n["id"]
        for ancien, neuf in prefixes[Path(n["source_file"]).resolve()]:
            # graphify s'arrête aussi au premier préfixe déjà relatif (`neuf_`) ; un titre porte toujours le préfixe de la
            # forme reçue (le premier ancien), la garde ne sert donc qu'à ses autres extracteurs et n'est pas reprise.
            if nid.startswith(ancien + "_"):
                titres[nid] = neuf + nid[len(ancien) :]
                break
    for n in noeuds:
        n["id"] = titres.get(n["id"], n["id"])
    for e in aretes:
        e["source"] = titres.get(e["source"], e["source"])
        e["target"] = titres.get(e["target"], e["target"])
    departager(noeuds, aretes, racine)
    for x in noeuds + aretes:
        x["source_file"] = Path(x["source_file"]).relative_to(racine).as_posix()
        x["_origin"] = "ast"
    return {"nodes": noeuds, "edges": aretes, "input_tokens": 0, "output_tokens": 0}

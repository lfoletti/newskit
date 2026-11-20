#!/usr/bin/env python3
"""Les dossiers transversaux du fil (mainteneur, 20.11.2026 : « certains thèmes/sujets pourraient devenir des rubriques,
comme Bugmaggedon, RSI, technologies éducatives, en impliquant le reclassement de certains enregistrements » ; « il y a
des critères de filtre à définir. par exemple pour bugmaggedon, la plupart des failles critiques détectées en 2026 y sont
rattachables, même si elles ne nomment pas le phénomène directement » ; « dossier transversal pour les trois » ; « ce
sont des thèmes transversaux et aussi, forcément, des noeuds à part entière »).

Un billet garde sa rubrique et reçoit zéro, un ou plusieurs dossiers. Le média déclare ses dossiers dans un fichier de
son dossier (`graphes.<g>.dossiers: {fichier: dossiers.yaml}`) : clé, libellés français et anglais, critère écrit, date
de départ facultative. Le moteur :
- donne les critères au tri du fil, qui rend les dossiers dans le même appel que la rubrique (fil.rediger) ;
- reclasse les nouvelles déjà au fil (billets, pile, archive), par lots, jusqu'à ce que toutes aient vu la déclaration
  du moment : un critère changé les fait juger à nouveau (empreinte) ;
- écrit la liste dans l'en-tête de la fiche (`dossiers`), que le squelette lit : un nœud par dossier, une arête
  `dans_le_dossier` de l'article vers lui (extraire_squelette.declarer_fiche). La fiche réécrite repart au cerveau.

Usage :
    python3 -m newskit.dossiers --graphe <graphe> [--compter] [--max N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

from newskit.commun import RACINE  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402

PAR_APPEL = 25  # nouvelles jugées par appel au reclassement
CLE = re.compile(r"[a-z0-9_]+")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

JUGE = """Tu ranges les nouvelles d'une veille sur {nom} dans des dossiers transversaux. Une nouvelle garde sa rubrique ;
un dossier s'y ajoute quand son critère s'applique au fait, même quand la nouvelle ne nomme pas le phénomène. Une
nouvelle peut entrer dans plusieurs dossiers, ou dans aucun.

Dossiers :
{dossiers}

Pour chaque nouvelle, rends {{"n": numéro, "dossiers": [clés des dossiers], "motif": ...}}. motif : en quinze mots au
plus, ce qui la fait entrer ; chaîne vide si la liste est vide. Rends l'objet {{"nouvelles": [...]}}.

Nouvelles :
{nouvelles}"""
SCHEMA_JUGE = {
    "type": "object",
    "required": ["nouvelles"],
    "properties": {
        "nouvelles": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "dossiers"],
                "properties": {
                    "n": {"type": "integer"},
                    "dossiers": {"type": "array", "items": {"type": "string"}},
                    "motif": {"type": "string"},
                },
            },
        }
    },
}
PROPRIETE = {"type": "array", "items": {"type": "string"}}  # le champ `dossiers` des schémas du tri du fil


# --- La déclaration


def declaration(v, racine: Path) -> list[dict]:
    """Les dossiers que déclare `{fichier}`, dans l'ordre du fichier. ValueError qui dit la faute."""
    if not isinstance(v, dict) or set(v) != {"fichier"} or not isinstance(v["fichier"], str) or not v["fichier"]:
        raise ValueError("{fichier} : le fichier des dossiers, dans le dossier du média")
    p = racine / v["fichier"]
    if not p.is_file():
        raise ValueError(f"fichier absent du dossier du média : {v['fichier']}")
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    items = d.get("dossiers") if isinstance(d, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("une liste « dossiers » non vide")
    out, vus = [], set()
    for x in items:
        cle = x.get("cle") if isinstance(x, dict) else None
        if not isinstance(cle, str) or not CLE.fullmatch(cle):
            raise ValueError(f"cle : minuscules, chiffres et _ ({cle!r})")
        if cle in vus:
            raise ValueError(f"cle en double : {cle}")
        vus.add(cle)
        for k in ("fr", "en", "critere"):
            if not isinstance(x.get(k), str) or not x[k].strip():
                raise ValueError(f"{cle} : {k}, un texte non vide")
        depuis = str(x.get("depuis") or "")
        if depuis and not DATE.fullmatch(depuis):
            raise ValueError(f"{cle} : depuis, une date AAAA-MM-JJ")
        out.append(
            {
                "cle": cle,
                "fr": x["fr"].strip(),
                "en": x["en"].strip(),
                "critere": " ".join(x["critere"].split()),
                "depuis": depuis,
            }
        )
    return out


def du_graphe(graphe: str) -> list[dict]:
    """Les dossiers du graphe ; liste vide sans déclaration."""
    v = cfg.graphe_declare(graphe).get("dossiers")
    return declaration(v, RACINE) if v else []


def libelles(graphe: str) -> dict[str, str]:
    """{clé: libellé français}, pour nommer les nœuds ; vide si la déclaration manque ou ne se lit pas."""
    try:
        return {d["cle"]: d["fr"] for d in du_graphe(graphe)}
    except (ValueError, OSError):
        return {}


def empreinte(d: dict) -> str:
    """L'empreinte d'un dossier : ce qui décide du jugement. Un critère ou une date changés font juger à nouveau ce
    dossier seul ; un libellé changé ne fait rien juger (mainteneur, 20.11.2026 : « une empreinte par dossier »)."""
    brut = json.dumps([d["cle"], d["critere"], d["depuis"]], ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:12]


def vus(dossiers: list[dict]) -> dict[str, str]:
    """{clé: empreinte} : la marque d'une nouvelle jugée pour ces dossiers."""
    return {d["cle"]: empreinte(d) for d in dossiers}


def a_revoir(b: dict, dossiers: list[dict]) -> list[dict]:
    """Les dossiers que la nouvelle n'a pas vus dans leur état du moment ; une marque illisible fait tout juger."""
    marque = b.get("dossiers_vus")
    marque = marque if isinstance(marque, dict) else {}
    return [d for d in dossiers if marque.get(d["cle"]) != empreinte(d)]


def _ligne(d: dict) -> str:
    return f"  {d['cle']} : {d['critere']}" + (f" (à partir du {d['depuis']})" if d["depuis"] else "")


def consigne(dossiers: list[dict]) -> str:
    """La ligne `dossiers` de la consigne du tri ; vide sans dossier."""
    if not dossiers:
        return ""
    return (
        "- dossiers : les clés des dossiers dont le critère s'applique au fait, liste vide sinon ; un dossier "
        "s'applique même quand la fiche ne nomme pas le phénomène :\n" + "\n".join(_ligne(d) for d in dossiers) + "\n"
    )


# --- Poser


def date_de(b: dict, chemin: str | None = None) -> str:
    """La date de la nouvelle ; sinon celle du nom de sa fiche ; sinon vide."""
    if DATE.fullmatch(str(b.get("date") or "")[:10]):
        return str(b["date"])[:10]
    m = DATE.search(Path(chemin or b.get("chemin") or "").name)
    return m.group(0) if m else ""


def poser(b: dict, cles, juges: list[dict], chemin: str | None = None, declares: list[dict] | None = None) -> list[str]:
    """Le verdict du modèle pour les dossiers jugés (`juges`) : une clé rendue hors des dossiers jugés ne compte pas, un
    dossier daté n'admet que les nouvelles de sa date de départ ou après (une nouvelle sans date n'y entre pas). Les
    dossiers non jugés gardent leur verdict ; un dossier sorti de la déclaration (`declares`, par défaut les jugés)
    quitte la nouvelle. La liste suit l'ordre de la déclaration ; la nouvelle est marquée jugée pour chaque dossier."""
    declares = juges if declares is None else declares
    par = {d["cle"]: d for d in juges}
    jour = date_de(b, chemin)
    admis = {
        c
        for c in cles or []
        if isinstance(c, str) and c in par and (not par[c]["depuis"] or (jour and jour >= par[c]["depuis"]))
    }
    garde = {c for c in b.get("dossiers") or [] if c not in par}
    b["dossiers"] = [d["cle"] for d in declares if d["cle"] in admis | garde]
    marque = b.get("dossiers_vus") if isinstance(b.get("dossiers_vus"), dict) else {}
    b["dossiers_vus"] = {d["cle"]: marque.get(d["cle"]) for d in declares if d["cle"] in marque} | vus(juges)
    return b["dossiers"]


def ecrire_dans_la_fiche(chemin: str, cles: list[str]) -> bool:
    """Les dossiers dans l'en-tête de la fiche ; une liste vide retire le champ. Rend vrai si la fiche a été réécrite :
    une même liste ne la réécrit pas (une fiche réécrite repart au cerveau)."""
    from newskit import fiche  # noqa: PLC0415

    p = RACINE / chemin
    try:
        champs, corps = fiche.lire_fichier(p)
    except OSError:
        return False
    if list(champs.get("dossiers") or []) == list(cles):
        return False
    if cles:
        champs["dossiers"] = list(cles)
    else:
        champs.pop("dossiers", None)
    try:
        fiche.ecrire(p, champs, corps, strict=False)
    except Exception as e:  # noqa: BLE001 — l'en-tête reste tel quel, le billet garde ses dossiers
        print(f"  dossiers : {type(e).__name__} sur {chemin}", file=sys.stderr)
        return False
    return True


# --- Reclasser l'existant


def a_juger(F: dict, dossiers: list[dict]) -> list[tuple[str, dict, list[dict]]]:
    """(chemin, nouvelle, dossiers à juger) de la pile, des billets puis de l'archive : chaque nouvelle qui n'a pas vu au
    moins un dossier dans son état du moment, avec ces dossiers seuls."""
    tout = [(b.get("chemin", ""), b) for b in (F.get("pile") or []) + (F.get("billets") or [])]
    tout += list((F.get("archive") or {}).items())
    return [(c, b, j) for c, b in tout if c and (j := a_revoir(b, dossiers))]


def reclasser(
    F: dict, graphe: str, dossiers: list[dict], maximum: int | None = None, demander=None
) -> tuple[int, float]:
    """Juge par lots les nouvelles qui n'ont pas vu la déclaration, pose leurs dossiers et les écrit dans leur fiche.
    Rend (nouvelles jugées, coût). Une nouvelle sans réponse reste à juger au passage suivant."""
    if demander is None:
        from newskit import fil  # noqa: PLC0415 — fil importe ce module

        demander = fil.demander
    nom = cfg.graphe_declare(graphe).get("libelle") or graphe
    todo = a_juger(F, dossiers)[:maximum] if maximum is not None else a_juger(F, dossiers)
    groupes: dict[tuple[str, ...], list] = {}  # un lot ne porte que les dossiers à juger, les mêmes pour tout le lot
    for chemin, b, juges in todo:
        groupes.setdefault(tuple(d["cle"] for d in juges), []).append((chemin, b))
    par = {d["cle"]: d for d in dossiers}
    n, cout = 0, 0.0
    for cles_jugees, nouvelles in groupes.items():
        juges = [par[k] for k in cles_jugees]
        for i in range(0, len(nouvelles), PAR_APPEL):
            lot = nouvelles[i : i + PAR_APPEL]
            inv = JUGE.format(
                nom=nom,
                dossiers="\n".join(_ligne(d) for d in juges),
                nouvelles="\n".join(
                    json.dumps(
                        {"n": k, "date": date_de(b, c), "titre": b.get("titre", ""), "texte": b.get("descriptif", "")},
                        ensure_ascii=False,
                    )
                    for k, (c, b) in enumerate(lot)
                ),
            )
            rep, c = demander(inv, SCHEMA_JUGE, graphe, "dossiers")
            cout += c
            faits = set()
            for r in rep.get("nouvelles") or []:
                k = r.get("n", -1)
                if not (isinstance(k, int) and 0 <= k < len(lot)) or k in faits:
                    continue
                faits.add(k)
                chemin, b = lot[k]
                cles = poser(b, r.get("dossiers"), juges, chemin, dossiers)
                b["dossiers_motif"] = str(r.get("motif") or "") if cles else ""
                ecrire_dans_la_fiche(chemin, cles)
                n += 1
    return n, cout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--compter", action="store_true", help="le nombre de nouvelles à juger, sans appel au modèle")
    ap.add_argument("--max", type=int, default=None, help="nouvelles jugées au plus")
    a = ap.parse_args(argv)
    from newskit import fil  # noqa: PLC0415

    dossiers = du_graphe(a.graphe)
    if not dossiers:
        print(f"{a.graphe} : aucun dossier déclaré")
        return 0
    with fil.verrou(a.graphe):
        F = fil.lire(a.graphe)
        if a.compter:
            print(f"{len(a_juger(F, dossiers))} nouvelle(s) à juger, {len(dossiers)} dossier(s)")
            return 0
        n, cout = reclasser(F, a.graphe, dossiers, a.max)
        fil.ecrire(a.graphe, F)
    par = par_dossier(F, dossiers)
    print(f"{n} nouvelle(s) jugée(s), {cout:.2f} $ ; par dossier : " + ", ".join(f"{k} {v}" for k, v in par.items()))
    return 0


def par_dossier(F: dict, dossiers: list[dict]) -> dict[str, int]:
    """Le nombre de nouvelles de chaque dossier (pile, billets, archive)."""
    tout = (F.get("pile") or []) + (F.get("billets") or []) + list((F.get("archive") or {}).values())
    return {d["cle"]: sum(d["cle"] in (b.get("dossiers") or []) for b in tout) for d in dossiers}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

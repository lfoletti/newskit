#!/usr/bin/env python3
"""Collecte des documents PDF déjà présents sur le disque.

Une source sans réseau, et c'est ce qui la distingue : les documents sont là,
lus par l'utilisateur, souvent annotés ailleurs. Ils entrent au corpus comme
n'importe quelle autre source, par le contrat de fiche, avec leur provenance —
le chemin du fichier, son nombre de pages, l'origine de sa date.

Le régime du document commande la méthode, exactement comme le dit la fiche
d'extraction PDF du corpus. Un PDF natif porte son texte : `pypdf` le lit, sans
modèle, de façon rejouable. Un PDF numérisé ne rend rien, et il est **signalé,
pas ingéré** : une fiche vide qui se déclarerait complète est pire qu'une
absence. Aucun OCR n'est tenté ici, faute d'outil installé et parce que ce
serait une inférence, pas une lecture.

Un point d'identité qui a son importance. Quand le document est le tirage local
d'un papier arXiv déjà au corpus, la fiche **ne porte pas** `arxiv_id` : ce
champ sert d'identité de nœud, et deux fiches sous un seul nœud feraient échouer
le contrôle de fraîcheur. L'identifiant va dans `papier_arxiv`, et le lien vers
la notice arXiv est écrit dans le corps : la couche 1 en fait alors une arête
`cite_papier` du document local vers le papier, ce qui est exactement la
relation vraie.

Usage :
    python3 -m newskit.collecte_pdf_local [--source <cle>] [--recommencer]
Sortie : sources/<dossier>/<date>_<nom>.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit.commun import agent_robot  # noqa: E402
from newskit import fiche  # noqa: E402

RE_ARXIV = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")
RE_ARXIV_TEXTE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})", re.I)
# Un titre de PDF vaut ce que valent ses métadonnées : souvent le nom du modèle
# de document, parfois « Microsoft Word - … ». Ces valeurs sont refusées.
TITRES_DOUTEUX = re.compile(r"^(untitled|microsoft word|document\d*|print|slide)", re.I)
# Lignes de tête qui ne sont pas un titre : nom de revue, apparat éditorial,
# mention de licence. « sustainability » avait été pris pour un titre, et
# Crossref l'avait alors apparié à un livre sans rapport.
LIGNES_NON_TITRE = re.compile(
    r"^(sustainability|applied sciences|sensors|energies|buildings|automation in construction"
    r"|article|review|editorial|received|accepted|published|abstract|keywords|introduction"
    r"|www\.|doi|https?:|copyright|licen[sc]e|open access|creative commons|preprint"
    r"|proceedings|conference|journal of|international journal)\b", re.I)
# Caractères par page en dessous desquels le PDF est réputé numérisé. 60 laissait passer un
# scan muni d'une page de garde en texte (JSTOR) : Moor 1979, huit pages, 90 mots, entré comme
# une fiche complète le 02.10.2026. Une page de texte en porte deux à trois mille.
CAR_MINIMUM = 250


def lire_pdf(chemin: Path) -> tuple[str, int, dict]:
    """Rend (texte, pages, métadonnées). Sans modèle, donc rejouable à l'identique."""
    from pypdf import PdfReader  # noqa: PLC0415
    r = PdfReader(str(chemin))
    pages = [(p.extract_text() or "") for p in r.pages]
    meta = {}
    try:
        for cle, valeur in (r.metadata or {}).items():
            meta[str(cle).lstrip("/")] = str(valeur)
    except Exception:
        pass
    return "\n\n".join(pages).strip(), len(pages), meta


RE_REF_ARXIV = re.compile(
    r"(?<!\]\()\b(?:arXiv\s+preprint\s+)?arXiv[:\s]?(\d{4}\.\d{4,5})(v\d+)?\b", re.I)


def lier_references(texte: str) -> tuple[str, int]:
    """Rend cliquables les « arXiv:1706.03762 » des bibliographies.

    Une bibliographie de papier cite ses sources sous cette forme, en texte et
    non en lien : le squeletteur, qui ne lit que des URL, n'en voyait rien. Deux
    cent dix-neuf mentions dormaient ainsi dans ces vingt-quatre documents.
    Les transformer en liens ne change pas le texte du document — l'identifiant
    reste écrit tel quel — et rend à la couche 1 ce qu'elle sait faire : une
    arête `cite_papier` avec son fichier et sa ligne. Ces papiers sont ensuite
    récupérés par `collecte_arxiv.py --citations`, comme toute autre citation.
    """
    n = 0

    def remplace(m):
        nonlocal n
        n += 1
        aid = m.group(1)
        return f"[arXiv:{aid}](https://arxiv.org/abs/{aid})"

    return RE_REF_ARXIV.sub(remplace, texte), n


def nettoyer(texte: str) -> str:
    """Recoud les coupures de ligne du PDF sans réécrire le texte."""
    t = texte.replace("\r", "\n")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)            # mot coupé en fin de ligne
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def titre_de(chemin: Path, meta: dict, texte: str) -> str:
    m = (meta.get("Title") or "").strip()
    if len(m) > 8 and not TITRES_DOUTEUX.match(m):
        return " ".join(m.split())
    for ligne in texte.split("\n")[:14]:
        l = " ".join(ligne.split()).strip("“”\"'")
        if not (20 <= len(l) <= 180) or len(l.split()) < 3:
            continue
        if LIGNES_NON_TITRE.match(l) or l.lower().startswith(("arxiv", "http", "doi")):
            continue
        return l
    return chemin.stem.replace("_", " ").replace(".", " ").strip()


def date_de(chemin: Path, meta: dict, aid: str = "") -> tuple[str, str]:
    """(date, origine de la date). Jamais inventée, et sa source est toujours dite.

    L'ordre suit la fiabilité, et c'est ce qui compte pour un graphe à deux
    horloges. Un identifiant arXiv encode l'année et le mois de dépôt : c'est la
    meilleure source, et elle est mécanique. Vient ensuite l'année en tête du
    nom de fichier, convention de ces dossiers de lecture. Puis la date de
    création du PDF, souvent celle d'un retirage. La date de modification du
    fichier en dernier : elle ne dit rien du document, seulement du disque —
    elle datait le papier ImageNet de 2012 en 2024.
    """
    if aid and re.match(r"^\d{4}\.", aid):
        aa, mm = aid[:2], aid[2:4]
        if 1 <= int(mm) <= 12:
            siecle = "20" if int(aa) < 90 else "19"
            return f"{siecle}{aa}-{mm}-01", "identifiant arXiv (année et mois de dépôt)"
    m = re.match(r"^(19|20)(\d{2})[._-]", chemin.name)
    if m:
        return f"{m.group(1)}{m.group(2)}-01-01", "année en tête du nom de fichier"
    brut = (meta.get("CreationDate") or "").strip()
    m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", brut)
    if m:
        a, mo, j = m.groups()
        if 1980 <= int(a) <= datetime.now().year and 1 <= int(mo) <= 12 and 1 <= int(j) <= 31:
            return f"{a}-{mo}-{j}", "métadonnées du PDF"
    m = re.search(r"\b(19\d{2}|20[0-2]\d)\b", chemin.name)
    if m:
        return f"{m.group(1)}-01-01", "année trouvée dans le nom de fichier"
    mt = datetime.fromtimestamp(chemin.stat().st_mtime, tz=timezone.utc)
    return mt.strftime("%Y-%m-%d"), "date de modification du fichier, faute de mieux"


def crossref(titre: str, annee_attendue: int | None = None, delai: float = 1.0) -> dict:
    """Auteurs, date et DOI d'un article de revue, si Crossref confirme le titre.

    Les métadonnées d'un PDF ne portent presque jamais les auteurs : dix-huit
    des vingt-quatre documents de la première passe n'en avaient pas, et un
    auteur absent est une arête `ecrit_par` absente. Les deviner depuis la
    première page serait une inférence ; les demander à Crossref est une
    lecture, et le titre rendu sert de garde-fou. Trois conditions, apprises de
    deux appariements faux à la première passe : le titre rendu doit être
    **exactement** le titre cherché après normalisation, ce titre doit faire au
    moins vingt-cinq caractères, et si le nom de fichier porte une année, celle
    de Crossref doit s'en approcher à un an près. Sans ces trois conditions,
    « Computer Implementation of Shape Grammars » de 1999 se voyait attribuer
    les auteurs d'un homonyme de 2006 : des titres identiques existent d'un
    lieu de publication à l'autre.
    """
    import json as _json  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query.bibliographic": titre[:200], "rows": 3})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": agent_robot()})
        with urllib.request.urlopen(req, timeout=40) as r:
            items = _json.loads(r.read())["message"]["items"]
    except Exception:
        return {}
    finally:
        time.sleep(delai)
    cible = norm(titre)
    if len(cible) < 25:
        return {}
    for it in items:
        rendu = norm((it.get("title") or [""])[0])
        if rendu != cible:
            continue
        parts_ctrl = ((it.get("issued") or {}).get("date-parts") or [[]])[0]
        if annee_attendue and parts_ctrl and parts_ctrl[0] and abs(parts_ctrl[0] - annee_attendue) > 1:
            continue
        auteurs = [" ".join(x for x in (a.get("given"), a.get("family")) if x)
                   for a in it.get("author", [])]
        parts = ((it.get("issued") or {}).get("date-parts") or [[]])[0]
        date = ""
        if parts and parts[0]:
            date = f"{parts[0]:04d}-{(parts[1] if len(parts) > 1 else 1):02d}-" \
                   f"{(parts[2] if len(parts) > 2 else 1):02d}"
        return {"author": ", ".join(a for a in auteurs if a)[:300], "date": date,
                "doi": it.get("DOI", ""), "publication_source": (it.get("container-title") or [""])[0]}
    return {}


def identifiant_arxiv(chemin: Path, texte: str) -> str:
    m = RE_ARXIV.search(chemin.name)
    if m:
        return m.group(1)
    m = RE_ARXIV_TEXTE.search(texte[:4000])
    return m.group(1) if m else ""


TEXTES_VUS: dict[str, Path] = {}      # empreinte du texte extrait → premier fichier qui l'a porté, sur un passage


def traiter(src, chemin: Path, recommencer: bool, manifeste: dict,
            empreinte: str) -> tuple[str, str]:
    """Rend (état, détail). L'état vaut « écrite », « inchangée » ou « numérisée ».

    Le saut d'un document déjà traité se décide sur le **manifeste**, et non sur
    l'existence d'un fichier au nom calculé : la date d'une fiche peut être
    corrigée par Crossref après coup, donc le nom final n'est pas connu avant
    d'avoir tout fait. Deux fiches étaient ainsi réécrites à chaque passage,
    dont un ouvrage de 739 pages.
    """
    connu = manifeste.get(str(chemin))
    if connu and not recommencer and connu.get("empreinte") == empreinte \
            and (src.chemin / connu.get("fiche", "")).exists():
        return "inchangée", connu["fiche"]

    texte, pages, meta = lire_pdf(chemin)
    texte = nettoyer(texte)
    texte, refs = lier_references(texte)
    if pages and len(texte) / pages < CAR_MINIMUM:
        return "numérisée", (f"{pages} pages, {len(texte) // max(1, pages)} car./page : "
                             f"PDF numérisé, océrisation requise — non ingéré")
    # Le même texte sous un autre fichier (« … - copie.pdf », réenregistré, donc d'une autre
    # empreinte d'octets) : l'empreinte du texte extrait est tout aussi exacte.
    h_texte = hashlib.sha256(texte.encode("utf-8")).hexdigest()[:16]
    if h_texte in TEXTES_VUS and TEXTES_VUS[h_texte] != chemin:
        return "doublon", f"{chemin} — même texte que {TEXTES_VUS[h_texte].name}"
    TEXTES_VUS[h_texte] = chemin

    titre = titre_de(chemin, meta, texte)
    aid = identifiant_arxiv(chemin, texte)
    date, origine_date = date_de(chemin, meta, aid)
    nom = re.sub(r"[^a-z0-9]+", "-", chemin.stem.lower()).strip("-")[:80]
    sortie = src.chemin / f"{date}_{nom}.md"

    entete = [f"# {titre}", ""]
    if aid:
        # Ce lien est ce qui rattache le tirage local au papier du corpus.
        entete += [f"Tirage local du papier arXiv [{aid}](https://arxiv.org/abs/{aid}).", ""]
    entete += [f"*Document local : `{chemin}` — {pages} pages.*", ""]

    champs = {
        "title": titre,
        "subtitle": (meta.get("Subject") or "").strip()[:200],
        "author": " ".join((meta.get("Author") or "").split())[:200],
        "date": date,
        "url": "",
        "plateforme": "Bibliothèque locale",
        "publication": src.nom,
        "complet": True,
        "collected": time.strftime("%Y-%m-%d"),
        "source": f"PDF local, {chemin.parent.name}",
        "pages": pages,
        "chemin_local": str(chemin),
        "origine_date": origine_date,
    }
    if aid:
        champs["papier_arxiv"] = aid
    # Auteur manquant : Crossref peut le rendre, sur confirmation du titre. Les
    # papiers arXiv sont exclus de cette recherche, leur notice étant déjà au
    # corpus avec ses auteurs et l'arête `cite_papier` les reliant.
    if not champs["author"] and not aid:
        m_an = re.search(r"\b(19|20)(\d{2})\b", chemin.name)
        cr = crossref(titre, int(m_an.group(0)) if m_an else None)
        if cr.get("author"):
            champs["author"] = cr["author"]
            champs["origine_auteur"] = "Crossref, titre confirmé à l'identique"
            champs["origine_date"] = origine_date = (
                "Crossref" if cr.get("date") else origine_date)
            if cr.get("date"):
                champs["date"] = cr["date"]
                sortie = src.chemin / f"{cr['date']}_{nom}.md"
            if cr.get("doi"):
                champs["doi"] = cr["doi"]
            if cr.get("publication_source"):
                champs["source"] = f"{cr['publication_source']} (tirage local)"
    try:
        fiche.ecrire(sortie, champs, "\n".join(entete) + texte)
    except fiche.IdentiteDejaVue as deja:              # le même papier (DOI), déjà dans le graphe par une autre voie
        return "doublon", f"{sortie.name} : déjà vue ({deja.cle}, {deja.existante.name})"
    manifeste[str(chemin)] = {"fiche": sortie.name, "empreinte": empreinte, "pages": pages,
                              "titre": titre, "date": champs["date"],
                              "origine_date": champs["origine_date"], "texte": h_texte}
    return "écrite", (f"{sortie.name}  ({pages} p., {len(texte.split())} mots"
                      + (f", {refs} réf. arXiv liées" if refs else "") + ")")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="ne traiter qu'une source du registre")
    ap.add_argument("--recommencer", action="store_true", help="réécrire les fiches existantes")
    ap.add_argument("--graphe", help="ne traiter que les sources d'un graphe")
    args = ap.parse_args()

    sources = [s for s in cfg.toutes("Bibliothèque locale", graphe=args.graphe)
               if not args.source or s.cle == args.source]
    if not sources:
        print("  aucune source « Bibliothèque locale » au registre.", file=sys.stderr)
        return 1

    total = {"écrite": 0, "inchangée": 0, "numérisée": 0}
    numerisees: list[str] = []
    doublons: list[str] = []
    illisibles: list[str] = []
    for src in sources:
        src.chemin.mkdir(parents=True, exist_ok=True)
        manifeste = (json.loads(src.manifeste.read_text(encoding="utf-8"))
                     if src.manifeste.exists() else {})
        # Un manifeste de la version précédente portait un chemin par nom de
        # fichier : il est simplement remplacé, rien n'en dépend.
        if manifeste and not isinstance(next(iter(manifeste.values())), dict):
            manifeste = {}
        # Les textes déjà en fiche, d'un passage à l'autre : sans cela un doublon de texte écarté
        # hier revient aujourd'hui, son jumeau étant « inchangé » et donc pas relu (02.10.2026).
        for chemin_connu, entree in manifeste.items():
            if entree.get("texte"):
                TEXTES_VUS.setdefault(entree["texte"], Path(chemin_connu))
        print(f"{src.nom}")
        pdfs: list[Path] = []
        for brut in src.chemins:
            p = Path(brut).expanduser()
            if p.is_dir():
                pdfs += sorted(p.rglob("*.pdf"))
            elif p.is_file():
                pdfs.append(p)
            else:
                print(f"  !! chemin déclaré absent : {p}", file=sys.stderr)
        # Dédoublonnage par empreinte du fichier. Deux dossiers de lecture
        # contenaient le même papier à l'octet près : l'ingérer deux fois
        # doublerait son texte au corpus et ses arêtes au graphe. L'empreinte
        # est exacte, donc aucune fusion approximative n'est en jeu.
        vus: dict[str, Path] = {}
        empreintes: dict[Path, str] = {}
        for pdf in list(pdfs):
            h = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
            empreintes[pdf] = h
            if h in vus:
                doublons.append(f"{pdf} — identique à {vus[h]}")
                print(f"  = {pdf.name} : identique à {vus[h].name}, ignoré")
                pdfs.remove(pdf)
            else:
                vus[h] = pdf

        for pdf in pdfs:
            try:
                etat, detail = traiter(src, pdf, args.recommencer, manifeste, empreintes[pdf])
            except Exception as e:
                illisibles.append(f"{pdf} : {type(e).__name__} — {str(e)[:70]}")
                print(f"  !! {pdf.name} : {type(e).__name__} — {str(e)[:90]}", file=sys.stderr)
                continue
            if etat == "doublon":
                doublons.append(detail)
                print(f"  = {pdf.name} : même texte qu'un autre fichier, ignoré")
                continue
            total[etat] += 1
            if etat == "numérisée":
                numerisees.append(f"{pdf} — {detail}")
                print(f"  ⚠ {pdf.name} : {detail}")
            elif etat == "écrite":
                print(f"  + {detail}")
        # Le manifeste ne garde que les documents encore déclarés : un PDF
        # retiré des dossiers ne doit pas y survivre comme un état fantôme.
        vivants = {str(p) for p in pdfs}
        manifeste = {k: v for k, v in manifeste.items() if k in vivants}
        src.manifeste.write_text(json.dumps(manifeste, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"\n  {total['écrite']} fiche(s) écrite(s), {total['inchangée']} inchangée(s), "
          f"{total['numérisée']} document(s) numérisé(s) non ingéré(s), "
          f"{len(doublons)} doublon(s) ignoré(s)")
    for d in doublons:
        print(f"    doublon : {d}")
    if numerisees:
        print("  ALERTE : documents numérisés, à océriser avant ingestion :")
        for n in numerisees:
            print(f"    {n}")
    if illisibles:
        print("  ALERTE : documents que pypdf n'a pas pu ouvrir ; ce ne sont pas des "
              "documents absents :", file=sys.stderr)
        for i in illisibles:
            print(f"    {i}", file=sys.stderr)
    return 1 if (numerisees or illisibles) else 0


if __name__ == "__main__":
    sys.exit(main())

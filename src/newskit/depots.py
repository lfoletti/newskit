#!/usr/bin/env python3
"""Ingère les liens déposés depuis le navigateur, et garde la mémoire de leurs sources.

Le cas d'usage : « cette page est remarquable, elle doit faire partie du graphe ».
Un clic droit dans Chrome (extension deploiement/extension_chrome) envoie le lien
au serveur, qui le range dans la table `depots` de la base des jetons. Ce script,
qui tourne **sur la machine qui collecte**, vient les lire par SSH, et pour chacun :

    1. lit la page (collecte_rss.corps_de_page) et écrit une fiche dans
       sources/depots/<graphe>/, dossier dense : la couche 2 l'extraira au passage
       suivant de la routine du graphe ;
    2. étudie la source — le domaine — avec prospecter_source : a-t-elle un flux,
       combien de mots, depuis quand ? L'étude est gardée dans
       sources/depots/_sources.json avec le compte des dépôts par domaine, et
       la prospection refaite au plus tous les trente jours ;
    3. note dans la base ce qu'il en a fait (traite_le, resultat).

Un domaine déposé deux fois est une source que le porteur lit : le rapport le
dit et donne le bloc YAML pour la déclarer. Un lien déjà déposé n'est pas réécrit.
L'accès au VPS est celui d'alerte_jetons.py (~/.secrets/vps_kg.env).

Usage :
    python3 -m newskit.depots              # ingère ce qui attend, écrit, envoie un courriel s'il y a eu du neuf
    python3 -m newskit.depots --montrer    # ce qui attend et la mémoire des sources, sans rien faire
    python3 -m newskit.depots --etat       # la mémoire des sources
    python3 -m newskit.depots --suivre     # ingère, puis extrait et recompose aussitôt les graphes touchés
                                           # (daemon des dépôts, toutes les dix minutes)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import fiche  # noqa: E402

ACCES = Path.home() / ".secrets" / "vps_kg.env"
DOSSIER = RACINE / "sources" / "depots"
MEMOIRE = DOSSIER / "_sources.json"
JOURS_PROSPECTION = 30
MOTS_MINIMUM = 120

REQUETE = (
    "SELECT id, graphe, REPLACE(url, '\\t', ' '), REPLACE(COALESCE(titre, ''), '\\t', ' '), "
    "REPLACE(COALESCE(note, ''), '\\t', ' '), cree_le FROM depots WHERE traite_le IS NULL ORDER BY id"
)


def pousser() -> str:
    """Pousse les commits du passage ; un refus (hook pre-push, réseau) se dit au journal et au courriel (06.11.2026)."""
    try:
        r = subprocess.run(["git", "push", "-q"], cwd=RACINE, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess([], 1, "", "délai dépassé")
    if r.returncode == 0:
        return ""
    alerte = f"ALERTE : push refusé : {(r.stderr or r.stdout or '').strip()[:300]}"
    print(alerte, file=sys.stderr)
    return alerte


def lire_env(chemin: Path) -> dict:
    conf = {}
    for l in chemin.read_text(encoding="utf-8").splitlines():
        if "=" in l and not l.lstrip().startswith("#"):
            k, v = l.split("=", 1)
            conf[k.strip()] = v.strip().strip("\"'")
    return conf


def sql(requete: str) -> str:
    acces = lire_env(ACCES)
    r = subprocess.run(
        [
            "ssh",
            "-i",
            str(Path(acces["VPS_CLE"]).expanduser()),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}",
            "sudo mariadb -N -B kg",
        ],
        input=requete,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"base des dépôts illisible par SSH : {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def en_attente() -> list[dict]:
    out = []
    for l in sql(REQUETE).splitlines():
        c = l.split("\t")
        if len(c) == 6:
            out.append(
                {
                    "id": int(c[0]),
                    "graphe": c[1],
                    "url": c[2].replace("\\n", " "),
                    "titre": c[3],
                    "note": c[4],
                    "cree_le": c[5],
                }
            )
    return out


def noter(ident: int, resultat: str) -> None:
    propre = re.sub(r"[^\w\s.,;:()'’«»/-]", " ", resultat)[:480].replace("'", "''")
    sql(f"UPDATE depots SET traite_le = NOW(), resultat = '{propre}' WHERE id = {int(ident)}")


def domaine_de(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def slug(url: str, titre: str) -> str:
    base = titre or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return s[:80] or "page"


def lire_memoire() -> dict:
    return json.loads(MEMOIRE.read_text(encoding="utf-8")) if MEMOIRE.exists() else {"domaines": {}}


def ecrire_memoire(m: dict) -> None:
    MEMOIRE.parent.mkdir(parents=True, exist_ok=True)
    MEMOIRE.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def cle_canonique(url: str) -> str:
    """La clé d'une page : « domaine#identifiant » quand le registre déclare l'identifiant du domaine
    (config_sources.identifiants : /en/algoritme/<id> et /nl/algoritme/<gm>/<id>/<slug> de l'Algoritmeregister), sinon
    l'adresse sans schéma, sans www ni barre finale."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    u = urlsplit(str(url).strip())
    hote = u.netloc.lower().removeprefix("www.")
    for dom, motif in cfg.identifiants().items():
        if hote == dom or hote.endswith("." + dom):
            m = re.search(motif, u.path)
            if m:
                return f"{dom}#{m.group(1)}"
    return hote + u.path.rstrip("/") + (f"?{u.query}" if u.query else "")


def deja_deposee(url: str) -> Path | None:
    cle = cle_canonique(url)
    for f in DOSSIER.rglob("*.md"):
        if f.name.startswith("_"):
            continue
        champs, _ = fiche.lire_fichier(f)
        if champs.get("url") and cle_canonique(str(champs["url"])) == cle:
            return f
    return None


def etudier_source(domaine: str, m: dict, aujourdhui: str) -> dict:
    """La prospection du domaine, refaite si elle a plus de JOURS_PROSPECTION jours."""
    from newskit import prospecter_source as ps  # noqa: PLC0415

    d = m["domaines"].setdefault(domaine, {"depots": 0, "graphes": [], "etude": None, "etudie_le": None})
    vieux = True
    if d.get("etudie_le"):
        try:
            vieux = (
                datetime.strptime(aujourdhui, "%Y-%m-%d") - datetime.strptime(d["etudie_le"], "%Y-%m-%d")
            ).days > JOURS_PROSPECTION
        except ValueError:
            vieux = True
    if vieux:
        try:
            r = ps.essai_substack(domaine) or ps.essai_flux(domaine)
            v, pourquoi = ps.verdict(r)
            d["etude"] = {
                "verdict": v,
                "note": pourquoi,
                "voie": (r or {}).get("voie", "—"),
                "dernier": (r or {}).get("dernier", "—"),
                "mots": (r or {}).get("mots", 0),
                "flux": (r or {}).get("adresse") or "",
                "yaml": ps.bloc_yaml(domaine, r) if v == "OUI" else "",
            }
        except Exception as e:  # noqa: BLE001 — l'étude est un confort, la fiche compte d'abord
            d["etude"] = {"verdict": "?", "note": f"prospection impossible : {str(e)[:80]}"}
        d["etudie_le"] = aujourdhui
    return d


def deja_declaree(domaine: str) -> str | None:
    """Le nom de la source du registre qui lit déjà ce domaine, s'il y en a une."""
    for s in cfg.toutes():
        for champ in ("domaine", "site", "flux"):
            v = str(s.propres.get(champ) or "")
            if v and domaine in v.lower():
                return s.nom
    return None


def graphes_candidats() -> dict[str, str]:
    """{graphe: ce qu'il couvre} pour les graphes qui ont un dossier de dépôt, sauf ceux que le registre écarte
    (`depots: false`, un graphe local)."""
    from newskit import media  # noqa: PLC0415

    registre = media.registre_des_graphes()
    skill = json.loads(registre.read_text(encoding="utf-8")) if registre.exists() else {}
    out = {}
    for s in cfg.toutes("Dépôt"):
        if cfg.graphe_declare(s.graphe).get("depots") is False:
            continue
        g = skill.get(s.graphe) or {}
        out[s.graphe] = " ; ".join(
            [g.get("resume") or cfg.graphe_declare(s.graphe)["libelle"], *(g.get("sujets") or [])]
        )
    return out


def graphe_par_le_vocabulaire(texte: str, candidats: dict[str, str]) -> tuple[str, int]:
    """Le graphe dont les sujets partagent le plus de racines avec la page, et ce nombre. Sans modèle."""
    from newskit import themes_emergents as te  # noqa: PLC0415

    page = te.mots(texte, 3)
    notes = {g: len(page & te.mots(couvre, 3)) for g, couvre in candidats.items()}
    meilleur = max(notes, key=lambda g: (notes[g], g == cfg.GRAPHE_PRINCIPAL)) if notes else cfg.GRAPHE_PRINCIPAL
    return meilleur, notes.get(meilleur, 0)


GRAPHES_TOUCHES: dict[str, int] = {}  # graphe → fiches écrites à ce passage, pour le passage court qui suit
EN_ATTENTE = "_en_attente"  # dossier des pages qu'aucun graphe ne couvre : gardées, lues par aucun


def choisir_graphe(titre: str, texte: str, propositions: dict | None = None) -> tuple[str | None, str, dict | None]:
    """(graphe, motif, proposition) : le graphe le plus pertinent pour une page déposée en « auto ».

    Une question au modèle, sur le titre et le début de la page, contre ce que chaque graphe déclare
    couvrir. Si aucun ne convient, le graphe est None et le modèle propose un graphe nouveau — son nom
    et ce qu'il couvrirait —, en reprenant une proposition déjà faite quand elle va (`propositions`).
    Créer un graphe reste une décision du porteur : la page attend, la proposition paraît au rapport.
    Sans modèle, le vocabulaire tranche ; s'il ne trouve rien de commun, la page attend sans proposition."""
    from newskit import themes_emergents as te  # noqa: PLC0415

    te.POSTE = "depots"
    candidats = graphes_candidats()
    debut = " ".join(texte.split()[:400])
    schema = {
        "type": "object",
        "required": ["graphe", "motif"],
        "properties": {
            "graphe": {"type": ["string", "null"]},
            "motif": {"type": "string"},
            "nouveau": {
                "type": ["object", "null"],
                "properties": {"nom": {"type": "string"}, "couvre": {"type": "string"}},
            },
        },
    }
    deja = "".join(f"\n- {n} : {p.get('couvre', '')}" for n, p in (propositions or {}).items())
    rep = (
        te.appeler_claude(
            "Une page vient d'être déposée dans une veille. Dis dans quel graphe elle a le plus sa place.\n\n"
            + "\n".join(f"- {g} : {couvre[:700]}" for g, couvre in candidats.items())
            + f"\n\nTitre : {titre}\nDébut : {debut}\n\n"
            "Si elle relève clairement d'un de ces graphes, donne son nom. Si aucun ne la couvre — ne force pas —, réponds "
            "`graphe: null` et propose dans `nouveau` un graphe qui la couvrirait : `nom` court sur le modèle des autres "
            "(« ai-… », minuscules et tirets), `couvre` en une ligne."
            + (f" Graphes déjà proposés, à reprendre s'ils vont :{deja}" if deja else "")
            + '\nRéponds en JSON : {"graphe": "<nom de la liste, ou null>", "motif": "<une ligne>", "nouveau": {"nom": "…", "couvre": "…"} ou null}.',
            schema,
        )
        if candidats
        else None
    )
    if isinstance(rep, dict):
        if rep.get("graphe") in candidats:
            return rep["graphe"], str(rep.get("motif") or "")[:160], None
        nouveau = rep.get("nouveau") if isinstance(rep.get("nouveau"), dict) else None
        if rep.get("graphe") is None and nouveau and cfg.NOM_DE_GRAPHE.match(str(nouveau.get("nom") or "")):
            return (
                None,
                str(rep.get("motif") or "")[:160],
                {"nom": nouveau["nom"], "couvre": str(nouveau.get("couvre") or "")[:200]},
            )
    g, n = graphe_par_le_vocabulaire(f"{titre} {debut}", candidats)
    if n == 0:
        return None, "aucun graphe ne partage de vocabulaire avec cette page", None
    return g, f"par le vocabulaire ({n} racine(s) en commun avec ses sujets)", None


def date_iso(brute: str) -> str:
    """La date d'une page en AAAA-MM-JJ, ou "" : ISO, « 10/24/2025 » (mois d'abord, sauf jour > 12) ou RFC 822."""
    brute = str(brute or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", brute)
    if m:
        return m.group(0)
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", brute[:10])
    if m:
        a, b, an = int(m.group(1)), int(m.group(2)), m.group(3)
        mois, jour = (b, a) if a > 12 else (a, b)
        return f"{an}-{mois:02d}-{jour:02d}" if 1 <= mois <= 12 and 1 <= jour <= 31 else ""
    try:
        return parsedate_to_datetime(brute).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return ""


def ingerer(depot: dict, m: dict, aujourdhui: str) -> tuple[str, str]:
    """(résultat court pour la base, ligne pour le rapport)."""
    from newskit import collecte_rss as cr  # noqa: PLC0415

    auto = depot["graphe"] == "auto"
    graphe = cfg.GRAPHE_PRINCIPAL if auto else cfg.nom_actuel(depot["graphe"])  # « auto » : choisi une fois la page lue
    if graphe not in cfg.graphes():
        return "graphe inconnu", f"  !! dépôt {depot['id']} : graphe inconnu « {depot['graphe']} » — {depot['url']}"
    url = depot["url"]
    domaine = domaine_de(url)
    d = etudier_source(domaine, m, aujourdhui)
    d["depots"] += 1
    d["dernier"] = aujourdhui
    existante = deja_deposee(url)
    if existante:
        return f"doublon : {existante.relative_to(RACINE)}", f"  =   déjà déposée : {url[:90]}"
    n_mots, corps, mur, meta = cr.corps_de_page(url)
    titre = (depot["titre"] or meta.get("titre") or urlsplit(url).path.rsplit("/", 1)[-1] or domaine).strip()
    motif, proposition = "", None
    if auto:
        choisi, motif, proposition = choisir_graphe(titre, corps, m.setdefault("propositions", {}))
        graphe = choisi or EN_ATTENTE
        if choisi is None:
            # Aucun graphe ne couvre la page : elle attend, et la proposition de graphe s'accumule.
            p = m["propositions"].setdefault(
                (proposition or {}).get("nom") or "(sans nom)",
                {"couvre": (proposition or {}).get("couvre", ""), "pages": []},
            )
            p["pages"].append(url)
    if graphe not in d["graphes"]:
        d["graphes"].append(graphe)
    date = date_iso(meta.get("date") or "") or aujourdhui
    dossier = DOSSIER / graphe
    dossier.mkdir(parents=True, exist_ok=True)
    nom = f"{date}_{slug(url, titre)}.md"
    if (dossier / nom).exists():  # un homonyme d'une autre adresse : le dernier segment de l'adresse
        nom = f"{date}_{slug(url, titre)[:60]}-{slug(url, '')[:19]}.md"
    lisible = n_mots >= MOTS_MINIMUM
    if not lisible:
        corps = (
            f"*Texte non lisible par le collecteur ({n_mots} mots"
            + (", mur d'abonnés" if mur else "")
            + ") : lien gardé, à lire à la source.*"
        )
    champs = {
        "title": titre,
        "subtitle": "",
        "author": meta.get("author") or meta.get("auteur") or "",
        "date": date,
        "url": url,
        "plateforme": "Dépôt",
        "publication": domaine,
        "complet": lisible,
        "collected": aujourdhui,
        "source": "déposé par le porteur depuis le navigateur"
        + (f" ; graphe choisi à l'ingestion : {motif}"[:200] if auto else ""),
        "note": depot["note"] or "",
        "depot_id": depot["id"],
    }
    entete = f"# {titre}\n\n" + (f"> Note du porteur : {depot['note']}\n\n" if depot["note"] else "")
    try:
        # Une page qu'aucun graphe ne couvre attend dans _en_attente, que personne ne lit (hors_graphe).
        fiche.ecrire(dossier / nom, champs, entete + corps, hors_graphe=graphe == EN_ATTENTE)
    except fiche.IdentiteDejaVue as deja:  # le document est déjà une fiche du graphe
        rel = deja.existante.relative_to(Path(RACINE).resolve())
        return f"déjà dans le graphe : {rel}"[
            :480
        ], f"  =   [{graphe}] {titre[:70]} — déjà vue ({deja.cle}) — {domaine}"
    res = (
        f"fiche écrite : sources/depots/{graphe}/{nom} ({n_mots} mots)"
        if lisible
        else f"lien gardé, texte illisible ({n_mots} mots)"
    )
    if lisible and graphe != EN_ATTENTE:
        GRAPHES_TOUCHES[graphe] = GRAPHES_TOUCHES.get(graphe, 0) + 1
    if auto and graphe == EN_ATTENTE:
        nom = (proposition or {}).get("nom") or "à nommer"
        return (
            f"en attente : aucun graphe ne la couvre ; graphe suggéré : {nom}"[:480],
            f"  ??  [aucun graphe] {titre[:60]} — {domaine} — graphe suggéré : {nom} ({(proposition or {}).get('couvre', motif)[:90]})",
        )
    choix = f" (graphe choisi : {motif[:80]})" if auto else ""
    return res + choix[
        :120
    ], f"  ok  [{graphe}{' ← auto' if auto else ''}] {titre[:70]} — {n_mots} mots — {domaine}{choix}"


def suivre() -> list[str]:
    """Le passage court qui suit un dépôt : le lien annoncé est dans le graphe sans attendre la routine du matin.

    Pour chaque graphe qui vient de recevoir une fiche : extraction des seules dernières arrivées, recomposition,
    carte, commit et envoi (`routine_hebdo.py --sans-collecte --plafond n`). Puis le serveur en ligne est
    prié de se mettre à jour tout de suite, au lieu d'attendre son heure. Une routine en cours fait attendre."""
    lignes = []
    for graphe, n in GRAPHES_TOUCHES.items():
        args = ["--sans-collecte", "--sans-courriel", "--plafond", str(n)]
        if graphe != cfg.GRAPHE_PRINCIPAL:
            args += ["--graphe", graphe]
        r = subprocess.run(
            [sys.executable, str(CODE / "routine_hebdo.py"), *args],
            cwd=RACINE,
            capture_output=True,
            text=True,
            timeout=14 * 3600,
        )
        fait = r.returncode == 0
        lignes.append(
            f"  {graphe} : {n} fiche(s) "
            + (
                "extraite(s), graphe recomposé et envoyé"
                if fait
                else f"non intégrée(s) — passage court en échec (code {r.returncode}), "
                "elles passeront à la routine du matin"
            )
        )
    try:
        acces = lire_env(ACCES)
        subprocess.run(
            [
                "ssh",
                "-i",
                str(Path(acces["VPS_CLE"]).expanduser()),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
                f"{acces['VPS_UTILISATEUR']}@{acces['VPS_HOTE']}",
                "sudo systemctl start kg-mcp-maj.service",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        lignes.append("  serveur en ligne : mis à jour")
    except Exception as e:  # noqa: BLE001 — il se mettra à jour seul, à l'heure
        lignes.append(f"  serveur en ligne : mise à jour non déclenchée ({str(e)[:80]}), il la fera à l'heure")
    for l in lignes:
        print(l)
    return lignes


def rapport_sources(m: dict, touches: set[str]) -> list[str]:
    L = []
    for domaine in sorted(touches):
        d = m["domaines"][domaine]
        e = d.get("etude") or {}
        decl = deja_declaree(domaine)
        ligne = f"  {domaine} : {d['depots']} dépôt(s) ({', '.join(d['graphes'])}) — "
        if decl:
            ligne += f"déjà lue par la source « {decl} »"
        elif e.get("verdict") == "OUI":
            ligne += f"collectable ({e.get('voie')}, {e.get('mots')} mots, dernier {e.get('dernier')})"
            if d["depots"] >= 2:
                ligne += " — déposée deux fois : à déclarer ?"
        else:
            ligne += f"pas collectable en continu ({e.get('note', '?')})"
        L.append(ligne)
        if not decl and e.get("verdict") == "OUI" and d["depots"] >= 2 and e.get("yaml"):
            L += ["    " + l for l in e["yaml"].splitlines()]
    # Les graphes que l'assignation automatique a suggérés : des pages qu'aucun graphe ne couvre attendent
    # dans sources/depots/_en_attente. Créer le graphe, ou les ranger ailleurs, est une décision du porteur.
    for nom, p in sorted((m.get("propositions") or {}).items(), key=lambda kv: -len(kv[1]["pages"])):
        L.append(f"  graphe suggéré : {nom} — {p.get('couvre', '')} — {len(p['pages'])} page(s) en attente")
    return L


def main() -> int:
    montrer, etat = "--montrer" in sys.argv, "--etat" in sys.argv
    m = lire_memoire()
    if etat:
        if not m["domaines"]:
            print("  aucun dépôt encore")
        for l in rapport_sources(m, set(m["domaines"])):
            print(l)
        return 0
    if not ACCES.exists():
        print(f"  accès au VPS non configuré : {ACCES} absent", file=sys.stderr)
        return 1
    try:
        attente = en_attente()
    except (RuntimeError, subprocess.TimeoutExpired, KeyError) as e:
        print(f"  !! {e}", file=sys.stderr)
        return 1
    print(f"  {len(attente)} dépôt(s) en attente")
    if montrer or not attente:
        for d in attente:
            print(f"    {d['id']:>4}  [{d['graphe']}] {d['titre'][:60] or d['url'][:60]}")
        return 0
    aujourdhui = time.strftime("%Y-%m-%d")
    lignes, touches, ecrites = [], set(), 0
    for depot in attente:
        try:
            res, ligne = ingerer(depot, m, aujourdhui)
        except Exception as e:  # noqa: BLE001 — un dépôt qui casse n'empêche pas les autres
            res, ligne = f"échec : {str(e)[:100]}", f"  !! dépôt {depot['id']} : {str(e)[:100]} — {depot['url'][:80]}"
        print(ligne)
        lignes.append(ligne)
        ecrites += ligne.startswith("  ok")
        touches.add(domaine_de(depot["url"]))
        try:
            noter(depot["id"], res)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            print(f"  !! dépôt {depot['id']} non noté dans la base : {e}", file=sys.stderr)
    ecrire_memoire(m)
    sources = rapport_sources(m, touches)
    print("\nSources :")
    [print(l) for l in sources]
    alerte = ""
    if ecrites:
        subprocess.run(["git", "add", "sources/depots"], cwd=RACINE, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"dépôts : {ecrites} lien(s) ingéré(s) depuis le navigateur"],
            cwd=RACINE,
            capture_output=True,
        )
        alerte = pousser()
    suite = "Les notions seront extraites au prochain passage de la routine du graphe."
    if "--suivre" in sys.argv and GRAPHES_TOUCHES:
        suite = "\n".join(suivre())
    from newskit import notifier_courriel  # noqa: PLC0415

    objet = f"veille — {ecrites} lien(s) ingéré(s)" + (
        f", {len(attente) - ecrites} non" if len(attente) > ecrites else ""
    )
    corps = "\n".join(
        [*([alerte] if alerte else []), "Dépôts traités :", *lignes, "", "Sources :", *sources, "", suite]
    )
    notifier_courriel.envoyer(objet, corps)
    return 0


if __name__ == "__main__":
    sys.exit(main())

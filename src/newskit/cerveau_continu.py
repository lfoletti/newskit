"""Le passage continu : les fiches en attente entrent dans le cerveau que le serveur tient ouvert.

Un graphe continu (`cerveau_continu: true`) n'attend pas la nuit. Lancé toutes les quelques minutes, ce passage lit la
file des fiches (`file_fiches`), extrait la couche 2 des fiches denses sous le budget du jour, puis écrit chaque fiche
dans le serveur vivant, avec la clé d'écriture de l'édition : un document neuf s'ajoute, un document changé remplace
l'ancien, une fiche retirée quitte le cerveau. Le relevé des empreintes (`<g>.empreintes.json`), que la nuit lit, suit
chaque écriture ; un versement inchangé ne s'écrit pas.

Le passage attend sans rien faire pendant la routine du graphe, pendant une construction du cerveau, tant qu'un cerveau
prêt n'est pas mis en place, sans serveur vivant ou sans clé d'écriture. Les événements restent alors dans la file.

Une écriture interrompue laisse au relevé la marque « en cours » : le passage suivant remplace le document. Le contrôle
de la nuit compare tous les fichiers au cerveau et corrige ce que le passage continu ne voit pas (lots d'un graphe
inclus, arêtes gagnées par une fiche ancienne).

    python -m newskit.cerveau_continu --graphe <nom>
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from newskit import cerveau_cortexdb as cc
from newskit import config_sources as cfg
from newskit import fiche
from newskit import file_fiches
from newskit import lecture_cortexdb as lc
from newskit import puits_cortexdb as pc
from newskit import serveur_cortexdb as sc

EN_COURS = "en cours"  # au relevé, à la place d'une empreinte : l'écriture du document a commencé
LOT_MAX = 500  # événements traités par passage ; le reste attend le suivant
CONSTRUCTION = "une construction du cerveau est en cours"


def releve(graphe: str) -> Path:
    """Le relevé des empreintes du cerveau servi : par identifiant de document, l'empreinte de son versement."""
    return cc.DOSSIER / f"{graphe}.empreintes.json"


def lire_releve(graphe: str) -> dict[str, str]:
    try:
        return json.loads(releve(graphe).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def noter(p: Path, ident: str, empreinte: str | None) -> None:
    """Écrit au relevé l'empreinte d'un document, la marque « en cours » avant son écriture, ou le retire (None).
    L'écriture est atomique : le relevé n'est jamais lu à moitié écrit."""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        d = {}
    if empreinte is None:
        d.pop(ident, None)
    else:
        d[ident] = empreinte
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def du_jour(graphe: str) -> Path:
    """Les documents que le passage continu a écrits depuis le dernier contrôle de la nuit, avec leur empreinte."""
    return cc.DOSSIER / f"{graphe}.continu.jour.json"


def noter_du_jour(graphe: str, ident: str, empreinte: str | None) -> None:
    noter(du_jour(graphe), ident, empreinte)


def est_verse(graphe: str, chemin: str) -> bool:
    """La fiche (chemin relatif) est au cerveau servi : son document est au relevé, hors marque « en cours »."""
    return lire_releve(graphe).get(pc.court(chemin), EN_COURS) != EN_COURS


def routine_en_cours() -> str | None:
    from newskit import routine_hebdo as rh  # noqa: PLC0415 — module lourd, lu au passage

    return rh.graphe_en_routine()


def en_pause(graphe: str) -> bool:
    """La pause des routines (`cache/routines.pause`) vaut pour ce graphe, comme pour sa routine : sa ligne
    « sauf: <graphe>, <graphe> » laisse tourner les graphes nommés, ou ceux qui en incluent un."""
    try:
        texte = (pc.RACINE / "cache" / "routines.pause").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    m = re.search(r"^sauf\s*:\s*(.+)$", texte, flags=re.M)
    exceptes = {x.strip() for x in m.group(1).split(",") if x.strip()} if m else set()
    return not (exceptes & {graphe, *cfg.graphes_inclus(graphe)})


def peut_passer(graphe: str) -> str:
    """La raison d'attendre, vide si le passage peut écrire. Le verrou du cerveau se prend à part, dans `passer`."""
    if not cfg.cerveau_continu(graphe):
        return "le graphe n'a pas de cerveau continu"
    if en_pause(graphe):
        return "les routines sont en pause"
    if routine_en_cours() == graphe:
        return "la routine du graphe est en cours"
    if sc.pret(cc.chemin_cerveau(graphe)).exists():
        return "un cerveau prêt attend sa mise en place"
    if not cc.servi(graphe):
        return "aucun serveur ne tient le cerveau ouvert"
    if lc.lire_adresse_ecriture(lc.fichier_adresse(graphe)) is None:
        return "le serveur ne publie pas de clé d'écriture"
    return ""


def a_traiter(evenements: list[dict], retenues: set[str]) -> dict[str, str]:
    """Par chemin, ce que le passage fait : « verser » ou « retirer ». Le dernier événement d'une fiche décide ; une
    fiche hors du graphe (`retenues` : les fiches que le squelette garde) ou absente du disque se retire."""
    out: dict[str, str] = {}
    for e in evenements:
        chemin = str(e.get("chemin") or "")
        if not chemin:
            continue
        retiree = e.get("sorte") == "retiree" or chemin not in retenues or not (pc.RACINE / chemin).exists()
        out.pop(chemin, None)  # le dernier événement prend aussi la dernière place
        out[chemin] = "retirer" if retiree else "verser"
    return out


def ordonner(chemins: list[str], au_fil: set[str], retard: list[str]) -> list[str]:
    """L'ordre d'un passage : les fiches du fil, puis les autres fiches en événement, puis le retard."""
    tous = list(dict.fromkeys([*chemins, *retard]))
    return [c for c in tous if c in au_fil] + [c for c in tous if c not in au_fil]


def au_fil(graphe: str) -> set[str]:
    """Les fiches des billets du fil et de sa pile : leurs articles paraissent, leur lot passe d'abord."""
    from newskit import fil  # noqa: PLC0415 — module lourd, lu au passage

    d = fil.lire(graphe)
    chemins = {str((b or {}).get("chemin") or "") for b in [*(d.get("billets") or []), *(d.get("pile") or [])]}
    return {c if c.endswith(".md") else c + ".md" for c in chemins if c}


def squelette_du_graphe(graphe: str) -> dict:
    from newskit import extraire_squelette as ex  # noqa: PLC0415

    return ex.construire(graphe)[0]


def structure_de(source: str) -> dict:
    from newskit import structure_titres as st  # noqa: PLC0415

    return st.extraire([pc.RACINE / source], pc.RACINE)


def lot_de(source: str) -> dict | None:
    """Le lot de couche 2 de la fiche dans le graphe visé (`extraire_concepts.viser`), s'il existe."""
    from newskit import extraire_concepts as ec  # noqa: PLC0415

    chemin = pc.RACINE / source
    p = ec.LOTS / f"{ec.identifiant_de_fiche(chemin, fiche.lire_fichier(chemin)[0])}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _alias(graphe: str) -> dict[str, str]:
    p = cfg.chemins_du_graphe(graphe)["base"] / "alias.json"
    return pc.alias_par_identifiant(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else {}


def _connexion(graphe: str, client):
    if client is not None:
        return contextlib.nullcontext(client)
    from cortexdb_client import CortexClient  # noqa: PLC0415 — présent dans le venv qui lance le passage

    adresse, jeton = lc.lire_adresse_ecriture(lc.fichier_adresse(graphe))
    return CortexClient.connect(adresse, token=jeton)


def passer(graphe: str, client=None, horloge=None) -> dict:
    """Un passage continu du graphe ; rend son bilan. `attente` y dit pourquoi rien n'a été fait, `erreur` pourquoi
    le passage s'est arrêté : la file n'est alors pas acquittée et le passage suivant rejoue."""
    bilan: dict = {"graphe": graphe}
    if cfg.cerveau_continu(graphe):
        file_fiches.ouvrir(graphe)  # dès le premier passage, les événements s'écrivent, même pendant une attente
    raison = peut_passer(graphe)
    if raison:
        return {**bilan, "attente": raison}
    with cc.verrou_du_cerveau(graphe, attendre=False) as pris:
        if not pris:
            return {**bilan, "attente": CONSTRUCTION}
        return _passer(graphe, client, horloge or datetime.now, bilan)


def _passer(graphe: str, client, horloge, bilan: dict) -> dict:
    from newskit import extraire_concepts as ec  # noqa: PLC0415

    t0 = time.time()
    evenements = file_fiches.lire(graphe)[:LOT_MAX]
    bilan["evenements"] = len(evenements)
    p_releve, rel = releve(graphe), lire_releve(graphe)
    interrompus = [ident for ident, h in rel.items() if h == EN_COURS]
    decl = ec.viser(graphe)
    maintenant = horloge()
    budget = int(decl.get("couche2_continu_jour") or 0)
    faites = ec.faites_du_jour(ec.GRAPHE, maintenant.strftime("%Y-%m-%d")) if budget else 0
    retard = {
        f.relative_to(pc.RACINE).as_posix(): (f, h) for f, h in (ec.fiches_en_retard() if faites < budget else [])
    }
    du_fil = au_fil(graphe) if evenements or retard else set()
    en_evenement = list(dict.fromkeys(str(e.get("chemin") or "") for e in evenements if e.get("chemin")))
    candidates = [c for c in ordonner(en_evenement, du_fil, list(retard)) if c in retard]
    urgentes = [c for c in candidates if c in du_fil]
    n_fil, n_autres = ec.quota(budget, faites, maintenant, len(urgentes))
    liste = urgentes[:n_fil] + [c for c in candidates if c not in du_fil][:n_autres]
    if not evenements and not interrompus and not liste:
        return bilan
    if liste:
        extraction = ec.extraire([retard[c] for c in liste])
        bilan.update(extraites=extraction["faites"], echecs_d_extraction=extraction["echecs"])
    squelette = squelette_du_graphe(graphe)
    couche1 = pc.CoucheUn(squelette, pc.RACINE, _alias(graphe))
    canon = pc.canon_squelette({"nodes": squelette.get("nodes") or []})
    droits = cfg.charger_droits_auteurs()
    actions = a_traiter(evenements, couche1.fiches)
    for c in liste:
        if c in couche1.fiches:
            actions.setdefault(c, "verser")
    par_ident = {pc.court(c): c for c in couche1.fiches}
    orphelins = []
    for ident in interrompus:  # une écriture interrompue se rejoue, événement ou non
        if ident in par_ident:
            actions.setdefault(par_ident[ident], "verser")
        elif ident not in {pc.court(c) for c in actions}:
            orphelins.append(ident)
    bilan["invalides"] = 0
    with _connexion(graphe, client) as cl:
        ep = pc.EnPlace(cl, rel, bilan)

        def retirer(ident: str) -> None:
            if ident in rel:
                try:
                    ep.retirer(ident)
                except Exception as e:  # noqa: BLE001 — grpc.RpcError, sans importer grpc ici
                    if "NOT_FOUND" not in str(e):  # un retrait rejoué : le document a déjà quitté le cerveau
                        raise
                noter(p_releve, ident, None)
                noter_du_jour(graphe, ident, None)
                del rel[ident]

        try:
            for source in ordonner(list(actions), du_fil, []):
                v = None
                if actions[source] == "verser":
                    try:
                        v = pc.versement_d_une_fiche(
                            source,
                            lot_de(source),
                            couche1.d_une_fiche(source, structure_de(source)),
                            couche1.alias,
                            canon,
                            droits,
                            pc.RACINE,
                        )
                    except pc.LotInvalide as e:
                        bilan["invalides"] += 1
                        print(f"  !! {source} : {e}", file=sys.stderr)
                        continue
                if v is None:
                    retirer(pc.court(source))
                    continue
                ident, h = v["document"]["document_id"], pc.empreinte(v)
                if rel.get(ident) != h:
                    noter(p_releve, ident, EN_COURS)
                ep.verser(v, {})
                if rel.get(ident) != h:
                    noter(p_releve, ident, h)
                    noter_du_jour(graphe, ident, h)
                    rel[ident] = h
            for ident in orphelins:
                retirer(ident)
        except Exception as e:  # noqa: BLE001 — le serveur s'arrête entre deux appels : la file reste, le suivant rejoue
            bilan["erreur"] = f"{type(e).__name__} : {str(e)[:300]}"
    if "erreur" not in bilan:
        file_fiches.acquitter(graphe, len(evenements))
    bilan["duree_s"] = round(time.time() - t0, 1)
    return bilan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    args = ap.parse_args(argv)
    bilan = passer(args.graphe)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(bilan, ensure_ascii=False), flush=True)
    return 1 if "erreur" in bilan else 0


if __name__ == "__main__":
    sys.exit(main())

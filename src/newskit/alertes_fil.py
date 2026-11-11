"""Les alertes du fil par paliers (mainteneur, 11.11.2026 : « j'ai besoin d'une overview des limites et de l'assurance que
le fil soit toujours alimenté. éventuellement, alertes e-mail par paliers »).

La collecte continue appelle `surveiller` après chaque passage du fil. Les constats séparent le blocage (des billets
attendent, rien ne paraît) de la pénurie (les sources se taisent) :

- palier 2, un courriel par jour et par cause : budget du fil à PALIER2_BUDGET du plafond (les tâches secondaires
  s'arrêtent), PALIER2_SILENCE minutes sans billet de jour alors que la pile attend, publication sur le serveur en échec
  ECHECS_PUBLICATION fois de suite ;
- palier 3, un courriel toutes les REPETITION minutes tant que la cause dure : plafond du jour atteint, limite d'usage,
  passage du fil en échec ECHECS_FIL fois de suite, PALIER3_SILENCE minutes sans billet de jour.

Un courriel porte tous les constats du moment ; son objet nomme le palier le plus haut. L'état (dernier envoi par cause,
échecs de suite) vit dans cache/alertes_fil_<graphe>.json. Un envoi manqué se retente au passage suivant.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from newskit import couts
from newskit import fil
from newskit import notifier_courriel as nc
from newskit.commun import RACINE

PALIER2_BUDGET = fil.PART_SECONDAIRE
PALIER2_SILENCE = 60  # minutes sans billet, de jour, pile non vide : blocage
PALIER3_SILENCE = 180  # minutes sans billet, de jour, quelle qu'en soit la cause
REPETITION = 120  # minutes entre deux courriels du palier 3 pour la même cause
ECHECS_FIL = 2
ECHECS_PUBLICATION = 3
ETAT = RACINE / "cache" / "alertes_fil_{graphe}.json"
chemin_fil = fil.chemin_fil


def _minutes(depuis: str, quand: str) -> float:
    return (fil._instant(quand) - fil._instant(depuis)).total_seconds() / 60


def dernier_billet(F: dict) -> str | None:
    return max((b["ajoute"] for b in F.get("billets") or [] if b.get("ajoute")), default=None)


def constats(
    F: dict, plafond: float, quand: str, en_limite: bool, echecs_fil: int, echecs_publication: int
) -> list[tuple[int, str, str]]:
    """Les constats du moment : (palier, cause, phrase)."""
    depense = float((F.get("depense") or {}).get(quand[:10]) or 0)
    out = []
    if depense >= plafond - 0.10:
        out.append(
            (3, "plafond", f"plafond du jour atteint ({depense:.2f} $ sur {plafond:.2f} $) : plus aucun billet rédigé")
        )
    elif depense >= PALIER2_BUDGET * plafond:
        out.append(
            (2, "budget", f"budget du fil à {depense / plafond:.0%} : fiches, liens, définitions et anglais arrêtés")
        )
    if en_limite:
        out.append((3, "limite", "limite d'usage de l'abonnement atteinte : plus aucun billet rédigé"))
    if echecs_fil >= ECHECS_FIL:
        out.append((3, "passage", f"le passage du fil échoue ({echecs_fil} fois de suite)"))
    if echecs_publication >= ECHECS_PUBLICATION:
        out.append((2, "publication", f"la publication sur le serveur échoue ({echecs_publication} fois de suite)"))
    dernier = dernier_billet(F)
    if dernier and fil.de_jour(quand):
        silence = _minutes(dernier, quand)
        if silence >= PALIER3_SILENCE:
            out.append((3, "silence", f"aucun billet depuis {silence / 60:.1f} h"))
        elif silence >= PALIER2_SILENCE and F.get("pile"):
            out.append((2, "blocage", f"aucun billet depuis {silence:.0f} min, {len(F['pile'])} billet(s) attendent"))
    return out


def _du(palier: int, cause: str, etat: dict, quand: str) -> bool:
    """Vrai si la cause doit partir : palier 2 une fois par jour, palier 3 toutes les REPETITION minutes."""
    avant = (etat.get("envois") or {}).get(cause)
    if not avant:
        return True
    if palier >= 3:
        return _minutes(avant, quand) >= REPETITION
    return avant[:10] != quand[:10]


def _echec_publication(ligne: str) -> bool:
    return ligne.startswith(("échec", "sans accès"))


def surveiller(graphe: str, code_fil: int, publication: str | None, quand: str | None = None) -> list[str]:
    """Après un passage du fil : compte les échecs de suite, établit les constats, envoie le courriel dû. Rend les
    causes envoyées."""
    quand = quand or time.strftime("%Y-%m-%d %H:%M")
    p = Path(str(ETAT).format(graphe=graphe))
    try:
        etat = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        etat = {}
    etat["echecs_fil"] = int(etat.get("echecs_fil") or 0) + 1 if code_fil else 0
    if publication is not None:
        echec = _echec_publication(publication)
        etat["echecs_publication"] = int(etat.get("echecs_publication") or 0) + 1 if echec else 0
    envoyees = []
    try:
        F = json.loads(chemin_fil(graphe).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        F = None
    if F is not None:
        jour = quand[:10]
        tous = constats(
            F,
            fil.plafond(jour, graphe),
            quand,
            couts.en_limite(),
            etat["echecs_fil"],
            int(etat.get("echecs_publication") or 0),
        )
        dus = [c for c in tous if _du(c[0], c[1], etat, quand)]
        if dus:
            haut = max(dus, key=lambda c: c[0])
            objet = f"[fil · palier {haut[0]}] {graphe} : {haut[2]}"
            if nc.envoyer(objet, corps(F, graphe, quand, tous)) == 0:
                for c in dus:
                    etat.setdefault("envois", {})[c[1]] = quand
                envoyees = [c[1] for c in dus]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    return envoyees


def corps(F: dict, graphe: str, quand: str, tous: list[tuple[int, str, str]]) -> str:
    jour = quand[:10]
    depense = float((F.get("depense") or {}).get(jour) or 0)
    fiches = float((F.get("depense_fiche") or {}).get(jour) or 0)
    lignes = [f"Fil {graphe}, {quand}.", "", "Constats :"]
    lignes += [f"  - palier {p} : {t}" for p, _, t in sorted(tous, key=lambda c: -c[0])]
    lignes += [
        "",
        "État :",
        f"  - dernier billet paru : {dernier_billet(F) or 'aucun'}",
        f"  - {len(F.get('pile') or [])} billet(s) en attente dans la pile",
        f"  - dépense du fil : {depense:.2f} $ sur {fil.plafond(jour, graphe):.2f} $ (fiches : {fiches:.2f} $)",
        "",
        "Journal : cache/collecte_continue.log.",
    ]
    return "\n".join(lignes) + "\n"

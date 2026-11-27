"""Le refus d'un auteur ou d'une source (mainteneur, 06.11.2026 : « oui aux deux » ; CHANTIER_DEMARCHAGE.md, décisions du
6 novembre 2026). Un refus porte sur le texte de l'auteur dans le graphe, pas sur la présence de l'article ni sur le
relais : le texte quitte le graphe, l'article y reste par son titre, l'auteur, le lien et le résumé publié (le billet
du fil, sinon l'élément d'une lettre), d'où ses notions sont tirées à nouveau. Le fil et les lettres ne changent pas,
et la fiche reste au corpus : le fil écrit d'après elle.

`corps_utilisable` est le seul point de passage : l'extraction des notions (extraire_concepts.corps_de) et le
versement au cerveau (puits_cortexdb.corps_versable) le lisent. Le refus se lit dans les droits écrits par
`accords.py appliquer` (droits_auteurs.yaml, champ `masque` du registre), par le contrôle unique cfg.fiche_publiable.
Une fiche refusée change d'empreinte : l'extraction la reprend d'elle-même d'après le résumé, et le versement en place
remplace son document au cerveau.
"""

from __future__ import annotations

import json

from newskit import config_sources as cfg
from newskit.commun import RACINE

_cache: dict = {}


def oublier() -> None:
    """Relit les droits et les résumés au prochain appel (après `accords.py appliquer`, et dans les tests)."""
    _cache.clear()


def _droits() -> dict:
    if "droits" not in _cache:
        _cache["droits"] = cfg.charger_droits_auteurs()
    return _cache["droits"]


def _sources_masquees() -> bool:
    if "sources" not in _cache:
        try:
            _cache["sources"] = any(s.masque for s in cfg.toutes())
        except Exception:  # noqa: BLE001 — un registre illisible ne masque rien ici ; le contrôle unique le dira
            _cache["sources"] = False
    return _cache["sources"]


def refuse(chemin: str, entete: dict) -> bool:
    """Vrai si la source de la fiche ou l'un de ses auteurs a refusé : le texte ne sert plus au graphe."""
    droits = _droits()
    if not droits and not _sources_masquees():
        return False  # aucun refus nulle part : rien à lire de plus
    return not cfg.fiche_publiable(cfg.source_du_chemin(chemin), entete, droits)


def _index() -> dict[str, list[str]]:
    """Les résumés publiés par chemin de fiche : le billet du fil ou de son archive (français, puis anglais tenu), sinon
    l'élément d'une lettre gardée."""
    if "resumes" in _cache:
        return _cache["resumes"]
    fil: dict[str, list[str]] = {}
    for p in sorted((RACINE / "graphe").glob("*/fil.json")):
        try:
            F = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        archive = [{"chemin": c, **x} for c, x in (F.get("archive") or {}).items()]
        for b in (F.get("pile") or []) + (F.get("billets") or []) + archive:
            if b.get("chemin") and b.get("tenu") is not False and b["chemin"] not in fil:
                en = b.get("en") or {}
                fil[b["chemin"]] = [
                    x
                    for x in (
                        b.get("titre"),
                        b.get("descriptif"),
                        *((en.get("titre"), en.get("texte")) if en.get("tenu") else ()),
                    )
                    if x
                ]
    lettres: dict[str, list[str]] = {}
    for p in sorted((RACINE / "lettres").glob("*/*.json")):
        try:
            L = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        elements = list(L.get("faits") or []) + [
            x for g in ("aussi", "bref") for v in (L.get(g) or {}).values() for x in v
        ]
        for x in elements:
            texte = x.get("paragraphe") or x.get("descriptif")
            if x.get("chemin") and texte:
                lettres.setdefault(x["chemin"], [t for t in (x.get("titre"), texte) if t])
    _cache["resumes"] = {**lettres, **fil}
    return _cache["resumes"]


def resume(chemin: str) -> list[str]:
    """Le résumé publié d'une fiche, en lignes ; vide si elle n'a jamais été relayée."""
    return list(_index().get(chemin) or [])


def corps_utilisable(chemin: str, entete: dict, corps: str) -> str:
    """Le texte que le graphe peut lire d'une fiche : le sien, ou, sur un refus, sa référence et son résumé publié."""
    if not refuse(chemin, entete):
        return corps
    reference = [str(entete.get(k) or "") for k in ("title", "author", "url") if entete.get(k)]
    lignes = resume(chemin)
    return "\n".join(reference) + ("\n\nRésumé publié :\n" + "\n".join(lignes) if lignes else "") + "\n"

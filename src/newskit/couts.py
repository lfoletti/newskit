#!/usr/bin/env python3
"""Registre des coûts et coupe-circuit des appels au modèle (`claude -p`).

Ouvert le 08.10.2026 au soir, après que la moitié d'un forfait hebdomadaire a été consommée en 24 heures sans
qu'on sache par quel poste. Règle du projet depuis : **aucun appel au modèle sans ligne au registre**.

Trois services, employés par tous les scripts qui appellent `claude -p` :

1. `enregistrer(poste, graphe, modele, r, duree)` : à partir du résultat d'un `subprocess.run` (ou de son
   enveloppe JSON déjà lue), écrit une ligne dans `cache/couts.jsonl` — horodatage, poste (extraction, pyramide,
   poseur, banc, éclaireur, canon, avance, thèmes…), graphe, modèle, tokens par nature, tours, coût en dollars
   (`total_cost_usd`, l'équivalent API que rend Claude Code), durée, issue. Rend l'enveloppe (dict, vide si illisible).

2. Le coupe-circuit : une **limite d'usage** (réponse en 0 ms d'API, ou message explicite) est reconnue et comptée ;
   après `SEUIL` limites consécutives dans le processus, ou dès qu'un autre processus l'a signalée il y a moins de
   `MARQUE_VALIDE_S` secondes (fichier `cache/limite_atteinte`), `en_limite()` est vrai et les boucles s'arrêtent au
   lieu d'enchaîner des centaines d'appels refusés (08.10.2026 : 6 415 lots refusés d'affilée pour un graphe d'éthique, 7 435
   pour un graphe de sécurité, une demi-heure chacun). Un appel réussi remet le compteur à zéro et retire la marque.

3. Le bilan : `python3 -m newskit.couts` (ou `--etat`) agrège le registre par jour et par poste ;
   `python3 -m newskit.couts resumer` écrit `graphe/couts.json` (par jour : par poste et par graphe), versionné, que la
   routine met à jour en fin de passage et dont elle tire la ligne « coût du passage » de son bilan.

Le registre lui-même (`cache/couts.jsonl`, une ligne par appel) reste hors dépôt : il grossit de quelques milliers
de lignes par jour.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

REGISTRE = RACINE / "cache" / "couts.jsonl"
MARQUE = RACINE / "cache" / "limite_atteinte"
# Le début de toute commande au modèle, le seul : tout site compose `[*couts.CLAUDE_P, …]` (tests/test_couts.py).
# Le plugin cortexdb, installé au niveau de l'utilisateur le 18.10.2026, chargeait ses skills, ses outils et ses
# crochets dans chaque appel du pipeline : 75 259 jetons de contexte pour « ok », 22 714 sans (mesuré le 22.10.2026).
CLAUDE_P = [
    "claude",
    "-p",
    "--settings",
    json.dumps({"enabledPlugins": {"cortexdb@cortexdb": False, "thalamus@thalamus": False}}),
]
RESUME = RACINE / "graphe" / "couts.json"
SEUIL = 3  # limites consécutives avant que le processus se déclare en limite
MARQUE_VALIDE_S = (
    3 * 3600
)  # une marque posée par un autre processus vaut trois heures (la fenêtre d'usage se rouvre après)
RE_LIMITE = re.compile(
    r"usage limit|hit your limit|limit reached|rate.?limit|out of (?:extra )?usage|limite d.usage|"
    r"quota|too many requests|resets? at|overloaded",
    re.I,
)

# La sentinelle de jetons (CHANTIER_GENERIQUE.md, étape 1) : quand la médiane du contexte d'entrée (entrée, cache écrit,
# cache lu, pesés à leur prix : voir contexte()) des appels réussis d'un poste dans ce processus, après SENTINELLE_APPELS appels, dépasse SENTINELLE_FACTEUR
# fois la médiane des SENTINELLE_JOURS jours précédents, la marque est posée et les boucles s'arrêtent trois heures.
# Pas de jugement appel par appel : la queue est longue (un appel à plusieurs tours vaut couramment dix fois la
# médiane), et le rejeu du registre du 7 au 31.10.2026 l'aurait fait couper presque chaque nuit. Sur la médiane, le
# même rejeu voit l'incident du plugin dès sa première nuit (18.10.2026 : pyramide ×2,8, banc de thèmes local ×2,6)
# pour une seule alerte hors incident (banc_themes ×9, 10.10.2026).
SENTINELLE_FACTEUR = 2.5
SENTINELLE_JOURS = 7
SENTINELLE_MIN = 20  # appels réussis du poste sur la période, en dessous desquels la sentinelle ne juge pas
SENTINELLE_APPELS = 10  # appels réussis du poste dans le processus avant le premier jugement

_verrou = threading.Lock()
_consecutives = 0
_derniere_limite = 0.0  # l'heure du dernier refus : un succès lancé avant ne dit rien de la fenêtre (06.11.2026)
_medianes: dict[str, float | None] = {}  # par poste, calculée une fois par processus
_courants: dict[str, list[int]] = {}  # par poste, le contexte des appels réussis de ce processus
_alertes: set[str] = set()  # postes déjà signalés par la sentinelle dans ce processus


class LimiteAtteinte(RuntimeError):
    """Levée par les scripts qui préfèrent arrêter net une boucle plutôt que de continuer sans modèle."""


def lire_enveloppe(stdout: str | None) -> dict:
    """L'enveloppe JSON de `claude -p --output-format json`, ou {} si la sortie n'en est pas une."""
    t = (stdout or "").strip()
    if not t:
        return {}
    try:
        env = json.loads(t)
        return env if isinstance(env, dict) else {}
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                env = json.loads(t[i : j + 1])
                return env if isinstance(env, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def est_une_limite(env: dict, texte: str = "", code: int | None = None) -> bool:
    """Une limite d'usage : l'API n'a pas été appelée (`duration_api_ms` à 0) sur un appel non abouti, ou le message le dit.
    Un refus du modèle (`stop_reason: refusal` avec une durée d'API non nulle) n'est pas une limite."""
    if env:
        if env.get("subtype") == "error_max_budget_usd":
            return False  # coupé par son propre plafond (--max-budget-usd) : un plafond, pas une limite (03.11.2026)
        if env.get("duration_api_ms") == 0 and (code not in (0, None) or env.get("is_error")):
            return True
        if RE_LIMITE.search(str(env.get("result") or "")):
            return True
    return bool(texte and RE_LIMITE.search(texte))


def _poser_marque(note: str) -> None:
    try:
        MARQUE.parent.mkdir(parents=True, exist_ok=True)
        MARQUE.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {note}\n", encoding="utf-8")
    except OSError:
        pass


def _retirer_marque() -> None:
    try:
        MARQUE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def marque_en_cours() -> str | None:
    """Le contenu de la marque posée par un processus (le sien ou un autre) il y a moins de MARQUE_VALIDE_S, sinon None."""
    try:
        if MARQUE.exists() and time.time() - MARQUE.stat().st_mtime < MARQUE_VALIDE_S:
            return MARQUE.read_text(encoding="utf-8").strip()[:200]
    except OSError:
        pass
    return None


def en_limite() -> bool:
    """Vrai quand il ne sert à rien d'appeler le modèle : SEUIL limites d'affilée ici, ou une marque fraîche ailleurs."""
    return _consecutives >= SEUIL or marque_en_cours() is not None


def noter_limite(poste: str, graphe: str | None) -> None:
    """Compte une limite ; au seuil, pose la marque pour les autres processus de la nuit."""
    global _consecutives, _derniere_limite
    with _verrou:
        _consecutives += 1
        _derniere_limite = time.time()
        if _consecutives >= SEUIL:
            _poser_marque(f"limite d'usage : {SEUIL} appels refusés d'affilée ({poste}, {graphe or 'principal'})")


def noter_succes(debut: float | None = None) -> None:
    """Un appel abouti rouvre la fenêtre, sauf s'il a été lancé avant le dernier refus (`debut`) : en parallèle, un
    appel encore en vol quand la fenêtre se ferme finit après les refus ; le témoin l'a vu retirer la marque."""
    global _consecutives
    with _verrou:
        if debut is not None and debut < _derniere_limite:
            return
        if _consecutives:
            _consecutives = 0
            _retirer_marque()


# Le prix d'un jeton de cache, rapporté à un jeton d'entrée : écrit 1,25, lu 0,1. La sentinelle pèse le contexte à son prix
# (01.11.2026) : compté plein, le cache lu d'un banc par thème, quatre fois plus gros qu'une semaine avant pour le même coût
# par appel, coupait toutes les routines chaque matin depuis le 25 octobre ; rejouée sur le registre, la pesée ne tombe
# plus que sur les incidents connus (9 et 10 octobre, 18 au 21 octobre).
PRIX_CACHE_ECRIT = 1.25
PRIX_CACHE_LU = 0.1


def contexte(ligne: dict) -> float:
    """Le contexte d'entrée d'un appel, pesé à son prix, en jetons d'entrée : entrée, cache écrit, cache lu."""
    return (
        int(ligne.get("entree") or 0)
        + PRIX_CACHE_ECRIT * int(ligne.get("cache_ecriture") or 0)
        + PRIX_CACHE_LU * int(ligne.get("cache_lecture") or 0)
    )


def _mediane(v: list[int]) -> float:
    v = sorted(v)
    return float(v[len(v) // 2]) if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def sentinelle(poste: str, ctx: int, graphe: str | None = None) -> tuple[float, float] | None:
    """Ajoute un appel réussi au poste ; rend (médiane du processus, médiane de la période) quand la sentinelle
    tombe, une fois par poste, graphe et processus, sinon None. Un poste se juge dans son graphe (07.11.2026 : le banc
    d'un graphe, à 14 tours, jugé contre les bancs à deux tours des autres graphes, coupait toutes les boucles)."""
    cle = f"{poste}\t{graphe or 'principal'}"
    m = mediane_du_poste(poste, graphe)  # lue au premier appel, avant que ceux du processus n'entrent au registre
    v = _courants.setdefault(cle, [])
    v.append(ctx)
    if cle in _alertes or len(v) < SENTINELLE_APPELS:
        return None
    if m is None:
        return None
    ici = _mediane(v)
    if ici > SENTINELLE_FACTEUR * m:
        _alertes.add(cle)
        return ici, m
    return None


def mediane_du_poste(poste: str, graphe: str | None = None) -> float | None:
    """La médiane du contexte des appels réussis du poste dans son graphe sur SENTINELLE_JOURS jours ; sous
    SENTINELLE_MIN appels du graphe, celle du poste tous graphes confondus ; None sous SENTINELLE_MIN en tout."""
    cle = f"{poste}\t{graphe or 'principal'}"
    if cle not in _medianes:
        depuis = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - SENTINELLE_JOURS * 86400))
        tous = [d for d in lignes(depuis) if d.get("poste") == poste and d.get("ok")]
        ici = sorted(contexte(d) for d in tous if (d.get("graphe") or "principal") == (graphe or "principal"))
        v = ici if len(ici) >= SENTINELLE_MIN else sorted(contexte(d) for d in tous)
        _medianes[cle] = None if len(v) < SENTINELLE_MIN else _mediane(v)
    return _medianes[cle]


def _modele_de(env: dict, demande: str | None) -> str:
    mu = env.get("modelUsage")
    if isinstance(mu, dict) and mu:
        return ",".join(sorted(mu))
    return demande or "défaut"


def enregistrer(
    poste: str,
    graphe: str | None,
    modele: str | None,
    r=None,
    duree: float | None = None,
    env: dict | None = None,
    note: str = "",
) -> dict:
    """Consigne un appel et tient le coupe-circuit. `r` : le CompletedProcess de `claude -p` (stdout, stderr,
    returncode), ou None quand l'appel n'a pas abouti (délai, commande absente) — passer alors `note`. `env` : l'enveloppe
    déjà lue, si l'appelant l'a. Rend l'enveloppe (vide si illisible) ; ne lève jamais."""
    try:
        code = getattr(r, "returncode", None) if r is not None else None
        if env is None:
            env = lire_enveloppe(getattr(r, "stdout", None)) if r is not None else {}
        texte = (getattr(r, "stderr", "") or "") if r is not None else note
        u = env.get("usage") or {}
        limite = est_une_limite(env, texte, code)
        ok = bool(env) and code == 0 and not env.get("is_error") and not limite
        ligne = {
            "quand": time.strftime("%Y-%m-%d %H:%M:%S"),
            "poste": poste,
            "graphe": graphe or "principal",
            "modele": _modele_de(env, modele),
            "ok": ok,
            "limite": limite,
            "code": code,
            "entree": int(u.get("input_tokens") or 0),
            "cache_ecriture": int(u.get("cache_creation_input_tokens") or 0),
            "cache_lecture": int(u.get("cache_read_input_tokens") or 0),
            "sortie": int(u.get("output_tokens") or 0),
            "tours": int(env.get("num_turns") or 0),
            "cout_usd": round(float(env.get("total_cost_usd") or 0), 5),
            "duree_api_ms": int(env.get("duration_api_ms") or 0),
            "duree_s": round(duree, 1) if duree is not None else None,
            "arret": str(env.get("stop_reason") or ""),
            "pid": os.getpid(),
        }
        if note or (not ok and not env):
            ligne["note"] = (note or texte or "sortie illisible")[:160]
        alerte = sentinelle(poste, contexte(ligne), graphe) if ok else None
        if alerte:
            ligne["sentinelle"] = {"mediane_processus": alerte[0], "mediane_periode": alerte[1]}
        REGISTRE.parent.mkdir(parents=True, exist_ok=True)
        with _verrou:
            with open(REGISTRE, "a", encoding="utf-8") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        if limite:
            noter_limite(poste, graphe)
        elif ok:
            noter_succes(time.time() - duree if duree is not None else None)
        if alerte:
            _poser_marque(
                f"sentinelle de jetons : {poste} ({graphe or 'principal'}), médiane de {alerte[0]:.0f} jetons "
                f"d'entrée (pesés à leur prix) ici pour {alerte[1]:.0f} sur {SENTINELLE_JOURS} jours"
            )
            print(
                f"  !! sentinelle de jetons : {poste}, médiane {alerte[0]:.0f} jetons d'entrée contre {alerte[1]:.0f}",
                file=sys.stderr,
            )
        return env
    except Exception as e:  # noqa: BLE001 — le registre ne doit jamais faire échouer un appel
        print(f"  (registre des coûts : {type(e).__name__} {str(e)[:80]})", file=sys.stderr)
        return env or {}


# --- Bilans ---------------------------------------------------------------------------------------------------------


def lignes(depuis: str | None = None, jusqu_a: str | None = None) -> list[dict]:
    """Les lignes du registre entre deux horodatages « AAAA-MM-JJ[ HH:MM:SS] » (bornes incluses)."""
    if not REGISTRE.exists():
        return []
    out = []
    for l in REGISTRE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except json.JSONDecodeError:
            continue
        q = d.get("quand", "")
        if depuis and q < depuis:
            continue
        if jusqu_a and q > jusqu_a:
            continue
        out.append(d)
    return out


def agreger(ls: list[dict], cles: tuple[str, ...]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for d in ls:
        k = tuple(d.get("quand", "")[:10] if c == "jour" else d.get(c) for c in cles)
        a = out.setdefault(
            k,
            {
                "appels": 0,
                "ok": 0,
                "limites": 0,
                "echecs": 0,
                "tokens": 0,
                "sortie": 0,
                "cout_usd": 0.0,
                "duree_s": 0.0,
            },
        )
        a["appels"] += 1
        a["ok"] += bool(d.get("ok"))
        a["limites"] += bool(d.get("limite"))
        a["echecs"] += not d.get("ok") and not d.get("limite")
        a["tokens"] += d.get("entree", 0) + d.get("cache_ecriture", 0) + d.get("cache_lecture", 0) + d.get("sortie", 0)
        a["sortie"] += d.get("sortie", 0)
        a["cout_usd"] += d.get("cout_usd", 0) or 0
        a["duree_s"] += d.get("duree_s") or 0
    for a in out.values():
        a["cout_usd"] = round(a["cout_usd"], 3)
        a["duree_s"] = round(a["duree_s"])
    return out


def total(ls: list[dict]) -> dict:
    return next(
        iter(agreger(ls, ()).values()),
        {"appels": 0, "ok": 0, "limites": 0, "echecs": 0, "tokens": 0, "sortie": 0, "cout_usd": 0.0, "duree_s": 0},
    )


def phrase(ls: list[dict]) -> str:
    """Une ligne de bilan : « 412 appels, 7,83 $ — extraction 5,10 $ (280), pyramide 2,01 $ (90)… ; 12 refusés (limite) »."""
    t = total(ls)
    if not t["appels"]:
        return "aucun appel au modèle consigné"
    par_poste = sorted(agreger(ls, ("poste",)).items(), key=lambda kv: -kv[1]["cout_usd"])
    detail = ", ".join(f"{k[0]} {a['cout_usd']:.2f} $ ({a['appels']})" for k, a in par_poste[:8])
    s = f"{t['appels']} appel(s), {t['cout_usd']:.2f} $ — {detail}"
    if t["limites"]:
        s += f" ; {t['limites']} refusé(s) par la limite d'usage"
    if t["echecs"]:
        s += f" ; {t['echecs']} échec(s)"
    return s


def resumer() -> dict:
    """Écrit graphe/couts.json : par jour, le total, puis par poste et par graphe. Rend le résumé."""
    ls = lignes()
    resume: dict = {}
    for (jour,), a in sorted(agreger(ls, ("jour",)).items()):
        resume[jour] = {"total": a, "postes": {}, "graphes": {}, "modeles": {}}
    for (jour, poste), a in agreger(ls, ("jour", "poste")).items():
        resume[jour]["postes"][poste] = a
    for (jour, g), a in agreger(ls, ("jour", "graphe")).items():
        resume[jour]["graphes"][g] = a
    for (jour, m), a in agreger(ls, ("jour", "modele")).items():
        resume[jour]["modeles"][m] = a
    RESUME.parent.mkdir(parents=True, exist_ok=True)
    RESUME.write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
    return resume


def afficher(ls: list[dict], par: str) -> None:
    if not ls:
        print("  registre vide (cache/couts.jsonl) : aucun appel consigné")
        return
    cles = {"poste": ("jour", "poste"), "graphe": ("jour", "graphe"), "modele": ("jour", "modele"), "jour": ("jour",)}[
        par
    ]
    print(
        f"  {'jour':10} {par if par != 'jour' else '':14} {'appels':>7} {'ok':>5} {'limite':>6} {'échec':>5} {'tokens':>11} {'sortie':>8} {'coût $':>8} {'durée':>7}"
    )
    for k, a in sorted(agreger(ls, cles).items()):
        etiquette = str(k[1])[:14] if len(k) > 1 else ""
        print(
            f"  {k[0]:10} {etiquette:14} {a['appels']:7} {a['ok']:5} {a['limites']:6} {a['echecs']:5} {a['tokens']:11} "
            f"{a['sortie']:8} {a['cout_usd']:8.2f} {a['duree_s'] // 60:5.0f} mn"
        )
    t = total(ls)
    print(
        f"  total : {t['appels']} appels, {t['cout_usd']:.2f} $, {t['tokens']} tokens, {t['limites']} refusés par la limite, {t['echecs']} échecs"
    )
    m = marque_en_cours()
    if m:
        print(f"  LIMITE EN COURS : {m}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "action",
        nargs="?",
        choices=["etat", "resumer", "lever"],
        default="etat",
        help="etat : le bilan ; resumer : écrire graphe/couts.json ; lever : retirer la marque de limite",
    )
    ap.add_argument("--etat", action="store_true", help="(équivalent de l'action etat)")
    ap.add_argument("--depuis", help="AAAA-MM-JJ (défaut : 7 derniers jours)")
    ap.add_argument("--par", choices=["poste", "graphe", "modele", "jour"], default="poste")
    a = ap.parse_args()
    if a.action == "lever":
        _retirer_marque()
        print("  marque de limite retirée")
        return 0
    if a.action == "resumer":
        r = resumer()
        print(f"  {RESUME.relative_to(RACINE)} : {len(r)} jour(s)")
        for jour in sorted(r)[-3:]:
            print(f"    {jour} : {phrase([l for l in lignes() if l.get('quand', '')[:10] == jour])}")
        return 0
    depuis = a.depuis or time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    afficher(lignes(depuis), a.par)
    return 0


if __name__ == "__main__":
    sys.exit(main())

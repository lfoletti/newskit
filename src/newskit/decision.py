#!/usr/bin/env python3
"""Modèle de décision local : une question typée, une probabilité par option, sans génération.

Ce que Jev (TypeSafe AI, 15.09.2026) fait côté interface — `noul` (oui/non), `choice`, `score`, probabilités
calibrées, un passage avant — reproduit ici sur un petit modèle instruct gelé, sur la machine, sur CPU
(`CHANTIER_DECISION.md`). Le mécanisme est le softmax restreint décrit par les fiches du graphe (Medium
« Jev Clearly Explained », tutoriel Hyperstack, SemIf) : la consigne se termine là où la réponse commence,
chaque option porte une lettre à un seul jeton, on lit les logits de la dernière position sur ces seuls
jetons, et le softmax entre eux donne la distribution. Pas de prose, donc pas d'hallucination de forme ; la
justesse, elle, se mesure au banc (`banc_decision.py`) avant tout emploi.

Rien ne sort de la machine (porteur, 09.10.2026) ; aucun appel sans ligne au registre : chaque lot est consigné
par `couts.enregistrer` à 0 $, avec ses jetons et sa durée (un appel local est un appel aussi).

API :
    d = Decideur("qwen-0.5b")
    d.decider(etat, [{"type": "noul", "question": "…"},
                     {"type": "choice", "question": "…", "options": ["juste", "faux", "ne_sait_pas"]},
                     {"type": "score", "question": "…", "echelle": 5}], permuter=False)
    → [{"reponse", "probas": {option: p}, "confiance", "ms"}, …]  (score : aussi "score", l'espérance)

    python3 -m newskit.decision --fumee [--modele qwen-0.5b]   trois questions d'essai, sans rien écrire d'autre
    que la ligne du registre.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import couts  # noqa: E402

MODELES = {
    "qwen-0.5b": ("Qwen/Qwen2.5-0.5B-Instruct", "float32"),  # ≈ 1 Go sur disque, ≈ 2 Go en mémoire
    "qwen-1.5b": ("Qwen/Qwen2.5-1.5B-Instruct", "bfloat16"),  # ≈ 3 Go en mémoire ; seulement si 0.5b rate le critère
}
DEFAUT = "qwen-0.5b"
LETTRES = "ABCDEFGHIJKLMNOP"
SYSTEME = "You are a decision model. You answer with the letter of one option only, nothing else."
MAX_JETONS = 3072  # au-delà, l'état est coupé par la fin (les questions tiennent dans les 400 derniers)
FILS = int(os.environ.get("KG_DECISION_FILS", "4"))
_DECIDEUR: dict[str, "Decideur"] = {}


def rss_mo() -> int:
    """Mémoire résidente crête du processus, en Mo (macOS compte en octets)."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1_048_576)


class Decideur:
    def __init__(self, nom: str = DEFAUT) -> None:
        import torch  # noqa: PLC0415
        from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

        if nom not in MODELES:
            raise ValueError(f"modèle inconnu : {nom} (connus : {', '.join(MODELES)})")
        depot, dtype = MODELES[nom]
        self.nom = nom
        torch.set_num_threads(FILS)
        local = (
            Path(os.environ.get("KG_MODELES", "")) / depot.replace("/", "__") if os.environ.get("KG_MODELES") else None
        )
        source = str(local) if local and local.exists() else depot
        kw = {"local_files_only": os.environ.get("HF_HUB_OFFLINE", "1") == "1"}
        t0 = time.time()
        self.tok = AutoTokenizer.from_pretrained(source, **kw)
        self.tok.padding_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.modele = AutoModelForCausalLM.from_pretrained(source, dtype=getattr(torch, dtype), **kw).eval()
        self.chargement_s = round(time.time() - t0, 1)
        self._ids: dict[int, list[int]] = {}

    # --- lettres -------------------------------------------------------------------------------------------------
    def ids_lettres(self, n: int) -> list[int]:
        """Les identifiants de jeton de A, B, … (n lettres), chacun vérifié à un seul jeton, en cache."""
        if n > len(LETTRES):
            raise ValueError(f"au plus {len(LETTRES)} options")
        if n not in self._ids:
            ids = []
            for l in LETTRES[:n]:
                t = self.tok.encode(l, add_special_tokens=False)
                if len(t) != 1:
                    raise RuntimeError(f"la lettre {l} n'est pas un jeton unique pour {self.nom} : {t}")
                ids.append(t[0])
            self._ids[n] = ids
        return self._ids[n]

    def ids_directs(self, options: tuple[str, ...]) -> list[int] | None:
        """Les identifiants de jeton des options elles-mêmes, si chacune est un jeton unique (« Yes », « No ») : on
        lit alors ces jetons plutôt que des lettres, ce qui ôte le biais de position (mesuré le 09.10.2026 sur le
        0.5B : avec des lettres, A l'emportait toujours). Sinon None, et les lettres servent."""
        cle = ("direct", *options)
        if cle not in self._ids:
            ids = [self.tok.encode(o, add_special_tokens=False) for o in options]
            self._ids[cle] = [t[0] for t in ids] if all(len(t) == 1 for t in ids) else None
        return self._ids[cle]

    # --- consigne ------------------------------------------------------------------------------------------------
    def consigne(self, etat: str, question: str, options: list[str]) -> str:
        if self.ids_directs(tuple(options)) is not None:
            utilisateur = (
                f"{etat.strip()}\n\nQuestion: {question.strip()}\n\nAnswer with one word, {' or '.join(options)}."
            )
        else:
            lignes = "\n".join(f"{LETTRES[i]}. {o}" for i, o in enumerate(options))
            utilisateur = (
                f"{etat.strip()}\n\nQuestion: {question.strip()}\n\nOptions:\n{lignes}\n\n"
                f"Answer with the letter of the correct option only."
            )
        return self.tok.apply_chat_template(
            [{"role": "system", "content": SYSTEME}, {"role": "user", "content": utilisateur}],
            tokenize=False,
            add_generation_prompt=True,
        )

    # --- passage avant -------------------------------------------------------------------------------------------
    def scorer(self, consignes: list[str], options: list[list[str]]) -> tuple[list[list[float]], int]:
        """Pour chaque consigne, la distribution sur ses options (softmax restreint aux jetons des options, ou des
        lettres) ; et les jetons lus."""
        import torch  # noqa: PLC0415

        enc = self.tok(consignes, return_tensors="pt", padding=True, truncation=True, max_length=MAX_JETONS)
        with torch.inference_mode():
            logits = self.modele(**enc).logits[:, -1, :].float()
        out = []
        for k, opts in enumerate(options):
            ids = self.ids_directs(tuple(opts)) or self.ids_lettres(len(opts))
            out.append(torch.softmax(logits[k, ids], dim=0).tolist())
        return out, int(enc["attention_mask"].sum().item())

    # --- API à la Jev --------------------------------------------------------------------------------------------
    @staticmethod
    def options_de(q: dict) -> list[str]:
        t = q.get("type", "choice")
        if t == "noul":
            return ["Yes", "No"]
        if t == "score":
            k = int(q.get("echelle") or 5)
            return [str(i) for i in range(1, k + 1)]
        opts = list(q.get("options") or [])
        if len(opts) < 2:
            raise ValueError("une question `choice` a au moins deux options")
        return opts

    def decider(
        self,
        etat: str,
        questions: list[dict],
        *,
        permuter: bool = False,
        lot: int = 8,
        poste: str | None = None,
        graphe: str | None = None,
    ) -> list[dict]:
        """Une réponse par question. `permuter` : second passage avec les options en ordre inverse, moyenne des
        deux (biais de position des petits modèles, quand les options passent par des lettres). `poste` : consigne
        le lot au registre des coûts."""
        t0 = time.time()
        options = [self.options_de(q) for q in questions]
        plans = [(k, False) for k in range(len(questions))] + (
            [(k, True) for k in range(len(questions))] if permuter else []
        )
        probas: dict[int, list[list[float]]] = {k: [] for k in range(len(questions))}
        jetons = 0
        for i0 in range(0, len(plans), lot):
            tranche = plans[i0 : i0 + lot]
            consignes, opts_lot = [], []
            for k, inv in tranche:
                opts = options[k][::-1] if inv else options[k]
                consignes.append(
                    self.consigne(etat if isinstance(etat, str) else etat[k], questions[k]["question"], opts)
                )
                opts_lot.append(opts)
            dist, n = self.scorer(consignes, opts_lot)
            jetons += n
            for (k, inv), p in zip(tranche, dist):
                probas[k].append(p[::-1] if inv else p)
        ms = int((time.time() - t0) * 1000) or 1
        out = []
        for k, q in enumerate(questions):
            p = [sum(x) / len(probas[k]) for x in zip(*probas[k])]
            opts = options[k]
            meilleur = max(range(len(opts)), key=lambda i: p[i])
            r = {
                "reponse": opts[meilleur],
                "probas": {o: round(v, 4) for o, v in zip(opts, p)},
                "confiance": round(p[meilleur], 4),
                "ms": ms // max(1, len(questions)),
            }
            if q.get("type") == "noul":
                r["noul"] = round(p[0], 4)
            if q.get("type") == "score":
                r["score"] = round(sum((i + 1) * v for i, v in enumerate(p)), 3)
            out.append(r)
        if poste:
            enregistrer_lot(poste, graphe, self.nom, jetons, len(questions), ms)
        return out


def enregistrer_lot(poste: str, graphe: str | None, nom: str, jetons: int, n: int, ms: int) -> None:
    """Une ligne au registre pour un lot local : 0 $, mais jetons et durée mesurés, `ok` vrai (code 0, durée non
    nulle : un appel à 0 ms passerait pour une limite d'usage)."""
    couts.enregistrer(
        poste,
        graphe,
        f"local:{nom}",
        r=SimpleNamespace(returncode=0, stdout="", stderr=""),
        duree=ms / 1000,
        env={
            "usage": {"input_tokens": jetons, "output_tokens": n},
            "total_cost_usd": 0.0,
            "num_turns": 1,
            "duration_api_ms": max(1, ms),
            "stop_reason": "local",
        },
    )


def decideur(nom: str = DEFAUT) -> Decideur:
    """Un seul modèle chargé par processus."""
    if nom not in _DECIDEUR:
        _DECIDEUR[nom] = Decideur(nom)
    return _DECIDEUR[nom]


def fumee(nom: str) -> int:
    d = decideur(nom)
    print(f"  {nom} chargé en {d.chargement_s} s, RSS {rss_mo()} Mo")
    etat = (
        "Knowledge graph on AI compliance. Node A: « Data Minimization » (neighbours: GDPR ; Purpose limitation ; "
        "Article 5). Node B: « Minimisation des données » (neighbours: RGPD ; Article 5 ; Finalité)."
    )
    qs = [
        {"type": "noul", "question": "Do A and B denote exactly the same notion (translation, acronym, spelling)?"},
        {
            "type": "choice",
            "question": "What kind of relation holds between A and B?",
            "options": ["same notion in two languages", "parent and sub-case", "unrelated"],
        },
        {
            "type": "score",
            "question": "How confident are you that A and B should be merged, 1 (no) to 5 (certain)?",
            "echelle": 5,
        },
    ]
    t0 = time.time()
    for q, r in zip(qs, d.decider(etat, qs, permuter=True, poste="essai_decision", graphe="essai")):
        print(
            f"  [{q['type']}] {r['reponse']}  confiance {r['confiance']}  probas {r['probas']}"
            + (f"  score {r['score']}" if "score" in r else "")
        )
    print(f"  {len(qs)} questions ×2 (permutées) en {time.time() - t0:.1f} s, RSS {rss_mo()} Mo")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modele", default=DEFAUT, choices=sorted(MODELES))
    ap.add_argument("--fumee", action="store_true", help="trois questions d'essai")
    args = ap.parse_args()
    if args.fumee:
        return fumee(args.modele)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

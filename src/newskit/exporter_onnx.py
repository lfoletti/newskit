#!/usr/bin/env python3
"""Exportation ONNX de l'encodeur affiné et du reclasseur, pour le serveur (10.10.2026).

Le serveur (venv fastembed/onnxruntime, sans torch ni sentence-transformers, ~300 Mo) sert la recherche hybride et le
reclassement avec les mêmes modèles que ce Mac, exportés une fois ici en ONNX : le calcul du modèle (pooling moyen et
normalisation compris pour l'encodeur, logit pour le reclasseur) est dans le graphe ONNX, le serveur n'a besoin que
de `tokenizers` et d'`onnxruntime` (synthese._EncodeurONNX, synthese._ReclasseurONNX).

Chaque exportation est un dossier autonome `onnx/` (model.onnx, tokenizer.json, config.json) :
  - encodeur  : cache/encodeur/<graphe>/<modele retenu>/onnx/   (à côté du modèle sentence-transformers, hors dépôt)
  - reclasseur : cache/reclasseur/<nom court du modèle>/onnx/

    python3 -m newskit.exporter_onnx encodeur --graphe <graphe>      # le modèle que `retenu` nomme
    python3 -m newskit.exporter_onnx reclasseur                           # synthese.RECLASSEUR
    python3 -m newskit.exporter_onnx alleger --graphe <graphe>       # plongements en int8 (−500 Mo de RSS par modèle)
    python3 -m newskit.exporter_onnx verifier --graphe <graphe>      # torch contre ONNX sur des paragraphes réels

`verifier` est la mesure avant la mise en service : écart maximal des vecteurs, et accord du classement du reclasseur
sur de vrais candidats (synthese.passages_hybrides) ; publier_index.py envoie les dossiers `onnx/` au serveur.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import synthese as sy  # noqa: E402

OPSET = 17


def _exporter(module, exemples: tuple, sortie: Path, sorties: list[str]) -> None:
    import torch  # noqa: PLC0415

    sortie.parent.mkdir(parents=True, exist_ok=True)
    axes = {"input_ids": {0: "lot", 1: "seq"}, "attention_mask": {0: "lot", 1: "seq"}}
    axes.update({s: {0: "lot"} for s in sorties})
    with torch.inference_mode():
        torch.onnx.export(
            module,
            exemples,
            str(sortie),
            input_names=["input_ids", "attention_mask"],
            output_names=sorties,
            dynamic_axes=axes,
            opset_version=OPSET,
            dynamo=False,
            do_constant_folding=True,
        )


def exporter_encodeur(graphe: str) -> Path:
    """Le modèle affiné retenu du graphe → <modele>/onnx/ : vecteurs moyens normalisés (comme encode(normalize_embeddings=True))."""
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    from transformers import AutoModel  # noqa: PLC0415

    d = sy.modele_affine(graphe)
    if not d:
        sys.exit(f"aucun modèle affiné retenu pour {graphe} (cache/encodeur/{graphe}/retenu)")
    st = SentenceTransformer(str(d), device="cpu")
    pooling = json.loads((d / "1_Pooling" / "config.json").read_text(encoding="utf-8"))
    if pooling.get("pooling_mode") != "mean":
        sys.exit(f"pooling {pooling.get('pooling_mode')} non pris en charge (mean attendu)")
    m = AutoModel.from_pretrained(str(d), attn_implementation="eager").eval()

    class Enc(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            h = self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            mk = attention_mask.unsqueeze(-1).to(h.dtype)
            e = (h * mk).sum(1) / mk.sum(1).clamp(min=1e-9)
            return torch.nn.functional.normalize(e, p=2, dim=1)

    out = d / "onnx"
    ids = torch.tensor([[0, 6, 7, 8, 2], [0, 6, 2, 1, 1]], dtype=torch.int64)
    mask = (ids != 1).to(torch.int64)
    _exporter(Enc(), (ids, mask), out / "model.onnx", ["embedding"])
    shutil.copy(d / "tokenizer.json", out / "tokenizer.json")
    tcfg = json.loads((d / "tokenizer_config.json").read_text(encoding="utf-8"))
    (out / "config.json").write_text(
        json.dumps(
            {
                "genre": "encodeur",
                "nom": d.name,
                "max_seq_length": int(st.max_seq_length),
                "pad_token": tcfg.get("pad_token", "<pad>"),
                "dimension": int(st.get_sentence_embedding_dimension()),
                "exporte_le": time.strftime("%Y-%m-%d"),
                "opset": OPSET,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  encodeur {d.name} exporté : {out} ({(out / 'model.onnx').stat().st_size / 1e6:.0f} Mo)")
    return out


def dossier_reclasseur(nom: str = sy.RECLASSEUR) -> Path:
    return RACINE / "cache" / "reclasseur" / nom.split("/")[-1] / "onnx"


def exporter_reclasseur(nom: str = sy.RECLASSEUR) -> Path:
    """Le modèle croisé → cache/reclasseur/<nom>/onnx/ : un logit par paire (question, passage) ; seul l'ordre compte."""
    import torch  # noqa: PLC0415
    from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

    tok = AutoTokenizer.from_pretrained(nom)
    m = AutoModelForSequenceClassification.from_pretrained(nom, attn_implementation="eager").eval()

    class Rc(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits[:, 0]

    out = dossier_reclasseur(nom)
    ids = torch.tensor([[0, 6, 7, 2, 2, 8, 2], [0, 6, 2, 2, 8, 2, 1]], dtype=torch.int64)
    mask = (ids != 1).to(torch.int64)
    _exporter(Rc(), (ids, mask), out / "model.onnx", ["logit"])
    tok.save_pretrained(str(out / "_tok"))
    shutil.copy(out / "_tok" / "tokenizer.json", out / "tokenizer.json")
    shutil.rmtree(out / "_tok")
    (out / "config.json").write_text(
        json.dumps(
            {
                "genre": "reclasseur",
                "nom": nom,
                "max_length": 512,
                "pad_token": tok.pad_token or "<pad>",
                "exporte_le": time.strftime("%Y-%m-%d"),
                "opset": OPSET,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  reclasseur {nom} exporté : {out} ({(out / 'model.onnx').stat().st_size / 1e6:.0f} Mo)")
    return out


def alleger(dossier: Path) -> None:
    """Quantifie en int8 la seule table des plongements (opérateur Gather : 96 des 118 M de paramètres de ces deux modèles,
    vocabulaire de 250 000 jetons) ; le corps du transformeur reste en fp32. Mesuré le 10.10.2026 : fichier 470 → 182 Mo,
    RSS −500 Mo par modèle ; similarité des vecteurs ≥ 0,991 (moyenne 0,9997), 20 premiers voisins communs à 98,5 %,
    mêmes trois premiers du reclasseur 10/10. La quantification dynamique de tout le modèle (int8 partout) est refusée :
    voisins communs 81 %, ordre de tête du reclasseur perdu 10 fois sur 10."""
    from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: PLC0415

    src, tmp = dossier / "model.onnx", dossier / "model.int8.onnx"
    quantize_dynamic(str(src), str(tmp), weight_type=QuantType.QUInt8, op_types_to_quantize=["Gather"])
    tmp.replace(src)
    c = json.loads((dossier / "config.json").read_text(encoding="utf-8"))
    c["plongements"] = "int8"
    (dossier / "config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {dossier.parent.name} : plongements en int8, {src.stat().st_size / 1e6:.0f} Mo")


def verifier(graphe: str, combien: int = 40) -> bool:
    """Torch contre ONNX sur des paragraphes réels de l'index : vecteurs (écart maximal, similarité minimale) et
    reclassement (ordre identique des candidats pour quelques questions des bancs). Faux si l'écart dépasse la tolérance."""
    import numpy as np  # noqa: PLC0415

    ix = sy.index(graphe)
    if not ix:
        sys.exit(f"index absent pour {graphe} (synthese.py indexer)")
    textes = [t for t in ix[1] if t][:combien]
    bon = True
    d = sy.modele_affine(graphe)
    if d and (d / "onnx" / "model.onnx").exists():
        t0 = time.time()
        A = np.array(list(sy._EncodeurAffine(d).embed(textes)))
        ta = time.time() - t0
        t0 = time.time()
        B = np.array(list(sy._EncodeurONNX(d / "onnx").embed(textes)))
        tb = time.time() - t0
        ecart = float(np.abs(A - B).max())
        sim = float((A * B).sum(1).min())
        leger = json.loads((d / "onnx" / "config.json").read_text(encoding="utf-8")).get("plongements") == "int8"
        print(
            f"  encodeur{' (plongements int8)' if leger else ''} : {len(textes)} paragraphes, écart max {ecart:.2e}, "
            f"similarité min {sim:.5f} ; torch {ta:.1f} s, onnx {tb:.1f} s"
        )
        bon &= sim > 0.98 if leger else ecart < 1e-3
    else:
        print("  encodeur : pas d'exportation ONNX à vérifier")
    rc = dossier_reclasseur()
    if (rc / "model.onnx").exists():
        questions = []
        for f in (RACINE / "graphe" / graphe / "banc_questions.json",):
            if f.exists():
                try:
                    j = json.loads(f.read_text(encoding="utf-8"))
                    questions = [
                        q.get("question") or q.get("q") for q in (j if isinstance(j, list) else j.get("questions", []))
                    ][:5]
                except (ValueError, AttributeError):
                    pass
        questions = [q for q in questions if q] or [
            "Quelles sont les obligations des fournisseurs de modèles à usage général ?"
        ]
        rt = sy.reclasseur()
        ro = sy._ReclasseurONNX(rc)
        accords, top3, tt, to = 0, 0, 0.0, 0.0
        for q in questions:
            cands = [t[:1500] for t in textes[: sy.CANDIDATS_RECLASSEUR]]
            paires = [(q, t) for t in cands]
            t0 = time.time()
            st = np.asarray(rt.predict(paires, batch_size=16, show_progress_bar=False))
            tt += time.time() - t0
            t0 = time.time()
            so = ro.predict(paires)
            to += time.time() - t0
            a, b = np.argsort(-st), np.argsort(-so)
            accords += int((a == b).all())
            top3 += int(set(a[:3]) == set(b[:3]))
        print(
            f"  reclasseur : {len(questions)} questions × {len(cands)} candidats, ordre identique {accords}/{len(questions)}, "
            f"mêmes 3 premiers {top3}/{len(questions)} ; torch {tt:.1f} s, onnx {to:.1f} s"
        )
        bon &= top3 == len(questions)
    else:
        print("  reclasseur : pas d'exportation ONNX à vérifier")
    return bon


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("encodeur")
    p.add_argument("--graphe", required=True)
    p = sub.add_parser("reclasseur")
    p.add_argument("--modele", default=sy.RECLASSEUR)
    p = sub.add_parser("alleger", help="plongements en int8 (Gather seul) dans les exportations existantes")
    p.add_argument("--graphe", required=True)
    p.add_argument("--sans-reclasseur", action="store_true")
    p = sub.add_parser("verifier")
    p.add_argument("--graphe", required=True)
    p.add_argument("--combien", type=int, default=40)
    a = ap.parse_args()
    if a.cmd == "encodeur":
        exporter_encodeur(a.graphe)
    elif a.cmd == "reclasseur":
        exporter_reclasseur(a.modele)
    elif a.cmd == "alleger":
        d = sy.modele_affine(a.graphe)
        if d and (d / "onnx" / "model.onnx").exists():
            alleger(d / "onnx")
        if not a.sans_reclasseur and (dossier_reclasseur() / "model.onnx").exists():
            alleger(dossier_reclasseur())
    elif a.cmd == "verifier":
        return 0 if verifier(a.graphe, a.combien) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

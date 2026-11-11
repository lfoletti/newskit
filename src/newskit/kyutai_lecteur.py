"""Le lecteur Kyutai de lecture_audio.py : lancé par l'interpréteur de l'environnement Kyutai (NEWSKIT_AUDIO_PYTHON,
Python 3.11 avec moshi_mlx), jamais importé par newskit.

Il charge le modèle une fois (Kyutai TTS, MLX, poids en 8 bits), puis lit sur l'entrée une ligne JSON par texte,
{"texte": ..., "sortie": chemin du WAV} ; il répond sur la sortie {"ok": true} ou {"ok": false, "erreur": ...}. Les
journaux vont sur la sortie d'erreur. D'après dsm/scripts/tts_mlx.py de Kyutai (delayed-streams-modeling).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback


def charger(modele: str, voix: str, quantize: int, temp: float):
    import mlx.core as mx
    import mlx.nn as nn
    import sentencepiece
    from moshi_mlx import models
    from moshi_mlx.models.tts import DEFAULT_DSM_TTS_VOICE_REPO, TTSModel
    from moshi_mlx.utils.loaders import hf_get

    with open(hf_get(hf_get("config.json", modele)), encoding="utf-8") as f:
        raw = json.load(f)
    lm_config = models.LmConfig.from_config_dict(raw)
    lm_config.transformer.max_seq_len = lm_config.transformer.context  # contournement de moshi_mlx <= 0.3.0
    lm = models.Lm(lm_config)
    lm.set_dtype(mx.bfloat16)
    lm.load_pytorch_weights(str(hf_get(raw.get("moshi_name", "model.safetensors"), modele)), lm_config, strict=True)
    nn.quantize(lm.depformer, bits=quantize)
    for couche in lm.transformer.layers:
        nn.quantize(couche.self_attn, bits=quantize)
        nn.quantize(couche.gating, bits=quantize)
    texte = sentencepiece.SentencePieceProcessor(str(hf_get(raw["tokenizer_name"], modele)))
    mimi = models.mimi.Mimi(models.mimi_202407(lm_config.generated_codebooks))
    mimi.load_pytorch_weights(str(hf_get(raw["mimi_name"], modele)), strict=True)
    tts = TTSModel(
        lm,
        mimi,
        texte,
        voice_repo=DEFAULT_DSM_TTS_VOICE_REPO,
        temp=temp,
        cfg_coef=1,
        max_padding=8,
        initial_padding=2,
        final_padding=2,
        padding_bonus=0,
        raw_config=raw,
    )
    cfg = None
    if tts.valid_cfg_conditionings:  # modèle entraîné par distillation du guidage
        cfg, tts.cfg_coef = tts.cfg_coef, 1.0
    voix_ = [tts.get_voice_path(voix)] if tts.multi_speaker else []
    return tts, tts.make_condition_attributes(voix_, cfg), not tts.valid_cfg_conditionings


def lire(tts, attributs, sans_guidage: bool, texte: str, sortie: str) -> None:
    import mlx.core as mx
    import numpy as np
    import sphn

    mx.random.seed(299792458)  # la graine du script de Kyutai : la voix retenue a été écoutée avec elle
    morceaux = []

    def trame(t):
        if (t == -1).any():
            return
        pcm = tts.mimi.decode_step(t[:, :, None])
        morceaux.append(np.array(mx.clip(pcm[0, 0], -1, 1)))

    tts.generate(
        [tts.prepare_script([texte])],
        [attributs],
        cfg_is_no_prefix=sans_guidage,
        cfg_is_no_text=sans_guidage,
        on_frame=trame,
    )
    sphn.write_wav(sortie, np.concatenate(morceaux, -1), tts.mimi.sample_rate)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modele", required=True)
    ap.add_argument("--voix", required=True)
    ap.add_argument("--quantize", type=int, default=8)
    ap.add_argument("--temp", type=float, default=0.6)
    a = ap.parse_args()
    tts, attributs, sans_guidage = charger(a.modele, a.voix, a.quantize, a.temp)
    print("modèle chargé", file=sys.stderr, flush=True)
    for ligne in sys.stdin:
        try:
            d = json.loads(ligne)
            lire(tts, attributs, sans_guidage, d["texte"], d["sortie"])
            rep = {"ok": True}
        except Exception as e:  # noqa: BLE001 — un texte en échec ne coupe pas le lecteur
            traceback.print_exc(file=sys.stderr)
            rep = {"ok": False, "erreur": f"{type(e).__name__} : {e}"[:300]}
        print(json.dumps(rep), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

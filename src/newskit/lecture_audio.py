#!/usr/bin/env python3
"""La lecture audio des billets (mainteneur, 11.11.2026 : « la création des audios pourrait être un processus parallèle
indépendant » ; « la génération audio prend toujours le billet le plus récent et va descendant dans le fil »).

Un processus à part, sans lien avec le passage du fil : il lit la copie publique du fil sur le site (fil.json), donne un
son à chaque billet qui n'en a pas d'à jour, du plus récent au plus ancien. Il dépose sur le VPS les sons puis leur
index (audio.json : adresse du billet → empreinte). Après chaque billet, il relit la copie et repart du haut : un billet
entrant passe avant le retard. Le site montre un picto audio pour chaque billet de l'index.

Le modèle (Kyutai TTS, MLX) tourne dans un environnement Python à part : NEWSKIT_AUDIO_PYTHON nomme son interpréteur,
qui lance kyutai_lecteur.py et garde le modèle chargé le temps du passage. ffmpeg ralentit le son (vitesse) et l'écrit
en Opus, puis en AAC. Les réglages viennent de `lecture_audio` dans le config.yaml du média.

Usage :
    python3 -m newskit.lecture_audio
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import select
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from newskit import media, publier_fil
from newskit.commun import RACINE

CACHE = RACINE / "cache" / "audio"
FORMATS = (".webm", ".mp4")  # Opus, puis AAC pour les navigateurs sans Opus
DUREE_MAX = 50 * 60  # secondes de conversion par passage, au plus : le passage suivant reprend
LIRE_MAX = 15 * 60  # secondes pour un billet ; au-delà, le lecteur est arrêté (un modèle bloqué garderait le verrou)
VERSION = 1  # la façon de lire le texte ; la changer refait tous les sons
LECTEUR = Path(__file__).with_name("kyutai_lecteur.py")


def reglages(c: dict | None = None) -> dict | None:
    """Les réglages `lecture_audio` du média ; None sans eux : pas de lecture audio."""
    c = media.courant() if c is None else c
    return c.get("lecture_audio") or None


def url_copie(c: dict | None = None) -> str:
    """La copie publique du fil sur le site du média."""
    m = (media.courant() if c is None else c).get("media") or {}
    return str(m.get("lettre_site") or m.get("site") or "").rstrip("/") + "/fil.json"


def lire_copie(url: str) -> list[dict] | None:
    """Les billets de la copie publique ; None si elle ne se lit pas."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "newskit-lecture-audio"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — l'adresse vient du config.yaml du média
            return list(json.loads(r.read().decode("utf-8")).get("billets") or [])
    except (OSError, ValueError) as e:
        print(f"copie publique illisible ({type(e).__name__})", file=sys.stderr)
        return None


# Le texte lu


def _net(t: str) -> str:
    t = str(t or "").replace(" ", " ").replace(" ", " ")
    t = re.sub(r"\s*&\s*", " et ", t)
    t = re.sub(r"\s*\(([^()]*)\)", r", \1,", t)  # une parenthèse se lit entre deux virgules
    t = re.sub(r",\s*([,.;:!?])", r"\1", t)
    return " ".join(t.split())


def texte_lu(b: dict, sigles: dict | None = None) -> str:
    """Le texte que la voix lit : titre, chapeau (résumé, sinon descriptif), textes des éléments sans leurs intertitres.
    Signes remplacés ; un sigle de `sigles` se développe à sa première apparition, si le texte ne le développe pas."""
    titre = _net(b.get("titre"))
    if titre and titre[-1] not in ".!?…":
        titre += "."
    corps = [_net(b.get("resume") or b.get("descriptif"))] + [_net(e.get("texte")) for e in b.get("elements") or []]
    t = "\n\n".join(p for p in [titre, *corps] if p)
    for sigle, long in (sigles or {}).items():
        if long not in t:
            t = re.sub(rf"\b{re.escape(sigle)}\b", long, t, count=1)
    return t


def empreinte(texte: str, r: dict) -> str:
    """Le nom du son : il change avec le texte lu, la voix, la vitesse ou le modèle."""
    cle = json.dumps([VERSION, texte, r["modele"], r["voix"], r["vitesse"]], ensure_ascii=False)
    return hashlib.sha256(cle.encode()).hexdigest()[:16]


def a_convertir(billets: list[dict], index: dict, r: dict) -> list[tuple[dict, str, str]]:
    """Les billets sans son à jour, du plus récent au plus ancien : (billet, texte lu, empreinte)."""
    out = []
    for b in sorted(billets, key=lambda b: str(b.get("ajoute") or ""), reverse=True):
        t = texte_lu(b, r.get("sigles"))
        e = empreinte(t, r)
        if index.get(b.get("url")) != e:
            out.append((b, t, e))
    return out


# Le modèle, ffmpeg, le dépôt


class Lecteur:
    """Le modèle Kyutai, chargé une fois dans son environnement : un texte par ligne JSON, un WAV par réponse."""

    def __init__(self, r: dict):
        python = os.environ.get("NEWSKIT_AUDIO_PYTHON")
        if not python:
            raise RuntimeError("NEWSKIT_AUDIO_PYTHON : l'interpréteur de l'environnement Kyutai n'est pas nommé")
        self.p = subprocess.Popen(
            [python, str(LECTEUR), "--modele", r["modele"], "--voix", r["voix"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def lire(self, texte: str, wav: Path) -> bool:
        try:
            self.p.stdin.write(json.dumps({"texte": texte, "sortie": str(wav)}, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            if not select.select([self.p.stdout], [], [], LIRE_MAX)[0]:
                print("lecteur sans réponse : arrêté", file=sys.stderr)
                self.p.kill()
                return False
            ligne = self.p.stdout.readline()
            return bool(json.loads(ligne).get("ok")) and Path(wav).is_file()
        except (OSError, ValueError):
            return False

    def fermer(self) -> None:
        with contextlib.suppress(OSError):
            self.p.stdin.close()
        try:
            self.p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.p.kill()


def encoder(wav: Path, base: Path, vitesse: float) -> bool:
    """Le WAV ralenti à `vitesse`, sans changer la hauteur, en Opus 48 kb/s puis en AAC 64 kb/s."""
    codes = []
    for ext, codec in ((".webm", ["-c:a", "libopus", "-b:a", "48k"]), (".mp4", ["-c:a", "aac", "-b:a", "64k"])):
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", f"atempo={vitesse}", "-ac", "1", *codec]
        codes.append(subprocess.run([*cmd, str(base) + ext], capture_output=True, check=False).returncode)
    return not any(codes)


def deposer(dossier: Path, nouveaux: list[str], index: dict, env: Path = publier_fil.ENV) -> str:
    """Dépose les sons nouveaux, puis l'index, puis retire du VPS les sons hors index ; rend une ligne pour le journal."""
    v = publier_fil.acces(env)
    if v is None:
        return f"sans accès au VPS ({env})"
    corps = json.dumps({"billets": index}, ensure_ascii=False, separators=(",", ":")).encode()
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as t:
        for nom, donnees in [*((n, (Path(dossier) / n).read_bytes()) for n in nouveaux), ("audio.json", corps)]:
            info = tarfile.TarInfo(nom)
            info.size, info.mode, info.mtime = len(donnees), 0o644, int(time.time())
            t.addfile(info, io.BytesIO(donnees))
    d = publier_fil.DOSSIER_VPS + "/audio"
    garde = " ".join(e + ext for e in sorted(set(index.values())) for ext in FORMATS)
    distant = "; ".join(
        [
            f"set -e; mkdir -p {d}/.neuf; cd {d}; tar -C .neuf -xf -",
            *(f"mv -f .neuf/{n} {n}" for n in nouveaux),
            "mv -f .neuf/audio.json audio.json",
            f"garde=' {garde} '",
            'for f in *.webm *.mp4; do [ -e "$f" ] || continue; case "$garde" in *" $f "*) ;; *) rm -f "$f";; esac; done',
        ]
    )
    r = subprocess.run(
        [
            "ssh",
            "-i",
            v["VPS_CLE"],
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            f"{v['VPS_UTILISATEUR']}@{v['VPS_HOTE']}",
            distant,
        ],
        input=tampon.getvalue(),
        capture_output=True,
        check=False,
    )
    if r.returncode:
        return f"échec du dépôt (ssh {r.returncode}) : {r.stderr.decode(errors='replace').strip()[:160]}"
    return f"déposé ({len(nouveaux) // len(FORMATS)} son(s) neuf(s), {len(index)} billet(s))"


# Le passage


@contextlib.contextmanager
def verrou():
    """Un passage à la fois ; rend False si un autre passage tient le verrou."""
    import fcntl  # noqa: PLC0415

    CACHE.mkdir(parents=True, exist_ok=True)
    with open(CACHE / "audio.verrou", "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


def _index() -> dict:
    try:
        return dict(json.loads((CACHE / "audio.json").read_text(encoding="utf-8")).get("billets") or {})
    except (OSError, ValueError):
        return {}


def _garder(index: dict) -> None:
    (CACHE / "audio.json").write_text(json.dumps({"billets": index}, ensure_ascii=False), encoding="utf-8")
    garde = {e + ext for e in index.values() for ext in FORMATS}
    for f in CACHE.iterdir():
        if f.suffix in (*FORMATS, ".wav") and f.name not in garde:
            f.unlink(missing_ok=True)


def passer(lecteur=None, horloge=time.monotonic) -> int:
    """Un passage : convertit et dépose billet par billet, du plus récent au plus ancien, en relisant la copie publique
    après chaque billet. Rend le nombre de billets convertis."""
    r = reglages()
    if not r:
        return 0
    with verrou() as pris:
        if not pris:
            print("un autre passage tourne")
            return 0
        index, faits, echoues, lec, debut = _index(), 0, set(), lecteur, horloge()
        depose = dict(index)
        try:
            while True:
                billets = lire_copie(url_copie())
                if billets is None:
                    break
                presents = {b.get("url") for b in billets}
                index = {u: e for u, e in index.items() if u in presents}  # un billet sorti de la copie sort de l'index
                file = [x for x in a_convertir(billets, index, r) if x[0].get("url") not in echoues]
                if not file or horloge() - debut > DUREE_MAX:
                    break
                b, texte, e = file[0]
                if lec is None:
                    try:
                        lec = Lecteur(r)
                    except (RuntimeError, OSError) as err:
                        print(f"modèle indisponible : {err}", file=sys.stderr)
                        break
                wav = CACHE / f"{e}.wav"
                if not (lec.lire(texte, wav) and encoder(wav, CACHE / e, r["vitesse"])):
                    print(f"échec de la conversion : {b.get('url')}", file=sys.stderr)
                    echoues.add(b.get("url"))
                    continue
                neuf = {**index, b["url"]: e}
                ligne = deposer(CACHE, [e + ext for ext in FORMATS], neuf)
                print(f"{b.get('ajoute')} {ligne}")
                if not ligne.startswith("déposé"):
                    break
                index = depose = neuf
                _garder(index)
                faits += 1
            if index != depose:  # des billets sortis de la copie : l'index se dépose sans eux
                ligne = deposer(CACHE, [], index)
                print(ligne)
                if ligne.startswith("déposé"):
                    _garder(index)
        finally:
            if lec is not None and lec is not lecteur:
                lec.fermer()
        return faits


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    print(f"{passer()} billet(s) converti(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

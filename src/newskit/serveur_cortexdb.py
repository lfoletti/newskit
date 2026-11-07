#!/usr/bin/env python3
"""Le serveur CortexDB permanent d'un graphe, que `lecture_cortexdb` interroge pour les passages de `repondre`
(CHANTIER_PILOTE_CORTEXDB.md, 23.10.2026).

Deux processus, gardés par celui-ci : l'encodeur du graphe derrière /v1/embeddings (embeddings_locaux.py, le même que
l'index) et cortexdb-grpc sur le cerveau publié (`cache/cortexdb/<g>.db`). Tous deux écoutent sur 127.0.0.1 seulement ;
l'adresse et un jeton tiré au démarrage s'écrivent dans `cache/cortexdb/<g>.adresse` (lisible du seul propriétaire),
que la lecture relit à chaque question.

Un cerveau servi ne se remplace pas sous le serveur : cortexdb-grpc le garde ouvert, avec son journal (-wal) et son
instantané HNSW réécrit à la fermeture. La construction publie donc à côté (`<g>.pret.db`) quand le graphe est servi ;
le gardien voit le fichier, arrête cortexdb, le met en place, efface le journal de l'ancien et relance. Le cerveau publié
a déjà été ouvert avec l'encodeur (cerveau_cortexdb.CHAUFFE) : la relance ne reconstruit pas l'index.

Si cortexdb meurt, le gardien sort en erreur et son service (launchd, systemd) le relance.

    python3 -m newskit.serveur_cortexdb --graphe <graphe>
    KG_CORTEXDB_BINAIRE=/chemin/cortexdb-grpc python3 -m newskit.serveur_cortexdb --graphe <graphe>
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

from newskit import cerveau_cortexdb as cc  # noqa: E402
from newskit import lecture_cortexdb as lc  # noqa: E402

TOUR = 10   # secondes entre deux regards du gardien


def pret(cerveau: Path) -> Path:
    """Le cerveau publié à côté du servi, en attente d'être mis en place."""
    return cerveau.with_name(cerveau.stem + ".pret.db")


def ecrire_adresse(p: Path, adresse: str, jeton: str, pid: int | None = None) -> None:
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"adresse": adresse, "jeton": jeton, "pid": pid or os.getpid()}, f)
    os.replace(tmp, p)


def assurer_cle(cles: Path, graphe: str) -> str:
    """La clé de lecture de l'édition (`<edition>/<g>`, tout le cerveau, bloc `edition:` du registre) dans le fichier de clés que Thalamus tient ;
    créée une fois, rendue ensuite. Thalamus ne touche qu'à ses clés (`th/…`) : celle-ci reste."""
    try:
        d = json.loads(cles.read_text(encoding="utf-8"))
    except FileNotFoundError:
        d = {"keys": []}
    from newskit import config_sources as cfg  # noqa: PLC0415
    ident = f"{cfg.edition()['nom']}/{graphe}"
    for k in d.get("keys") or []:
        if k.get("id") == ident:
            return k["secret"]
    secret = secrets.token_hex(24)
    d.setdefault("keys", []).append({"id": ident, "secret": secret, "clearance": "read-only"})
    tmp = cles.with_name(cles.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, cles)
    return secret


def environnement(cerveau: Path, port: int, jeton: str, encodeur_url: str, dim: int, cles: Path | None = None) -> dict:
    """Avec un fichier de clés (Thalamus), cortexdb le prend pour toute sa politique : le jeton ne sert plus."""
    env = {**os.environ, "CORTEXDB_PATH": str(cerveau), "CORTEXDB_GRPC_ADDR": f"127.0.0.1:{port}",
           "OPENAI_BASE_URL": encodeur_url, "OPENAI_API_KEY": "local", "CORTEXDB_EMBED_MODEL": "local",
           "CORTEXDB_EMBED_DIM": str(dim)}
    env.pop("CORTEXDB_GRPC_TOKEN", None)
    if cles:
        env["CORTEXDB_KEY_FILE"] = str(cles)
    else:
        env["CORTEXDB_GRPC_TOKEN"] = jeton
    return env


class Gardien:
    """cortexdb-grpc sur `cerveau`, relancé sur un cerveau prêt et quand le fichier `surveille` change (les clés de
    Thalamus : cortexdb ne relit son fichier de clés qu'au démarrage) ; `lancer` rend le processus."""

    def __init__(self, cerveau: Path, lancer, surveille: Path | None = None):
        self.cerveau, self.lancer, self.p, self.surveille = cerveau, lancer, None, surveille
        self.vu = self._etat()

    def _etat(self):
        try:
            s = self.surveille.stat() if self.surveille else None
        except FileNotFoundError:
            return None
        return s and (s.st_mtime_ns, s.st_size, s.st_ino)

    def _mettre_en_place(self) -> None:
        for suffixe in ("-wal", "-shm"):
            Path(f"{self.cerveau}{suffixe}").unlink(missing_ok=True)
        os.replace(pret(self.cerveau), self.cerveau)

    def demarrer(self) -> None:
        if pret(self.cerveau).exists():
            self._mettre_en_place()
        self.p = self.lancer()

    def tour(self) -> int | None:
        """None tant que tout va ; 1 si cortexdb est mort."""
        if self.p.poll() is not None:
            return 1
        if pret(self.cerveau).exists():
            self.arreter()
            self._mettre_en_place()
            self.p = self.lancer()
        elif self._etat() != self.vu:
            self.arreter()
            self.p = self.lancer()
        self.vu = self._etat()
        return None

    def arreter(self) -> None:
        if self.p is not None and self.p.poll() is None:
            self.p.terminate()
            self.p.wait(timeout=cc.CHAUFFE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--port", type=int, default=0, help="port de cortexdb-grpc (défaut : cortexdb_port du registre, sinon libre)")
    ap.add_argument("--cles", type=Path, default=os.environ.get("KG_CORTEXDB_CLES") or None,
                    help="fichier de clés de Thalamus ; l’édition y reçoit sa clé de lecture (défaut : KG_CORTEXDB_CLES)")
    a = ap.parse_args()
    cerveau = cc.chemin_cerveau(a.graphe)
    binaire = os.environ.get("KG_CORTEXDB_BINAIRE") or str(cc.BINAIRE)
    if not (cerveau.exists() or pret(cerveau).exists()):
        print(f"pas de cerveau publié pour {a.graphe} ({cerveau})", file=sys.stderr)
        return 2
    journal = cc.DOSSIER / f"{a.graphe}.serveur.log"
    adresse_p = lc.fichier_adresse(a.graphe)
    from newskit import config_sources as cfg  # noqa: PLC0415
    port = a.port or cfg.port_cortexdb(a.graphe) or cc.port_libre()
    jeton = secrets.token_hex(16)
    with cc.Encodeur(a.graphe, cc.DOSSIER / f"{a.graphe}.encodeur.log") as e:
        def lancer():
            nonlocal jeton
            t0 = time.time()
            if a.cles:
                jeton = assurer_cle(a.cles, a.graphe)
            p = subprocess.Popen([binaire], env=environnement(cerveau, port, jeton, e.url, e.dim, a.cles),
                                 stdout=journal.open("a"), stderr=subprocess.STDOUT)
            cc.attendre_port(port, cc.CHAUFFE)
            print(f"cortexdb {a.graphe} : 127.0.0.1:{port}, {cerveau.stat().st_size / 1e6:.0f} Mo, ouvert en "
                  f"{time.time() - t0:.1f} s", flush=True)
            return p
        g = Gardien(cerveau, lancer, surveille=a.cles)
        fini = []
        signal.signal(signal.SIGTERM, lambda *_: fini.append(0))
        signal.signal(signal.SIGINT, lambda *_: fini.append(0))
        g.demarrer()
        g.vu = g._etat()                         # la clé de l'édition écrite au lancement n'est pas un changement
        ecrire_adresse(adresse_p, f"127.0.0.1:{port}", jeton)
        code = None
        try:
            while not fini and code is None:
                for _ in range(TOUR * 2):          # un signal se voit dans la demi-seconde (le sommeil reprend après lui)
                    if fini:
                        break
                    time.sleep(0.5)
                else:
                    code = g.tour()
        finally:
            adresse_p.unlink(missing_ok=True)
            g.arreter()
        if code:
            print(f"cortexdb {a.graphe} arrêté (code {g.p.poll()}) : voir {journal}", file=sys.stderr)
    return code or 0


if __name__ == "__main__":
    sys.exit(main())

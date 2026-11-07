#!/usr/bin/env python3
"""L'encodeur du moteur derrière une adresse compatible OpenAI (POST /embeddings), pour le bras « vecteurs » du
pilote CortexDB (CHANTIER_PILOTE_CORTEXDB.md) : CortexDB v2.136.0 n'appelle que ce protocole (OPENAI_BASE_URL). Écoute
sur 127.0.0.1 seulement ; même encodeur que l'index du graphe (`synthese.encodeur(graphe)`), aucun appel payant.

    python3 -m newskit.embeddings_locaux --graphe <graphe> --port 47900
    OPENAI_BASE_URL=http://127.0.0.1:47900/v1 CORTEXDB_EMBED_MODEL=local CORTEXDB_EMBED_DIM=<dim> cortexdb-grpc
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path



def textes(corps: dict) -> list[str]:
    x = corps.get("input")
    return [x] if isinstance(x, str) else [str(t) for t in x or []]


def reponse(vecteurs, modele: str) -> dict:
    return {"object": "list", "model": modele,
            "data": [{"object": "embedding", "index": i, "embedding": [float(v) for v in vec]} for i, vec in enumerate(vecteurs)]}


def main() -> int:
    from newskit import synthese as sy  # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--port", type=int, default=47900)
    a = ap.parse_args()
    enc = sy.encodeur(a.graphe)
    dim = len(next(iter(enc.embed(["dimension"]))))
    modele = f"local:{type(enc).__name__}"
    print(f"encodeur {modele}, dimension {dim}, http://127.0.0.1:{a.port}/v1", flush=True)

    class Service(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if not self.path.rstrip("/").endswith("/embeddings"):
                self.send_error(404); return
            corps = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            sortie = json.dumps(reponse(list(enc.embed(textes(corps))), modele)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(sortie)))
            self.end_headers()
            self.wfile.write(sortie)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", a.port), Service).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

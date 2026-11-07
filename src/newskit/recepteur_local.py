#!/usr/bin/env python3
"""Récepteur HTTP local : le navigateur (session connectée) POSTe le texte des articles ici.
POST /save  {"path": "medium/xxx.md", "content": "..."} -> écrit sources/<path> (chemin confiné à sources/).
Lancement : python3 -m newskit.recepteur_local [port=8765]"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from newskit.commun import RACINE  # noqa: E402 — la racine des données (N1)
ROOT = (RACINE / "sources").resolve()
class H(BaseHTTPRequestHandler):
    timeout = 20
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "content-type"); self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS"); self.send_header("Access-Control-Allow-Private-Network", "true")
    def do_OPTIONS(self): self.send_response(204); self._cors(); self.end_headers()
    def do_GET(self): self.send_response(200); self._cors(); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        p = (ROOT / d["path"]).resolve()
        if ROOT not in p.parents: self.send_response(400); self._cors(); self.end_headers(); return
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(d["content"], encoding="utf-8")
        self.send_response(200); self._cors(); self.end_headers(); self.wfile.write(str(len(d["content"])).encode())
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), H).serve_forever()

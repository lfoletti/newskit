"""La passerelle Thalamus répond en JSON ou en flux d'événements (SSE) : le format se lit à l'en-tête Content-Type, pas
au contenu. 17.11.2026 : une réponse JSON dont un extrait contenait « data: » (« personal data: … ») était lue comme un
flux, rendue vide, et la station du site renvoyait 503 sur les notions les plus reliées (Amazon, European Commission)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from newskit import interroger

REPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"content": [{"type": "text", "text": "special categories of personal data: art. 9"}]},
}


def serveur(type_contenu: str, corps: str):
    class Gestion(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            donnees = corps.encode()
            self.send_response(200)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Mcp-Session-Id", "s1")
            self.send_header("Content-Length", str(len(donnees)))
            self.end_headers()
            self.wfile.write(donnees)

        def log_message(self, *a):
            pass

    s = HTTPServer(("127.0.0.1", 0), Gestion)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.mark.parametrize(
    "type_contenu, corps",
    [
        ("application/json", json.dumps(REPONSE)),
        ("text/event-stream", "event: message\ndata: " + json.dumps(REPONSE) + "\n\n"),
    ],
)
def test_la_reponse_se_lit_selon_son_type_pas_selon_son_contenu(type_contenu, corps):
    s = serveur(type_contenu, corps)
    try:
        p = interroger.Passerelle(f"http://127.0.0.1:{s.server_port}", "cle")
        p._session = "s0"
        session, r = p._poster({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    finally:
        s.shutdown()
    assert session == "s1"
    assert r == REPONSE


def test_un_flux_sans_donnees_rend_none():
    s = serveur("text/event-stream; charset=utf-8", "event: ping\n\n")
    try:
        _, r = interroger.Passerelle(f"http://127.0.0.1:{s.server_port}", "cle")._poster({"jsonrpc": "2.0"})
    finally:
        s.shutdown()
    assert r is None

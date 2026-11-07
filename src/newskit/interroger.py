#!/usr/bin/env python3
"""La station « Interroger le graphe » : une question, ses sources dans le graphe du site, sans modèle (mainteneur, 06.11.2026 :
« le dialogue doit être très réactif et correctement sourcé. Idéalement, se passer d'un LLM », le graphe du site seul, « les réponses obtenues par le site doivent être de la même qualité que les résultats
du bench »).

Une seule fonction, `repondre`, sert le site et le banc (deploiement/accueil/banc_interroger.py) : le banc mesure donc
ce que le site rend. Elle ne fait rien de plus que le rappel qu'un agent ferait par MCP (mainteneur, 06.11.2026 : « je
n'aimerais pas que le site fonctionne mieux que le mcp ») : un appel à th_recall sur le graphe du site seul, ses résultats
dans leur ordre. Ce qui améliore la recherche va au graphe ou à Thalamus, et sert tout le monde. Une panne de la
passerelle ou d'un cerveau rend « indisponible », jamais une réponse moindre donnée pour valable.

    python3 -m newskit.interroger [--passerelle http://127.0.0.1:8787] [--cle <fichier de la clé>] "question"

La clé se lit dans un fichier (installer_vps.sh la range pour le compte « site »), jamais sur la ligne de commande."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

SOURCES = 5  # sources rendues, au plus
MOTS_EXTRAIT = 60  # mots de l'extrait d'une source
QUESTION_MAX = 500  # caractères d'une question
DELAI = 30  # secondes accordées à la passerelle


def cerveau() -> str:
    """Le cerveau du graphe du site dans Thalamus (config_sources.cerveau_thalamus, bloc `edition:`)."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    return cfg.cerveau_thalamus(cfg.graphe_du_site())


def _extrait(texte: str, mots: int = MOTS_EXTRAIT) -> str:
    return " ".join((texte or "").split()[:mots])


def repondre(question: str, appeler) -> dict:
    """La réponse de la station : {question, sources: [{titre, url, extrait, id}], erreur?}. `appeler(outil, args)`
    parle à la passerelle (Passerelle, ou un faux en test)."""
    question = (question or "").strip()
    if not question or len(question) > QUESTION_MAX:
        return {"question": question, "sources": [], "erreur": "question"}
    try:
        r = appeler("th_recall", {"query": question, "brains": [cerveau()], "top_k": SOURCES, "syntheses": 0})
    except Exception:  # noqa: BLE001 — toute panne se dit telle
        return {"question": question, "sources": [], "erreur": "indisponible"}
    sources = [
        {
            "titre": h.get("title", ""),
            "url": h.get("source_url", ""),
            "id": h.get("knowledge_id"),
            "extrait": _extrait(h.get("snippet", "")),
        }
        for h in (r.get("results") or [])[:SOURCES]
    ]
    return {"question": question, "sources": sources}


class Passerelle:
    """La passerelle Thalamus par MCP en HTTP, avec la clé du compte « site »."""

    def __init__(self, url: str, cle: str, delai: float = DELAI):
        self.url, self.cle, self.delai, self._session = url.rstrip("/"), cle, delai, None

    @classmethod
    def du_fichier(cls, url: str, fichier: Path, delai: float = DELAI) -> "Passerelle":
        return cls(url, Path(fichier).read_text(encoding="utf-8").strip(), delai)

    def __repr__(self) -> str:
        return f"Passerelle({self.url!r})"

    def _poster(self, message: dict):
        entetes = {
            "Authorization": f"Bearer {self.cle}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session:
            entetes["Mcp-Session-Id"] = self._session
        r = urllib.request.urlopen(
            urllib.request.Request(self.url + "/mcp", json.dumps(message).encode(), entetes), timeout=self.delai
        )
        texte = r.read().decode()
        if "data:" in texte:
            lignes = [x[5:].strip() for x in texte.splitlines() if x.startswith("data:")]
            texte = lignes[-1] if lignes else ""
        return r.headers.get("Mcp-Session-Id"), (json.loads(texte) if texte.strip() else None)

    def _ouvrir(self) -> None:
        self._session, _ = self._poster(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "interroger", "version": "1"},
                },
            }
        )
        self._poster({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def __call__(self, outil: str, args: dict) -> dict:
        if not self._session:
            self._ouvrir()
        _, r = self._poster(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": outil, "arguments": args}}
        )
        res = (r or {}).get("result") or {}
        texte = ((res.get("content") or [{}])[0]).get("text", "")
        if res.get("isError") or "error" in (r or {}):
            raise RuntimeError(texte[:200] or "erreur de la passerelle")
        return json.loads(texte)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--passerelle", default="http://127.0.0.1:8787")
    p.add_argument(
        "--cle",
        type=Path,
        default=Path(os.environ.get("NEWSKIT_CLE_INTERROGER", "cle")),
        help="fichier de la clé de lecture (défaut : NEWSKIT_CLE_INTERROGER, sinon ./cle)",
    )
    p.add_argument("question")
    a = p.parse_args(argv)
    print(json.dumps(repondre(a.question, Passerelle.du_fichier(a.passerelle, a.cle)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

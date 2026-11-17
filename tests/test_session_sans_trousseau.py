"""Au VPS (Linux), la commande `security` du trousseau macOS n'existe pas. 17.11.2026 : `etat_session` levait
FileNotFoundError, et le rapport de routine ne partait pas, alors que `SMTP_MOT_DE_PASSE` était dans courriel.env."""

from __future__ import annotations

import subprocess

from newskit import config_sources as cfg


def test_sans_trousseau_la_session_est_absente(monkeypatch):
    def sans_security(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "security")

    monkeypatch.setattr(cfg, "_session_en_fichier", lambda nom: False)
    monkeypatch.setattr(cfg, "_service", lambda nom: ("compte", "service"))
    monkeypatch.setattr(subprocess, "run", sans_security)
    assert cfg.etat_session("courriel_compte") == "absente"

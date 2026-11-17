"""La copie de travail d'un passage en place fait remplir à CortexDB ses index CJK. `retirer_cjk` les retire du cerveau
publié ; CortexDB les recrée vides à l'ouverture et ne les remplit pas, car `PRAGMA user_version` dit qu'il l'a déjà fait.
Le déclencheur d'une réécriture efface alors d'un index vide : « database disk image is malformed » au premier document
remplacé (17.11.2026, sur le Mac et au VPS). La copie repart de user_version 0 ; le cerveau publié garde le sien."""

from __future__ import annotations

import sqlite3

from newskit import cerveau_cortexdb as cc


def version(chemin) -> int:
    c = sqlite3.connect(chemin)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def test_la_copie_de_travail_fait_remplir_les_index_cjk(tmp_path):
    source, copie = tmp_path / "g.db", tmp_path / "g.neuf.db"
    c = sqlite3.connect(source)
    c.execute("CREATE TABLE embeddings (id TEXT, content TEXT)")
    c.execute("PRAGMA user_version = 3")
    c.commit()
    c.close()
    cc.cloner(source, copie)
    assert version(copie) == 0
    assert version(source) == 3

"""Le champ author ne porte qu'un nom : ni adresse web, ni courriel, ni balise. Les courriels vont au fichier de contacts."""

import json

import pytest

from newskit import collecte_rss as rss
from newskit import extraire_squelette as sq
from newskit import fiche
from newskit import migrer_frontmatter as mg


@pytest.mark.parametrize(
    ("brut", "nom", "courriels"),
    [
        (
            "Georgia Jenkinshttp://www.blogger.com/profile/10796829969295635922noreply@blogger.com",
            "Georgia Jenkins",
            [],
        ),
        ("matt.mullen@futurenet.com (Matt Mullen)", "Matt Mullen", ["matt.mullen@futurenet.com"]),
        ("noreply@blogger.com (Cécile Lamborot)", "Cécile Lamborot", []),
        ("info@thehackernews.com (The Hacker News)", "The Hacker News", ["info@thehackernews.com"]),
        ("Jane Doe <jane@example.org>", "Jane Doe", ["jane@example.org"]),
        ("lamia.fadouach@cnm.fr", "", ["lamia.fadouach@cnm.fr"]),
        ("Olivier Ezratty http://www.oezratty.net", "Olivier Ezratty", []),
        ("Clyde &amp; Co LLP", "Clyde & Co LLP", []),
        ("Cynyoung Park&nbsp;and&nbsp;Kwanho Shin", "Cynyoung Park and Kwanho Shin", []),
        ('<a href="https://x.org/o" hreflang="und">Bradley Arant LLP</a>', "Bradley Arant LLP", []),
        ("Ada (Editor) Lovelace", "Ada (Editor) Lovelace", []),
        ("Tivadar Danka, Alejandro Piad", "Tivadar Danka, Alejandro Piad", []),
        ("no-reply@revue.org (Bob)", "Bob", []),
        ('"Johanna Ratliff"', "Johanna Ratliff", []),
        ("['Marc  Wiggers', 'Sophia D\\'Antoine']", "Marc Wiggers, Sophia D'Antoine", []),
        ("[pas une liste", "pas une liste", []),
        ("[pas, une liste]", "pas, une liste", []),
        ("", "", []),
    ],
)
def test_separer_auteur(brut, nom, courriels):
    assert fiche.separer_auteur(brut) == (nom, courriels)
    assert fiche.separer_auteur(nom)[0] == nom  # idempotente


def test_normaliser_nettoie_author():
    assert fiche.normaliser({"author": "a@b.org (Ada)", "url": "https://x.org/a"})["author"] == "Ada"


def test_normaliser_sans_author_le_laisse_vide():
    assert fiche.normaliser({"url": "https://x.org/a"})["author"] == ""


def champs(auteur):
    return {
        "title": "T",
        "subtitle": "",
        "author": auteur,
        "date": "2026-11-08",
        "url": "https://x.org/a",
        "plateforme": "Flux RSS",
        "publication": "P",
        "complet": True,
        "collected": "2026-11-08",
    }


def test_ecrire_garde_le_courriel_hors_de_la_fiche(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    f = tmp_path / "a.md"
    fiche.ecrire(f, champs("ada@revue.org (Ada)"), "corps " * 50, strict=False, hors_graphe=True)
    assert "@" not in f.read_text(encoding="utf-8")
    assert fiche.lire_fichier(f)[0]["author"] == "Ada"
    lignes = (tmp_path / "cache" / "contacts_auteurs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lignes] == [
        {"courriel": "ada@revue.org", "nom": "Ada", "type": "auteur", "url": "https://x.org/a"}
    ]


def test_un_contact_connu_n_est_pas_repete(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    for _ in range(2):
        fiche.noter_contacts("ada@revue.org (Ada)", "https://x.org/a")
    assert len((tmp_path / "cache" / "contacts_auteurs.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_sans_courriel_aucun_fichier(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    fiche.noter_contacts("Ada", "https://x.org/a")
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize(
    ("courriel", "genre"),
    [
        ("info@thehackernews.com", "redaction"),
        ("editors@tomshardware.com", "redaction"),
        ("joe.foley@futurenet.com", "auteur"),
    ],
)
def test_type_du_courriel(courriel, genre):
    assert fiche.type_de_courriel(courriel) == genre


ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>t</title><link rel="alternate" href="https://x.org/a"/>
<author><name>Georgia Jenkins</name><uri>https://www.blogger.com/profile/1</uri><email>noreply@blogger.com</email></author>
</entry><entry><title>u</title><link rel="alternate" href="https://x.org/b"/>
<author><name>Jane Doe</name><email>jane@x.org</email></author></entry></feed>"""


def test_atom_prend_le_nom_sans_coller_uri_et_courriel():
    a, b = rss.entrees(ATOM)
    assert fiche.separer_auteur(a["auteur"]) == ("Georgia Jenkins", [])
    assert fiche.separer_auteur(b["auteur"]) == ("Jane Doe", ["jane@x.org"])


def test_squelette_nettoie_et_ne_coupe_pas_un_cabinet():
    assert sq.normaliser_auteurs("Clyde &amp; Co LLP") == ["Clyde & Co LLP"]
    assert sq.normaliser_auteurs("a@b.org (Ada Lovelace)") == ["Ada Lovelace"]
    assert sq.normaliser_auteurs("Ada & Bob") == ["Ada", "Bob"]
    assert sq.normaliser_auteurs("['Marc Wiggers', 'Robert Fröger']") == ["Marc Wiggers", "Robert Fröger"]


def test_la_migration_repare_le_champ_et_garde_le_courriel(tmp_path, monkeypatch):
    monkeypatch.setattr(fiche, "racine_des_donnees", lambda: tmp_path)
    monkeypatch.setattr(mg, "RACINE", tmp_path)
    monkeypatch.setattr(mg, "DOSSIERS", ["sources"])
    monkeypatch.setattr("sys.argv", ["migrer"])
    f = tmp_path / "sources" / "a.md"
    f.parent.mkdir()
    c = {**fiche.normaliser(champs("Ada"), "corps"), "author": "ada@revue.org (Ada)"}
    f.write_text(fiche.rendre(c, "corps"), encoding="utf-8")
    assert mg.main() == 0
    assert fiche.lire_fichier(f)[0]["author"] == "Ada"
    assert fiche.lire_fichier(f)[1].strip() == "corps"
    assert "ada@revue.org" in (tmp_path / "cache" / "contacts_auteurs.jsonl").read_text(encoding="utf-8")

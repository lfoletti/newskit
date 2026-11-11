"""La lecture audio des billets (mainteneur, 11.11.2026 : voix « K36-93 » retenue ; « la création des audios pourrait être
un processus parallèle indépendant » ; « la génération audio prend toujours le billet le plus récent et va descendant
dans le fil »).

Le processus lit la copie publique du fil sur le site, convertit les billets sans son à jour, du plus récent au plus
ancien. Il dépose les sons puis leur index. Kyutai, ffmpeg, le réseau et ssh sont remplacés par des doubles.
"""

from __future__ import annotations

import io
import json
import py_compile
from pathlib import Path

import pytest
import yaml

from newskit import lecture_audio as la
from newskit import media

R = {"modele": "kyutai/tts-1.6b-en_fr", "voix": "cml-tts/fr/928_486_000075-0001_enhanced.wav", "vitesse": 0.93}


def _b(u, ajoute, **k):
    return {
        "url": u,
        "ajoute": ajoute,
        "titre": "Titre " + u,
        "descriptif": "Texte " + u,
        "resume": "",
        "elements": [],
        **k,
    }


# 1. Les réglages du média


def _config(tmp_path, valeur):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "media": {"nom": "media"}, "lecture_audio": valeur}), encoding="utf-8")
    return p


def test_le_config_admet_la_lecture_audio(tmp_path):
    assert (
        media.charger(_config(tmp_path, {**R, "sigles": {"DNS": "Domain Name System"}}))["lecture_audio"]["vitesse"]
        == 0.93
    )


@pytest.mark.parametrize(
    "valeur",
    [
        {**R, "vitesse": 0.5},
        {**R, "vitesse": 1.5},
        {**R, "voix": ""},
        {**R, "modele": 3},
        {**R, "sigles": ["DNS"]},
        "oui",
        {**R, "inconnu": 1},
    ],
)
def test_le_config_refuse_une_lecture_audio_mal_formee(tmp_path, valeur):
    with pytest.raises(media.ConfigMediaInvalide, match="lecture_audio"):
        media.charger(_config(tmp_path, valeur))


# 2. Le texte lu et son empreinte


def test_le_texte_lu_prend_titre_resume_et_elements_sans_intertitres():
    b = _b(
        "u",
        "x",
        resume="Le résumé.",
        elements=[{"champ": "Contexte", "texte": "Premier."}, {"champ": "Suite", "texte": "Second."}],
    )
    t = la.texte_lu(b)
    assert t == "Titre u.\n\nLe résumé.\n\nPremier.\n\nSecond."
    assert "Contexte" not in t and "Texte u" not in t


def test_sans_resume_le_descriptif():
    assert la.texte_lu(_b("u", "x")) == "Titre u.\n\nTexte u"


def test_le_texte_lu_remplace_les_signes():
    b = _b("u", "x", titre="Bain & Company : un chiffre !", descriptif="Le Domain Name System (DNS), un annuaire.")
    t = la.texte_lu(b)
    assert t.startswith("Bain et Company : un chiffre !")
    assert "Domain Name System, DNS, un annuaire." in t
    assert "(" not in t and " " not in t and " " not in t


def test_un_sigle_se_developpe_a_sa_premiere_apparition_seulement():
    b = _b("u", "x", titre="Le DNS change", descriptif="Le DNS et encore le DNS.")
    t = la.texte_lu(b, {"DNS": "Domain Name System"})
    assert t.count("Domain Name System") == 1 and t.count("DNS") == 2
    deja = _b("u", "x", titre="Le Domain Name System", descriptif="Le DNS.")
    assert la.texte_lu(deja, {"DNS": "Domain Name System"}).count("Domain Name System") == 1


def test_l_empreinte_suit_le_texte_la_voix_la_vitesse_et_le_modele():
    e = la.empreinte("texte", R)
    assert e == la.empreinte("texte", dict(R)) and len(e) == 16
    for autre in ({**R, "voix": "x"}, {**R, "vitesse": 0.9}, {**R, "modele": "y"}):
        assert la.empreinte("texte", autre) != e
    assert la.empreinte("texte.", R) != e


# 3. La file : du plus récent au plus ancien, sans reprendre un billet à jour


def test_la_file_part_du_plus_recent():
    billets = [_b("a", "2026-11-11 10:00"), _b("c", "2026-11-11 12:00"), _b("b", "2026-11-11 11:00")]
    assert [b["url"] for b, _, _ in la.a_convertir(billets, {}, R)] == ["c", "b", "a"]


def test_un_billet_a_jour_n_est_pas_repris_un_billet_revise_l_est():
    a, b = _b("a", "1"), _b("b", "2")
    index = {"a": la.empreinte(la.texte_lu(a), R), "b": "ancienne"}
    assert [x["url"] for x, _, _ in la.a_convertir([a, b], index, R)] == ["b"]


# 4. Le passage


class Lecteur:
    def __init__(self, echecs=()):
        self.lus, self.echecs = [], set(echecs)

    def lire(self, texte, wav):
        self.lus.append(texte)
        assert len(self.lus) < 20, "le passage boucle"
        if any(e in texte for e in self.echecs):
            return False
        Path(wav).write_bytes(b"wav")
        return True

    def fermer(self):
        pass


@pytest.fixture
def monde(tmp_path, monkeypatch):
    copies = [[_b("a", "2026-11-11 10:00"), _b("b", "2026-11-11 11:00")]]
    depots = []
    monkeypatch.setattr(la, "CACHE", tmp_path / "audio")
    monkeypatch.setattr(la, "reglages", lambda c=None: R)
    monkeypatch.setattr(la, "lire_copie", lambda url: copies[0] if len(copies) == 1 else copies.pop(0))
    monkeypatch.setattr(la, "url_copie", lambda: "https://site/fil.json")

    def encoder(wav, base, vitesse):
        for ext in la.FORMATS:
            Path(str(base) + ext).write_bytes(b"son")
        return True

    monkeypatch.setattr(la, "encoder", encoder)
    monkeypatch.setattr(
        la, "deposer", lambda dossier, nouveaux, index: depots.append((list(nouveaux), dict(index))) or "déposé"
    )
    return copies, depots, tmp_path


def test_un_passage_convertit_du_plus_recent_au_plus_ancien_et_depose_apres_chaque_billet(monde):
    _, depots, tmp = monde
    lec = Lecteur()
    assert la.passer(lecteur=lec) == 2
    assert [t.split(".")[0] for t in lec.lus] == ["Titre b", "Titre a"]
    assert len(depots) == 2 and list(depots[0][1]) == ["b"] and set(depots[1][1]) == {"a", "b"}
    assert json.loads((tmp / "audio" / "audio.json").read_text())["billets"] == depots[1][1]
    assert la.passer(lecteur=Lecteur()) == 0  # tout est à jour


def test_un_billet_entre_pendant_le_passage_est_le_suivant_converti(monde):
    copies, _, _ = monde
    copies[:] = [
        [_b("a", "2026-11-11 10:00"), _b("b", "2026-11-11 11:00")],
        [_b("a", "2026-11-11 10:00"), _b("b", "2026-11-11 11:00"), _b("n", "2026-11-11 12:00")],
    ]
    lec = Lecteur()
    la.passer(lecteur=lec)
    assert [t.split(".")[0] for t in lec.lus] == ["Titre b", "Titre n", "Titre a"]


def test_l_index_ne_nomme_que_des_sons_deposes_et_les_nouveaux_partent_avant(monde):
    _, depots, _ = monde
    la.passer(lecteur=Lecteur())
    nouveaux, index = depots[0]
    assert sorted(nouveaux) == sorted(index["b"] + ext for ext in la.FORMATS)


def test_un_echec_de_kyutai_laisse_le_billet_dans_la_file(monde):
    _, depots, _ = monde
    lec = Lecteur(echecs={"Titre b"})
    assert la.passer(lecteur=lec) == 1
    assert [t.split(".")[0] for t in lec.lus] == ["Titre b", "Titre a"]  # pas de boucle sur l'échec
    assert list(depots[-1][1]) == ["a"]
    lec2 = Lecteur()
    assert la.passer(lecteur=lec2) == 1 and lec2.lus[0].startswith("Titre b")  # repris au passage suivant


def test_un_billet_sorti_de_la_copie_sort_de_l_index(monde):
    copies, depots, _ = monde
    la.passer(lecteur=Lecteur())
    copies[:] = [[_b("b", "2026-11-11 11:00")]]
    la.passer(lecteur=Lecteur())
    assert list(depots[-1][1]) == ["b"]


def test_la_duree_max_arrete_le_passage(monde):
    temps = iter([0.0, 0.0, la.DUREE_MAX + 1, la.DUREE_MAX + 1])
    lec = Lecteur()
    assert la.passer(lecteur=lec, horloge=lambda: next(temps)) == 1


def test_une_copie_illisible_n_arrete_rien(monde, monkeypatch):
    monkeypatch.setattr(la, "lire_copie", lambda url: None)
    assert la.passer(lecteur=Lecteur()) == 0


def test_sans_reglage_pas_de_passage(monde, monkeypatch):
    monkeypatch.setattr(la, "reglages", lambda c=None: None)
    assert la.passer(lecteur=Lecteur()) == 0


def test_le_verrou_arrete_un_second_passage(monde):
    with la.verrou() as pris:
        assert pris
        assert la.passer(lecteur=Lecteur()) == 0
        with la.verrou() as second:
            assert not second


# 5. Le dépôt et l'encodage


def test_le_depot_place_les_sons_avant_l_index_et_retire_les_orphelins(tmp_path, monkeypatch):
    vus = {}
    monkeypatch.setattr(la.publier_fil, "acces", lambda env: {"VPS_CLE": "k", "VPS_UTILISATEUR": "u", "VPS_HOTE": "h"})

    def run(cmd, input=None, **k):
        vus["cmd"], vus["paquet"] = cmd[-1], input
        return type("R", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(la.subprocess, "run", run)
    (tmp_path / "e1.webm").write_bytes(b"x")
    (tmp_path / "e1.mp4").write_bytes(b"x")
    assert la.deposer(tmp_path, ["e1.webm", "e1.mp4"], {"u1": "e1"}) == "déposé (1 son(s) neuf(s), 1 billet(s))"
    c = vus["cmd"]
    assert c.index("mv -f .neuf/e1.webm") < c.index("mv -f .neuf/audio.json")
    assert "e1.webm" in c.split("garde=")[1] and "rm -f" in c


def test_le_depot_sans_acces_ne_fait_rien(tmp_path, monkeypatch):
    monkeypatch.setattr(la.publier_fil, "acces", lambda env: None)
    assert la.deposer(tmp_path, [], {}).startswith("sans accès")


def test_l_encodage_ralentit_puis_ecrit_opus_et_aac(tmp_path, monkeypatch):
    cmds = []
    monkeypatch.setattr(la.subprocess, "run", lambda cmd, **k: cmds.append(cmd) or type("R", (), {"returncode": 0})())
    assert la.encoder(tmp_path / "x.wav", tmp_path / "e", 0.93)
    assert all("atempo=0.93" in c for c in cmds) and len(cmds) == 2
    assert any("libopus" in c for c in cmds) and any(str(tmp_path / "e.mp4") in c for c in cmds)


def test_le_lecteur_kyutai_se_compile_sans_newskit():
    src = Path(la.__file__).with_name("kyutai_lecteur.py")
    py_compile.compile(str(src), doraise=True)
    assert "newskit" not in src.read_text(encoding="utf-8").split('"""', 2)[2]


# 6. Le lecteur, la copie publique, les échecs


FAUX_LECTEUR = """
import json, sys
for ligne in sys.stdin:
    d = json.loads(ligne)
    if "panne" in d["texte"]:
        print(json.dumps({"ok": False, "erreur": "panne"}), flush=True)
        continue
    open(d["sortie"], "wb").write(b"RIFF")
    print(json.dumps({"ok": True}), flush=True)
"""


def test_le_lecteur_garde_un_processus_et_lit_texte_par_texte(tmp_path, monkeypatch):
    script = tmp_path / "lecteur.py"
    script.write_text(FAUX_LECTEUR, encoding="utf-8")
    monkeypatch.setattr(la, "LECTEUR", script)
    monkeypatch.setenv("NEWSKIT_AUDIO_PYTHON", __import__("sys").executable)
    lec = la.Lecteur(R)
    assert lec.lire("un", tmp_path / "1.wav") and lec.lire("deux", tmp_path / "2.wav")
    assert not lec.lire("panne", tmp_path / "3.wav")
    lec.fermer()
    assert lec.p.returncode == 0
    assert not lec.lire("après", tmp_path / "4.wav")  # processus fermé : échec, pas d'exception


def test_sans_interpreteur_nomme_pas_de_lecteur(monkeypatch):
    monkeypatch.delenv("NEWSKIT_AUDIO_PYTHON", raising=False)
    with pytest.raises(RuntimeError, match="NEWSKIT_AUDIO_PYTHON"):
        la.Lecteur(R)


def test_un_modele_indisponible_arrete_le_passage_sans_erreur(monde, monkeypatch):
    monkeypatch.delenv("NEWSKIT_AUDIO_PYTHON", raising=False)
    assert la.passer() == 0


def test_un_depot_en_echec_arrete_le_passage_et_garde_l_index(monde, monkeypatch):
    _, _, tmp = monde
    monkeypatch.setattr(la, "deposer", lambda *a: "échec du dépôt (ssh 255) : x")
    assert la.passer(lecteur=Lecteur()) == 0
    assert not (tmp / "audio" / "audio.json").exists()


def test_un_depot_ssh_en_echec_se_dit(tmp_path, monkeypatch):
    monkeypatch.setattr(la.publier_fil, "acces", lambda env: {"VPS_CLE": "k", "VPS_UTILISATEUR": "u", "VPS_HOTE": "h"})
    monkeypatch.setattr(la.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 255, "stderr": b"refus"})())
    assert la.deposer(tmp_path, [], {}) == "échec du dépôt (ssh 255) : refus"


def test_la_copie_publique_se_lit_sur_le_site(monkeypatch):
    class Rep(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(la.urllib.request, "urlopen", lambda req, timeout: Rep(b'{"billets": [{"url": "u"}]}'))
    assert la.lire_copie("https://site/fil.json") == [{"url": "u"}]

    def panne(req, timeout):
        raise OSError("hors ligne")

    monkeypatch.setattr(la.urllib.request, "urlopen", panne)
    assert la.lire_copie("https://site/fil.json") is None


def test_l_adresse_de_la_copie_et_les_reglages_viennent_du_media():
    c = {"media": {"lettre_site": "https://governance.news/"}, "lecture_audio": R}
    assert la.url_copie(c) == "https://governance.news/fil.json"
    assert la.url_copie({"media": {"site": "https://a.b"}}) == "https://a.b/fil.json"
    assert la.reglages(c) == R and la.reglages({}) is None


def test_le_cache_ne_garde_que_les_sons_de_l_index(monde):
    _, _, tmp = monde
    (tmp / "audio").mkdir(parents=True, exist_ok=True)
    (tmp / "audio" / "vieux.webm").write_bytes(b"x")
    la.passer(lecteur=Lecteur())
    assert not (tmp / "audio" / "vieux.webm").exists()
    assert len(list((tmp / "audio").glob("*.webm"))) == 2


def test_la_commande_affiche_le_nombre_de_billets(monkeypatch, capsys):
    monkeypatch.setattr(la, "passer", lambda: 3)
    assert la.main([]) == 0 and "3 billet(s) converti(s)" in capsys.readouterr().out


def test_un_passage_lance_puis_ferme_son_lecteur(monde, tmp_path, monkeypatch):
    script = tmp_path / "lecteur.py"
    script.write_text(FAUX_LECTEUR, encoding="utf-8")
    monkeypatch.setattr(la, "LECTEUR", script)
    monkeypatch.setenv("NEWSKIT_AUDIO_PYTHON", __import__("sys").executable)
    fermes = []
    monkeypatch.setattr(la.Lecteur, "fermer", lambda self: fermes.append(self) or la.Lecteur.__dict__)
    assert la.passer() == 2 and len(fermes) == 1


def test_un_lecteur_qui_ne_s_arrete_pas_est_tue(tmp_path, monkeypatch):
    script = tmp_path / "lecteur.py"
    script.write_text(FAUX_LECTEUR, encoding="utf-8")
    monkeypatch.setattr(la, "LECTEUR", script)
    monkeypatch.setenv("NEWSKIT_AUDIO_PYTHON", __import__("sys").executable)
    lec = la.Lecteur(R)
    tues = []

    def attendre(timeout):
        raise la.subprocess.TimeoutExpired("lecteur", timeout)

    monkeypatch.setattr(lec.p, "wait", attendre)
    monkeypatch.setattr(lec.p, "kill", lambda: tues.append(True) or lec.p.terminate())
    lec.fermer()
    assert tues == [True]


def test_un_lecteur_bloque_est_arrete_au_bout_du_temps_permis(tmp_path, monkeypatch):
    script = tmp_path / "lecteur.py"
    script.write_text("import sys, time\nfor ligne in sys.stdin:\n    time.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(la, "LECTEUR", script)
    monkeypatch.setattr(la, "LIRE_MAX", 0.5)
    monkeypatch.setenv("NEWSKIT_AUDIO_PYTHON", __import__("sys").executable)
    lec = la.Lecteur(R)
    assert not lec.lire("texte", tmp_path / "x.wav")
    assert lec.p.wait(timeout=5) != 0  # tué

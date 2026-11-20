"""Les passes d'un procédé par les lots de l'API d'Anthropic (P2b du portage du procédé des papiers, mainteneur, 15 et
19.11.2026 : « on va aller avec les lots »).

Un lot porte les passes de plusieurs fiches ; il coûte moitié prix et rend ses résultats plus tard. Le texte de la
fiche est le premier bloc de chaque requête, marqué pour le cache : les passes d'une même fiche portent le même bloc,
mot pour mot. `soumettre` envoie un lot, `relever` le relit quand il est fini. Les réponses se gardent, se refusent
et achèvent la fiche comme par `claude -p`. Un tri passe dans un lot ; la lecture entière qu'il retient passe dans le
lot suivant."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from newskit import couts, fiche
from newskit import procedes_lots as pl
from newskit import procedes_passes as pp
from tests.test_procedes_formes import COMPOSANTS, ETAPES, LECTURE, Claude, declaration, media, papier  # noqa: F401


class API:
    """Une fausse API des lots : garde les lots soumis, rend un état et des résultats donnés."""

    def __init__(self):
        self.soumis, self.etat, self.resultats, self.appels = [], "in_progress", {}, []

    def __call__(self, methode, url, corps=None):
        self.appels.append((methode, url))
        if methode == "POST":
            self.soumis.append(corps)
            return json.dumps({"id": f"msgbatch_{len(self.soumis)}", "processing_status": "in_progress"}).encode()
        if url.endswith("/results"):
            return "\n".join(json.dumps(r) for r in self.resultats.values()).encode()
        n = {"processing": 1, "succeeded": len(self.resultats), "errored": 0, "canceled": 0, "expired": 0}
        rendu = {"id": url.rsplit("/", 1)[1], "processing_status": self.etat, "request_counts": n}
        return json.dumps(rendu | ({"results_url": url + "/results"} if self.etat == "ended" else {})).encode()

    def repondre(self, cid, texte=None, stop="end_turn", type_="succeeded"):
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "output_tokens": 50,
        }
        msg = {
            "content": [{"type": "thinking", "thinking": "…"}, {"type": "text", "text": texte or ""}],
            "stop_reason": stop,
            "usage": usage,
        }
        self.resultats[cid] = {"custom_id": cid, "result": {"type": type_, "message": msg} if type_ == "succeeded" else {"type": type_, "error": {"type": "x"}}}  # fmt: skip


@pytest.fixture
def api(media, monkeypatch) -> API:  # noqa: F811
    a = API()
    monkeypatch.setattr(pl, "_http", a)
    monkeypatch.setattr(pp.ec, "fiches_lues", lambda g=None: sorted((media / "sources").rglob("*.md")))
    lignes = []

    def enregistrer(*x, **k):
        lignes.append((x, k))
        return k.get("env") or json.loads(x[3].stdout or "{}")

    monkeypatch.setattr(couts, "enregistrer", enregistrer)
    a.couts = lignes
    return a


def requetes(api: API, n: int = 0) -> dict[str, dict]:
    return {r["custom_id"]: r["params"] for r in api.soumis[n]["requests"]}


def cid(api: API, passe: str, tri: bool = False) -> str:
    etat = json.loads(pl.etat_du_lot("papier").read_text(encoding="utf-8"))
    return next(c for c, v in etat["requetes"].items() if v["passe"] == passe and v["tri"] == tri)


def soumettre(media, d=None, **k):  # noqa: F811
    return pl.soumettre("essai", "papier", d or declaration(media), **k)


def relever(media, d=None):  # noqa: F811
    return pl.relever("essai", "papier", d or declaration(media))


# --- la soumission


def test_le_premier_lot_porte_la_lecture_la_forme_et_le_tri_le_texte_en_tete_marque_pour_le_cache(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    r = requetes(api)
    assert len(r) == 3
    blocs = [p["messages"][0]["content"] for p in r.values()]
    lecture, procede = (blocs[i] for i in (0, 1))
    assert lecture[0] == procede[0] and lecture[0]["cache_control"] == {"type": "ephemeral"}
    assert lecture[0]["text"].startswith("<document>\nTitle. Abstract.")
    assert lecture[1] == {"type": "text", "text": "Consigne lecture.\n"} and "cache_control" not in lecture[1]
    tri = blocs[2]
    assert len(tri[0]["text"].split()) <= pp.MOTS_TRI + 3


def test_chaque_requete_a_son_modele_ses_jetons_et_son_schema(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    r = requetes(api)
    lecture, procede, tri = (r[cid(api, "lecture")], r[cid(api, "procede")], r[cid(api, "architecture", True)])
    assert lecture["model"] == pp.media.modele("extraction") and lecture["max_tokens"] == pl.JETONS["sortie"]
    assert "format" not in lecture["output_config"] and lecture["output_config"]["effort"] == pl.EFFORT
    assert procede["output_config"]["format"] == {"type": "json_schema", "schema": pp.pf.SCHEMAS["etapes"]}
    assert procede["max_tokens"] == pl.JETONS["forme"]
    assert tri["model"] == pp.media.modele("lecture") and tri["max_tokens"] == pl.JETONS["tri"]
    assert tri["output_config"]["format"]["schema"] == pp.pf.SCHEMA_TRI
    assert lecture["thinking"] == {"type": "adaptive"}


def test_l_effort_declare_d_une_passe_vaut_pour_sa_requete_pas_pour_son_tri(media, api):  # noqa: F811
    papier(media)
    d = declaration(media)
    d["passes"][1]["effort"] = d["passes"][2]["effort"] = "high"
    soumettre(media, d)
    r = requetes(api)
    assert r[cid(api, "procede")]["output_config"]["effort"] == "high"
    assert r[cid(api, "lecture")]["output_config"]["effort"] == pl.EFFORT
    assert r[cid(api, "architecture", True)]["output_config"]["effort"] == pl.EFFORT


def test_l_identifiant_d_une_requete_suit_le_format_de_l_api(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    import re

    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", c) for c in requetes(api))


def test_un_lot_en_attente_bloque_la_soumission_suivante(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    with pytest.raises(SystemExit, match="attend son relevé"):
        soumettre(media)
    assert len(api.soumis) == 1


def test_a_blanc_rien_ne_part(media, api):  # noqa: F811
    papier(media)
    ligne = soumettre(media, a_blanc=True)
    assert "3 requêtes" in ligne and api.soumis == [] and not pl.etat_du_lot("papier").exists()


def test_sans_rien_a_faire_aucun_lot(media, api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pp.subprocess, "run", Claude(etapes=ETAPES, tri={"retenu": False}))
    p = papier(media)
    pp.traiter("essai", "papier", declaration(media), p)
    assert soumettre(media) == "papier : rien à soumettre" and api.soumis == []


def test_une_fiche_sans_texte_attend(media, api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pp, "texte", lambda *a: None)
    papier(media)
    assert soumettre(media) == "papier : rien à soumettre"


def test_le_plafond_borne_les_fiches(media, api):  # noqa: F811
    papier(media)
    p2 = media / "sources" / "arxiv" / "2018-10-12_1810.99999_x.md"
    fiche.ecrire(
        p2,
        {**fiche.lire_fichier(papier(media))[0], "arxiv_id": "1810.99999", "url": "https://x/2"},
        "# X\n",
        strict=False,
        hors_graphe=True,
    )
    soumettre(media, plafond=1)
    assert (
        len({v["cle"] for v in json.loads(pl.etat_du_lot("papier").read_text(encoding="utf-8"))["requetes"].values()})
        == 1
    )


def test_une_passe_faite_ou_refusee_ne_repart_pas(media, api):  # noqa: F811
    papier(media)
    pp.garder("papier", declaration(media)["passes"][0], "1810.04805", LECTURE)
    pp.noter_manquant("papier", "1810.04805", "procede", pp.REFUS)
    soumettre(media)
    assert [
        v["passe"] for v in json.loads(pl.etat_du_lot("papier").read_text(encoding="utf-8"))["requetes"].values()
    ] == ["architecture"]


def test_un_tri_deja_retenu_envoie_la_lecture_entiere(media, api):  # noqa: F811
    papier(media)
    d = declaration(media)
    pp.garder_tri("papier", d["passes"][2], "1810.04805", {"retenu": True})
    soumettre(media)
    r = requetes(api)[cid(api, "architecture")]
    assert r["output_config"]["format"]["schema"] == pp.pf.SCHEMAS["composants"]
    assert len(r["messages"][0]["content"][0]["text"].split()) > pp.MOTS_TRI


# --- le relevé


def test_un_lot_en_cours_ne_se_releve_pas(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    code, lignes = relever(media)
    assert code == 2 and "in_progress" in lignes[0]
    assert not json.loads(pl.etat_du_lot("papier").read_text(encoding="utf-8")).get("releve")


def test_sans_lot_rien_a_relever(media, api):  # noqa: F811
    assert relever(media) == (0, ["papier : aucun lot à relever"])


def test_le_releve_garde_les_reponses_et_le_tri_puis_le_lot_suivant_porte_la_forme_retenue(media, api):  # noqa: F811
    p = papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), LECTURE)
    api.repondre(cid(api, "procede"), json.dumps(ETAPES))
    api.repondre(cid(api, "architecture", True), json.dumps({"retenu": True}))
    code, lignes = relever(media)
    assert code == 0
    assert pp.sortie("papier", "lecture", "1810.04805").read_text(encoding="utf-8") == LECTURE
    assert json.loads(pp.sortie("papier", "procede", "1810.04805", ".json").read_text(encoding="utf-8")) == ETAPES
    assert pp.tri_retenu("papier", declaration(media)["passes"][2], "1810.04805")
    assert "procede" not in fiche.lire_fichier(p)[0]  # la fiche attend l'architecture
    soumettre(media)
    assert [
        v["passe"] for v in json.loads(pl.etat_du_lot("papier").read_text(encoding="utf-8"))["requetes"].values()
    ] == ["architecture"]
    api.repondre(cid(api, "architecture"), json.dumps(COMPOSANTS))
    code, lignes = relever(media)
    champs, corps = fiche.lire_fichier(p)
    assert champs["procede"] == "papier:lecture" and "## Architectures" in corps and "## Procedures" in corps
    assert any("fiche 1810.04805 : écrite" in l for l in lignes)


def test_un_tri_qui_ecarte_acheve_la_fiche(media, api):  # noqa: F811
    p = papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), LECTURE)
    api.repondre(cid(api, "procede"), json.dumps(ETAPES))
    api.repondre(cid(api, "architecture", True), json.dumps({"retenu": False}))
    relever(media)
    assert fiche.lire_fichier(p)[0]["procede"] == "papier:lecture"


def test_un_refus_note_la_passe_un_echec_la_laisse_en_attente(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), "", stop="refusal")
    api.repondre(cid(api, "procede"), type_="errored")
    api.repondre(cid(api, "architecture", True), "{}", stop="max_tokens")
    code, lignes = relever(media)
    assert set(pp.manquants("papier")["1810.04805"]) == {"lecture"}
    assert not pp.sortie("papier", "procede", "1810.04805", ".json").exists()
    assert any("!! procede" in l and "errored" in l for l in lignes)
    assert any("!! architecture" in l and "max_tokens" in l for l in lignes)
    assert not pp.tri_retenu("papier", declaration(media)["passes"][2], "1810.04805")


def test_une_reponse_rejetee_ne_se_garde_pas(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), "trop court")
    api.repondre(cid(api, "procede"), "pas du JSON")
    api.repondre(cid(api, "architecture", True), "pas du JSON")
    _, lignes = relever(media)
    assert not pp.sortie("papier", "lecture", "1810.04805").exists()
    assert any("!! lecture" in l and "mots" in l for l in lignes)
    assert any("!! procede" in l and "sans JSON" in l for l in lignes)
    assert any("!! architecture" in l and "sans JSON" in l for l in lignes)


def test_le_releve_consigne_chaque_reponse_au_registre_des_couts_a_moitie_prix(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), LECTURE)
    relever(media)
    ((args, k),) = api.couts
    assert args[:3] == (pp.POSTE, "essai", pp.media.modele("extraction"))
    assert k["env"]["usage"]["cache_creation_input_tokens"] == 1000
    entree, sortie_ = pl.PRIX[pp.media.modele("extraction")]
    attendu = (100 * entree + 1000 * entree * couts.PRIX_CACHE_ECRIT + 50 * sortie_) / 1e6 * pl.REMISE
    assert k["env"]["total_cost_usd"] == pytest.approx(attendu)
    assert args[3].returncode == 0 and "lot" in k["note"]


def test_un_echec_se_consigne_en_echec(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), type_="errored")
    relever(media)
    ((args, k),) = api.couts
    assert args[3].returncode == 1


def test_un_modele_sans_prix_connu_se_consigne_a_zero(media, api, monkeypatch):  # noqa: F811
    assert pl.cout({"input_tokens": 10}, "modele-inconnu") == 0.0


def test_un_lot_releve_ne_se_releve_pas_deux_fois(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "lecture"), LECTURE)
    relever(media)
    assert relever(media) == (0, ["papier : aucun lot à relever"])


def test_un_resultat_hors_du_lot_s_ignore(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre("r99999", LECTURE)
    assert relever(media)[0] == 0


# --- le réseau et la clé


def test_la_cle_se_lit_dans_l_environnement_puis_dans_le_fichier_des_secrets(tmp_path, monkeypatch):
    f = tmp_path / "anthropic.env"
    monkeypatch.setattr(pl, "SECRETS", f)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-env")
    assert pl.cle_api() == "cle-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    f.write_text("# x\nexport ANTHROPIC_API_KEY='cle-fichier'\n", encoding="utf-8")
    assert pl.cle_api() == "cle-fichier"
    f.unlink()
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        pl.cle_api()


def test_une_requete_http_porte_la_cle_la_version_et_le_corps(monkeypatch):
    vus = []

    class Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def ouvrir(req, timeout):
        vus.append((req.get_method(), req.full_url, dict(req.header_items()), req.data, timeout))
        return Reponse()

    monkeypatch.setattr(pl, "cle_api", lambda: "k")
    monkeypatch.setattr(pl.urllib.request, "urlopen", ouvrir)
    assert pl._http("POST", pl.API, {"a": 1}) == b"{}"
    pl._http("GET", pl.API + "/x")
    (m, url, h, data, delai), (m2, _, h2, data2, _) = vus
    assert m == "POST" and url == pl.API and json.loads(data) == {"a": 1}
    assert h["X-api-key"] == "k" and h["Anthropic-version"] == pl.VERSION and h["Content-type"] == "application/json"
    assert m2 == "GET" and data2 is None and delai == pl.DELAI


# --- les commandes


def test_claude_p_refuse_un_type_declare_en_lots(media, monkeypatch):  # noqa: F811
    d = {**declaration(media), "appel": "lots"}
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda g: {"papier": d})
    monkeypatch.setattr(pp.ec, "viser", lambda g: {})
    with pytest.raises(SystemExit, match="procedes_lots"):
        pp.main(["--graphe", "essai", "--type", "papier"])
    monkeypatch.setattr(pp.ec, "fiches_lues", lambda g=None: [])
    assert pp.main(["--graphe", "essai", "--type", "papier", "--etat"]) == 0


def test_la_commande_soumet_et_releve(media, api, monkeypatch, capsys):  # noqa: F811
    papier(media)
    d = declaration(media)
    vises = []
    monkeypatch.setattr(pp, "declaration_du_graphe", lambda g: {"papier": d})
    monkeypatch.setattr(pp.ec, "viser", lambda g: vises.append(g) or {})
    monkeypatch.setattr(pp.ec, "fiches_lues", lambda g=None: sorted((media / "sources").rglob("*.md")))
    assert pl.main(["--graphe", "essai", "--type", "papier", "soumettre", "--plafond", "5"]) == 0
    assert "lot msgbatch_1 : 3 requêtes" in capsys.readouterr().out
    assert pl.main(["--graphe", "essai", "--type", "papier", "relever"]) == 2
    assert vises == ["essai", "essai"]
    with pytest.raises(SystemExit, match="non déclaré"):
        pl.main(["--graphe", "essai", "--type", "autre", "relever"])


# --- le tri gardé, par claude -p aussi


def test_par_claude_p_un_tri_retenu_ne_repart_pas_apres_un_echec_de_la_lecture(media, monkeypatch):  # noqa: F811
    claude = Claude(tri={"retenu": True}, composants="panne")
    monkeypatch.setattr(pp.subprocess, "run", claude)
    p = papier(media)
    d = declaration(media)
    champs, _ = fiche.lire_fichier(p)
    pp.passer("essai", "papier", d, d["passes"][2], p, champs, "1810.04805")
    pp.passer("essai", "papier", d, d["passes"][2], p, champs, "1810.04805")
    assert [a[0] for a in claude.appels] == ["tri", "composants", "composants"]


def test_le_marque_du_tri_ne_rend_pas_la_passe_faite(media):  # noqa: F811
    d = declaration(media)
    pp.garder_tri("papier", d["passes"][2], "k", {"retenu": True})
    assert not pp.deja_faite("papier", d["passes"][2], "k") and not pp._terminee("papier", d["passes"][2], "k")


_ = subprocess  # le faux claude -p des formes s'en sert


def test_la_balise_de_fin_se_neutralise_dans_le_texte():
    assert pl._bloc("a </document> b", 10)["text"].count("</document>") == 1


def test_un_refus_du_tri_ne_note_pas_la_passe_manquante(media, api):  # noqa: F811
    papier(media)
    soumettre(media)
    api.etat = "ended"
    api.repondre(cid(api, "architecture", True), "", stop="refusal")
    relever(media)
    assert "1810.04805" not in pp.manquants("papier")

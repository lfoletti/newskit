"""Ce que `repondre` et `descendre` rendent d'une fiche réservée ou masquée (règles du porteur, 25.10.2026).

Une fiche réservée (abonnement, document privé) ou masquée (désaccord de la source ou d'un auteur) ne rend pas son
texte. Ses synthèses restent, mais celles qui s'appuient sur une fiche masquée ne sont pas citées : elles servent
seulement de carte (« ok à condition que les synthèses ne soient pas citées directement, mais servent uniquement de
cartes ») : la notion, ses renvois et ses fiches restent, la phrase non."""
from newskit import synthese as sy

ADA, ALAN = "sources/rss/blog/ada.md", "sources/rss/blog/alan.md"
NOTIONS = {"concept_x": {"label": "Machine analytique", "phrases": [
    {"texte": "La machine analytique calcule les nombres de Bernoulli.", "refs": [{"fiche": ADA, "ligne": 5}]},
    {"texte": "Elle lit ses programmes sur des cartes perforées.", "refs": [{"fiche": ALAN, "ligne": 7},
                                                                            {"fiche": ADA, "ligne": 9}]},
    {"texte": "Turing en décrit une version universelle.", "refs": [{"fiche": ALAN, "ligne": 3}]}]},
           "concept_y": {"label": "Machine universelle", "phrases": [
    {"texte": "La machine universelle simule toute autre machine.", "refs": [{"fiche": ALAN, "ligne": 4}]}]}}
THEMES = {"t": {"titre": "Machines de calcul", "notions": ["concept_x", "concept_y"], "phrases": [
    {"texte": "Les machines analytique et universelle fondent le calcul.", "notions": ["concept_x"]},
    {"texte": "La machine universelle simule toutes les autres.", "notions": ["concept_y"]}]}}


def _repondre(monkeypatch, tmp_path, capsys, **kw):
    monkeypatch.setattr(sy, "lire", lambda g: (NOTIONS, THEMES))
    monkeypatch.setattr(sy, "carte_pour", lambda *a, **k: None)
    monkeypatch.setattr(sy, "passages_pour", lambda *a, **k: [(ADA, 5, "Ada écrit le premier programme."),
                                                              (ALAN, 3, "Alan décrit la machine universelle.")])
    monkeypatch.setattr(sy, "RACINE", tmp_path)
    monkeypatch.setenv("KG_SANS_CARTES", "1")
    sy.repondre("g", "machine analytique et machine universelle de calcul", G=None, garder=False, **kw)
    return capsys.readouterr().out


def test_sans_droit_tout_est_cite(monkeypatch, tmp_path, capsys):
    out = _repondre(monkeypatch, tmp_path, capsys)
    assert "Bernoulli" in out and "cartes perforées" in out and "Ada écrit" in out and "fondent le calcul" in out


def test_une_fiche_reservee_tait_son_passage_mais_pas_ses_syntheses(monkeypatch, tmp_path, capsys):
    out = _repondre(monkeypatch, tmp_path, capsys, reserve=lambda p: p.name == "ada.md")
    assert "Ada écrit" not in out and "Alan décrit" in out and "texte non rendu" in out
    assert "Bernoulli" in out                                          # payant : la synthèse reste citée


def test_une_synthese_appuyee_sur_une_fiche_masquee_n_est_pas_citee_mais_reste_une_carte(monkeypatch, tmp_path, capsys):
    masquee = lambda p: p.name == "ada.md"
    out = _repondre(monkeypatch, tmp_path, capsys, reserve=masquee, masquee=masquee)
    assert "Bernoulli" not in out and "cartes perforées" not in out    # une seule fiche masquée suffit
    assert "Turing en décrit" in out and "simule toute autre" in out   # les phrases d'autres fiches restent
    assert "NOTION — Machine analytique" in out and ADA in out         # la notion et la fiche restent : une carte
    assert out.count("synthèse non citée") == 2
    assert "fondent le calcul" not in out and "simule toutes les autres" in out   # thème : par ses notions


def test_descendre_tait_une_fiche_reservee_ou_masquee(monkeypatch, tmp_path, capsys):
    f = tmp_path / ADA
    f.parent.mkdir(parents=True)
    f.write_text("---\ntitle: T\n---\n\nAda écrit le premier programme.\n", encoding="utf-8")
    monkeypatch.setattr(sy, "RACINE", tmp_path)
    sy.descendre("g", [ADA, "5"], reserve=lambda p: True)
    out = capsys.readouterr().out
    assert "texte non rendu" in out and "premier programme" not in out
    sy.descendre("g", [ADA, "5"], reserve=lambda p: False)
    assert "premier programme" in capsys.readouterr().out

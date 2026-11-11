"""Les réglages propres au média (mainteneur, 11.11.2026 : « la variable FIL_MAX ne devrait pas passer par newskit, mais
se faire en corrigeant le fichier config. c'est la même remarque pour toutes les variables "média-spécifique" »).

Le bloc `reglages:` du config.yaml du média remplace une constante d'un module, par module : `reglages.fil.pile_delai`.
Le moteur garde la valeur par défaut. Seules les constantes de REGLABLES se règlent ; un nom inconnu ou un type différent
de celui du défaut est refusé au chargement."""

from __future__ import annotations

import ast
import importlib
import inspect

import pytest

from newskit import media
from newskit import reglages as rg


def test_un_reglage_remplace_la_constante_du_module():
    espace = {"PILE_DELAI": 10, "PART_SECONDAIRE": 0.8, "JOUR_GARANTI": (6, 23)}
    c = {"reglages": {"fil": {"pile_delai": 15, "part_secondaire": 1, "jour_garanti": [7, 22]}}}
    rg.appliquer("newskit.fil", espace, c)
    assert espace == {"PILE_DELAI": 15, "PART_SECONDAIRE": 1.0, "JOUR_GARANTI": (7, 22)}
    assert isinstance(espace["PART_SECONDAIRE"], float)


def test_sans_reglage_le_defaut_reste():
    espace = {"PILE_DELAI": 10}
    rg.appliquer("newskit.fil", espace, {})
    rg.appliquer("newskit.fil", espace, {"reglages": {"lettre": {"faits_max": 3}}})
    assert espace == {"PILE_DELAI": 10}


@pytest.mark.parametrize(
    "bloc",
    [
        {"inconnu": {"x": 1}},
        {"fil": {"fil_inconnu": 1}},
        {"fil": {"pile_delai": "10"}},
        {"fil": {"pile_delai": True}},
        {"fil": {"pile_delai": 1.5}},
        {"fil": {"part_secondaire": "0.8"}},
        {"fil": {"jour_garanti": [6]}},
        {"fil": {"jour_garanti": [6, "23"]}},
        {"lettre": {"point": 3}},
        {"fil": []},
        [],
    ],
)
def test_un_reglage_inconnu_ou_mal_type_est_refuse(bloc, tmp_path):
    with pytest.raises(media.ConfigMediaInvalide):
        rg.valider(bloc)
    p = tmp_path / "config.yaml"
    import yaml

    p.write_text(yaml.safe_dump({"version": 1, "media": {"nom": "media"}, "reglages": bloc}), encoding="utf-8")
    with pytest.raises(media.ConfigMediaInvalide):
        media.charger(p)


def test_le_config_du_media_admet_le_bloc(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("version: 1\nmedia: {nom: media}\nreglages:\n  fil: {pile_delai: 12}\n  lettre: {point: '#000000'}\n")
    assert media.charger(p)["reglages"]["fil"]["pile_delai"] == 12


def test_chaque_reglable_existe_et_son_module_applique_les_reglages():
    for court, noms in rg.REGLABLES.items():
        m = importlib.import_module(f"newskit.{court}")
        for nom in noms:
            assert hasattr(m, nom), (court, nom)
        arbre = ast.parse(inspect.getsource(m))
        dernier = ast.unparse(arbre.body[-1]) if not isinstance(arbre.body[-1], ast.If) else ast.unparse(arbre.body[-2])
        assert dernier == "reglages.appliquer(__name__, globals())", court


def test_aucun_reglable_n_est_fige_avant_l_application():
    """Une valeur par défaut d'argument ou une constante dérivée se calcule à l'import, avant le réglage : elle garderait
    le défaut du moteur. Un réglable ne se lit que dans le corps d'une fonction."""
    for court, noms in rg.REGLABLES.items():
        m = importlib.import_module(f"newskit.{court}")
        arbre = ast.parse(inspect.getsource(m))
        figes = set()
        for n in ast.walk(arbre):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in n.args.defaults + n.args.kw_defaults:
                    figes |= {x.id for x in ast.walk(d) if isinstance(x, ast.Name)} if d else set()
        copies = set()  # un réglable d'un autre module, copié à l'import (fil.PART_SECONDAIRE)
        for n in arbre.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                valeur = n.value
                figes |= {x.id for x in ast.walk(valeur) if isinstance(x, ast.Name)} if valeur else set()
                copies |= {x.attr for x in ast.walk(valeur) if isinstance(x, ast.Attribute)} if valeur else set()
        assert not figes & noms, (court, sorted(figes & noms))
        assert not copies & set().union(*rg.REGLABLES.values()), (court, sorted(copies))


def test_le_reglage_du_media_atteint_le_module(monkeypatch):
    from newskit import fil

    monkeypatch.setattr(fil, "PILE_DELAI", fil.PILE_DELAI)
    rg.appliquer(fil.__name__, vars(fil), {"reglages": {"fil": {"pile_delai": 17}}})
    assert fil.delai_de_parution(1) == 17


class _Arret(Exception):
    pass


def test_les_arguments_regles_de_la_lettre_lisent_la_constante_a_l_appel(monkeypatch):
    from newskit import lettre as lt

    faits = [{"editeur": e, "editeurs_recents": 2} for e in ("A", "B", "C")]
    monkeypatch.setattr(lt, "UNE_MIN", 2)
    assert len(lt.completer_une([], faits)[0]) == 2
    monkeypatch.setattr(lt, "FAITS_MAX", 0)
    sans = lt.estimer_usd("x")
    monkeypatch.setattr(lt, "FAITS_MAX", 50)
    assert lt.estimer_usd("x") > sans
    texte = "Le texte dit une chose. Il en dit une autre."
    monkeypatch.setattr(lt, "CHAPEAU_PHRASES", 1)
    assert lt.ecarts_de_forme(texte, 1, 100) != lt.ecarts_de_forme(texte, 1, 100, 5)
    assert lt.choisir_description([texte], texte, 1, 100)[0] == texte
    monkeypatch.setattr(lt, "dernier_passage", lambda g: None)
    assert lt.passages_de_la_periode("g") is None

    def arret(*a, **k):
        raise _Arret

    from newskit import notifier_courriel as nc

    monkeypatch.setattr(nc, "lire_rapport", arret)
    with pytest.raises(_Arret):
        lt.construire("r", {}, {}, rubriques=[])

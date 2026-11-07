"""Les noms que le moteur écrit hors de lui ne nomment pas la première édition (N5, avant N6, 07.11.2026).

La métadonnée d'une entité qui porte l'identifiant du nœud s'appelait `contaixt_id` ; le client MCP du relevé des
lacunes se présentait au gateway comme `contaixt-releve`. Ils s'appellent `newskit_id` et `newskit-releve`.

Aucun lecteur ne lit `contaixt_id` ; mais l'empreinte d'un versement (versement en place) couvre les métadonnées : un
renommage nu ferait reverser chaque document de chaque cerveau. L'empreinte se calcule donc sous l'ancien nom de la
clé ; un document ne se reverse que s'il change, et le cerveau prend le nouveau nom au fil des versements.
"""

from __future__ import annotations

import inspect

from newskit import lacunes  # noqa: E402
from newskit import puits_cortexdb as pc  # noqa: E402


def test_une_entite_porte_l_identifiant_sous_le_nom_du_moteur():
    for n in ({"id": "concept_ruche", "label": "ruche"},
              {"id": "auteur_x", "label": "X", "kind": "auteur"},
              {"id": "titre_1", "label": "Titre", "_origin": "ast"}):
        m = pc.entite_composee(n, "sources/a.md", {})["metadata"]
        assert m.get("newskit_id") == n["id"] and "contaixt_id" not in m, n


def test_le_renommage_ne_change_pas_l_empreinte_d_un_versement():
    def versement(cle: str) -> dict:
        return {"document": {"document_id": "abc"}, "relations": [],
                "entites": [{"name": "ruche", "metadata": {"_source": "s", cle: "concept_ruche"}}]}
    assert pc.empreinte(versement("newskit_id")) == pc.empreinte(versement("contaixt_id"))
    autre = versement("newskit_id")
    autre["entites"][0]["metadata"]["newskit_id"] = "concept_autre"
    assert pc.empreinte(autre) != pc.empreinte(versement("newskit_id"))


def test_le_releve_des_lacunes_se_presente_sous_le_nom_du_moteur():
    s = inspect.getsource(lacunes.Thalamus.__init__)
    assert '"newskit-releve"' in s and "contaixt" not in s

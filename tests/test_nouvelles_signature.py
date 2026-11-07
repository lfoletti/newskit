"""N3 de CHANTIER_NOUVELLES.md : la signature d'un texte (ses noms propres et ses nombres) propose les nouvelles
proches ; le modèle décide. Le 05.11.2026 le fil a fait trois billets de l'enquête de DNSFilter : il comparait les
titres, et aucun ne nommait DNSFilter."""

from __future__ import annotations

from newskit import nouvelles as nv

HCAMAG = (
    "One in five firms hit by shadow AI incident. A DNSFilter survey of 1,271 IT leaders found that 20.1% had an "
    "incident. Ken Carnesi, chief executive of DNSFilter, said that 46.1% saw an employee connect a tool."
)
KITEWORKS = (
    "Compliance teams lose weeks to shadow AI. The report by DNSFilter says 20.1% of organisations had an "
    "incident and 36.8% cannot see the tools. Kiteworks reads the numbers for compliance officers."
)
ASUS = "ASUS presents an agentic AI offer for companies at its Taipei event. The offer costs 1,271 dollars a year."
VOLTA = "Cisco Talos describes the Volta infostealer, which targets Roblox and DoorDash accounts."


def test_la_signature_garde_les_noms_propres_et_les_nombres():
    s = nv.signature(
        "Selon Ken Carnesi (DNSFilter), 20,1 % des 1 271 responsables ont vu un incident en 2026. "
        "Le rapport de l'Éducation nationale et de l’ONU en parle le 5 novembre."
    )
    assert {"ken carnesi", "dnsfilter", "education", "onu"} <= s  # « Selon » ouvre la phrase, « L' » est un article
    assert {"#201%", "#1271"} <= s  # la virgule, le point et l'espace ne changent pas un nombre
    assert "#2026" not in s and "#5" not in s  # une année ou un chiffre seul ne distingue pas une nouvelle
    assert not {"novembre", "responsables", "selon", "le", "le rapport"} & s
    assert not {"ue", "ia"} & nv.signature("Dans l'UE, l'IA avance.")  # deux lettres : un sigle trop courant
    assert nv.signature("20.1% of 1,271 leaders") >= {"#201%", "#1271"}  # la même en anglais
    assert nv.signature("") == set() and nv.signature(None) == set()


def test_les_nouvelles_proches_partagent_des_termes_rares():
    autres = {"hcamag": nv.signature(HCAMAG), "asus": nv.signature(ASUS), "volta": nv.signature(VOLTA)}
    assert nv.proches(nv.signature(KITEWORKS), autres) == ["hcamag"]  # DNSFilter, 20,1 %
    assert nv.proches(nv.signature(HCAMAG), {**autres, "kiteworks": nv.signature(KITEWORKS)})[0] == "hcamag"
    assert nv.proches(nv.signature("Rien de commun ici."), autres) == []


def test_un_terme_commun_a_beaucoup_de_nouvelles_ne_rapproche_pas():
    # « The » ouvre toutes les sources en anglais : au-delà de `plafond` nouvelles, un terme ne compte plus.
    autres = {f"n{i}": nv.signature(f"The Commission said {1000 + i} things. Nom{i} Propre{i}.") for i in range(8)}
    assert nv.proches(nv.signature("The Commission said nothing. Autre Chose."), autres) == []
    assert nv.proches(nv.signature("The Commission. Nom3 Propre3."), autres) == ["n3"]


def test_un_terme_rare_pese_plus_que_deux_termes_courants():
    autres = {f"courant{i}": {"c1", "c2"} for i in range(7)} | {"rare": {"r"}}
    assert nv.proches({"c1", "c2", "r"}, autres, k=1, plafond=99) == ["rare"]


def test_les_plus_proches_d_abord_et_k_au_plus():
    autres = {"un": {"a", "b", "c"}, "deux": {"a", "b"}, "trois": {"a"}, "quatre": {"z"}}
    assert nv.proches({"a", "b", "c"}, autres, plafond=9) == ["un", "deux", "trois"]
    assert nv.proches({"a", "b", "c"}, autres, k=2, plafond=9) == ["un", "deux"]


AVANT = (
    "Szpunar conseille à la Cour de justice de laisser de côté l'essentiel des questions. L'avocat général "
    "Maciej Szpunar a rendu son avis dans l'affaire Like Company le 4 novembre 2026. Google Gemini, 250 mots."
)


def test_un_developpement_porte_un_fait_absent_des_sources_d_avant():
    assert nv.fait_neuf("La Cour de justice rendra son arrêt le 12 janvier 2027.", AVANT) is True  # une date
    assert nv.fait_neuf("L'éditeur hongrois réclame 3,5 millions d'euros à Google.", AVANT) is True  # un nombre
    assert nv.fait_neuf("La réaction d'Angela Mills Wade, du Conseil européen des éditeurs.", AVANT) is True  # un nom
    assert nv.fait_neuf("La source confirme l'avis de Maciej Szpunar dans l'affaire Like Company.", AVANT) is False
    assert nv.fait_neuf("Szpunar a rendu son avis le 4 novembre 2026 : 250 mots de Gemini.", AVANT) is False
    assert (
        nv.fait_neuf("Selon la source, l'avis compte pour les éditeurs.", AVANT) is False
    )  # « Selon » ouvre la phrase
    assert nv.fait_neuf("", AVANT) is False


def test_la_fusion_garde_une_nouvelle_et_toutes_les_sources():
    F = {
        "pile": [{"chemin": "s/p.md", "url": "p", "titre": "P", "suite_de": "s/b.md"}],
        "billets": [
            {
                "chemin": "s/a.md",
                "url": "a",
                "titre": "A",
                "source": {"titre": "ta", "editeur": "ea", "date": "da", "url": "a"},
            },
            {
                "chemin": "s/b.md",
                "url": "b",
                "titre": "B",
                "source": {"titre": "tb", "editeur": "eb", "date": "db", "url": "b"},
                "sources_plus": [{"titre": "tc", "editeur": "ec", "date": "dc", "url": "c", "chemin": "s/c.md"}],
            },
        ],
    }
    a, b = F["billets"]
    nv.fusionner(F, a, b)
    assert [x["titre"] for x in F["billets"]] == ["A"]
    assert a["sources_plus"] == [
        {"titre": "tb", "editeur": "eb", "date": "db", "url": "b", "chemin": "s/b.md"},
        {"titre": "tc", "editeur": "ec", "date": "dc", "url": "c", "chemin": "s/c.md"},
    ]
    assert nv.nouvelle_de(F, "s/b.md") is a and nv.nouvelle_de(F, "s/c.md") is a
    assert F["pile"][0]["suite_de"] == "s/a.md"  # la suite de la nouvelle absorbée suit la nouvelle gardée
    p = F["pile"][0]
    nv.fusionner(F, a, p)  # une nouvelle de la pile s'absorbe de même
    assert F["pile"] == [] and nv.nouvelle_de(F, "s/p.md") is a

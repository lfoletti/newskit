"""La lettre dans Outlook Windows et le logo du site (mainteneur, 10.11.2026 : « le logo n'est pas à jour, prendre
identique à site, et dans outlook windows l'aspect est plutôt catastrophique, liserés mal placés, marges déséquilibrées »).

Outlook Windows rend le HTML avec le moteur de Word : il ignore `max-width`, le `margin` d'une table, le `padding` d'un
`div`, `valign="baseline"` et l'image de fond CSS ; il agrandit images et marges à l'échelle de Windows sans
`PixelsPerInch`. Le logo est une image du groupe marque et nom du site, ondes comprises.
"""

from __future__ import annotations

import re

from newskit import lettre as lt

SOURCE = {"editeur": "example.org", "url": "https://example.org/a", "date": "2026-11-09"}


def _lettre(**plus) -> dict:
    L = {
        "site": "https://site.example",
        "marque": "site.example",
        "graphe": "essai",
        "depot": "depot",
        "lettre_nom": "The Governance News",
        "lettre_titre": "The Governance",
        "lettre_slogan": "",
        "periode": "jour",
        "jour": "2026-11-10",
        "envoi": "2026-11-10",
        "heure": "2026-11-10 07:13",
        "faits": [
            {
                "titre": f"Fait {i}",
                "paragraphe": "Texte du fait.",
                "source": SOURCE,
                "rubrique": "Conformité",
                "gras": [],
            }
            for i in (1, 2)
        ],
        "aussi": {},
        "bref": {
            "Éthique": [{"titre": "Brève A", "descriptif": "Texte.", "url": "https://example.org/b"}],
            "Droit": [{"titre": "Brève B", "descriptif": "Texte.", "url": "https://example.org/c"}],
        },
        "ordre": ["Éthique", "Droit"],
        "bref_reste": 0,
        "notions_neuves": 3,
        "identifiants": 5,
        "neuf": "+2",
        "noeuds": "100",
        "aretes": "200",
        "ecart_noeuds": "",
        "ecart_aretes": "",
        "fiches": "10",
        "cout_passage": "",
        "cout_lettre": 0,
        "sante": ["une source muette"],
        "etat": "ok",
    }
    L.update(plus)
    return L


def _pages() -> list[str]:
    return [
        lt.html(_lettre()),
        lt.html(_lettre(), coulisses=True),
        lt.html(_lettre(periode="semaine", debut="2026-11-02")),
    ]


def test_l_en_tete_porte_le_logo_du_site():
    for h in _pages():
        assert 'src="https://site.example/lettres/logo_lettre.png"' in h
        assert "compas_marine.png" not in h


def test_le_logo_est_servi_avec_les_images_des_lettres():
    assert "logo_lettre.png" in lt.LOGOS


def test_aucune_image_de_fond_css():
    for h in _pages():
        assert "background-image" not in h


def test_la_colonne_a_une_table_fantome_de_600_px_pour_outlook():
    for h in _pages():
        assert re.search(r'<!--\[if mso\]><table role="presentation" width="600" align="center"[^>]*><tr><td>', h)
        assert h.count("<!--[if mso]></td></tr></table><![endif]-->") >= 1


def test_outlook_garde_l_echelle_de_96_ppp():
    for h in _pages():
        assert 'xmlns:o="urn:schemas-microsoft-com:office:office"' in h
        assert "<o:PixelsPerInch>96</o:PixelsPerInch>" in h


def test_outlook_recoit_des_polices_de_repli():
    for h in _pages():
        tete = h[: h.index("<body")]
        assert re.search(r"<!--\[if mso\]><style>[^<]*Segoe UI[^<]*Georgia[^<]*</style><!\[endif\]-->", tete)


def test_aucun_alignement_sur_la_ligne_de_base():
    for h in _pages():
        assert 'valign="baseline"' not in h


def test_aucune_marge_sur_une_table():
    for h in _pages():
        for t in re.findall(r"<table[^>]*>", h):
            assert "margin" not in t, t


def test_aucun_filet_sur_un_div_ou_un_paragraphe():
    for h in _pages():
        for t in re.findall(r"<(?:div|p)\b[^>]*>", h):
            assert "border-top" not in t, t


def test_les_filets_sont_des_bords_de_cellule():
    h = lt.html(_lettre(), coulisses=True)
    # un filet entre les deux faits de la une, un entre les deux rubriques, un au-dessus du rapport des coulisses
    assert len(re.findall(r'<td[^>]*style="[^"]*border-top:1px solid', h)) >= 3


def test_aucun_bloc_a_fond_ou_bord_dans_un_div():
    # Outlook ignore le padding d'un div : le cadre de « Santé du passage » se colle au texte.
    for h in _pages():
        for t in re.findall(r"<div\b[^>]*>", h):
            assert not re.search(r"border:1px|background:", t), t


def test_les_tuiles_des_coulisses_sont_en_ligne_dans_outlook():
    h = lt.html(_lettre(), coulisses=True)
    assert h.count('<!--[if mso]></td><td width="158" valign="top"><![endif]-->') == 3


def test_sans_site_la_marque_reste_en_texte():
    h = lt.html(_lettre(site=""))
    assert "logo_lettre.png" not in h


# Variante B (mainteneur, 10.11.2026 : « choix B ») : le logo seul au-dessus des cartes, à 0,8 (« logo plus petit x0.8,
# moins de marge en dessus et en dessous ») ; le titre et la date en tête de la première carte.
def _ban(h: str) -> str:
    i = h.index('class="ban"')
    return h[i : h.index("</td></tr>", i)]


def _premiere_carte(h: str) -> str:
    i = h.index('class="carte"')
    return h[i : h.index("</td></tr>\n", i)]


def test_le_logo_est_seul_au_dessus_des_cartes_a_0_8():
    for h in _pages():
        ban = _ban(h)
        assert 'width="384" height="74"' in ban and "max-width:384px" in ban
        assert "The Governance" not in ban and "<h1" not in ban


def test_le_titre_et_la_date_ouvrent_la_carte_des_unes():
    h = lt.html(_lettre())
    carte = _premiere_carte(h)
    assert (
        carte.index("The Governance")
        < carte.index(lt.entete(lt.polir(_lettre(), html_=True))[2])
        < carte.index("À la une")
    )
    assert re.search(r'<td colspan="2" style="border-top:1px solid #aab2bf;', carte)  # le trait sous le titre


def test_sans_une_le_titre_ouvre_la_carte_des_rubriques():
    h = lt.html(_lettre(faits=[]))
    carte = _premiere_carte(h)
    assert "The Governance" in carte and "Éthique" in carte and "À la une" not in h


def test_moins_de_marge_au_dessus_du_logo():
    for h in _pages():
        assert 'class="fond" style="padding:20px 12px 34px 12px;"' in h


def test_au_telephone_la_date_passe_sous_le_titre():
    assert ".tete-date { display: block !important;" in lt.STYLE

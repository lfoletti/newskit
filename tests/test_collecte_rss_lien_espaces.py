"""Un flux qui écrit l'adresse d'un article avec des espaces et des guillemets typographiques (Sixth Tone, 06.11.2026 :
« URL can't contain control characters ») : l'adresse est encodée à la lecture du flux, la page se lit."""

from __future__ import annotations

from newskit import collecte_rss as rss

ADRESSE = "https://www.sixthtone.com/news/1019126/Chinese Users Find Little Money in ‘Selling’ Their Faces to AI"
ENCODEE = "https://www.sixthtone.com/news/1019126/Chinese%20Users%20Find%20Little%20Money%20in%20%E2%80%98Selling%E2%80%99%20Their%20Faces%20to%20AI"
RSS = f"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>
<item><title>Faces</title><link>{ADRESSE}</link><pubDate>Thu, 05 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Deja</title><link>https://exemple.org/a%20b?x=1#n</link></item>
</channel></rss>""".encode()


def test_l_adresse_d_un_article_est_encodee_a_la_lecture_du_flux():
    for lire in (rss.entrees, rss.entrees_tolerantes):
        liens = [e["lien"] for e in lire(RSS)]
        assert liens == [ENCODEE, "https://exemple.org/a%20b?x=1#n"], lire.__name__  # un encodage déjà fait reste


def test_le_nom_de_fiche_vient_de_l_adresse_decodee():
    assert (
        rss.slug(ENCODEE, "") == rss.slug(ADRESSE, "") == "chinese-users-find-little-money-in-selling-their-faces-to-ai"
    )

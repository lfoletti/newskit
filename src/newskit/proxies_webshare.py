#!/usr/bin/env python3
"""Liste de proxys Webshare : récupération à la demande, sans rien écrire.

Pourquoi ce script existe. arXiv bride les rafales, et c'est légitime : trois
secondes entre deux requêtes sont ce que l'API demande. Un proxy ne supprime
pas cette demande, il la contourne — donc il sert à débloquer une situation,
pas à tenir un régime de collecte. Le script est écrit pour cet usage-là.

Ce qui ne se trouve pas ici. Ni l'URL de téléchargement de la liste, ni un seul
proxy. L'URL porte un jeton — quiconque l'a peut télécharger la liste, laquelle
contient les identifiants de chaque proxy : c'est une crédence, et elle vit au
trousseau macOS sous le service `<compte>:webshare:liste` (compte : bloc `credences` du registre), comme les
sessions payantes. La liste elle-même n'est pas mise en cache par défaut : elle
est retéléchargée quand on en a besoin, gardée en mémoire, et perdue à la fin du
processus. Un fichier de proxys oublié sur le disque est une fuite qui dort.

Déposer l'URL, la valeur étant demandée à l'écran ou prise au presse-papiers :
    security add-generic-password -a <compte> \\
        -s <compte>:webshare:liste -U -T /usr/bin/security -w

Usage :
    python3 -m newskit.proxies_webshare                 # combien, et d'où, sans rien révéler
    python3 -m newskit.proxies_webshare --une-url       # une URL de proxy, pour l'environnement
    python3 -m newskit.proxies_webshare --eprouver 5    # teste 5 proxys contre l'API arXiv

Pour une collecte qui doit passer par un proxy :
    export http_proxy="$(python3 -m newskit.proxies_webshare --une-url)"
"""

from __future__ import annotations

import argparse
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402

CREDENCE = "webshare_liste"
SONDE = "http://export.arxiv.org/api/query?id_list=1706.03762&max_results=1"


def _masque(hote: str) -> str:
    """Un hôte reste identifiable sans être réutilisable : dernier octet caché."""
    bouts = hote.split(".")
    return ".".join(bouts[:-1] + ["x"]) if len(bouts) == 4 else hote


def telecharger() -> list[str]:
    """Rend une liste d'URL de proxy. Ne journalise ni URL ni identifiant."""
    url = cfg.session(CREDENCE)
    if not url:
        raise SystemExit(
            "  URL de liste absente du trousseau. La déposer, valeur demandée à l'écran :\n"
            f"    {cfg.commande_de_depot(CREDENCE)}\n"
            "  ou, l'URL étant dans le presse-papiers :\n"
            f"    {cfg.commande_de_depot(CREDENCE, presse_papier=True)}"
        )
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "veille-data-science/1.0"}), timeout=60
    ) as r:
        brut = r.read().decode("utf-8", "replace")

    proxys = []
    for ligne in brut.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne.startswith("http"):
            proxys.append(ligne)
            continue
        # Forme usuelle de Webshare : hote:port:identifiant:motdepasse
        bouts = ligne.split(":")
        if len(bouts) == 4:
            h, p, u, m = bouts
            proxys.append(f"http://{urllib.parse.quote(u)}:{urllib.parse.quote(m)}@{h}:{p}")
    return proxys


def eprouver(url_proxy: str, delai: int = 25) -> tuple[bool, str]:
    """Une requête réelle à l'API d'arXiv à travers le proxy. Sinon on ne sait rien."""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url_proxy, "https": url_proxy}))
    try:
        with op.open(
            urllib.request.Request(SONDE, headers={"User-Agent": "veille-data-science/1.0"}), timeout=delai
        ) as r:
            return (b"Attention Is All You Need" in r.read()), "réponse attendue"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--une-url", action="store_true", help="écrit une URL de proxy sur la sortie standard, et rien d'autre"
    )
    ap.add_argument("--eprouver", type=int, metavar="N", help="éprouve N proxys tirés au hasard contre l'API d'arXiv")
    args = ap.parse_args()

    proxys = telecharger()
    if not proxys:
        print("  liste téléchargée mais vide ou de forme inconnue.", file=sys.stderr)
        return 1

    if args.une_url:
        # Seul cas où une URL complète est écrite : elle est destinée à
        # l'environnement d'un processus, pas à un journal ni à un rapport.
        print(random.choice(proxys))
        return 0

    hotes = {urllib.parse.urlsplit(p).hostname or "?" for p in proxys}
    print(f"  {len(proxys)} proxy(s) dans la liste, {len(hotes)} hôte(s) distinct(s).")
    print(f"  exemple d'hôte, masqué : {_masque(sorted(hotes)[0])}")
    print("  aucune valeur n'est écrite sur le disque ; la liste est perdue à la sortie.")

    if args.eprouver:
        echantillon = random.sample(proxys, min(args.eprouver, len(proxys)))
        bons = 0
        print(f"\n  épreuve de {len(echantillon)} proxy(s) contre l'API d'arXiv :")
        for p in echantillon:
            ok, detail = eprouver(p)
            bons += ok
            print(f"    {'ok   ' if ok else 'échec'}  {_masque(urllib.parse.urlsplit(p).hostname or '?')}  {detail}")
        print(f"  {bons}/{len(echantillon)} utilisable(s).")
        return 0 if bons else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

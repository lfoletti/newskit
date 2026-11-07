#!/usr/bin/env python3
"""Convertit un résultat get_page_text (JSON archivé par l'outil navigateur) en fiche markdown sources/medium/.
Usage : python3 -m newskit.convertir_page_text <fichier.json> [<fichier.json> ...]"""
import json, re, sys, datetime
from pathlib import Path

from newskit import fiche
from newskit.commun import RACINE  # noqa: E402 — la racine des données (N1)
OUT = RACINE / "sources" / "medium"
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
for fn in sys.argv[1:]:
    d = json.load(open(fn)); txt = d[0]["text"] if isinstance(d, list) else d["text"]
    head, _, body = txt.partition("\n---\n")
    title = re.search(r"^Title: (.*)$", head, re.M).group(1); url = re.search(r"^URL: (.*)$", head, re.M).group(1).split("?")[0]
    m = re.search(r"\b([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b", body[:3000])
    date = f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}" if m else "0000-00-00"
    am = re.search(r"\n([^\n]{2,60})\nFollow\n", body[:3000]); author = am.group(1).strip() if am else ""
    lines = [l.rstrip() for l in body.split("\n")]
    if title.split(" | ")[0].strip() in ("Medium", ""):   # certains domaines personnalisés n'exposent pas le titre
        mr = next((i for i, l in enumerate(lines) if re.match(r"\d+ min read", l.strip())), len(lines))
        title = next((l.strip() for l in lines[:mr] if len(l.strip()) > 30), title)
        if not author and mr >= 1: author = lines[mr - 1].strip()
    # retirer le bandeau (tout avant le titre) et les compteurs / boutons
    idx = next((i for i, l in enumerate(lines) if l.strip() == title.split(" | ")[0].strip()), 0)
    lines = lines[idx:]
    junk = re.compile(r"^(\d+(\.\d+)?K?|·|Follow|Share|Listen|Top highlight|Member-only story|\d+ min read|Read this story for free: link|Press enter or click to view image in full size)$")
    lines = [l for l in lines if not junk.match(l.strip())]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    words = len(body.split())
    slug = re.sub(r"^https?://[^/]+/", "", url).strip("/").replace("@", "").replace("/", "__")
    champs = {"title": title.split(" | ")[0].strip(), "author": author, "date": date, "url": url,
              "plateforme": "Medium", "complet": True, "source": "Medium (abonnement)",
              "collected": datetime.date.today().isoformat(), "words": words}
    out = OUT / f"{date}_{slug}.md"
    try:
        fiche.ecrire(out, champs, "# " + champs["title"] + "\n\n" + body)   # contrat commun
    except fiche.IdentiteDejaVue as deja:              # l'article est déjà une fiche du graphe
        print(f"déjà vue : {deja}")
        continue
    print(out.name, words, "mots")

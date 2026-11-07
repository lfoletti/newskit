#!/usr/bin/env python3
"""Complète les fiches Substack « aperçu seulement » à partir des textes de page capturés dans la transcription
(get_page_text sur la page du post, session abonné). Usage : python3 -m newskit.convertir_substack_page_text <session.jsonl>"""

import json, re, sys
from pathlib import Path

from newskit import fiche
from newskit.commun import RACINE  # noqa: E402 — la racine des données (N1)

ROOT = RACINE / "sources" / "substack"
seen = {}
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    m = d.get("message")
    if not isinstance(m, dict) or m.get("role") != "user" or not isinstance(m.get("content"), list):
        continue
    for c in m["content"]:
        if c.get("type") != "tool_result":
            continue
        content = c.get("content")
        txt = None
        if isinstance(content, list) and content and content[0].get("type") == "text":
            txt = content[0]["text"]
        elif isinstance(content, str):
            txt = content
        if not txt:
            continue
        pm = re.search(r"Full output saved to: (\S+\.json)", txt)
        if pm and Path(pm.group(1)).exists():
            j = json.load(open(pm.group(1)))
            txt = j[0]["text"] if isinstance(j, list) else j.get("text", "")
        um = re.search(r"^URL: (\S+)", txt, re.M)
        if txt.startswith("Title: ") and um and "/p/" in um.group(1):
            seen[um.group(1).split("?")[0]] = txt
print(len(seen), "pages Substack dans la transcription")
for url, txt in seen.items():
    slug = url.rstrip("/").rsplit("/", 1)[1]
    hit = None
    for mf in ROOT.glob("*/_manifest.json"):
        man = json.loads(mf.read_text())
        if slug in man:
            hit = (mf.parent, man)
            break
    if not hit:
        print("  ? pas dans un manifeste :", slug)
        continue
    pubdir, man = hit
    entry = man[slug]
    f = pubdir / entry["file"]
    champs, _ = fiche.lire_fichier(f)
    body = txt.partition("\n---\n")[2]
    lines = [l.rstrip() for l in body.split("\n")]
    junk = re.compile(
        r"^(Share|Share this post|Copy link|Facebook|Email|Notes|More|Subscribe|Sign in|\d+|Discussion about this post|Comments|Restacks|Ready for more\?|Previous|Next|© \d{4}.*|Privacy|Terms|Collection notice|Start your Substack|Get the app|Substack is the home for great culture)$"
    )
    lines = [l for l in lines if not junk.match(l.strip())]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    # couper le pied de page (commentaires / suggestions) après le dernier paragraphe : on garde tout jusqu'à "Discussion about this post" si présent
    cut = body.find("\nDiscussion about this post")
    if cut > 0:
        body = body[:cut]
    words = len(body.split())
    # Le frontmatter n'est plus retouché par expression régulière : on repasse
    # par le contrat commun, qui recalcule et revalide (voir newskit.fiche).
    champs.update({"words": words, "complet": True, "source": "Substack (abonnement, texte de page navigateur)"})
    fiche.ecrire(f, champs, "# " + entry["title"] + "\n\n" + body)
    entry["complet"] = True
    entry["words"] = words
    (pubdir / "_manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    print("  ok", entry["file"], words, "mots")

#!/usr/bin/env python3
"""Extrait de la transcription de session Claude Code tous les résultats get_page_text d'articles Medium
(ou d'un fichier <persisted-output>) et les convertit en fiches sources/medium/*.md via convertir_page_text.
Usage : python3 -m newskit.convertir_transcription <session.jsonl>"""

import json, re, sys, subprocess, tempfile
from pathlib import Path

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
        if not txt.startswith("Title: "):
            continue
        um = re.search(r"^URL: (\S+)", txt, re.M)
        if not um or not re.search(r"-[0-9a-f]{8,}$", um.group(1).split("?")[0]):
            continue  # seulement des articles Medium
        seen[um.group(1).split("?")[0]] = txt  # le dernier résultat gagne
print(len(seen), "articles trouvés dans la transcription")
tmp = Path(tempfile.mkdtemp())
files = []
for i, (u, t) in enumerate(seen.items()):
    p = tmp / f"{i}.json"
    p.write_text(json.dumps([{"type": "text", "text": t}]))
    files.append(str(p))
if files:
    subprocess.run([sys.executable, str(Path(__file__).parent / "convertir_page_text.py"), *files], check=True)

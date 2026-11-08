#!/usr/bin/env python3
"""Le fil en direct sur le site de l'édition (mainteneur, 04.11.2026 : « j'aimerais mettre en place la mise à jour automatique »).

Après chaque passage du fil (collecte_continue.py), le Mac dépose sur le VPS une copie publique du fil : fil.json réduit
aux champs que la page affiche (ni la dépense, ni les sources vues, ni les chemins des fiches) et fil.xml (le flux RSS,
déjà public). nginx les sert sous /fil.json et /fil.xml ; la page d'accueil relit /fil.json toutes les 5 minutes.

Accès : ~/.secrets/vps_kg.env (VPS_HOTE, VPS_UTILISATEUR, VPS_CLE). Le dépôt passe par une seule connexion ssh et
remplace les deux fichiers par renommage. Un fil inchangé ne repart pas (empreinte dans cache/fil_publie_<graphe>.sha).

Usage :
    python3 -m newskit.publier_fil [--graphe <graphe du site>]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)
from newskit import lettre as lt  # noqa: E402 — la typographie de tout texte publié
from newskit import nouvelles as nv  # noqa: E402 — le texte d'une nouvelle, sa définition comprise

ENV = Path.home() / ".secrets" / "vps_kg.env"
DOSSIER_VPS = "/var/www/kg-mcp/fil"
CHAMPS = ("titre", "descriptif", "rubrique", "url")
MAX = 150  # billets de la copie publique (mainteneur, 06.11.2026 : « passe à 150 news affichées »)


def public(F: dict) -> dict:
    """La copie publique du fil : les billets récents, avec les seuls champs que la page affiche ; titres et textes
    passés par la typographie de tout texte publié (lt.typographie)."""
    return {"billets": [_typo(b) for b in _public(F)]}


def _typo(p: dict) -> dict:
    fr = {k: lt.typographie(p[k], "fr") for k in ("titre", "descriptif", "fiche", "resume", "categorie")}
    en = {k: lt.typographie(p[k], "en") for k in ("titre_en", "descriptif_en", "resume_en", "categorie_en")}
    els = {
        k: [{c: lt.typographie(e[c], lg) for c in ("champ", "texte")} for e in p[k]]
        for k, lg in (("elements", "fr"), ("elements_en", "en"))
    }
    return {**p, **fr, **en, **els}


def liens(b: dict) -> dict:
    """La source mise en lien sur chaque page du site : la version dans sa langue, sinon l'anglaise, sinon l'originale
    (fil.lien)."""
    from newskit import fil  # noqa: PLC0415 — fil importe ce module au passage

    fr, en = fil.lien(b, "fr"), fil.lien(b, "en")
    return {"lien_fr": fr[0], "langue_fr": fr[1], "lien_en": en[0], "langue_en": en[1]}


def _public(F: dict) -> list[dict]:
    return [
        {
            **{k: b.get(k, "") for k in CHAMPS},
            **liens(b),
            "descriptif": nv.texte(b),
            "editeur": (b.get("source") or {}).get("editeur", ""),
            "date": b.get("date", ""),
            "ajoute": b.get("ajoute", ""),
            "langue": b.get("langue", ""),
            "fiche": (b.get("fiche") or {}).get("texte", "") if (b.get("fiche") or {}).get("tenu") is True else "",
            **anglais(b),
            **elements(b),
        }
        for b in (F.get("billets") or [])[:MAX]
    ]


def elements(b: dict) -> dict:
    """La fiche au format résumé, catégorie, éléments, dans les deux langues ; les citations restent internes. Vide pour une
    fiche d'avant ce format ou refusée."""
    f = b.get("fiche") or {}
    tenue = f if f.get("tenu") is True else {}
    en = tenue.get("en") or {}
    garde = lambda d: [{"champ": e["champ"], "texte": e["texte"]} for e in d.get("elements") or []]  # noqa: E731
    return {
        "resume": tenue.get("resume", ""),
        "categorie": tenue.get("categorie", ""),
        "elements": garde(tenue),
        "resume_en": en.get("resume", ""),
        "categorie_en": en.get("categorie", ""),
        "elements_en": garde(en),
    }


def anglais(b: dict) -> dict:
    """La version anglaise tenue (fil_anglais), écrite d'après la source ; vide sinon : la page anglaise ne la montre pas."""
    en = b.get("en") or {}
    tenu = en.get("tenu") is True
    return {"titre_en": en.get("titre", "") if tenu else "", "descriptif_en": en.get("texte", "") if tenu else ""}


def acces(env: Path) -> dict | None:
    try:
        lignes = env.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    v = dict(l.split("=", 1) for l in lignes if "=" in l and not l.lstrip().startswith("#"))
    v = {k.strip(): x.strip().strip('"').strip("'") for k, x in v.items()}
    if not all(v.get(k) for k in ("VPS_HOTE", "VPS_UTILISATEUR", "VPS_CLE")):
        return None
    v["VPS_CLE"] = os.path.expanduser(v["VPS_CLE"])
    return v


def paquet(corps: bytes, xml: bytes, maintenant: float | None = None, xml_en: bytes | None = None) -> bytes:
    """L'archive déposée sur le VPS, fichiers datés : nginx en tire Last-Modified et l'ETag (05.11.2026 : sans date,
    l'ETag ne dépendait que de la taille, et la page revalide le fil toutes les 10 secondes)."""
    import time  # noqa: PLC0415

    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as t:
        for nom, donnees in (
            ("fil.json", corps),
            ("fil.xml", xml),
            *((("fil_en.xml", xml_en),) if xml_en is not None else ()),
        ):
            info = tarfile.TarInfo(nom)
            info.size, info.mode, info.mtime = (
                len(donnees),
                0o644,
                int(maintenant if maintenant is not None else time.time()),
            )
            t.addfile(info, io.BytesIO(donnees))
    return tampon.getvalue()


def publier(base: Path, env: Path = ENV, marque: Path | None = None) -> str:
    """Dépose fil.json (copie publique) et fil.xml de `base` sur le VPS ; rend une ligne pour le journal."""
    marque = marque or RACINE / "cache" / f"fil_publie_{base.name}.sha"
    try:
        corps = json.dumps(
            public(json.loads((base / "fil.json").read_text(encoding="utf-8"))),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        xml = (base / "fil.xml").read_bytes()
        xml_en = (base / "fil_en.xml").read_bytes() if (base / "fil_en.xml").exists() else None  # le flux anglais
    except (OSError, ValueError) as e:
        return f"sans fil à déposer ({type(e).__name__})"
    empreinte = hashlib.sha256(corps + b"\0" + xml + (b"\0" + xml_en if xml_en is not None else b"")).hexdigest()
    if marque.exists() and marque.read_text().strip() == empreinte:
        return "inchangé"
    v = acces(env)
    if v is None:
        return f"sans accès au VPS ({env})"
    paquet_ = paquet(corps, xml, xml_en=xml_en)
    distant = (
        f"set -e; mkdir -p {DOSSIER_VPS}/.neuf; tar -C {DOSSIER_VPS}/.neuf -xf -; "
        f"mv {DOSSIER_VPS}/.neuf/fil.json {DOSSIER_VPS}/fil.json; mv {DOSSIER_VPS}/.neuf/fil.xml {DOSSIER_VPS}/fil.xml"
        + (f"; mv {DOSSIER_VPS}/.neuf/fil_en.xml {DOSSIER_VPS}/fil_en.xml" if xml_en is not None else "")
    )
    r = subprocess.run(
        [
            "ssh",
            "-i",
            v["VPS_CLE"],
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            f"{v['VPS_UTILISATEUR']}@{v['VPS_HOTE']}",
            distant,
        ],
        input=paquet_,
        capture_output=True,
        check=False,
    )
    if r.returncode:
        return f"échec du dépôt (ssh {r.returncode}) : {r.stderr.decode(errors='replace').strip()[:160]}"
    marque.parent.mkdir(parents=True, exist_ok=True)
    marque.write_text(empreinte)
    return f"déposé ({len(json.loads(corps)['billets'])} billets)"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    from newskit import config_sources as cfg  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.graphe_du_site())
    return ap.parse_args(argv)


def main() -> int:
    a = arguments()
    from newskit import fil  # noqa: PLC0415

    print(publier(fil.chemin_fil(a.graphe).parent))
    return 0


if __name__ == "__main__":
    sys.exit(main())

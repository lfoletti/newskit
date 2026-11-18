"""Les règles de rédaction apprises des refus de la relecture (mainteneur, 17.11.2026 : « les motifs récurrents ne
devraient-ils pas être consignés et portés en consigne lors des premières passes? » ; 18.11.2026 : « il manque
visiblement un mécanisme ajoutant des règles d'écriture pour éviter les motifs récurrents »).

refus_frequents porte en consigne des exemples récents, à la première rédaction seulement. Ici, une passe par jour
transforme chaque motif vu au moins SEUIL fois dans la fenêtre de refus_frequents, et encore sans règle, en une règle
générale, en français et en anglais : un appel au modèle, MAX_PAR_JOUR motifs au plus, les plus fréquents d'abord. Le
modèle rend une règle vide quand une règle du média couvre déjà le motif ; le motif est alors retenu sans règle.

Les règles vivent dans graphe/<graphe>/regles_apprises.json, à côté du fil, dans le dossier de données du média : le
fichier de règles du média peut être un lien vers un clone de code où le serveur n'écrit pas. lettre.regles les ajoute
aux règles du média, donc à toutes les rédactions et réécritures. Une règle reste jusqu'à ce que le mainteneur la retire
du fichier. Les règles ajoutées partent au mainteneur par courriel ; un envoi manqué ne retire rien.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from newskit import notifier_courriel as nc
from newskit import refus_frequents as rf

SEUIL = 5  # refus dans la fenêtre de refus_frequents pour qu'un motif devienne une règle
MAX_PAR_JOUR = 3  # motifs transformés par passe, au plus
EXEMPLE_MAX = 300
TITRES = {
    "fr": "Règles tirées des refus de la relecture :",
    "en": "Rules drawn from review refusals:",
}

REDACTION = """Tu écris les règles de rédaction d'un fil d'actualité sur {nom}. La relecture adverse refuse souvent les
billets et leurs fiches pour les motifs ci-dessous (fois : refus sur {jours} jours ; exemple : le dernier reproche).
Pour chaque motif, écris la règle générale qui l'évite dès la première rédaction :
- une phrase à l'impératif, 35 mots au plus, qui vaut pour tout billet ;
- sans le cas de l'exemple : ni son nom, ni sa date, ni son chiffre ;
- en français (fr) et en anglais (en), la même règle.
Si une règle de rédaction ci-dessous couvre déjà le motif, rends fr et en vides.
Rends {{"regles": [{{"n": numéro du motif, "fr": ..., "en": ...}}]}}.

Règles de rédaction :
{regles}
Motifs :
{motifs}"""
SCHEMA = {
    "type": "object",
    "properties": {
        "regles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "fr": {"type": "string"}, "en": {"type": "string"}},
                "required": ["n", "fr", "en"],
            },
        }
    },
    "required": ["regles"],
}


def chemin(graphe: str) -> Path:
    from newskit import fil  # noqa: PLC0415 — fil importe lettre, qui lit ce module

    return fil.chemin_fil(graphe).parent / "regles_apprises.json"


def charger(graphe: str) -> dict:
    """{jour, regles} ; un fichier absent ou illisible donne un état vide."""
    try:
        d = json.loads(chemin(graphe).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"jour": "", "regles": []}
    return d if isinstance(d, dict) and isinstance(d.get("regles"), list) else {"jour": "", "regles": []}


def texte(graphe: str, langue: str = "fr") -> str:
    """Le bloc des règles apprises d'une langue, ajouté aux règles du média (lettre.regles) ; vide sans règle."""
    lignes = [str(r.get(langue) or "").strip() for r in charger(graphe)["regles"] if isinstance(r, dict)]
    lignes = [x for x in lignes if x]
    return f"{TITRES[langue]}\n" + "".join(f"- {x}\n" for x in lignes) if lignes else ""


def candidats(graphe: str, deja: set[str], maintenant: datetime) -> list[tuple[int, str]]:
    """(fois, exemple) des motifs vus SEUIL fois au moins et encore sans règle, les plus fréquents d'abord."""
    try:
        motifs = rf.frequents(rf._entrees(graphe), maintenant)
    except (OSError, ValueError):
        return []
    return [(n, ex) for n, ex in motifs if n >= SEUIL and rf.cle(ex) not in deja][:MAX_PAR_JOUR]


def passer(graphe: str, maintenant: datetime | None = None) -> list[dict]:
    """La passe du jour : rend les règles ajoutées. Une seule passe par jour, même sans motif."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import fil  # noqa: PLC0415
    from newskit import lettre as lt  # noqa: PLC0415

    maintenant = maintenant or datetime.now()
    jour = f"{maintenant:%Y-%m-%d}"
    etat = charger(graphe)
    if etat.get("jour") == jour:
        return []
    deja = {str(r.get("cle")) for r in etat["regles"] if isinstance(r, dict)}
    motifs = candidats(graphe, deja, maintenant)
    ajoutees: list[dict] = []
    if motifs:
        nom = cfg.graphe_declare(graphe).get("libelle") or graphe
        r, _ = fil.demander(
            REDACTION.format(
                nom=nom,
                jours=rf.JOURS,
                regles=lt.regles(graphe),
                motifs="\n".join(
                    json.dumps({"n": i, "fois": n, "exemple": ex[:EXEMPLE_MAX]}, ensure_ascii=False)
                    for i, (n, ex) in enumerate(motifs)
                ),
            ),
            SCHEMA,
            graphe,
            "règles apprises",
        )
        for x in r.get("regles") or []:
            i = x.get("n")
            if not isinstance(i, int) or not 0 <= i < len(motifs):
                continue
            n, ex = motifs[i]
            regle = {
                "cle": rf.cle(ex),
                "fr": str(x.get("fr") or "").strip(),
                "en": str(x.get("en") or "").strip(),
                "vus": n,
                "exemple": ex,
                "ajoutee": jour,
            }
            etat["regles"].append(regle)
            if regle["fr"] and regle["en"]:
                ajoutees.append(regle)
    etat["jour"] = jour
    p = chemin(graphe)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
    if ajoutees:
        corps = "".join(
            f"- {r['fr']}\n  {r['en']}\n  ({r['vus']} refus ; dernier : {r['exemple']})\n\n" for r in ajoutees
        )
        if nc.envoyer(f"{graphe} : {len(ajoutees)} règle(s) de rédaction apprise(s)", corps) != 0:
            print(f"  règles apprises : courriel non envoyé ({len(ajoutees)} règle(s) gardée(s))", file=sys.stderr)
    return ajoutees

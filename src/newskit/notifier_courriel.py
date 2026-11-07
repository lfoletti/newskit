#!/usr/bin/env python3
"""Envoie le résumé de la dernière exécution de la routine par courriel.

Le rapport `ROUTINE.md` est écrit à chaque passage, mais un rapport qu'on ne
lit que quand on y pense ne remplit pas son office : un arrêt bloquant du
lundi peut attendre une semaine. Ce script en envoie la partie haute — état,
volumes, ce qui reste à faire, échecs — au destinataire déclaré au registre,
par SMTP, avec un mot de passe d'application lu au trousseau au moment de
l'envoi — ou, hors session (LaunchDaemon), dans `~/.secrets/courriel.env`.
Rien du secret ne passe ailleurs.

La routine l'appelle en fin de passage, y compris après un arrêt bloquant. Un
échec d'envoi est dit sur la sortie et rend un code non nul ; il n'arrête
rien, le commit étant déjà fait.

Usage :
    python3 -m newskit.notifier_courriel            # envoie la dernière exécution
    python3 -m newskit.notifier_courriel --montrer   # compose sans envoyer ; HTML dans cache/courriel.html
    python3 -m newskit.notifier_courriel --graphe <g> --essai <dossier>   # la lettre écrite dans <dossier>, sans envoi
    python3 -m newskit.notifier_courriel --graphe <g> --essai <dossier> --wip   # et la page de travail numérotée, son index

Le message part en deux versions, texte simple et HTML (fond gris clair, une
taille de police, deux graisses), le client de messagerie choisissant.
"""

from __future__ import annotations

import json
import re
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

RAPPORT = RACINE / "ROUTINE.md"
LETTRES = RACINE / "lettres"  # chaque lettre envoyée, gardée (suivi) : deploiement/lettres.py en fait sa page

from newskit import commun  # noqa: E402
from newskit import config_sources as cfg  # noqa: E402
from newskit import publier_fil  # noqa: E402


def lire_rapport(rapport: str) -> dict:
    """Ce que le résumé retient de ROUTINE.md : dernière ligne d'historique et deux sections."""
    lignes = rapport.split("\n")
    derniere = next((l for l in lignes if l.startswith("| 20")), "")
    cases = [c.strip() for c in derniere.strip("|").split("|")] if derniere else []
    # | date | état | fiches | nœuds | arêtes | nouvelles fiches | à faire |
    date, etat, fiches, noeuds, aretes, neuf, afaire = (cases + [""] * 7)[:7]

    def section(titre: str) -> list[str]:
        try:
            i = lignes.index(titre)
        except ValueError:
            return []
        out = []
        for l in lignes[i + 1 :]:
            if l.startswith("#"):
                break
            if l.startswith("- "):
                out.append(l[2:].strip())
        return out

    # Le passage précédent, pour dire de combien le graphe a bougé.
    precedente = next((l for l in lignes if l.startswith("| 20") and l != derniere), "")
    cases_p = [c.strip() for c in precedente.strip("|").split("|")] if precedente else []
    _, _, _, noeuds_p, aretes_p, _, _ = (cases_p + [""] * 7)[:7]

    def ecart(a: str, b: str) -> str:
        try:
            d = int(a) - int(b)
        except ValueError:
            return ""
        return f"{d:+d}"

    # Les fiches neuves : « **groupe (n)** » puis « - date — [titre](url) ».
    groupes: list[dict] = []
    try:
        i = next(k for k, l in enumerate(lignes) if l.startswith("### Nouvelles fiches"))
    except StopIteration:
        i = -1
    total_neuves = None
    modifiees = ""
    if i >= 0:
        m = re.search(r"\((\d+)\)", lignes[i])
        total_neuves = int(m.group(1)) if m else 0
        for l in lignes[i + 1 :]:
            if l.startswith("#"):
                break
            g = re.match(r"\*\*(.+?) \((\d+)\)\*\*$", l.strip())
            if g:
                groupes.append({"nom": g.group(1), "n": int(g.group(2)), "fiches": []})
                continue
            f = re.match(r"- (\S+) — \[(.+?)\]\((\S*)\)$", l.strip()) or re.match(r"- (\S+) — (.+)$", l.strip())
            if f and groupes:
                url = f.group(3) if f.lastindex and f.lastindex >= 3 else ""
                groupes[-1]["fiches"].append({"date": f.group(1), "titre": f.group(2), "url": url})
            elif l.strip().startswith("- … et") and groupes:
                groupes[-1]["reste"] = l.strip()[2:]
            elif "mise(s) à jour" in l:
                modifiees = l.strip()

    # Le déroulé : le bloc de code qui suit « ### Déroulé », tel quel.
    deroule = ""
    try:
        k = lignes.index("### Déroulé")
        debut = lignes.index("```", k) + 1
        fin = lignes.index("```", debut)
        deroule = "\n".join(lignes[debut:fin]).rstrip()
    except ValueError:
        pass

    # Une seule définition des fiches neuves : celles que le commit du passage verse, comptées dans leur section. La
    # colonne de l'historique disait « +0 » pour 87 fiches quand un passage en reprenait un autre (02.11.2026, B16).
    if total_neuves is not None:
        neuf = f"+{total_neuves}"
    titre_notions = next((l for l in lignes if l.startswith("### Nouvelles notions")), "### Nouvelles notions")
    m_n = re.search(r"\((\d+)\)", titre_notions)
    return {
        "date": date,
        "etat": etat,
        "fiches": fiches,
        "noeuds": noeuds,
        "aretes": aretes,
        "neuf": neuf,
        "afaire": afaire,
        "notions_neuves": int(m_n.group(1)) if m_n else None,
        "ecart_noeuds": ecart(noeuds, noeuds_p),
        "ecart_aretes": ecart(aretes, aretes_p),
        "notions": section(titre_notions),
        "assistes": section("### Ce qui demande une session assistée"),
        "echecs": section("### Échecs bloquants"),
        "alertes": section("### Alertes"),
        "autres_graphes": section("### Autres graphes"),
        "groupes": groupes,
        "total_neuves": total_neuves,
        "modifiees": modifiees,
        "deroule": deroule,
    }


def pour_le_graphe(objet: str, html: str, graphe: str) -> tuple[str, str]:
    """L'objet et l'en-tête du courriel d'un graphe à part : le nom du graphe après celui de l'édition."""
    nom = cfg.edition()["nom"]
    return (
        objet.replace(f"{nom} —", f"{nom} ({graphe}) —", 1),
        html.replace(f"{_echapper(nom)} — routine hebdomadaire", f"{_echapper(nom)} — graphe {graphe}", 1),
    )


def resumer(rapport: str) -> tuple[str, str]:
    """(objet, corps en texte simple) à partir de ROUTINE.md. Sans réseau, testable."""
    r = lire_rapport(rapport)
    date, etat, fiches, noeuds, aretes, neuf, afaire = (
        r["date"],
        r["etat"],
        r["fiches"],
        r["noeuds"],
        r["aretes"],
        r["neuf"],
        r["afaire"],
    )
    assistes, echecs = r["assistes"], r["echecs"]

    objet = f"{cfg.edition()['nom']} — {etat or 'sans état'}"
    if fiches:
        objet += f" — {fiches} fiches ({neuf})"
    if echecs:
        objet += f" — {len(echecs)} échec(s)"
    elif afaire and afaire != "0":
        objet += f" — {afaire} à faire"

    corps = [f"Routine du {date}", f"État : {etat}", ""]
    if fiches:
        en = r["ecart_noeuds"]
        ea = r["ecart_aretes"]
        corps += [
            f"Corpus : {fiches} fiches ({neuf} cette semaine)",
            f"Graphe : {noeuds} nœuds{f' ({en})' if en else ''}, {aretes} arêtes{f' ({ea})' if ea else ''}",
            "",
        ]
    if r["notions"]:
        compte = f" ({r['notions_neuves']})" if r.get("notions_neuves") is not None else ""
        corps += [f"Nouvelles notions au graphe, par thème{compte} :"] + [f"  - {n}" for n in r["notions"]] + [""]
    if echecs:
        corps += ["Échecs bloquants — rien n'a été committé :"] + [f"  - {e}" for e in echecs] + [""]
    if r["alertes"]:
        corps += ["Alertes :"] + [f"  - {a}" for a in r["alertes"]] + [""]
    if r["total_neuves"] is not None:
        corps.append(f"Nouvelles fiches : {r['total_neuves']}")
        for g in r["groupes"]:
            corps.append(f"  {g['nom']} ({g['n']})")
            for f in g["fiches"]:
                corps.append(f"    - {f['date']}  {f['titre']}" + (f"  {f['url']}" if f["url"] else ""))
            if g.get("reste"):
                corps.append(f"    - {g['reste']}")
        if r["modifiees"]:
            corps.append(f"  {r['modifiees']}")
        corps.append("")
    if r["autres_graphes"]:
        corps += ["Autres graphes :"] + [f"  - {g}" for g in r["autres_graphes"]] + [""]
    if assistes:
        corps += ["À faire à la main :"] + [f"  - {a}" for a in assistes] + [""]
    if not echecs and not assistes:
        corps += ["Rien à faire à la main.", ""]
    if r["deroule"]:
        corps += ["Déroulé complet :", "", r["deroule"], ""]
    if cfg.edition()["depot"]:
        corps += [f"Rapport : ROUTINE.md du dépôt {cfg.edition()['depot']}."]
    return objet, "\n".join(corps)


# --- Gabarit HTML --------------------------------------------------------
#
# Un minimum de mise en forme, demandé le 27.09.2026 : fond gris clair, une
# seule taille de police (14 px), deux graisses (gras pour les libellés et les
# titres, normal pour le reste). Tout est en styles en ligne et en tableaux :
# c'est ce que les clients de messagerie rendent de façon prévisible, Gmail
# compris, qui ignore les feuilles de style externes et une partie du <style>.

_POLICE = (
    "font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.5;"
)
_GRIS_FOND = "#eceef0"
_BLANC = "#ffffff"
_TEXTE = "#1f2328"
_TEXTE_DOUX = "#57606a"
_FILET = "#d0d7de"


def _echapper(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def composer_html(rapport: str) -> str:
    """Le même résumé que le texte, mis en page."""
    r = lire_rapport(rapport)
    etat = r["etat"] or "sans état"
    echec = etat.upper().startswith("ÉCHEC") or etat.upper().startswith("ECHEC")
    couleur_etat = "#a40e26" if echec else "#1a7f37"

    def ligne(libelle: str, valeur: str) -> str:
        return (
            f'<tr><td style="{_POLICE} color:{_TEXTE}; font-weight:bold; padding:4px 16px 4px 0; '
            f'white-space:nowrap; vertical-align:top;">{_echapper(libelle)}</td>'
            f'<td style="{_POLICE} color:{_TEXTE}; font-weight:normal; padding:4px 0;">{_echapper(valeur)}</td></tr>'
        )

    def titre(t: str) -> str:
        return f'<p style="{_POLICE} color:{_TEXTE}; font-weight:bold; margin:20px 0 6px 0;">{_echapper(t)}</p>'

    def liste(items: list[str]) -> str:
        lis = "".join(
            f'<li style="{_POLICE} color:{_TEXTE}; font-weight:normal; margin:0 0 6px 0;">{_echapper(i)}</li>'
            for i in items
        )
        return f'<ul style="margin:0; padding-left:20px;">{lis}</ul>'

    blocs = [
        f'<p style="{_POLICE} color:{_TEXTE}; font-weight:bold; margin:0 0 2px 0;">{_echapper(cfg.edition()["nom"])} — routine hebdomadaire</p>',
        f'<p style="{_POLICE} color:{_TEXTE_DOUX}; font-weight:normal; margin:0 0 16px 0;">{_echapper(r["date"])}</p>',
        f'<p style="{_POLICE} font-weight:bold; color:{couleur_etat}; margin:0 0 16px 0;">{_echapper(etat)}</p>',
    ]
    if r["fiches"]:
        en = r["ecart_noeuds"]
        ea = r["ecart_aretes"]
        blocs.append(
            '<table cellpadding="0" cellspacing="0" border="0">'
            + ligne("Corpus", f"{r['fiches']} fiches, {r['neuf']} cette semaine")
            + ligne(
                "Graphe",
                f"{r['noeuds']} nœuds{f' ({en})' if en else ''}, {r['aretes']} arêtes{f' ({ea})' if ea else ''}",
            )
            + "</table>"
        )
    if r["autres_graphes"]:
        blocs.append(
            '<table cellpadding="0" cellspacing="0" border="0" style="margin-top:4px;">'
            + "".join(
                ligne(g.split(" : ", 1)[0], g.split(" : ", 1)[1] if " : " in g else "") for g in r["autres_graphes"]
            )
            + "</table>"
        )
    if r["notions"]:
        compte = f" ({r['notions_neuves']})" if r.get("notions_neuves") is not None else ""
        blocs.append(titre(f"Nouvelles notions au graphe, par thème{compte}") + liste(r["notions"]))
    if r["echecs"]:
        blocs.append(titre("Échecs bloquants — rien n'a été committé") + liste(r["echecs"]))
    if r["alertes"]:
        blocs.append(titre("Alertes") + liste(r["alertes"]))
    if r["total_neuves"] is not None:
        blocs.append(titre(f"Nouvelles fiches ({r['total_neuves']})"))
        if not r["groupes"]:
            blocs.append(
                f'<p style="{_POLICE} color:{_TEXTE}; font-weight:normal; margin:0;">Aucune cette semaine.</p>'
            )
        for g in r["groupes"]:
            blocs.append(
                f'<p style="{_POLICE} color:{_TEXTE}; font-weight:bold; margin:12px 0 4px 0;">'
                f'{_echapper(g["nom"])} <span style="font-weight:normal; color:{_TEXTE_DOUX};">({g["n"]})</span></p>'
            )
            lis = []
            for f in g["fiches"]:
                t = _echapper(f["titre"])
                if f["url"]:
                    t = f'<a href="{_echapper(f["url"])}" style="color:{_TEXTE}; text-decoration:underline;">{t}</a>'
                lis.append(
                    f'<li style="{_POLICE} color:{_TEXTE}; font-weight:normal; margin:0 0 3px 0;">'
                    f'<span style="color:{_TEXTE_DOUX};">{_echapper(f["date"])}</span>&nbsp; {t}</li>'
                )
            if g.get("reste"):
                lis.append(
                    f'<li style="{_POLICE} color:{_TEXTE_DOUX}; font-weight:normal; list-style:none; margin-left:-20px;">{_echapper(g["reste"])}</li>'
                )
            blocs.append(f'<ul style="margin:0; padding-left:20px;">{"".join(lis)}</ul>')
        if r["modifiees"]:
            blocs.append(
                f'<p style="{_POLICE} color:{_TEXTE_DOUX}; font-weight:normal; margin:10px 0 0 0;">{_echapper(r["modifiees"])}</p>'
            )
    if r["assistes"]:
        blocs.append(titre("À faire à la main") + liste(r["assistes"]))
    if not r["echecs"] and not r["assistes"]:
        blocs.append(
            f'<p style="{_POLICE} color:{_TEXTE}; font-weight:normal; margin:16px 0 0 0;">Rien à faire à la main.</p>'
        )
    if r["deroule"]:
        # Le déroulé complet, en bas, en monospace : même taille, même fond gris
        # que la page, pour qui veut vérifier une étape sans ouvrir le dépôt.
        blocs.append(titre("Déroulé complet"))
        blocs.append(
            f'<pre style="font-family: SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; '
            f"line-height: 1.45; color:{_TEXTE}; background:{_GRIS_FOND}; border:1px solid {_FILET}; "
            f'border-radius:4px; padding:12px; margin:0; white-space:pre-wrap; word-break:break-word;">'
            f"{_echapper(r['deroule'])}</pre>"
        )
    if cfg.edition()["depot"]:
        blocs.append(
            f'<p style="{_POLICE} color:{_TEXTE_DOUX}; font-weight:normal; margin:24px 0 0 0; '
            f'padding-top:12px; border-top:1px solid {_FILET};">Rapport : ROUTINE.md du dépôt '
            f"{_echapper(cfg.edition()['depot'])}.</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0; padding:0; background:{_GRIS_FOND};">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_GRIS_FOND};">
<tr><td align="center" style="padding:24px 12px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px; background:{_BLANC}; border:1px solid {_FILET}; border-radius:6px;">
<tr><td style="padding:24px;">
{"".join(blocs)}
</td></tr></table>
</td></tr></table>
</body></html>
"""


SECRET_FICHIER = Path.home() / ".secrets" / "courriel.env"  # une ligne : SMTP_MOT_DE_PASSE=…
COMPTE_LETTRE = "lettre"  # le compte d'envoi de la lettre (credences.lettre, 06.11.2026)
DOSSIER_SECRETS = Path.home() / ".secrets"  # le fichier d'un compte qui en déclare un (`secret:`)


def mot_de_passe_fichier(fichier: Path | None = None) -> str:
    """Repli hors session : un LaunchDaemon ne lit pas le trousseau de la session
    à l'écran (routine du graphe financier, depuis le 02.10.2026). Le même fichier
    sert à la veille des plateformes de dollar_fountain. Droits 600 attendus."""
    try:
        for l in (fichier or SECRET_FICHIER).read_text(encoding="utf-8").splitlines():
            if l.startswith("SMTP_MOT_DE_PASSE="):
                return l.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


# Aucun courriel ne part par erreur ni empoisonné (mainteneur, 04.11.2026 : « assure-toi qu'aucun courriel ne puisse partir
# par erreur. on ne voudrait pas être hackés et faire partir une lettre empoisonnée »). Trois gardes : l'envoi demandé
# en toutes lettres (--envoyer), un destinataire connu (le registre ou la liste des abonnés), un HTML sans code actif.
OPTIONS = {"--graphe": 1, "--essai": 1, "--wip": 0, "--montrer": 0, "--envoyer": 0}
RE_ACTIF = re.compile(
    r"<\s*(?:script|iframe|form|object|embed|link|base)\b|<\s*meta[^>]*http-equiv|\son\w+\s*=|javascript:|vbscript:|"
    r"data:text/html|expression\s*\(",
    re.I,
)
RE_ADRESSE = re.compile(r"""\b(href|src)\s*=\s*["']([^"']*)["']""", re.I)


def options_refusees(argv: list[str]) -> str:
    """La raison de refuser la ligne de commande, vide si elle est valide : une option inconnue ne lance rien."""
    i = 0
    while i < len(argv):
        n = OPTIONS.get(argv[i])
        if n is None:
            return f"option inconnue : {argv[i]} (options : {', '.join(OPTIONS)})"
        if n and (i + 1 >= len(argv) or argv[i + 1].startswith("--")):
            return f"{argv[i]} attend une valeur"
        i += 1 + n
    if not {"--envoyer", "--essai", "--montrer"} & set(argv):
        return "rien à faire : --envoyer pour envoyer, --essai <dossier> ou --montrer pour lire sans envoyer"
    return ""


def destinataire_admis(vers: str, registre: str, admis=()) -> bool:
    """Le destinataire est l'adresse du registre, un abonné inscrit (diffusion.lire) ou une adresse de `admis` (celles que
    la diffusion a lues sur le serveur : abonnés du site, demandes à confirmer) ; aucune autre."""
    from newskit import diffusion  # noqa: PLC0415
    from email.utils import parseaddr  # noqa: PLC0415

    a = parseaddr(vers)[1].lower()
    return bool(a) and (
        a == parseaddr(registre)[1].lower()
        or a in {x["adresse"].lower() for x in diffusion.lire()}
        or a in {str(x).lower() for x in admis}
    )


def contenu_refuse(html: str, site: str | list[str]) -> str:
    """La raison de ne pas envoyer ce HTML, vide s'il est sain : pas de code actif (script, cadre, formulaire,
    attribut on…, javascript:) ; des liens en http(s) ou mailto seulement ; des images servies par un site de
    l'édition seul (`site` : une adresse ou plusieurs, le site d'avant un changement de nom compris)."""
    m = RE_ACTIF.search(html or "")
    if m:
        return f"code actif dans la lettre : {m.group(0)[:40]}"
    hotes = [
        h
        for h in (
            re.sub(r"^https?://", "", (s or "").rstrip("/")).split("/")[0].lower()
            for s in ([site] if isinstance(site, str) else site or [])
        )
        if h
    ]
    for attr, url in RE_ADRESSE.findall(html or ""):
        u = url.strip().lower()
        if attr.lower() == "src" and not any(u.startswith(f"https://{h}/") for h in hotes):
            return f"image hors du site : {url[:80]}"
        if attr.lower() == "href" and not re.match(r"(?:https?://|mailto:|#)", u):
            return f"lien refusé : {url[:80]}"
    return ""


def par_le_serveur(msg: EmailMessage) -> str:
    """Le courriel passé au serveur, qui l'envoie par le compte de la lettre (`kg-abonnement lettre`, 06.11.2026 :
    depuis le poste, Infomaniak refusait la lettre) ; rend la raison d'un refus, vide quand il est parti."""
    v = publier_fil.acces(publier_fil.ENV)
    if v is None:
        return "accès ssh au serveur absent"
    try:
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
                "sudo -n /usr/local/sbin/kg-abonnement lettre",
            ],
            input=msg.as_bytes(),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"ssh : {type(e).__name__}"
    try:
        issue = json.loads(r.stdout or b"{}").get("lettre")
    except ValueError:
        issue = None
    return "" if r.returncode == 0 and issue == "envoye" else f"serveur : {issue or 'ssh ' + str(r.returncode)}"


def envoyer(
    objet: str,
    corps: str,
    html: str = "",
    vers: str | None = None,
    admis=(),
    desinscription: str = "",
    compte: str = "courriel",
) -> int:
    """`compte` : un autre compte d'envoi du registre (`credences.<compte>` : de, smtp, port, secret), par exemple
    celui des inscriptions ; il garde le destinataire par défaut du compte « courriel ». Un compte non déclaré laisse
    le compte « courriel » envoyer. Un compte déclaré sans mot de passe n'envoie rien : aucun autre compte ne prend
    sa place, l'expéditeur est celui que le registre nomme."""
    credences = cfg.charger()["credences"]
    c = credences.get("courriel") or {}
    propre = (credences.get(compte) or {}) if compte != "courriel" else {}
    c = {**c, **propre}
    serveur = propre.get("par") == "serveur"  # le serveur envoie (la lettre, 06.11.2026) : ni SMTP ni mot de passe ici
    for champ in ("vers", "de") if serveur else ("vers", "de", "smtp", "session"):
        if not c.get(champ):
            print(f"  registre : credences.courriel.{champ} manquant, pas d'envoi", file=sys.stderr)
            return 1
    if vers and not destinataire_admis(vers, commun.adresse(c["vers"]), admis):
        print(f"ALERTE : destinataire inconnu ({vers}) : ni le registre ni un abonné, pas d'envoi", file=sys.stderr)
        return 1
    raison = contenu_refuse(html, cfg.sites_de_l_edition())
    if raison:
        print(f"ALERTE : lettre refusée, {raison} ; pas d'envoi", file=sys.stderr)
        return 1
    if serveur:
        mdp = ""
    elif propre.get("secret"):  # un mot de passe d'appareil dans un fichier du poste, lisible par un daemon
        fichier = DOSSIER_SECRETS / str(propre["secret"])
        mdp = mot_de_passe_fichier(fichier)
        if not mdp:
            print(
                f"  compte « {compte} » : pas de SMTP_MOT_DE_PASSE dans {commun.masquer_chemins(str(fichier))} ; pas d'envoi",
                file=sys.stderr,
            )
            return 1
    else:
        etat = cfg.etat_session(c["session"])
        mdp = (cfg.session(c["session"]) or "") if etat == "deposee" else mot_de_passe_fichier()
    if not mdp and not serveur:
        print(
            f"  mot de passe SMTP « {c['session']} » : {etat} au trousseau, et pas de "
            f"SMTP_MOT_DE_PASSE dans {SECRET_FICHIER} ; pas d'envoi",
            file=sys.stderr,
        )
        return 1
    msg = EmailMessage()
    msg["Subject"] = objet
    de, vers = commun.adresse(c["de"]), vers or commun.adresse(c["vers"])  # `vers` : un abonné (diffusion)
    msg["From"] = de
    msg["To"] = vers
    from email.utils import parseaddr  # noqa: PLC0415

    if desinscription.startswith("https://"):  # un abonné du site : son lien, et le retrait en un clic (RFC 8058)
        msg["List-Unsubscribe"] = f"<{desinscription}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    else:
        msg["List-Unsubscribe"] = (
            f"<mailto:{parseaddr(de)[1]}?subject=Désinscription>"  # pied de la lettre (03.11.2026)
        )
    msg.set_content(corps)
    if html:
        msg.add_alternative(html, subtype="html")
    if serveur:
        raison = par_le_serveur(msg)
        if raison:
            print(f"  envoi refusé : {raison}", file=sys.stderr)
            return 1
        print(f"  courriel envoyé à {vers} par le serveur : {objet}")
        return 0
    try:
        with smtplib.SMTP(c["smtp"], int(c.get("port", 587)), timeout=60) as s:
            s.starttls()
            s.login(de, mdp)
            s.send_message(msg)
    except Exception as e:
        # Le message d'erreur SMTP ne contient pas le mot de passe ; il dit en
        # général pourquoi (authentification refusée, réseau).
        print(f"  envoi refusé : {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1
    print(f"  courriel envoyé à {vers} : {objet}")
    return 0


def nom_lisible(graphe: str) -> str:
    """« Gouvernance de l'IA » : le libellé du graphe au registre, avant son tiret."""
    try:
        return str(cfg.graphe_declare(graphe).get("libelle") or graphe).split(" — ")[0].strip() or graphe
    except Exception:  # noqa: BLE001
        return graphe


def ecrire_essai(dossier: Path, objet: str, corps: str, html: str, cout: float | None = None) -> None:
    """Un essai : la lettre écrite dans `dossier` (lettre.html, lettre.txt, objet.txt, cout.txt), sans rien envoyer."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "lettre.html").write_text(html, encoding="utf-8")
    (dossier / "lettre.txt").write_text(corps, encoding="utf-8")
    (dossier / "objet.txt").write_text(objet + "\n", encoding="utf-8")
    if cout is not None:
        (dossier / "cout.txt").write_text(
            f"appel au modèle de la lettre : {cout:.4f} $ (poste lettre au registre des coûts)\n", encoding="utf-8"
        )


def ecrire_wip(dossier: Path, L: dict) -> Path:
    """Le rendu de travail : lettre_wip.html (un numéro de référence par élément dans la marge) et l'index
    lettre_<graphe>_<date>.json (numéro → fiche, url, titre, rubrique), pour pointer un élément de la lettre."""
    import json  # noqa: PLC0415
    from newskit import lettre as lt  # noqa: PLC0415

    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "lettre_wip.html").write_text(lt.html(L, wip=True), encoding="utf-8")
    p = dossier / f"lettre_{L['graphe']}_{L['envoi']}.json"
    p.write_text(
        json.dumps(
            {"graphe": L["graphe"], "envoi": L["envoi"], "objet": lt.objet(L), "elements": lt.index(L)},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return p


PRIVES_DE_LA_LETTRE = ("expediteur",)  # champs du courriel jamais gardés sous lettres/ (dépôt public)


def garder_lettre(L: dict) -> Path:
    """La lettre envoyée, gardée sous lettres/<graphe>/<envoi>.json : sa page web <site>/lettres/<graphe>/<envoi>,
    vers laquelle mènent les liens de la lettre, en est faite sur le VPS (deploiement/lettres.py, lot 2, 03.11.2026)."""
    import json  # noqa: PLC0415

    p = (
        LETTRES / L["graphe"] / f"{L['envoi']}{'-weekly' if L.get('periode') == 'semaine' else ''}.json"
    )  # la weekly du dimanche à côté de sa daily
    p.parent.mkdir(parents=True, exist_ok=True)
    # Sans l'adresse d'expédition : le pre-push refuse un secret dans un fichier suivi (mainteneur, 04.11.2026).
    garde = {k: v for k, v in L.items() if k not in PRIVES_DE_LA_LETTRE}
    p.write_text(json.dumps(garde, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return p


def main() -> int:
    global RAPPORT
    refus = options_refusees(sys.argv[1:])
    if refus:
        print(f"  {refus} ; rien n'est composé ni envoyé", file=sys.stderr)
        return 2
    graphe = sys.argv[sys.argv.index("--graphe") + 1] if "--graphe" in sys.argv else ""
    if graphe and graphe != cfg.GRAPHE_PRINCIPAL:
        RAPPORT = cfg.chemins_du_graphe(graphe)["rapport"]
    if not RAPPORT.exists():
        print(f"  {RAPPORT.name} absent : rien à résumer", file=sys.stderr)
        return 1
    texte = RAPPORT.read_text(encoding="utf-8")
    # La lettre (B16, 03.11.2026) quand le dernier passage a le format qui la permet ; sinon le résumé d'avant.
    # Jamais bloquant : une lettre qui échoue laisse partir le résumé.
    lettre_ = None
    try:
        from newskit import lettre as lt  # noqa: PLC0415

        lettre_ = lt.lettre_du_graphe(
            graphe or cfg.GRAPHE_PRINCIPAL, texte, nom=nom_lisible(graphe or cfg.GRAPHE_PRINCIPAL)
        )
    except Exception as e:  # noqa: BLE001
        print(f"  lettre non composée ({type(e).__name__} {str(e)[:120]}) : le résumé part à sa place", file=sys.stderr)
    if lettre_ is not None and lettre_.get("redige") is False:
        # Une lettre non rédigée ne part pas, ni son résumé : le passage suivant écrit la sienne (mainteneur, 04.11.2026).
        print("ALERTE : lettre non rédigée (limite d'usage ou plafond du modèle) : rien n'est envoyé", file=sys.stderr)
        return 0
    if lettre_ is not None:
        c_ = cfg.charger()["credences"].get(COMPTE_LETTRE) or {}
        lettre_.setdefault("expediteur", commun.adresse(c_["de"]) if c_.get("de") else "")
        # La daily est la même pour tous, le mainteneur compris (mainteneur, 04.11.2026) ; il reçoit en plus la WIP (wip()).
        objet, corps, html = lt.objet(lettre_), lt.texte(lettre_), lt.html(lettre_)
    else:
        objet, corps = resumer(texte)
        html = composer_html(texte)
        if graphe and graphe != cfg.GRAPHE_PRINCIPAL:
            objet, html = pour_le_graphe(objet, html, graphe)
    if "--essai" in sys.argv:
        dossier = Path(sys.argv[sys.argv.index("--essai") + 1])
        ecrire_essai(dossier, objet, corps, html, lettre_["cout_lettre"] if lettre_ else None)
        if lettre_ is not None and "--wip" in sys.argv:
            ecrire_wip(dossier, lettre_)
        print(f"Objet : {objet}\n(essai, rien n'est envoyé : lettre écrite dans {dossier})")
        return 0
    if "--montrer" in sys.argv:
        apercu = RACINE / "cache" / "courriel.html"
        apercu.parent.mkdir(parents=True, exist_ok=True)
        apercu.write_text(html, encoding="utf-8")
        print(f"Objet : {objet}\n\n{corps}\n\n(version HTML écrite dans {apercu.relative_to(RACINE)})")
        return 0
    if lettre_ is not None:
        garder_lettre(lettre_)
    code = envoyer(objet, corps, html, compte=COMPTE_LETTRE) if lettre_ is not None else envoyer(objet, corps, html)
    if lettre_ is not None and code == 0:
        lettre_["selection"] = selection(lettre_, graphe or cfg.GRAPHE_PRINCIPAL)
        code = envoyer(*wip(lettre_))
    return code


def selection(L: dict, graphe: str) -> list[dict]:
    """Les sujets d'enquête proposés par le Sélectionneur pour les Coulisses de la WIP (mainteneur, 04.11.2026) ; jamais
    bloquant : une panne laisse partir la WIP sans eux."""
    try:
        from newskit import selectionneur  # noqa: PLC0415

        return selectionneur.proposer(L, graphe)
    except Exception as e:  # noqa: BLE001
        print(f"  sélection non faite ({type(e).__name__} {str(e)[:120]})", file=sys.stderr)
        return []


def wip(L: dict) -> tuple[str, str, str]:
    """La WIP du mainteneur, à l'adresse du registre seule : la daily, chaque élément numéroté en fin de texte pour le
    désigner en approfondissement, et les Coulisses (le passage, les chiffres du graphe, la santé) en bas de page
    (mainteneur, 04.11.2026). (objet, texte, html)."""
    from newskit import lettre as lt  # noqa: PLC0415

    return ("[WIP] " + lt.objet(L), lt.texte(L, numeros=True, coulisses=True), lt.html(L, numeros=True, coulisses=True))


if __name__ == "__main__":
    sys.exit(main())

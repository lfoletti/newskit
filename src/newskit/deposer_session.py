#!/usr/bin/env python3
"""Déposer une session au trousseau sans la tronquer, et dire ce qui a été reçu.

Pourquoi ce script existe. Le 27.09.2026, deux sessions déposées par
`security add-generic-password -w` depuis un terminal SSH ont été refusées par
les deux sites, alors que les entrées existaient bien. La cause probable est
le terminal lui-même : en mode canonique, une ligne saisie ou collée est
tronquée à 1 024 octets (MAX_CANON), sans message, et un en-tête `cookie`
dépasse couramment cette taille. `security` lit sa valeur par ce chemin.

Ici, la valeur est lue sur le terminal en mode non canonique, sans écho, donc
sans plafond, puis remise à `security`. Avant de déposer, le script dit ce
qu'il a reçu : la longueur, et les **noms** des cookies présents — jamais leur
valeur. Un en-tête sans le cookie de session attendu est refusé avant d'entrer
au trousseau : une entrée fausse déposée avec succès est le défaut qu'on
vient de payer.

Usage, dans un terminal réel où le trousseau est déverrouillé :
    python3 -m newskit.deposer_session <session>
    python3 -m newskit.deposer_session medium
    python3 -m newskit.deposer_session medium --etat    # longueur et noms de l'entrée déposée
    python3 -m newskit.deposer_session courriel_smtp    # un mot de passe (genre: mot_de_passe au registre)

Le trousseau doit être déverrouillé dans la session courante (en SSH :
`security unlock-keychain ~/Library/Keychains/login.keychain-db`).
"""

from __future__ import annotations

import subprocess
import sys
import termios
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402

def cookies_attendus(nom_session: str) -> tuple[str, ...]:
    """Les cookies sans lesquels le site ne reconnaît pas de session : `cookies:` de la session au registre (N2). Les
    autres cookies (préférences, mesure d'audience) peuvent manquer sans conséquence."""
    s = (cfg.charger()["credences"].get("sessions") or {}).get(nom_session) or {}
    return tuple(s.get("cookies") or ())


def noms_de_cookies(en_tete: str) -> list[str]:
    """Les noms d'un en-tête `cookie`, sans les valeurs."""
    noms = []
    for morceau in en_tete.split(";"):
        nom = morceau.split("=", 1)[0].strip()
        if nom:
            noms.append(nom)
    return noms


def genre(nom_session: str) -> str:
    """`cookie` (défaut) ou `mot_de_passe`, selon le registre."""
    s = (cfg.charger()["credences"].get("sessions") or {}).get(nom_session) or {}
    return s.get("genre", "cookie")


def examiner(nom_session: str, valeur: str, genre_: str = "cookie") -> tuple[bool, str]:
    """(acceptable, diagnostic) pour une valeur reçue. Ne cite jamais la valeur."""
    v = valeur.strip()
    if not v:
        return False, "rien reçu"
    if genre_ == "mot_de_passe":
        # Un mot de passe d'application Google fait 16 lettres, que Google
        # affiche par groupes de quatre séparés d'espaces : les espaces sont
        # retirés, le reste doit être de l'ASCII imprimable.
        v = v.replace(" ", "")
        bizarres = sorted({c for c in v if ord(c) < 33 or ord(c) > 126})
        if bizarres:
            return False, f"{len(v)} caractères, {len(bizarres)} hors ASCII imprimable : {bizarres[:5]!r}"
        return True, f"{len(v)} caractères"
    noms = noms_de_cookies(v)
    attendus = cookies_attendus(nom_session)
    courts = [n if len(n) <= 30 else n[:27] + '…' for n in noms[:12]]
    detail = f"{len(v)} caractères, {len(noms)} cookie(s) : {', '.join(courts)}"
    if len(noms) > 12:
        detail += ", …"
    if v.lower().startswith("cookie:"):
        return False, detail + "\n  l'en-tête commence par « cookie: » : coller la valeur seule, sans le nom de l'en-tête"
    if "=" not in v:
        return False, detail + "\n  ce n'est pas un en-tête cookie (aucun nom=valeur)"
    bizarres = sorted({c for c in v if ord(c) < 32 or ord(c) > 126})
    if bizarres:
        # Un en-tête HTTP est de l'ASCII imprimable ; un caractère hors de cette
        # plage (guillemet typographique, espace insécable, caractère de
        # contrôle) fait répondre 400 au serveur, ce qui ressemble à un refus.
        return False, detail + f"\n  {len(bizarres)} caractère(s) hors ASCII imprimable : {bizarres[:5]!r}"
    if attendus and not any(a in noms for a in attendus):
        return False, detail + (f"\n  le cookie de session « {attendus[0]} » manque : la valeur ne vient pas "
                                f"d'une requête connectée à ce site")
    if len(v) in (1023, 1024):
        return False, detail + "\n  longueur exactement 1 024 : tronquée par le terminal, à recoller par ce script"
    return True, detail


def lire_sans_plafond(invite: str) -> str:
    """Lit une ligne sur le terminal sans écho ni plafond de 1 024 octets."""
    try:
        tty = open("/dev/tty", "r+b", buffering=0)
    except OSError:
        sys.exit("  aucun terminal : ce script se lance dans un terminal réel")
    fd = tty.fileno()
    ancien = termios.tcgetattr(fd)
    nouveau = termios.tcgetattr(fd)
    nouveau[3] &= ~(termios.ICANON | termios.ECHO)
    tty.write(invite.encode())
    octets = bytearray()
    try:
        termios.tcsetattr(fd, termios.TCSAFLUSH, nouveau)
        while True:
            c = tty.read(1)
            if not c or c in (b"\n", b"\r"):
                break
            if c == b"\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if c in (b"\x7f", b"\x08"):
                if octets:
                    octets.pop()
                continue
            octets += c
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, ancien)
        tty.write(b"\n")
        tty.close()
    return octets.decode("utf-8", "replace")


TROUSSEAU = Path.home() / "Library" / "Keychains" / "login.keychain-db"


def trousseau_ouvert() -> bool:
    """Le trousseau de connexion répond-il sans interaction ?

    En SSH, chaque connexion est une session de sécurité neuve où le trousseau
    est verrouillé : `security` répond alors « User interaction is not
    allowed ». Le 27.09.2026, une entrée a été supprimée puis n'a pas pu être
    recréée pour cette raison. On vérifie donc avant de toucher à quoi que ce
    soit, et on déverrouille à l'écran si nécessaire.
    """
    r = subprocess.run(["security", "show-keychain-info", str(TROUSSEAU)],
                       capture_output=True, text=True)
    return "not allowed" not in (r.stderr or "")


def ouvrir_trousseau() -> bool:
    if trousseau_ouvert():
        return True
    print("  le trousseau est verrouillé dans cette session ; mot de passe de session macOS :")
    r = subprocess.run(["security", "unlock-keychain", str(TROUSSEAU)])
    if r.returncode != 0 or not trousseau_ouvert():
        print("  déverrouillage refusé, rien n'a été touché.", file=sys.stderr)
        return False
    return True


def deposer(nom_session: str, valeur: str) -> int:
    conf = cfg.charger()
    c = conf["credences"]
    compte = (c.get("trousseau") or {}).get("compte", cfg.edition()["nom"])
    service = c["sessions"][nom_session]["service"]
    # Pas de `-U` : mettre à jour une entrée existante en modifie les droits
    # d'accès, et cette opération exige une confirmation à l'écran même dans
    # un trousseau déverrouillé (« SecKeychainItemSetAccess: User interaction
    # is not allowed », vu en SSH le 27.09.2026). Supprimer puis recréer ne
    # demande rien.
    if not ouvrir_trousseau():
        return 1
    if cfg.etat_session(nom_session) != "absente":
        r = subprocess.run(["security", "delete-generic-password", "-a", compte, "-s", service],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  l'ancienne entrée n'a pas pu être supprimée : {r.stderr.strip()}", file=sys.stderr)
            return 1
        print("  ancienne entrée supprimée")
    # La valeur passe en argument de `security`, comme le fait déjà la variante
    # `-w "$(pbpaste)"` du registre : brièvement visible dans la table des
    # processus, jamais dans un fichier ni dans l'historique du shell.
    r = subprocess.run(["security", "add-generic-password", "-a", compte, "-s", service,
                        "-T", "/usr/bin/security", "-w", valeur],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  security a refusé : {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"  déposée sous « {service} »")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    nom = args[0]
    sessions = cfg.charger()["credences"]["sessions"]
    if nom not in sessions:
        print(f"  session inconnue : {nom} (déclarées : {', '.join(sessions)})", file=sys.stderr)
        return 2
    if "--etat" in sys.argv:
        if not ouvrir_trousseau():
            return 1
        etat = cfg.etat_session(nom)
        if etat != "deposee":
            print(f"  {nom} : {etat}")
            return 1
        ok, diag = examiner(nom, cfg.session(nom) or "", genre(nom))
        print(f"  {nom} : {diag}")
        return 0 if ok else 1
    if not ouvrir_trousseau():
        return 1
    g = genre(nom)
    if g == "mot_de_passe":
        print(f"Session « {nom} » — coller le mot de passe, puis Entrée. Rien ne s'affiche pendant la saisie.")
        valeur = lire_sans_plafond("  mot de passe : ").replace(" ", "")
    else:
        print(f"Session « {nom} » — coller la valeur de l'en-tête `cookie` d'une requête connectée,")
        print("puis Entrée. Rien ne s'affiche pendant la saisie.")
        valeur = lire_sans_plafond("  cookie : ")
    ok, diag = examiner(nom, valeur, g)
    print(f"  reçu : {diag}")
    if not ok:
        print("  rien n'a été déposé.")
        return 1
    return deposer(nom, valeur)


if __name__ == "__main__":
    sys.exit(main())

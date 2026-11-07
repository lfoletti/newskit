#!/usr/bin/env python3
"""Le témoin absurde (NEWSKIT.md, N4 ; CHANTIER_GENERIQUE.md § 4.3) : le moteur tourne sur une copie de lui-même,
avec un graphe sans rapport (apiculture-medievale), sans toucher au réel.

Tranche 1, l'étanchéité :
- `preparer(dossier)` copie `scripts/` et `skill/` dans `dossier/code` (un dépôt git sans distant), écrit un registre
  à un seul graphe dans `dossier/donnees` (la racine des données, NEWSKIT_DONNEES), une maison vide, un faux `claude`
  et le canari ;
- `environnement(t)` : HOME temporaire (les `Path.home()` du moteur y tombent), faux `claude` en tête du PATH, canari
  par PYTHONPATH dans chaque processus Python, PYTHONHASHSEED=0, HF_HUB_OFFLINE=1, pas de bytecode écrit ;
- le canari (`sitecustomize`) refuse et journalise toute écriture sous les racines réelles (le code, les données,
  la maison) hors du dossier du témoin, et toute connexion hors de la boucle locale ;
- le faux `claude` rend la réponse enregistrée sous `tests/temoin/reponses/<empreinte>.json`, l'empreinte étant
  sha256(modèle, schéma, consigne aux blancs normalisés) ; une consigne inconnue va dans `manquantes/` et rend 98.

- `ssh`, `scp`, `rsync`, `curl`, `wget` sont remplacés en tête du PATH par un refus journalisé (06.11.2026 : remis à
  la vraie maison, le témoin lisait la table des dépôts du VPS par ssh).

Limite connue : le canari ne voit que les processus Python ; un autre binaire (git, sqlite3, cortexdb) n'est borné que
par HOME, le PATH et ses arguments.

    python3 -m newskit.temoin preparer <dossier>        # prépare et imprime l'environnement
    python3 -m newskit.temoin reenregistrer <dossier> [--plafond 30]   # paie les consignes manquantes
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import site
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines (NEWSKIT.md, N1)

GRAPHE = "apiculture-medievale"
CONSIGNE_INCONNUE = 98
RESEAU_COUPE = 97
RESEAU = ("ssh", "scp", "rsync", "curl", "wget")  # binaires qui échappent au canari : remplacés dans le PATH
TEMOIN = CODE / "donnees_temoin"  # les fiches absurdes et les réponses enregistrées, versionnées
COPIES = ()  # le paquet est copié entier sous code/newskit (N5)
# La date des fiches du témoin qui n'en écrivent pas : celle de leur enregistrement (06.11.2026). Elle entre dans les
# consignes ; fixée, une copie neuve du moteur rejoue les mêmes réponses (07.11.2026).
DATE_DES_FICHES = dt.date(2026, 11, 6)


@dataclass(frozen=True)
class Temoin:
    dossier: Path
    reponses: Path

    @property
    def code(self) -> Path:
        return self.dossier / "code"

    @property
    def donnees(self) -> Path:
        return self.dossier / "donnees"

    @property
    def maison(self) -> Path:
        return self.dossier / "maison"

    @property
    def bin(self) -> Path:
        return self.dossier / "bin"

    @property
    def canari(self) -> Path:
        return self.dossier / "canari"

    @property
    def journal(self) -> Path:
        return self.dossier / "canari.jsonl"

    @property
    def manquantes(self) -> Path:
        return self.dossier / "manquantes"


def empreinte(modele: str, schema: str, consigne: str) -> str:
    """La clé d'une réponse enregistrée : le modèle, le schéma et la consigne aux blancs normalisés."""
    return hashlib.sha256(
        "\n".join((modele or "", schema or "", " ".join((consigne or "").split()))).encode("utf-8")
    ).hexdigest()


def sources_reseau() -> list[dict]:
    """Une source par collecteur réseau, sur des hôtes `.invalid` : réseau coupé, chacune doit dire son refus
    (anticorps test_refus_n_est_pas_absence)."""
    base = {"abonnement": "gratuit", "dense": True, "graphe": GRAPHE, "posture": "recherche"}
    return [
        {
            **base,
            "cle": "ruchers_rss",
            "plateforme": "Flux RSS",
            "nom": "Gazette des ruchers",
            "dossier": "ruchers/rss",
            "flux": "https://gazette.ruchers.invalid/rss",
            "site": "https://gazette.ruchers.invalid",
        },
        {
            **base,
            "cle": "ruchers_liste",
            "plateforme": "Page de liste",
            "nom": "Annales de Valmiel",
            "dossier": "ruchers/liste",
            "liste": "https://annales.valmiel.invalid/",
            "chemins": r"valmiel\.invalid/\d+",
            "site": "https://annales.valmiel.invalid",
        },
        {
            **base,
            "cle": "ruchers_plan",
            "plateforme": "Plan de site",
            "nom": "Archives de Hohenruche",
            "dossier": "ruchers/plan",
            "plan": "https://archives.hohenruche.invalid/sitemap.xml",
            "chemins": r"hohenruche\.invalid/[a-z-]+/$",
            "site": "https://archives.hohenruche.invalid",
        },
        {
            **base,
            "cle": "ruchers_revue",
            "plateforme": "Revue",
            "nom": "Revue d'apiculture médiévale",
            "dossier": "ruchers/revue",
            "issn": "0000-0000",
        },
        {
            **base,
            "cle": "ruchers_openalex",
            "plateforme": "OpenAlex",
            "nom": "OpenAlex, essaimage",
            "dossier": "ruchers/openalex",
            "jours": 365,
            "par_requete": 4,
            "mots_cles": ["swarm", "beeswax"],
            "requetes": ["medieval beekeeping swarm"],
        },
        {
            **base,
            "cle": "ruchers_github",
            "plateforme": "GitHub",
            "nom": "GitHub, ruchers",
            "dossier": "ruchers/github",
            "jours": 30,
            "par_requete": 4,
            "etoiles_min": 1,
            "requetes": ["topic:beekeeping"],
        },
        {
            **base,
            "cle": "ruchers_hf",
            "plateforme": "HuggingFace",
            "nom": "HuggingFace, ruchers",
            "dossier": "ruchers/hf",
            "jours": 30,
            "par_requete": 4,
            "activite_min": 1,
            "genres": ["datasets"],
            "requetes": ["beekeeping"],
        },
    ]


def registre(reseau: bool = False) -> dict:
    """Le registre du témoin : un graphe, une source « Dossier local » sur ses fiches absurdes ; `reseau`, en plus
    une source par collecteur réseau (sources_reseau)."""
    fichiers = sorted(p.name for p in (TEMOIN / "dossier").glob("*.md"))
    return {
        "version": 1,
        "edition": {
            "nom": "temoin",
            "principal": GRAPHE,
            "graphe_du_site": GRAPHE,
            "domaine_principal": "medieval beekeeping",
            "launchd": "local.temoin",
        },
        "graphes": {
            GRAPHE: {"libelle": "Apiculture médiévale", "domaine": "medieval beekeeping: swarming, wax, apiary law"}
        },
        "sources": [
            {
                "cle": "ruchers_dossier",
                "plateforme": "Dossier local",
                "nom": "Carnets des ruchers",
                "dossier": "ruchers/dossier",
                "abonnement": "interne",
                "dense": True,
                "graphe": GRAPHE,
                "racine": "dossier_local",
                "fichiers": fichiers,
            }
        ]
        + (sources_reseau() if reseau else []),
    }


def photo_du_reel() -> dict:
    """L'état du vrai registre des coûts et de la vraie marque de limite avant un passage du témoin (fuites_vers_le_reel)."""
    reg, marque = RACINE / "cache" / "couts.jsonl", RACINE / "cache" / "limite_atteinte"
    return {
        "taille": reg.stat().st_size if reg.exists() else 0,
        "marque": (marque.stat().st_size, marque.stat().st_mtime_ns) if marque.exists() else None,
    }


def fuites_vers_le_reel(photo: dict, graphes_reels: set[str] | None = None) -> list[str]:
    """Ce que le témoin a laissé dans le réel depuis `photo` : une ligne neuve du vrai registre des coûts qui ne vient
    d'aucun vrai graphe, une vraie marque de limite posée ou touchée. Un rattrapage ou une routine écrivent au vrai
    registre pendant le passage (07.11.2026) : le registre bouge, seule une ligne étrangère aux vrais graphes est une
    fuite."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    reels = set(cfg.graphes()) if graphes_reels is None else graphes_reels
    reg, marque = RACINE / "cache" / "couts.jsonl", RACINE / "cache" / "limite_atteinte"
    out = []
    if reg.exists():
        with reg.open("rb") as f:
            f.seek(photo["taille"] if reg.stat().st_size >= photo["taille"] else 0)
            for brut in f.read().decode("utf-8", errors="replace").splitlines():
                try:
                    x = json.loads(brut)
                except ValueError:
                    continue
                if x.get("graphe") not in reels:
                    out.append(f"couts.jsonl : {brut[:160]}")
    m = (marque.stat().st_size, marque.stat().st_mtime_ns) if marque.exists() else None
    if m != photo["marque"]:
        out.append(f"limite_atteinte : {photo['marque']} -> {m}")
    return out


def ecrire_dossier(donnees: Path, reg: dict) -> None:
    """Le dossier du média du témoin, premier client de config.yaml (NEWSKIT.md, § Le dossier du média) : l'identité et
    les graphes dans `config.yaml`, les sources dans `sources.yaml`, qu'il nomme."""
    config = {
        "version": 1,
        "media": {**reg["edition"], "langue": "fr"},
        "sources": "sources.yaml",
        "graphes": reg["graphes"],
    }
    import yaml  # noqa: PLC0415

    sources = {k: v for k, v in reg.items() if k not in ("edition", "graphes")}
    for nom, d in (("config.yaml", config), ("sources.yaml", sources)):
        (donnees / nom).write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")


FAUX_CLAUDE = '''#!{python}
"""Le faux claude du témoin (newskit.temoin) : rejoue une réponse enregistrée, sinon 98."""
import hashlib, json, os, sys
a = sys.argv[1:]
def opt(nom):
    return a[a.index(nom) + 1] if nom in a and a.index(nom) + 1 < len(a) else ""
consigne = sys.stdin.read() if not sys.stdin.isatty() else ""
modele, schema = opt("--model"), opt("--json-schema")
cle = hashlib.sha256("\\n".join((modele, schema, " ".join(consigne.split()))).encode("utf-8")).hexdigest()
import fcntl
with open(os.environ["TEMOIN_APPELS"], "a+", encoding="utf-8") as f:     # chaque appel, pour les anticorps ; numéroté
    fcntl.flock(f, fcntl.LOCK_EX)                                      # sous verrou : les appels parallèles se suivent
    f.seek(0)
    deja = sum(1 for _ in f)
    f.write(json.dumps({{"cle": cle, "argv": a, "taille": len(consigne)}}, ensure_ascii=False) + "\\n")
if os.environ.get("TEMOIN_TUER_APRES") and deja + 1 == int(os.environ["TEMOIN_TUER_APRES"]):
    import signal; os.kill(os.getppid(), signal.SIGKILL); sys.exit(1)   # l'étape tuée net au N-ième appel
if os.environ.get("TEMOIN_LIMITE") and deja >= int(os.environ["TEMOIN_LIMITE"]):   # mode limite : la fenêtre se ferme après N appels
    sys.stdout.write(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "duration_api_ms": 0,
                                  "result": "You've hit your usage limit (témoin)", "total_cost_usd": 0}}))
    sys.exit(1)
p = os.path.join(os.environ["TEMOIN_REPONSES"], cle + ".json")
if os.environ.get("TEMOIN_LIMITE") and deja == int(os.environ["TEMOIN_LIMITE"]) - 1:
    import time; time.sleep(float(os.environ.get("TEMOIN_EN_VOL_S") or 3))   # le dernier accordé, encore en vol aux refus
if os.path.exists(p):
    sys.stdout.write(open(p, encoding="utf-8").read())
    sys.exit(0)
m = os.environ["TEMOIN_MANQUANTES"]
os.makedirs(m, exist_ok=True)
json.dump({{"modele": modele, "schema": schema, "consigne": consigne, "argv": a}}, open(os.path.join(m, cle + ".json"), "w", encoding="utf-8"),
          ensure_ascii=False)
sys.stderr.write("témoin : consigne inconnue (consigne changée), " + cle + "\\n")
sys.exit({code})
'''

FAUX_RESEAU = '''#!{python}
"""Un outil réseau du témoin (newskit.temoin) : refusé et journalisé."""
import json, os, sys
with open(os.environ["TEMOIN_JOURNAL"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"quoi": {nom!r}, "vise": " ".join(sys.argv[1:]), "pid": os.getpid(), "argv": sys.argv[:3]}},
                       ensure_ascii=False) + "\\n")
sys.stderr.write("témoin : réseau coupé : {nom}\\n")
sys.exit({code})
'''

CANARI = r'''"""Le canari du témoin (newskit.temoin) : refuse et journalise une écriture vers le réel ou une connexion hors de
la boucle locale, dans chaque processus Python du témoin."""
import json, os, sys

if os.environ.get("TEMOIN_SANS_PAUSE"):        # les pauses de politesse et d'attente entre tentatives, annulées
    import time as _time
    _time.sleep = lambda s: None

_REEL = [os.path.realpath(p) for p in os.environ.get("TEMOIN_REEL", "").split(os.pathsep) if p]
_ICI = os.path.realpath(os.environ.get("TEMOIN_ICI") or "/nonexistent-temoin")
_JOURNAL = os.environ.get("TEMOIN_JOURNAL")
_LOCAUX = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
_dedans = False
_ECRIT = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _reel(p):
    if p is None or isinstance(p, int):
        return None
    try:
        p = os.fsdecode(os.fspath(p))
    except TypeError:
        return None
    q = os.path.realpath(os.path.abspath(p))
    if q == _ICI or q.startswith(_ICI + os.sep):
        return None
    return q if any(q == r or q.startswith(r + os.sep) for r in _REEL) else None


def _local(hote):
    hote = hote.decode() if isinstance(hote, bytes) else str(hote or "")
    return hote in _LOCAUX or hote.startswith("127.")


def _noter(quoi, quoi_vise):
    global _dedans
    if _JOURNAL and not _dedans:
        _dedans = True
        try:
            with open(_JOURNAL, "a", encoding="utf-8") as f:
                f.write(json.dumps({"quoi": quoi, "vise": quoi_vise, "pid": os.getpid(), "argv": sys.argv[:3]},
                                   ensure_ascii=False) + "\n")
        finally:
            _dedans = False


def _refuser(quoi, vise, motif):
    _noter(quoi, vise)
    raise PermissionError(f"témoin : {motif} : {vise}")


def _crochet(ev, args):
    if _dedans:
        return
    if ev == "open":
        chemin, mode, drapeaux = (list(args) + [None, None, None])[:3]
        ecrit = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(drapeaux, int) and drapeaux & _ECRIT)
        if ecrit and (q := _reel(chemin)):
            _refuser(ev, q, "écriture vers le réel refusée")
    elif ev in ("os.remove", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod", "os.chown", "os.utime", "shutil.rmtree"):
        if args and (q := _reel(args[0])):
            _refuser(ev, q, "écriture vers le réel refusée")
    elif ev in ("os.rename", "os.symlink", "os.link"):
        for p in args[:2]:
            if q := _reel(p):
                _refuser(ev, q, "écriture vers le réel refusée")
    elif ev in ("socket.connect", "socket.sendto"):
        adresse = args[1] if len(args) > 1 else None
        if isinstance(adresse, tuple) and adresse and not _local(adresse[0]):
            _refuser(ev, str(adresse[0]), "réseau coupé")
    elif ev == "socket.getaddrinfo":
        if args and args[0] is not None and not _local(args[0]):
            _refuser(ev, str(args[0]), "réseau coupé")


sys.addaudithook(_crochet)
'''


def preparer(dossier: Path, reponses: Path | None = None, reseau: bool = False) -> Temoin:
    """Le dossier du témoin, prêt : copie du moteur (dépôt git sans distant), données à un graphe, maison vide, faux
    `claude`, canari. Un dossier existant est refait."""
    t = Temoin(Path(dossier).resolve(), Path(reponses or TEMOIN / "reponses").resolve())
    if t.dossier.exists():
        shutil.rmtree(t.dossier)
    for d in (t.code, t.donnees / "cache", t.maison, t.bin, t.canari):
        d.mkdir(parents=True)
    ignorer = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(CODE, t.code / "newskit", ignore=ignorer)
    subprocess.run(["git", "init", "-q", str(t.code)], check=True)
    import yaml  # noqa: PLC0415

    ecrire_dossier(t.donnees, registre(reseau))
    shutil.copytree(TEMOIN / "dossier", t.donnees / "dossier_local")
    fixe = time.mktime(DATE_DES_FICHES.timetuple()) + 12 * 3600
    for f in (t.donnees / "dossier_local").rglob("*"):
        os.utime(f, (fixe, fixe))  # la date d'une fiche sans date écrite : la même pour toute copie
    f = t.bin / "claude"
    f.write_text(FAUX_CLAUDE.format(python=sys.executable, code=CONSIGNE_INCONNUE), encoding="utf-8")
    f.chmod(0o755)
    for nom in RESEAU:
        f = t.bin / nom
        f.write_text(FAUX_RESEAU.format(python=sys.executable, nom=nom, code=RESEAU_COUPE), encoding="utf-8")
        f.chmod(0o755)
    (t.canari / "sitecustomize.py").write_text(CANARI, encoding="utf-8")
    return t


def environnement(t: Temoin, reel: list[Path] | None = None) -> dict[str, str]:
    """L'environnement d'un processus du témoin. `reel` : les racines à protéger (par défaut le code, les données et la
    maison réels)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NEWSKIT_", "TEMOIN_", "CORTEXDB_DOSSIER"))}
    racines = reel if reel is not None else [CODE, RACINE, Path.home()]
    env.update(
        {
            "HOME": str(t.maison),
            "PATH": os.pathsep.join([str(t.bin), env.get("PATH", "")]),
            "PYTHONPATH": os.pathsep.join([str(t.canari), str(t.code)]),
            "PYTHONUSERBASE": site.getuserbase(),  # les paquets de l'utilisateur restent lisibles sous une autre maison
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "HF_HUB_OFFLINE": "1",
            "NEWSKIT_DONNEES": str(t.donnees),
            "NEWSKIT_CONTACT_EMAIL": "temoin@exemple.invalid",
            "TEMOIN_REEL": os.pathsep.join(str(Path(p).resolve()) for p in racines),
            "TEMOIN_ICI": str(t.dossier),
            "TEMOIN_JOURNAL": str(t.journal),
            "TEMOIN_REPONSES": str(t.reponses),
            "TEMOIN_MANQUANTES": str(t.manquantes),
            "TEMOIN_APPELS": str(t.dossier / "appels.jsonl"),
        }
    )
    return env


def vrai_claude() -> Path | None:
    """Le vrai `claude` du PATH, hors des dossiers de témoins (le faux vit dans `bin/`, à côté du `canari/`)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        f = Path(d) / "claude"
        if f.is_file() and os.access(f, os.X_OK) and not (f.parent.parent / "canari").is_dir():
            return f
    return None


PLAFOND = 30  # appels payés par réenregistrement, au plus (CHANTIER_GENERIQUE.md § 4.3)


def reenregistrer(t: Temoin, plafond: int = PLAFOND, claude: Path | None = None, delai: int = 900) -> dict:
    """Paie les consignes manquantes du témoin, une par une, sous `plafond` appels : le vrai `claude` reçoit les
    mêmes arguments et la même consigne, hors du témoin (la vraie maison porte l'abonnement). La réponse rejouable
    va sous `reponses/<empreinte>.json` et la manquante est retirée ; chaque appel entre au vrai registre des coûts,
    poste « temoin ». Une réponse en erreur reste manquante ; la limite d'usage arrête tout."""
    import time  # noqa: PLC0415
    from newskit import couts  # noqa: PLC0415

    claude = claude or vrai_claude()
    if claude is None:
        raise SystemExit("témoin : aucun vrai claude sur le PATH")
    manquantes = sorted(t.manquantes.glob("*.json")) if t.manquantes.exists() else []
    bilan = {"payes": 0, "erreurs": 0, "restantes": len(manquantes), "cout_usd": 0.0}
    for m in manquantes[: max(plafond, 0)]:
        if couts.en_limite():
            break
        d = json.loads(m.read_text(encoding="utf-8"))
        t0 = time.time()
        r = subprocess.run(
            [str(claude), *d["argv"]], input=d["consigne"], capture_output=True, text=True, timeout=delai, cwd=t.dossier
        )
        env = couts.enregistrer("temoin", GRAPHE, d.get("modele") or None, r, time.time() - t0)
        bilan["cout_usd"] = round(bilan["cout_usd"] + float(env.get("total_cost_usd") or 0), 6)
        if r.returncode == 0 and env and not env.get("is_error"):
            t.reponses.mkdir(parents=True, exist_ok=True)
            (t.reponses / m.name).write_text(r.stdout, encoding="utf-8")
            m.unlink()
            bilan["payes"] += 1
            bilan["restantes"] -= 1
        else:
            bilan["erreurs"] += 1
    return bilan


# --- Le passage entier (CHANTIER_GENERIQUE.md § 4.3) ------------------------------------------------------------------------------

MARQUE = "temoin_vert"  # sous cache/ des vraies données : date, commit, verdict du dernier passage


def commit_du_code() -> str:
    """Le commit du dépôt du moteur s'il est une copie de travail git, sinon la version installée du paquet."""
    r = subprocess.run(["git", "-C", str(CODE), "rev-parse", "HEAD"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    import newskit  # noqa: PLC0415

    return f"v{newskit.__version__}"


def verdict(racine: Path, commit: str, jour: str) -> tuple[bool, str]:
    """(la routine peut partir, ce qu'elle dit). Sans marque, le témoin n'a jamais tourné (daemon pas installé) : la
    routine part et le dit. Une marque rouge, d'un autre jour ou d'un autre commit l'arrête."""
    p = Path(racine) / "cache" / MARQUE
    if not p.exists():
        return True, "témoin jamais passé : la routine part sans lui (daemon du témoin à installer)"
    m = json.loads(p.read_text(encoding="utf-8"))
    if not m.get("vert"):
        return False, f"témoin rouge du {m.get('date')} : {m.get('detail', '')}"
    if m.get("date") != jour:
        return False, f"témoin du {m.get('date')}, pas du jour ({jour})"
    if m.get("commit") != commit:
        return False, f"témoin passé sur {str(m.get('commit'))[:9]}, code à {commit[:9]}"
    return True, ""


def essayer() -> tuple[int, str]:
    """Le témoin entier : tous les tests tests/test_temoin_*.py. Rend (code, dernière ligne de pytest)."""
    tests = sorted(str(x) for x in (CODE / "donnees_temoin" / "tests").glob("test_temoin_*.py"))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=CODE,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    lignes = [x for x in r.stdout.splitlines() if x.strip()]
    return r.returncode, (lignes[-1] if lignes else r.stderr.strip()[-300:])


def rejouer(dossier: Path) -> Temoin:
    """Un passage de la routine dans un témoin neuf, pour recueillir les consignes manquantes (code 98)."""
    t = preparer(dossier)
    subprocess.run(
        [
            sys.executable,
            str(t.code / "newskit" / "routine_hebdo.py"),
            "--graphe",
            GRAPHE,
            "--sans-commit",
            "--sans-courriel",
        ],
        env=environnement(t),
        cwd=t.donnees,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    return t


def nuit(
    racine: Path | None = None,
    essai=essayer,
    rejeu=rejouer,
    payer=None,
    commit: str | None = None,
    jour: str | None = None,
    plafond: int = PLAFOND,
) -> int:
    """Passe le témoin et pose la marque. Un rouge d'enregistrement (consignes manquantes) paie les manquantes sous
    `plafond` et repasse une fois ; un rouge d'infrastructure pose une marque rouge sans rien payer."""
    import time  # noqa: PLC0415

    racine = Path(racine or RACINE)
    commit, jour = commit or commit_du_code(), jour or time.strftime("%Y-%m-%d")
    code, ligne = essai()
    neuves = 0
    if code:
        t = rejeu(racine / "cache" / "temoin" / "nuit")
        if t.manquantes.exists() and any(t.manquantes.glob("*.json")):
            neuves = (payer or reenregistrer)(t, plafond)["payes"]
            if neuves:
                code, ligne = essai()
    m = {
        "date": jour,
        "commit": commit,
        "vert": code == 0,
        "detail": ligne,
        "reponses_neuves": neuves,
        "le": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = racine / "cache" / MARQUE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    print(
        f"témoin {'vert' if code == 0 else 'ROUGE'} sur {commit[:9]} : {ligne}"
        + (f" ; {neuves} réponse(s) neuve(s) dans tests/temoin/reponses, à commiter" if neuves else "")
    )
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("preparer").add_argument("dossier", type=Path)
    e = sp.add_parser("reenregistrer", help="paie les consignes manquantes d'un témoin déjà passé, sous un plafond")
    e.add_argument("dossier", type=Path)
    e.add_argument("--plafond", type=int, default=PLAFOND)
    sp.add_parser(
        "passage", aliases=["nuit"], help="passe le témoin entier et pose cache/temoin_vert (le daemon de 00:30)"
    )
    a = ap.parse_args(argv)
    if a.cmd in ("passage", "nuit"):
        return nuit()
    if a.cmd == "reenregistrer":
        t = Temoin(a.dossier.resolve(), (TEMOIN / "reponses").resolve())
        print(json.dumps(reenregistrer(t, a.plafond), ensure_ascii=False))
        return 0
    t = preparer(a.dossier)
    for k, v in sorted(environnement(t).items()):
        if k.startswith(("TEMOIN_", "NEWSKIT_")) or k in ("HOME", "PYTHONPATH"):
            print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Le cerveau CortexDB d'un graphe, mis à jour à chaque passage (CortexDB est la base du moteur, porteur,
22.10.2026 ; CHANTIER_PILOTE_CORTEXDB.md). Aucun appel au modèle : les lots et les synthèses déjà payés se versent tels
quels (puits_cortexdb.py), les vecteurs viennent de l'encodeur local du graphe.

En place (B15, mainteneur, 2 novembre 2026), dès CortexDB v2.143.0 et quand le cerveau précédent a ses empreintes
(<graphe>.empreintes.json) : un clone APFS de l'ancien cerveau (aucune place tant qu'on n'y écrit pas) reçoit les seuls
versements changés, ajoutés ou retirés (puits_cortexdb.EnPlace), l'encodeur n'encode que les morceaux neufs, et le clone
remplace l'ancien par renommage ; il se compacte quand ses pages libres passent COMPACTER. Sinon, reconstruction
complète, et le bilan dit pourquoi (`mode`). `--complet` force la reconstruction ; `--en-place` la refuse (arrêt).
Une reconstruction complète demande PLACE_COMPLETE fois l'ancien cerveau en place libre : sans elle, le passage se fait
en place s'il le peut, sinon il s'arrête avant d'écrire et l'ancien cerveau reste.

La reconstruction complète date de CortexDB v2.136.0, qui ne mettait pas un document à jour (morceaux en trop,
vecteurs laissés par `delete_document_graph`, corrigés en v2.137.0) ; elle sert de banc au versement en place, et :
- le vecteur d'un morceau inchangé (même identifiant, même texte, même encodeur) est repris de l'ancien cerveau ;
- `vector_dimension_repair` n'encode que le reste, puis la provenance qu'il efface (doc_id, metadata, acl) revient
  de la copie gardée avant ;
- le journal des changements (`change_log`, un quart du fichier) est vidé après le versement, le cerveau compacté à la
  publication (un graphe audio 833 → 563 Mo, mesure identique) ;
- le cerveau neuf ne remplace l'ancien que complet (tout encodé, aucun morceau sans document).

    python3 -m newskit.cerveau_cortexdb --graphe <graphe> [--reprendre-de <cerveau.db> …]   # même encodeur seulement
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)

BINAIRE = Path(
    os.environ.get("CORTEXDB_BINAIRE") or Path.home() / ".local" / "bin" / "cortexdb-grpc"
)  # deployer_cortexdb.py (C1)
PYTHON_CLIENT = RACINE / "cache" / "pilote_cortexdb" / "venv" / "bin" / "python"  # cortexdb_client
# Le chemin que reçoit le Python du client : un lien vers le paquet Newskit, rien d'autre (13.11.2026).
CHEMIN_CLIENT = RACINE / "cache" / "pilote_cortexdb" / "chemin"
DOSSIER = Path(os.environ.get("CORTEXDB_DOSSIER") or RACINE / "cache" / "cortexdb")  # un banc écrit ailleurs
EN_PLACE_DES = (
    2,
    143,
    0,
)  # D6 : delete_document_graph + knowledge_delete ne laissent rien (liliang-cn/cortexdb#122, #123)
# Au-delà, un passage en place coûte plus qu'une reconstruction (mainteneur, 14.11.2026). Un remplacement est un appel à
# delete_document_graph, un retrait deux (avec knowledge_delete) ; CortexDB lit tout le graphe à chacun. Un graphe de
# gouvernance (396 000 nœuds, 1 043 819 arêtes), 14.11.2026 : 4,0 s par delete_document_graph (972 appels),
# 6,0 s par knowledge_delete, contre 32 minutes pour sa reconstruction complète du matin.
EFFACEMENTS_MAX = 300
# CortexDB v2.154.0 (liliang-cn/cortexdb#190) trouve ce qu'un document a mis dans le graphe par des index de provenance :
# une suppression ne lit plus tout le graphe. Copie du même graphe, 16.11.2026 : 0,04 à 0,19 s par delete_document_graph,
# contre 0,9 à 1,2 s en v2.147.0. 3000 effacements au pire mesuré font ≈ 570 s, contre 1632 s pour sa reconstruction
# complète du 16.11.2026.
INDEX_PROVENANCE_DES = (2, 154, 0)
EFFACEMENTS_MAX_INDEXES = 3000
COMPACTER = 0.15  # part de pages libres au-delà de laquelle le cerveau en place se compacte
# Une reconstruction complète garde l'ancien cerveau jusqu'à la publication et écrit le neuf à côté, puis sa copie
# compacte. Un graphe de gouvernance, 15.11.2026 : ancien 4,0 Go, neuf à 4,2 Go et pas fini quand le disque s'est rempli.
PLACE_COMPLETE = 2  # place libre demandée, en multiple de l'ancien cerveau et de son journal


def version_binaire(binaire: Path = None) -> tuple[int, ...]:
    r = subprocess.run([str(binaire or BINAIRE), "-version"], capture_output=True, text=True, timeout=60)
    v = (r.stdout.split() or ["v0"])[-1].lstrip("v").split("+")[0]
    return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())


def mode_du_passage(
    cible: Path, empreintes: Path, version: tuple[int, ...], force: str | None = None
) -> tuple[str, str]:
    """('en_place' | 'complet', raison). `force` : 'complet' ou 'en_place' ; un en place impossible et forcé arrête."""
    if force == "complet":
        return "complet", "demandé"
    raison = (
        (
            ""
            if version >= EN_PLACE_DES
            else f"CortexDB {'.'.join(map(str, version))} < {'.'.join(map(str, EN_PLACE_DES))}"
        )
        or ("" if cible.exists() else "aucun cerveau précédent")
        or ("" if empreintes.exists() else "cerveau précédent sans empreintes")
    )
    if raison and force == "en_place":
        raise SystemExit(f"  en place impossible : {raison}")
    return ("complet", raison) if raison else ("en_place", "")


def plafond_effacements(version: tuple[int, ...]) -> int:
    """Le nombre d'appels d'effacement au-delà duquel un passage en place coûte plus qu'une reconstruction, selon le
    binaire qui versera."""
    return EFFACEMENTS_MAX_INDEXES if version >= INDEX_PROVENANCE_DES else EFFACEMENTS_MAX


def mode_selon_effacements(
    mode: str, raison: str, compte: dict | None, force: str | None, version: tuple[int, ...] = ()
) -> tuple[str, str]:
    """Le mode après le compte d'un passage en place : reconstruction complète au-delà du plafond d'appels
    d'effacement du binaire (`plafond_effacements`), sauf en place forcé."""
    if mode != "en_place" or force == "en_place":
        return mode, raison
    n, plafond = compte.get("remplaces", 0) + 2 * compte.get("retires", 0), plafond_effacements(version)
    return ("complet", f"{n} effacements en place > {plafond}") if n > plafond else (mode, raison)


def place_libre(dossier: Path) -> int:
    return shutil.disk_usage(dossier).free


def mode_selon_place(mode: str, raison: str, possible: str, cible: Path, libre: int) -> tuple[str, str]:
    """Une reconstruction complète sans la place de PLACE_COMPLETE fois l'ancien cerveau ne commence pas : le passage
    se fait en place quand il le peut (`possible`, le mode avant le compte des effacements), sinon il s'arrête avant
    d'écrire et l'ancien cerveau reste. Sans ancien cerveau, il ne demande rien."""
    if mode != "complet":
        return mode, raison
    ancien = sum(p.stat().st_size for p in (cible, Path(f"{cible}-wal")) if p.exists())
    besoin = PLACE_COMPLETE * ancien
    if libre >= besoin:
        return mode, raison
    manque = f"{libre / 1e9:.1f} Go libres pour {besoin / 1e9:.1f} Go"
    if possible == "en_place":
        return "en_place", f"reconstruction complète ({raison}) sans la place : {manque}"
    raise SystemExit(
        f"  place insuffisante pour une reconstruction complète ({raison}) : {manque} ; l'ancien cerveau est gardé"
    )


def cloner(source: Path, dest: Path) -> None:
    """Clone APFS (`cp -c`) du cerveau et de son WAL : aucune place tant qu'on n'écrit pas ; copie ordinaire ailleurs."""
    effacer(dest)
    for suffixe in ("", "-wal"):
        s_ = Path(str(source) + suffixe)
        if s_.exists():
            r = subprocess.run(["cp", "-c", str(s_), str(dest) + suffixe], capture_output=True)
            if r.returncode:
                subprocess.run(["cp", str(s_), str(dest) + suffixe], check=True)
    # retirer_cjk a retiré les index CJK du cerveau publié ; CortexDB les recrée vides et ne les remplit qu'au-dessous de
    # sa version de schéma. Vides, leur déclencheur casse la première réécriture (« malformed », 17.11.2026).
    c = sqlite3.connect(dest)
    try:
        c.execute("PRAGMA user_version = 0")
        c.commit()
    finally:
        c.close()


def pages_libres(cerveau: Path) -> float:
    c = sqlite3.connect(cerveau)
    try:
        libres, total = c.execute("PRAGMA freelist_count").fetchone()[0], c.execute("PRAGMA page_count").fetchone()[0]
        return libres / total if total else 0.0
    finally:
        c.close()


def publier_en_place(neuf: Path, cible: Path, servi: bool = False) -> bool:
    """Le clone mis à jour remplace l'ancien par renommage ; compacté seulement au-delà de COMPACTER pages libres.
    Rend True s'il a été compacté. Un cerveau servi se pose à côté (`<g>.pret.db`), comme `publier`."""
    retirer_cjk(neuf)
    dest = cible.with_name(cible.stem + ".pret.db") if servi else cible
    if pages_libres(neuf) > COMPACTER:
        publier(neuf, cible, servi)
        return True
    effacer(Path(str(dest) + "-wal"))
    effacer(Path(str(dest) + "-shm"))
    os.replace(neuf, dest)
    effacer(neuf)
    return False


def engage_couche1(graphe: str) -> bool:
    """Le cerveau du graphe porte aussi le squelette et la structure des titres (K0) : `couche1: true` dans sa
    déclaration au registre. Environ +15 % par cerveau."""
    from newskit import config_sources as cfg  # noqa: PLC0415

    return bool(cfg.graphe_declare(graphe).get("couche1"))


CHAUFFE = 900  # secondes pour la première ouverture avec l'encodeur : l'index HNSW se reconstruit (un graphe d'éthique : 100 s)


def chemin_cerveau(graphe: str) -> Path:
    return DOSSIER / f"{graphe}.db"


# --- le calcul, en SQL sur des cerveaux arrêtés ------------------------------------------------------------------------


def _identifiant_court(ancien: str) -> str:
    """`chunk:<chemin>:<n>` d'un cerveau d'avant le 23.10.2026 → `chunk:<empreinte>:<n>` (puits_cortexdb.court)."""
    from newskit import puits_cortexdb as pc  # noqa: PLC0415

    tete, _, reste = ancien.partition(":")
    chemin, sep, n = reste.rpartition(":")
    return f"{tete}:{pc.court(chemin)}:{n}" if sep and chemin.startswith(("sources/", "pyramide/")) else ancien


def reprendre_vecteurs(neuf: Path, ancien: Path, octets: int) -> int:
    """Copie dans `neuf` le vecteur d'un morceau de même identifiant et de même texte, s'il est déjà encodé (`octets`).
    L'identifiant ancien se lit aussi sous sa forme courte : le passage aux identifiants courts ne réencode rien."""
    c = sqlite3.connect(neuf)
    c.execute("ATTACH ? AS a", (str(ancien),))
    c.create_function("court", 1, _identifiant_court, deterministic=True)
    c.execute(
        "CREATE TEMP TABLE repris AS SELECT court(id) AS id, content, vector FROM a.embeddings WHERE length(vector) = ?",
        (octets,),
    )
    c.execute("CREATE INDEX temp.repris_id ON repris(id)")
    n = c.execute(
        """UPDATE embeddings SET vector = (SELECT x.vector FROM repris x WHERE x.id = embeddings.id
                     AND x.content = embeddings.content LIMIT 1)
                     WHERE length(vector) != ? AND EXISTS (SELECT 1 FROM repris x WHERE x.id = embeddings.id
                     AND x.content = embeddings.content)""",
        (octets,),
    ).rowcount
    c.commit()
    c.close()
    return n


def garder_provenance(cerveau: Path, cote: Path) -> None:
    cote.unlink(missing_ok=True)
    c = sqlite3.connect(cerveau)
    c.execute("ATTACH ? AS c", (str(cote),))
    c.execute("CREATE TABLE c.provenance AS SELECT id, doc_id, metadata, acl FROM embeddings")
    c.commit()
    c.close()


def restaurer_provenance(cerveau: Path, cote: Path) -> int:
    c = sqlite3.connect(cerveau)
    c.execute("ATTACH ? AS c", (str(cote),))
    n = c.execute("""UPDATE embeddings SET doc_id = p.doc_id, metadata = p.metadata, acl = p.acl
                     FROM c.provenance p WHERE p.id = embeddings.id""").rowcount
    c.commit()
    c.close()
    return n


def controle(cerveau: Path, octets: int) -> dict:
    c = sqlite3.connect(cerveau)
    lignes, a_encoder, sans = c.execute(
        """SELECT count(*), coalesce(sum(length(vector) != ?), 0),
                                           coalesce(sum(doc_id IS NULL OR doc_id = ''), 0) FROM embeddings""",
        (octets,),
    ).fetchone()
    c.close()
    return {"lignes": lignes, "a_encoder": a_encoder, "sans_document": sans}


def reparer_jusqu_au_bout(reparer, restant, essais: int = 3) -> int:
    """Une réparation peut laisser des lots (échec passager de l'encodeur : 192 morceaux sur 105 719, un graphe d'éthique) ; on
    la relance tant que le reste diminue, au plus `essais` fois. Rend le total réencodé."""
    total, avant = reparer(), None
    for _ in range(essais - 1):
        r = restant()
        if r == 0 or (avant is not None and r >= avant):
            break
        avant = r
        total += reparer()
    return total


def publiable(c: dict) -> bool:
    return c["lignes"] > 0 and c["a_encoder"] == 0 and c["sans_document"] == 0


def elaguer_journal(cerveau: Path) -> int:
    """Vide le journal des changements comme PruneChanges : la dernière ligne reste (son seq n'est jamais redonné), la
    marque pruned_through suit. Un cerveau reconstruit à chaque passage n'en a pas l'usage (un graphe audio : 211 Mo sur 833,
    mesure identique sans lui, 23.10.2026). Les pages libérées servent aux vecteurs encodés ensuite."""
    c = sqlite3.connect(cerveau)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'change_log'").fetchone():
            return 0
        (dernier,) = c.execute("SELECT max(seq) FROM change_log").fetchone()
        if dernier is None:
            return 0
        n = c.execute("DELETE FROM change_log WHERE seq < ?", (dernier,)).rowcount
        c.execute("UPDATE change_log_meta SET meta_value = ? WHERE meta_key = 'pruned_through'", (str(dernier - 1),))
        c.commit()
        return n
    finally:
        c.close()


def elaguer_historique(cerveau: Path) -> int:
    """Vide l'historique du graphe comme Purge (`vacuum_graph`, pkg/graph/temporal.go v2.141.0) : les versions closes
    (valid_to ou retracted_at posé), nœuds et arêtes ; rend leur nombre. Seules les lectures à une date (as_of,
    graph_diff, changes_since) le lisent, et ni Thalamus ni le moteur n'en servent ; un cerveau reconstruit à chaque
    passage n'en a pas l'usage (expertise du 29.10.2026 : 340 Mo sur 2 243 pour un graphe d'ingénierie). La place revient au
    compactage de la publication."""
    c = sqlite3.connect(cerveau)
    try:
        n = 0
        for table in ("graph_node_history", "graph_edge_history"):
            if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
                n += c.execute(f"DELETE FROM {table} WHERE COALESCE(retracted_at, valid_to) IS NOT NULL").rowcount
        c.commit()
        return n
    finally:
        c.close()


def compacter(source: Path, dest: Path) -> None:
    """VACUUM INTO : une seule copie compacte écrite, quand VACUUM en place en écrit deux (copie, puis journal)."""
    c = sqlite3.connect(source)
    try:
        c.execute("VACUUM INTO ?", (str(dest),))
    finally:
        c.close()


def retirer_cjk(cerveau: Path) -> int:
    """Retire les index trigram des écritures chinoise, japonaise et coréenne et leurs déclencheurs ; rend leur nombre.
    un graphe d'éthique, 23.10.2026 : 265 Mo sur 2,1 Go, pour des fiches sans CJK. CortexDB les recrée vides à l'ouverture
    (`CREATE … IF NOT EXISTS`, remplissage une fois par version de schéma) : un cerveau publié n'étant plus écrit, ils
    le restent. Aucun réglage du serveur ne les coupe (v2.138.0)."""
    c = sqlite3.connect(cerveau)
    try:
        objets = c.execute(
            "SELECT type, name FROM sqlite_master WHERE name LIKE '%\\_cjk%' ESCAPE '\\' AND "
            "(type = 'trigger' OR (type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%'))"
        ).fetchall()
        for genre, nom in sorted(objets, key=lambda o: o[0] != "trigger"):
            c.execute(f'DROP {"TRIGGER" if genre == "trigger" else "TABLE"} IF EXISTS "{nom}"')
        c.commit()
        return len(objets)
    finally:
        c.close()


def servi(graphe: str) -> bool:
    """Un serveur permanent (serveur_cortexdb.py) tient ce cerveau ouvert : son fichier d'adresse nomme un processus vivant."""
    from newskit import lecture_cortexdb as lc  # noqa: PLC0415

    try:
        os.kill(int(json.loads(lc.fichier_adresse(graphe).read_text(encoding="utf-8"))["pid"]), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def publier(neuf: Path, cible: Path, servi: bool = False) -> None:
    """Remplace l'ancien cerveau par le neuf, complet, sans index CJK, compacté. L'ancien s'efface d'abord (le neuf,
    complet, le remplace déjà) : la place de pointe est le neuf et sa copie compacte. Un compactage qui échoue (disque
    plein) publie le neuf tel quel. Un cerveau `servi` ne s'efface pas sous son serveur : le neuf se publie à côté
    (`<g>.pret.db`), que le gardien de serveur_cortexdb met en place."""
    retirer_cjk(neuf)
    a_cote = cible.with_name(cible.stem + ".pret.db")
    effacer(a_cote)
    if servi:
        cible = a_cote
    effacer(cible)
    try:
        compacter(neuf, cible)
    except sqlite3.Error:
        effacer(cible)
        os.replace(neuf, cible)
    effacer(neuf)


# --- les processus : serveur CortexDB, encodeur local -------------------------------------------------------------------


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def attendre_port(port: int, delai: float = 60) -> None:
    fin = time.time() + delai
    while time.time() < fin:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError(f"port {port} jamais ouvert")


class Serveur:
    """cortexdb-grpc sur un cerveau, le temps d'un bloc `with` ; arrêté proprement (le WAL est vidé à la sortie)."""

    def __init__(self, cerveau: Path, journal: Path, encodeur_url: str | None = None, dim: int = 0, delai: float = 60):
        self.port, self.jeton, self.delai = port_libre(), secrets.token_hex(8), delai
        self.env = {
            **os.environ,
            "CORTEXDB_PATH": str(cerveau),
            "CORTEXDB_GRPC_TOKEN": self.jeton,
            "CORTEXDB_GRPC_ADDR": f"127.0.0.1:{self.port}",
        }
        if encodeur_url:
            self.env.update(
                {
                    "OPENAI_BASE_URL": encodeur_url,
                    "OPENAI_API_KEY": "local",
                    "CORTEXDB_EMBED_MODEL": "local",
                    "CORTEXDB_EMBED_DIM": str(dim),
                }
            )
        self.journal = journal

    def __enter__(self):
        self.p = subprocess.Popen([str(BINAIRE)], env=self.env, stdout=self.journal.open("a"), stderr=subprocess.STDOUT)
        attendre_port(self.port, self.delai)
        return self

    def __exit__(self, *_):
        self.p.terminate()
        self.p.wait(timeout=max(60, self.delai))

    @property
    def adresse(self) -> str:
        return f"127.0.0.1:{self.port}"


class Encodeur:
    """embeddings_locaux.py : l'encodeur du graphe derrière /v1/embeddings ; lit son nom et sa dimension."""

    def __init__(self, graphe: str, journal: Path):
        self.graphe, self.port, self.journal = graphe, port_libre(), journal

    def __enter__(self):
        self.journal.write_text("")
        self.p = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(CODE / "embeddings_locaux.py"),
                "--graphe",
                self.graphe,
                "--port",
                str(self.port),
            ],
            stdout=self.journal.open("a"),
            stderr=subprocess.STDOUT,
        )
        fin = time.time() + 300
        while time.time() < fin:
            ligne = next((l for l in self.journal.read_text().splitlines() if l.startswith("encodeur ")), "")
            if ligne:  # « encodeur local:TextEmbedding, dimension 384, http://… »
                self.nom = ligne.split(",")[0].removeprefix("encodeur ").strip()
                self.dim = int(ligne.split("dimension ")[1].split(",")[0])
                attendre_port(self.port)
                return self
            if self.p.poll() is not None:
                raise RuntimeError(f"encodeur arrêté : {self.journal.read_text()[-500:]}")
            time.sleep(0.5)
        raise RuntimeError("encodeur jamais prêt")

    def __exit__(self, *_):
        self.p.terminate()
        self.p.wait(timeout=60)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


def env_du_client() -> dict:
    """L'environnement du Python du client (venv du pilote, qui n'a que cortexdb_client) : PYTHONPATH porte un dossier
    où ne vit qu'un lien vers le paquet Newskit installé. Du 08.11 au 13.11.2026, le versement mourait chaque nuit sur
    « No module named 'newskit' » et le cerveau publié restait celui du 7 novembre. Le dossier des paquets du poste
    n'est pas ouvert au client : ses grpcio et protobuf masqueraient ceux du client."""
    lien = CHEMIN_CLIENT / "newskit"
    CHEMIN_CLIENT.mkdir(parents=True, exist_ok=True)
    if lien.is_symlink() and lien.resolve() != CODE:
        lien.unlink()
    if not lien.exists():
        lien.symlink_to(CODE, target_is_directory=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(CHEMIN_CLIENT), *[p for p in [env.get("PYTHONPATH")] if p]])
    return env


def lancer_client(args: list[str]) -> subprocess.CompletedProcess:
    """Un script de Newskit sous le Python du client (le versement)."""
    return subprocess.run(args, capture_output=True, text=True, timeout=6 * 3600, env=env_du_client())


def appeler_outil(serveur: Serveur, nom: str, args: dict) -> dict:
    code = (
        "import json, sys\nfrom cortexdb_client import CortexClient, proto\n"
        "a, j, n, x = sys.argv[1:5]\n"
        "with CortexClient.connect(a, token=j) as c:\n"
        "    print(c.tools.CallTool(proto.CallToolRequest(name=n, args_json=x)).result_json)\n"
    )
    r = subprocess.run(
        [str(PYTHON_CLIENT), "-c", code, serveur.adresse, serveur.jeton, nom, json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=6 * 3600,
        env=env_du_client(),
    )
    if r.returncode:
        raise RuntimeError(f"{nom} : {r.stderr[-500:]}")
    return json.loads(r.stdout or "{}")


def effacer(cerveau: Path) -> None:
    for suffixe in ("", "-wal", "-shm"):
        Path(str(cerveau) + suffixe).unlink(missing_ok=True)


# --- un passage --------------------------------------------------------------------------------------------------------


def _publier_du_graphe(graphe: str, neuf: Path, cible: Path) -> None:
    publier(neuf, cible, servi=servi(graphe))


def options_du_versement(graphe: str, base: Path, couche1: bool = False) -> list[str]:
    """Les options de puits_cortexdb.py pour ce graphe : la pyramide si elle existe, la couche 1 s'il y est engagé ou
    si cette construction la force (`--couche1`, une mesure sans engager le graphe : K2 du projet d'alignement), et
    avec elle les vues du graphe (carte, sommets, relations : K4), que kg-mcp servait aux mêmes graphes."""
    return [
        *(["--pyramide"] if (base / "synthese" / "notions.json").exists() else []),
        *(["--couche1", "--vues", str(DOSSIER / f"{graphe}.vues.json")] if couche1 or engage_couche1(graphe) else []),
    ]


def ecrire_vues(graphe: str, chemin: Path) -> None:
    """Les vues du graphe (K4 : carte, sommets, relations), calculées ici, où networkx est là ; le puits, qui tourne dans
    le Python du pilote, ne fait que les verser. Sans graphe sur le disque, aucune vue."""
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import interroger_graphe as ig  # noqa: PLC0415
    from newskit import puits_cortexdb as pc  # noqa: PLC0415

    ch = cfg.chemins_du_graphe(graphe)
    vues = []
    if ch.get("graphe") and Path(ch["graphe"]).exists():
        carte = Path(ch["carte"])
        vues = pc.vues_du_graphe(
            ig.charger(Path(ch["graphe"])), carte.read_text(encoding="utf-8") if carte.exists() else None
        )
    chemin.write_text(json.dumps(vues, ensure_ascii=False), encoding="utf-8")


def cerveaux_a_reprendre(reprendre_de: list[Path], cible: Path | None, precedent: dict, encodeur: str) -> list[Path]:
    """Les cerveaux dont les vecteurs se reprennent : ceux nommés, s'ils sont encodés par le même encodeur (leur bilan
    <cerveau>.json le dit) ; sinon l'ancien cerveau du graphe, s'il l'était. Un cerveau d'un autre encodeur n'est jamais
    repris : ses vecteurs sont d'un autre espace (GV7, 2 novembre 2026 : un graphe fusionné, un graphe à encodeur affiné)."""

    def encode_par(p: Path) -> str | None:
        try:
            return json.loads(p.with_suffix(".json").read_text(encoding="utf-8")).get("encodeur")
        except (OSError, ValueError):
            return None

    if reprendre_de:
        # Un nom sans chemin ni suffixe est celui d'un graphe : son cerveau (GV7, 2 novembre 2026 : `--reprendre-de
        # <graphe>` ne trouvait rien et tout se réencodait en silence). Un cerveau nommé absent arrête tout.
        chemins = [DOSSIER / f"{p}.db" if p.parent == Path(".") and not p.suffix else p for p in reprendre_de]
        absents = [str(p) for p in chemins if not p.exists()]
        if absents:
            raise SystemExit(f"  --reprendre-de : cerveau absent : {', '.join(absents)}")
        return [p for p in chemins if encode_par(p) == encodeur]
    return [cible] if cible is not None and cible.exists() and precedent.get("encodeur") == encodeur else []


def attendre_encodeur(graphe: str, delai: int = 3 * 3600, pas: int = 60) -> int:
    """Attend qu'un essai d'encodeur du graphe finisse (cache/encodeur/<graphe>/en_cours, posé par qui l'entraîne ou
    l'évalue), au plus `delai` secondes : le cerveau se reconstruit une fois, avec l'encodeur gagnant (GV7, mainteneur,
    2 novembre 2026). Rend les secondes attendues."""
    marque, attendu = RACINE / "cache" / "encodeur" / graphe / "en_cours", 0
    while marque.exists() and attendu < delai:
        time.sleep(pas)
        attendu += pas
    return attendu


def construire(
    graphe: str, reprendre_de: list[Path] | None = None, couche1: bool = False, force: str | None = None
) -> dict:
    from newskit import config_sources as cfg  # noqa: PLC0415

    attendu = attendre_encodeur(graphe)
    if attendu:
        print(f"  {graphe} : {attendu} s d'attente d'un essai d'encodeur", file=sys.stderr)
    DOSSIER.mkdir(parents=True, exist_ok=True)
    cible, neuf, cote = chemin_cerveau(graphe), DOSSIER / f"{graphe}.neuf.db", DOSSIER / f"{graphe}.provenance.db"
    journal, p_bilan = DOSSIER / f"{graphe}.log", DOSSIER / f"{graphe}.json"
    precedent = json.loads(p_bilan.read_text(encoding="utf-8")) if p_bilan.exists() else {}
    p_emp, neuf_emp = DOSSIER / f"{graphe}.empreintes.json", DOSSIER / f"{graphe}.neuf.empreintes.json"
    version = version_binaire()
    mode, raison = mode_du_passage(cible, p_emp, version, force)
    if mode == "en_place" and precedent.get("couche1", False) != bool(couche1 or engage_couche1(graphe)):
        if force == "en_place":
            raise SystemExit("  en place impossible : la couche 1 a changé")
        mode, raison = "complet", "la couche 1 a changé"
    possible = mode  # en place, ou complet sans recours
    t0 = time.time()
    options = options_du_versement(graphe, cfg.chemins_du_graphe(graphe)["base"], couche1)
    if "--vues" in options:
        ecrire_vues(graphe, Path(options[options.index("--vues") + 1]))
    puits = [str(PYTHON_CLIENT), str(CODE / "puits_cortexdb.py"), "--graphe", graphe]
    if mode == "en_place" and force != "en_place":
        r = lancer_client([*puits, *options, "--empreintes-avant", str(p_emp), "--compter"])
        if r.returncode:
            raise RuntimeError(f"comptage : {(r.stdout + r.stderr)[-800:]}")
        mode, raison = mode_selon_effacements(
            mode, raison, json.loads(r.stdout.strip().splitlines()[-1]), force, version
        )
    mode, raison = mode_selon_place(mode, raison, possible, cible, place_libre(DOSSIER))
    print(
        f"  {graphe} : {'en place' if mode == 'en_place' else 'reconstruction complète'}{f' ({raison})' if raison else ''}",
        file=sys.stderr,
    )
    effacer(neuf)
    if mode == "en_place":
        cloner(cible, neuf)
    options += [
        "--empreintes-apres",
        str(neuf_emp),
        *(["--empreintes-avant", str(p_emp)] if mode == "en_place" else []),
    ]

    def verser_dans(s: Serveur):
        return lancer_client([*puits, "--adresse", s.adresse, "--jeton", s.jeton, *options])

    if mode == "en_place":
        # En place, l'encodeur sert dès le versement : sans lui, CortexDB remplit un document neuf de vecteurs lexicaux
        # à la dimension du cerveau, que `controle` prend pour encodés (banc B15 du 03.11.2026 : 20 morceaux sur 16 126).
        with (
            Encodeur(graphe, DOSSIER / f"{graphe}.encodeur.log") as e0,
            Serveur(neuf, journal, e0.url, e0.dim, delai=CHAUFFE) as s,
        ):
            r = verser_dans(s)
    else:
        with Serveur(neuf, journal) as s:
            r = verser_dans(s)
    if r.returncode:
        raise RuntimeError(f"versement : {(r.stdout + r.stderr)[-800:]}")
    versement = json.loads(r.stdout.strip().splitlines()[-1])
    journal_elague = elaguer_journal(neuf)
    with Encodeur(graphe, DOSSIER / f"{graphe}.encodeur.log") as e:
        octets = 4 * e.dim + 4
        if mode == "en_place" and precedent.get("encodeur") != e.nom:
            raise RuntimeError(
                f"en place : l'encodeur a changé ({precedent.get('encodeur')} → {e.nom}), reconstruire avec --complet"
            )
        repris = (
            0
            if mode == "en_place"
            else sum(
                reprendre_vecteurs(neuf, p, octets)
                for p in cerveaux_a_reprendre(reprendre_de or [], cible, precedent, e.nom)
            )
        )
        avant = controle(neuf, octets)
        encodes = 0
        change = mode == "complet" or any(versement.get(k) for k in ("remplaces", "ajoutes", "retires"))
        if avant["a_encoder"]:
            if mode == "complet":
                garder_provenance(neuf, cote)
            # Les vecteurs repris reconstruisent l'index HNSW avant que le port s'ouvre (un graphe d'éthique, 24.10.2026 : 84 s).
            with Serveur(neuf, journal, e.url, e.dim, delai=CHAUFFE) as s:
                encodes = reparer_jusqu_au_bout(
                    lambda: int(
                        appeler_outil(s, "vector_dimension_repair", {"dry_run": False, "batch_size": 64})
                        .get("repair", {})
                        .get("reembedded")
                        or 0
                    ),
                    lambda: controle(neuf, octets)["a_encoder"],
                )
            if mode == "complet":
                restaurer_provenance(neuf, cote)
                cote.unlink(missing_ok=True)
        fin = controle(neuf, octets)
        # Une ouverture avec l'encodeur : l'index HNSW se reconstruit des vrais vecteurs et son instantané s'écrit à la
        # fermeture ; sinon la première ouverture servie le refait (un graphe d'éthique : 100 s) et le cerveau publié grossit après.
        if publiable(fin) and change:
            with Serveur(neuf, journal, e.url, e.dim, delai=CHAUFFE):
                pass
        nom, dim = e.nom, e.dim
    historique_elague = elaguer_historique(neuf)  # après la dernière ouverture : plus rien n'écrit le cerveau
    bilan = {
        "graphe": graphe,
        "quand": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "raison": raison,
        "version": ".".join(map(str, version_binaire())),
        "couche1": bool(couche1 or engage_couche1(graphe)),
        "encodeur": nom,
        "dimension": dim,
        "lots": versement.get("verses"),
        "syntheses": versement.get("syntheses", 0),
        **fin,
        **{k: versement.get(k, 0) for k in ("inchanges", "remplaces", "ajoutes", "retires")},
        "vecteurs_repris": repris,
        "vecteurs_encodes": encodes,
        "journal_elague": journal_elague,
        "historique_elague": historique_elague,
        "duree_s": round(time.time() - t0, 1),
    }
    if not publiable(fin):
        bilan["publie"] = False
        print(json.dumps(bilan, ensure_ascii=False))
        raise RuntimeError(f"cerveau incomplet, l'ancien est gardé : {fin}")
    if mode == "en_place":
        bilan["compacte"] = publier_en_place(neuf, cible, servi=servi(graphe))
    else:
        _publier_du_graphe(graphe, neuf, cible)
    os.replace(neuf_emp, p_emp)
    bilan["octets"] = cible.stat().st_size
    bilan["publie"] = True
    p_bilan.write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    return bilan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument(
        "--reprendre-de",
        type=Path,
        action="append",
        default=[],
        help="cerveau dont reprendre les vecteurs, s'il a le même encodeur (premier passage ; répétable)",
    )
    ap.add_argument(
        "--couche1",
        action="store_true",
        help="verser la couche 1 pour cette construction, même sans couche1 au registre",
    )
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--complet", action="store_const", const="complet", dest="force", help="reconstruire en entier")
    m.add_argument("--en-place", action="store_const", const="en_place", dest="force", help="en place ou arrêt")
    a = ap.parse_args()
    if not BINAIRE.exists() or not PYTHON_CLIENT.exists():
        print(f"  CortexDB absent : {BINAIRE} et {PYTHON_CLIENT} (CHANTIER_PILOTE_CORTEXDB.md)", file=sys.stderr)
        return 2
    b = construire(a.graphe, a.reprendre_de, a.couche1, a.force)
    print(
        f"  {a.graphe} ({b['mode']}) : {b['inchanges']} inchangés, {b['remplaces']} remplacés, {b['ajoutes']} ajoutés, "
        f"{b['retires']} retirés ; {b['lots']} lots, {b['syntheses']} synthèses, {b['lignes']} morceaux ; vecteurs repris "
        f"{b['vecteurs_repris']}, encodés {b['vecteurs_encodes']} ({b['encodeur']}) ; {b['duree_s']} s"
    )
    print(json.dumps(b, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

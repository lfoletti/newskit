#!/usr/bin/env python3
"""Les cerveaux CortexDB du serveur, démarrés à la demande (23.10.2026 ; porteur : « démarrage à la demande, le dernier
cerveau interrogé reste actif »).

Le VPS n'a pas la mémoire de tous les cerveaux chargés à la fois (12 Go sans swap, ≈ 15 Go pour sept graphes). Un seul
processus écoute, sur 127.0.0.1, le port fixe de chaque graphe (`cortexdb_port` au registre, celui que rights.yaml et
les fichiers d'adresse nomment). À la première connexion, il démarre cortexdb-grpc sur un port interne, puis relaie
les octets dans les deux sens ; le client (Thalamus, l'édition) ne voit qu'une première réponse plus lente.

- Un cerveau sans octet depuis `repos` secondes s'arrête, sauf le dernier interrogé, qui reste chargé.
- Un cerveau chaud (KG_CORTEXDB_CHAUDS, séparés par des virgules) démarre avec le service, ne se repose jamais, ne fait
  jamais place et redémarre aussitôt après un arrêt (cerveau neuf, clés changées, processus mort). mainteneur, 06.11.2026 :
  « il faut garder le cerveau governance chaud » ; le graphe du site met 142 s à démarrer, la station du site attend 30 s.
- Un cerveau qui ne tient pas dans le budget (Go) fait place en arrêtant les cerveaux calmes, du plus anciennement interrogé au plus récent ; un cerveau qui répond (une demande sans réponse
  encore, ou des octets depuis moins de `calme` secondes) n'est jamais arrêté : la nouvelle question attend.
  Le poids d'un cerveau est sa mémoire résidente mesurée (le pic de son dernier passage, lu dans /proc) ; avant sa
  première mesure, une estimation d'après la taille du fichier. Jusqu'au 29.10.2026, l'estimation seule servait et
  valait 2,5 à 3 fois la RSS (expertise : un graphe d'ingénierie estimé 3 Go, mesuré 0,9 à 1,2) : elle chassait des cerveaux
  pour une mémoire qu'ils ne prenaient pas. Le budget ne dépasse jamais la borne du service moins une réserve.
- Un cerveau prêt (`<g>.pret.db`, publié par la construction) ou un fichier de clés changé (Thalamus) arrête le
  cerveau servi ; il redémarre sur le nouveau à la question suivante. Un cortexdb mort est noté arrêté, de même.
- Un encodeur par modèle, partagé entre les graphes qui l'emploient, chargé à la première demande et gardé.

Le gardien d'un graphe seul (serveur_cortexdb.py) reste pour le Mac.

    python3 -m newskit.aiguilleur_cortexdb --graphes <g1>,<g2>
    KG_CORTEXDB_CLES_DOSSIER=cache/cortexdb/cles KG_CORTEXDB_BUDGET=6 python3 -m newskit.aiguilleur_cortexdb --graphes …
"""
from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from newskit import cerveau_cortexdb as cc  # noqa: E402
from newskit.commun import RACINE  # noqa: E402 — la racine des données (N1)

port_libre = cc.port_libre
attendre = cc.attendre_port


class Politique:
    """Qui démarre, qui fait place, qui se repose ; sans horloge ni processus (les instants sont donnés)."""

    def __init__(self, poids: dict[str, float], budget: float, calme: float, repos: float, chauds=frozenset()):
        self.poids, self.budget, self.calme, self.repos = poids, budget, calme, repos
        self.chauds = frozenset(chauds)
        self.dernier: dict[str, float] = {}          # cerveaux actifs → instant de leur dernier octet
        self.mesures: dict[str, float] = {}          # le pic mesuré du dernier passage, gardé après l'arrêt
        self.neufs: set[str] = set()                 # démarrés, pas encore mesurés dans ce passage

    def demarre(self, g: str, t: float) -> None:
        self.dernier[g] = t
        self.neufs.add(g)

    def mesure(self, g: str, go: float) -> None:
        """La mémoire résidente de `g`, en Go : la première d'un passage remplace le pic du passage d'avant."""
        if g in self.neufs:
            self.neufs.discard(g)
            self.mesures[g] = go
        else:
            self.mesures[g] = max(self.mesures.get(g, 0.0), go)

    def poids_de(self, g: str) -> float:
        return self.mesures.get(g, self.poids[g])

    def touche(self, g: str, t: float) -> None:
        if g in self.dernier:
            self.dernier[g] = max(self.dernier[g], t)

    def arrete(self, g: str) -> None:
        self.dernier.pop(g, None)

    def place_pour(self, g: str, t: float, occupes: frozenset = frozenset()) -> list[str] | None:
        """Les cerveaux à arrêter pour que `g` tienne ; None s'il faut attendre (les autres répondent)."""
        if g in self.dernier:
            return []
        charge = sum(self.poids_de(a) for a in self.dernier)
        victimes = []
        for a in sorted(self.dernier, key=self.dernier.get):
            if charge + self.poids_de(g) <= self.budget:
                break
            if a not in occupes and a not in self.chauds and t - self.dernier[a] >= self.calme:
                victimes.append(a)
                charge -= self.poids_de(a)
        if charge + self.poids_de(g) <= self.budget or len(victimes) == len(set(self.dernier) - self.chauds):
            return victimes
        return None

    def a_reposer(self, t: float) -> list[str]:
        if not self.dernier:
            return []
        garde = max(self.dernier, key=self.dernier.get)
        return [a for a, d in self.dernier.items() if a != garde and a not in self.chauds and t - d >= self.repos]


def chemin_declaration() -> Path:
    """La déclaration des cerveaux partagés (bloc `partages`) : KG_PARTAGES_DECLARATION, sinon celle de l'édition
    sous la racine des données. Lue ici, sans importer `droits_thalamus`, qui est de l'édition (N2)."""
    v = os.environ.get("KG_PARTAGES_DECLARATION")
    return Path(v) if v else RACINE / "deploiement" / "thalamus_droits.yaml"


def partages_declares() -> dict:
    """Le bloc `partages` de la déclaration ; vide si elle manque (un cerveau partagé demandé n'est alors pas servi)."""
    import yaml  # noqa: PLC0415
    try:
        d = yaml.safe_load(chemin_declaration().read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    return d.get("partages") or {}


RESERVE = 1.5   # Go laissés sous la borne du service : l'encodeur (1 Go mesuré le 29.10.2026) et une marge


def budget_du_service(env, borne: int | None) -> float:
    """Le budget (Go de mémoire résidente) : KG_CORTEXDB_BUDGET, jamais au-dessus de la borne du service moins la
    réserve. Une unité installée avant le 29.10.2026 porte 8, qui comptait des poids estimés : sous MemoryMax=6G,
    pris tel quel en mémoire mesurée, il laisserait l'OOM trancher."""
    budget = float(env.get("KG_CORTEXDB_BUDGET", "6"))
    return budget if borne is None else min(budget, borne / 1e9 - RESERVE)


def rss_go(pid: int, racine: Path = Path("/proc")) -> float | None:
    """La mémoire résidente du processus, en Go (VmRSS) ; None sans /proc (le Mac) ou le processus parti."""
    try:
        for ligne in (racine / str(pid) / "status").read_text().splitlines():
            if ligne.startswith("VmRSS:"):
                return int(ligne.split()[1]) * 1024 / 1e9
    except (OSError, ValueError, IndexError):
        pass
    return None


class Encodeurs:
    """Un encodeur par modèle, partagé ; deux cerveaux qui s'ouvrent ensemble n'en lancent qu'un (verrou : sans lui,
    le second remplaçait le premier, resté orphelin à l'arrêt — essai local du 23.10.2026). `fabrique(cle)` rend un
    objet qui a `fermer()`."""

    def __init__(self, fabrique):
        self.fabrique, self.par_cle, self.verrou = fabrique, {}, threading.Lock()

    def obtenir(self, cle: str):
        with self.verrou:
            if cle not in self.par_cle:
                self.par_cle[cle] = self.fabrique(cle)
            return self.par_cle[cle]

    def fermer(self) -> None:
        with self.verrou:
            for e in self.par_cle.values():
                e.fermer()
            self.par_cle.clear()


@dataclass
class Cerveau:
    """Un cerveau servi : un graphe de l'édition (reconstruit et envoyé du Mac) ou un cerveau partagé, que ses
    lecteurs remplissent par Thalamus (proposé, relu, puis servi) et qui ne vit que sur le serveur."""
    nom: str
    chemin: Path
    port: int
    cles: Path | None
    partage: bool = False


def declarer(graphes, partages, ports, decl, dossier_partages, dossier_cles, chemin_graphe) -> list[Cerveau]:
    """Les cerveaux à servir ; ValueError sur un port absent ou pris deux fois, un partagé sans clés. Un partagé absent
    de la déclaration n'est pas servi (dit sur la sortie d'erreur)."""
    out = []
    for g in graphes:
        if not ports.get(g):
            raise ValueError(f"{g} : pas de cortexdb_port au registre")
        out.append(Cerveau(g, chemin_graphe(g), ports[g], Path(dossier_cles) / f"{g}.json" if dossier_cles else None))
    for n in partages:
        if n not in decl:                    # renommé ou retiré depuis l'installation : les autres démarrent quand même
            print(f"cerveau partagé absent de deploiement/thalamus_droits.yaml, non servi : {n}", file=sys.stderr)
            continue
        if not dossier_cles:
            raise ValueError(f"{n} : un cerveau partagé s'écrit, il ne se sert pas sans clés (Thalamus)")
        out.append(Cerveau(n, Path(dossier_partages) / f"{n}.db", int(decl[n]["port"]), Path(dossier_cles) / f"{n}.json",
                           partage=True))
    vus: dict[int, str] = {}
    for c in out:
        if c.port in vus:
            raise ValueError(f"port {c.port} pris par {vus[c.port]} et par {c.nom}")
        vus[c.port] = c.nom
    return out


def environnement_de(c: Cerveau, port: int, jeton: str, encodeur_url: str, dim: int) -> dict:
    """L'environnement de cortexdb ; un cerveau partagé ne sert un document proposé qu'une fois accepté."""
    from newskit import serveur_cortexdb as sc  # noqa: PLC0415
    env = sc.environnement(c.chemin, port, jeton, encodeur_url, dim, c.cles)
    if c.partage:
        env["CORTEXDB_REVIEW_GATE"] = "1"
    return env


class _Liaison:
    """Une connexion relayée ; « en attente » quand le client a parlé après la dernière réponse du cerveau."""

    def __init__(self, client: socket.socket, cerveau: socket.socket):
        self.client, self.cerveau = client, cerveau
        self.demande = self.reponse = 0.0

    def en_attente(self) -> bool:
        return self.demande > self.reponse

    def fermer(self) -> None:
        for s in (self.client, self.cerveau):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


class Aiguilleur:
    """`lancer(g, port)` démarre cortexdb de `g` sur `port` et rend le processus, prêt ; `a_relancer(g)` dit qu'un
    cerveau servi doit s'arrêter (cerveau prêt, clés changées)."""

    def __init__(self, ports: dict[str, int], lancer, politique: Politique, tour: float = 5,
                 a_relancer=None, attente: float = 300, horloge=time.monotonic, rss=rss_go):
        self.ports, self.lancer, self.pol, self.tour, self.rss = ports, lancer, politique, tour, rss
        self.a_relancer, self.attente, self.horloge = a_relancer or (lambda g: False), attente, horloge
        self.proc: dict[str, subprocess.Popen] = {}
        self.interne: dict[str, int] = {}
        self.demarrage: dict[str, threading.Event] = {}   # cerveaux en cours d'ouverture, hors du verrou
        self.liaisons: dict[str, set[_Liaison]] = {g: set() for g in ports}
        self.verrou = threading.RLock()
        self.fin = threading.Event()
        self.ecoutes: list[socket.socket] = []
        self.chauffe: set[str] = set()                     # cerveaux chauds en cours de rechargement

    def _chauffer(self) -> None:
        """Recharge, hors du verrou, chaque cerveau chaud qui n'est ni servi ni en cours d'ouverture."""
        def un(g):
            try:
                self.assurer(g)
            except Exception as e:  # noqa: BLE001 — réessayé au tour suivant
                print(f"{g} : chaud, démarrage échoué ({type(e).__name__} {str(e)[:100]})", flush=True)
            finally:
                with self.verrou:
                    self.chauffe.discard(g)
        with self.verrou:
            for g in sorted(self.pol.chauds & set(self.ports)):
                if g not in self.proc and g not in self.demarrage and g not in self.chauffe and not self.fin.is_set():
                    self.chauffe.add(g)
                    threading.Thread(target=un, args=(g,), daemon=True).start()

    def actifs(self) -> list[str]:
        return sorted(self.proc)

    def _occupes(self) -> frozenset:
        return frozenset(g for g, ls in self.liaisons.items() if any(l.en_attente() for l in list(ls))) | set(self.demarrage)

    def _arreter(self, g: str, pourquoi: str) -> None:
        p = self.proc.pop(g, None)
        self.pol.arrete(g)
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=cc.CHAUFFE)
            except subprocess.TimeoutExpired:
                p.kill()
        for l in list(self.liaisons[g]):
            l.fermer()
        print(f"{g} : arrêté ({pourquoi})", flush=True)

    def assurer(self, g: str) -> int:
        """Le port interne de `g`, démarré s'il le faut ; attend qu'une place se libère."""
        fin = self.horloge() + self.attente
        while not self.fin.is_set():
            ouvrir = None
            with self.verrou:
                if g in self.proc:
                    return self.interne[g]
                en_cours = self.demarrage.get(g)
                if en_cours is None:
                    victimes = self.pol.place_pour(g, self.horloge(), self._occupes())
                    if victimes is not None:
                        for v in victimes:
                            self._arreter(v, f"place pour {g}")
                        ouvrir = self.demarrage[g] = threading.Event()
                        self.pol.demarre(g, self.horloge())      # sa place est prise pendant l'ouverture
            if ouvrir is not None:                               # l'ouverture hors du verrou : les autres en parallèle
                return self._ouvrir(g, ouvrir)
            if en_cours is not None:
                en_cours.wait(max(0.0, fin - self.horloge()))
                continue
            if self.horloge() > fin:
                raise TimeoutError(f"{g} : pas de place en {self.attente:.0f} s")
            time.sleep(0.2)
        raise TimeoutError("arrêt en cours")

    def _ouvrir(self, g: str, ouvert: threading.Event) -> int:
        port = port_libre()
        t0 = time.time()
        try:
            p = self.lancer(g, port)
        except BaseException:
            with self.verrou:
                self.pol.arrete(g)
                self.demarrage.pop(g, None)
            ouvert.set()
            raise
        with self.verrou:
            self.proc[g], self.interne[g] = p, port
            self.demarrage.pop(g, None)
        ouvert.set()
        print(f"{g} : démarré en {time.time() - t0:.1f} s ; actifs {', '.join(self.actifs())}", flush=True)
        return port

    def _pomper(self, g: str, l: _Liaison, de: socket.socket, vers: socket.socket, sens: str) -> None:
        try:
            while d := de.recv(65536):
                t = self.horloge()
                setattr(l, sens, t)
                self.pol.touche(g, t)
                vers.sendall(d)
        except OSError:
            pass
        finally:
            l.fermer()
            self.liaisons[g].discard(l)

    def _servir_client(self, g: str, client: socket.socket) -> None:
        try:
            port = self.assurer(g)
            cerveau = socket.create_connection(("127.0.0.1", port), timeout=30)
            cerveau.settimeout(None)
        except Exception as e:  # noqa: BLE001 — le client voit une connexion fermée, réessaie
            print(f"{g} : connexion refusée ({type(e).__name__} {str(e)[:100]})", flush=True)
            client.close()
            return
        l = _Liaison(client, cerveau)
        self.liaisons[g].add(l)
        self.pol.touche(g, self.horloge())
        threading.Thread(target=self._pomper, args=(g, l, cerveau, client, "reponse"), daemon=True).start()
        self._pomper(g, l, client, cerveau, "demande")

    def _ecouter(self, g: str, s: socket.socket) -> None:
        while not self.fin.is_set():
            try:
                c, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=self._servir_client, args=(g, c), daemon=True).start()

    def _veiller(self) -> None:
        with self.verrou:
            for g in list(self.proc):
                if self.proc[g].poll() is not None:
                    self._arreter(g, f"mort, code {self.proc[g].poll()}")
                    continue
                if (go := self.rss(self.proc[g].pid)) is not None:
                    self.pol.mesure(g, go)
                if self.a_relancer(g) and g not in self._occupes():
                    self._arreter(g, "cerveau neuf ou clés changées : redémarre à la prochaine question")
            for g in self.pol.a_reposer(self.horloge()):
                if g in self.proc and g not in self._occupes():
                    self._arreter(g, "au repos")
        self._chauffer()

    def servir(self) -> None:
        for g, port in self.ports.items():
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.listen(64)
            self.ecoutes.append(s)
            threading.Thread(target=self._ecouter, args=(g, s), daemon=True).start()
        while not self.fin.wait(self.tour):
            self._veiller()

    def arreter(self) -> None:
        self.fin.set()
        for s in self.ecoutes:
            s.close()
        with self.verrou:
            for g in list(self.proc):
                self._arreter(g, "fin du service")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphes", required=True, help="graphes servis, séparés par des virgules")
    ap.add_argument("--partages", default="", help="cerveaux partagés (deploiement/thalamus_droits.yaml), séparés par des virgules")
    a = ap.parse_args()
    from newskit import config_sources as cfg  # noqa: PLC0415
    from newskit import lecture_cortexdb as lc  # noqa: PLC0415
    from newskit import serveur_cortexdb as sc  # noqa: PLC0415
    from newskit import synthese as sy  # noqa: PLC0415
    graphes = [g for g in a.graphes.split(",") if g]
    partages = [n for n in a.partages.split(",") if n]
    dossier_cles = os.environ.get("KG_CORTEXDB_CLES_DOSSIER")
    try:
        liste = declarer(graphes, partages, {g: cfg.port_cortexdb(g) for g in graphes},
                         partages_declares(),
                         os.environ.get("KG_CERVEAUX_PARTAGES") or cc.DOSSIER / "partages", dossier_cles, cc.chemin_cerveau)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    cerveaux = {c.nom: c for c in liste}
    for c in liste:
        if c.partage:
            c.chemin.parent.mkdir(parents=True, exist_ok=True)     # cortexdb crée le cerveau vide à la première ouverture
    binaire = os.environ.get("KG_CORTEXDB_BINAIRE") or str(cc.BINAIRE)
    jetons = {c.nom: (secrets.token_hex(16) if c.partage or not c.cles else sc.assurer_cle(c.cles, c.nom)) for c in liste}
    vu: dict[str, object] = {}

    def etat_cles(g):
        try:
            s = cerveaux[g].cles.stat() if cerveaux[g].cles else None
        except FileNotFoundError:
            return None
        return s and (s.st_mtime_ns, s.st_size, s.st_ino)

    def cle_encodeur(g):
        return "" if cerveaux[g].partage else str(sy.modele_affine(g) or "")

    class _Ouvert:
        def __init__(self, cle):
            g = next(x for x in cerveaux if cle_encodeur(x) == cle)
            self.e = cc.Encodeur(g, cc.DOSSIER / f"encodeur-{Path(cle).name or 'base'}.log").__enter__()
            self.url, self.dim = self.e.url, self.e.dim

        def fermer(self):
            self.e.__exit__()

    encodeurs = Encodeurs(_Ouvert)

    def lancer(g, port):
        c = cerveaux[g]
        if sc.pret(c.chemin).exists():
            for suffixe in ("-wal", "-shm"):
                Path(f"{c.chemin}{suffixe}").unlink(missing_ok=True)
            os.replace(sc.pret(c.chemin), c.chemin)
        if c.cles and not c.partage:
            jetons[g] = sc.assurer_cle(c.cles, g)
        vu[g] = etat_cles(g)
        e = encodeurs.obtenir(cle_encodeur(g))
        p = subprocess.Popen([binaire], env=environnement_de(c, port, jetons[g], e.url, e.dim),
                             stdout=(cc.DOSSIER / f"{g}.serveur.log").open("a"), stderr=subprocess.STDOUT)
        cc.attendre_port(port, cc.CHAUFFE)
        return p

    def a_relancer(g):
        return sc.pret(cerveaux[g].chemin).exists() or etat_cles(g) != vu.get(g)

    def poids(g):
        c = cerveaux[g].chemin
        c = c if c.exists() else sc.pret(c)
        # Avant la première mesure. RSS mesurée le 29.10.2026 : 0,14 à 0,22 Go pour 0,25 Go de fichier, 0,30 à 0,42
        # pour 0,57, 0,91 à 1,16 pour 2,24 ; cette droite passe au-dessus des trois pics.
        return 0.1 + 0.5 * (c.stat().st_size / 1e9 if c.exists() else 0)

    budget = budget_du_service(os.environ, sy.memoire_bornee())
    repos = float(os.environ.get("KG_CORTEXDB_REPOS", "300"))
    chauds = {g for g in os.environ.get("KG_CORTEXDB_CHAUDS", "").split(",") if g in cerveaux}
    aig = Aiguilleur({c.nom: c.port for c in liste}, lancer,
                     Politique({g: poids(g) for g in cerveaux}, budget=budget, calme=3, repos=repos, chauds=chauds), a_relancer=a_relancer)
    signal.signal(signal.SIGTERM, lambda *_: aig.fin.set())
    signal.signal(signal.SIGINT, lambda *_: aig.fin.set())
    for g in graphes:                                    # le pid de l'aiguilleur : la construction publie à côté
        sc.ecrire_adresse(lc.fichier_adresse(g), f"127.0.0.1:{cerveaux[g].port}", jetons[g])
    print(f"aiguilleur : {', '.join(f'{c.nom} {c.port}' + (' (partagé)' if c.partage else '') for c in liste)} ; "
          f"budget {budget:.2f} Go, repos {repos:g} s, chauds {', '.join(sorted(chauds)) or 'aucun'}", flush=True)
    try:
        aig.servir()
    finally:
        for g in graphes:
            lc.fichier_adresse(g).unlink(missing_ok=True)
        aig.arreter()
        encodeurs.fermer()
    return 0


if __name__ == "__main__":
    sys.exit(main())

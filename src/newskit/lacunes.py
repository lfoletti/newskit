#!/usr/bin/env python3
"""Les questions restées sans réponse : ce que le graphe n'a pas su dire devient ce qu'il va chercher.

Une recherche qui ne rend aucun nœud, une notion introuvable, une question à laquelle le skill a dû
répondre « le graphe ne contient rien là-dessus » : chacune est notée ici, par graphe. L'éclaireur
(eclaireur.py) les prend en tête de ses points, cherche sur le web et ingère ce qu'il trouve ; au passage
suivant la recherche est rejouée, et la lacune est close si le graphe répond.

Trois origines (décision du porteur, 04.10.2026) :
    local     l'outil d'interrogation (interroger_graphe.py), quand une recherche échoue ;
    skill     la question entière, enregistrée par le skill /kg quand le graphe n'a pas su répondre :
              python3 -m newskit.interroger_graphe --graphe <nom> lacune "<la question>"
    serveur   les recherches à vide des utilisateurs du serveur en ligne : il les range dans la table
              `lacunes` de sa base (sans le jeton de celui qui a cherché), relevée ici par SSH.
    signale   ce qu'un client du serveur dit avoir manqué pour répondre (outil MCP `signaler_manques`,
              route POST /lacune, 12.10.2026) : un objet précis par lacune, la question en contexte ;
              même table, même relève. Clos quand une étiquette du graphe porte l'objet.
              Depuis K4 (29.10.2026), aussi par Thalamus : `th_add` dans le cerveau partagé `lacunes-signalees` (nommé `manques`
              jusqu'au 05.11.2026), un objet par
              ligne, métadonnées `graphe` et `question` ; relevé avec la clé du relecteur `releve`
              (~/.secrets/th_releve.env, copie de /etc/thalamus/remis/releve.env), puis accepté.

État : graphe/<nom>/lacunes.json — ouvertes, closes, à trancher (deux recherches web sans effet),
hors sujet (jugé par l'éclaireur : une recherche à vide n'est pas toujours une lacune).

    python3 -m newskit.lacunes --graphe <graphe>            # l'état
    python3 -m newskit.lacunes --relever                     # lit la table du serveur, range par graphe
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402

ESSAIS = 2                   # recherches web sans effet avant « à trancher »
LONGUEUR = 300
CERVEAU_LACUNES = "lacunes-signalees"     # nommé `manques` jusqu'au 05.11.2026 (mainteneur : « clarifier son rôle »)
PAR_RELEVE = 100
ACCES_RELEVE = Path.home() / ".secrets" / "th_releve.env"


def chemin(graphe: str) -> Path:
    return cfg.chemins_du_graphe(graphe)["base"] / "lacunes.json"


def lire(graphe: str) -> dict:
    p = chemin(graphe)
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for k in ("ouvertes", "closes", "a_trancher", "hors_sujet"):
        d.setdefault(k, {})
    return d


def ecrire(graphe: str, d: dict) -> None:
    chemin(graphe).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def cle_de(termes: str) -> str:
    """« D5 Render ? » et « d5 render » sont la même lacune."""
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", termes.lower()).strip()[:120]


def noter(graphe: str, termes: str, outil: str = "chercher", origine: str = "local", question: str = "",
          jour: str | None = None) -> bool:
    """Note une recherche sans réponse. Rend faux si elle est vide, déjà close ou écartée."""
    termes = " ".join(str(termes).split())[:LONGUEUR]
    k = cle_de(termes)
    if len(k) < 2:
        return False
    d = lire(graphe)
    if k in d["hors_sujet"] or k in d["a_trancher"]:
        return False
    jour = jour or time.strftime("%Y-%m-%d")
    d["closes"].pop(k, None)                            # elle revient : le graphe n'y répond donc pas
    x = d["ouvertes"].setdefault(k, {"termes": termes, "vues": 0, "premier": jour, "essais": 0, "origines": []})
    x["vues"] += 1
    x["dernier"] = jour
    if origine not in x["origines"]:
        x["origines"].append(origine)
    if question:
        x["question"] = " ".join(question.split())[:600]
    ecrire(graphe, d)
    return True


def noter_sans_bruit(graphe: str, termes: str, outil: str = "chercher", origine: str = "local") -> None:
    """Pour l'outil d'interrogation : noter ne doit jamais faire échouer une recherche."""
    try:
        noter(graphe, termes, outil, origine)
    except Exception:                                   # noqa: BLE001 — le journal est un confort
        pass


def repond(G, termes: str) -> bool:
    """Le graphe a-t-il maintenant un nœud (hors auteurs) dont l'étiquette contient ces termes ?"""
    q = termes.casefold().strip()
    return any(q in str(d.get("label") or n).casefold() and (d.get("kind") or d.get("file_type")) != "auteur"
               for n, d in G.nodes(data=True))


def rejouer(graphe: str, G) -> int:
    """Clôt les lacunes auxquelles le graphe répond désormais. Rend le nombre de closes."""
    d = lire(graphe)
    jour, n = time.strftime("%Y-%m-%d"), 0
    for k in list(d["ouvertes"]):
        x = d["ouvertes"][k]
        if repond(G, x["termes"]):
            d["closes"][k] = {**d["ouvertes"].pop(k), "close_le": jour}
            n += 1
        elif x.get("question") and cle_de(x["termes"]) == cle_de(x["question"]) and x.get("essais", 0) >= 1:
            # Une question entière ne se retrouve pas dans une étiquette : une fois cherchée sur le web et
            # ses pages ingérées, elle est close « à reposer » — c'est en la reposant qu'on saura. Un objet
            # signalé avec sa question en contexte, lui, se juge sur l'étiquette (12.10.2026).
            d["closes"][k] = {**d["ouvertes"].pop(k), "close_le": jour, "note": "pages ingérées, question à reposer"}
            n += 1
        elif x.get("essais", 0) >= ESSAIS:
            d["a_trancher"][k] = {**d["ouvertes"].pop(k), "motif": f"{ESSAIS} recherches web sans effet"}
    if n or d["a_trancher"]:
        ecrire(graphe, d)
    return n


def a_chercher(graphe: str, combien: int) -> list[tuple[str, dict]]:
    """Les lacunes ouvertes à soumettre à l'éclaireur : les plus vues, puis les plus récentes."""
    d = lire(graphe)
    recentes = sorted(d["ouvertes"].items(), key=lambda kx: kx[1].get("dernier", ""), reverse=True)
    # Tri stable : d'abord une question entière venue du skill (le signal le plus riche), puis les plus vues,
    # et à égalité la plus récente.
    return sorted(recentes, key=lambda kx: ("question" not in kx[1], -kx[1]["vues"]))[:combien]


def marquer(graphe: str, cherchees: list[str], hors_sujet: list[str]) -> None:
    """Après un passage de l'éclaireur : un essai de plus pour les lacunes cherchées, les hors sujet rangées."""
    d = lire(graphe)
    for k in cherchees:
        if k in d["ouvertes"]:
            d["ouvertes"][k]["essais"] = d["ouvertes"][k].get("essais", 0) + 1
    for k in hors_sujet:
        if k in d["ouvertes"]:
            d["hors_sujet"][k] = d["ouvertes"].pop(k)
    ecrire(graphe, d)


class Thalamus:
    """Un client MCP du gateway Thalamus (HTTP, JSON) : une session, puis un appel par outil. Rend l'objet JSON de la
    réponse ; une erreur de l'outil lève RuntimeError."""

    def __init__(self, gateway: str, cle: str):
        self.url, self.cle, self.session, self.n = gateway.rstrip("/") + "/mcp", cle, None, 0
        self._poster({"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "newskit-releve", "version": "1"}}})
        self._poster({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _id(self) -> int:
        self.n += 1
        return self.n

    def _poster(self, msg: dict):
        import urllib.request  # noqa: PLC0415
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + self.cle}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        with urllib.request.urlopen(urllib.request.Request(self.url, json.dumps(msg).encode(), h), timeout=120) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            corps = r.read()
        return json.loads(corps) if corps else None

    def __call__(self, outil: str, **args) -> dict:
        r = self._poster({"jsonrpc": "2.0", "id": self._id(), "method": "tools/call", "params": {"name": outil, "arguments": args}})
        res = r.get("result") or {}
        texte = (res.get("content") or [{}])[0].get("text", "")
        if res.get("isError") or "error" in r:
            raise RuntimeError(f"{outil} : {(texte or str(r.get('error')))[:200]}")
        return json.loads(texte)


def lacunes_du_document(k: dict) -> tuple[str, list[str], str] | None:
    """(graphe, manques, question) d'une proposition du cerveau lacunes-signalees : un objet par ligne (1 à 10, 3 caractères au
    moins, sans doublon), le graphe et la question en métadonnées. None si elle est mal formée."""
    k = k.get("knowledge") or {}
    md = k.get("metadata") or {}
    # Le graphe, ou le nom de son cerveau dans Thalamus (`<édition>/<graphe>`), par lequel un agent le connaît.
    graphe = cfg.nom_actuel(str(md.get("graphe") or "").strip().removeprefix(cfg.edition()["nom"] + "/"))
    propres: list[str] = []
    for m in str(k.get("content") or "").splitlines():
        m = " ".join(m.split())[:LONGUEUR]
        if len(m) >= 3 and m.casefold() not in {x.casefold() for x in propres}:
            propres.append(m)
    if not graphe or not propres or len(propres) > 10:
        return None
    return graphe, propres, " ".join(str(md.get("question") or "").split())[:600]


def relever_lacunes_signalees(appeler) -> dict:
    """Les propositions en attente du cerveau lacunes-signalees : chaque manque est noté (origine « signale »), puis le document
    est accepté ; mal formé ou d'un graphe inconnu, il est refusé sans rien noter. Noter avant d'accepter : une
    acceptation perdue fait relire le document, jamais perdre un manque."""
    bilan = {"notes": 0, "acceptes": 0, "refuses": 0}
    for h in appeler("th_pending", brain=CERVEAU_LACUNES, top_k=PAR_RELEVE).get("results") or []:
        kid = h["knowledge_id"]
        lu = lacunes_du_document(appeler("th_get", brain=CERVEAU_LACUNES, knowledge_id=kid))
        if lu is None or lu[0] not in cfg.graphes():
            appeler("th_review", brain=CERVEAU_LACUNES, knowledge_id=kid, decision="rejected")
            bilan["refuses"] += 1
            continue
        graphe, manques, question = lu
        bilan["notes"] += sum(noter(graphe, m, "signaler", "signale", question=question) for m in manques)
        appeler("th_review", brain=CERVEAU_LACUNES, knowledge_id=kid, decision="accepted")
        bilan["acceptes"] += 1
    return bilan


def acces_releve(chemin: Path) -> tuple[str, str] | None:
    """(gateway, clé) du fichier remis au relecteur `releve` (TH_GATEWAY, TH_KEY), ou None."""
    if not Path(chemin).exists():
        return None
    v = dict(l.split("=", 1) for l in Path(chemin).read_text(encoding="utf-8").splitlines() if "=" in l)
    return (v["TH_GATEWAY"].strip(), v["TH_KEY"].strip()) if v.get("TH_GATEWAY") and v.get("TH_KEY") else None


def relever_le_cerveau_des_lacunes() -> dict | None:
    acces = acces_releve(ACCES_RELEVE)
    if acces is None:
        print(f"  cerveau lacunes-signalees non relevé : {ACCES_RELEVE} absent ; le copier de /etc/thalamus/remis/releve.env du VPS")
        return None
    bilan = relever_lacunes_signalees(Thalamus(*acces))
    print(f"  cerveau lacunes-signalees : {bilan['acceptes']} proposition(s) relevée(s), {bilan['notes']} lacune(s) notée(s), "
          f"{bilan['refuses']} refusée(s)")
    return bilan


def relever() -> int:
    """Lit les manques proposés au cerveau lacunes-signalees de Thalamus, puis les recherches à vide notées par le serveur en
    ligne, et les range par graphe."""
    try:
        relever_le_cerveau_des_lacunes()
    except Exception as e:                               # noqa: BLE001 — la table du serveur se relève quand même
        print(f"  cerveau lacunes-signalees non relevé : {str(e)[:100]}")
    from newskit import depots  # noqa: PLC0415 — l'accès SSH à la base du VPS (~/.secrets/vps_kg.env)
    lignes = depots.sql("SELECT id, graphe, outil, REPLACE(termes, '\\t', ' '), REPLACE(COALESCE(question, ''), '\\t', ' '), "
                        "origine FROM lacunes WHERE releve_le IS NULL ORDER BY id LIMIT 500").splitlines()
    ids, n = [], 0
    for l in lignes:
        c = l.split("\t")
        if len(c) != 6:
            continue
        ids.append(int(c[0]))
        graphe = cfg.nom_actuel(c[1])
        # Une recherche à vide (origine « recherche ») ou un manque signalé par un client après sa réponse
        # (« signale », avec la question en contexte : serveur_mcp.signaler, 12.10.2026 ; kg-mcp retiré le 3.11.2026, la table
        # ne reçoit plus rien, ce relevé lit ce qui y reste).
        origine = "signale" if c[5] == "signale" else "serveur"
        if graphe in cfg.graphes() and noter(graphe, c[3], c[2], origine, question=c[4] if origine == "signale" else ""):
            n += 1
    if ids:
        depots.sql(f"UPDATE lacunes SET releve_le = NOW() WHERE id IN ({', '.join(str(i) for i in ids)})")
    print(f"  serveur : {len(ids)} recherche(s) à vide relevée(s), {n} lacune(s) notée(s)")
    return 0


def afficher(graphe: str) -> None:
    d = lire(graphe)
    print(f"  {graphe} : {len(d['ouvertes'])} lacune(s) ouverte(s), {len(d['closes'])} close(s), "
          f"{len(d['a_trancher'])} à trancher, {len(d['hors_sujet'])} hors sujet")
    for k, x in sorted(d["ouvertes"].items(), key=lambda kx: -kx[1]["vues"]):
        print(f"    ouverte    {x['termes'][:70]}  — vue {x['vues']} fois ({', '.join(x['origines'])}), {x.get('essais', 0)} essai(s)")
    for k, x in d["a_trancher"].items():
        print(f"    à trancher {x['termes'][:70]}  — {x.get('motif', '')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", default=cfg.GRAPHE_PRINCIPAL)
    ap.add_argument("--relever", action="store_true", help="lire la table du serveur en ligne")
    args = ap.parse_args()
    if args.relever:
        return relever()
    afficher(cfg.nom_actuel(args.graphe))
    return 0


if __name__ == "__main__":
    sys.exit(main())

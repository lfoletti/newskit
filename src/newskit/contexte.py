#!/usr/bin/env python3
"""La qualité du contexte donné au lecteur, mesurée sans lui (porteur, 10.10.2026 : « une réponse juste attend un contexte
précis qui peut être mesuré en amont »). Pour chaque question d'un jeu du banc, le contexte est assemblé exactement comme
pour une réponse (`synthese.repondre`, sans appel au modèle) ; on mesure si les faits de la réponse attendue et de sa
citation — nombres, dates, noms propres (`synthese.faits`) — s'y lisent. Déterministe, local, quelques secondes par question.

    python3 -m newskit.contexte --graphe <graphe> --jeu banc      # les 140 questions du banc agnostique
    python3 -m newskit.contexte --graphe <graphe> --jeu themes    # les questions du banc par thème
    python3 -m newskit.contexte --graphe <graphe> --jeu themes --sans-cartes

Rend, par jeu : la part des questions dont la page citée est dans le graphe (atteignables), la couverture moyenne des
faits attendus par le contexte, la part des contextes suffisants (couverture ≥ SEUIL), et le croisement avec les verdicts
de la dernière mesure quand elle existe (contexte suffisant → réponse juste ?). C'est la mesure à poser avant et après
tout changement de la recherche (index, encodeur, cartes, passe-partout), sans payer de lecteur.
Écrit cache/contexte/<graphe>_<jeu>_<horodatage>.json ; rien dans le dépôt."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import time
from pathlib import Path

from newskit.commun import CODE, RACINE  # noqa: E402 — deux racines, le code et les données (N1)
from newskit import config_sources as cfg  # noqa: E402
from newskit import interroger_graphe as ig  # noqa: E402
from newskit import synthese as sy  # noqa: E402

SEUIL = 0.6          # part des faits attendus présents dans le contexte à partir de laquelle il est « suffisant »
PART_COMMUNE = 0.01  # un fait présent dans plus de 1 % des paragraphes du corpus n'est pas discriminant


def faits_attendus(q: dict) -> set[str]:
    citation = re.sub(r"https?://\S+", " ", str(q.get("citation") or ""))
    f = sy.faits(str(q.get("attendu") or "")) | sy.faits(citation)
    return {x for x in f if len(x) >= 2}


def contexte_de(graphe: str, question: str, G, sans_cartes: bool) -> str:
    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        sy.repondre(graphe, question, G=G, garder=False, sans_cartes=sans_cartes)
    return tampon.getvalue()


def localiser(ctx_brut: str, fiche: str | None, faits: set[str]) -> dict:
    """Où, dans le contexte, se trouvent la fiche citée et les faits attendus (porteur, 10.10.2026 : « la localisation de
    la citation dans le contexte peut-elle jouer un rôle ? ») : le bloc (CARTE, NOTION, PASSAGES), la position en fraction
    du contexte lu par le lecteur (avant les RENVOIS), et le rang du passage. Les renvois R<n> d'une fiche se lisent dans
    le bloc RENVOIS ; leurs marqueurs [R<n>] dans les synthèses et les passages disent où sa matière est servie."""
    corps, _, renvois = ctx_brut.partition("\nRENVOIS")
    plie = ig._plier(corps); longueur = max(len(plie), 1)
    blocs = []
    for nom, motif in (("carte", r"(?:^|\n)carte "), ("notion", r"(?:^|\n)notion "), ("passages", r"(?:^|\n)passages ")):
        m = re.search(motif, plie)          # la carte ouvre le contexte sans saut de ligne devant elle
        if m:
            blocs.append((m.start(), nom))
    blocs.sort()
    def bloc_de(pos: int) -> str:
        b = "entete"
        for i, nom in blocs:
            if pos >= i: b = nom
        return b
    out = {"longueur": len(corps), "fiche_bloc": None, "fiche_position": None, "fiche_rang_passage": None, "faits_position": None, "faits_bloc": None}
    if fiche:
        rs = []
        for ligne in renvois.splitlines():
            if ligne.strip().endswith(fiche):
                rs += re.findall(r"R(\d+)=", ligne)
        marqueurs = [m for m in re.finditer(r"\[r(\d+(?:, r\d+)*)\]", plie) if set(re.findall(r"\d+", m.group(1))) & set(rs)]
        if marqueurs:
            pos = marqueurs[0].start()
            out["fiche_bloc"], out["fiche_position"] = bloc_de(pos), round(pos / longueur, 3)
            ip = plie.find("\npassages ")
            if ip >= 0:
                passages = [m for m in re.finditer(r"\n\s*\[r(\d+)\]", plie[ip:])]
                for k, m in enumerate(passages):
                    if m.group(1) in rs:
                        out["fiche_rang_passage"] = k + 1; break
    positions = sorted(plie.find(f) for f in faits if f in plie)
    if positions:
        med = positions[len(positions) // 2]
        out["faits_position"], out["faits_bloc"] = round(med / longueur, 3), bloc_de(positions[0])
    return out


def mesurer(graphe: str, jeu: str, sans_cartes: bool = False) -> dict:
    base = cfg.chemins_du_graphe(graphe)["base"]
    if jeu == "banc":
        r = json.loads((base / "banc.json").read_text(encoding="utf-8"))
        questions = [d for d in r["details"] if d.get("type") in ("ag_recent", "ag_fond")]
        verdicts = {int(d["i"]): (d.get("graphe") or {}).get("verdict") for d in questions}
    else:
        questions = [q for q in json.loads((base / "themes_banc.json").read_text(encoding="utf-8")) if not q.get("comblee")]
        hist = (base / "themes_banc_historique.jsonl")
        derniere = json.loads(hist.read_text(encoding="utf-8").splitlines()[-1]) if hist.exists() else {"details": []}
        verdicts = {int(d["i"]): d.get("verdict") for d in derniere.get("details", [])}
    G = sy.charger_graphe(graphe)
    urls = {str(d.get("url") or "").rstrip("/"): str(d.get("source_file")) for _, d in G.nodes(data=True) if d.get("url") and d.get("source_file")}
    # Les porteurs d'un fait (porteur, 10.10.2026 : « des faits populaires se trouvent plus facilement parce qu'il y en a
    # simplement plus ; un fait peu documenté implique autre chose ») : combien de fiches du graphe ont un paragraphe qui
    # porte au moins SEUIL des faits attendus. Un fait porté par une seule fiche exige que la recherche trouve cette fiche.
    ix = sy.index(graphe)
    paragraphes = [(i[0], ig._plier(x)) for i, x in zip(ix[0], ix[1])] if ix else []
    # Seuls les faits discriminants comptent (10.10.2026 : « KIDS », « Act », « article » étaient présents partout et
    # donnaient une couverture de 100 % à des questions dont la réponse n'est dans aucune fiche) : un fait présent dans
    # plus de PART_COMMUNE des paragraphes du corpus ne prouve rien. Sans index, tous les faits comptent.
    df_cache: dict[str, int] = {}
    def commun(f: str) -> bool:
        if not paragraphes:
            return False
        if f not in df_cache:
            df_cache[f] = sum(1 for _, px in paragraphes if f in px)
        return df_cache[f] > PART_COMMUNE * len(paragraphes)
    lignes = []
    t0 = time.time()
    for k, q in enumerate(questions):
        attendus = {f for f in faits_attendus(q) if not commun(f)}
        porteurs = len({f for f, px in paragraphes if attendus and sum(1 for a in attendus if a in px) >= max(1, round(SEUIL * len(attendus)))}) if paragraphes else None
        ctx_brut = contexte_de(graphe, q["question"], G, sans_cartes)
        ctx = ig._plier(ctx_brut)
        presents = {f for f in attendus if f in ctx}
        couverture = len(presents) / len(attendus) if attendus else None
        url = (re.findall(r"https?://[^\s)\"]+", str(q.get("citation") or "") + " " + str(q.get("source") or "")) or [""])[0].rstrip("/")
        fiche = urls.get(url)
        lignes.append({"i": q.get("i"), "type": q.get("type"), "theme": q.get("theme"), "question": q["question"][:160],
                       "faits": len(attendus), "presents": len(presents), "couverture": couverture,
                       "suffisant": couverture is not None and couverture >= SEUIL, "page_dans_graphe": bool(fiche),
                       "fiche_dans_contexte": bool(fiche) and (fiche.split("/")[-1][:40].lower() in ctx), "verdict": verdicts.get(int(q["i"]) if q.get("i") is not None else -1),
                       "manquants": sorted(attendus - presents)[:8], "porteurs": porteurs, **localiser(ctx_brut, fiche, attendus)})
        if (k + 1) % 20 == 0:
            print(f"  {k + 1}/{len(questions)} ({time.time() - t0:.0f} s)", flush=True)
    n = len(lignes)
    mesures = [l for l in lignes if l["couverture"] is not None]
    att = [l for l in lignes if l["page_dans_graphe"]]
    def part(ls, cle): return round(sum(1 for l in ls if l[cle]) / len(ls), 3) if ls else None
    def moyenne(ls): return round(sum(l["couverture"] for l in ls) / len(ls), 3) if ls else None
    juste_si_suffisant = [l for l in mesures if l["suffisant"] and l["verdict"]]
    juste_si_insuffisant = [l for l in mesures if not l["suffisant"] and l["verdict"]]
    bilan = {"graphe": graphe, "jeu": jeu, "sans_cartes": sans_cartes, "horodatage": time.strftime("%Y-%m-%dT%H-%M"), "seuil": SEUIL,
             "questions": n, "page_citee_dans_graphe": part(lignes, "page_dans_graphe"),
             "couverture_moyenne": moyenne(mesures), "contexte_suffisant": part(mesures, "suffisant"),
             "couverture_moyenne_atteignables": moyenne([l for l in att if l["couverture"] is not None]),
             "contexte_suffisant_atteignables": part([l for l in att if l["couverture"] is not None], "suffisant"),
             "fiche_citee_dans_contexte_atteignables": part(att, "fiche_dans_contexte"),
             "juste_quand_suffisant": round(sum(1 for l in juste_si_suffisant if l["verdict"] == "juste") / len(juste_si_suffisant), 3) if juste_si_suffisant else None,
             "juste_quand_insuffisant": round(sum(1 for l in juste_si_insuffisant if l["verdict"] == "juste") / len(juste_si_insuffisant), 3) if juste_si_insuffisant else None,
             "ne_sait_pas_quand_insuffisant": round(sum(1 for l in juste_si_insuffisant if l["verdict"] == "ne_sait_pas") / len(juste_si_insuffisant), 3) if juste_si_insuffisant else None,
             "duree_s": round(time.time() - t0)}
    # Position : la réponse est-elle juste selon l'endroit du contexte où la matière attendue apparaît ?
    def taux(ls):
        avec = [l for l in ls if l["verdict"]]
        return (round(sum(1 for l in avec if l["verdict"] == "juste") / len(avec), 3), len(avec)) if avec else (None, 0)
    bilan["juste_par_bloc_des_faits"] = {b: taux([l for l in mesures if l["faits_bloc"] == b]) for b in ("carte", "notion", "passages")}
    bilan["juste_par_position_des_faits"] = {z: taux([l for l in mesures if l["faits_position"] is not None and lo <= l["faits_position"] < hi])
                                             for z, lo, hi in (("début", 0, 1 / 3), ("milieu", 1 / 3, 2 / 3), ("fin", 2 / 3, 1.01))}
    bilan["juste_par_bloc_de_la_fiche"] = {b: taux([l for l in att if l["fiche_bloc"] == b]) for b in ("carte", "notion", "passages")}
    bilan["juste_fiche_absente_du_contexte"] = taux([l for l in att if not l["fiche_bloc"]])
    bilan["juste_par_rang_de_passage"] = {str(r): taux([l for l in att if l["fiche_rang_passage"] == r]) for r in (1, 2, 3)}
    bilan["longueur_moyenne"] = round(sum(l["longueur"] for l in lignes) / max(len(lignes), 1))
    # Par rareté du fait : combien de fiches le portent (0 : aucune n'en porte assez, 1 : une seule, 2 à 4, 5 et plus).
    strates = (("0 fiche", 0, 0), ("1 fiche", 1, 1), ("2 à 4", 2, 4), ("5 et plus", 5, 10 ** 9))
    bilan["par_rarete"] = {}
    for nom, lo, hi in strates:
        ls = [l for l in mesures if l["porteurs"] is not None and lo <= l["porteurs"] <= hi]
        j, n_j = taux(ls)
        bilan["par_rarete"][nom] = {"questions": len(ls), "couverture": moyenne(ls), "contexte_suffisant": part(ls, "suffisant"), "juste": j, "n_verdicts": n_j,
                                   "fiche_citee_dans_contexte": part([l for l in ls if l["page_dans_graphe"]], "fiche_bloc") if any(l["page_dans_graphe"] for l in ls) else None}
    bilan["lignes"] = lignes
    d = RACINE / "cache" / "contexte"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{graphe}_{jeu}{'_sans-cartes' if sans_cartes else ''}_{bilan['horodatage']}.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    return bilan


def afficher(b: dict) -> None:
    print(f"  {b['graphe']} — {b['jeu']}{' (sans cartes)' if b['sans_cartes'] else ''} : {b['questions']} questions, {b['duree_s']} s")
    print(f"    page citée dans le graphe : {b['page_citee_dans_graphe']:.0%}")
    print(f"    couverture des faits attendus par le contexte : {b['couverture_moyenne']:.0%} en moyenne ; contexte suffisant (≥ {b['seuil']:.0%}) : {b['contexte_suffisant']:.0%}")
    if b["couverture_moyenne_atteignables"] is not None:
        print(f"    sur les pages atteignables : couverture {b['couverture_moyenne_atteignables']:.0%}, suffisant {b['contexte_suffisant_atteignables']:.0%}, fiche citée présente dans le contexte {b['fiche_citee_dans_contexte_atteignables']:.0%}")
    print(f"    longueur moyenne du contexte : {b['longueur_moyenne']} caractères")
    print("    juste selon le bloc où les faits attendus apparaissent d'abord :", {k: f"{v[0]:.0%} (n={v[1]})" if v[0] is not None else "—" for k, v in b["juste_par_bloc_des_faits"].items()})
    print("    juste selon la position des faits (début, milieu, fin) :", {k: f"{v[0]:.0%} (n={v[1]})" if v[0] is not None else "—" for k, v in b["juste_par_position_des_faits"].items()})
    print("    pages atteignables, juste selon le bloc qui sert la fiche citée :", {k: f"{v[0]:.0%} (n={v[1]})" if v[0] is not None else "—" for k, v in b["juste_par_bloc_de_la_fiche"].items()},
          "| fiche absente du contexte :", f"{b['juste_fiche_absente_du_contexte'][0]:.0%} (n={b['juste_fiche_absente_du_contexte'][1]})" if b["juste_fiche_absente_du_contexte"][0] is not None else "—")
    print("    juste selon le rang du passage de la fiche citée :", {k: f"{v[0]:.0%} (n={v[1]})" if v[0] is not None else "—" for k, v in b["juste_par_rang_de_passage"].items()})
    print("    par rareté du fait (fiches qui le portent) :")
    for nom, v in b["par_rarete"].items():
        if v["questions"]:
            print(f"      {nom:10} {v['questions']:3} q. | couverture {v['couverture']:.0%} | suffisant {v['contexte_suffisant']:.0%} | juste "
                  + (f"{v['juste']:.0%} (n={v['n_verdicts']})" if v["juste"] is not None else "—")
                  + (f" | fiche citée servie {v['fiche_citee_dans_contexte']:.0%}" if v["fiche_citee_dans_contexte"] is not None else ""))
    if b["juste_quand_suffisant"] is not None:
        print(f"    croisement avec la dernière mesure : juste {b['juste_quand_suffisant']:.0%} quand le contexte suffit, {b['juste_quand_insuffisant']:.0%} sinon (« ne sait pas » {b['ne_sait_pas_quand_insuffisant']:.0%} sinon)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphe", required=True)
    ap.add_argument("--jeu", choices=("banc", "themes"), default="themes")
    ap.add_argument("--sans-cartes", action="store_true")
    a = ap.parse_args()
    afficher(mesurer(cfg.nom_actuel(a.graphe), a.jeu, a.sans_cartes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

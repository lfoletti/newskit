# Newskit

[![ci](https://github.com/lfoletti/newskit/actions/workflows/ci.yml/badge.svg)](https://github.com/lfoletti/newskit/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![version](https://img.shields.io/github/v/tag/lfoletti/newskit?label=version)](CHANGELOG.md)

Un moteur de veille sans thème, pour Claude Code. Il fait naître un graphe de connaissances sur n'importe quel sujet et le tient à jour chaque nuit : il collecte les sources, en tire des fiches, extrait de chaque fiche les notions et leurs relations, puis compose le graphe, sous plafond de dépense. Chaque réponse tirée du graphe cite sa fiche et sa ligne.

Pour des développeurs qui ont déjà Claude Code : chaque appel au modèle passe par `claude -p`, sous votre abonnement.

## Démarrage rapide

Il faut Python 3.12 ou plus récent, git et Claude Code (`claude`) connecté.

```sh
pip install "newskit @ git+https://github.com/lfoletti/newskit@v0.1.5"
mkdir mon-media && cd mon-media
newskit temoin nuit
```

La dernière commande fait passer une nuit entière au moteur sur un graphe sans rapport (l'apiculture médiévale), rejouée de réponses enregistrées : aucun appel payant, une minute environ. Elle doit finir par « témoin vert ».

Puis, dans Claude Code :

```text
/plugin marketplace add lfoletti/newskit
/plugin install newskit@newskit
```

Demandez ensuite à Claude de suivre un sujet (« fais naître un graphe de veille sur les potagers urbains »). Le skill `newskit` cherche le `config.yaml` du média et, s'il manque, pose ses questions, propose des thèmes tirés des sources que vous donnez, fait naître le graphe et lance une première nuit sous vos yeux.

## Le dossier du média

Un média est un dossier qui porte un `config.yaml`. Le moteur s'y accroche : la variable `NEWSKIT_DONNEES` d'abord, sinon le dossier courant. Le code reste dans le paquet installé ; les données (fiches, graphe, registre des coûts) vivent dans le dossier du média.

```yaml
version: 1
media:
  nom: potagers
  langue: fr                  # la langue des consignes et de la publication
  traductions: [en]           # fil et lettre traduits ; aucune par défaut
  frequence: {heure: 5, minute: 30}
modeles:                      # un modèle par rôle ; un rôle absent garde son défaut
  extraction: claude-opus-5-5
  redaction: claude-sonnet-5-5
sources: sources.yaml         # le fichier des sources, à part
# registre_des_graphes: graphes.json   # libellés et sujets déclarés, tenus par le moteur (défaut)
graphes:
  potagers-urbains:
    libelle: Potagers urbains
    domaine: "urban gardening: allotments, soil, seed exchange"
```

`newskit media` dit ce qui est lu ; un champ inconnu est refusé et nommé. Les rôles : `extraction`, `analyse`, `redaction`, `lecture`, `traduction`, `questions`.

## Les commandes

| Commande | Ce qu'elle fait |
| :- | :- |
| `newskit` | les commandes, une ligne chacune |
| `newskit media` | le média lu : langue, traductions, heure de la nuit, modèle par rôle |
| `newskit nouveau-graphe <nom>` | fait naître un graphe, ou dit ce qui lui manque (`--controle`) |
| `newskit routine --graphe <nom>` | une nuit : collecte, fiches, couche 2 sous plafond, graphe |
| `newskit graphe --graphe <nom> chercher "…"` | interroge le graphe, sans appel au modèle ; chaque réponse porte sa provenance |
| `newskit couts --par graphe` | la dépense des sept derniers jours |
| `newskit temoin nuit` | le témoin du jour ; une nuit ne part pas sans lui |

## Les gardes

- **Le témoin.** Une nuit refuse de partir sans un témoin vert du jour et de la même version du moteur.
- **Les plafonds.** Chaque étape a son plafond de fiches et de dépense ; chaque appel au modèle entre au registre des coûts, sous un poste et un graphe.
- **La limite d'usage.** Après trois limites d'affilée, le moteur pose une marque qui vaut trois heures. Le crochet du plugin refuse alors tout appel au modèle lancé depuis Claude Code.

## Limites de la version 0.1

- Les consignes aux modèles sont écrites en français ; la seule traduction est l'anglais.
- macOS et Linux. Les nuits planifiées passent par launchd sur macOS ; sous Linux, par une ligne de cron qui lance `newskit temoin nuit` puis `newskit routine`.
- `newskit nouveau-graphe` écrit la tâche planifiée du graphe sous `taches/`, prête à poser dans `/Library/LaunchDaemons`.

## Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md). Une faille se signale en privé : [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE).

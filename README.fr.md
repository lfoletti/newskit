# Newskit

[![ci](https://github.com/lfoletti/newskit/actions/workflows/ci.yml/badge.svg)](https://github.com/lfoletti/newskit/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![version](https://img.shields.io/github/v/tag/lfoletti/newskit?label=version)](CHANGELOG.md)

[English](README.md) | Français

Une veille automatique pour Claude Code. Vous nommez un sujet et donnez quelques sources ; à intervalles réguliers, Newskit les collecte, écrit une fiche par article, en extrait les notions et leurs relations et tient à jour un graphe de connaissances, sous plafond de dépense. Deux choses le distinguent d'un lecteur de flux : chaque réponse tirée du graphe cite sa fiche et sa ligne ; le graphe s'étend de lui-même à des thèmes et à des sources nouveaux.

Pour des développeurs qui ont déjà Claude Code : chaque appel au modèle passe par `claude -p`, sous votre abonnement.

[![governance.news, un média sur la gouvernance de l'IA bâti sur Newskit](docs/governance-news-fr.png)](https://governance.news/)

*[governance.news](https://governance.news/) : un bulletin et un graphe de connaissances sur la gouvernance de l'IA en entreprise, bâtis sur Newskit.*

## Ce que vous obtenez

Tout vit dans le dossier du média, en fichiers ordinaires :

- **Des fiches** : un fichier Markdown par article collecté, sous `sources/`, avec son titre, son auteur, sa date et son adresse.
- **Le graphe** : `graphe/<nom>/graphe.json`, sa carte (`carte.md`, les grands thèmes et leurs notions) et le bilan de chaque passage (`ROUTINE.md`).
- **Des réponses sourcées** : `newskit query` cherche dans le graphe sans appel au modèle et donne, pour chaque réponse, la fiche et la ligne qui l'attestent.
- **Un fil et une lettre** : des billets courts sur les derniers articles (`fil.json`) et une lettre du graphe, envoyée par votre propre module de diffusion (`diffusion:` dans `config.yaml`).

```text
$ newskit query --graph ai-governance sources "general-purpose AI model"
  Sources attestant « General-purpose AI models »
    sources/ai_compliance/rss/ec_digital/2026-08-02_navigating-ai-act.md L211
```

## Démarrage rapide

Il faut Python 3.12 ou plus récent, git et Claude Code (`claude`) connecté.

1. Installer le moteur et l'essayer dans un dossier vide :

   ```sh
   pip install "newskit @ git+https://github.com/lfoletti/newskit@v0.1.5"
   mkdir mon-media && cd mon-media
   newskit test
   ```

   L'essai fait faire au moteur un passage entier sur un graphe d'exemple (l'apiculture médiévale), avec des réponses du modèle rejouées d'un enregistrement : aucun appel payant, une minute environ. Il doit finir par « témoin vert » (essai réussi).

2. Ouvrir Claude Code dans ce dossier (`claude`) et installer le plugin :

   ```text
   /plugin marketplace add lfoletti/newskit
   /plugin install newskit@newskit
   ```

3. Demander à Claude de suivre un sujet : « fais naître un graphe de veille sur les potagers urbains ». Le skill `newskit` pose ses questions, écrit `config.yaml` et `sources.yaml`, propose des thèmes tirés des sources que vous donnez, fait naître le graphe et lance un premier passage sous vos yeux.

4. Lire le résultat : `graphe/<nom>/ROUTINE.md` pour le bilan, `newskit query --graph <nom> search "…"` pour le graphe.

5. Planifier les passages suivants. `newskit new-graph` a écrit la tâche du graphe sous `taches/` et imprimé les deux lignes `sudo` qui la posent dans launchd (macOS). Sous Linux, ajouter une ligne de cron à chaque heure de `passages`, qui lance `newskit test` puis `newskit run --graph <nom> --scheduled` ; un jour hors de l'intervalle, le passage planifié s'arrête de lui-même.

## Les sources

Les sources se déclarent dans `sources.yaml`, à côté de `config.yaml`, un bloc par source, chacune rattachée à un graphe. Pris en charge : flux RSS, plans de site et pages de liste (pour les sites sans flux), Substack, Medium, lettres LinkedIn, arXiv, revues par ISSN, OpenAlex, recherches GitHub et Hugging Face, une boîte de courriel, des dossiers locaux de fichiers Markdown ou PDF.

```yaml
version: 1
sources:
  - cle: city_farmer
    plateforme: Flux RSS
    nom: City Farmer News
    dossier: potagers/rss
    abonnement: gratuit
    graphe: potagers-urbains
    flux: https://cityfarmer.info/feed/
  - cle: soil_papers
    plateforme: OpenAlex
    nom: OpenAlex, urban soil
    dossier: potagers/openalex
    abonnement: gratuit
    graphe: potagers-urbains
    requetes: ["urban allotment soil contamination"]
```

Le skill écrit ce fichier pour vous à partir des adresses que vous donnez ; une source que le moteur ne sait pas lire est refusée avec sa raison.

## Comment le graphe grandit

Le graphe ne reste pas dans les sujets déclarés au départ : il s'étend.

- **Thèmes nouveaux.** Après chaque passage, le moteur cherche les groupes de notions qu'aucun sujet déclaré ne couvre. Un candidat qui revient trois passages de suite entre parmi les sujets ; les sources et les recherches le suivent.
- **Auto-complétion.** Une question à laquelle le graphe n'a pas su répondre devient une recherche du passage suivant. Un éclaireur cherche sur le web autour des sujets déclarés ; un poseur de questions interroge le graphe comme le ferait un praticien ; ce qu'un modèle de langage tient pour essentiel et que le graphe n'a pas devient une liste de sources à trouver.
- **La liste des sources.** Le moteur étudie un domaine candidat avant de le déclarer et garde la mémoire de chaque source qu'un lecteur dépose (un lien envoyé depuis le navigateur). Une source qui échoue plusieurs fois est auditée et, quand une voie existe, réparée d'elle-même (voir accesspath plus bas).
- **Des notions nettes.** La même notion sous plusieurs libellés (traduction, sigle, variante) est fondue après qu'un modèle a confirmé qu'il s'agit exactement de la même ; les refus sont gardés et jamais redemandés.

## Le dossier du média

Le média est un dossier qui porte un `config.yaml`. Le moteur s'y accroche : la variable `NEWSKIT_DONNEES` d'abord, sinon le dossier courant. Le code reste dans le paquet installé ; les données (fiches, graphe, registre des coûts) vivent dans le dossier du média.

```yaml
version: 1
media:
  nom: potagers
  langue: fr                  # la langue des consignes et de la publication
  traductions: [en]           # fil et lettre traduits ; aucune par défaut
  passages: {tous_les: 1, heures: ["05:30"]}   # tous les N jours, à ces heures
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
| `newskit media` | le média lu : langue, traductions, passages, modèle par rôle |
| `newskit new-graph <name>` | fait naître un graphe, ou dit ce qui lui manque (`--check`) |
| `newskit run --graph <name>` | un passage : collecte, fiches, extraction des notions sous plafond, graphe |
| `newskit query --graph <name> search "…"` | interroge le graphe, sans appel au modèle ; chaque réponse porte sa provenance |
| `newskit costs --by graph` | la dépense des sept derniers jours |
| `newskit test` | essaie le moteur sur un graphe d'exemple, sans frais ; un vrai passage ne part qu'après un essai réussi le même jour |

## Les gardes

- **La répétition.** Avant le vrai passage, le moteur le répète sur le graphe d'exemple, avec des réponses enregistrées. Le vrai passage ne part que si une répétition a réussi le même jour avec la même version du moteur. Une mise à jour du moteur ne peut donc pas casser un passage sans qu'on le voie : il s'arrête avant de rien dépenser.
- **Les plafonds.** Chaque étape a son plafond de fiches et de dépense ; chaque appel au modèle entre au registre des coûts, sous une étape et un graphe.
- **La limite d'usage.** Après trois limites d'affilée, le moteur pose une marque qui vaut trois heures. Le crochet du plugin refuse alors tout appel au modèle lancé depuis Claude Code.

## Avec d'autres outils

Newskit s'appuie sur :

- **[graphify](https://github.com/Graphify-Labs/graphify)**. Lit la structure des documents, valide chaque extraction contre le schéma, compose le graphe et le découpe en communautés.
- **[trafilatura](https://trafilatura.readthedocs.io)**. Extraction du texte principal des pages web.

Les outils suivants en étendent les fonctionnalités quand ils sont installés :

- **[accesspath](https://github.com/lfoletti/accesspath)** (sap). Audit d'accès aux sites web. Quand une source échoue plusieurs fois, Newskit demande à sap quelle voie l'atteint encore (flux RSS, API, plan du site, HTML simple, navigateur) et répare la source.
- **[cortexdb](https://github.com/lfoletti/cortexdb)**. Mémoire d'IA et graphe de connaissances dans un seul fichier SQLite. Un graphe peut être versé dans un cerveau cortexdb, chaque relation rattachée au passage qui la soutient, puis interrogé depuis tout client MCP.
- **[thalamus](https://github.com/lfoletti/thalamus)**. Une couche de gouvernance sur cortexdb pour le savoir partagé d'une organisation. Les questions restées sans réponse chez ses clients reviennent au moteur comme recherches.

## Limites de la version 0.1

- Les consignes aux modèles sont écrites en français ; la seule traduction est l'anglais.
- macOS et Linux. Les passages planifiés passent par launchd sur macOS ; sous Linux, par une ligne de cron qui lance `newskit test` puis `newskit run`.
- `newskit new-graph` écrit la tâche planifiée du graphe sous `taches/`, prête à poser dans `/Library/LaunchDaemons`.

## Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md). Une faille se signale en privé : [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE).

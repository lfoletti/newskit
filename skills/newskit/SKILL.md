---
name: newskit
description: Créer et tenir un graphe de veille sur n'importe quel sujet, avec le moteur Newskit. Le skill trouve ou écrit le config.yaml du média (formulaire), propose les sources et les thèmes, fait naître le graphe, lance une nuit, lit le bilan et l'interroge. Utiliser quand on veut suivre un domaine dans le temps, faire naître un graphe, lancer ou relire une nuit de veille, ou interroger un graphe Newskit.
---

# Newskit : un graphe de veille, tenu chaque nuit

Newskit est un moteur sans thème. Il collecte des sources, en tire des fiches, extrait de chaque fiche les notions et leurs relations (la couche 2), et tient le graphe à jour chaque nuit, sous plafond de dépense. Ce skill le pilote : il ne fait pas le travail du modèle lui-même, il lance la commande `newskit` qui le fait.

Avant tout : `newskit --version` doit répondre. Sinon, l'installer (`pip install "newskit @ git+https://github.com/lfoletti/newskit@v0.1.0"`) et le redire à l'utilisateur.

## 1. Trouver le média

Un média est un dossier qui porte un `config.yaml` avec un bloc `media:`. Le moteur s'y accroche dans cet ordre : la variable `NEWSKIT_DONNEES`, sinon le dossier courant s'il porte ce fichier, sinon le dossier courant tel quel.

Lancer `newskit media`. La commande dit le média lu, sa langue, ses traductions, l'heure de sa nuit et le modèle de chaque rôle. Si elle répond qu'il n'y a aucun `config.yaml`, passer au formulaire.

## 2. Le formulaire

Le fichier reste la source : le formulaire ne fait que le remplir. Poser les questions par l'outil de questions (AskUserQuestion), quatre au plus par appel, avec des choix proposés ; la réponse libre reste possible. Au second passage, montrer les valeurs déjà présentes comme premier choix.

Les champs :

- `media.nom` : minuscules, chiffres et tirets.
- `media.langue` : la langue des consignes et de la publication. Le moteur écrit aujourd'hui en `fr` seulement.
- `media.traductions` : les langues traduites (fil et lettre), `[en]` ou aucune.
- `media.frequence` : l'heure de la nuit d'un graphe neuf, `{heure: 5, minute: 30}` par défaut.
- `modeles` : un modèle par rôle ; un rôle absent garde le modèle par défaut (`newskit media` les montre). Les rôles : `extraction` (lire une fiche et en tirer la couche 2), `analyse` (juger un graphe entier), `redaction` (synthèses et lettre), `lecture` (trancher : même notion ?), `traduction`, `questions` (questions d'entraînement de l'encodeur).
- `sources` : le fichier des sources, relatif au dossier (`sources.yaml`).
- `diffusion` : le module qui sert les abonnés du média (`envoyer`, `lire`, `relever`). Facultatif : sans lui, aucune lettre ne part, et le moteur le dit.
- `graphes` : un bloc par graphe (libellé, domaine en anglais pour l'extraction).

Écrire `config.yaml` avec `version: 1`, puis relancer `newskit media` : un champ refusé est nommé par le message, le corriger et relancer.

## 3. Les sources de départ et les thèmes

Demander, en option, une ou plusieurs sources : une adresse, un flux RSS, un dossier de fichiers Markdown. Lire chacune (WebFetch pour une page, lecture des fichiers pour un dossier) et en tirer cinq à douze thèmes. Les montrer dans une question à choix multiples pour les garder, les retirer ou les renommer. Sans source donnée, demander les thèmes directement.

Déclarer chaque source dans le fichier des sources, avec `graphe: <nom>`. Une source qui résiste (403, page faite en JavaScript, mur anti-robot) se profile d'abord avec un outil d'audit d'accès, s'il est installé.

## 4. Faire naître le graphe

    newskit nouveau-graphe <nom> --libelle "…" --domaine "…" --sujets "thème 1 ; thème 2"

La commande pose les dossiers et le modèle de tâche planifiée, et imprime le bloc `graphes.<nom>` à coller dans `config.yaml` : le registre est commenté à la main et ne se réécrit pas par programme. `--controle` dit les pièces présentes et manquantes sans rien écrire ; le relancer jusqu'à ce que rien ne manque. En 0.1.0, les pièces de l'installateur des daemons et du serveur ne valent que pour un média qui porte ces scripts : ailleurs, les laisser manquantes et installer la tâche planifiée à la main.

## 5. Le témoin du jour

`newskit temoin nuit` fait passer une nuit entière au moteur sur un graphe sans rapport (l'apiculture médiévale), avec des réponses enregistrées et sans aucun appel payant. Le lancer dans le dossier du média : vert, il pose `cache/temoin_vert`, avec la date et la version du moteur. Une nuit refuse de partir sans un témoin vert du jour et de la même version ; la tâche planifiée le lance d'abord.

## 6. Une nuit

    newskit routine --graphe <nom> --sans-commit --sans-courriel

La première fois, la lancer à la main, sous les yeux de l'utilisateur : elle collecte, écrit les fiches, extrait la couche 2 sous plafond et inscrit chaque appel au modèle au registre des coûts. Le bilan s'imprime étape par étape (`ok`, `--`, `sauté`, `ÉCHEC`). Le relire avec l'utilisateur : une étape en échec dit pourquoi.

Ensuite, les nuits partent de la tâche planifiée. `newskit couts --par graphe` dit ce que les sept derniers jours ont coûté.

Un crochet du plugin refuse tout appel au modèle quand la fenêtre d'usage est fermée (la marque `cache/limite_atteinte` du média, posée après trois limites d'affilée, vaut trois heures). Ne pas la contourner : attendre qu'elle tombe.

## 7. Interroger le graphe

`newskit graphe --graphe <nom> chercher "…"`, puis `voisinage`, `chemin`, `expliquer`, `sources`, `fiche`. Aucun appel au modèle ; chaque réponse porte sa provenance (fiche et ligne). Citer la provenance dans la réponse à l'utilisateur.

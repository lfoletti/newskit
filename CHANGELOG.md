# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent [SemVer](https://semver.org/lang/fr/).

## [Non publié]

## [0.1.0] - 2026-11-07

Première version, sortie du dépôt de l'édition où le moteur est né.

### Ajouté

- Le paquet `newskit` et la commande `newskit` : un module du moteur par sous-commande (`newskit routine`, `newskit nouveau-graphe`, `newskit graphe`, `newskit media`, `newskit temoin`, `newskit couts`).
- Le dossier du média : un `config.yaml` (identité, langue, traductions, heure de la nuit, un modèle par rôle, fichier des sources, module de diffusion, graphes), lu depuis `NEWSKIT_DONNEES` ou le dossier courant.
- Le témoin : une nuit entière sur un graphe sans rapport, rejouée de réponses enregistrées, sans appel payant (`newskit temoin nuit`) ; une nuit ne part pas sans un témoin vert du jour.
- Le plugin de Claude Code : le skill `newskit` (formulaire du média, sources et thèmes de départ, naissance, nuit, interrogation) et un crochet qui refuse les appels au modèle quand la fenêtre d'usage est fermée.

[Non publié]: https://github.com/lfoletti/newskit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lfoletti/newskit/releases/tag/v0.1.0

# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent [SemVer](https://semver.org/lang/fr/).

## [Non publié]

### Modifié

- `media.passages: {tous_les, heures}` remplace `media.frequence` : plusieurs passages par jour, ou un tous les N jours. La tâche planifiée part à chaque heure avec `--planifie` ; un jour hors de l'intervalle, la routine s'arrête avant le témoin. `frequence` reste lue.
- Les commandes en anglais : `run`, `new-graph`, `query … search`, `costs --by`, `test`, avec leurs options (`--graph`, `--check`, `--label`, `--domain`, `--subjects`, `--since`, `--limit`, `--no-commit`, `--no-email`…). Les noms français restent acceptés.

### Modifié

- README en anglais (`README.md`) et en français (`README.fr.md`) : comment le graphe grandit, la répétition expliquée, les outils qui l'étendent. Descriptions du plugin en anglais.

## [0.1.5] - 2026-11-07

### Corrigé

- Couche 2 : un appel au modèle ne part plus avant que le résultat précédent soit écrit. Une étape tuée perdait des réponses payées, qui étaient repayées au passage suivant.

## [0.1.4] - 2026-11-07

### Modifié

- Le registre des graphes (libellés, résumés, sujets déclarés, que les thèmes émergents réécrivent chaque nuit) vit dans le dossier du média, `graphes.json` ; la clé `registre_des_graphes` de `config.yaml` le place ailleurs. Il était fixé au registre d'un skill de la première édition.

## [0.1.3] - 2026-11-07

### Modifié

- `newskit nouveau-graphe` écrit une tâche planifiée qui lance le moteur installé (`-m newskit.routine_hebdo`) sur le dossier du média, avec l'interpréteur qui porte le paquet ; sans installateur des daemons, elle est prête à poser sous `taches/`. Les pièces des installateurs ne sont demandées qu'au média qui les porte.
- La consigne de traduction des libellés nomme le graphe traduit (son libellé), plus un thème fixe.
- La métadonnée d'identifiant des entités versées dans CortexDB s'appelle `newskit_id` ; l'empreinte d'un versement la lit sous son ancien nom, si bien qu'aucun cerveau n'est reversé en entier. Le relevé des lacunes se présente au gateway comme `newskit-releve`.

### Ajouté

- L'icône du plugin.

## [0.1.2] - 2026-11-07

### Corrigé

- Un article, une fiche, une langue : la collecte n'écrit plus une seconde fiche pour la version d'un article dans une autre langue. Le contrôle des doublons d'identité nomme les fiches d'un même document.

## [0.1.1] - 2026-11-07

### Corrigé

- pytest s'installe avec le paquet : le témoin, sans lequel aucune nuit ne part, tombait sur « No module named pytest » après un `pip install` simple. L'extra `temoin` disparaît.
- Le vocabulaire anglais est livré avec le moteur (web2) : sous Linux, sans liste de mots du système, le tri des libellés à traduire changeait.

## [0.1.0] - 2026-11-07

Première version, sortie du dépôt de l'édition où le moteur est né.

### Ajouté

- Le paquet `newskit` et la commande `newskit` : un module du moteur par sous-commande (`newskit routine`, `newskit nouveau-graphe`, `newskit graphe`, `newskit media`, `newskit temoin`, `newskit couts`).
- Le dossier du média : un `config.yaml` (identité, langue, traductions, heure de la nuit, un modèle par rôle, fichier des sources, module de diffusion, graphes), lu depuis `NEWSKIT_DONNEES` ou le dossier courant.
- Le témoin : une nuit entière sur un graphe sans rapport, rejouée de réponses enregistrées, sans appel payant (`newskit temoin nuit`) ; une nuit ne part pas sans un témoin vert du jour.
- Le plugin de Claude Code : le skill `newskit` (formulaire du média, sources et thèmes de départ, naissance, nuit, interrogation) et un crochet qui refuse les appels au modèle quand la fenêtre d'usage est fermée.

[Non publié]: https://github.com/lfoletti/newskit/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/lfoletti/newskit/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/lfoletti/newskit/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lfoletti/newskit/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lfoletti/newskit/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/lfoletti/newskit/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lfoletti/newskit/releases/tag/v0.1.0

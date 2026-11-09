# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent [SemVer](https://semver.org/lang/fr/).

## [0.1.17] - 2026-11-09

### Modifié

- La typographie de tout texte publié rend insécable l’espace entre un nombre et « % », en toute langue ; sans espace, rien ne change.

## [0.1.16] - 2026-11-09

### Ajouté

- `media_sample/`, un dossier de média complet à copier.

### Modifié

- Un billet a la même forme en français et en anglais dans la copie publique du fil : la fiche développée paraît seulement si elle existe dans les deux langues, sinon le billet paraît en brève dans les deux ; un billet sans version anglaise tenue ne paraît pas.

## [0.1.15] - 2026-11-08

### Modifié

- 30 mots au plus par phrase partout, lettre comprise. Les gabarits anglais demandent en première phrase ce que la source établit.

## [0.1.14] - 2026-11-08

### Ajouté

- Les règles de rédaction peuvent se lire dans un fichier du média, déclaré par `regles_redaction`.

## [0.1.13] - 2026-11-08

### Modifié

- Le relecteur refuse un texte dont la première phrase ne dit que la parution ou l'intervention.

## [0.1.12] - 2026-11-08

### Modifié

- Dans un titre, l'attribution vient en tête.

## [0.1.11] - 2026-11-08

### Ajouté

- Les fiches portent un en-tête (`meta`) et l'auteur de la source. L'arbre déclare ses champs dans `champs_entete`.

## [0.1.10] - 2026-11-08

### Ajouté

- Fiches du fil au format résumé, catégorie, éléments, en français et en anglais, pour un graphe qui déclare `arbre_fiches`. Chaque élément est prouvé par une citation exacte de la source, gardée hors de la copie publique. La rédaction et la relecture lisent l'article entier.
- Une fiche tenue donne le titre et le chapeau du billet, repris par le fil et la lettre. Pour un graphe à arbre, le billet attend sa fiche avant de paraître.
- Un graphe peut déclarer `fiche_modele` et `fiche_usd_jour`. Il peut aussi déclarer `fil_usd_jour`, le plafond du jour du fil.

## [0.1.9] - 2026-11-07

### Modifié

- Les noms des cerveaux de Thalamus se déclarent dans `config.yaml` : `media.cerveau_du_site`, le cerveau que la station « Interroger le graphe » interroge ; `media.cerveau_lacunes`, le cerveau où un lecteur signale ce qu'un graphe n'a pas su lui dire (relève et carte servie). Vides, ils gardent les noms d'avant : « <média>/<graphe du site> » et « lacunes-signalees ». Minuscules, chiffres et « -/ » seulement.

## [0.1.8] - 2026-11-07

### Modifié

- Le service de mise à jour du serveur se déclare dans `config.yaml` (`media.service_maj`) : le moteur ne lance plus `kg-mcp-maj`, le nom d'un service de la première édition. Sans clé, rien n'est lancé après un passage ; le serveur se met à jour à son heure. Le nom part dans une commande ssh : seuls lettres, chiffres et « @._- » sont admis.

## [0.1.7] - 2026-11-07

### Modifié

- Newskit est publié sur PyPI : `pip install newskit`. Publication par Trusted Publishing à chaque tag, répétition sur TestPyPI (`testpypi.yml`). Description du paquet en anglais ; liens du README absolus, pour la page PyPI.
- Format du code vérifié en CI et au pre-push (`ruff format --check`) ; modèles d'issue et de PR en anglais ; Discussions.

## [0.1.6] - 2026-11-07

### Modifié

- Le mot « nuit » devient « passage » dans le skill, l'aide et les messages ; `newskit temoin passage`, `nuit` restant accepté.
- `media.passages: {tous_les, heures}` remplace `media.frequence` : plusieurs passages par jour, ou un tous les N jours. La tâche planifiée part à chaque heure avec `--planifie` ; un jour hors de l'intervalle, la routine s'arrête avant le témoin. `frequence` reste lue.
- Les commandes en anglais : `run`, `new-graph`, `query … search`, `costs --by`, `test`, avec leurs options (`--graph`, `--check`, `--label`, `--domain`, `--subjects`, `--since`, `--limit`, `--no-commit`, `--no-email`…). Les noms français restent acceptés.
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

[Non publié]: https://github.com/lfoletti/newskit/compare/v0.1.7...HEAD
[0.1.7]: https://github.com/lfoletti/newskit/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/lfoletti/newskit/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/lfoletti/newskit/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/lfoletti/newskit/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lfoletti/newskit/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lfoletti/newskit/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/lfoletti/newskit/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lfoletti/newskit/releases/tag/v0.1.0

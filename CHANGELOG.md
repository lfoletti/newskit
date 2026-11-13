# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent [SemVer](https://semver.org/lang/fr/).

## [0.1.34] - 2026-11-13

### Modifié

- La version anglaise d’une suite garde son angle. Sa rédaction et sa réécriture reçoivent le fait neuf (« developpement »). Son titre et la première phrase de son texte le nomment, sinon elle est réécrite avec ce reproche. Une version anglaise qui ne le nomme jamais n’est pas tenue.
- Inventaire fait : le titre et le texte d’un billet s’écrivent à quatre endroits (la relecture, la révision des titres, la fiche, la version anglaise). Les quatre tiennent maintenant l’angle d’une suite.

## [0.1.33] - 2026-11-13

### Modifié

- La fiche d’une suite garde son angle. Sa rédaction reçoit le fait neuf (« developpement »). Son titre et la première phrase de son résumé le nomment dans les deux langues. Sinon la fiche est refusée et réécrite avec ce reproche. Le billet garde son titre et son chapeau tant que sa fiche n’est pas tenue.

## [0.1.32] - 2026-11-13

### Modifié

- Une suite fait de son fait neuf son angle. Le titre et la première phrase de son texte nomment ce fait. Sinon la relecture le reproche et fait réécrire le billet, avec le fait neuf sous les yeux. Une suite qui ne tient pas devient une source de plus de sa nouvelle (`sans_angle`, `fondre_sans_angle`).
- La révision des titres refuse, pour une suite, un titre qui ne dit pas le fait neuf. Les candidats reçoivent ce fait.
- Le rattrapage fait relire une nouvelle qu’il change en suite : elle est réécrite sur son fait neuf.
- Deux suites de la même nouvelle se comparent une fois, même rapprochées. Si elles portent le même fait, la seconde devient une source de plus de la première.
- Le fait neuf se mesure contre toute l’affaire : la chaîne des suites avec leurs sources attachées, puis les nouvelles du même contexte entrées avant. Ni le nom qui suit « d’après », « selon » ou « according to », ni l’éditeur de la source ne comptent plus comme un fait. Une source qui redit le fait d’une suite s’attache à cette suite.
- Une suite garde ses termes neufs (`neuf`).

## [0.1.31] - 2026-11-11

### Modifié

- La lecture audio lit chaque morceau du billet à part : le titre, le chapeau, puis chaque paragraphe. Les intertitres restent absents. ffmpeg assemble les morceaux avec un silence après chacun : 1,2 s après le titre, 1,0 s après le chapeau, 0,8 s après un paragraphe.
- La clé `pauses` de `lecture_audio` change ces durées, de 0 à 3 secondes. L’empreinte d’un son suit les pauses : une durée changée refait les sons.
- Tous les sons se refont une fois, car la façon de lire change (version 2).

## [0.1.30] - 2026-11-11

### Ajouté

- `python -m newskit.lecture_audio` : la lecture audio des billets, un processus à part du fil. Il lit la copie publique du fil sur le site, donne un son à chaque billet qui n’en a pas d’à jour, du plus récent au plus ancien. Il dépose ensuite sur le VPS les sons (Opus, puis AAC), puis leur index `audio.json`. Après chaque billet, il relit la copie : un billet entrant passe avant le retard.
- `kyutai_lecteur.py` : le lecteur Kyutai TTS (MLX), lancé par l’interpréteur que nomme `NEWSKIT_AUDIO_PYTHON`. Il garde le modèle chargé le temps du passage. Un billet qui dépasse 15 minutes arrête le lecteur.
- La clé `lecture_audio` du `config.yaml` du média : `modele`, `voix`, `vitesse` (de 0,8 à 1,2) et `sigles` (sigle → forme longue, développée à sa première apparition).

## [0.1.29] - 2026-11-11

### Modifié

- Au palier 2 du budget du fil (80 % du plafond du jour), la version anglaise des billets continue avec la rédaction, jusqu’au plafond. Un billet sans version anglaise ne paraît pas sur le site : le palier arrêtait donc la parution.
- Le courriel du palier 2 nomme les tâches arrêtées : fiches, liens et définitions.

## [0.1.28] - 2026-11-11

### Ajouté

- La clé `lettre_aux_abonnes: true` du `config.yaml` du média envoie la daily aux abonnés sans validation, après l’envoi à l’adresse du registre. La diffusion de l’édition (`diffusion.envoyer`) sert la liste, comme pour la weekly.
- Une diffusion en échec est signalée sur la sortie d’erreur ; le rapport de routine part quand même.
- La clé n’admet que `true` ou `false`. Sans elle, la daily ne part qu’à l’adresse du registre, comme avant.

## [0.1.27] - 2026-11-11

### Ajouté

- Le bloc `reglages:` du `config.yaml` du média règle les constantes propres au média : le fil, la fiche, les alertes, la copie publique, la lettre, la lettre de la semaine, la routine et la collecte RSS. Le moteur garde la valeur par défaut.
- Un réglage inconnu ou d’un autre type que la valeur par défaut est refusé au chargement.

### Modifié

- Les arguments de la lettre réglés par le média lisent leur valeur à l’appel, pas à l’import.

## [0.1.26] - 2026-11-11

### Ajouté

- Un graphe déclare la longueur de son fil par `fil_max` dans sa configuration. Sans déclaration, le fil garde 300 billets (`FIL_MAX`).

## [0.1.25] - 2026-11-11

### Corrigé

- La docstring de `rapport_objet` ne nomme plus un graphe d'une édition : le moteur ne porte aucun nom d'édition.

## [0.1.24] - 2026-11-11

### Modifié

- Le fil ne s'arrête plus sur une étape payante. Un billet rédigé attend sa fiche 20 minutes au plus. Il n'attend pas quand la fiche ne peut pas s'écrire (plafond des fiches, budget secondaire, limite d'usage). Sa fiche s'écrit ensuite, comme celle d'un billet déjà paru.
- Le budget du jour sert d'abord la rédaction. À 80 % du plafond (`PART_SECONDAIRE`), le rapprochement, la relecture des billets d'avant, les définitions, les liens, l'anglais et les fiches s'arrêtent. La rédaction des billets neufs continue jusqu'au plafond.
- La pile garde une réserve de 6 billets (`PILE_GARDE`) contre les heures sans nouvelle. En dessous, un billet paraît toutes les 30 minutes de 06 h à 23 h, aucun la nuit, où la réserve se refait. Pendant la routine du graphe, l'étalement prime.

### Ajouté

- Alertes du fil par paliers (`alertes_fil`), après chaque passage de la collecte continue. Palier 2, un courriel par jour et par cause : budget à 80 %, 60 minutes sans billet de jour avec une pile non vide, publication sur le serveur en échec 3 fois de suite. Palier 3, un courriel toutes les 2 heures tant que la cause dure : plafond atteint, limite d'usage, passage du fil en échec 2 fois de suite, 3 heures sans billet de jour. L'alerte unique de 4 heures disparaît.

## [0.1.23] - 2026-11-11

### Modifié

- Le mainteneur ne reçoit plus la WIP (la daily numérotée avec les coulisses). Il reçoit la daily, comme les abonnés, puis un rapport de routine : les chiffres du passage, sans la lettre. Objet : « Rapport de routine — <graphe> — <date> — <n> points à suivre ».
- Le rapport range les chiffres par section : en-tête avec le verdict et quatre tuiles, puis « Corpus et graphe », « Qualité » (actualité, banc), « Coût », « Santé du passage » et « Sujets d'enquête proposés ». Une section sans valeur ne paraît pas. Le verdict est vert sans point à suivre, ambre sinon. Les sujets proposés sont désignés par leur titre. Le rapport est toujours en français.
- La page de travail numérotée (`--essai <dossier> --wip`) reste disponible.

## [0.1.22] - 2026-11-10

### Modifié

- En-tête de la lettre : le logo du site (le graphique, le nom et le sous-titre, ondes comprises) en une image `logo_lettre.png` servie sous `<site>/lettres/`, seule au-dessus des cartes. Le titre et la date ouvrent la première carte, un trait dessous ; au téléphone, la date passe sous le titre. Le compas et l'image de fond en ondes quittent la lettre ; leurs fichiers restent servis pour les lettres déjà parties.
- Rendu dans Outlook Windows : la colonne est bornée à 600 px par une table conditionnelle, l'échelle reste à 96 ppp, les filets et les espaces sont des cellules, les numéros de la une s'alignent sans `valign="baseline"`, les tuiles des coulisses restent côte à côte. Segoe UI et Georgia servent de polices de repli.

## [0.1.21] - 2026-11-10

### Corrigé

- Fiche à éléments : le relecteur juge chaque langue sous le titre de la fiche, qui devient celui du billet. Avant, il jugeait sous l'ancien titre du billet. Il refusait les fiches dont le titre réécrit suivait un autre angle de l'article ; le billet restait alors en forme brève.

## [0.1.19] - 2026-11-09

### Ajouté

- Révision des titres du fil : après la relecture, un appel propose trois titres par billet, puis un juge, qui ne voit pas le rédacteur raisonner, choisit entre le titre en place et les propositions. Le titre choisi remplace l'ancien s'il passe les contrôles sans modèle (longueur, question, nom ou nombre absent de la source) ; le billet garde la révision (`revision_titre`).

### Modifié

- Règle de l'attribution dans le titre : le plus souvent, aucune ; le titre dit le fait, la source va dans la première phrase. Le conditionnel pour un fait non établi ; « Selon X » ou « Pour X » en tête seulement quand X fait la nouvelle. Le titre nomme une personne ou une organisation seulement si le lecteur la connaît. Même règle en anglais.

## [0.1.18] - 2026-11-09

### Modifié

- « dit que » est refusé dans le titre et le texte d'un billet français, « says that » et « said that » dans un billet anglais ; « dit » reste permis devant une citation directe entre guillemets.

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

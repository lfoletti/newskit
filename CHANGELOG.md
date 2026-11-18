# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent [SemVer](https://semver.org/lang/fr/).

## [0.1.73] - 2026-11-18

### Ajouté

- La file des fiches d’un graphe continu (`file_fiches`) : qui écrit, change ou retire une fiche le signale par une ligne de `<graphe>.file.jsonl`, dans le dossier des cerveaux. Quatre sortes d’événement : `parue`, `modifiee`, `retiree`, `droits`. `file_fiches.lire` rend les événements en attente ; `file_fiches.acquitter` retire ceux qui sont traités, sous verrou, sans perdre un événement arrivé entre-temps.
- La file n’existe que pour un graphe qui déclare `cerveau_continu: true`, sur la machine où le passage continu a posé sa marque (`file_fiches.ouvrir`). Ailleurs, rien ne s’écrit. `file_fiches.signaler` ne lève jamais : une file illisible n’empêche pas l’écriture d’une fiche.
- `fiche.retirer` efface une fiche et le signale ; `fiche.deplacer` déplace ou copie une fiche et signale le départ et l’arrivée.

### Modifié

- `fiche.ecrire` signale « parue » pour une fiche neuve et « modifiée » quand le texte écrit change. Une réécriture identique ne signale rien.
- Les programmes qui effacent ou déplacent une fiche passent par `fiche.retirer` ou `fiche.deplacer` : la fusion des doublons d’identité, la purge des publicités, la redatation et la répartition des fiches arXiv, la collecte d’un dossier local, la quarantaine d’un graphe naissant.
- `purger_fiches` a une fonction `main` et une fonction `retirer_publicites`.

## [0.1.72] - 2026-11-18

### Ajouté

- L’édition reçoit une clé d’écriture pour le cerveau servi d’un graphe continu (`serveur_cortexdb.assurer_cle` avec `ecriture=True`) : identifiant `<edition>/<graphe>/ecriture`, niveau `read-write`, créée une fois dans le fichier de clés. La clé de lecture reste `read-only`. Mesure sur CortexDB 2.154.6, serveur lancé avec un fichier de clés : une clé `read-only` lit et ne peut pas écrire, une clé `read-write` écrit.
- Le fichier d’adresse du serveur publie cette clé à part, dans le champ `jeton_ecriture` (`serveur_cortexdb.ecrire_adresse`). `lecture_cortexdb.lire_adresse_ecriture` la rend ; `lire_adresse` rend toujours la clé de lecture. Sans fichier de clés, le jeton du serveur écrit déjà : il est publié dans ce champ.
- L’aiguilleur et le gardien d’un graphe créent la clé d’écriture pour un graphe continu seulement (`serveur_cortexdb.cle_d_ecriture`). Un autre graphe n’en a pas.

### Modifié

- Quand une clé de l’édition a été retirée du fichier de clés pendant que l’aiguilleur tourne, le lancement suivant du serveur la recrée et réécrit le fichier d’adresse. Avant, l’adresse publiée gardait l’ancienne clé jusqu’au redémarrage du service.

## [0.1.71] - 2026-11-18

### Modifié

- Le cerveau publié d’un graphe continu (`cerveau_continu: true`) garde ses index CJK (`cerveau_cortexdb.publier` et `publier_en_place`, paramètre `garder_cjk`). Sans eux, CortexDB les recrée vides à l’ouverture et la première réécriture d’un document échoue (« database disk image is malformed »). Mesure sur un cerveau de 4,6 Go : les index pleins pèsent 7,6 % du fichier.
- La copie de travail d’un passage en place ne repart de `user_version` 0 que si les index CJK sont vides (`cerveau_cortexdb.index_cjk_vides`). Sur des index pleins, le remplissage se rejouait en entier : 80 à 100 s de plus à l’ouverture du serveur pour ce même cerveau.
- La mise en place d’un cerveau prêt est une seule fonction, `serveur_cortexdb.mettre_en_place`, pour le gardien d’un graphe et pour l’aiguilleur. Avant, chacun portait sa copie des mêmes lignes.
- Un graphe sans `cerveau_continu` garde son comportement : ses index CJK sont retirés à la publication.

## [0.1.70] - 2026-11-18

### Ajouté

- Un graphe déclare `cerveau_continu: true` au registre (`config_sources.cerveau_continu`) : ses fiches entreront dans son cerveau au fil de la journée, dans le fichier que le serveur tient ouvert. La clé demande `cortexdb: true`. Cette version pose la clé et ses deux garanties ; le passage continu vient ensuite.

### Modifié

- Le cerveau d’un graphe continu ne s’envoie pas à un serveur (`publier_cerveau.continu`), ni lors d’un passage complet ni par un envoi qui le nomme : l’envoi poserait un cerveau plus ancien à la place du cerveau servi.
- Le fichier d’adresse du serveur et l’envoi lisent le dossier des cerveaux de la construction (`cerveau_cortexdb.DOSSIER`, que `CORTEXDB_DOSSIER` déplace). Avant, ils lisaient toujours `cache/cortexdb` du dossier de données : sur une machine où le cerveau servi est rangé ailleurs, la construction ne voyait pas le serveur.
- `publier_cerveau --lister` écrit le chemin entier de chaque cerveau.

## [0.1.69] - 2026-11-18

### Modifié

- Les relecteurs du fil, en français et en anglais, lisent dans la source un sigle développé selon son sens établi dans le pays et le domaine de la source (« PS » : « Parti socialiste », dans un article sur la politique chilienne). Avant, ils comptaient ce développement comme un fait absent de la source : une fiche était refusée 3 fois et son billet retiré.

## [0.1.68] - 2026-11-18

### Modifié

- Pour un graphe qui déclare un arbre des catégories (`arbre_fiches`), le billet trie seulement (`fil.tri_seul`, `fil.CONSIGNE_TRI`) : pertinent, doublon ou suite, rubrique. Il n’écrit ni titre ni texte, en français comme en anglais. Il n’a ni relecture, ni révision des titres, ni complément. Jusqu’à sa fiche, il garde le titre de la source et le début de son texte, qui servent au rapprochement.
- La fiche écrit le billet : elle lit l’article entier et lui donne son titre et son chapeau dans les deux langues (`fil_fiche._reporter`). Sa relecture adverse est la seule. Elle s’écrit sur le budget de la rédaction, pas sur le budget secondaire.
- Un billet trié attend sa fiche tenue dans la pile, sans limite de temps (`fil.provisoire`). Une fiche refusée 3 fois, ou une source qui ne se lit plus, retire le billet sans parution (`fil_fiche.sans_fiche_possible`). Le refus va au journal des refus, que `refus_frequents` et `regles_apprises` lisent.
- La liaison des nouvelles, la relecture des billets d’avant et la version anglaise sautent un billet trié sans fiche tenue.
- Un graphe sans arbre des catégories garde le billet écrit, relu et traduit. Un billet entré dans la pile avant cette version garde aussi son parcours.

## [0.1.67] - 2026-11-18

### Ajouté

- Les motifs de refus récurrents de la relecture deviennent des règles de rédaction du média (`regles_apprises`). Une passe par jour, avant la lettre (`fil.lire_le_reste`), prend les motifs vus au moins 5 fois dans la fenêtre de `refus_frequents` et encore sans règle, 3 au plus, les plus fréquents d’abord. Un appel au modèle écrit pour chacun une règle générale, en français et en anglais ; une règle vide retient un motif que les règles du média couvrent déjà.
- Les règles vivent dans `graphe/<graphe>/regles_apprises.json`, dans le dossier de données du média. `lettre.regles` les ajoute aux règles du média : elles entrent dans toutes les rédactions et réécritures, pas seulement dans la première. Une règle reste jusqu’à ce que le mainteneur la retire du fichier.
- Les règles ajoutées partent au mainteneur par courriel. Un envoi manqué ne retire aucune règle ; une passe en échec ne bloque pas la lettre.

## [0.1.66] - 2026-11-18

### Modifié

- Les rédacteurs et les relecteurs du fil, en français et en anglais, lisent la source à la même longueur, celle du média : la clé `source_max` de la déclaration du graphe (`fil.source_max`), un entier positif. Sans la clé, ils lisent la source entière. Avant, le rédacteur français lisait 1 200 caractères, le relecteur et la version anglaise 3 000 : un article qui ouvre sur une anecdote ne montrait que l’anecdote au rédacteur français. Les deux langues racontaient alors deux histoires.
- La longueur se compte après filtrage (`fil.filtrer`) : les images, les adresses des liens (le texte du lien reste), les adresses nues, les balises et les dièses des titres ne comptent plus.
- Règle d’angle dans les règles par défaut, en français (`lettre.ANGLE`) et en anglais : le titre et le texte portent ce que la source apporte au lecteur, comme son titre l’annonce (mesures à prendre, résultats, recommandations, décision) ; l’anecdote d’ouverture reste du contexte. Les relecteurs français et anglais refusent un titre qui porte cette anecdote.

## [0.1.65] - 2026-11-18

### Ajouté

- Le singulier et le pluriel d’une notion sont candidats à la fusion (`fondre_doublons.singulier`, règle « pluriel » de `candidats`) : deux libellés égaux une fois chaque mot mis au singulier anglais, sans appel ; le modèle tranche ensuite comme pour les autres règles. Un sigle en capitales ou un mot à chiffre reste tel quel (« LPD » et « LPDS » ne se rapprochent pas). L’encodeur rapprochait « Deepfake » et « Deepfakes » à 0,913, sous le seuil de 0,93 : la paire n’était jamais proposée.

## [0.1.64] - 2026-11-18

### Modifié

- La traduction française d’un libellé donne à un nom propre ou à un sigle sa forme française établie (« United Nations » → « Nations unies », « UN » → « ONU ») ; sans forme établie, il reste tel quel. Une recherche en français trouvait une notion sous son seul nom anglais.

### Ajouté

- `python3 -m newskit.francais --revoir-noms N` retraduit les N libellés gardés tels quels les plus reliés (`gardes_tels_quels`, `revoir`). L’ancienne traduction reste dans la table jusqu’à ce que la nouvelle la remplace : un passage interrompu ne laisse aucun libellé sans français.

## [0.1.63] - 2026-11-17

### Ajouté

- Les refus fréquents de la relecture entrent dans la consigne de la première rédaction, pour les billets du fil et pour leurs fiches (`refus_frequents`). Le module lit les reproches des 14 derniers jours dans les journaux du média : `cache/fil_refus.jsonl` et les fiches refusées du registre du fil. Il les regroupe par motif : le terme cité en tête du reproche, sinon la forme du reproche (tête avant « : », nombres réduits, 6 premiers mots). Il porte les 10 motifs les plus fréquents, vus au moins 2 fois, avec leur dernier exemple. Le 17.11.2026, 295 reproches d’ai-governance revenaient sous quelques formes : faits absents de la source (79), titres trop longs (19), fait neuf absent du titre (17), « agents » sans dire qu’il s’agit d’IA (13). Chaque refus coûtait une réécriture.

## [0.1.62] - 2026-11-17

### Corrigé

- Sur une machine sans trousseau macOS (Linux), l’état d’une session vaut « absente ». Avant, l’absence de la commande `security` arrêtait l’envoi du rapport de routine par une erreur, alors que `SMTP_MOT_DE_PASSE` était dans le fichier des secrets.
- Le pilote du cerveau renvoie un appel quand CortexDB répond `no turn to write` : il attend 10, 30, puis 60 s. Le point de contrôle du WAL de CortexDB prend le tour d’écriture. Sur un disque lent, il peut dépasser les 30 s d’attente accordées aux autres écrivains. La reconstruction complète s’arrêtait alors au premier tour perdu, après une heure. Cette erreur tombe avant toute écriture : renvoyer l’appel ne double rien.

## [0.1.61] - 2026-11-17

### Corrigé

- Un passage en place du cerveau ne s’arrête plus à `database disk image is malformed` sur le premier document remplacé. La publication retire les index CJK du cerveau ; CortexDB les recréait vides à l’ouverture de la copie de travail sans les remplir. Leur déclencheur échouait à la première réécriture. La copie de travail repart de `user_version` 0 : CortexDB remplit ces index, puis la publication les retire comme avant.

## [0.1.60] - 2026-11-17

### Corrigé

- La passerelle Thalamus lit le format de sa réponse dans l’en-tête `Content-Type`. Avant, une réponse JSON dont un extrait contenait `data:` était lue comme un flux d’événements et rendue vide. La station du site renvoyait alors l’erreur 503 sur les notions les plus reliées. Un flux sans ligne de données rend aussi une réponse vide, sans erreur de lecture.

## [0.1.59] - 2026-11-16

### Ajouté

- Une boîte aux lettres peut nommer dans `mot_de_passe_env` la variable qui porte son mot de passe. Le moteur la lit dans l’environnement, puis dans `~/.secrets/newskit.env`, avant le trousseau macOS. Une routine sur un serveur Linux, sans trousseau, relève ainsi les lettres reçues par courriel. Le registre porte le nom de la variable, jamais le mot de passe. Sans variable déclarée, rien ne change.

## [0.1.58] - 2026-11-16

### Corrigé

- Un média dont les données sont un dépôt git sans distant publie quand même : la routine ne pousse rien, puis publie le fil, l’index, les graphes et le cerveau comme après une poussée réussie. Avant, elle s’arrêtait à « Pas de dépôt distant » et ne publiait rien. Les dépôts du navigateur ne lèvent plus d’alerte sans distant. Sans distant, la publication envoie aussi au serveur tout fichier suivi par le dépôt de données, sauf les liens : le rapport, les lettres, les éclairages et les fiches n’arrivent plus par le `git pull` du serveur. C’est la forme des données au serveur, où la routine tournera seule.

## [0.1.57] - 2026-11-16

### Modifié

- Un passage en place efface jusqu’à 3 000 documents avec CortexDB v2.154.0 ou plus, contre 300 avant. Cette version trouve ce qu’un document a mis dans le graphe par des index de provenance (liliang-cn/cortexdb#190) : une suppression ne lit plus tout le graphe. Sur une copie d’un graphe de 400 000 nœuds, un `delete_document_graph` prend 0,04 à 0,19 s, contre 0,9 à 1,2 s en v2.147.0. Avec un binaire plus ancien, le plafond reste à 300.

## [0.1.56] - 2026-11-15

### Ajouté

- Un graphe nomme son cerveau de Thalamus. Un graphe qui déclare `cerveau: <nom>` sous `graphes:` est servi sous ce nom, qu’il soit ou non le graphe du site ; sans déclaration, le nom reste `cerveau_du_site` pour le graphe du site et `<média>/<graphe>` pour les autres. Le nom suit la règle des autres cerveaux (minuscules, chiffres et « -/ », 63 caractères au plus) : un autre nom arrête la lecture de `config.yaml`.

## [0.1.55] - 2026-11-15

### Corrigé

- Une longue réponse réussie n’est plus prise pour un refus d’usage. La règle qui reconnaît une limite lisait « rate limit », « quota » ou « overloaded » dans le texte rendu par le modèle : une lecture de plusieurs milliers de mots sur ces sujets était comptée comme un refus ; trois de suite arrêtaient les boucles trois heures (6 lectures sur 78 le 15.11.2026). Un message de plus de 1 500 caractères n’est plus un refus ; un appel que l’API n’a pas reçu en reste un, quelle que soit sa longueur.

## [0.1.54] - 2026-11-15

### Ajouté

- Les tendances d’un graphe, pour l’accueil d’un média. Un graphe qui déclare `tendances: {jours: 30, max: 8, hors: [...]}` garde dans `tendances.json` les notions que plusieurs sources indépendantes ont attestées pendant la fenêtre, lues du graphe composé sans appel au modèle (`newskit tendances --graphe <g>` à la main, à chaque passage de la routine après la composition). Un papier compte pour une source, un dossier d’articles pour une seule : cent billets d’un même blog repris d’un coup ne font pas une tendance. Une notion entre avec deux sources au moins, dans l’ordre du nombre de sources, puis de fiches. Un libellé qui est aussi celui d’un auteur, d’une entité ou d’une publication du graphe est écarté ; le média écarte les autres noms sous `hors`.

## [0.1.53] - 2026-11-15

### Ajouté

- Les rubriques d’un graphe tirées de ses sujets. Un graphe qui déclare `rubriques: {du_graphe: true, max: 7}` ne tient plus de table de rubriques à la main : ses sujets déclarés, que les thèmes émergents font bouger, sont regroupés en `max` rubriques au plus (7 sans `max`, 12 au plus) par un appel au modèle, avec un nom français, un nom anglais et un vocabulaire par rubrique. Le regroupement se garde avec l’empreinte des sujets et de la borne dans `rubriques.json` du graphe : il se refait seulement quand un sujet entre ou sort, ou quand la borne change, à l’étape des thèmes de la routine (`newskit rubriques_du_graphe --graphe <g>` à la main). Une réponse hors contrat (trop de rubriques, un sujet rangé deux fois ou oublié, un nom vide, en double ou de plus de 24 caractères) laisse les rubriques d’avant. Le fil, le flux et la lettre en anglais prennent le nom anglais calculé. Une table déclarée (`{fichier, ordre}`) se lit comme avant.

## [0.1.52] - 2026-11-15

### Corrigé

- Une reconstruction complète du cerveau ne commence plus sans la place qu’elle demande. Elle garde l’ancien cerveau jusqu’à la publication et écrit le neuf à côté : le 15.11.2026, sur un graphe de gouvernance, le neuf faisait 4,2 Go, l’ancien 4,0 ; le versement n’était pas fini quand le disque s’est rempli. Avant de commencer, l’étape compare la place libre à `PLACE_COMPLETE` (2) fois l’ancien cerveau et son journal. Sans cette place, le passage se fait en place s’il le peut (plus lent mais complet) et le bilan dit pourquoi ; sinon (couche 1 changée, reconstruction demandée), il s’arrête avant d’écrire et l’ancien cerveau reste en service.

## [0.1.51] - 2026-11-15

### Corrigé

- Une arête du modèle posée sur une arête de structure ne passe plus pour déterministe. Sur une même paire de nœuds, graphify et la composition reprise de lui en 0.1.49 ajoutaient les champs de la seconde arête à la première : l’arête gardait `_origin` (`ast` ou `squelette`) et gagnait la citation (`evidence`) et le contributeur du modèle ; le puits la versait comme arête déterministe, sans sa citation. D’un producteur à l’autre, la dernière écrite remplace désormais la précédente ; entre deux arêtes du même producteur, la fusion reste, pour qu’une copie sans citation garde celle de la première. Sur les extractions du graphe de la gouvernance du 14.11.2026 (298 425 arêtes), 107 arêtes changent, toutes passées d’un producteur à l’autre ; les 29 arêtes mixtes disparaissent ; l’ensemble et l’ordre des arêtes sont identiques.

## [0.1.50] - 2026-11-14

### Modifié

- Un passage en place qui effacerait trop se fait en reconstruction complète. Avant de cloner le cerveau, le puits compte sans serveur (`puits_cortexdb --compter`) ce qu’un passage en place remplacerait, ajouterait et retirerait, par le même chemin que le versement. Au-delà de `EFFACEMENTS_MAX` (300) appels d’effacement, un par document remplacé et deux par document retiré, le cerveau se reconstruit en entier et le bilan en donne la raison. Un en place forcé (`--en-place`) ne compte pas. CortexDB lit tout le graphe à chaque `delete_document_graph` et `knowledge_delete` : sur un graphe de 396 000 nœuds et 1 043 819 arêtes, 4,0 s et 6,0 s par appel, plus d’une heure d’effacements, contre 32 minutes pour la reconstruction complète. Sur ce graphe, le comptage prend 18 s ; il a trouvé 1 477 effacements.

## [0.1.49] - 2026-11-14

### Retiré

- La dépendance à graphify (`graphifyy`). La structure des titres (`structure_titres`), la validation des extractions et la composition avec sa déduplication (`composition`), les communautés de la carte (`communautes`) vivent dans le paquet, reprises de graphify 0.9.20 (licence MIT, notice gardée en tête de chaque module) sur les seuls chemins que les graphes empruntent. Sur les neuf graphes composables du registre, les quatre sorties (`structure.json`, `graphe.json`, `graphe_stats.md`, `carte.md`) sont identiques octet pour octet à celles de graphify (348 588 nœuds, 746 893 liens) ; les 9 017 communautés aussi ; la validation rend les mêmes erreurs sur 39 719 extractions, dont 6 634 invalides. `rapidfuzz` devient une dépendance directe. La structure des titres se calcule sans cache. Le dossier `graphify-out/` garde son nom.

## [0.1.48] - 2026-11-14

### Corrigé

- Une phrase de synthèse se lit sans l’original. La consigne des notions écarte une preuve qui décrit seulement un document (une page de liste, un sommaire, l’annonce d’un épisode) et demande de traduire un mot anglais ordinaire ; seuls les noms, les sigles et les titres restent tels quels. Le contrôle retire une phrase qui garde hors citation un mot anglais ordinaire (`synthese.illisible` : « listing », « maker », « reportedly »…) ; un mot composé (« de-listing ») reste. Sur Palantir, une phrase disait : « Un listing d’octobre 2026 décrit Palantir comme le maker de Maven ». Sur les 33 971 phrases du graphe de la gouvernance, une seule tomberait.

## [0.1.47] - 2026-11-14

### Ajouté

- Une source « Page de liste » ou « Plan de site » peut dire où sa date est écrite dans l’adresse (`date_adresse`, expression rationnelle aux groupes nommés `a`, `m`, `j` ; une année à deux chiffres est de ce siècle). Cette date passe devant une date lue dans le texte. Le Japan AISI publie sous `/activity_news/261030/` sans méta de date : la fiche du 30 octobre était datée du 26, date d’un document d’OpenAI cité dans le texte. Le registre refuse un motif illisible ou sans les trois groupes.

## [0.1.44] - 2026-11-13

### Ajouté

- Une source doit être spécialisée pour que l’éclaireur la déclare. Un graphe déclare son sujet (`specialisation: {motif, part}` dans `graphes:` du registre) ; l’étude d’un domaine garde le début de ses 12 dernières entrées ; la part de celles qui répondent au motif (sans égard aux accents) doit atteindre `part`. Un domaine en dessous est noté `ecartee` et n’entre pas. Une étude d’avant cette mesure se refait avant de juger. Sans `specialisation`, rien ne change.

### Retiré

- L’article atteint par un agrégateur ne compte plus comme trouvaille de l’éclaireur (`eclaireur.noter_trouvaille`, 0.1.42) : l’OCDE cite surtout la presse généraliste : 250 médias généralistes seraient devenus candidats.

## [0.1.43] - 2026-11-13

### Corrigé

- Une page dont le ld+json mêle des nombres à ses mots-clés se lit : `meta_de_page` les écrit en texte et laisse les vides dehors. La reprise des fiches de l’OCDE s’arrêtait sur une erreur de type (un nombre au lieu d’un texte) ; une source déclarée `agregateur` aurait fait de même à la collecte.

## [0.1.42] - 2026-11-13

### Ajouté

- Une source « Plan de site » se déclare `agregateur: true` quand sa page ne vaut que par les articles où elle mène (l’AI Incidents Monitor de l’OCDE). La fiche porte alors l’article d’origine (`collecte_rss.suivre_l_agregat`) : parmi les articles cités, le plus ancien qui se lit (robots.txt admis, pas d’aperçu derrière un mur, `ESSAIS_AGREGAT` essais au plus). Son adresse, son titre, son auteur, sa date et sa publication font la fiche. La page de l’agrégateur reste en métadonnée (`via`), avec les autres articles cités (`autres_sources`) et sa classification (`classification`, lue par `classification_motif`). Sans article lisible, la page de l’agrégateur reste, avec une note. Le manifeste garde l’agrégat pour clé, avec son `origine`.
- Le domaine de l’article d’origine compte comme une trouvaille de l’éclaireur (`eclaireur.noter_trouvaille`, `compter_domaine`) : trouvé deux fois, il est étudié, puis il entre au registre si son flux se lit.

## [0.1.41] - 2026-11-13

### Ajouté

- Un pas de l’édition après le fil de chaque passage : le `config.yaml` du média nomme un module (`apres_redaction: scripts/contacts.py`) qui porte `apres_redaction(graphe) -> str`. `collecte_continue` l’appelle après le fil de chaque graphe et écrit sa ligne au journal. Sa panne se dit et n’arrête jamais le passage. `diffusion.charger(nom)` charge tout module de l’édition, une fois.

### Corrigé

- Le versement au cerveau CortexDB trouve Newskit : le Python du client (`cache/pilote_cortexdb/venv`) reçoit un chemin qui ne porte qu’un lien vers le paquet installé (`cerveau_cortexdb.env_du_client`, `lancer_client`). Du 8 au 13 novembre, chaque nuit, le versement mourait sur « No module named 'newskit' », en « non bloquant » ; le cerveau publié restait celui du 7 novembre. Les paquets du poste ne sont pas ouverts au client : leurs grpcio et protobuf masqueraient ceux du client.

## [0.1.40] - 2026-11-13

### Changé

- Le fil se range par heure de parution, la plus récente en tête, puis par date de l’article (`fil.classer`). Rangé par date de l’article, un article de la veille paru à 12:52 passait sous ceux du jour parus à 11:47 : la tête du fil ne bougeait plus. La pile garde l’ordre par date de l’article (`fil.classer_pile`) : l’article le plus récent paraît d’abord.

### Retiré

- L’entrée en arrière-plan (`fil.arriere_plan`) : un article plus ancien que ceux du panneau prend son tour dans la pile comme les autres, puis paraît en tête du fil. Sinon, le nouveau rangement l’aurait fait entrer en paquet en tête.

## [0.1.39] - 2026-11-13

### Changé

- La carte d’aperçu se réduit au groupe logo du média, grandi au centre : dans un fil réduit, le titre dessiné dans l’image devenait illisible ; le réseau l’écrit déjà dessous. Une carte par langue (`cartes/marque-<langue>.png`), la même pour toutes les fiches ; son adresse porte l’empreinte de l’image. La marque ne garde que `polices: {nom, sous}`, `couleurs: {fond, marque, accent, discret}`, `textes: {<langue>: {sous}}` ; une marque à l’ancien format est refusée.
- Le titre de la page commence par le nom du site (`governance.news · <titre de la fiche>`) : c’est lui qui s’affiche dans l’onglet ; la suite reste propre à chaque page pour les moteurs. `og:title` garde le titre de la fiche seul.

### Retiré

- Les fonctions de l’ancienne carte : `champs_carte`, `titre_carte`, `couper`, `rubriques_anglaises`. `deposer` ne prend plus `rubriques_en` ni `empreinte_marque`. `rendre` reçoit la seule langue.

## [0.1.38] - 2026-11-13

### Ajouté

- La page d’une fiche dit son auteur : le média qui la publie, dans `<meta name="author">` et dans les données structurées (`author`, comme `publisher`). LinkedIn signalait « No author found ». L’auteur de l’article d’origine reste cité comme source ; la synthèse ne lui est pas attribuée.

## [0.1.37] - 2026-11-13

### Corrigé

- L’adresse de la carte d’aperçu porte sa version (`?v=…`) : elle change quand la carte change (titre, rubrique, date, source ou marque). Les réseaux gardent une image par adresse ; une fiche réécrite montrait encore l’ancienne carte. Le fichier garde son nom.

## [0.1.36] - 2026-11-13

### Corrigé

- Le reproche fait à une suite dont le titre tait le fait neuf nomme les mots à dire, dans leur forme du développement (« Harvey Jones »), en français comme en anglais ; il sert la relecture, la fiche et la version anglaise. Le reproche ne disait que le fait ; le rédacteur gardait le nom déjà dit par la nouvelle d’avant ; la suite épuisait ses essais.

## [0.1.35] - 2026-11-13

### Ajouté

- Une page par fiche, à une adresse lisible (`pages_fiches.py`) : `/<langue>/<titre de la fiche>-<identifiant>`, le titre en minuscules, sans accents ni ponctuation, coupé entre deux mots. L’identifiant est celui du site : le serveur retrouve la fiche par lui seul, d’une seule règle. Un titre réécrit change l’adresse ; l’ancienne mène encore à la fiche, dont le lien canonique nomme la nouvelle.
- Pour chaque billet publié, le Mac dépose sur le serveur l’en-tête de la page, la fiche en HTML simple et la carte d’aperçu, dans les deux langues, à chaque passage de la collecte. L’en-tête porte le titre, la description, le lien canonique, les liens entre les langues, l’aperçu (og : et twitter :) et les données structurées d’un article de presse. La fiche se lit sans script : un titre de niveau 1, le chapeau, la date, la source, un titre de niveau 2 par élément.
- Seule une fiche développée s’indexe : un billet bref porte `noindex`. Le plan des fiches (`plan_fiches.xml`) liste les fiches développées, celles sorties du registre comprises.
- La carte d’aperçu (1200 × 630) se dessine avec Pillow d’après la marque que le média déclare (`pages_fiches : marque :` dans `config.yaml`) : points du logo, polices, couleurs, nom, textes des deux langues. Chrome sans écran échouait sous le daemon.
- Dépendance : Pillow.

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

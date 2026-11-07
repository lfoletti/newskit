# Contribuer à Newskit

Merci de votre aide. Ce dépôt a peu de règles, mais elles tiennent.

## Préparer

```sh
git clone https://github.com/lfoletti/newskit && cd newskit
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
git config core.hooksPath .githooks
.venv/bin/python -m pytest
```

La suite tourne sans réseau et sans appel au modèle : les tests posent un média jetable, et le témoin rejoue des réponses enregistrées.

## Les règles

- **Les tests d'abord, et qui échouent sans le code.** Pour chaque règle ajoutée, nommez le test qui la couvre et montrez qu'il tombe sans elle : la ligne `FAILED` observée, ou la mutation qui le fait tomber. Mettez-les dans la section « Failing first » de la PR.
- **Le témoin reste vert.** `newskit temoin nuit` doit finir par « témoin vert ». Une consigne au modèle changée change ses réponses enregistrées : dites-le dans la PR, elles se réenregistrent à part.
- **Le moteur n'a pas de thème.** Aucun nom de graphe, d'auteur, de domaine ni de personne dans `src/newskit/` : ce qui est propre à un média va dans son `config.yaml`.
- **Une PR, un sujet.** Elle s'ouvre en brouillon et passe en relecture quand la CI est verte.
- `ruff check .` ne relève rien.

## Signaler

Un défaut ou une idée : une issue, avec le modèle proposé. Une faille : jamais en issue publique, voir [SECURITY.md](SECURITY.md).

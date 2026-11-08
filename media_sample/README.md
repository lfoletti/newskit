# media_sample

A complete media folder for Newskit. Copy it, then replace the subject, the sources and the categories.

| File | Content |
| :- | :- |
| `config.yaml` | the media, its models and its graph |
| `sources.yaml` | the sources of the graph |
| `categories.yaml` | the category tree of the feed cards |
| `regles_redaction.md` | the writing rules, one section per language (`# fr`, `# en`), editable without a release |

```sh
NEWSKIT_DONNEES=$PWD newskit media
```

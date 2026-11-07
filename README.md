# Newskit

[![ci](https://github.com/lfoletti/newskit/actions/workflows/ci.yml/badge.svg)](https://github.com/lfoletti/newskit/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![version](https://img.shields.io/github/v/tag/lfoletti/newskit?label=version)](CHANGELOG.md)

English | [Français](README.fr.md)

A topic monitoring engine for Claude Code. You name a subject; Newskit builds a knowledge graph on it and keeps it up to date at regular intervals. At each pass it collects the sources, turns them into notes, extracts from each note the concepts and how they relate, then rebuilds the graph, within a spending cap. Every answer drawn from the graph cites its note and its line.

For developers who already use Claude Code: every model call goes through `claude -p`, under your own subscription.

## How the graph grows

The graph does not stay within the subjects you declared at the start.

- **New themes.** After each pass, the engine looks for clusters of concepts that none of the declared subjects covers. A candidate that comes back three passes in a row joins the subjects; the sources and searches follow it.
- **Auto-completion.** A question the graph could not answer becomes a search for the next pass. A web scout searches the declared subjects; a question agent probes the graph the way a practitioner would; what a language model considers essential and the graph lacks becomes a shopping list of sources to find.
- **The source list.** The engine assesses a candidate domain before it is declared, and keeps track of every source a user drops in (a link sent from the browser). A source that keeps failing is audited and, when a route exists, repaired on its own (see `sap` below).
- **Clean concepts.** The same concept found under several labels (a translation, an acronym, a variant) is merged after a model confirms it is exactly the same concept; refusals are kept and never asked again.

## Quick start

You need Python 3.12 or later, git and a logged-in Claude Code (`claude`).

```sh
pip install "newskit @ git+https://github.com/lfoletti/newskit@v0.1.5"
mkdir my-media && cd my-media
newskit test
```

The last command is a dress rehearsal. It runs one full pass of the engine on an unrelated sample graph (medieval beekeeping), with model answers replayed from a recording: no paid call, about one minute. It must end with « témoin vert » (rehearsal passed).

Then, in Claude Code:

```text
/plugin marketplace add lfoletti/newskit
/plugin install newskit@newskit
```

Ask Claude to follow a subject (“start a monitoring graph on urban gardening”). The `newskit` skill looks for the media folder's `config.yaml`; if there is none, it asks its questions, suggests themes drawn from the sources you give, creates the graph and runs a first pass in front of you.

## The media folder

A media is a folder that holds a `config.yaml`. The engine attaches to it: the `NEWSKIT_DONNEES` variable first, otherwise the current folder. The code stays in the installed package; the data (notes, graph, cost ledger) live in the media folder.

```yaml
version: 1
media:
  nom: potagers
  langue: fr                  # language of the prompts and of the publication
  traductions: [en]           # translated feed and newsletter; none by default
  frequence: {heure: 5, minute: 30}   # time of the scheduled pass
modeles:                      # one model per role; a missing role keeps its default
  extraction: claude-opus-5-5
  redaction: claude-sonnet-5-5
sources: sources.yaml         # the source list, kept apart
# registre_des_graphes: graphes.json   # labels and declared subjects, kept by the engine (default)
graphes:
  potagers-urbains:
    libelle: Potagers urbains
    domaine: "urban gardening: allotments, soil, seed exchange"
```

`newskit media` shows what was read; an unknown field is refused and named. The roles: `extraction`, `analyse`, `redaction`, `lecture`, `traduction`, `questions`.

## Commands

| Command | What it does |
| :- | :- |
| `newskit` | the commands, one line each |
| `newskit media` | the media as read: language, translations, time of the pass, model per role |
| `newskit nouveau-graphe <nom>` | creates a graph, or says what it lacks (`--controle`) |
| `newskit routine --graphe <nom>` | one pass: collection, notes, concept extraction within the cap, graph |
| `newskit graphe --graphe <nom> chercher "…"` | queries the graph, no model call; each answer carries its provenance |
| `newskit couts --par graphe` | spending over the last seven days |
| `newskit test` | tries the engine on a sample graph, at no cost; a real pass starts only after a successful test the same day |

## Safeguards

- **The rehearsal.** Before the real pass, the engine rehearses it on the sample graph with recorded answers. The real pass starts only if a rehearsal passed the same day with the same engine version. An engine update therefore cannot break a pass unnoticed: it stops before spending anything.
- **The caps.** Each step has its own cap on notes and on spending; every model call is written to the cost ledger, under a step and a graph.
- **The usage limit.** After three usage limits in a row, the engine sets a marker that lasts three hours. The plugin's hook then refuses any model call started from Claude Code.

## Works with

Newskit runs alone. The following tools extend its features when they are installed:

- **[sap](https://github.com/lfoletti/accesspath)** (accesspath). Website access audit. When a source keeps failing, Newskit asks sap which route still reaches it (RSS feed, API, sitemap, plain HTML, browser) and repairs the source.
- **[cortexdb](https://github.com/lfoletti/cortexdb)**. AI memory and knowledge graph in one SQLite file. A graph can be loaded into a cortexdb brain, each relation tied to the passage that supports it, and queried from any MCP client.
- **[thalamus](https://github.com/lfoletti/thalamus)**. A governance layer over cortexdb for an organisation's shared knowledge. Questions left unanswered by Thalamus clients return to the engine as searches.
- **[graphify](https://github.com/Graphify-Labs/graphify)**. Reads the structure of the documents, validates each extraction against the schema, builds the graph and splits it into communities.
- **[trafilatura](https://trafilatura.readthedocs.io)**. Main text extraction from web pages.

## Limits of version 0.1

- The prompts to the models are written in French; the only translation is English.
- macOS and Linux. Scheduled passes run through launchd on macOS; on Linux, through a cron line that runs `newskit test` then `newskit routine`.
- `newskit nouveau-graphe` writes the graph's scheduled task under `taches/`, ready to install in `/Library/LaunchDaemons`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report a vulnerability privately: [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE).

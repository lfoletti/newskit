# Newskit

[![ci](https://github.com/lfoletti/newskit/actions/workflows/ci.yml/badge.svg)](https://github.com/lfoletti/newskit/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![version](https://img.shields.io/github/v/tag/lfoletti/newskit?label=version)](CHANGELOG.md)

English | [Français](README.fr.md)

Automated topic monitoring for Claude Code. You name a subject and give a few sources; at regular intervals Newskit collects them, writes one note per item, extracts the concepts and how they relate, and keeps a knowledge graph up to date, within a spending cap. Two things set it apart from a feed reader: every answer drawn from the graph cites its note and its line, and the graph extends itself to new themes and new sources.

For developers who already use Claude Code: every model call goes through `claude -p`, under your own Claude subscription. Notes, feed and newsletter are written in French; an English translation can be added (`traductions: [en]`).

[![governance.news, an AI governance media built on Newskit](docs/governance-news-en.png)](https://governance.news/en/)

*[governance.news](https://governance.news/en/): a newsletter and a knowledge graph on AI governance in business, built on Newskit.*

## What you get

All of it lives in your media folder, as plain files:

- **Notes**: one Markdown file per collected item, under `sources/`, with its title, author, date and address.
- **The graph**: `graphe/<name>/graphe.json`, its map (`carte.md`, the main themes and their concepts) and the report of each pass (`ROUTINE.md`).
- **Answers with their source**: `newskit query` searches the graph without any model call and gives, for each answer, the note and the line that support it.
- **A feed and a newsletter**: short posts on the latest items (`fil.json`) and a newsletter of the graph, sent through your own delivery module (`diffusion:` in `config.yaml`).

```text
$ newskit query --graph ai-governance sources "general-purpose AI model"
  Sources attestant « General-purpose AI models »
    sources/ai_compliance/rss/ec_digital/2026-08-02_navigating-ai-act.md L211
```

## Quick start

You need Python 3.12 or later, git, macOS or Linux, and a logged-in Claude Code (`claude`).

The words used below: the **media** is your project folder; it holds one or more **graphs**; each graph follows **subjects**; a **pass** collects the graph's sources and writes one **note** per item.

1. Install the engine and test it in an empty folder:

   ```sh
   pip install "newskit @ git+https://github.com/lfoletti/newskit@v0.1.5"
   mkdir my-media && cd my-media
   newskit test
   ```

   The test runs one full pass on a sample graph (medieval beekeeping), with model answers replayed from a recording: no paid call, about one minute. It must end with « témoin vert » (test passed).

2. Open Claude Code in that folder (`claude`) and install the plugin:

   ```text
   /plugin marketplace add lfoletti/newskit
   /plugin install newskit@newskit
   ```

3. Ask Claude to follow a subject: “start a monitoring graph on urban gardening”. The `newskit` skill asks its questions, writes `config.yaml` and `sources.yaml`, suggests themes drawn from the sources you give, creates the graph and runs a first pass in front of you.

4. Read the result: `graphe/<name>/ROUTINE.md` for the report, `newskit query --graph <name> search "…"` for the graph.

5. Schedule the next passes. `newskit new-graph` has written the graph's task under `taches/` and printed the two `sudo` lines that install it in launchd (macOS). On Linux, add a cron line at each time of `passages` that runs `newskit test` then `newskit run --graph <name> --scheduled`; on a day outside the interval, the scheduled run stops by itself.

## Sources

Sources are declared in `sources.yaml`, next to `config.yaml`, one block per source, each tied to a graph. Supported: RSS feeds, sitemaps and listing pages (for sites without a feed), Substack, Medium, LinkedIn newsletters, arXiv, journals by ISSN, OpenAlex, GitHub and Hugging Face searches, an e-mail inbox, and local folders of Markdown or PDF files.

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

The skill writes this file for you from the addresses you give; a source the engine cannot read is refused with its reason.

## How the graph grows

The graph does not stay within the subjects you declared at the start: it expands.

- **New themes.** After each pass, the engine looks for clusters of concepts that none of the declared subjects covers. A candidate that comes back three passes in a row joins the subjects; the sources and searches follow it.
- **Auto-completion.** A question the graph could not answer becomes a search for the next pass. A web scout searches the declared subjects; a question agent probes the graph the way a practitioner would; what a language model considers essential and the graph lacks becomes a shopping list of sources to find.
- **The source list.** The engine assesses a candidate domain before it is declared, and keeps track of every source a user drops in (a link sent from the browser). A source that keeps failing is audited and, when a route exists, repaired on its own (see accesspath below).
- **Clean concepts.** The same concept found under several labels (a translation, an acronym, a variant) is merged after a model confirms it is exactly the same concept; refusals are kept and never asked again.

## The media folder

The media is a folder that holds a `config.yaml`. The engine attaches to it: the `NEWSKIT_DONNEES` variable first, otherwise the current folder. The code stays in the installed package; the data (notes, graph, cost ledger) live in the media folder.

```yaml
version: 1
media:
  nom: potagers               # name: lowercase, digits and hyphens
  langue: fr                  # language of the prompts and of the publication
  traductions: [en]           # translated feed and newsletter; none by default
  passages: {tous_les: 1, heures: ["05:30"]}   # every N days, at these times
modeles:                      # one model per role; a missing role keeps its default
  extraction: claude-opus-5-5
  redaction: claude-sonnet-5-5
sources: sources.yaml         # the source list, kept apart
# registre_des_graphes: graphes.json   # labels and declared subjects, kept by the engine (default)
graphes:
  potagers-urbains:
    libelle: Potagers urbains   # label
    domaine: "urban gardening: allotments, soil, seed exchange"   # the domain, in English, for extraction
```

`newskit media` shows what was read; an unknown field is refused and named. The roles: `extraction` (reading a note and drawing its concepts), `analyse` (judging a whole graph), `redaction` (summaries and newsletter), `lecture` (deciding whether two labels are the same concept), `traduction` (translation), `questions` (training questions for the local encoder).

## Cost

Every model call goes through your subscription and counts against its usage limits; the cost ledger records each one at its API-equivalent price, so you can see where the usage goes (`newskit costs --by graph`). Concept extraction is the main item: measured on governance.news, about $0.25 per note with the default model (Opus), so about $10 per pass at the default cap of 40 notes. The cap is set per graph (`--cap` of `newskit new-graph`).

## Commands

| Command | What it does |
| :- | :- |
| `newskit` | the commands, one line each |
| `newskit media` | the media as read: language, translations, passages, model per role |
| `newskit new-graph <name>` | creates a graph, or says what it lacks (`--check`) |
| `newskit run --graph <name>` | one pass: collection, notes, concept extraction within the cap, graph |
| `newskit query --graph <name> search "…"` | queries the graph, no model call; each answer carries its provenance |
| `newskit costs --by graph` | spending over the last seven days |
| `newskit test` | tries the engine on a sample graph, at no cost; a real pass starts only after a successful test the same day |

## Safeguards

- **The test.** Before the real pass, the engine runs `newskit test` on the sample graph with recorded answers. The real pass starts only if a test passed the same day with the same engine version. An engine update therefore cannot break a pass unnoticed: it stops before spending anything.
- **The caps.** Each step has its own cap on notes and on spending; every model call is written to the cost ledger, under a step and a graph.
- **The usage limit.** After three usage limits in a row, the engine sets a marker that lasts three hours. The plugin's hook then refuses any model call started from Claude Code.

## Works with

Newskit relies on:

- **[graphify](https://github.com/Graphify-Labs/graphify)**, installed with the package. Reads the structure of the documents, validates each extraction against the schema, builds the graph and splits it into communities.
- **[trafilatura](https://trafilatura.readthedocs.io)**, installed with the package. Main text extraction from web pages.
- **[pytest](https://pytest.org)**, installed with the package: `newskit test` runs on it.

The following tools extend its features when they are installed:

- **[accesspath](https://github.com/lfoletti/accesspath)** (sap). Website access audit. When a source keeps failing, Newskit asks sap which route still reaches it (RSS feed, API, sitemap, plain HTML, browser) and repairs the source.
- **[cortexdb](https://github.com/lfoletti/cortexdb)**. AI memory and knowledge graph in one SQLite file. A graph can be loaded into a cortexdb brain, each relation tied to the passage that supports it, and queried from any MCP client.
- **[thalamus](https://github.com/lfoletti/thalamus)**. A governance layer over cortexdb for an organisation's shared knowledge. Questions left unanswered by its clients return to the engine as searches.

## Limits of version 0.1

- The prompts to the models are written in French; the only translation is English.
- macOS and Linux. Scheduled passes run through launchd on macOS; on Linux, through a cron line.
- `newskit new-graph` writes the graph's scheduled task under `taches/`, ready to install in `/Library/LaunchDaemons`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report a vulnerability privately: [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE).

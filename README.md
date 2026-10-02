# MintCode

A terminal coding agent. It reads and writes files, searches your codebase, runs
shell commands, and looks things up on the web, all from an interactive REPL
powered by a reasoning model running on [Groq](https://console.groq.com).

click here to watch demo video: [demo video](https://youtu.be/hIazXb-en2c)

## Features

**Seven tools**, exposed to the model as function calls and dispatched by name:

| Tool | What it does |
| --- | --- |
| `read_file` | Read a slice of a file with line numbers, using `offset` and `limit` so you only pull what you need |
| `write_file` | Create a file or rewrite one completely |
| `edit_file` | Replace an exact snippet in an existing file, so only the changed text is sent instead of the whole file |
| `run_command` | Run a program as an argument list, with optional `stdin`, under a 30s timeout |
| `glob` | Find files by name pattern |
| `grep` | Search file contents and get back matching lines with file names and line numbers |
| `web_search` | Search the web for current facts, versions, and advisories via Tavily |

**Multi-step tool use.** A single request can chain many tools. The model is
instructed to batch independent calls into one turn rather than round-tripping
per file, and the agent loops for up to 10 tool iterations before stopping.

**Context management.** Tokens are estimated per turn, and once a conversation
passes 80% of the 131k context limit the agent summarizes the older half into a
condensed transcript, keeping the 12 most recent messages intact.

**Resilient API calls.** Rate limits and connection errors are retried up to 5
times with exponential backoff. If a tool call is cut off mid-arguments by the
output token limit, it is discarded and the model is asked to retry with a
smaller call rather than being fed truncated JSON.

**Slash commands.** `/help` lists them, `/tokens` reports API call count and
accumulated token usage with an estimated dollar cost, `/clear` resets the
conversation, `/exit` quits.

**First-run setup.** On startup, missing API keys are prompted for inline,
masked as you type, validated against the provider before being saved, and
written to a gitignored `.env`. The working directory is printed under the
banner, since the agent operates on wherever you launched it.

## Tech stack

| | |
| --- | --- |
| Language | Python 3.13+ |
| Model | `qwen/qwen3.8-27b` via Groq, streaming with `reasoning_format="hidden"` |
| Terminal UI | `rich` (live-refreshed Markdown, markup, panels) |
| Web search | Tavily |
| Config | `python-dotenv`, read from `.env` |
| Packaging | `uv` |

## Installation

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and a
free [Groq API key](https://console.groq.com/keys). A
[Tavily key](https://app.tavily.com/home) is optional and only needed for
`web_search`.

```sh
git clone <your-repo-url> mintcode
cd mintcode
uv run agent.py
```

`uv run` creates the virtualenv and installs dependencies on first use, so
there is no separate install step.

On first launch you are asked for your Groq API key (and optionally a Tavily
key). Both are validated and saved to `.env`, which is gitignored. Later runs
read that file, so this only happens once.

### Run it on another project

The agent works on whatever directory you launch it from, so you can point it at
a different project by changing directory first. To save typing, add an alias
to `~/.zshrc` (or `~/.bashrc`) pointing at the two absolute paths:

```sh
alias mintcode='/path/to/mintcode/.venv/bin/python /path/to/mintcode/agent.py'
```

Then from any project:

```sh
cd ~/projects/some-other-project
mintcode
```

The directory you are standing in gets printed under the banner, so you can
confirm the agent is pointed where you expect before you type a prompt.

Two things to get right in the alias: use the project's **own virtualenv
interpreter**, not a bare `python3` (a fresh shell's `python3` is usually the
system one, without the dependencies installed), and pass `agent.py` by absolute
path. Do not wrap the command in `cd` or `uv --directory`, since both change the
working directory and would pin the agent to the MintCode checkout instead of
the project you are standing in.

Every launch starts a fresh conversation; there is no memory carried over
between sessions or between projects.

### API keys

Keys live in `.env` at the project root. To set them yourself instead of being
prompted, copy the template and fill it in:

```sh
cp .env.example .env
```

```dotenv
GROQ_API_KEY="your groq api key here"
TAVILY_API_KEY="your tavily api key here"
```

Keys already exported in your shell take precedence over the `.env` file.

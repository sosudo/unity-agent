# Unity Agent

Autoformalization pipeline for Lean theorem proving. Transforms mathematical documents into formally verified Lean 4 proofs.

## Features

- **Multi-phase pipeline**: Source Scan → Generation → Validation → Semiformalization → Exploration → Formalization → Critic → Retrospective → Escalation
- **Lean LSP integration**: Automatic `lean-lsp-mcp` server for all agents (diagnostics, goals, completions, verification)
- **Forum-based agent coordination**: MCP forum with per-chunk threads, structured attempt logging, decision tagging, phase handoffs
- **Flexible configuration**: Environment-based settings with interactive setup
- **Claude Agent SDK**: Powered by Anthropic's Claude models with multi-agent orchestration

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** - Python package manager
- **[Lean 4](https://lean-lang.org/)** - With `lake` build tool
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** (optional) - For lean-lsp-mcp local search
- **Anthropic API key**

## Installation

### From source (recommended)

```bash
# Clone the repository
git clone <repository-url>
cd unity-agent

# Install with uv
uv sync

# Or install globally as a tool
uv tool install .
```

### Development install

```bash
uv pip install -e .
```

## Quick Start

### 1. Setup configuration

The fastest path: copy the template and set your API key.

```bash
cp .env.example .env
# edit .env and set PRIMARY_API_KEY=sk-ant-...
```

Or use the interactive setup (asks only the essentials):

```bash
unity setup                 # simple — asks for API key + escalation tier (optional)
unity setup --advanced      # walks through every knob
```

Only `PRIMARY_API_KEY` is required. All other variables have sensible defaults
documented inline in `.env.example`.

### 2. Run the pipeline

```bash
# Basic usage
unity --source paper.tex --project ./my_lean_project

# With existing Lean context
unity --source notes.md --project ./existing_project --context

# Short flags
unity -s paper.tex -p ./proj -c
```

## CLI Reference

```
Usage: unity [OPTIONS] COMMAND [ARGS]...

  Unity Agent - Autoformalization pipeline for Lean theorem proving.

Options:
  -s, --source PATH    Source material to autoformalize (file or directory)
  -p, --project PATH   Target Lean project directory
  -c, --context        Use existing Lean files in project as context
  --help               Show this message and exit.

Commands:
  setup  Generate .env configuration file interactively.
```

### Commands

| Command | Description |
|---------|-------------|
| `unity setup` | Interactive .env file generator |
| `unity --source <file> --project <dir>` | Run the full pipeline |

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--source` | `-s` | `source.tex` | Input file or directory |
| `--project` | `-p` | `.` | Target Lean project directory |
| `--context` | `-c` | `false` | Use existing Lean files as context |

## Configuration

All configuration lives in `.env` (gitignored). The shipped `.env.example`
documents every variable inline with sensible defaults. The summary below
groups them by purpose; for the full reference read `.env.example`.

### Required

| Variable | Description |
|----------|-------------|
| `PRIMARY_API_KEY` | API key for the primary tier — your Anthropic key (`sk-ant-...`) or an OpenAI-compatible proxy's key |

### Primary tier (model selection)

| Variable | Default | Description |
|----------|---------|-------------|
| `PRIMARY_MODEL` | `claude-opus-4-7` | Model identifier the agents run as |
| `PRIMARY_BASE_URL` | _(SDK default)_ | Override only for non-Anthropic proxies (OpenRouter, etc.) |
| `PRIMARY_AUTH_TOKEN` | _(blank)_ | Bearer-auth tokens (some proxies) |

### Secondary tier (escalation; optional)

When the critic loop sees a chunk stuck for ≥2 iterations, Unity escalates
that chunk on a separate model. Leave blank to reuse primary credentials.

| Variable | Default | Description |
|----------|---------|-------------|
| `SECONDARY_API_KEY` | _(blank)_ | Set only if escalation should use a different provider |
| `SECONDARY_MODEL` | `claude-opus-4-7` | Escalation model identifier |
| `SECONDARY_BASE_URL` | _(blank)_ | Override for non-Anthropic escalation provider |
| `SECONDARY_AUTH_TOKEN` | _(blank)_ | Bearer auth (some proxies) |
| `SECONDARY_BUDGET` | `125` | Hard cumulative spend cap on escalation tier (USD) |

### Pipeline flags

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOFIX` | `true` | Translate source → IR with autofix (required for `--context`) |
| `EXPLORATION` | `true` | Run the exploration phase |
| `RECURSE` | `false` | Allow exploration to spawn child unity runs |
| `NO_BYPASS` | `false` | Require permission for file edits |
| `SILENT` | `false` | Redirect stdout/stderr to `unity.out`/`unity.err` |
| `RECORDING` | `true` | Tee stdout/stderr to log files |
| `SAVE_SPECIFICATION` | `true` | Keep `language/` after the run |
| `SAVE_SEMIFORMALIZATION` | `true` | Keep `semiformal/` after the run |

### Per-phase budgets (USD, blank = unlimited)

`GENERATION_BUDGET`, `SEMIFORMALIZATION_BUDGET`, `EXPLORATION_BUDGET`,
`SOURCE_SCAN_BUDGET`, `FORMALIZATION_BUDGET`, `CRITIC_BUDGET`,
`VALIDATION_BUDGET`. `SECONDARY_BUDGET` is the only hard global cap.

### Iteration / retry caps (blank = no cap)

`MAX_CRITIC_ITERATIONS`, `MAX_VALIDATION_ITERATIONS`, `RESOLVER_MAX_RETRIES`.

### Ports

| Variable | Default | Description |
|----------|---------|-------------|
| `FORUM_PORT` | `6367` | Forum web UI |
| `LEAN_LSP_PORT` | `6368` | Shared lean-lsp-mcp server |

### Advanced

| Variable | Default | Description |
|----------|---------|-------------|
| `SDK_MESSAGE_IDLE_TIMEOUT` | `600` | Seconds without a message before watchdog fires |
| `MAX_LSP_RESTARTS_BEFORE_DEGRADE` | `2` | Idle-timeout LSP restarts before degrading to LSP-less |
| `TOOL_RESULT_MAX_CHARS` | `50000` | Per-tool-call output truncation threshold |
| `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` | `180000` | SDK stream-close timeout (ms) |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | _(off)_ | Switch to `TEAMS/` prompts instead of `PROMPTS/` |

## Pipeline Phases

1. **Source Scan** — front-load Mathlib coverage for source claims
2. **Generation** — design a source-specific IR
3. **Validation** — structural + field-propagation checks on the IR
4. **Semiformalization** — translate source → IR chunks (council convergence)
5. **Exploration** — resolve external dependencies, gather helper material
6. **Formalization** — generate Lean 4 per DAG layer, parallel via worktrees
7. **Critic** — review proofs; loop back to Formalization until COMPLETE
8. **Retrospective** — distill lessons to `~/.unity/library/`
9. **Escalation** — re-run stagnant chunks on the secondary tier

## Lean LSP Integration

Unity automatically starts a `lean-lsp-mcp` server that provides all agents with:

- `lean_goal` - Get proof goals at any position
- `lean_diagnostic_messages` - Errors, warnings, hints
- `lean_file_outline` - File structure overview
- `lean_code_actions` - "Try this" suggestions from `simp?`, `exact?`, etc.
- `lean_verify` - Check proof soundness (axioms used)
- `lean_hover_info` - Documentation lookup
- `lean_completions` - Code completion
- And more...

## Project Structure

```
unity-agent/
├── unity_agent/
│   ├── __init__.py
│   ├── cli.py          # CLI entry point
│   ├── setup_cmd.py    # Interactive setup
│   └── pipeline.py     # Main pipeline logic
├── PROMPTS/            # System prompts for each phase
├── SUBAGENTS/          # Subagent configurations
├── pyproject.toml
└── README.md
```

## Development

```bash
# Install dev dependencies
uv sync

# Run directly
uv run unity --help

# Run tests (if available)
uv run pytest
```

## Troubleshooting

### `lake build` timeout

Run `lake build` manually in your Lean project before starting Unity:

```bash
cd your_lean_project
lake build
```

### lean-lsp-mcp not found

Ensure it's installed:

```bash
uv pip install lean-lsp-mcp
# or
uvx lean-lsp-mcp --help
```

### API errors

Check your `.env` file has valid credentials:

```bash
unity setup  # Re-run setup
```

## License

[Add your license here]

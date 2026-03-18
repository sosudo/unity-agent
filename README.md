# Unity Agent

Autoformalization pipeline for Lean theorem proving. Transforms mathematical documents into formally verified Lean 4 proofs.

## Features

- **Multi-phase pipeline**: Generation → Semiformalization → Exploration → Preparation → Formalization → Critic
- **Lean LSP integration**: Automatic `lean-lsp-mcp` server for all agents (diagnostics, goals, completions, verification)
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

Generate a `.env` file interactively:

```bash
unity setup
```

This will prompt you for:
- Anthropic API credentials
- Model preferences
- Pipeline flags (autofix, exploration, etc.)
- Budget limits per phase

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

All configuration is done via environment variables (`.env` file).

### API Configuration

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (required) |
| `ANTHROPIC_BASE_URL` | Custom API endpoint (optional) |
| `ANTHROPIC_AUTH_TOKEN` | Additional auth token (optional) |

### Model Configuration

| Variable | Default |
|----------|---------|
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `claude-opus-4-20250514` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `claude-sonnet-4-20250514` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `claude-haiku-4-20250514` |

### Pipeline Flags

| Variable | Description |
|----------|-------------|
| `AUTOFIX` | Enable automatic error fixing |
| `EXPLORATION` | Enable exploration phase |
| `RECURSE` | Enable recursive exploration |
| `NO_BYPASS` | Require permission for file edits |
| `SILENT` | Redirect output to `unity.out`/`unity.err` |
| `SAVE_SPECIFICATION` | Keep generated specification language |
| `SAVE_SEMIFORMALIZATION` | Keep semiformalized output |

### Budget Configuration (USD)

| Variable | Default | Description |
|----------|---------|-------------|
| `GENERATION_BUDGET` | `5.0` | Specification language generation |
| `SEMIFORMALIZATION_BUDGET` | `5.0` | Semiformal translation |
| `EXPLORATION_BUDGET` | `10.0` | Codebase exploration |
| `PREPARATION_BUDGET` | `5.0` | Pre-formalization setup |
| `FORMALIZATION_BUDGET` | `15.0` | Lean code generation |
| `CRITIC_BUDGET` | `5.0` | Review and validation |

## Pipeline Phases

1. **Generation**: Creates a domain-specific specification language for the source material
2. **Semiformalization**: Translates source into the specification language (IR)
3. **Exploration** (optional): Analyzes existing Lean project structure
4. **Preparation**: Sets up the Lean project and dependencies
5. **Formalization**: Generates Lean 4 code from the semiformal specification
6. **Critic**: Reviews and validates the generated proofs

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

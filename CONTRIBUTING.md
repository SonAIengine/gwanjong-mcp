# Contributing to gwanjong-mcp

Thanks for your interest! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/SonAIengine/gwanjong-mcp.git
cd gwanjong-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

## Running Tests

```bash
# Unit tests (no network required)
pytest tests/ -v

# Integration tests (requires API keys)
pytest tests/ -v -m integration
```

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check gwanjong_mcp/       # Lint
ruff format gwanjong_mcp/      # Format
```

### Conventions

- Python 3.10+, async/await
- Type hints required (`dict[str, Any]`, `list[str]` — lowercase generics)
- `dataclass` for internal models, `dict` for external returns
- Logging to stderr (stdout is reserved for MCP protocol)

## Project Structure

```
gwanjong_mcp/
├── server.py      # PipelineMCP + 5 tools + state assembly
├── pipeline.py    # scout/draft/strike core logic
├── events.py      # EventBus (the only shared dependency)
├── types.py       # Opportunity, DraftContext, ActionRecord
├── setup.py       # Platform onboarding
│
├── safety.py      # Rate limiting + content validation (plugin)
├── memory.py      # SQLite persistent storage (plugin)
├── tracker.py     # Reply tracking (plugin)
├── persona.py     # Per-platform persona config
├── llm.py         # Built-in LLM comment generation
├── autonomous.py  # Autonomous loop engine
└── daemon.py      # CLI entry point for daemon mode
```

### Adding a New Plugin

Plugins subscribe to EventBus events and don't import each other:

```python
from .events import Event, EventBus

class MyPlugin:
    def attach(self, bus: EventBus) -> None:
        bus.on("scout.done", self._on_scout)

    async def _on_scout(self, event: Event) -> None:
        # React to scout completion
        ...
```

Then wire it up in `server.py`:

```python
my_plugin = MyPlugin()
my_plugin.attach(bus)
```

## Pull Requests

1. Fork the repo and create a feature branch
2. Make your changes with tests
3. Run `ruff check` and `pytest` before pushing
4. Open a PR against `main`

## Configuration

All paths are configurable via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GWANJONG_ENV_PATH` | `~/.gwanjong/.env` | Platform API keys file |
| `GWANJONG_DB_PATH` | `~/.gwanjong/memory.db` | SQLite database |
| `GWANJONG_PERSONA_PATH` | `~/.gwanjong/persona.json` | Persona config |
| `GWANJONG_BROWSER_DATA_DIR` | `~/.gwanjong/browser-data` | Playwright data |

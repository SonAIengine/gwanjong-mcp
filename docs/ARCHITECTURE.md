# Architecture

## Overview

gwanjong-mcp is built on three core patterns: **Stateful Pipeline MCP**, **EventBus plugins**, and **Platform Registry**.

```
┌──────────────────────────────────────────────────┐
│  LLM Client (Claude Code, Cursor, etc.)          │
│  Judgment · Content generation · Final decision   │
└──────────────┬───────────────────────────────────┘
               │ 5 MCP tools (stdio)
┌──────────────▼───────────────────────────────────┐
│  gwanjong-mcp                                     │
│                                                   │
│  server.py ─── PipelineMCP + GwanjongState        │
│      │                                            │
│      ├── pipeline.py ── scout / draft / strike    │
│      │       │                                    │
│      │       └── devhub Hub ── Platform Registry  │
│      │               │                            │
│      │               ├── DevTo adapter            │
│      │               ├── Bluesky adapter          │
│      │               ├── Twitter adapter          │
│      │               └── Reddit adapter           │
│      │                                            │
│      └── EventBus ←── plugins                     │
│              ├── safety.py   (rate limit + guard)  │
│              ├── memory.py   (SQLite persistence)  │
│              └── tracker.py  (reply detection)     │
└──────────────────────────────────────────────────┘
```

## Stateful Pipeline MCP

Traditional MCP servers expose many fine-grained tools (list, get, search, write, etc.), requiring 9+ LLM round trips for a single action. gwanjong-mcp uses **5 tools with server-side state**:

```
scout(topic) ──stores──→ state.opportunities
                              │
draft(opp_id) ──requires──→ opportunities
              ──stores────→ state.contexts
                              │
strike(opp_id) ──requires──→ contexts
```

### Why 5 Tools?

Tool descriptions are included in **every** system prompt message. Each tool's description costs tokens on every LLM call, not just when used. Fewer tools = cheaper + more focused.

### stores / requires

Declared in the tool decorator:

```python
@mcp.tool(stores="opportunities")
async def gwanjong_scout(topic: str, ...):
    # Results cached in state.opportunities
    ...

@mcp.tool(requires="opportunities", stores="contexts")
async def gwanjong_draft(opportunity_id: str):
    # Can only run after scout; results cached in state.contexts
    ...
```

`mcp-pipeline` enforces the dependency chain. Calling `draft` before `scout` returns a clear error, not a crash.

## EventBus

Modules communicate through events, not direct imports. `events.py` is the **only shared dependency**.

### Events

| Event | When | Data |
|-------|------|------|
| `scout.done` | After scout completes | `{opportunities: {...}}` |
| `draft.done` | After draft completes | `{context: DraftContext}` |
| `strike.before` | Before strike executes | `{platform, action, content}` |
| `strike.after` | After strike completes | `{record, response}` |
| `reply.detected` | When tracker finds a reply | `{comment_id, author, body}` |
| `approval.executed` | After approval item is executed | `{item_id, status}` |

### Plugin Pattern

Every plugin follows the same structure:

```python
class MyPlugin:
    def attach(self, bus: EventBus) -> None:
        bus.on("event.name", self._handler)

    async def _handler(self, event: Event) -> bool | None:
        # Return False from *.before events to block the action
        # Return None to allow it
        ...
```

### Blocking Events

`strike.before` is special — if any handler returns `False`, the action is blocked by raising a `Blocked` exception. This is how `safety.py` enforces rate limits without pipeline knowing about safety.

```
pipeline.strike()
    │
    ├── bus.emit("strike.before") ──→ safety._on_strike_before()
    │                                      │
    │                                      ├── check_rate_limit() → False? BLOCK
    │                                      └── validate_content() → False? BLOCK
    │
    ├── (execute action via devhub)
    │
    └── bus.emit("strike.after") ──→ safety._on_strike_after()  (record to rate_log)
                                 ──→ memory._on_strike_after()  (record to actions)
```

### Module Independence

```
pipeline.py ──emit──→ EventBus ←──subscribe── safety.py
                          ↑                    memory.py
                          │                    tracker.py
                          │                    (future plugins)
                     server.py wires it all
```

No plugin imports another plugin. Adding or removing a plugin doesn't break anything.

## Platform Registry

`devhub.registry` discovers platform adapters dynamically:

```python
from devhub.registry import get_configured_adapters

# Returns only adapters whose env vars are set
adapters = get_configured_adapters()
# e.g., [DevToAdapter, BlueskyAdapter] if only those are configured
```

### Adding a New Platform

1. Implement `PlatformAdapter` in devhub (or as an external package)
2. Register via `devhub.adapters` entry point group
3. gwanjong-mcp picks it up automatically — no code changes needed

## Data Flow

### MCP Mode (LLM-driven)

```
LLM ──tool call──→ server.py ──→ pipeline.scout()
                                      │
                                      ├── devhub.Hub.trending() + search()
                                      ├── _score_relevance()
                                      ├── bus.emit("scout.done")
                                      └── return top N opportunities
```

### Daemon Mode (autonomous)

```
daemon.py
    └── AutonomousLoop.run_cycle(topic)
            │
            ├── pipeline.scout(topic)
            ├── pipeline.draft(opp_id)
            ├── llm.generate(context)     ← CommentGenerator
            ├── pipeline.strike(...)      ← or approval queue
            └── tracker.scan()            ← reply detection
```

## SQLite Schema

All plugins share `~/.gwanjong/memory.db` (configurable via `GWANJONG_DB_PATH`):

| Table | Owner | Purpose |
|-------|-------|---------|
| `actions` | memory.py | All activity history |
| `seen_posts` | memory.py | Duplicate prevention |
| `rate_log` | safety.py | Rate limit tracking |
| `replies` | tracker.py | Detected replies |
| `approval_queue` | approval.py | Pending approvals |

"""MCP (Model Context Protocol) server for Densa Deck.

Exposes the engine to AI clients (Claude desktop, ulcagent, Cursor, anything
that speaks MCP) as a curated set of tools. The server runs in-process via
stdio — the AI client launches `densa-deck mcp serve` as a subprocess and
talks JSON-RPC over the pipes. Nothing crosses the network unless an
exposed tool explicitly fetches (Scryfall search, etc.).

Free-tier tools (read-only analysis, search, combos, exports) are always
available. Pro-tier tools (goldfish, gauntlet, analyst LLM, coach) are
license-gated via `densa_deck.tiers.get_user_tier()` — same gate the CLI
and desktop UI use, so a paid customer's license unlocks all three
surfaces from one activation.

Wire it up by adding to your AI client's MCP config (Claude desktop's
`claude_desktop_config.json` or ulcagent's `--mcp` flag):

    {
      "mcpServers": {
        "densa-deck": {
          "command": "densa-deck",
          "args": ["mcp", "serve"]
        }
      }
    }

The optional `[mcp]` extra installs the protocol SDK; `densa-deck mcp serve`
prints a clear install hint if it's missing.
"""

from __future__ import annotations

from densa_deck.mcp.server import build_server, run_stdio_server

__all__ = ["build_server", "run_stdio_server"]

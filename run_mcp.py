#!/usr/bin/env python3
"""Entry point for MCP clients (Codex, OpenAI tunnel-client, Claude Desktop, ...).

MCP clients start servers from an arbitrary working directory, so point them at
this file by absolute path:

    python3 /absolute/path/to/Learn-My-Voice/run_mcp.py
"""
import sys
from pathlib import Path

root = str(Path(__file__).resolve().parent)
sys.path[:] = [root] + [p for p in sys.path if p not in ("", ".") and p != root]

from studio.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

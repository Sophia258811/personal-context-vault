"""Minimal stdio MCP server for task context from one explicitly configured vault."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .task_context import retrieve


TOOL = {
    "name": "get_task_context",
    "description": (
        "Read confirmed, currently supported memory for a task. Supply short topic terms "
        "and use the evidence IDs for provenance. An empty result means no matching memory."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "terms": {"type": "array", "minItems": 1, "maxItems": 10,
                      "items": {"type": "string", "minLength": 1, "maxLength": 100}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
        },
        "required": ["terms"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}


def call_tool(database, params):
    args = params.get("arguments")
    if params.get("name") != TOOL["name"] or not isinstance(args, dict) or args.keys() - {"terms", "limit"}:
        return tool_error("Unknown tool or invalid arguments.")
    terms = args.get("terms")
    limit = args.get("limit", 5)
    if (not isinstance(terms, list) or not 1 <= len(terms) <= 10
            or any(not isinstance(term, str) or not 1 <= len(term.strip()) <= 100 for term in terms)
            or type(limit) is not int or not 1 <= limit <= 5):
        return tool_error("Use 1–10 short topic terms and a limit from 1 to 5.")
    try:
        context = retrieve(database, terms, limit)
    except (OSError, sqlite3.Error, ValueError):
        return tool_error("Task context could not be read from the configured vault.")
    return {"content": [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]}


def tool_error(message):
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def dispatch(message, database):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in message:
        return None
    response = {"id": message["id"]}
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(params, dict):
        response["error"] = {"code": -32602, "message": "Invalid params"}
    elif method == "initialize":
        version = params.get("protocolVersion")
        supported = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
        response["result"] = {
            "protocolVersion": version if version in supported else "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pcv-context", "version": "0.1.0"},
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": [TOOL]}
    elif method == "tools/call":
        response["result"] = call_tool(database, params)
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    database = parser.parse_args().db.expanduser().resolve()
    if not database.is_file():
        parser.error("the configured vault does not exist")
    for line in sys.stdin.buffer:
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            response = {"id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = dispatch(message, database)
        if response is not None:
            print(json.dumps({"jsonrpc": "2.0", **response}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()

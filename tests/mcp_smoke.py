#!/usr/bin/env python3
"""Smoke-test an MCP server over stdio (newline-delimited JSON-RPC).

Usage:
    python3 tests/mcp_smoke.py [--call 'tool={"arg": "value"}' ...] [--full] -- <command...>

Example:
    python3 tests/mcp_smoke.py \
        --call 'get_dataset={"dataset_id": "seaf.company.ds.people"}' \
        -- docker run -i --rm -e API_BASE_URL=https://seaf-portal.example.com seaf-mcp:latest

Prints the tool list, then each call result (truncated to 2000 chars unless --full).
Exit code 0 if the server initialized and every call returned without protocol error.
"""

import argparse
import asyncio
import json
import sys

sys.stdout.reconfigure(line_buffering=True)


async def read_msg(reader, buffer: bytearray) -> dict | None:
    """Read one newline-delimited JSON message without asyncio's 64KB line limit."""
    while True:
        idx = buffer.find(b"\n")
        if idx >= 0:
            line = bytes(buffer[:idx])
            del buffer[:idx + 1]
            return json.loads(line)
        chunk = await reader.read(65536)
        if not chunk:
            return None
        buffer.extend(chunk)


async def request(writer, reader, buffer: bytearray, mid: int, method: str, params: dict) -> dict:
    writer.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}).encode() + b"\n")
    await writer.drain()
    while True:
        msg = await read_msg(reader, buffer)
        if msg is None:
            raise RuntimeError(f"server closed connection waiting for reply to {method}")
        if msg.get("id") == mid:
            return msg
        # skip notifications and log messages


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call", action="append", default=[],
                        help="tool call as name={json args}; repeatable")
    parser.add_argument("--full", action="store_true", help="print full call results")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="server command after --")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("server command required after --")

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    reader, writer = proc.stdout, proc.stdin
    buffer = bytearray()
    try:
        mid = 0

        async def rpc(method, params):
            nonlocal mid
            mid += 1
            return await request(writer, reader, buffer, mid, method, params)

        init = await rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-smoke", "version": "0.1"},
        })
        server_info = init.get("result", {}).get("serverInfo", {})
        print(f"initialized: {server_info.get('name')} {server_info.get('version')}")
        writer.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await writer.drain()

        tools = (await rpc("tools/list", {})).get("result", {}).get("tools", [])
        print(f"tools ({len(tools)}): {', '.join(t['name'] for t in tools)}")

        failed = False
        for spec in args.call:
            name, _, raw = spec.partition("=")
            call_args = json.loads(raw) if raw else {}
            reply = await rpc("tools/call", {"name": name, "arguments": call_args})
            if "error" in reply:
                print(f"\n### CALL {name} -> PROTOCOL ERROR: {reply['error']}")
                failed = True
                continue
            texts = [c.get("text", "") for c in reply.get("result", {}).get("content", [])]
            text = "\n".join(texts)
            limit = len(text) if args.full else 2000
            print(f"\n### CALL {name} ({len(text)} chars)\n{text[:limit]}")
        return 1 if failed else 0
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

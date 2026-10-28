"""MCP server exposing Claw-Bedrock skills as tools.

Runs over stdio so LiteLLM can spawn it as an MCP server. Reads skills from
the same TinyDB store the management UI writes to, so skills created/edited
in the UI are picked up on the next LiteLLM reload.
"""

import os
import subprocess
import sys

# Make the sibling modules importable when run as `python /app/mcp_skills_server.py`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

import db

EXECUTION_TIMEOUT = 30


def _is_exposed(skill: dict) -> bool:
    return bool(skill.get("expose_as_tool", True))


def _run_python(content: str, arguments: dict | None) -> str:
    try:
        proc = subprocess.run(
            [sys.executable],
            input=content,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {EXECUTION_TIMEOUT}s"
    out = proc.stdout or ""
    if proc.returncode != 0:
        err = proc.stderr or ""
        return (
            out + ("\n" if out and err else "") + err
        ).strip() or f"Exited with code {proc.returncode}"
    return out.strip()


def _run_shell(content: str, arguments: dict | None) -> str:
    try:
        proc = subprocess.run(
            content,
            shell=True,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {EXECUTION_TIMEOUT}s"
    out = proc.stdout or ""
    if proc.returncode != 0:
        err = proc.stderr or ""
        return (
            out + ("\n" if out and err else "") + err
        ).strip() or f"Exited with code {proc.returncode}"
    return out.strip()


def _run_prompt(content: str, arguments: dict | None) -> str:
    return content


def _run_template(content: str, arguments: dict | None) -> str:
    if not arguments:
        return content
    try:
        return content.format(**arguments)
    except KeyError, ValueError, IndexError:
        return content


_RUNNERS = {
    "python": _run_python,
    "shell": _run_shell,
    "prompt": _run_prompt,
    "template": _run_template,
}


def execute_skill(skill: dict, arguments: dict | None = None) -> str:
    """Run a skill and return its text output. Pure-ish; unit-testable."""
    skill_type = skill.get("type", "prompt")
    runner = _RUNNERS.get(skill_type, _run_prompt)
    content = skill.get("content", "") or ""
    try:
        return runner(content, arguments)
    except Exception as exc:  # noqa: BLE001 - a single skill must not kill the server
        return f"Error running skill: {exc}"


def build_server() -> FastMCP:
    server = FastMCP("claw-skills")

    for skill in db.get_all_skills():
        if not _is_exposed(skill):
            continue
        name = skill.get("name")
        if not name:
            continue
        description = skill.get("description") or f"Run the '{name}' skill."

        def make_tool(sk):
            def invoke(arguments: dict | None = None) -> str:
                return execute_skill(sk, arguments)

            return invoke

        server.add_tool(
            make_tool(skill),
            name=name,
            description=description,
        )

    return server


def main() -> None:
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

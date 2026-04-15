#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
ZSHRC = Path(os.environ.get("ZSHRC_PATH_OVERRIDE", str(Path.home() / ".zshrc")))


def require(cmd: str):
    if shutil.which(cmd) is None:
        sys.stderr.write(f"Error: required command not found: {cmd}\n")
        sys.exit(1)


def run(cmd, env=None, capture_output=False):
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        env=env,
        capture_output=capture_output,
    )


def ensure_login():
    try:
        run(["aws", "sts", "get-caller-identity", "--profile", PROFILE], capture_output=True)
    except subprocess.CalledProcessError:
        sys.stderr.write(f"AWS profile '{PROFILE}' is not authenticated. Starting aws login...\n")
        run(["aws", "login", "--profile", PROFILE, "--region", REGION])


def generate_token() -> str:
    os.environ["AWS_PROFILE"] = PROFILE
    os.environ["AWS_REGION"] = REGION
    from aws_bedrock_token_generator import provide_token
    return provide_token(region=REGION).strip()


def update_zshrc(token: str):
    ZSHRC.touch(exist_ok=True)
    with ZSHRC.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    new_line = f'export BEDROCK_MANTLE_API_KEY="{token}"\n'
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("export BEDROCK_MANTLE_API_KEY="):
            lines[i] = new_line
            replaced = True
            break

    if not replaced:
        # Insert before the last non-empty line (preserves trailing eval)
        insert_at = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                insert_at = i
                break
        lines.insert(insert_at, new_line)

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(ZSHRC.parent)) as tmp:
        tmp.writelines(lines)
        tmp_path = Path(tmp.name)

    tmp_path.replace(ZSHRC)


def main():
    require("aws")
    ensure_login()
    token = generate_token()
    update_zshrc(token)
    print(f"Updated {ZSHRC}")
    print(f"Run: source {ZSHRC}")
    print(f'Test: curl -s "$OPENAI_BASE_URL/models" -H "Authorization: Bearer $AWS_BEARER_TOKEN_BEDROCK"')


if __name__ == "__main__":
    main()
"""Guard the litellm pin introduced by the 2026-10-21 supply-chain incident.

The running image and lockfile agree on 1.102.1 today; these assertions keep a
future PR from silently widening the pin in requirements.txt or pulling a
known-compromised release through requirements.lock.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPROMISED = ("1.82.7", "1.82.8", "1.107.0")
SAFE_PIN = "litellm[proxy]==1.102.1"


def _litellm_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("litellm[proxy]"):
            return stripped
    return None


def test_requirements_txt_pins_litellm_exactly():
    text = (REPO_ROOT / "requirements.txt").read_text()
    assert _litellm_line(text) == SAFE_PIN, _litellm_line(text)


def test_requirements_lock_pins_litellm_exactly():
    lines = (REPO_ROOT / "requirements.lock").read_text().splitlines()
    assert SAFE_PIN in lines, "litellm pin missing or changed in requirements.lock"


def test_no_compromised_litellm_versions():
    # requirements.txt may mention compromised versions in its explanatory
    # comment; the lock is what actually gets installed, so guard that.
    text = (REPO_ROOT / "requirements.lock").read_text()
    for bad in COMPROMISED:
        assert bad not in text, f"{bad} appears in requirements.lock"

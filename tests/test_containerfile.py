"""The Containerfile must ship every module the app imports.

The image is built but never started in CI, so a module missing from the
Containerfile's COPY list is indistinguishable from a good image until it is
deployed. It then dies at import: the management process exits, start_container.sh
is `wait ${MGMT_PID}` so the container goes down with it, and the service is
simply gone. That is how settings_resolver.py reached production in a container
that reported healthy builds and passed all 198 tests.

scripts/smoke.sh covers the same ground by booting the image. These assertions
are the cheap static half: they run in the `test` job in seconds, they fail with
a precise message naming the missing file, and they hold even if the smoke test
is ever disabled or muted for flakiness.

Run from project root:
    PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/ -q
"""

import glob
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINERFILE = REPO_ROOT / ".github" / "Containerfile"
SRC = REPO_ROOT / "src"


def _copy_lines():
    """Yield the source patterns of every COPY instruction, one per path.

    Patterns are returned unexpanded: `COPY src/*.py .` is the preferred form
    precisely because it cannot fall out of date, so the comparison below has
    to expand them to decide what actually lands in the image.
    """
    for raw in CONTAINERFILE.read_text().splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        # `COPY src1 src2 ... dest` -- the last token is the destination and
        # everything before it is a source. Tokenised rather than regexed: a
        # multi-source COPY is the normal case here, not an edge case.
        yield from line.split()[1:-1]


def _expand(patterns):
    """Resolve COPY source patterns to the files they place in the image."""
    copied = set()
    for pattern in patterns:
        matched = glob.glob(str(REPO_ROOT / pattern), recursive=True)
        for path in matched:
            # A directory COPY is recorded by its own path; a file COPY by the
            # file. Both are compared against concrete repo paths below.
            copied.add(Path(path).resolve())
    return copied


COPIED_SOURCES = _expand(_copy_lines())


class TestModulesAreCopied:
    def test_containerfile_exists(self):
        assert CONTAINERFILE.is_file(), f"missing {CONTAINERFILE}"

    def test_src_has_modules_to_copy(self):
        # Guards the rest of the class against silently testing nothing: if the
        # glob below ever returns empty, every per-file assertion passes vacuously.
        modules = sorted(p.name for p in SRC.glob("*.py"))
        assert modules, f"no modules found in {SRC}"

    def test_copy_lines_are_not_continued(self):
        # A trailing backslash would make the parser above read only the first
        # fragment of the instruction and quietly under-report what is copied.
        # Fail loudly instead.
        continued = [
            lineno
            for lineno, raw in enumerate(
                CONTAINERFILE.read_text().splitlines(), start=1
            )
            if raw.strip().upper().startswith("COPY ") and raw.rstrip().endswith("\\")
        ]
        assert not continued, (
            f"multi-line COPY at {continued} is not supported by this parser; "
            "put each COPY on one line."
        )

    @pytest.mark.parametrize("module", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
    def test_module_is_copied(self, module):
        assert module.resolve() in COPIED_SOURCES, (
            f"{module.name} is not copied by the Containerfile. The image will "
            "build fine and fail at import on boot. Add it to a COPY line, or "
            "use `COPY src/*.py .` so new modules are picked up automatically."
        )


class TestRuntimeAssetsAreCopied:
    """The non-.py paths the app reads off disk at runtime.

    A missing template or asset directory does not raise at import; it fails
    later, on the first request that touches it, so it is easier to miss.
    """

    @pytest.mark.parametrize(
        "path",
        ["templates", "static", "start_container.sh"],
        ids=lambda p: p,
    )
    def test_asset_is_copied(self, path):
        expected = {
            "templates": REPO_ROOT / "templates",
            "static": SRC / "static",
            "start_container.sh": REPO_ROOT / "deploy" / "start_container.sh",
        }[path]
        assert expected.resolve() in COPIED_SOURCES, (
            f"{path} is not copied by the Containerfile."
        )

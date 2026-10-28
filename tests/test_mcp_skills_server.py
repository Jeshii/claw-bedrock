import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "mcp_skills_server", os.path.join(SRC_DIR, "mcp_skills_server.py")
    )
    # db import needs CONFIG_DIR; set before loading
    os.environ.setdefault("CONFIG_DIR", "/tmp")
    os.environ.setdefault(
        "ENCRYPTION_KEY", "7et38Ta-TUAjQExZycUZs4X1HZq9CqfgCoVcQmJjvMs="
    )
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mcp():
    return _load_module()


def test_execute_prompt(mcp):
    skill = {"name": "p", "type": "prompt", "content": "hello world"}
    assert mcp.execute_skill(skill) == "hello world"


def test_execute_template(mcp):
    skill = {"name": "t", "type": "template", "content": "Hi {name}, you are {age}"}
    assert mcp.execute_skill(skill, {"name": "Ada", "age": 30}) == "Hi Ada, you are 30"


def test_execute_template_missing_key_falls_back(mcp):
    skill = {"name": "t", "type": "template", "content": "Hi {name}, {missing}"}
    assert mcp.execute_skill(skill, {"name": "Ada"}) == "Hi {name}, {missing}"


def test_execute_python(mcp):
    skill = {"name": "py", "type": "python", "content": "print(2 + 3)"}
    assert mcp.execute_skill(skill).strip() == "5"


def test_execute_shell(mcp):
    skill = {"name": "sh", "type": "shell", "content": "echo hi"}
    assert mcp.execute_skill(skill).strip() == "hi"


def test_is_exposed_default_true(mcp):
    assert mcp._is_exposed({"name": "x"}) is True


def test_is_exposed_false(mcp):
    assert mcp._is_exposed({"name": "x", "expose_as_tool": False}) is False


def test_unknown_type_defaults_to_prompt(mcp):
    skill = {"name": "u", "type": "nope", "content": "fallback"}
    assert mcp.execute_skill(skill) == "fallback"

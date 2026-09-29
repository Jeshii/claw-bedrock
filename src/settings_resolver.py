"""Settings precedence for configuration that has both an env and a stored form.

The order is the unix one — flags, then environment variables, then the config
store, then whatever the code defaults to. A value set in the environment is
expert mode: the GUI yields to it, and reports that it lost, rather than
silently appearing to take effect.

This module is deliberately stateless. Nothing here is cached at import time,
so a caller that re-reads configuration always sees current values.
"""

import os
from dataclasses import dataclass
from typing import Literal

Source = Literal["flag", "env", "config", "default"]

#: Precedence order. Index 0 wins.
_PRECEDENCE: tuple[Source, ...] = ("flag", "env", "config", "default")


@dataclass(frozen=True)
class Resolved:
    """A setting's effective value plus where it came from."""

    value: str | None
    source: Source
    #: Lower-precedence layers that also hold a value, and so lost. Excludes
    #: `default`, which is a fallback rather than a deliberate choice.
    shadowed: tuple[Source, ...] = ()

    @property
    def is_shadowed(self) -> bool:
        return bool(self.shadowed)

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "shadowed": list(self.shadowed),
        }


def _present(value: object) -> bool:
    """An empty or whitespace-only string counts as unset.

    Container env vars are routinely set to "" by a compose file or quadlet, and
    treating that as a real value would let an empty env var outrank a config
    entry the user actually filled in.
    """
    if not isinstance(value, str):
        return bool(value)
    return bool(value.strip())


def resolve(
    env_var: str | None,
    *,
    config_key: str | None = None,
    flag: str | None = None,
    default: str | None = None,
) -> Resolved:
    """Resolve one setting across the precedence chain.

    `env_var` is looked up in `os.environ`; `config_key` is looked up in the
    settings table. They are passed separately because the two namespaces are
    independent — the settings table also holds `use_prefix`,
    `routing_strategy` and the encrypted LiteLLM master key, and imports keys
    verbatim from a restored backup, so reusing an env var name as a config key
    would invite collisions.
    """
    # Imported per call rather than at module scope. `db` opens its TinyDB file
    # at import, and callers that reload the module set (the test suite does, to
    # isolate CONFIG_DIR) would otherwise leave this module holding a reference
    # to a closed file.
    import db  # local import keeps this module free of import-order coupling

    config_value = db.get_setting(config_key) if config_key else None

    layers: dict[Source, object] = {
        "flag": flag,
        "env": os.environ.get(env_var) if env_var else None,
        "config": config_value,
        "default": default,
    }

    winner = next((s for s in _PRECEDENCE if _present(layers[s])), "default")
    # `default` is a fallback rather than something anyone chose, so it is
    # excluded from `shadowed`: "overrides config, default" tells the user
    # nothing they can act on. It is still reported as the source when it wins.
    shadowed = tuple(
        s
        for s in _PRECEDENCE[_PRECEDENCE.index(winner) + 1 : -1]
        if _present(layers[s])
    )

    value = layers[winner] if _present(layers[winner]) else None
    return Resolved(value=value, source=winner, shadowed=shadowed)

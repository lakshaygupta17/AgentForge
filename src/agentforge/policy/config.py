"""Strict YAML loading for human-readable AgentForge security policies."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml

from agentforge.policy.engine import SecurityPolicy


class PolicyConfigurationError(ValueError):
    """A policy file is malformed, ambiguous, or contains unsupported fields."""


def load_security_policy(path: Path) -> SecurityPolicy:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PolicyConfigurationError(f"Could not read policy file: {error}") from error
    document = _mapping(loaded, "policy")
    _reject_unknown(
        document,
        {"version", "commands", "resources", "network", "filesystem"},
        "policy",
    )
    if document.get("version") != 1:
        raise PolicyConfigurationError("Policy version must be 1")

    defaults = SecurityPolicy()
    commands = _section(document, "commands")
    resources = _section(document, "resources")
    network = _section(document, "network")
    filesystem = _section(document, "filesystem")
    _reject_unknown(
        commands,
        {
            "forbidden_executables",
            "maximum_timeout_seconds",
            "allowed_environment_keys",
            "allowed_git_subcommands",
        },
        "commands",
    )
    _reject_unknown(
        resources,
        {"maximum_cpu_count", "maximum_memory_mb", "maximum_processes", "maximum_output_bytes"},
        "resources",
    )
    _reject_unknown(network, {"allow", "allowed_destinations"}, "network")
    _reject_unknown(
        filesystem, {"readable_paths", "writable_paths", "denied_paths"}, "filesystem"
    )

    return SecurityPolicy(
        forbidden_executables=_string_set(
            commands, "forbidden_executables", defaults.forbidden_executables
        ),
        maximum_timeout_seconds=_positive_number(
            commands, "maximum_timeout_seconds", defaults.maximum_timeout_seconds
        ),
        allowed_environment_keys=_string_set(
            commands, "allowed_environment_keys", defaults.allowed_environment_keys
        ),
        maximum_cpu_count=_positive_number(
            resources, "maximum_cpu_count", defaults.maximum_cpu_count
        ),
        maximum_memory_mb=_positive_int(
            resources, "maximum_memory_mb", defaults.maximum_memory_mb
        ),
        maximum_processes=_positive_int(
            resources, "maximum_processes", defaults.maximum_processes
        ),
        maximum_output_bytes=_positive_int(
            resources, "maximum_output_bytes", defaults.maximum_output_bytes
        ),
        allow_network_access=_boolean(network, "allow", defaults.allow_network_access),
        allowed_network_destinations=_string_set(
            network, "allowed_destinations", defaults.allowed_network_destinations
        ),
        readable_paths=_string_tuple(
            filesystem, "readable_paths", defaults.readable_paths
        ),
        writable_paths=_string_tuple(
            filesystem, "writable_paths", defaults.writable_paths
        ),
        denied_paths=_string_tuple(filesystem, "denied_paths", defaults.denied_paths),
        allowed_git_subcommands=_string_set(
            commands, "allowed_git_subcommands", defaults.allowed_git_subcommands
        ),
    )


def _section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = document.get(key, {})
    return _mapping(value, key)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PolicyConfigurationError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _reject_unknown(
    values: Mapping[str, object], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise PolicyConfigurationError(
            f"Unknown {label} fields: {', '.join(unknown)}"
        )


def _string_values(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PolicyConfigurationError(f"{label} must be a list of non-empty strings")
    return tuple(cast(list[str], value))


def _string_set(
    values: Mapping[str, object], key: str, default: frozenset[str]
) -> frozenset[str]:
    if key not in values:
        return default
    return frozenset(_string_values(values[key], key))


def _string_tuple(
    values: Mapping[str, object], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    if key not in values:
        return default
    return _string_values(values[key], key)


def _positive_number(
    values: Mapping[str, object], key: str, default: float
) -> float:
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise PolicyConfigurationError(f"{key} must be a positive number")
    return float(value)


def _positive_int(values: Mapping[str, object], key: str, default: int) -> int:
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PolicyConfigurationError(f"{key} must be a positive integer")
    return value


def _boolean(values: Mapping[str, object], key: str, default: bool) -> bool:
    if key not in values:
        return default
    value = values[key]
    if not isinstance(value, bool):
        raise PolicyConfigurationError(f"{key} must be true or false")
    return value

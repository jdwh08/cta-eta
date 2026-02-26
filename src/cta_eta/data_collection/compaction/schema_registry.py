"""Schema registry module for CTA compaction pipeline.

Provides:
- DriftResult dataclass with BreakingFieldChange, AddedField, NullabilityChange
- classify_drift(registry_schema, observed_schema) — field-by-field drift classification
- schema_to_registry_dict(schema, daemon_name) — serialize schema to JSON-safe dict
- registry_dict_to_schema(data) — deserialize schema from registry dict
- load_registry(path) — load schema from JSON registry file
- save_registry(path, schema, daemon_name) — write schema to JSON registry file
- bootstrap_registry(path, schema, daemon_name) — create registry if none exists

Registry JSON format:
    {
        "version": 1,
        "daemon": "<daemon_name>",
        "updated": "YYYY-MM-DD",
        "fields": [{"name": "...", "type": "...", "nullable": bool}, ...],
        "schema_ipc_b64": "<base64-encoded Arrow IPC schema bytes>"
    }

The human-readable "fields" list enables git-diffable registry files.
The "schema_ipc_b64" ensures exact round-trip for complex types like
timestamp[us, tz=UTC] that cannot be reconstructed from type strings alone.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.ipc

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Drift classification types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakingFieldChange:
    """A field whose type changed incompatibly."""

    name: str
    old_type: str
    new_type: str


@dataclass(frozen=True)
class AddedField:
    """A new field present in observed schema but not in registry."""

    name: str
    type: str  # noqa: A003  (shadows builtin but matches spec)


@dataclass(frozen=True)
class NullabilityChange:
    """A field whose nullability changed."""

    name: str
    old_nullable: bool
    new_nullable: bool


@dataclass
class DriftResult:
    """Result of schema drift classification.

    drift_type priority: breaking > additive > none.
    """

    drift_type: Literal["none", "additive", "breaking"]
    breaking_fields: list[BreakingFieldChange] = field(default_factory=list)
    added_fields: list[AddedField] = field(default_factory=list)
    removed_fields: list[str] = field(default_factory=list)
    nullability_changes: list[NullabilityChange] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Widening pairs: safe numeric widening (from RESEARCH.md)
# ---------------------------------------------------------------------------

# frozenset of (old_type, new_type) tuples representing allowed widenings.
# Stored as string pairs to allow simple lookup via str(pa.DataType).
_WIDENING_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        # int8 widenings
        ("int8", "int16"),
        ("int8", "int32"),
        ("int8", "int64"),
        # int16 widenings
        ("int16", "int32"),
        ("int16", "int64"),
        # int32 widenings
        ("int32", "int64"),
        # int → float widenings
        ("int32", "double"),
        ("int64", "double"),
        # float32 → float64
        ("float", "double"),
    }
)


def _is_widening(old_type: pa.DataType, new_type: pa.DataType) -> bool:
    """Return True if old_type → new_type is a safe numeric widening."""
    return (str(old_type), str(new_type)) in _WIDENING_PAIRS


# ---------------------------------------------------------------------------
# classify_drift
# ---------------------------------------------------------------------------


def classify_drift(
    registry_schema: pa.Schema, observed_schema: pa.Schema
) -> DriftResult:
    """Compare observed_schema against registry_schema.

    Field order is ignored — comparison is name-based.

    Returns a DriftResult with drift_type:
    - "none": no differences (or only safe widenings)
    - "additive": new fields added, no breaking changes
    - "breaking": removed fields, incompatible type changes, or nullability changes
    """
    # Build name-indexed dicts
    registry_fields: dict[str, pa.Field] = {
        registry_schema.field(i).name: registry_schema.field(i)
        for i in range(len(registry_schema))
    }
    observed_fields: dict[str, pa.Field] = {
        observed_schema.field(i).name: observed_schema.field(i)
        for i in range(len(observed_schema))
    }

    removed: list[str] = []
    breaking: list[BreakingFieldChange] = []
    added: list[AddedField] = []
    nullability: list[NullabilityChange] = []

    # Check registry fields against observed
    for name, reg_field in registry_fields.items():
        if name not in observed_fields:
            removed.append(name)
            continue

        obs_field = observed_fields[name]

        # Type comparison
        if reg_field.type != obs_field.type:
            if not _is_widening(reg_field.type, obs_field.type):
                breaking.append(
                    BreakingFieldChange(
                        name=name,
                        old_type=str(reg_field.type),
                        new_type=str(obs_field.type),
                    )
                )
            # Widening: silent, no entry needed

        # Nullability comparison (independent of type)
        if reg_field.nullable != obs_field.nullable:
            nullability.append(
                NullabilityChange(
                    name=name,
                    old_nullable=reg_field.nullable,
                    new_nullable=obs_field.nullable,
                )
            )

    # Check for new fields in observed but not registry
    for name, obs_field in observed_fields.items():
        if name not in registry_fields:
            added.append(AddedField(name=name, type=str(obs_field.type)))

    # Determine drift_type with priority: breaking > additive > none
    if removed or breaking or nullability:
        drift_type: Literal["none", "additive", "breaking"] = "breaking"
    elif added:
        drift_type = "additive"
    else:
        drift_type = "none"

    return DriftResult(
        drift_type=drift_type,
        breaking_fields=breaking,
        added_fields=added,
        removed_fields=removed,
        nullability_changes=nullability,
    )


# ---------------------------------------------------------------------------
# Registry serialization
# ---------------------------------------------------------------------------


def schema_to_registry_dict(schema: pa.Schema, daemon_name: str) -> dict:  # type: ignore[type-arg]
    """Convert a pa.Schema to a JSON-serializable registry dictionary.

    The dict contains:
    - version: 1
    - daemon: daemon_name
    - updated: today's ISO date
    - fields: human-readable list of {name, type, nullable} for git diff
    - schema_ipc_b64: base64-encoded IPC schema bytes for exact reconstruction
    """
    fields_list = [
        {
            "name": schema.field(i).name,
            "type": str(schema.field(i).type),
            "nullable": schema.field(i).nullable,
        }
        for i in range(len(schema))
    ]
    ipc_bytes = schema.serialize().to_pybytes()
    ipc_b64 = base64.b64encode(ipc_bytes).decode("ascii")

    return {
        "version": 1,
        "daemon": daemon_name,
        "updated": date.today().isoformat(),
        "fields": fields_list,
        "schema_ipc_b64": ipc_b64,
    }


def registry_dict_to_schema(data: dict) -> pa.Schema:  # type: ignore[type-arg]
    """Reconstruct a pa.Schema from a registry dictionary.

    Uses schema_ipc_b64 for exact reconstruction — the human-readable
    'fields' list is for human review only and is ignored here.
    """
    ipc_bytes = base64.b64decode(data["schema_ipc_b64"])
    return pa.ipc.read_schema(pa.BufferReader(ipc_bytes))


# ---------------------------------------------------------------------------
# Registry file I/O
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> pa.Schema | None:
    """Load canonical schema from a JSON registry file.

    Returns None if path does not exist.
    Raises ValueError on corrupt or invalid JSON.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return registry_dict_to_schema(data)
    except (json.JSONDecodeError, KeyError, Exception) as exc:
        raise ValueError(
            f"Failed to load schema registry from {path}: {exc}"
        ) from exc


def save_registry(path: Path, schema: pa.Schema, daemon_name: str) -> None:
    """Write schema to a JSON registry file.

    Creates parent directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = schema_to_registry_dict(schema, daemon_name)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _log.info("Saved schema registry to %s", path)


def bootstrap_registry(path: Path, schema: pa.Schema, daemon_name: str) -> bool:
    """Create a registry file if none exists.

    If path exists: no-op, returns False.
    If path does not exist: calls save_registry() and returns True.
    """
    if path.exists():
        return False
    save_registry(path, schema, daemon_name)
    _log.info("Bootstrapped schema registry for %s at %s", daemon_name, path)
    return True

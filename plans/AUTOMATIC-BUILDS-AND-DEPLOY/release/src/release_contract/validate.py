"""Schema- and rule-based validation of OnlineShop release manifests.

Validation pipeline:
1. Strict JSON parsing (``json.load``) -- never regex parsing of the document.
2. Control-character scan of every string key and value (catches escaped
   ``\\u0000`` etc. that ``json.load`` accepts).
3. Structural validation against the versioned JSON Schema using the pinned
   ``jsonschema`` engine (Draft-07).
4. Cross-field identity-agreement rules (see ``crossrules.py``).

Every issue is normalized into a deterministic ``{code, field, message}``
triple so downstream tooling and tests can rely on stable output independent of
the exact ``jsonschema`` version.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft7Validator, ValidationError
from referencing import Registry, Resource

from . import crossrules

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RELEASE_ROOT = os.path.dirname(_PACKAGE_ROOT)
SCHEMA_PATH = os.path.join(_RELEASE_ROOT, "schema", "release-manifest.schema.json")

VALID_STATUSES = ("candidate", "official")

# Human-readable hints keyed by normalized field path, used to make pattern
# violations readable without sacrificing machine-stable error codes.
_FIELD_HINTS = {
    "schemaVersion": "manifest schema version (must be 1)",
    "release.version": "canonical MAJOR.MINOR.PATCH; no leading zeroes, "
    "prerelease, build metadata, or leading 'v'",
    "release.gitTag": "git/release tag v<version>",
    "release.status": "manifest state (candidate or official)",
    "release.createdAt": "RFC 3339 UTC timestamp with a trailing 'Z'",
    "release.sourceSha": "full 40-character lowercase hex monorepo commit SHA",
    "release.repository": "GitHub owner/repository, e.g. Djimi/OnlineShop-full-stack",
    "release.candidateWorkflow.runId": "GitHub Actions run ID (positive integer)",
    "release.candidateWorkflow.runAttempt": "GitHub Actions run attempt (positive integer)",
    "release.candidateWorkflow.url": "HTTPS URL of the candidate run",
    "release.artifactWorkflow.runId": "GitHub Actions run ID (positive integer)",
    "release.artifactWorkflow.runAttempt": "GitHub Actions run attempt (positive integer)",
    "release.artifactWorkflow.url": "HTTPS URL of the artifact-producing run",
    "release.stagingValidation.job": "staging E2E job name (e2e-staging)",
    "release.stagingValidation.validatedAt": "RFC 3339 UTC timestamp with a trailing 'Z'",
    "release.promotionWorkflow.runId": "GitHub Actions run ID (positive integer)",
    "release.promotionWorkflow.actor": "GitHub login that dispatched the promotion",
    "release.promotionWorkflow.approvedBy": "GitHub login that approved the promotion",
    "release.promotionWorkflow.approvedAt": "RFC 3339 UTC timestamp with a trailing 'Z'",
    "release.promotionWorkflow.deployedAt": "RFC 3339 UTC timestamp with a trailing 'Z'",
}


@dataclass
class ValidationResult:
    """Outcome of validating one manifest document."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)
    checksum: str | None = None


def dotted_path(pointer: str) -> str:
    """Convert a JSON Pointer (``/release/version``) to ``release.version``."""
    if pointer == "" or pointer == "/":
        return "$"
    parts = pointer.strip("/").split("/")
    return ".".join(part for part in parts)


def _unexpected_properties(error: ValidationError) -> list[str]:
    """Return property names rejected by an additionalProperties error."""
    instance = error.instance
    props = {}
    pattern_props = []
    if isinstance(error.schema, dict):
        props = error.schema.get("properties", {})
        pattern_props = error.schema.get("patternProperties", {})
    unexpected = []
    if isinstance(instance, dict):
        for key in instance:
            if key in props:
                continue
            if any(re.search(pattern, key) for pattern in pattern_props):
                continue
            unexpected.append(key)
    return unexpected


def _missing_properties(error: ValidationError) -> list[str]:
    """Return property names required by a required error but absent."""
    instance = error.instance
    required = error.validator_value if isinstance(error.validator_value, list) else []
    missing = [name for name in required if not isinstance(instance, dict) or name not in instance]
    return missing


def _field_hint(field: str) -> str | None:
    return _FIELD_HINTS.get(field)


def _message_for_field(field: str, value: Any) -> str:
    hint = _field_hint(field)
    if hint:
        return f"invalid value {value!r}; {hint}"
    return f"invalid value {value!r}"


def _normalize_schema_error(error: ValidationError) -> dict[str, str]:
    """Convert one jsonschema error into a deterministic issue triple."""
    keyword = error.validator
    field = dotted_path("/" + "/".join(str(p) for p in error.absolute_path))
    value = error.instance

    if keyword == "additionalProperties":
        unexpected = _unexpected_properties(error)
        name = unexpected[0] if unexpected else "<unknown>"
        return {
            "code": "EXTRA_FIELD",
            "field": f"{field}.{name}" if field and field != "$" else name,
            "message": f"field {name!r} is not allowed here",
        }

    if keyword == "required":
        missing = _missing_properties(error)
        name = missing[0] if missing else "<unknown>"
        return {
            "code": "MISSING_FIELD",
            "field": f"{field}.{name}" if field and field != "$" else name,
            "message": f"required field {name!r} is missing",
        }

    if keyword == "const":
        return {
            "code": "CONST_MISMATCH",
            "field": field,
            "message": (
                f"value {value!r} must equal {error.validator_value!r}; "
                f"{_field_hint(field) or 'see schema'}"
            ),
        }

    if keyword == "enum":
        return {
            "code": "INVALID_ENUM_VALUE",
            "field": field,
            "message": f"value {value!r} is not one of {error.validator_value!r}",
        }

    if keyword == "pattern":
        return {
            "code": "INVALID_FORMAT",
            "field": field,
            "message": _message_for_field(field, value),
        }

    if keyword == "type":
        return {
            "code": "INVALID_TYPE",
            "field": field,
            "message": f"value {value!r} must be of type {error.validator_value!r}",
        }

    if keyword == "minimum":
        return {
            "code": "OUT_OF_RANGE",
            "field": field,
            "message": f"value {value!r} must be >= {error.validator_value!r}",
        }

    if keyword in ("minLength", "maxLength"):
        return {
            "code": "INVALID_LENGTH",
            "field": field,
            "message": f"value length does not satisfy {keyword}={error.validator_value!r}",
        }

    if keyword in ("minItems", "maxItems"):
        return {
            "code": "OUT_OF_RANGE",
            "field": field,
            "message": f"collection size does not satisfy {keyword}={error.validator_value!r}",
        }

    if keyword == "anyOf":
        # The root anyOf distinguishes candidate from official state. Surface
        # the closest branch's dominant error for a readable, deterministic
        # message (handled by the caller for branch selection).
        return {
            "code": "STATE_MISMATCH",
            "field": field,
            "message": "document matches neither the candidate nor the official release schema",
        }

    return {
        "code": f"SCHEMA_{keyword.upper()}",
        "field": field,
        "message": error.message,
    }


def _schema_id(schema: dict[str, Any]) -> str:
    return schema.get("$id") or "urn:onlineshop:release-manifest"


def _flatten_any_of(data: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    """Produce deterministic issues for a root anyOf failure.

    The document must match exactly one of ``candidateManifest`` or
    ``officialManifest``. We validate the document against each branch schema
    directly (with ``$ref`` resolution) and prefer the branch whose
    ``release.status`` matches, so e.g. an official manifest missing
    ``promotionWorkflow`` reports the missing field rather than a
    candidate-branch const mismatch. A present-but-invalid status is reported
    directly.
    """
    if isinstance(data, dict):
        release = data.get("release")
        status = release.get("status") if isinstance(release, dict) else None
    else:
        status = None

    if status is not None and status not in VALID_STATUSES:
        return [
            {
                "code": "INVALID_ENUM_VALUE",
                "field": "release.status",
                "message": f"value {status!r} is not one of {list(VALID_STATUSES)!r}",
            }
        ]

    resolver = Registry().with_resource(_schema_id(schema), Resource.from_contents(schema))
    branch_issues: dict[str, list[dict[str, str]]] = {}
    for name in ("candidate", "official"):
        branch_root = {"$ref": f"{_schema_id(schema)}#/definitions/{name}Manifest"}
        validator = Draft7Validator(branch_root, registry=resolver)
        errors = sorted(validator.iter_errors(data), key=_error_sort_key)
        issues = [_normalize_schema_error(error) for error in errors]
        deduped = []
        seen = set()
        for issue in issues:
            key = (issue["code"], issue["field"])
            if key not in seen:
                seen.add(key)
                deduped.append(issue)
        deduped.sort(key=lambda item: (item["field"], item["code"]))
        branch_issues[name] = deduped

    if status in VALID_STATUSES:
        return branch_issues[status]
    # Status unknown/absent: surface the branch with the fewest problems.
    candidate = branch_issues["candidate"]
    official = branch_issues["official"]
    if not candidate:
        return official
    if not official:
        return candidate
    if len(candidate) <= len(official):
        return candidate
    return official


def find_unsafe_characters(data: Any, path: str = "") -> list[dict[str, str]]:
    """Scan every string key and value for control characters.

    ``json.load`` already rejects raw control characters in strict mode, but it
    accepts escaped ones such as ``\\u0000``; those must never reach shell,
    JSON, GitHub CLI, or AWS CLI interpolation.
    """
    issues: list[dict[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str):
                for ch in key:
                    if ord(ch) < 0x20 or ord(ch) == 0x7F:
                        issues.append(
                            {
                                "code": "UNSAFE_CHARACTER",
                                "field": f"{path}.{key}" if path else key,
                                "message": f"object key contains control character U+{ord(ch):04X}",
                            }
                        )
                        break
            issues.extend(find_unsafe_characters(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            issues.extend(find_unsafe_characters(value, f"{path}[{index}]"))
    elif isinstance(data, str):
        for ch in data:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                issues.append(
                    {
                        "code": "UNSAFE_CHARACTER",
                        "field": path or "$",
                        "message": f"string contains control character U+{ord(ch):04X}",
                    }
                )
                break
    return issues


def load_manifest(path: str) -> Any:
    """Parse a manifest file strictly. Raises ``json.JSONDecodeError``."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def validate_data(data: Any, schema_path: str = SCHEMA_PATH) -> ValidationResult:
    """Validate an already-parsed manifest document."""
    issues: list[dict[str, str]] = []

    issues.extend(find_unsafe_characters(data))
    schema_path = os.path.abspath(schema_path)
    with open(schema_path, encoding="utf-8") as handle:
        schema = json.load(handle)

    validator = Draft7Validator(schema)
    schema_errors = sorted(validator.iter_errors(data), key=_error_sort_key)

    if schema_errors:
        any_of_failure = any(error.validator == "anyOf" for error in schema_errors)
        if any_of_failure:
            issues.extend(_flatten_any_of(data, schema))
        else:
            for error in schema_errors:
                issues.append(_normalize_schema_error(error))

    envelope = (
        isinstance(data, dict)
        and isinstance(data.get("release"), dict)
        and isinstance(data.get("components"), dict)
    )
    if envelope and not schema_errors:
        issues.extend(crossrules.apply_cross_field_rules(data))

    deduped = []
    seen = set()
    for issue in issues:
        key = (issue["code"], issue["field"])
        if key not in seen:
            seen.add(key)
            deduped.append(issue)
    deduped.sort(key=lambda item: (item["field"], item["code"]))

    return ValidationResult(valid=not deduped, issues=deduped, checksum=_safe_checksum(data))


def _error_sort_key(error: ValidationError) -> tuple:
    return (len(error.absolute_path), tuple(error.absolute_path), error.validator)


def validate_file(path: str, schema_path: str = SCHEMA_PATH) -> ValidationResult:
    """Parse and validate a manifest file on disk."""
    try:
        data = load_manifest(path)
    except json.JSONDecodeError as exc:
        return ValidationResult(
            valid=False,
            issues=[
                {
                    "code": "INVALID_JSON",
                    "field": "$",
                    "message": (
                        f"document is not valid JSON: {exc.msg} at "
                        f"line {exc.lineno} column {exc.colno}"
                    ),
                }
            ],
        )
    return validate_data(data, schema_path=schema_path)


def _safe_checksum(data: Any) -> str | None:
    from . import checksums

    try:
        return checksums.manifest_checksum(data)
    except Exception:
        return None

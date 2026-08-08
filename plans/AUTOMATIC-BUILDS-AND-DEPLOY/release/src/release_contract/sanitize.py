"""Task-definition sanitization and drift-proofing (Pass 3, subphase 3.5).

Subphase 3.4 registers a new production task-definition revision by copying the
current definition and replacing **only** the intended container image with a
digest-pinned ``<registry>/<repository>@sha256:<digest>`` reference. This
module makes that transform safe and provable:

- :func:`sanitize_task_definition` copies the definition and sets exactly the
  named containers' ``image`` field to the given digest references.
- :func:`sanitized_diff_issues` proves the transform did not drift: every
  container that was re-imaged is now digest-pinned, nothing except ``image``
  changed, no container was removed, secret references are unchanged full ARNs
  in ``secrets[].valueFrom``, and no secret reference ever appears as
  plaintext in ``environment``/``command``.
- :func:`diff_fields` returns the exact set of changed field paths for the
  operator-facing read-back.

Secret hygiene is the core 3.5 requirement: sanitization must never copy a
secret value (or a secret ARN that reveals it) into a plaintext field. Because
real secret values are never present in the task definition (they live in
Secrets Manager and are referenced by ARN), the invariant checked here is that
the ``valueFrom`` ARN is preserved, is a full ``arn:aws:secretsmanager:`` ARN,
and is not echoed into ``environment`` or ``command``.

All functions are pure and fixture-tested; the shell wrapper
``bin/sanitize-task-definition.sh`` handles CLI I/O and calls this module.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

_FULL_SECRET_ARN_RE = re.compile(r"^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+")
_DIGEST_REF_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass
class SanitizeResult:
    """Result of a sanitize + diff operation.

    ``task_definition`` is the sanitized copy (None on usage errors),
    ``issues`` is non-empty on any drift, and ``changed_fields`` is the exact
    list of JSON pointer paths that differ between the original and sanitized
    definitions.
    """

    task_definition: Any
    issues: list[dict[str, str]] = field(default_factory=list)
    changed_fields: list[str] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _digest_pinned(image: Any) -> bool:
    return isinstance(image, str) and bool(_DIGEST_REF_RE.search(image))


def _secret_base_arn(value_from: str) -> str:
    """Strip a ``:<json-key>::`` selector suffix to the base Secrets Manager ARN."""
    if value_from.endswith("::"):
        stripped = value_from.rstrip(":")
        head, sep, key = stripped.rpartition(":")
        if sep and key:
            return head
    return value_from


def _secret_arns(container: dict[str, Any]) -> list[str]:
    return [
        _secret_base_arn(s.get("valueFrom", ""))
        for s in container.get("secrets", [])
        if isinstance(s, dict) and isinstance(s.get("valueFrom"), str)
    ]


def _plaintext_contains_secret(text: Any, secret_arns: list[str]) -> bool:
    if not isinstance(text, str) or not secret_arns:
        return False
    return any(arn in text for arn in secret_arns)


def sanitize_task_definition(td: Any, images: dict[str, str]) -> SanitizeResult:
    """Return a deep copy of ``td`` with exactly the named containers re-imaged.

    ``images`` maps container name -> full digest reference
    (``<registry>/<repository>@sha256:<hex>``). A container name that is not in
    the definition, or a reference that is not digest-pinned, is an issue.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(td, dict):
        return SanitizeResult(
            None, [_issue("INVALID_TD", "$", "task definition must be an object")]
        )
    containers = td.get("containerDefinitions")
    if not isinstance(containers, list):
        return SanitizeResult(
            None,
            [
                _issue(
                    "INVALID_TD",
                    "containerDefinitions",
                    "task definition must have containerDefinitions",
                )
            ],
        )

    by_name = {c.get("name"): c for c in containers if isinstance(c, dict)}
    new_td = copy.deepcopy(td)
    for name, reference in images.items():
        if name not in by_name:
            issues.append(
                _issue(
                    "MISSING_CONTAINER",
                    f"containerDefinitions[].{name}",
                    f"no container named {name!r} in the task definition",
                )
            )
            continue
        if not _digest_pinned(reference):
            issues.append(
                _issue(
                    "NOT_DIGEST_PINNED",
                    f"containerDefinitions[].{name}.image",
                    f"replacement image for {name!r} must be a digest reference "
                    f"@sha256:<hex>, got {reference!r}",
                )
            )
            continue
        for container in new_td.get("containerDefinitions", []):
            if isinstance(container, dict) and container.get("name") == name:
                container["image"] = reference

    return SanitizeResult(new_td, issues)


def _containers_by_name(td: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    containers = td.get("containerDefinitions") if isinstance(td, dict) else None
    if isinstance(containers, list):
        for container in containers:
            if isinstance(container, dict) and container.get("name"):
                result[container["name"]] = container
    return result


def _walk_diff(original: Any, sanitized: Any, path: str, changes: list[str]) -> None:
    if isinstance(original, dict) and isinstance(sanitized, dict):
        for key in sorted(set(original) | set(sanitized)):
            if key not in original:
                changes.append(f"{path}/{key} (added)")
            elif key not in sanitized:
                changes.append(f"{path}/{key} (removed)")
            else:
                _walk_diff(original[key], sanitized[key], f"{path}/{key}", changes)
    elif isinstance(original, list) and isinstance(sanitized, list):
        if original != sanitized:
            for index in range(max(len(original), len(sanitized))):
                _walk_diff(
                    original[index] if index < len(original) else None,
                    sanitized[index] if index < len(sanitized) else None,
                    f"{path}/{index}",
                    changes,
                )
    elif original != sanitized:
        changes.append(f"{path}")


def diff_fields(original: Any, sanitized: Any) -> list[str]:
    """Return the JSON-pointer paths that differ between the two definitions."""
    changes: list[str] = []
    _walk_diff(original, sanitized, "$", changes)
    return changes


def sanitized_diff_issues(original: Any, sanitized: Any) -> list[dict[str, str]]:
    """Prove the sanitize transform only re-imaged the intended containers.

    Returns an empty list when: every re-imaged container is now digest-pinned,
    no field other than ``image`` changed anywhere, no container was removed,
    every ``secrets[].valueFrom`` is a full Secrets Manager ARN and is
    unchanged, and no secret ARN appears as plaintext in ``environment`` or
    ``command``.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(original, dict) or not isinstance(sanitized, dict):
        return [
            _issue("INVALID_TD", "$", "original and sanitized must both be task-definition objects")
        ]

    orig_containers = _containers_by_name(original)
    san_containers = _containers_by_name(sanitized)

    for name in orig_containers:
        if name not in san_containers:
            issues.append(
                _issue(
                    "CONTAINER_REMOVED",
                    f"containerDefinitions[].{name}",
                    f"container {name!r} was removed by sanitization",
                )
            )
            continue
        original_container = orig_containers[name]
        sanitized_container = san_containers[name]

        if not _digest_pinned(sanitized_container.get("image")):
            issues.append(
                _issue(
                    "FLOATING_IMAGE",
                    f"containerDefinitions[].{name}.image",
                    f"container {name!r} is not digest-pinned after sanitization",
                )
            )

        for key, value in original_container.items():
            if key == "image":
                continue
            if key not in sanitized_container:
                issues.append(
                    _issue(
                        "UNRELATED_DRIFT",
                        f"containerDefinitions[].{name}.{key}",
                        f"container {name!r} lost field {key!r}",
                    )
                )
            elif sanitized_container[key] != value:
                issues.append(
                    _issue(
                        "UNRELATED_DRIFT",
                        f"containerDefinitions[].{name}.{key}",
                        f"container {name!r} field {key!r} changed during sanitization; "
                        "only the image may change",
                    )
                )

        for secret in sanitized_container.get("secrets", []):
            if not isinstance(secret, dict):
                continue
            value_from = secret.get("valueFrom", "")
            if not _FULL_SECRET_ARN_RE.match(value_from):
                issues.append(
                    _issue(
                        "SECRET_SHORT_ARN",
                        f"containerDefinitions[].{name}.secrets[].valueFrom",
                        f"secret {secret.get('name')!r} must reference a full "
                        "arn:aws:secretsmanager: ARN",
                    )
                )

        secret_arns = _secret_arns(sanitized_container)
        for env in sanitized_container.get("environment", []):
            if isinstance(env, dict) and _plaintext_contains_secret(env.get("value"), secret_arns):
                issues.append(
                    _issue(
                        "SECRET_PLAINTEXT_IN_ENV",
                        f"containerDefinitions[].{name}.environment[]",
                        f"container {name!r} repeats a secret reference in environment plaintext",
                    )
                )
        command = " ".join(str(c) for c in sanitized_container.get("command", []))
        if _plaintext_contains_secret(command, secret_arns):
            issues.append(
                _issue(
                    "SECRET_IN_COMMAND",
                    f"containerDefinitions[].{name}.command",
                    f"container {name!r} repeats a secret reference in the command",
                )
            )

    for name in san_containers:
        if name not in orig_containers:
            issues.append(
                _issue(
                    "CONTAINER_ADDED",
                    f"containerDefinitions[].{name}",
                    f"container {name!r} was added by sanitization",
                )
            )

    return issues


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _parse_images(raw: list[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for entry in raw:
        name, sep, reference = entry.partition("=")
        if not sep or not name or not reference:
            raise ValueError(
                f"invalid --set-image (expected name=<registry>/<repo>@sha256:<hex>): {entry}"
            )
        images[name] = reference
    return images


def _cmd_sanitize(args: argparse.Namespace) -> int:
    original = _read_json(args.input)
    images = _parse_images(args.set_image)
    result = sanitize_task_definition(original, images)
    if result.issues:
        _print_json({"valid": False, "issues": result.issues})
        return 1
    assert result.task_definition is not None
    issues = sanitized_diff_issues(original, result.task_definition)
    _write_json(args.output, result.task_definition)
    _print_json(
        {
            "valid": not issues,
            "issues": issues,
            "changedFields": diff_fields(original, result.task_definition),
        }
    )
    return 0 if not issues else 1


def _cmd_assert(args: argparse.Namespace) -> int:
    original = _read_json(args.original)
    sanitized = _read_json(args.sanitized)
    issues = sanitized_diff_issues(original, sanitized)
    _print_json(
        {"valid": not issues, "issues": issues, "changedFields": diff_fields(original, sanitized)}
    )
    return 0 if not issues else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.sanitize",
        description="Digest-pin a task definition and prove the transform "
        "never drifts or leaks secrets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sanitize = sub.add_parser(
        "sanitize",
        help="re-image named containers to digest references and write the sanitized definition",
    )
    sanitize.add_argument(
        "--input", required=True, metavar="FILE", help="original task definition JSON file"
    )
    sanitize.add_argument(
        "--output", required=True, metavar="FILE", help="output sanitized task definition JSON file"
    )
    sanitize.add_argument(
        "--set-image",
        action="append",
        default=[],
        metavar="name=<repo>@sha256:<hex>",
        help="repeatable container image replacement",
    )
    sanitize.set_defaults(func=_cmd_sanitize)

    assert_cmd = sub.add_parser(
        "assert",
        help="prove a sanitized definition only changed images and kept secrets in valueFrom",
    )
    assert_cmd.add_argument(
        "--original", required=True, metavar="FILE", help="original task definition JSON file"
    )
    assert_cmd.add_argument(
        "--sanitized", required=True, metavar="FILE", help="sanitized task definition JSON file"
    )
    assert_cmd.set_defaults(func=_cmd_assert)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

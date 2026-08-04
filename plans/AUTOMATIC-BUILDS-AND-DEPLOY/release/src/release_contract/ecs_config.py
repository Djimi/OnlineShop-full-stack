"""Production ECS task-definition and service-config hardening rules
(Pass 3, subphase 3.5).

These validators are the offline, fixture-tested contract for what a
production release task definition and its ECS service must look like before
it may be registered/promoted:

- Task definition: Fargate ``awsvpc``, a valid Fargate CPU/memory pair, a
  digest-pinned image (never a floating tag), ``versionConsistency`` enabled,
  a container health check, ``awslogs`` log configuration, named Service
  Connect ``portMappings``, a positive ``stopTimeout`` (graceful
  termination), an execution role, and strict secret hygiene — every secret
  injected only via ``secrets[].valueFrom`` with a full Secrets Manager ARN
  (the ``:json-key::`` selector form requires the full ARN) and never
  repeated as plaintext in ``environment``/``command``.
- Service configuration: ECS rolling-update controller with the deployment
  circuit breaker enabled **and** rollback on, ``minimumHealthyPercent=100``,
  ``maximumPercent=200``, a capacity-provider strategy, and — when a task
  definition is supplied — Service Connect ``portName`` values that exist as
  named ``portMappings`` in the definition.

The rules are intentionally strict: the production release target is hardened
(Pass 3, subphase 3.5) and subphase 3.4 registers digest-pinned revisions by
copying the current definition and replacing only the image. These checks are
pure and fixture-tested; shell wrappers gather the JSON state and pass it here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# --- Fargate task-level CPU/memory matrix (Linux, AWS ECS docs) -------------
# Keys are CPU units; values are the allowed memory values in MiB. Memory may
# also be written as "3 GB"; CPU as "1 vCPU" — both are normalized.
FARGATE_CPU_MEMORY_MIB: dict[int, tuple[int, ...]] = {
    256: (512, 1024, 2048),
    512: (1024, 2048, 3072, 4096),
    1024: (2048, 3072, 4096, 5120, 6144, 7168, 8192),
    2048: tuple(range(4096, 16384 + 1, 1024)),
    4096: tuple(range(8192, 30720 + 1, 1024)),
    8192: tuple(range(16384, 61440 + 1, 4096)),
    16384: tuple(range(32768, 122880 + 1, 8192)),
    32768: (61440, 122880, 249856),
}

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_FULL_SECRET_ARN_RE = re.compile(r"^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+")
# Container environment/command entries that must never carry a credential
# (all credentials must be injected through secrets[].valueFrom).
CREDENTIAL_ENV_NAME_RE = re.compile(r"(PASSWORD|PASSWD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY)$")

# A port mapping used by Service Connect must carry a non-empty name so the
# service's serviceConnectConfiguration.portName can resolve to it.
SC_NAMED_PORT_REQUIRED = True


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class ValidationOutcome:
    """Result of validating one task definition or service configuration."""

    valid: bool
    issues: list[dict[str, str]] = field(default_factory=list)


def _as_int(value: Any) -> int | None:
    """Normalize a Fargate CPU/memory value ('1024', '2 GB', '1 vCPU') to int."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    lower = text.lower()
    try:
        if lower.endswith(" gb"):
            return int(float(lower[:-3].strip()) * 1024)
        if lower.endswith(" vcpu"):
            return int(float(lower[:-5].strip()) * 1024)
        return int(text)
    except ValueError:
        return None


def _valid_fargate_resources(cpu: Any, memory: Any) -> bool:
    cpu_int = _as_int(cpu)
    mem_int = _as_int(memory)
    if cpu_int is None or mem_int is None:
        return False
    allowed = FARGATE_CPU_MEMORY_MIB.get(cpu_int)
    return allowed is not None and mem_int in allowed


def _digest_pinned(image: Any) -> bool:
    return isinstance(image, str) and bool(_DIGEST_RE.search(image))


def _secret_ref(value_from: Any) -> bool:
    """A secret reference is valid only as a full Secrets Manager ARN.

    ECS treats a name without an ``arn:`` prefix as an SSM parameter. The
    ``:json-key::`` selector form additionally requires the FULL ARN (the name
    alone is rejected by ECS), so requiring ``arn:aws:secretsmanager:`` for
    every reference is the safe, uniform rule.
    """
    return isinstance(value_from, str) and bool(_FULL_SECRET_ARN_RE.match(value_from))


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


def validate_task_definition(td: Any) -> ValidationOutcome:
    """Validate a production release task definition (Pass 3, subphase 3.5)."""
    issues: list[dict[str, str]] = []
    if not isinstance(td, dict):
        return ValidationOutcome(
            False, [_issue("INVALID_TD", "$", "task definition must be an object")]
        )

    if td.get("networkMode") != "awsvpc":
        issues.append(
            _issue("NETWORK_MODE", "networkMode", "production tasks must use networkMode awsvpc")
        )

    compat = td.get("requiresCompatibilities")
    if not isinstance(compat, list) or "FARGATE" not in compat:
        issues.append(
            _issue(
                "NOT_FARGATE", "requiresCompatibilities", "production tasks must require FARGATE"
            )
        )

    if not _valid_fargate_resources(td.get("cpu"), td.get("memory")):
        issues.append(
            _issue(
                "INVALID_CPU_MEMORY",
                "cpu/memory",
                f"invalid Fargate CPU/memory pair: cpu={td.get('cpu')!r} "
                f"memory={td.get('memory')!r}",
            )
        )

    if not td.get("executionRoleArn"):
        issues.append(
            _issue(
                "MISSING_EXECUTION_ROLE",
                "executionRoleArn",
                "execution role is required for secret injection",
            )
        )
    # Execution-role and task-role duties must stay separate: the execution role
    # pulls images and reads Secrets Manager, the task role grants the
    # application's own permissions. A single role for both violates that
    # separation even though ECS would accept it.
    task_role = td.get("taskRoleArn")
    if isinstance(task_role, str) and task_role and task_role == td.get("executionRoleArn"):
        issues.append(
            _issue(
                "ROLE_NOT_DISTINCT",
                "taskRoleArn",
                "taskRoleArn must differ from executionRoleArn so execution-role and "
                "task-role duties stay separate",
            )
        )

    containers = td.get("containerDefinitions")
    if not isinstance(containers, list) or not containers:
        issues.append(
            _issue("EMPTY_CONTAINERS", "containerDefinitions", "at least one container is required")
        )
        return ValidationOutcome(not issues, issues)

    for index, container in enumerate(containers):
        if not isinstance(container, dict):
            issues.append(
                _issue(
                    "INVALID_CONTAINER",
                    f"containerDefinitions[{index}]",
                    "container must be an object",
                )
            )
            continue
        prefix = f"containerDefinitions[{index}]"
        name = container.get("name", "<unnamed>")

        if not _digest_pinned(container.get("image")):
            issues.append(
                _issue(
                    "FLOATING_IMAGE",
                    f"{prefix}.image",
                    f"container {name!r} image must be digest-pinned (@sha256:...), "
                    f"got {container.get('image')!r}",
                )
            )

        if container.get("versionConsistency") != "enabled":
            issues.append(
                _issue(
                    "VERSION_CONSISTENCY_DISABLED",
                    f"{prefix}.versionConsistency",
                    f"container {name!r} must set versionConsistency=enabled",
                )
            )

        health = container.get("healthCheck")
        if not isinstance(health, dict) or not health.get("command"):
            issues.append(
                _issue(
                    "MISSING_HEALTH_CHECK",
                    f"{prefix}.healthCheck",
                    f"container {name!r} requires a health check",
                )
            )

        stop_timeout = container.get("stopTimeout")
        if not isinstance(stop_timeout, int) or stop_timeout <= 0:
            issues.append(
                _issue(
                    "INVALID_STOP_TIMEOUT",
                    f"{prefix}.stopTimeout",
                    f"container {name!r} requires a positive stopTimeout for graceful termination",
                )
            )

        logs = container.get("logConfiguration") or {}
        log_options = logs.get("options") if isinstance(logs, dict) else None
        if (
            not isinstance(logs, dict)
            or logs.get("logDriver") != "awslogs"
            or not isinstance(log_options, dict)
            or not log_options.get("awslogs-group")
            or not log_options.get("awslogs-region")
        ):
            issues.append(
                _issue(
                    "MISSING_LOGS",
                    f"{prefix}.logConfiguration",
                    f"container {name!r} requires awslogs with awslogs-group and awslogs-region",
                )
            )

        for pm_index, port in enumerate(container.get("portMappings", [])):
            if SC_NAMED_PORT_REQUIRED and (not isinstance(port, dict) or not port.get("name")):
                issues.append(
                    _issue(
                        "UNNAMED_PORT",
                        f"{prefix}.portMappings[{pm_index}].name",
                        f"container {name!r} port mapping must have a name for Service Connect",
                    )
                )

        secret_refs = _secret_arns(container)
        for secret in container.get("secrets", []):
            if (
                not isinstance(secret, dict)
                or not secret.get("name")
                or not secret.get("valueFrom")
            ):
                issues.append(
                    _issue(
                        "SECRET_NOT_VALUE_FROM",
                        f"{prefix}.secrets",
                        f"container {name!r} secret must use valueFrom",
                    )
                )
                continue
            if not _secret_ref(secret.get("valueFrom")):
                issues.append(
                    _issue(
                        "SECRET_SHORT_ARN",
                        f"{prefix}.secrets[].valueFrom",
                        f"container {name!r} secret {secret.get('name')!r} must reference a full "
                        "arn:aws:secretsmanager:... ARN (JSON-key selectors require the full ARN)",
                    )
                )

        for env in container.get("environment", []):
            if not isinstance(env, dict):
                continue
            env_name = env.get("name", "")
            if CREDENTIAL_ENV_NAME_RE.search(env_name):
                issues.append(
                    _issue(
                        "SECRET_PLAINTEXT_IN_ENV",
                        f"{prefix}.environment[].{env_name}",
                        f"container {name!r} injects credential {env_name!r} via environment; "
                        "use secrets[].valueFrom instead",
                    )
                )
            if _plaintext_contains_secret(env.get("value"), secret_refs):
                issues.append(
                    _issue(
                        "SECRET_PLAINTEXT_IN_ENV",
                        f"{prefix}.environment[]",
                        f"container {name!r} repeats a secret reference in environment plaintext",
                    )
                )

        command = " ".join(str(c) for c in container.get("command", []))
        if _plaintext_contains_secret(command, secret_refs):
            issues.append(
                _issue(
                    "SECRET_IN_COMMAND",
                    f"{prefix}.command",
                    f"container {name!r} repeats a secret reference in the command",
                )
            )

    return ValidationOutcome(not issues, issues)


def _normalize_port_names(td: Any) -> set[str]:
    names: set[str] = set()
    containers = td.get("containerDefinitions") if isinstance(td, dict) else None
    if not isinstance(containers, list):
        return names
    for container in containers:
        if not isinstance(container, dict):
            continue
        for port in container.get("portMappings", []):
            if isinstance(port, dict) and port.get("name"):
                names.add(port["name"])
    return names


def validate_service_config(service: Any, td: Any | None = None) -> ValidationOutcome:
    """Validate a production ECS service configuration (circuit breaker,
    safe rolling parameters, capacity provider, Service Connect names)."""
    issues: list[dict[str, str]] = []
    if not isinstance(service, dict):
        return ValidationOutcome(
            False, [_issue("INVALID_SERVICE", "$", "service must be an object")]
        )

    controller = service.get("deploymentController") or {}
    if isinstance(controller, dict) and controller.get("type") not in (None, "ECS"):
        issues.append(
            _issue(
                "WRONG_DEPLOYMENT_CONTROLLER",
                "deploymentController.type",
                "production services must use the ECS (rolling) deployment controller",
            )
        )

    deploy_config = service.get("deploymentConfiguration") or {}
    if not isinstance(deploy_config, dict):
        issues.append(
            _issue(
                "MISSING_DEPLOYMENT_CONFIG",
                "deploymentConfiguration",
                "deploymentConfiguration is required",
            )
        )
        deploy_config = {}

    circuit = deploy_config.get("deploymentCircuitBreaker") or {}
    if not isinstance(circuit, dict):
        issues.append(
            _issue(
                "CIRCUIT_BREAKER_DISABLED",
                "deploymentConfiguration.deploymentCircuitBreaker",
                "deployment circuit breaker must be configured",
            )
        )
    else:
        if circuit.get("enable") is not True:
            issues.append(
                _issue(
                    "CIRCUIT_BREAKER_DISABLED",
                    "deploymentConfiguration.deploymentCircuitBreaker.enable",
                    "deployment circuit breaker must be enabled",
                )
            )
        if circuit.get("rollback") is not True:
            issues.append(
                _issue(
                    "ROLLBACK_DISABLED",
                    "deploymentConfiguration.deploymentCircuitBreaker.rollback",
                    "deployment circuit breaker rollback must be enabled",
                )
            )

    if deploy_config.get("minimumHealthyPercent") != 100:
        issues.append(
            _issue(
                "MIN_HEALTHY_PERCENT",
                "deploymentConfiguration.minimumHealthyPercent",
                "minimumHealthyPercent must be 100 (zero-downtime rolling)",
            )
        )
    if deploy_config.get("maximumPercent") != 200:
        issues.append(
            _issue(
                "MAX_PERCENT",
                "deploymentConfiguration.maximumPercent",
                "maximumPercent must be 200 for safe rolling with minimumHealthyPercent=100",
            )
        )

    strategy = service.get("capacityProviderStrategy")
    if not isinstance(strategy, list) or not strategy:
        issues.append(
            _issue(
                "MISSING_CAPACITY_PROVIDER",
                "capacityProviderStrategy",
                "a capacity provider strategy is required",
            )
        )

    sc = service.get("serviceConnectConfiguration") or {}
    if isinstance(sc, dict) and sc.get("enabled"):
        if not sc.get("namespace"):
            issues.append(
                _issue(
                    "SC_MISSING_NAMESPACE",
                    "serviceConnectConfiguration.namespace",
                    "Service Connect requires a namespace",
                )
            )
        if td is not None:
            port_names = _normalize_port_names(td)
            for entry in sc.get("services", []):
                if not isinstance(entry, dict):
                    continue
                port_name = entry.get("portName")
                if port_name and port_name not in port_names:
                    issues.append(
                        _issue(
                            "SC_PORT_NOT_IN_TD",
                            "serviceConnectConfiguration.services[].portName",
                            f"Service Connect portName {port_name!r} has no matching "
                            "named portMapping in the task definition",
                        )
                    )

    return ValidationOutcome(not issues, issues)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_validate_td(args: argparse.Namespace) -> int:
    outcome = validate_task_definition(_read_json(args.input))
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def _cmd_validate_service(args: argparse.Namespace) -> int:
    service = _read_json(args.input)
    td = _read_json(args.task_definition) if args.task_definition else None
    outcome = validate_service_config(service, td)
    _print_json({"valid": outcome.valid, "issues": outcome.issues})
    return 0 if outcome.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.ecs_config",
        description="Production ECS task-definition and service-configuration "
        "hardening validation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    td = sub.add_parser("validate-td", help="validate a task definition JSON file")
    td.add_argument("--input", required=True, metavar="FILE", help="task definition JSON file")
    td.set_defaults(func=_cmd_validate_td)

    service = sub.add_parser(
        "validate-service", help="validate an ECS service configuration JSON file"
    )
    service.add_argument(
        "--input", required=True, metavar="FILE", help="service configuration JSON file"
    )
    service.add_argument(
        "--task-definition",
        metavar="FILE",
        help="optional task definition JSON file to cross-check Service Connect portName values",
    )
    service.set_defaults(func=_cmd_validate_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

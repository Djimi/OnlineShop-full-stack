#!/usr/bin/env python3
"""Authorize a manual candidate request and create its non-success check.

Only trusted main workflows may call this command. GitHub CLI receives its job
token through GH_TOKEN; API errors are never echoed. CI evidence is bounded inert
data from an unchanged trusted workflow, not a PR's provenance claim. No AWS
credentials or calls are needed. A request failure cannot publish success.
"""

import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

REPOSITORY = "Djimi/OnlineShop-full-stack"
API_ROOT = f"repos/{REPOSITORY}"
CI_WORKFLOW = ".github/workflows/ci.yml"
CHECK_NAME = "AWS validation"
CONTROLLER_WORKFLOWS = ("aws-validation.yml", "aws-check-proof.yml")
SHA = re.compile(r"[0-9a-f]{40}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
MAX_EVIDENCE_BYTES = 65_536
REQUIRED_CI_JOBS = {"Java (Auth)", "Java (API Gateway)", "Java (common -> Items)",
                    "Frontend", "Images and PR E2E", "Candidate identity"}


class RequestRejected(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="Freeze CI-verified PR identity; create a pending check")
    request.add_argument("--pr", type=positive_integer, required=True)
    request.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        context = trusted_dispatch()
        authorize_actors(context)
        identities = resolve_candidate(args.pr)
        ci = verify_source_ci(identities, context)
        reject_duplicate_attempt(identities, context)
        if resolve_candidate(args.pr) != identities:
            raise RequestRejected("PR or main changed during resolution; retry manually")
        record = {**identities, **ci, **context}
        record["check_run_id"] = create_pending_check(record)
        write_request(args.output, record)
        print(json.dumps(record, sort_keys=True))
        return 0
    except RequestRejected as error:
        print(f"Request rejected: {error}; correct prerequisites and retry manually", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, OSError, argparse.ArgumentTypeError):
        # External values and exception bodies can contain tokens. Only fixed
        # rejection diagnostics reach logs; detailed secrets never do.
        print("Request rejected: authorization, candidate or exact CI evidence is invalid; retry manually after correcting prerequisites", file=sys.stderr)
        return 1


def positive_integer(value):
    if not re.fullmatch(r"[1-9][0-9]{0,18}", str(value)):
        raise argparse.ArgumentTypeError("expected a positive decimal integer")
    return int(value)


def require_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise RequestRejected("invalid revision")
    return value


def trusted_dispatch():
    env = os.environ
    workflows = {f"{REPOSITORY}/.github/workflows/{name}@refs/heads/main" for name in CONTROLLER_WORKFLOWS}
    if (env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_WORKFLOW_REF") not in workflows
            or not env.get("GH_TOKEN")):
        raise RequestRejected("untrusted dispatch context")
    controller = require_sha(env.get("GITHUB_SHA"))
    if env.get("GITHUB_WORKFLOW_SHA") != controller:
        raise RequestRejected("workflow and controller differ")
    actor, triggering = env.get("GITHUB_ACTOR", ""), env.get("GITHUB_TRIGGERING_ACTOR", "")
    if not LOGIN.fullmatch(actor) or not LOGIN.fullmatch(triggering):
        raise RequestRejected("invalid actor")
    return {
        "controller_sha": controller, "actor": actor, "triggering_actor": triggering,
        "validation_run_id": positive_integer(env.get("GITHUB_RUN_ID", "")),
        "validation_run_attempt": positive_integer(env.get("GITHUB_RUN_ATTEMPT", "")),
    }


def github(path, *, method="GET", body=None, binary=False):
    command = ["gh", "api", f"{API_ROOT}/{path}", "--method", method]
    if body is not None:
        command += ["--input", "-"]
    try:
        result = subprocess.run(command, input=json.dumps(body).encode() if body is not None else None,
                                capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise RequestRejected("GitHub API unavailable") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise RequestRejected("GitHub API failed")
    return result.stdout if binary else json.loads(result.stdout, object_pairs_hook=unique_fields)


def github_list(path, key):
    separator = "&" if "?" in path else "?"
    for page in range(1, 11):
        values = github(f"{path}{separator}per_page=100&page={page}")[key]
        if not isinstance(values, list):
            raise RequestRejected("invalid API list")
        yield from values
        if len(values) < 100:
            return
    raise RequestRejected("API listing exceeds bound")


def authorize_actors(context):
    for actor in sorted({context["actor"], context["triggering_actor"]}):
        if github(f"collaborators/{actor}/permission").get("permission") != "admin":
            raise RequestRejected("actor requires repository admin permission")


def resolve_candidate(number):
    pr = github(f"pulls/{number}")
    if (pr.get("number") != number or pr.get("state") != "open" or pr.get("draft") is not False
            or pr.get("mergeable") is not True or pr["user"]["login"].lower() == "dependabot[bot]"
            or pr["head"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["repo"]["full_name"] != REPOSITORY or pr["base"]["ref"] != "main"):
        raise RequestRejected("unsupported PR")
    head, base = require_sha(pr["head"]["sha"]), require_sha(pr["base"]["sha"])
    candidate = require_sha(github(f"git/ref/pull/{number}/merge")["object"]["sha"])
    if (candidate != require_sha(pr["merge_commit_sha"])
            or base != require_sha(github("git/ref/heads/main")["object"]["sha"])):
        raise RequestRejected("ambiguous merge candidate")
    return {"repository": REPOSITORY, "pr": number, "head_sha": head,
            "base_sha": base, "candidate_sha": candidate}


def verify_source_ci(identities, context):
    """Run metadata reports PR head, so a separate trusted artifact binds merge SHA."""
    head = identities["head_sha"]
    runs = list(github_list(f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}", "workflow_runs"))
    matching = [run for run in runs if run.get("event") == "pull_request" and run.get("head_sha") == head
                and run.get("path") == CI_WORKFLOW and run.get("repository", {}).get("full_name") == REPOSITORY]
    if not matching:
        raise RequestRejected("missing source CI")
    # The newest run must pass; never fall back to an older successful rerun.
    run = max(matching, key=lambda value: positive_integer(value["id"]))
    run_id, attempt = positive_integer(run["id"]), positive_integer(run["run_attempt"])
    current = github(f"actions/runs/{run_id}/attempts/{attempt}")
    identity_fields = ("id", "run_attempt", "head_sha", "event", "path", "repository", "status", "conclusion")
    if (any(current.get(field) != run.get(field) for field in identity_fields)
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise RequestRejected("exact CI must complete successfully; retry manually")
    jobs = list(github_list(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"))
    for name in REQUIRED_CI_JOBS:
        named_jobs = [job for job in jobs if job.get("name") == name]
        if (len(named_jobs) != 1 or named_jobs[0].get("status") != "completed"
                or named_jobs[0].get("conclusion") != "success"):
            raise RequestRejected("required CI job is missing, skipped or unsuccessful")
    trusted = github(f"contents/{CI_WORKFLOW}?ref={context['controller_sha']}")
    producer = github(f"contents/{CI_WORKFLOW}?ref={identities['candidate_sha']}")
    if (trusted.get("encoding") != "base64" or producer.get("encoding") != "base64"
            or base64.b64decode(trusted["content"]) != base64.b64decode(producer["content"])):
        raise RequestRejected("candidate edits trusted CI evidence producer")
    artifacts = list(github_list(f"actions/runs/{run_id}/artifacts", "artifacts"))
    matching_artifacts = [artifact for artifact in artifacts if artifact.get("name") == f"ci-candidate-{run_id}-{attempt}"]
    if len(matching_artifacts) != 1:
        raise RequestRejected("missing or ambiguous CI candidate evidence")
    artifact = matching_artifacts[0]
    if (artifact.get("expired") is not False or not 0 < artifact["size_in_bytes"] <= MAX_EVIDENCE_BYTES
            or artifact["workflow_run"]["id"] != run_id or artifact["workflow_run"]["head_sha"] != head):
        raise RequestRejected("invalid source artifact")
    artifact_id = positive_integer(artifact["id"])
    evidence = read_candidate_evidence(github(f"actions/artifacts/{artifact_id}/zip", binary=True))
    expected = {**identities, "ci_run_id": run_id, "ci_run_attempt": attempt, "event": "pull_request"}
    if evidence != expected:
        raise RequestRejected("source CI tested a different candidate")
    return {"ci_run_id": run_id, "ci_run_attempt": attempt, "ci_artifact_id": artifact_id}


def read_candidate_evidence(data):
    if len(data) > MAX_EVIDENCE_BYTES:
        raise RequestRejected("oversized source evidence")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != "candidate.json"
                    or members[0].file_size > MAX_EVIDENCE_BYTES
                    or (members[0].external_attr >> 16) & 0o170000 == 0o120000):
                raise RequestRejected("unsafe source evidence")
            return json.loads(archive.read(members[0]), object_pairs_hook=unique_fields)
    except (zipfile.BadZipFile, RuntimeError):
        raise RequestRejected("corrupt source evidence") from None


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestRejected("duplicate JSON field")
        result[key] = value
    return result


def reject_duplicate_attempt(identities, context):
    checks = github_list(f"commits/{identities['candidate_sha']}/check-runs?check_name=AWS%20validation&filter=all", "check_runs")
    external_id = attempt_identity(context)
    if any(check.get("external_id") == external_id and check.get("app", {}).get("slug") == "github-actions" for check in checks):
        raise RequestRejected("duplicate attempt")


def attempt_identity(record):
    return f"aws-validation:{record['validation_run_id']}:{record['validation_run_attempt']}"


def create_pending_check(record):
    check = github("check-runs", method="POST", body={
        "name": CHECK_NAME, "head_sha": record["candidate_sha"], "status": "in_progress",
        "external_id": attempt_identity(record),
        "details_url": f"https://github.com/{REPOSITORY}/actions/runs/{record['validation_run_id']}",
        "output": {"title": "Manually requested AWS validation pending",
                   "summary": f"PR #{record['pr']}; candidate `{record['candidate_sha']}`; actor `{record['triggering_actor']}`. No deployment or tests completed."},
    })
    if check.get("head_sha") != record["candidate_sha"]:
        raise RequestRejected("check candidate association mismatch")
    return positive_integer(check["id"])


def write_request(output, record):
    # Exclusive creation avoids replacing another attempt's record or following a symlink.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    sys.exit(main())

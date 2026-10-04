"""Request stories at the GitHub CLI process boundary; no AWS access."""

import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/aws-validation.py"
REPO = "Djimi/OnlineShop-full-stack"
HEAD, BASE, CANDIDATE, CONTROLLER = (letter * 40 for letter in "abcd")
PREFIX = f"repos/{REPO}"


class RequestStories(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text('''#!/usr/bin/env python3
import base64,json,os,pathlib,sys
root=pathlib.Path(os.environ['FAKE_API'])
args=sys.argv[1:]
method=args[args.index('--method')+1]
path=args[1]
body=json.load(sys.stdin) if '--input' in args else None
with (root/'calls.jsonl').open('a') as f:
 f.write(json.dumps({'method':method,'path':path,'body':body})+'\\n')
responses=json.loads((root/'routes.json').read_text())
key=method+' '+path
value=responses.get(key)
if value is None:
 print('unexpected API route',file=sys.stderr); sys.exit(1)
if isinstance(value,dict) and '__sequence__' in value:
 sequence=value['__sequence__']; value=sequence.pop(0)
 (root/'routes.json').write_text(json.dumps(responses))
if isinstance(value,dict) and '__error__' in value:
 print(os.environ['GH_TOKEN'],file=sys.stderr); sys.exit(1)
if isinstance(value,dict) and '__bytes__' in value:
 sys.stdout.buffer.write(base64.b64decode(value['__bytes__']))
else: print(json.dumps(value))
''')
        gh.chmod(0o755)
        aws = self.bin / "aws"
        aws.write_text("#!/bin/sh\necho invoked > \"$FAKE_API/aws-called\"\nexit 99\n")
        aws.chmod(0o755)
        self.env = {
            **os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_API": str(self.directory), "GH_TOKEN": "secret-token-canary",
            "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": CONTROLLER,
            "GITHUB_WORKFLOW_SHA": CONTROLLER,
            "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/aws-validation.yml@refs/heads/main",
            "GITHUB_ACTOR": "owner", "GITHUB_TRIGGERING_ACTOR": "owner",
            "GITHUB_RUN_ID": "200", "GITHUB_RUN_ATTEMPT": "1",
        }
        self.pr = {
            "number": 42, "state": "open", "draft": False, "mergeable": True,
            "user": {"login": "owner"}, "merge_commit_sha": CANDIDATE,
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"sha": BASE, "ref": "main", "repo": {"full_name": REPO}},
        }
        self.run = {
            "id": 100, "run_attempt": 2, "head_sha": HEAD,
            "event": "pull_request", "status": "completed", "conclusion": "success",
            "path": ".github/workflows/ci.yml", "repository": {"full_name": REPO},
        }
        self.record = {
            "repository": REPO, "pr": 42, "head_sha": HEAD, "base_sha": BASE,
            "candidate_sha": CANDIDATE, "ci_run_id": 100, "ci_run_attempt": 2,
            "event": "pull_request",
        }
        self.archive = None
        content = {"encoding": "base64", "content": base64.b64encode(b"trusted CI").decode()}
        self.routes = {
            f"GET {PREFIX}/collaborators/owner/permission": {"permission": "admin"},
            f"GET {PREFIX}/pulls/42": self.pr,
            f"GET {PREFIX}/git/ref/pull/42/merge": {"object": {"sha": CANDIDATE}},
            f"GET {PREFIX}/git/ref/heads/main": {"object": {"sha": BASE}},
            f"GET {PREFIX}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={HEAD}&per_page=100&page=1": {"workflow_runs": [self.run]},
            f"GET {PREFIX}/actions/runs/100/attempts/2": self.run,
            f"GET {PREFIX}/actions/runs/100/attempts/2/jobs?per_page=100&page=1": {
                "jobs": [{"name": name, "status": "completed", "conclusion": "success"}
                         for name in ["Java (Auth)", "Java (API Gateway)", "Java (common -> Items)",
                                      "Frontend", "Images and PR E2E", "Candidate identity"]]},
            f"GET {PREFIX}/contents/.github/workflows/ci.yml?ref={CONTROLLER}": content,
            f"GET {PREFIX}/contents/.github/workflows/ci.yml?ref={CANDIDATE}": content,
            f"GET {PREFIX}/actions/runs/100/artifacts?per_page=100&page=1": {
                "artifacts": [{"id": 300, "name": "ci-candidate-100-2", "expired": False,
                               "size_in_bytes": 1000, "workflow_run": {"id": 100, "head_sha": HEAD}}]},
            f"GET {PREFIX}/commits/{CANDIDATE}/check-runs?check_name=AWS%20validation&filter=all&per_page=100&page=1": {"total_count": 0, "check_runs": []},
            f"POST {PREFIX}/check-runs": {"id": 400, "head_sha": CANDIDATE},
        }

    def invoke(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("candidate.json", json.dumps(self.record))
        self.routes[f"GET {PREFIX}/actions/artifacts/300/zip"] = {
            "__bytes__": base64.b64encode(self.archive if self.archive is not None else archive.getvalue()).decode()}
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "request", "--pr", "42", "--output", str(self.directory / "request.json")],
            env=self.env, capture_output=True, text=True, timeout=20)
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        if result.returncode:
            self.assertIn("Request rejected:", result.stderr)
        self.assertFalse((self.directory / "aws-called").exists())
        calls_file = self.directory / "calls.jsonl"
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return result, calls

    def test_accepts_exact_ci_candidate_and_creates_pending_attempt_check(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.directory / "request.json").read_text())
        self.assertEqual(record["candidate_sha"], CANDIDATE)
        self.assertEqual(record["ci_run_attempt"], 2)
        self.assertEqual(record["check_run_id"], 400)
        posted = [call["body"] for call in calls if call["method"] == "POST"]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["head_sha"], CANDIDATE)
        self.assertEqual(posted[0]["status"], "in_progress")
        self.assertEqual(posted[0]["external_id"], "aws-validation:200:1")
        self.assertNotIn("conclusion", posted[0])

    def test_rejects_unsupported_prs_before_creating_check(self):
        cases = [
            ("state", "closed"), ("mergeable", False), ("mergeable", None),
            ("draft", True), ("user", {"login": "dependabot[bot]"}),
            ("head", {"sha": HEAD, "repo": {"full_name": "fork/shop"}}),
            ("base", {"sha": BASE, "ref": "release", "repo": {"full_name": REPO}}),
        ]
        original = copy.deepcopy(self.pr)
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.routes[f"GET {PREFIX}/pulls/42"] = {**original, key: value}
                result, calls = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_rejects_untrusted_dispatch_context(self):
        for key, value in [("GITHUB_REF", "refs/heads/feature"),
                           ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_REPOSITORY", "fork/shop"),
                           ("GITHUB_WORKFLOW_SHA", HEAD)]:
            with self.subTest(key=key):
                original = self.env[key]
                self.env[key] = value
                result, calls = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(calls)
                self.env[key] = original

    def test_rerun_authorizes_actual_triggering_actor(self):
        self.env["GITHUB_TRIGGERING_ACTOR"] = "reader"
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        self.routes[f"GET {PREFIX}/collaborators/reader/permission"] = {"permission": "read"}
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_authorized_rerun_actor_cannot_bypass_original_actor_permission(self):
        self.env["GITHUB_ACTOR"] = "reader"
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        self.routes[f"GET {PREFIX}/collaborators/reader/permission"] = {"permission": "read"}
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_ci_head_is_not_accepted_as_candidate_evidence(self):
        self.record["candidate_sha"] = HEAD
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_push_wrong_revision_and_pending_ci_stop_without_waiting(self):
        for key, value in [("event", "push"), ("head_sha", BASE),
                           ("status", "in_progress"), ("conclusion", "failure")]:
            with self.subTest(key=key):
                original = self.run[key]
                self.run[key] = value
                result, calls = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call["method"] == "POST" for call in calls))
                self.run[key] = original

    def test_changed_main_during_resolution_stops_before_check_creation(self):
        changed = copy.deepcopy(self.pr)
        changed["base"]["sha"] = "e" * 40
        self.routes[f"GET {PREFIX}/pulls/42"] = {"__sequence__": [self.pr, changed]}
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_pr_edited_ci_provenance_is_not_trusted(self):
        self.routes[f"GET {PREFIX}/contents/.github/workflows/ci.yml?ref={CANDIDATE}"] = {
            "encoding": "base64", "content": base64.b64encode(b"untrusted producer").decode()}
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_duplicate_attempt_does_not_create_another_pending_check(self):
        self.routes[f"GET {PREFIX}/commits/{CANDIDATE}/check-runs?check_name=AWS%20validation&filter=all&per_page=100&page=1"] = {
            "total_count": 1, "check_runs": [{"id": 399, "external_id": "aws-validation:200:1",
                                           "app": {"slug": "github-actions"}}]}
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_api_failure_does_not_echo_token_or_create_record(self):
        self.routes[f"GET {PREFIX}/pulls/42"] = {"__error__": True}
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "request.json").exists())

    def test_attempt_endpoint_extra_metadata_does_not_change_identity(self):
        self.routes[f"GET {PREFIX}/actions/runs/100/attempts/2"] = {
            **self.run, "extra_api_metadata": "not an identity field"}
        result, _ = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_skipped_required_ci_job_cannot_satisfy_prerequisite(self):
        jobs = self.routes[f"GET {PREFIX}/actions/runs/100/attempts/2/jobs?per_page=100&page=1"]["jobs"]
        jobs[4]["conclusion"] = "skipped"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_ci_record_archive_rejects_traversal_links_and_excessive_content(self):
        for name, mode, data in [("../candidate.json", 0o100644, json.dumps(self.record)),
                                 ("candidate.json", 0o120777, json.dumps(self.record)),
                                 ("candidate.json", 0o100644, " " * 65_537),
                                 ("candidate.json", 0o100644, json.dumps(self.record)[:-1] + ',"pr":42}')]:
            with self.subTest(name=name, mode=mode):
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode << 16
                    zipped.writestr(info, data)
                self.archive = archive.getvalue()
                result, calls = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_expired_or_wrong_attempt_artifact_is_rejected(self):
        artifact = self.routes[f"GET {PREFIX}/actions/runs/100/artifacts?per_page=100&page=1"]["artifacts"][0]
        for key, value in [("expired", True), ("name", "ci-candidate-100-1")]:
            with self.subTest(key=key):
                original = artifact[key]
                artifact[key] = value
                result, calls = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call["method"] == "POST" for call in calls))
                artifact[key] = original


if __name__ == "__main__":
    unittest.main()

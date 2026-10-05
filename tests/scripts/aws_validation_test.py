"""Request stories at the GitHub CLI process boundary; no AWS access."""

import base64
import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

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
            f"GET {PREFIX}/check-runs/400": {"id": 400, "head_sha": CANDIDATE,
                "name": "AWS validation", "status": "in_progress", "external_id": "aws-validation:200:1",
                "app": {"slug": "github-actions"}},
            f"GET {PREFIX}/commits/{CANDIDATE}/check-runs?check_name=AWS%20validation&filter=latest&per_page=100&page=1": {
                "check_runs": [{"id": 400, "app": {"slug": "github-actions"}}]},
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
            env=self.env, capture_output=True, text=True, timeout=20, check=False)
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

    def packaging_fixture(self):
        import hashlib
        request = {"repository": REPO, "pr": 42, "head_sha": HEAD, "base_sha": BASE,
                   "candidate_sha": CANDIDATE, "controller_sha": CONTROLLER,
                   "ci_run_id": 100, "ci_run_attempt": 2, "ci_artifact_id": 300,
                   "validation_run_id": 200, "validation_run_attempt": 1,
                   "actor": "owner", "triggering_actor": "owner", "check_run_id": 400}
        (self.directory / "request.json").write_text(json.dumps(request))
        artifacts = self.directory / "artifacts"
        artifacts.mkdir()
        images = {}
        for name in ["auth", "items", "gateway", "frontend", "e2e"]:
            path = artifacts / f"{name}.tar"
            content = json.dumps([{"Config": "config.json", "RepoTags": [f"onlineshop-test-{name}:run-200-attempt-1"], "Layers": []}]).encode()
            with tarfile.open(path, "w") as archive:
                member = tarfile.TarInfo("manifest.json"); member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            images[name] = {"file": path.name, "size": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        path = artifacts / "fixtures.tar"
        with tarfile.open(path, "w") as archive:
            for name in ["Auth/init-db/01-schema.sql", "Items/init-db/01-schema.sql"]:
                content = b"CREATE TABLE test (id integer);"
                member = tarfile.TarInfo(name); member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        manifest = {"repository": REPO, "candidate_sha": CANDIDATE, "controller_sha": CONTROLLER,
                    "ci_run_id": 100, "ci_run_attempt": 2, "build_run_id": 200, "build_run_attempt": 1,
                    "images": images, "fixtures": {"file": "fixtures.tar", "size": path.stat().st_size,
                                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
        return artifacts, manifest

    def invoke_packaging(self, artifacts, manifest):
        (self.directory / "build.json").write_text(json.dumps(manifest))
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run([sys.executable, str(SCRIPT), "verify-build", "--request", str(self.directory / "request.json"),
                                 "--manifest", str(self.directory / "build.json"), "--artifacts", str(artifacts),
                                 "--output", str(self.directory / "verified.json")], env=self.env,
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        self.assertFalse((self.directory / "aws-called").exists())
        if result.returncode:
            self.assertIn("Request rejected:", result.stderr)
        return result

    def test_exact_candidate_archives_verify_without_loading_or_executing_images(self):
        artifacts, manifest = self.packaging_fixture()
        result = self.invoke_packaging(artifacts, manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.directory / "verified.json").read_text())
        self.assertEqual(set(record["images"]), {"auth", "items", "gateway", "frontend", "e2e"})

    def test_mismatched_candidate_or_build_attempt_is_rejected(self):
        artifacts, manifest = self.packaging_fixture()
        for key, value in [("candidate_sha", HEAD), ("build_run_attempt", 2), ("ci_run_id", 101)]:
            with self.subTest(key=key):
                result = self.invoke_packaging(artifacts, {**manifest, key: value})
                self.assertNotEqual(result.returncode, 0)

    def test_changed_candidate_is_rejected_before_archive_acceptance(self):
        artifacts, manifest = self.packaging_fixture()
        self.pr["head"]["sha"] = "e" * 40
        result = self.invoke_packaging(artifacts, manifest)
        self.assertNotEqual(result.returncode, 0)

    def test_checksum_and_unexpected_image_name_are_rejected(self):
        artifacts, manifest = self.packaging_fixture()
        invalid = copy.deepcopy(manifest); invalid["images"]["auth"]["sha256"] = "0" * 64
        self.assertNotEqual(self.invoke_packaging(artifacts, invalid).returncode, 0)
        invalid = copy.deepcopy(manifest); invalid["images"]["rogue"] = invalid["images"].pop("auth")
        self.assertNotEqual(self.invoke_packaging(artifacts, invalid).returncode, 0)

    def test_fixture_traversal_and_links_are_rejected_even_with_matching_checksum(self):
        import hashlib
        artifacts, manifest = self.packaging_fixture()
        for name, kind in [("../../escape.sql", tarfile.REGTYPE), ("Auth/init-db/01-schema.sql", tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                path = artifacts / "fixtures.tar"
                with tarfile.open(path, "w") as archive:
                    member = tarfile.TarInfo(name); member.type = kind; member.linkname = "/etc/passwd"
                    archive.addfile(member)
                manifest["fixtures"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertNotEqual(self.invoke_packaging(artifacts, manifest).returncode, 0)

    def test_newer_pending_attempt_blocks_acceptance_of_older_build(self):
        artifacts, manifest = self.packaging_fixture()
        self.routes[f"GET {PREFIX}/commits/{CANDIDATE}/check-runs?check_name=AWS%20validation&filter=latest&per_page=100&page=1"] = {
            "check_runs": [{"id": 401, "name": "AWS validation", "head_sha": CANDIDATE,
                            "external_id": "aws-validation:201:1", "status": "in_progress",
                            "app": {"slug": "github-actions"}}]}
        result = self.invoke_packaging(artifacts, manifest)
        self.assertNotEqual(result.returncode, 0)

    def test_changed_image_tag_is_rejected_even_with_matching_checksum(self):
        import hashlib
        artifacts, manifest = self.packaging_fixture()
        path = artifacts / "auth.tar"
        content = json.dumps([{"Config": "config.json", "RepoTags": ["onlineshop-test-auth:latest"], "Layers": []}]).encode()
        with tarfile.open(path, "w") as archive:
            member = tarfile.TarInfo("manifest.json"); member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        manifest["images"]["auth"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertNotEqual(self.invoke_packaging(artifacts, manifest).returncode, 0)

    def test_oversized_archive_is_rejected_without_reading_payload(self):
        artifacts, manifest = self.packaging_fixture()
        manifest["images"]["auth"]["size"] = 4 * 1024**3 + 1
        self.assertNotEqual(self.invoke_packaging(artifacts, manifest).returncode, 0)

    def invoke_failure(self):
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run([sys.executable, str(SCRIPT), "finalize-failure", "--request",
                                 str(self.directory / "request.json")], env=self.env,
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        self.assertFalse((self.directory / "aws-called").exists())
        if result.returncode:
            self.assertIn("Request rejected:", result.stderr)
        calls = [json.loads(line) for line in (self.directory / "calls.jsonl").read_text().splitlines()]
        return result, [call for call in calls if call["method"] == "PATCH"]

    def test_failure_finalizes_only_owned_check_even_after_candidate_changes(self):
        self.packaging_fixture()
        self.pr["state"] = "closed"
        self.routes[f"PATCH {PREFIX}/check-runs/400"] = {"id": 400, "status": "completed", "conclusion": "failure"}
        result, updates = self.invoke_failure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["path"], f"{PREFIX}/check-runs/400")
        self.assertEqual(updates[0]["body"]["conclusion"], "failure")

    def test_failure_cannot_modify_another_attempts_check(self):
        self.packaging_fixture()
        self.routes[f"GET {PREFIX}/check-runs/400"]["external_id"] = "aws-validation:201:1"
        result, updates = self.invoke_failure()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updates, [])

    def test_completed_check_is_preserved_by_idempotent_failure_finalizer(self):
        self.packaging_fixture()
        self.routes[f"GET {PREFIX}/check-runs/400"].update(status="completed", conclusion="success")
        result, updates = self.invoke_failure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(updates, [])

    def build_fixture(self):
        self.packaging_fixture()
        candidate = self.directory / "candidate"
        candidate.mkdir()
        for service in ["Auth", "Items", "api-gateway", "frontend", "e2e-tests"]:
            (candidate / service).mkdir()
            (candidate / service / "Dockerfile").write_text("FROM scratch\n")
        for service in ["Auth", "Items"]:
            (candidate / service / "init-db").mkdir()
            (candidate / service / "init-db" / "01-schema.sql").write_text("CREATE TABLE test (id integer);")
        (candidate / "Auth/init-db/02-seed-data.sql").write_text("committed seed must never be packaged")
        git = self.bin / "git"
        git.write_text(f'''#!/usr/bin/env python3
import os,sys
if sys.argv[-1]=='HEAD': print(os.environ.get('BUILD_HEAD','{CANDIDATE}'))
elif 'status' in sys.argv: print(os.environ.get('BUILD_DIRTY',''),end='')
else: sys.exit(1)
''')
        git.chmod(0o755)
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import io,json,os,pathlib,sys,tarfile
root=pathlib.Path(os.environ['FAKE_API']);args=sys.argv[1:]
with (root/'docker-calls.jsonl').open('a') as f:
 f.write(json.dumps({'args':args,'credentials_present':any(k.startswith('AWS_') or k in ['GH_TOKEN','GITHUB_TOKEN'] for k in os.environ)})+'\\n')
if args[0]=='build': sys.exit(int(os.environ.get('BUILD_FAIL','0')))
if args[0]=='save':
 path=pathlib.Path(args[args.index('--output')+1]);tag=args[-1]
 content=json.dumps([{'Config':'config.json','RepoTags':[tag],'Layers':[]}]).encode()
 with tarfile.open(path,'w') as archive:
  member=tarfile.TarInfo('manifest.json');member.size=len(content);archive.addfile(member,io.BytesIO(content))
else: sys.exit(1)
''')
        docker.chmod(0o755)
        return candidate

    def invoke_build(self, candidate):
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run([sys.executable, str(SCRIPT), "build", "--request",
                                 str(self.directory / "request.json"), "--candidate", str(candidate),
                                 "--artifacts", str(self.directory / "build-artifacts"), "--output",
                                 str(self.directory / "build.json")], env=self.env,
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        self.assertFalse((self.directory / "aws-called").exists())
        if result.returncode:
            self.assertIn("Request rejected:", result.stderr)
        calls_file = self.directory / "docker-calls.jsonl"
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return result, calls

    def test_build_packages_five_exact_images_without_credentials_and_separate_fixtures(self):
        candidate = self.build_fixture()
        result, calls = self.invoke_build(candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        builds = [call for call in calls if call['args'][0] == 'build']
        self.assertEqual(len(builds), 5)
        self.assertFalse(any(call['credentials_present'] for call in calls))
        contexts = {call['args'][call['args'].index('--tag')+1].split(':')[0]: call['args'][-1] for call in builds}
        self.assertEqual(contexts['onlineshop-test-items'], str(candidate))
        self.assertEqual(contexts['onlineshop-test-auth'], str(candidate / 'Auth'))
        manifest = json.loads((self.directory / "build.json").read_text())
        self.assertEqual(manifest['candidate_sha'], CANDIDATE)
        self.assertEqual(set(manifest['images']), {'auth','items','gateway','frontend','e2e'})
        with tarfile.open(self.directory / 'build-artifacts/fixtures.tar') as archive:
            self.assertEqual(set(archive.getnames()), {'Auth/init-db/01-schema.sql','Items/init-db/01-schema.sql'})

    def test_wrong_or_dirty_checkout_never_starts_a_candidate_build(self):
        candidate = self.build_fixture()
        for key, value in [('BUILD_HEAD',HEAD), ('BUILD_DIRTY',' M Auth/Dockerfile\n')]:
            with self.subTest(key=key):
                self.env[key] = value
                result, calls = self.invoke_build(candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                del self.env[key]

    def test_failed_image_build_has_no_accepted_manifest(self):
        candidate = self.build_fixture()
        self.env['BUILD_FAIL'] = '1'
        result, _ = self.invoke_build(candidate)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / 'build.json').exists())

    def publication_fixture(self):
        import hashlib
        artifacts, manifest = self.packaging_fixture()
        self.env['AWS_TESTING_ACCOUNT_ID'] = '111111111111'
        source = io.BytesIO()
        with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('build.json', json.dumps(manifest))
            for path in artifacts.iterdir():
                archive.write(path, path.name)
        self.routes[f'GET {PREFIX}/actions/runs/200/attempts/1'] = {
            'id':200,'run_attempt':1,'head_sha':CONTROLLER,'event':'workflow_dispatch',
            'path':'.github/workflows/aws-validation.yml','repository':{'full_name':REPO}}
        self.routes[f'GET {PREFIX}/actions/runs/200/attempts/1/jobs?per_page=100&page=1'] = {
            'jobs':[{'name':'Build candidate','status':'completed','conclusion':'success'}]}
        self.routes[f'GET {PREFIX}/actions/runs/200/artifacts?per_page=100&page=1'] = {
            'artifacts':[{'id':500,'name':'aws-build-200-1','expired':False,'size_in_bytes':len(source.getvalue()),
                          'digest':'sha256:'+hashlib.sha256(source.getvalue()).hexdigest(),
                          'workflow_run':{'id':200,'head_sha':CONTROLLER}}]}
        self.routes[f'GET {PREFIX}/actions/artifacts/500/zip'] = {
            '__bytes__':base64.b64encode(source.getvalue()).decode()}
        aws = self.bin / 'aws'
        aws.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['FAKE_API']);args=sys.argv[1:]
with (root/'aws-calls.jsonl').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[:2]==['sts','get-caller-identity']:
 print(json.dumps({'Account':os.environ.get('PUBLISH_ACCOUNT','111111111111'),'Arn':'arn:aws:sts::111111111111:assumed-role/onlineshop-test-publisher/job'}))
elif args[:2]==['ecr','get-login-password']: print('ecr-secret-canary')
elif args[:2]==['ecr','describe-images']: print(json.dumps({'imageDetails':[{'imageDigest':'sha256:'+'1'*64}]}))
else: sys.exit(1)
''')
        aws.chmod(0o755)
        docker = self.bin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['FAKE_API']);args=sys.argv[1:]
with (root/'docker-calls.jsonl').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='login':
 if sys.stdin.read().strip()!='ecr-secret-canary':sys.exit(1)
elif args[:2]==['image','inspect']:
 image=args[-1].split(':')[0];print(json.dumps([image+'@sha256:'+os.environ.get('PUSH_DIGEST','1'*64)]))
elif args[0] not in ['load','tag','push']: sys.exit(1)
''')
        docker.chmod(0o755)
        return artifacts, manifest

    def invoke_publish(self, artifacts, manifest):
        (self.directory / 'build.json').write_text(json.dumps(manifest))
        (self.directory / 'routes.json').write_text(json.dumps(self.routes))
        result = subprocess.run([sys.executable,str(SCRIPT),'publish','--request',str(self.directory/'request.json'),
                                 '--manifest',str(self.directory/'build.json'),'--artifacts',str(artifacts),
                                 '--output',str(self.directory/'images.json')],env=self.env,capture_output=True,text=True,timeout=20,check=False)
        self.assertNotIn('ecr-secret-canary',result.stdout+result.stderr)
        self.assertNotIn('secret-token-canary',result.stdout+result.stderr)
        if result.returncode:self.assertIn('Request rejected:',result.stderr)
        calls=self.directory/'docker-calls.jsonl'
        return result, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def test_publication_uses_trusted_source_artifact_and_fixed_digest_repositories_without_running_images(self):
        artifacts,manifest=self.publication_fixture()
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertEqual(result.returncode,0,result.stderr)
        published=json.loads((self.directory/'images.json').read_text())
        self.assertEqual(published['build_artifact_id'],500)
        self.assertEqual(published['images']['auth'],'111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-auth@sha256:'+'1'*64)
        self.assertEqual(len([call for call in calls if call[0]=='push']),5)
        self.assertFalse(any(call[0] in ['run','build','exec'] for call in calls))

    def test_untrusted_build_job_cannot_publish(self):
        artifacts,manifest=self.publication_fixture()
        self.routes[f'GET {PREFIX}/actions/runs/200/attempts/1/jobs?per_page=100&page=1']['jobs'][0]['conclusion']='skipped'
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,[])
        self.assertFalse((self.directory/'aws-calls.jsonl').exists())

    def test_replaced_local_manifest_cannot_relabel_a_trusted_build(self):
        artifacts,manifest=self.publication_fixture()
        manifest['images']['auth']['sha256']='0'*64
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,[])

    def test_wrong_publisher_account_never_loads_or_pushes_an_image(self):
        artifacts,manifest=self.publication_fixture()
        self.env['PUBLISH_ACCOUNT']='222222222222'
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,[])

    def test_source_artifact_digest_mismatch_prevents_aws_access(self):
        artifacts,manifest=self.publication_fixture()
        self.routes[f'GET {PREFIX}/actions/runs/200/artifacts?per_page=100&page=1']['artifacts'][0]['digest']='sha256:'+'0'*64
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,[])
        self.assertFalse((self.directory/'aws-calls.jsonl').exists())

    def test_changed_candidate_never_exchanges_or_publishes(self):
        artifacts,manifest=self.publication_fixture()
        self.pr['head']['sha']='e'*40
        result,calls=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(calls,[])
        self.assertFalse((self.directory/'aws-calls.jsonl').exists())

    def test_remote_tag_is_not_enough_when_pushed_digest_does_not_match(self):
        artifacts,manifest=self.publication_fixture()
        self.env['PUSH_DIGEST']='2'*64
        result,_=self.invoke_publish(artifacts,manifest)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.directory/'images.json').exists())


if __name__ == "__main__":
    unittest.main()

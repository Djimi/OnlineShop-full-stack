"""Trusted receipt/fixture verification without AWS or image execution."""

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import unittest
import zipfile

from tests.scripts import aws_validation_test as validation


class PublicationEvidenceStories(unittest.TestCase):
    def setUp(self):
        validation.RequestStories.setUp(self)
        result, _ = validation.RequestStories.invoke(self)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env["AWS_TESTING_ACCOUNT_ID"] = "111111111111"
        docker = self.bin / "docker"
        docker.write_text(
            '#!/bin/sh\necho invoked > "$FAKE_API/docker-called"\nexit 99\n'
        )
        docker.chmod(0o755)
        self.fixture = self.directory / "fixtures.tar"
        with tarfile.open(self.fixture, "w") as archive:
            for name in ["Auth/init-db/01-schema.sql", "Items/init-db/01-schema.sql"]:
                data = b"CREATE TABLE sample (id integer);"
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.receipt = json.loads((self.directory / "request.json").read_text())
        self.receipt.update(
            images={
                name: f"111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-{name}@sha256:"
                + "1" * 64
                for name in ["auth", "items", "gateway", "frontend", "e2e"]
            },
            fixtures={
                "file": "fixtures.tar",
                "size": self.fixture.stat().st_size,
                "sha256": hashlib.sha256(self.fixture.read_bytes()).hexdigest(),
            },
            build_artifact_id=600,
            build_artifact_digest="sha256:" + "e" * 64,
        )
        self.source = {
            "id": 200,
            "run_attempt": 1,
            "head_sha": "d" * 40,
            "head_branch": "main",
            "path": ".github/workflows/aws-validation.yml",
            "event": "workflow_dispatch",
            "status": "in_progress",
            "repository": {"full_name": validation.REPO},
        }
        self.jobs = [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in ["Candidate request", "Build candidate", "Publish candidate"]
        ]

    def invoke_publication(self):
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            member = zipfile.ZipInfo(
                "../images.json"
                if getattr(self, "unsafe_path", False)
                else "images.json"
            )
            if getattr(self, "linked", False):
                member.create_system = 3
                member.external_attr = 0o120777 << 16
            archive.writestr(member, json.dumps(self.receipt))
        data = zipped.getvalue()
        prefix = validation.PREFIX
        self.routes.update(
            {
                f"GET {prefix}/actions/runs/200/attempts/1": self.source,
                f"GET {prefix}/actions/runs/200/attempts/1/jobs?per_page=100&page=1": {
                    "jobs": self.jobs
                },
                f"GET {prefix}/actions/runs/200/artifacts?per_page=100&page=1": {
                    "artifacts": [
                        {
                            "id": 700,
                            "name": "aws-images-200-1",
                            "expired": False,
                            "size_in_bytes": len(data),
                            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                            "workflow_run": {"id": 200, "head_sha": "d" * 40},
                        }
                    ]
                },
                f"GET {prefix}/actions/artifacts/700/zip": {
                    "__bytes__": validation.base64.b64encode(data).decode()
                },
            }
        )
        artifacts = self.routes[
            f"GET {prefix}/actions/runs/200/artifacts?per_page=100&page=1"
        ]["artifacts"]
        case = getattr(self, "artifact_case", None)
        if case == "missing":
            artifacts.clear()
        if case == "duplicate":
            artifacts.append(dict(artifacts[0]))
        if case == "expired":
            artifacts[0]["expired"] = True
        if case == "digest":
            artifacts[0]["digest"] = "sha256:" + "0" * 64
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run(
            [
                sys.executable,
                str(validation.SCRIPT),
                "verify-publication",
                "--request",
                str(self.directory / "request.json"),
                "--fixtures",
                str(self.fixture),
                "--output",
                str(self.directory / "verified-publication.json"),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        self.assertFalse((self.directory / "aws-called").exists())
        self.assertFalse((self.directory / "docker-called").exists())
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        return result

    def test_trusted_main_publisher_receipt_and_fixture_verify_without_image_execution(
        self,
    ):
        result = self.invoke_publication()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads((self.directory / "verified-publication.json").read_text())
        self.assertEqual(proof["publication_artifact_id"], 700)
        self.assertEqual(proof["receipt"], self.receipt)
        self.assertFalse(proof["aws_validation_success"])

    def test_forged_run_skipped_publisher_wrong_candidate_and_mutable_digest_refuse(
        self,
    ):
        original_source = dict(self.source)
        original_receipt = json.loads(json.dumps(self.receipt))
        for case in ["feature", "skipped", "candidate", "mutable", "fixture"]:
            with self.subTest(case=case):
                if case == "feature":
                    self.source["head_branch"] = "feature"
                if case == "skipped":
                    self.jobs[2]["conclusion"] = "skipped"
                if case == "candidate":
                    self.receipt["candidate_sha"] = "f" * 40
                if case == "mutable":
                    self.receipt["images"]["auth"] = (
                        "111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-auth:latest"
                    )
                if case == "fixture":
                    self.receipt["fixtures"]["sha256"] = "0" * 64
                result = self.invoke_publication()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    (self.directory / "verified-publication.json").exists()
                )
                self.source = dict(original_source)
                self.receipt = json.loads(json.dumps(original_receipt))
                self.jobs[2]["conclusion"] = "success"

    def test_missing_duplicate_expired_or_corrupt_receipt_artifacts_refuse(self):
        for case in ["missing", "duplicate", "expired", "digest"]:
            with self.subTest(case=case):
                self.artifact_case = case
                self.assertNotEqual(self.invoke_publication().returncode, 0)
                self.assertFalse(
                    (self.directory / "verified-publication.json").exists()
                )

    def test_link_or_traversal_zip_member_cannot_become_a_receipt(self):
        for flag in ["unsafe_path", "linked"]:
            with self.subTest(flag=flag):
                setattr(self, flag, True)
                self.assertNotEqual(self.invoke_publication().returncode, 0)
                self.assertFalse(
                    (self.directory / "verified-publication.json").exists()
                )
                setattr(self, flag, False)

    def test_candidate_change_before_receipt_output_refuses(self):
        changed = json.loads(json.dumps(self.pr))
        changed["head"]["sha"] = "f" * 40
        self.routes[f"GET {validation.PREFIX}/pulls/42"] = {
            "__sequence__": [self.pr, changed]
        }
        self.assertNotEqual(self.invoke_publication().returncode, 0)
        self.assertFalse((self.directory / "verified-publication.json").exists())

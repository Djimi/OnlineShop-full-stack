"""Report-verification stories; GitHub is external, report parsing is real."""

import copy
import io
import json
import subprocess
import sys
import tarfile
import unittest

from tests.scripts import aws_validation_test as validation


class ReportStories(unittest.TestCase):
    def setUp(self):
        validation.RequestStories.setUp(self)
        result, _ = validation.RequestStories.invoke(self)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.directory / "calls.jsonl").unlink()
        self.members = []
        for suite, count in [("ItemsE2ETest", 3), ("RestAssuredLoggingTest", 1)]:
            name = "com.onlineshop.e2e." + suite
            cases = "".join(
                f'<testcase classname="{name}" name="test{i}" />' for i in range(count)
            )
            data = f'<testsuite name="{name}" tests="{count}" failures="0" errors="0" skipped="0">{cases}</testsuite>'.encode()
            self.members.append(("TEST-" + name + ".xml", data))

    def invoke_reports(self):
        archive = self.directory / "reports.tar"
        with tarfile.open(
            archive, "w:gz" if getattr(self, "compressed", False) else "w"
        ) as output:
            for name, data in self.members:
                member = tarfile.TarInfo(name)
                if getattr(self, "link_member", False):
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../escape.xml"
                    output.addfile(member)
                    continue
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run(
            [
                sys.executable,
                str(validation.SCRIPT),
                "verify-reports",
                "--request",
                str(self.directory / "request.json"),
                "--reports",
                str(archive),
                "--output",
                str(self.directory / "verified-reports.json"),
            ],
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertFalse((self.directory / "aws-called").exists())
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        return result

    def test_four_executed_tests_verify_reports_without_publishing_success(self):
        result = self.invoke_reports()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.directory / "verified-reports.json").read_text())
        self.assertEqual(report["tests"], 4)
        self.assertFalse(report["aws_validation_success"])
        self.assertFalse(report["runtime_provenance_verified"])
        self.assertEqual(report["candidate_sha"], "c" * 40)
        calls = [
            json.loads(line)
            for line in (self.directory / "calls.jsonl").read_text().splitlines()
        ]
        self.assertTrue(all(call["method"] == "GET" for call in calls))

    def test_false_counts_empty_skipped_failed_and_entity_reports_refuse(self):
        original = list(self.members)
        for data in [
            original[0][1].replace(b'tests="3"', b'tests="0"'),
            original[0][1].replace(b'skipped="0"', b'skipped="3"'),
            original[0][1].replace(b'failures="0"', b'failures="1"'),
            original[0][1].replace(
                b"<testcase", b'<failure message="raw-secret-canary"/><testcase', 1
            ),
            b'<!DOCTYPE testsuite [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
            + original[0][1],
            b'<testsuite name="raw-secret-canary">',
        ]:
            with self.subTest(data=data[:40]):
                self.members = [(original[0][0], data), original[1]]
                result = self.invoke_reports()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "verified-reports.json").exists())
                self.assertNotIn("raw-secret-canary", result.stdout + result.stderr)

    def test_missing_duplicate_traversal_and_extra_suites_refuse(self):
        original = list(self.members)
        for members in [
            original[:1],
            original + original[:1],
            [("../escape.xml", original[0][1]), original[1]],
            original + [("extra.xml", b"<testsuite/>")],
        ]:
            with self.subTest(names=[name for name, _ in members]):
                self.members = members
                self.assertNotEqual(self.invoke_reports().returncode, 0)
                self.assertFalse((self.directory / "verified-reports.json").exists())
                self.assertFalse((self.directory.parent / "escape.xml").exists())

    def test_link_members_and_excessive_report_payload_refuse_without_extraction(self):
        self.link_member = True
        self.assertNotEqual(self.invoke_reports().returncode, 0)
        self.assertFalse((self.directory / "verified-reports.json").exists())
        self.link_member = False
        self.members[0] = (self.members[0][0], b"x" * (512 * 1024 + 1))
        self.assertNotEqual(self.invoke_reports().returncode, 0)
        self.assertFalse((self.directory / "verified-reports.json").exists())

    def test_compressed_reports_refuse_before_unbounded_metadata_decompression(self):
        self.compressed = True
        self.assertNotEqual(self.invoke_reports().returncode, 0)
        self.assertFalse((self.directory / "verified-reports.json").exists())

    def test_candidate_change_before_evidence_output_cannot_verify_current_attempt(
        self,
    ):
        changed = copy.deepcopy(self.pr)
        changed["head"]["sha"] = "e" * 40
        self.routes[f"GET {validation.PREFIX}/pulls/42"] = {
            "__sequence__": [self.pr, changed]
        }
        result = self.invoke_reports()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "verified-reports.json").exists())

    def test_arbitrary_test_properties_output_and_case_names_never_enter_evidence(self):
        name, data = self.members[0]
        data = data.replace(b"test0", b"raw-secret-canary").replace(
            b"</testsuite>",
            b'<properties><property name="password" value="raw-secret-canary"/></properties><system-out>raw-secret-canary</system-out></testsuite>',
        )
        self.members[0] = (name, data)
        result = self.invoke_reports()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = (self.directory / "verified-reports.json").read_text()
        self.assertNotIn("raw-secret-canary", evidence + result.stdout + result.stderr)

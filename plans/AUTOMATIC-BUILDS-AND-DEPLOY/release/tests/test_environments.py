"""Unit tests for production/staging separation and inventory consistency
(Pass 3, subphase 3.5)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import environments


def prod_config():
    return {
        "vpcId": "vpc-11111111111111111",
        "cluster": "onlineshop-cluster",
        "dbInstance": "onlineshop-postgres-db",
        "dbSubnetGroup": "default-vpc-11111111111111111",
        "dbSecurityGroup": "sg-aaaaaaaaaaaaaaaa1",
        "ecsSecurityGroup": "sg-bbbbbbbbbbbbbbbbb1",
        "albSecurityGroup": "sg-ccccccccccccccccc1",
        "albName": "onlineshop-alb",
        "targetGroupArn": (
            "arn:aws:elasticloadbalancing:eu-north-1:799111666795:"
            "targetgroup/onlineshop-gateway-tg/1"
        ),
        "gatewayService": "onlineshop-api-gateway",
        "namespace": "onlineshop.local",
        "services": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
        "subnets": ["subnet-1", "subnet-2", "subnet-3"],
        "secrets": ["onlineshop/auth/db", "onlineshop/items/db", "onlineshop/rds/master"],
        "logGroups": [
            "/ecs/onlineshop-auth",
            "/ecs/onlineshop-items",
            "/ecs/onlineshop-api-gateway",
        ],
    }


def staging_config():
    return {
        "vpcId": "vpc-22222222222222222",
        "cluster": "onlineshop-staging-cluster",
        "dbInstance": "onlineshop-staging-postgres",
        "dbSubnetGroup": "onlineshop-staging-db-subnets",
        "dbSecurityGroup": "sg-ddddddddddddddddd1",
        "ecsSecurityGroup": "sg-eeeeeeeeeeeeeeeee1",
        "albSecurityGroup": "sg-fffffffffffffffff1",
        "albName": "onlineshop-staging-v2-alb",
        "targetGroupArn": (
            "arn:aws:elasticloadbalancing:eu-north-1:799111666795:"
            "targetgroup/onlineshop-staging-tg-v2/2"
        ),
        "gatewayService": "onlineshop-api-gateway-staging",
        "namespace": "staging.onlineshop.local",
        "services": [
            "onlineshop-auth-staging",
            "onlineshop-items-staging",
            "onlineshop-api-gateway-staging",
        ],
        "subnets": ["subnet-4", "subnet-5"],
        "secrets": ["onlineshop/auth/db-staging", "onlineshop/items/db-staging"],
        "logGroups": [
            "/ecs/onlineshop-auth-staging",
            "/ecs/onlineshop-items-staging",
            "/ecs/onlineshop-api-gateway-staging",
        ],
    }


def codes(outcome):
    return [issue["code"] for issue in outcome.issues]


class SeparationTests(unittest.TestCase):
    def test_isolated_configs_pass(self):
        outcome = environments.separation_issues(prod_config(), staging_config())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_shared_vpc_rejected(self):
        staging = staging_config()
        staging["vpcId"] = prod_config()["vpcId"]
        outcome = environments.separation_issues(prod_config(), staging)
        self.assertFalse(outcome.valid)
        shared = [issue for issue in outcome.issues if issue["code"] == "SHARED_RESOURCE"]
        self.assertTrue(any("vpcId" in issue["field"] for issue in shared))

    def test_shared_cluster_rejected(self):
        staging = staging_config()
        staging["cluster"] = prod_config()["cluster"]
        outcome = environments.separation_issues(prod_config(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("SHARED_RESOURCE", codes(outcome))

    def test_shared_secret_rejected(self):
        staging = staging_config()
        staging["secrets"] = list(prod_config()["secrets"])
        outcome = environments.separation_issues(prod_config(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("SHARED_RESOURCE", codes(outcome))

    def test_missing_identifier_reported(self):
        staging = dict(staging_config())
        del staging["namespace"]
        outcome = environments.separation_issues(prod_config(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_IDENTIFIER", codes(outcome))

    def test_shared_execution_role_is_not_a_separation_violation(self):
        # The ECS execution role is shared infrastructure and is not an
        # environment-scoped identifier.
        prod = prod_config()
        staging = staging_config()
        prod["executionRoleArn"] = "arn:aws:iam::799111666795:role/ecsTaskExecutionRole"
        staging["executionRoleArn"] = "arn:aws:iam::799111666795:role/ecsTaskExecutionRole"
        outcome = environments.separation_issues(prod, staging)
        self.assertTrue(outcome.valid, outcome.issues)

    def test_invalid_input_rejected(self):
        outcome = environments.separation_issues([], {})
        self.assertFalse(outcome.valid)
        self.assertIn("INVALID_INPUT", codes(outcome))


class InventoryTests(unittest.TestCase):
    def test_expected_matches_observed(self):
        expected = prod_config()
        observed = dict(expected)
        outcome = environments.inventory_issues(expected, observed)
        self.assertTrue(outcome.valid, outcome.issues)

    def test_drift_reported(self):
        expected = prod_config()
        observed = dict(expected)
        observed["cluster"] = "some-other-cluster"
        outcome = environments.inventory_issues(expected, observed)
        self.assertFalse(outcome.valid)
        self.assertIn("INVENTORY_DRIFT", codes(outcome))

    def test_observed_missing_reported(self):
        expected = prod_config()
        observed = {k: v for k, v in expected.items() if k != "vpcId"}
        outcome = environments.inventory_issues(expected, observed)
        self.assertFalse(outcome.valid)
        self.assertIn("OBSERVED_MISSING", codes(outcome))

    def test_config_metadata_keys_are_skipped(self):
        expected = prod_config()
        expected["accountId"] = "799111666795"
        observed = dict(expected)
        observed["accountId"] = "000000000000"
        outcome = environments.inventory_issues(expected, observed)
        self.assertTrue(outcome.valid, outcome.issues)

    def test_api_read_error_fails_closed(self):
        # An AWS read failure ("error") must fail the inventory with a distinct
        # code — it is never disguised as a missing resource or as drift.
        expected = prod_config()
        observed = dict(expected)
        observed["vpcId"] = "error"
        outcome = environments.inventory_issues(expected, observed)
        self.assertFalse(outcome.valid)
        self.assertIn("OBSERVED_READ_ERROR", codes(outcome))

    def test_element_read_error_fails_closed(self):
        expected = prod_config()
        observed = dict(expected)
        observed["services"] = [
            "onlineshop-auth",
            "onlineshop-items-ERROR",
            "onlineshop-api-gateway",
        ]
        outcome = environments.inventory_issues(expected, observed)
        self.assertFalse(outcome.valid)
        self.assertIn("OBSERVED_READ_ERROR", codes(outcome))

    def test_public_database_rejected(self):
        expected = prod_config()
        observed = dict(expected)
        observed["dbPublicAccessible"] = "true"
        outcome = environments.inventory_issues(expected, observed)
        self.assertFalse(outcome.valid)
        self.assertIn("DB_PUBLIC_ACCESSIBLE", codes(outcome))

    def test_private_database_accepted(self):
        expected = prod_config()
        observed = dict(expected)
        observed["dbPublicAccessible"] = "false"
        outcome = environments.inventory_issues(expected, observed)
        self.assertTrue(outcome.valid, outcome.issues)


class TopologyTests(unittest.TestCase):
    def prod_topology(self):
        return {
            "vpcId": "vpc-11111111111111111",
            "sgVpcs": ["vpc-11111111111111111", "vpc-11111111111111111"],
            "subnetVpcs": ["vpc-11111111111111111"],
            "dbSubnetGroupVpc": "vpc-11111111111111111",
            "serviceNamespaces": {"onlineshop-auth": "onlineshop.local"},
        }

    def staging_topology(self):
        return {
            "vpcId": "vpc-22222222222222222",
            "sgVpcs": ["vpc-22222222222222222", "vpc-22222222222222222"],
            "subnetVpcs": ["vpc-22222222222222222"],
            "dbSubnetGroupVpc": "vpc-22222222222222222",
            "serviceNamespaces": {"onlineshop-auth-staging": "staging.onlineshop.local"},
        }

    def test_disjoint_topology_passes(self):
        outcome = environments.topology_overlap_issues(
            self.prod_topology(), self.staging_topology()
        )
        self.assertTrue(outcome.valid, outcome.issues)

    def test_shared_security_group_vpc_rejected(self):
        staging = self.staging_topology()
        staging["sgVpcs"] = ["vpc-22222222222222222", "vpc-11111111111111111"]
        outcome = environments.topology_overlap_issues(self.prod_topology(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("SHARED_VPC", codes(outcome))

    def test_shared_namespace_rejected(self):
        staging = self.staging_topology()
        staging["serviceNamespaces"]["onlineshop-auth-staging"] = "onlineshop.local"
        outcome = environments.topology_overlap_issues(self.prod_topology(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("SHARED_NAMESPACE", codes(outcome))

    def test_shared_db_subnet_vpc_rejected(self):
        staging = self.staging_topology()
        staging["dbSubnetGroupVpc"] = "vpc-11111111111111111"
        outcome = environments.topology_overlap_issues(self.prod_topology(), staging)
        self.assertFalse(outcome.valid)
        self.assertIn("SHARED_VPC", codes(outcome))

    def test_missing_values_not_treated_as_shared(self):
        prod = self.prod_topology()
        prod["vpcId"] = "missing"
        outcome = environments.topology_overlap_issues(prod, self.staging_topology())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_topology_read_error_fails_closed(self):
        # A failed topology read cannot prove isolation either way; it must
        # fail closed with a distinct code rather than being treated as
        # "missing" (non-shared).
        prod = self.prod_topology()
        prod["sgVpcs"] = ["vpc-11111111111111111", "error"]
        outcome = environments.topology_overlap_issues(prod, self.staging_topology())
        self.assertFalse(outcome.valid)
        self.assertIn("TOPO_READ_ERROR", codes(outcome))


class CliTests(unittest.TestCase):
    def test_cli_separation_and_inventory(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths = {
            "prod": os.path.join(base, "tmp-env-prod.json"),
            "staging": os.path.join(base, "tmp-env-staging.json"),
            "observed": os.path.join(base, "tmp-env-observed.json"),
        }
        try:
            with open(paths["prod"], "w", encoding="utf-8") as handle:
                json.dump(prod_config(), handle)
            with open(paths["staging"], "w", encoding="utf-8") as handle:
                json.dump(staging_config(), handle)
            with open(paths["observed"], "w", encoding="utf-8") as handle:
                json.dump(prod_config(), handle)
            sep = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.environments",
                    "separation",
                    "--prod",
                    paths["prod"],
                    "--staging",
                    paths["staging"],
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sep.returncode, 0, sep.stderr)
            self.assertIn('"valid":true', sep.stdout)
            inv = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.environments",
                    "inventory",
                    "--expected",
                    paths["prod"],
                    "--observed",
                    paths["observed"],
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(inv.returncode, 0, inv.stderr)
            self.assertIn('"valid":true', inv.stdout)
        finally:
            for path in paths.values():
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    unittest.main()

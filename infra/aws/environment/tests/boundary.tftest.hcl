mock_provider "aws" {}
variables {
  account_id   = "111111111111"
  host_profile = "onlineshop-test-host"
  ami_id       = "ami-04478a3e21a0d79a7"
  generation   = "run-123-attempt-1"
}
run "private_on_demand_host" {
  command = plan
  assert {
    condition     = aws_instance.host.instance_type == "m7i-flex.large"
    error_message = "Use the selected Free-plan-compatible x86_64 host."
  }
  assert {
    condition     = length(aws_security_group.host.ingress) == 0
    error_message = "No public inbound rule is allowed."
  }
  assert {
    condition     = aws_instance.host.root_block_device[0].encrypted && aws_instance.host.root_block_device[0].delete_on_termination
    error_message = "Disposable encrypted root disk is mandatory."
  }
  assert {
    condition     = aws_instance.host.metadata_options[0].http_tokens == "required" && aws_instance.host.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDSv2 and minimal metadata hop limit are mandatory."
  }
  assert {
    condition     = length(aws_instance.host.instance_market_options) == 0
    error_message = "Spot is forbidden."
  }
}

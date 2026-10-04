mock_provider "aws" {}
override_resource {
  target          = aws_iam_role.host
  override_during = plan
  values          = { arn = "arn:aws:iam::111111111111:role/onlineshop-test-host" }
}
override_resource {
  target          = aws_iam_instance_profile.host
  override_during = plan
  values          = { arn = "arn:aws:iam::111111111111:instance-profile/onlineshop-test-host" }
}
override_resource {
  target          = aws_secretsmanager_secret.credentials
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:eu-north-1:111111111111:secret:onlineshop-test/credentials-example" }
}
override_resource {
  target          = aws_ecr_repository.application
  override_during = plan
  values          = { arn = "arn:aws:ecr:eu-north-1:111111111111:repository/onlineshop-test-example" }
}
variables {
  account_id   = "111111111111"
  state_bucket = "test-protected-state"
  oidc_subject = "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:aws-testing"
}
run "retained_bootstrap" {
  command = plan
  assert {
    condition = anytrue([for statement in jsondecode(aws_iam_role_policy.operator.policy).Statement :
      statement.Sid == "LaunchTaggedHost" &&
      try(statement.Condition.StringEquals["ec2:InstanceType"], "") == "m7i-flex.large"
    ])
    error_message = "Operator launch permission must match the selected Free-plan host."
  }
  assert {
    condition     = length(aws_ecr_repository.application) == 5
    error_message = "Only five fixed private application/test repositories are allowed."
  }
  assert {
    condition     = alltrue([for repository in aws_ecr_repository.application : !repository.force_delete])
    error_message = "Routine disposal must not erase image repositories."
  }
  assert {
    condition     = jsondecode(aws_iam_role.publisher.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "OIDC audience must be exact."
  }
  assert {
    condition     = jsondecode(aws_iam_role.operator.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:aws-testing"
    error_message = "Use the actual observed immutable-ID subject."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_role_policy.operator.policy).Statement :
      !can(statement.Condition.StringEquals["ec2:InstanceType"]) ||
      alltrue([for resource in flatten([statement.Resource]) : can(regex(":instance/", resource))])
    ])
    error_message = "Instance-type conditions cannot be required on volumes or network interfaces that do not supply that key."
  }
  assert {
    condition = alltrue([for action in ["ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:CreateSecurityGroup"] :
      anytrue([for statement in jsondecode(aws_iam_role_policy.operator.policy).Statement :
        contains(flatten([statement.Action]), action) &&
        try(statement.Condition.StringEquals["ec2:ResourceTag/ManagedBy"], "") == "onlineshop-test" &&
        contains(flatten([statement.Resource]), "arn:aws:ec2:eu-north-1:111111111111:vpc/*")
      ])
    ])
    error_message = "Creation must separately authorize the already-owned VPC using resource tags, not absent request tags."
  }
}

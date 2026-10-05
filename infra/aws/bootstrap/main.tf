locals {
  tags         = { ManagedBy = "onlineshop-test-bootstrap", Repository = "Djimi/OnlineShop-full-stack" }
  images       = toset(["auth", "items", "gateway", "frontend", "e2e"])
  repositories = [for repository in aws_ecr_repository.application : repository.arn]
  ec2          = "arn:aws:ec2:eu-north-1:${var.account_id}"
  owned        = { StringEquals = { "ec2:ResourceTag/ManagedBy" = "onlineshop-test" } }
  creation     = { StringEquals = { "aws:RequestTag/ManagedBy" = "onlineshop-test" } }
  trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com" },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = var.oidc_subject
    } }
  }] })
}
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_ecr_repository" "application" {
  for_each             = local.images
  name                 = "onlineshop-test-${each.key}"
  force_delete         = false
  image_tag_mutability = "IMMUTABLE_WITH_EXCLUSION"
  image_tag_mutability_exclusion_filter {
    filter      = "active-*"
    filter_type = "WILDCARD"
  }
  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_ecr_lifecycle_policy" "application" {
  for_each   = aws_ecr_repository.application
  repository = each.value.name
  policy = jsonencode({ rules = [
    { rulePriority = 1, description = "Protect the bounded previous/incoming/current active digests", selection = {
      tagStatus = "tagged", tagPrefixList = ["active-"], countType = "imageCountMoreThan", countNumber = 1000
    }, action = { type = "expire" } },
    { rulePriority = 2, description = "Bound inactive candidate images", selection = {
      tagStatus = "tagged", tagPrefixList = ["run-"], countType = "imageCountMoreThan", countNumber = 20
    }, action = { type = "expire" } },
    { rulePriority = 3, description = "Bound untagged leftovers", selection = {
      tagStatus = "untagged", countType = "imageCountMoreThan", countNumber = 10
    }, action = { type = "expire" } }
  ] })
}
resource "aws_secretsmanager_secret" "credentials" {
  name = "onlineshop-test/credentials"
  tags = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role" "publisher" {
  depends_on           = [aws_iam_openid_connect_provider.github]
  name                 = "onlineshop-test-publisher"
  max_session_duration = 7200
  assume_role_policy   = local.trust
  tags                 = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "publisher" {
  role = aws_iam_role.publisher.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages", "ecr:BatchGetImage"], Resource = local.repositories }
  ] })
}
resource "aws_iam_role" "host" {
  name = "onlineshop-test-host"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
  tags = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_instance_profile" "host" {
  name = "onlineshop-test-host"
  role = aws_iam_role.host.name
  tags = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "host" {
  role = aws_iam_role.host.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = local.repositories },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.credentials.arn },
    { Sid = "ReadFixedRuntimeInputs", Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.state_bucket}/operations/runtime-input/*" },
    { Sid = "WriteFixedRuntimeEvidence", Effect = "Allow", Action = ["s3:PutObject"], Resource = "arn:aws:s3:::${var.state_bucket}/operations/runtime-evidence/*", Condition = { StringEquals = { "s3:if-none-match" = "*" } } },
    { Effect = "Allow", Action = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], Resource = "*" }
  ] })
}
resource "aws_iam_role" "operator" {
  depends_on           = [aws_iam_openid_connect_provider.github]
  name                 = "onlineshop-test-operator"
  max_session_duration = 7200
  assume_role_policy   = local.trust
  tags                 = local.tags
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role_policy" "operator" {
  role = aws_iam_role.operator.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "DiscoveryOnly", Effect = "Allow", Action = ["ec2:Describe*", "ssm:DescribeInstanceInformation", "ssm:ListCommands", "ssm:ListCommandInvocations", "ssm:GetCommandInvocation", "ssm:CancelCommand"], Resource = "*" },
    { Sid = "CreateTaggedEnvironment", Effect = "Allow", Action = ["ec2:CreateVpc", "ec2:CreateSubnet", "ec2:CreateInternetGateway", "ec2:CreateRouteTable", "ec2:CreateSecurityGroup", "ec2:CreateLaunchTemplate"], Resource = "${local.ec2}:*/*", Condition = local.creation },
    { Sid = "CreateOnlyInsideOwnedVpc", Effect = "Allow", Action = ["ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:CreateSecurityGroup"], Resource = "${local.ec2}:vpc/*", Condition = local.owned },
    { Sid = "OperateOwnedEnvironment", Effect = "Allow", Action = ["ec2:DeleteVpc", "ec2:DeleteSubnet", "ec2:DeleteInternetGateway", "ec2:DeleteRouteTable", "ec2:DeleteSecurityGroup", "ec2:DeleteLaunchTemplate", "ec2:CreateLaunchTemplateVersion", "ec2:ModifyLaunchTemplate", "ec2:ModifyVpcAttribute", "ec2:ModifySubnetAttribute", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway", "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:ReplaceRoute", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress", "ec2:TerminateInstances", "ec2:StopInstances", "ec2:StartInstances", "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceMetadataOptions", "ec2:CreateTags"], Resource = "${local.ec2}:*/*", Condition = local.owned },
    { Sid = "TagOnlyAtCreation", Effect = "Allow", Action = ["ec2:CreateTags"], Resource = "${local.ec2}:*/*", Condition = { StringEquals = { "aws:RequestTag/ManagedBy" = "onlineshop-test", "ec2:CreateAction" = ["CreateVpc", "CreateSubnet", "CreateInternetGateway", "CreateRouteTable", "CreateSecurityGroup", "CreateLaunchTemplate", "RunInstances"] } } },
    { Sid = "DeleteOnlyOwnedInterfaceGeneration", Effect = "Allow", Action = ["ec2:DeleteTags"], Resource = "${local.ec2}:network-interface/*", Condition = {
      StringEquals                = { "ec2:ResourceTag/ManagedBy" = "onlineshop-test" },
      "ForAllValues:StringEquals" = { "aws:TagKeys" = ["Generation"] },
      Null                        = { "aws:TagKeys" = "false" }
    } },
    { Sid = "LaunchTaggedHost", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "aws:RequestTag/ManagedBy" = "onlineshop-test", "ec2:InstanceType" = var.instance_type }, StringEqualsIfExists = { "ec2:InstanceMarketType" = "on-demand" } } },
    { Sid = "LaunchTaggedHostStorageAndInterface", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["${local.ec2}:volume/*", "${local.ec2}:network-interface/*"], Condition = local.creation },
    { Sid = "UseOwnedNetworkAndTemplate", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["${local.ec2}:subnet/*", "${local.ec2}:security-group/*", "${local.ec2}:launch-template/*"], Condition = local.owned },
    { Sid = "PinnedAMIOnly", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "arn:aws:ec2:eu-north-1::image/${var.ami_id}" },
    { Sid = "PassExactHostRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.host.arn, Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } } },
    { Sid = "ReadExactProfile", Effect = "Allow", Action = ["iam:GetInstanceProfile"], Resource = aws_iam_instance_profile.host.arn },
    { Sid = "ReadBucketLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = "arn:aws:s3:::${var.state_bucket}" },
    { Sid = "ListOnlyEnvironmentAndOperations", Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${var.state_bucket}", Condition = { StringLike = { "s3:prefix" = ["state/environment.tfstate", "state/environment.tfstate.tflock", "state/disabled-workspaces", "state/disabled-workspaces/*", "operations/*", "operations/"] } } },
    { Sid = "EnvironmentStateAndOperationRecords", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["arn:aws:s3:::${var.state_bucket}/state/environment.tfstate", "arn:aws:s3:::${var.state_bucket}/state/environment.tfstate.tflock", "arn:aws:s3:::${var.state_bucket}/operations/*"] },
    { Sid = "UnlockOnlyExactEnvironmentLock", Effect = "Allow", Action = ["s3:DeleteObject"], Resource = "arn:aws:s3:::${var.state_bucket}/state/environment.tfstate.tflock" },
    { Sid = "RunOnlyFixedSSMDocument", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ssm:eu-north-1::document/AWS-RunShellScript" },
    { Sid = "RunOnlyOwnedHost", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "ssm:resourceTag/ManagedBy" = "onlineshop-test" } } },
    { Sid = "ProtectDeployedImageRetention", Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchGetImage", "ecr:PutImage", "ecr:BatchDeleteImage"], Resource = local.repositories }
  ] })
}

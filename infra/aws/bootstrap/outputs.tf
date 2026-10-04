output "publisher_role" { value = aws_iam_role.publisher.arn }
output "operator_role" { value = aws_iam_role.operator.arn }
output "host_profile" { value = aws_iam_instance_profile.host.name }
output "secret_arn" { value = aws_secretsmanager_secret.credentials.arn }
output "repositories" { value = { for name, repository in aws_ecr_repository.application : name => repository.repository_url } }

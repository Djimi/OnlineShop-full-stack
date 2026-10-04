output "instance_id" { value = aws_instance.host.id }
output "vpc_id" { value = aws_vpc.main.id }
output "security_group_id" { value = aws_security_group.host.id }
output "generation" { value = var.generation }
output "root_volume_id" { value = aws_instance.host.root_block_device[0].volume_id }

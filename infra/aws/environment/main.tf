locals {
  tags = { ManagedBy = "onlineshop-test", Repository = "Djimi/OnlineShop-full-stack", Generation = var.generation }
}
resource "aws_vpc" "main" {
  cidr_block           = "10.83.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = local.tags
}
resource "aws_subnet" "host" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.83.1.0/24"
  availability_zone       = "eu-north-1a"
  map_public_ip_on_launch = true
  tags                    = local.tags
}
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = local.tags
}
resource "aws_route_table" "host" {
  vpc_id = aws_vpc.main.id
  tags   = local.tags
}
resource "aws_route" "outbound" {
  route_table_id         = aws_route_table.host.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}
resource "aws_route_table_association" "host" {
  subnet_id      = aws_subnet.host.id
  route_table_id = aws_route_table.host.id
}
resource "aws_security_group" "host" {
  name        = "onlineshop-test-host"
  description = "SSM-only testing host; no incoming connectivity"
  vpc_id      = aws_vpc.main.id
  ingress     = []
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}
resource "aws_launch_template" "host" {
  name = "onlineshop-test-host"
  tags = local.tags
  dynamic "tag_specifications" {
    for_each = toset(["instance", "volume", "network-interface"])
    content {
      resource_type = tag_specifications.value
      # Stable launch-time ownership avoids a new version for every attempt.
      tags = { ManagedBy = local.tags.ManagedBy, Repository = local.tags.Repository }
    }
  }
}
resource "aws_instance" "host" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.host.id
  vpc_security_group_ids      = [aws_security_group.host.id]
  associate_public_ip_address = true
  iam_instance_profile        = var.host_profile
  launch_template {
    id      = aws_launch_template.host.id
    version = tostring(aws_launch_template.host.latest_version)
  }
  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_protocol_ipv6          = "disabled"
    instance_metadata_tags      = "disabled"
  }
  tags        = local.tags
  volume_tags = local.tags
  depends_on  = [aws_route.outbound, aws_route_table_association.host]
}
resource "aws_ec2_tag" "network_generation" {
  resource_id = aws_instance.host.primary_network_interface_id
  key         = "Generation"
  value       = var.generation
}

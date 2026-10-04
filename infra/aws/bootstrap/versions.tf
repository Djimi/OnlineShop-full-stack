terraform {
  required_version = "= 1.16.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.67.0" }
  }
}
provider "aws" {
  region              = "eu-north-1"
  allowed_account_ids = [var.account_id]
}

variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use the verified target account."
  }
}
variable "state_bucket" { type = string }
variable "oidc_subject" {
  type = string
  validation {
    condition     = var.oidc_subject == "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:aws-testing"
    error_message = "Record and verify the actual exact GitHub Environment subject before provisioning."
  }
}
variable "ami_id" {
  type    = string
  default = "ami-04478a3e21a0d79a7"
}
variable "instance_type" {
  type    = string
  default = "c7i.xlarge"
}

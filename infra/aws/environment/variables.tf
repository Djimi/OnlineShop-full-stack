variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use the verified target account."
  }
}
variable "host_profile" {
  type = string
  validation {
    condition     = var.host_profile == "onlineshop-test-host"
    error_message = "Only the approved host profile is permitted."
  }
}
variable "ami_id" {
  type    = string
  default = "ami-04478a3e21a0d79a7"
  validation {
    condition     = var.ami_id == "ami-04478a3e21a0d79a7"
    error_message = "AMI changes require a trusted configuration update."
  }
}
variable "instance_type" {
  type    = string
  default = "m7i-flex.large"
  validation {
    condition     = var.instance_type == "m7i-flex.large"
    error_message = "Use the selected Free-plan-compatible On-Demand x86_64 size."
  }
}
variable "generation" {
  type = string
  validation {
    condition     = can(regex("^run-[1-9][0-9]*-attempt-[1-9][0-9]*$", var.generation))
    error_message = "Generation must identify the owning attempt."
  }
}

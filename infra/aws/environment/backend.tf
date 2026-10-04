terraform {
  backend "s3" {
    key                  = "state/environment.tfstate"
    region               = "eu-north-1"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "state/disabled-workspaces"
  }
}

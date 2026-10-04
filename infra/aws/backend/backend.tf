terraform {
  backend "s3" {
    key          = "state/backend.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

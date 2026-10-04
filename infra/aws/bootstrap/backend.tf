terraform {
  backend "s3" {
    key          = "state/bootstrap.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

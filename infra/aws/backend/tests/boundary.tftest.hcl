mock_provider "aws" {}
variables {
  account_id = "111111111111"
}
run "private_retained_state" {
  command = plan
  assert {
    condition     = aws_s3_bucket.state.force_destroy == false
    error_message = "State bucket cannot be disposable."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "State storage must remain private."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State version history is mandatory."
  }
}

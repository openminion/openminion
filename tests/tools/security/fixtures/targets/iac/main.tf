resource "aws_s3_bucket" "public" {
  bucket = "openminion-synthetic-fixture"
  acl    = "public-read"
}

"""A generic S3-compatible blob store, configured only by environment variables (no endpoint or bucket in the code)."""
import pathlib

from score_lib.blobs import check_sha, key_of


class S3BlobStore:
    deep_always = False

    def __init__(self, client, bucket, prefix="", public_url=None):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.public_url = public_url.rstrip("/") if public_url else None

    @classmethod
    def from_environment(cls, environ):
        """A store from SCORE_LIB_S3_* and the standard AWS credential variables."""
        import boto3
        client = boto3.client("s3", endpoint_url=environ["SCORE_LIB_S3_ENDPOINT"],
                              region_name=environ.get("SCORE_LIB_S3_REGION") or None,
                              aws_access_key_id=environ.get("AWS_ACCESS_KEY_ID"),
                              aws_secret_access_key=environ.get("AWS_SECRET_ACCESS_KEY"))
        return cls(client, environ["SCORE_LIB_S3_BUCKET"], environ.get("SCORE_LIB_S3_PREFIX", ""),
                   environ.get("SCORE_LIB_PUBLIC_URL"))

    def key(self, sha256):
        """The object key of a blob, under the optional prefix."""
        return f"{self.prefix}/{key_of(sha256)}" if self.prefix else key_of(sha256)

    def has(self, sha256):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(sha256))
        except Exception as error:  # botocore ClientError; a 404 means absent, anything else is raised
            if getattr(error, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def put(self, sha256, source):
        """Upload a file under its sha256 after checking it, with metadata sha256; nothing when already there."""
        check_sha(source, sha256)
        if self.has(sha256):
            return
        self.client.upload_file(str(source), self.bucket, self.key(sha256),
                                ExtraArgs={"Metadata": {"sha256": sha256}})

    def get(self, sha256, destination):
        """Download a blob to a path and check its bytes."""
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self.key(sha256), str(destination))
        check_sha(destination, sha256)

    def url(self, sha256):
        """The public read link when SCORE_LIB_PUBLIC_URL is set."""
        return f"{self.public_url}/{self.key(sha256)}" if self.public_url else None

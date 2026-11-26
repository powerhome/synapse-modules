"""Where in-cluster modules upload to the media repo."""

# The media repo serves the homeserver's domain by Host, so uploads present the
# homeserver's in-cluster address rather than the media repo's own.
UPLOAD_URL = "http://media-repo:8000/_matrix/media/v3/upload"
HOST = "synapse:8008"

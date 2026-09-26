"""Format metadata for validated new images and legacy PNG checkpoints."""


def extension(data):
    # Generation validates decoded content before storage. Existing images and
    # checkpoints are PNG; a WebP container has RIFF and WEBP signatures.
    return 'webp' if data[:4]==b'RIFF' and data[8:12]==b'WEBP' else 'png'


def media_type(data):
    return 'image/'+extension(data)


def path_media_type(path):
    return 'image/webp' if path.endswith('.webp') else 'image/png'

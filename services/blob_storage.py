"""Private Vercel Blob helpers for SIMTS certificate images.

Certificates are stored as private PNG/JPG images. The browser never receives
the private Blob URL directly. Flask authenticates to Vercel Blob and serves
the image inline through the certificate route.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from werkzeug.utils import secure_filename


class BlobStorageError(RuntimeError):
    pass


def blob_enabled():
    """Return True when either token-based or Vercel OIDC Blob access is available."""
    return bool(
        (os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip()
        or (
            (os.getenv("BLOB_STORE_ID") or "").strip()
            and (os.getenv("VERCEL_OIDC_TOKEN") or "").strip()
        )
    )


def _client():
    if not blob_enabled():
        raise BlobStorageError("Vercel Blob storage is not configured.")
    try:
        from vercel.blob import BlobClient
    except ImportError as exc:
        raise BlobStorageError(
            "The Vercel Python SDK is not installed. Add vercel>=0.5.0 to requirements.txt."
        ) from exc
    return BlobClient()


def upload_image(file_storage, prefix, base_name):
    """Upload a PNG/JPEG certificate image as a private Blob."""
    filename = secure_filename(base_name)
    ext = Path(filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg"}:
        raise BlobStorageError("Only PNG and JPG/JPEG certificate images are allowed.")

    content_type = "image/png" if ext == ".png" else "image/jpeg"
    body = file_storage.read()
    file_storage.stream.seek(0)

    if not body:
        raise BlobStorageError("The certificate image is empty.")

    try:
        uploaded = _client().put(
            f"simts/{prefix}/{filename}",
            body,
            access="private",
            content_type=content_type,
            add_random_suffix=True,
            multipart=len(body) >= 5 * 1024 * 1024,
        )
        return uploaded.url
    except Exception as exc:
        raise BlobStorageError(f"Vercel Blob upload failed: {exc}") from exc


def delete_file(file_reference):
    """Delete a private Blob URL when the certificate is removed."""
    if (
        not file_reference
        or not blob_enabled()
        or not str(file_reference).startswith(("http://", "https://"))
    ):
        return

    try:
        _client().delete([str(file_reference)])
    except Exception as exc:
        raise BlobStorageError(f"Vercel Blob delete failed: {exc}") from exc


async def _write_blob_stream(stream, output_path):
    """Write the SDK's async byte stream to a local temporary file."""
    with output_path.open("wb") as out:
        async for chunk in stream:
            if chunk:
                out.write(chunk)


def download_to_temp(file_reference):
    """Read a private Blob through the official SDK into a temporary image file.

    This deliberately uses Vercel's ``get()`` SDK method instead of manually
    constructing an Authorization header. That supports both the traditional
    BLOB_READ_WRITE_TOKEN flow and Vercel's current OIDC authentication.
    """
    if not blob_enabled():
        raise BlobStorageError("Vercel Blob storage is not configured.")

    reference = str(file_reference or "").strip()
    if not reference.startswith(("http://", "https://")):
        raise BlobStorageError("Invalid private Blob URL.")

    fd, temp_name = tempfile.mkstemp(prefix="simts_cert_", suffix=".img")
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        try:
            from vercel.blob import get
        except ImportError as exc:
            raise BlobStorageError(
                "The Vercel Python SDK is not installed. Add vercel>=0.5.0 to requirements.txt."
            ) from exc

        result = get(reference, access="private")

        if result is None or getattr(result, "status_code", None) != 200:
            temp_path.unlink(missing_ok=True)
            status = getattr(result, "status_code", "not found")
            raise BlobStorageError(f"Vercel Blob returned status {status}.")

        stream = getattr(result, "stream", None)
        if stream is None:
            temp_path.unlink(missing_ok=True)
            raise BlobStorageError("Vercel Blob returned no image stream.")

        # The Python SDK exposes the response body as an AsyncIterator[bytes].
        asyncio.run(_write_blob_stream(stream, temp_path))

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            temp_path.unlink(missing_ok=True)
            raise BlobStorageError("Vercel Blob returned an empty image.")

        return temp_path

    except BlobStorageError:
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise BlobStorageError(f"Vercel Blob download failed: {exc}") from exc

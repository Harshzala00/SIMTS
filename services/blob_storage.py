"""Private Vercel Blob helpers for certificate images.

Certificates are stored as private PNG/JPG images. The browser never receives
a public Blob URL directly; the Flask route fetches the image with the Blob
token and serves it inline without a download attachment.
"""
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from werkzeug.utils import secure_filename


class BlobStorageError(RuntimeError):
    pass


def blob_enabled():
    return bool((os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip())


def _client():
    if not blob_enabled():
        raise BlobStorageError("Vercel Blob storage is not configured.")
    try:
        from vercel.blob import BlobClient
    except ImportError as exc:
        raise BlobStorageError("The Vercel Python SDK is not installed.") from exc
    return BlobClient()


def _blob_token():
    token = (os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip()
    if not token:
        raise BlobStorageError("BLOB_READ_WRITE_TOKEN is missing.")
    return token


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
    if not file_reference or not blob_enabled() or not str(file_reference).startswith("http"):
        return
    try:
        _client().delete([file_reference])
    except Exception as exc:
        raise BlobStorageError(f"Vercel Blob delete failed: {exc}") from exc


def download_to_temp(file_reference):
    """Authenticated private-Blob read, returning a temporary image path."""
    if not blob_enabled():
        raise BlobStorageError("Vercel Blob storage is not configured.")
    if not file_reference or not str(file_reference).startswith(("http://", "https://")):
        raise BlobStorageError("Invalid private Blob URL.")

    token = _blob_token()
    fd, temp_name = tempfile.mkstemp(prefix="simts_cert_", suffix=".img")
    os.close(fd)
    temp_path = Path(temp_name)
    request = Request(
        str(file_reference),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "image/png,image/jpeg,image/*;q=0.9,*/*;q=0.1",
            "User-Agent": "SIMTS/4.0 certificate reader",
        },
        method="GET",
    )
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as remote, temp_path.open("wb") as out:
                if getattr(remote, "status", 200) != 200:
                    raise BlobStorageError("Vercel Blob returned a non-success status.")
                while True:
                    chunk = remote.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            if temp_path.stat().st_size:
                return temp_path
            raise BlobStorageError("Vercel Blob returned an empty image.")
        except HTTPError as exc:
            if exc.code in (401, 403, 404):
                temp_path.unlink(missing_ok=True)
                raise BlobStorageError(f"Vercel Blob returned HTTP {exc.code}.") from exc
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        except BlobStorageError:
            temp_path.unlink(missing_ok=True)
            raise
        if attempt < 2:
            time.sleep(0.25 * (2 ** attempt))
    temp_path.unlink(missing_ok=True)
    raise BlobStorageError(f"Vercel Blob download failed: {last_error}") from last_error

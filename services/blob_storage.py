"""Private Vercel Blob helpers for SIMTS certificate images.

Certificates are stored as private PNG/JPG images. The browser never receives
the private Blob credential. Flask retrieves the object server-side and streams
the image through the public certificate-view route.
"""

import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen

from werkzeug.utils import secure_filename


class BlobStorageError(RuntimeError):
    pass


def blob_enabled():
    return bool((os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip())


def _token():
    token = (os.getenv("BLOB_READ_WRITE_TOKEN") or "").strip()
    if not token:
        raise BlobStorageError("BLOB_READ_WRITE_TOKEN is missing.")
    return token


def _client():
    if not blob_enabled():
        raise BlobStorageError("Vercel Blob storage is not configured.")
    try:
        from vercel.blob import BlobClient
    except ImportError as exc:
        raise BlobStorageError(
            "The Vercel Python SDK is not installed. Add vercel>=0.5.0 to requirements.txt."
        ) from exc
    return BlobClient(token=_token())


def upload_image(file_storage, prefix, base_name):
    """Upload a PNG/JPG certificate as a private Vercel Blob."""
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


def _with_cache_bypass(url):
    """Add Vercel's cache=0 flag without changing the blob pathname."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["cache"] = "0"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _download_http(file_reference, temp_path):
    """Read a private Blob using the documented Bearer-token HTTP interface."""
    token = _token()
    url = _with_cache_bypass(str(file_reference).strip())

    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "image/png,image/jpeg,image/*;q=0.9,*/*;q=0.1",
            "User-Agent": "SIMTS/5.0 private-certificate-reader",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )

    last_error = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as remote:
                status = getattr(remote, "status", 200)
                if status != 200:
                    raise BlobStorageError(
                        f"Vercel Blob returned HTTP {status} while reading certificate."
                    )

                with temp_path.open("wb") as out:
                    while True:
                        chunk = remote.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)

            if temp_path.stat().st_size > 0:
                return

            raise BlobStorageError("Vercel Blob returned an empty certificate image.")

        except HTTPError as exc:
            # 401/403/404 are definitive for this object/token. Do not retry.
            if exc.code in (401, 403, 404):
                raise BlobStorageError(
                    f"Vercel Blob returned HTTP {exc.code} for the certificate."
                ) from exc
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < 2:
            time.sleep(0.25 * (2 ** attempt))

    raise BlobStorageError(f"Vercel Blob download failed: {last_error}")


def _download_sdk(file_reference, temp_path):
    """Fallback using the official Python SDK get() method."""
    try:
        from vercel.blob import get
    except ImportError as exc:
        raise BlobStorageError(
            "The Vercel Python SDK is not installed."
        ) from exc

    try:
        result = get(
            str(file_reference).strip(),
            access="private",
            token=_token(),
        )
    except Exception as exc:
        raise BlobStorageError(f"Vercel Blob SDK read failed: {exc}") from exc

    if result is None:
        raise BlobStorageError("Vercel Blob could not find the certificate.")

    status = getattr(result, "status_code", None)
    if status != 200:
        raise BlobStorageError(
            f"Vercel Blob SDK returned HTTP {status} for the certificate."
        )

    stream = getattr(result, "stream", None)
    if stream is None:
        raise BlobStorageError("Vercel Blob SDK returned no certificate stream.")

    # SDK get() returns an async iterator. Run it only after all HTTP
    # fallbacks have been exhausted.
    import asyncio

    async def write_stream():
        with temp_path.open("wb") as out:
            async for chunk in stream:
                if chunk:
                    out.write(chunk)

    try:
        asyncio.run(write_stream())
    except RuntimeError as exc:
        # This should not happen in normal synchronous Flask requests, but
        # provide a clear error rather than returning a broken image.
        raise BlobStorageError(f"Could not read the Blob stream: {exc}") from exc

    if not temp_path.exists() or temp_path.stat().st_size == 0:
        raise BlobStorageError("Vercel Blob SDK returned an empty certificate.")


def download_to_temp(file_reference):
    """Return a local temporary copy of a private PNG/JPG certificate.

    The documented direct HTTP method is used first. If the Blob endpoint
    rejects that request, the official Python SDK get() method is attempted.
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
            _download_http(reference, temp_path)
            return temp_path
        except BlobStorageError as http_error:
            temp_path.unlink(missing_ok=True)
            try:
                _download_sdk(reference, temp_path)
                return temp_path
            except BlobStorageError as sdk_error:
                temp_path.unlink(missing_ok=True)
                raise BlobStorageError(
                    f"Private certificate read failed. HTTP method: {http_error}; "
                    f"SDK method: {sdk_error}"
                ) from sdk_error
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

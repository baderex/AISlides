import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from PIL import Image as PILImage
from pypdf import PdfReader

from app.config import settings
from app.lucide import bundled_icon, icon_png

OBJECT_PREFIX = "framecraft-objects/"
PILImage.MAX_IMAGE_PIXELS = 24_000_000


class Storage:
    def __init__(self):
        self.settings = settings()
        self.root = self.settings.local_storage_path
        self.s3 = None
        if self.settings.storage_backend == "local":
            (self.root / OBJECT_PREFIX).mkdir(parents=True, exist_ok=True)
        else:
            import boto3
            self.s3 = boto3.client("s3", endpoint_url=self.settings.s3_endpoint)

    def _key(self, key: str) -> str:
        return str(UUID(key))

    def put(self, key: str, raw: bytes, media_type: str):
        key = OBJECT_PREFIX + self._key(key)
        if self.s3:
            self.s3.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=raw, ContentType=media_type)
        else:
            temporary = self.root / (key + ".tmp")
            try:
                temporary.write_bytes(raw)
                temporary.replace(self.root / key)
            finally:
                temporary.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        if icon := bundled_icon(key):
            return icon_png(*icon)
        key = self._key(key)
        for path in (OBJECT_PREFIX + key, key):
            if self.s3:
                try:
                    with self.s3.get_object(Bucket=self.settings.s3_bucket, Key=path)["Body"] as body:
                        return body.read()
                except self.s3.exceptions.NoSuchKey:
                    continue
            try:
                return (self.root / path).read_bytes()
            except FileNotFoundError:
                continue
        raise FileNotFoundError(key)

    def delete(self, key: str):
        key = self._key(key)
        for path in (OBJECT_PREFIX + key, key):
            if self.s3:
                self.s3.delete_object(Bucket=self.settings.s3_bucket, Key=path)
            else:
                (self.root / path).unlink(missing_ok=True)

    def old_objects(self, cutoff):
        """Enumerate UUID objects in the studio storage namespace older than the grace period."""
        if self.s3:
            pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.settings.s3_bucket, Prefix=OBJECT_PREFIX)
            entries = ((item["Key"][len(OBJECT_PREFIX):], item["LastModified"].timestamp()) for page in pages for item in page.get("Contents", ()))
        else:
            entries = ((entry.name, entry.stat().st_mtime) for entry in (self.root / OBJECT_PREFIX).iterdir() if entry.is_file())
        for key, modified in entries:
            if modified >= cutoff:
                continue
            try:
                if not self.s3 and key.endswith(".tmp") and self._key(key[:-4]) == key[:-4]:
                    (self.root / OBJECT_PREFIX / key).unlink(missing_ok=True)
                elif self._key(key) == key:
                    yield key
            except ValueError:
                continue

    def check(self):
        if self.s3:
            self.s3.head_bucket(Bucket=self.settings.s3_bucket)
        else:
            if not os.access(self.root, os.R_OK | os.W_OK):
                raise OSError("Storage is not readable and writable")


def decode_upload(name, raw, declared):
    try:
        media_type, raw, extracted = parse_upload(name, raw)
    except (ValueError, OSError, subprocess.SubprocessError, BadZipFile) as error:
        raise ValueError("The file is invalid or uses an unsupported format.") from error
    if declared not in {"", "application/octet-stream", media_type, "text/markdown", "text/csv"}:
        if not (media_type == "image/png" and declared in {"image/jpeg", "image/webp"}):
            raise ValueError("The declared media type does not match the file.")
    return media_type, raw, extracted


def parse_upload(name: str, raw: bytes) -> tuple[str, bytes, str]:
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".pptx", ".docx"}:
        return inspect_upload(name, raw)
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result = subprocess.run([sys.executable, "-I", "-m", "app.document_parser", "upload" + suffix],
                            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                            check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    media_type, text = json.loads(result.stdout)
    return media_type, raw, text


def inspect_upload(name: str, raw: bytes) -> tuple[str, bytes, str]:
    extension = Path(name).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg", ".webp"}:
        if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")
                or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")):
            raise ValueError("Image signature is invalid.")
        with PILImage.open(io.BytesIO(raw)) as image:
            expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[extension]
            if image.format != expected or image.width * image.height > 24_000_000:
                raise ValueError("Image format or dimensions are invalid.")
            result = image.convert("RGBA")
            result.thumbnail((2400, 2400))
            output = io.BytesIO()
            result.save(output, format="PNG")
        return "image/png", output.getvalue(), ""
    if extension == ".pdf" and raw.startswith(b"%PDF-"):
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("PDF files must be unencrypted and contain at most 100 pages.")
        return "application/pdf", raw, "\n".join((page.extract_text() or "")[:3500] for page in reader.pages[:20])[:20000]
    if extension in {".pptx", ".docx"} and raw.startswith(b"PK\x03\x04"):
        return _office(extension, raw)
    if extension in {".txt", ".md", ".csv"}:
        text = raw.decode("utf-8-sig")
        if "\0" in text or any(ord(value) < 32 and value not in "\t\n\r" for value in text[:10000]):
            raise ValueError("Text upload contains binary data.")
        return "text/plain", raw, text[:20000]
    if extension == ".json":
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("A skill package must be a JSON object.")
        return "application/json", raw, ""
    raise ValueError("Supported uploads are PDF, PPTX, DOCX, plain text, CSV, PNG, JPEG, WebP and skill JSON.")


def _office(extension: str, raw: bytes) -> tuple[str, bytes, str]:
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 1500 or sum(e.file_size for e in entries) > 55_000_000:
            raise ValueError("Office archive exceeds extraction limits.")
        names = archive.namelist()
        root = "ppt/presentation.xml" if extension == ".pptx" else "word/document.xml"
        if root not in names or "[Content_Types].xml" not in names:
            raise BadZipFile("Office archive is missing required files.")
        targets = ([n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")][:30]
                   if extension == ".pptx" else [root])
        texts = []
        for target in targets:
            node = ElementTree.fromstring(archive.read(target))
            texts.extend(item.text for item in node.iter() if item.tag.endswith("}t") and item.text)
    suffix = "presentationml.presentation" if extension == ".pptx" else "wordprocessingml.document"
    return "application/vnd.openxmlformats-officedocument." + suffix, raw, "\n".join(texts)[:20000]

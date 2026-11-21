import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from threading import BoundedSemaphore

import pypdfium2 as pdfium
from pypdfium2.raw import FPDF_PAGEOBJ_TEXT

from app.config import settings
from app.domain import Document, Text
from app.rendering import flattened


class ReviewUnavailable(RuntimeError):
    pass


class ReviewBusy(RuntimeError):
    pass


review_slot = BoundedSemaphore()


def renderer() -> str | None:
    if configured := settings().powerpoint_renderer:
        return shutil.which(configured)
    if executable := shutil.which("soffice.com" if os.name == "nt" else "soffice") or shutil.which("libreoffice"):
        return executable
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            if root := os.environ.get(key):
                candidate = Path(root) / "LibreOffice/program/soffice.com"
                if candidate.is_file():
                    return str(candidate)
    return None


def render_powerpoint(executable: str, raw: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="framecraft-office-") as folder:
        work = Path(folder)
        profile = work / "profile"
        user = profile / "user"
        user.mkdir(parents=True)
        (user / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>', encoding="utf-8")
        deck = work / "review.pptx"
        deck.write_bytes(raw)
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PROGRAMDATA", "SYSTEMDRIVE", "LANG", "LC_ALL"}}
        env.update({key: folder for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR")})
        try:
            subprocess.run([executable, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
                            "--convert-to", "pdf:impress_pdf_Export", "--outdir", folder, str(deck)],
                           cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=settings().powerpoint_review_timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            pdf = deck.with_suffix(".pdf")
            if pdf.stat().st_size > 75_000_000:
                raise ReviewUnavailable("The rendered PDF exceeds the review size limit.")
            return pdf.read_bytes()
        except subprocess.TimeoutExpired as error:
            raise ReviewUnavailable("PowerPoint rendering timed out. Try a smaller deck or download it directly.") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise ReviewUnavailable("LibreOffice could not render this PowerPoint file.") from error


@dataclass(frozen=True)
class TextRun:
    text: str
    box: tuple[float, float, float, float]
    font: str


def normalized(value):
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).casefold() if ch.isalnum())


def coverage(expected, actual):
    words = Counter(expected)
    return sum((words & Counter(actual)).values()) / max(1, len(expected))


def area(box):
    left, bottom, right, top = box
    return max(0, right - left) * max(0, top - bottom)


def intersection(a, b):
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def page_findings(page, slide):
    issues = []

    def report(code, message):
        if len(issues) < 12:
            issues.append({"code": code, "message": message})

    width, height = page.get_size()
    with closing(page.get_textpage()) as text:
        runs = [TextRun(obj.extract().strip(), obj.get_bounds(), obj.get_font().get_family_name())
                for obj in islice(page.get_objects(filter=[FPDF_PAGEOBJ_TEXT], textpage=text), 800)]
        runs = [run for run in runs if run.text and area(run.box) > 0]
        for element, x, y, w, h in flattened(slide.elements):
            if not isinstance(element, Text) or not (expected := normalized(element.text)) or element.opacity == 0:
                continue
            if element.rotation % 360:
                continue
            box = (x * width - 2, (1 - y - h) * height - 2, (x + w) * width + 2, (1 - y) * height + 2)
            label = " ".join(element.text.split())[:82]
            local_text = normalized(text.get_text_bounded(*box))
            if expected not in local_text and coverage(expected, local_text) < .94:
                report("text-fit", f"Some text is missing from its frame or could be clipped: {label}")
            words = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", element.text).casefold()))
            matched = [run for run in runs if intersection(run.box, box) >= .8 * area(run.box)
                       and words.intersection(re.findall(r"[^\W_]+", run.text.casefold()))]
            actual_fonts = {run.font for run in matched if run.font}
            expected_face = normalized(element.font)
            if expected_face and actual_fonts and not any(normalized(face).startswith(expected_face) for face in actual_fonts):
                report("font-substitution", f"{element.font} rendered as {', '.join(sorted(actual_fonts))}: {label}")
            full_line = [run for run in matched if normalized(run.text) == expected]
            if len(full_line) == 1:
                left, _, right, _ = full_line[0].box
                anchors = {"left": (left, x * width), "center": ((left + right) / 2, (x + w / 2) * width),
                           "right": (right, (x + w) * width)}
                actual, intended = anchors[element.align]
                if abs(actual - intended) > max(14, .04 * w * width):
                    report("text-alignment", f"Rendered text shifted from its frame alignment: {label}")

    slide_box = (-1, -1, width + 1, height + 1)
    if any(intersection(run.box, slide_box) < .97 * area(run.box) for run in runs):
        report("off-slide", "Rendered text extends past the slide edge.")
    runs.sort(key=lambda run: run.box[0])
    for index, first in enumerate(runs):
        for following in range(index + 1, len(runs)):
            second = runs[following]
            if second.box[0] >= first.box[2]:
                break
            if intersection(first.box, second.box) >= .4 * min(area(first.box), area(second.box)):
                report("text-overlap", f"Rendered text overlaps: {first.text[:44]} / {second.text[:44]}")
                return issues
    return issues


def inspect_pdf(raw: bytes, document: Document) -> list[dict]:
    with pdfium.PdfDocument(raw) as pdf:
        if len(pdf) != len(document.slides):
            raise ReviewUnavailable("The rendered page count differs from the PowerPoint slide count.")
        results = []
        for index, slide in enumerate(document.slides):
            with closing(pdf[index]) as page:
                with closing(page.render(scale=1000 / max(page.get_size()))) as bitmap:
                    output = io.BytesIO()
                    bitmap.to_pil().convert("RGB").save(output, format="JPEG", quality=86)
                    image = base64.b64encode(output.getvalue()).decode("ascii")
                results.append({"number": index + 1, "title": slide.title, "image": image,
                                "issues": page_findings(page, slide)})
        return results


def review_powerpoint(raw: bytes, document: Document) -> list[dict]:
    executable = renderer()
    if not executable:
        raise ReviewUnavailable("Install LibreOffice Impress or configure POWERPOINT_RENDERER to review exported decks.")
    if not review_slot.acquire(blocking=False):
        raise ReviewBusy("Another PowerPoint review is running. Try again in a moment.")
    try:
        return inspect_pdf(render_powerpoint(executable, raw), document)
    except pdfium.PdfiumError as error:
        raise ReviewUnavailable("The rendered PowerPoint could not be inspected.") from error
    finally:
        review_slot.release()

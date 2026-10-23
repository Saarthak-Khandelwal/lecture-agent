"""Find diagrams in lecture files and prepare them as embeddable images.

PPTX  : slides that look like they contain diagrams/charts/pictures are rendered to PNG
        with LibreOffice (best, captures shapes, SmartArt and charts). If LibreOffice is
        not installed, embedded pictures are extracted instead (shapes/charts are missed).
PDF   : pages with images or lots of vector drawing are rendered to PNG.
Images: uploaded image files are offered as figures as they are.

The model sees small thumbnails of the candidates and decides which ones to embed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from PIL import Image

from .config import Config
from .extractors import IMAGE_EXT
from .extractors.pptx_extractor import embedded_pictures, slide_infos
from .lectures import Lecture

log = logging.getLogger(__name__)

THUMB_PX = 768
MIN_EMBEDDED_PX = 120       # ignore icons and bullets
MAX_SLIDES_PER_PICTURE = 2  # a picture repeated on more slides is probably a logo


@dataclass
class Figure:
    id: str
    path: Path
    label: str               # human label used in captions, e.g. "Slide 5"
    hint: str = ""           # slide title or first text line, shown to the model
    score: int = 0
    in_prompt: bool = True   # False for uploaded image files (already sent as sources)
    source_name: str | None = None


def slug(text: str, n: int = 24) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "file"


def _libreoffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


# ---------------------------------------------------------------- PPTX

def _render_pptx(cfg: Config, path: Path, out_dir: Path) -> list[Figure] | None:
    """Render candidate slides with LibreOffice. Returns None if it can't be done."""
    exe = _libreoffice()
    if not exe:
        return None
    infos = slide_infos(path)
    visible = [i for i in infos if not i.hidden]  # LibreOffice skips hidden slides when exporting
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "profile"
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless",
               "--convert-to", "pdf", "--outdir", tmp, str(path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except Exception as e:
            log.warning("LibreOffice conversion failed for %s: %s", path.name, e)
            return None
        pdf = Path(tmp) / f"{path.stem}.pdf"
        if not pdf.exists():
            return None
        figs: list[Figure] = []
        with fitz.open(pdf) as doc:
            if len(doc) != len(visible):
                log.warning("Slide count mismatch rendering %s (%d vs %d), using embedded pictures",
                            path.name, len(doc), len(visible))
                return None
            for page, info in zip(doc, visible):
                if not info.candidate:
                    continue
                fid = f"{slug(path.stem)}-s{info.no:02d}"
                dest = out_dir / f"{fid}.png"
                page.get_pixmap(matrix=fitz.Matrix(cfg.fig_zoom, cfg.fig_zoom)).save(dest)
                figs.append(Figure(fid, dest, f"Slide {info.no}", info.title, info.score))
        return figs


def _embedded_pptx(path: Path, out_dir: Path) -> list[Figure]:
    infos = {i.no: i for i in slide_infos(path)}
    items = list(embedded_pictures(path))
    hashes = [hashlib.sha1(blob).hexdigest() for _, _, blob, _, _ in items]
    slides_per_hash: dict[str, set[int]] = {}
    for (no, *_), h in zip(items, hashes):
        slides_per_hash.setdefault(h, set()).add(no)

    figs: list[Figure] = []
    seen: set[str] = set()
    for (no, idx, blob, ext, (w, h_px)), h in zip(items, hashes):
        if h in seen or len(slides_per_hash[h]) > MAX_SLIDES_PER_PICTURE:
            continue
        if w < MIN_EMBEDDED_PX or h_px < MIN_EMBEDDED_PX or infos[no].hidden:
            continue
        try:
            img = Image.open(io.BytesIO(blob))
            img.load()
        except Exception:  # emf/wmf and other formats Pillow can't read
            continue
        seen.add(h)
        fid = f"{slug(path.stem)}-s{no:02d}-img{idx}"
        dest = out_dir / f"{fid}.png"
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(dest)
        figs.append(Figure(fid, dest, f"Slide {no}", infos[no].title, 3 + infos[no].score))
    return figs


def _from_pptx(cfg: Config, path: Path, out_dir: Path) -> list[Figure]:
    if not any(i.candidate and not i.hidden for i in slide_infos(path)):
        return []
    if cfg.fig_libreoffice:
        figs = _render_pptx(cfg, path, out_dir)
        if figs is not None:
            return figs
        log.info("LibreOffice not available or failed, extracting embedded pictures from %s", path.name)
    return _embedded_pptx(path, out_dir)


# ---------------------------------------------------------------- PDF

def _from_pdf(cfg: Config, path: Path, out_dir: Path) -> list[Figure]:
    figs: list[Figure] = []
    with fitz.open(path) as doc:
        for no, page in enumerate(doc, start=1):
            n_img = len(page.get_images(full=True))
            n_draw = len(page.get_drawings()) if n_img == 0 else 0
            if n_img == 0 and n_draw < 12:
                continue
            first_line = next((ln.strip() for ln in page.get_text().splitlines() if ln.strip()), "")[:80]
            fid = f"{slug(path.stem)}-p{no:02d}"
            dest = out_dir / f"{fid}.png"
            page.get_pixmap(matrix=fitz.Matrix(cfg.fig_zoom, cfg.fig_zoom)).save(dest)
            figs.append(Figure(fid, dest, f"Page {no}", first_line, n_img * 2 + min(n_draw, 30) // 3))
    return figs


# ---------------------------------------------------------------- public API

def build_figures(cfg: Config, lecture: Lecture) -> list[Figure]:
    """Create figure images for a lecture. Safe to call repeatedly (output folder is rebuilt)."""
    if not cfg.figures_enabled:
        return []
    out_dir = cfg.figures_dir / slug(lecture.unit, 40) / slug(lecture.name, 40)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    figs: list[Figure] = []
    for f in lecture.files:
        ext = f.suffix.lower()
        try:
            if ext == ".pptx":
                figs += _from_pptx(cfg, f, out_dir)
            elif ext == ".pdf":
                figs += _from_pdf(cfg, f, out_dir)
            elif ext in IMAGE_EXT:
                figs.append(Figure(f"file-{slug(f.stem)}", f, "Uploaded image", f.name,
                                   in_prompt=False, source_name=f.name))
        except Exception:
            log.exception("Figure extraction failed for %s", f.name)

    candidates = [x for x in figs if x.in_prompt]
    if len(candidates) > cfg.fig_max_candidates:
        keep = {x.id for x in sorted(candidates, key=lambda x: -x.score)[:cfg.fig_max_candidates]}
        figs = [x for x in figs if not x.in_prompt or x.id in keep]
    ids = Counter(x.id for x in figs)
    if any(c > 1 for c in ids.values()):  # two files with the same stem, keep first of each id
        seen, uniq = set(), []
        for x in figs:
            if x.id not in seen:
                seen.add(x.id)
                uniq.append(x)
        figs = uniq
    log.info("Found %d candidate figure(s)", len([x for x in figs if x.in_prompt]))
    return figs


def thumbnail(fig: Figure) -> tuple[str, str]:
    """(media_type, base64) small JPEG for sending to the model."""
    img = Image.open(fig.path)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    img = img.convert("RGB")
    img.thumbnail((THUMB_PX, THUMB_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()

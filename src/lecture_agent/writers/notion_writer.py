from __future__ import annotations

import io
import logging
import mimetypes
from datetime import date
from pathlib import Path

import httpx
from notion_client import Client
from PIL import Image

from ..config import Config
from ..figures import Figure
from ..lectures import Lecture
from ..render import render_body
from .md_to_notion import rich_text, to_blocks

log = logging.getLogger(__name__)
BATCH = 100  # Notion allows at most 100 child blocks per request
API = "https://api.notion.com/v1"
MAX_UPLOAD = 4_500_000  # stay under the 5 MiB limit of free workspaces


def make_client(cfg: Config) -> Client:
    return Client(auth=cfg.notion_token, notion_version="2022-06-28")


def _props(cfg: Config, lecture: Lecture, notes: dict) -> dict:
    p = cfg.notion_props
    props: dict = {}
    if p.get("title"):
        props[p["title"]] = {"title": [{"type": "text", "text": {"content": notes["title"][:2000]}}]}
    if p.get("unit"):
        props[p["unit"]] = {"select": {"name": lecture.unit[:100]}}
    if p.get("lecture"):
        props[p["lecture"]] = {"rich_text": [{"type": "text", "text": {"content": lecture.name[:2000]}}]}
    if p.get("tags"):
        props[p["tags"]] = {"multi_select": [{"name": t[:100].replace(",", "")} for t in notes.get("tags", [])]}
    if p.get("date"):
        props[p["date"]] = {"date": {"start": date.today().isoformat()}}
    return props


def _upload_bytes(path: Path) -> tuple[bytes, str, str]:
    data = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "image/png"
    name = path.name
    if len(data) > MAX_UPLOAD:  # shrink big photos to a JPEG
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((2200, 2200))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data, ctype, name = buf.getvalue(), "image/jpeg", path.stem + ".jpg"
    return data, ctype, name


def upload_image(cfg: Config, path: Path) -> str:
    """Upload a local image with Notion's File Upload API. Returns the file_upload id."""
    data, ctype, name = _upload_bytes(path)
    headers = {"Authorization": f"Bearer {cfg.notion_token}", "Notion-Version": "2022-06-28"}
    with httpx.Client(timeout=60) as http:
        r = http.post(f"{API}/file_uploads", headers=headers, json={"filename": name, "content_type": ctype})
        r.raise_for_status()
        upload_id = r.json()["id"]
        r = http.post(f"{API}/file_uploads/{upload_id}/send", headers=headers,
                      files={"file": (name, data, ctype)})
        r.raise_for_status()
    return upload_id


def _figure_resolver(cfg: Config, figures: list[Figure]):
    by_id = {f.id: f for f in figures}
    used: set[str] = set()

    def resolve(fid: str, caption: str) -> dict | None:
        fig = by_id.get(fid)
        if fig is None or fid in used:
            return None
        used.add(fid)
        cap = f"Figure: {caption} ({fig.label})" if caption else f"Figure ({fig.label})"
        caption_rt = rich_text(f"<small>{cap}</small>")
        try:
            upload_id = upload_image(cfg, fig.path)
        except Exception:
            log.exception("Could not upload figure %s to Notion", fid)
            return {"object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": rich_text(f"<small>Figure not uploaded: {cap}</small>")}}
        return {"object": "block", "type": "image",
                "image": {"type": "file_upload", "file_upload": {"id": upload_id}, "caption": caption_rt}}

    return resolve


def publish(cfg: Config, lecture: Lecture, notes: dict, previous_page_id: str | None = None,
            figures: list[Figure] | None = None) -> str:
    """Create the Notion page (archiving the previous version if there was one). Returns page id."""
    client = make_client(cfg)
    blocks = to_blocks(render_body(notes, include_title=False), _figure_resolver(cfg, figures or []))

    page = client.pages.create(
        parent={"database_id": cfg.notion_database_id},
        properties=_props(cfg, lecture, notes),
        children=blocks[:BATCH],
    )
    page_id = page["id"]
    for i in range(BATCH, len(blocks), BATCH):
        client.blocks.children.append(block_id=page_id, children=blocks[i:i + BATCH])

    if previous_page_id:
        try:
            client.pages.update(page_id=previous_page_id, archived=True)
        except Exception:
            log.warning("Could not archive previous Notion page %s", previous_page_id)
    return page_id


def create_database(cfg: Config, parent_page_id: str, title: str = "Lectures") -> str:
    """Create a database with the properties the agent expects. Returns database id.

    Uses the REST API directly (pinned to Notion-Version 2022-06-28) because recent
    notion-client releases changed databases.create and drop the `properties` field.
    """
    p = cfg.notion_props
    props: dict = {p.get("title") or "Name": {"title": {}}}
    if p.get("unit"):
        props[p["unit"]] = {"select": {}}
    if p.get("lecture"):
        props[p["lecture"]] = {"rich_text": {}}
    if p.get("tags"):
        props[p["tags"]] = {"multi_select": {}}
    if p.get("date"):
        props[p["date"]] = {"date": {}}
    headers = {"Authorization": f"Bearer {cfg.notion_token}", "Notion-Version": "2022-06-28"}
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": props,
    }
    r = httpx.post(f"{API}/databases", headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Notion refused to create the database ({r.status_code}): {r.text}")
    return r.json()["id"]


def verify_database(cfg: Config) -> str | None:
    """None if NOTION_DATABASE_ID is a database the integration can use, else a helpful message."""
    headers = {"Authorization": f"Bearer {cfg.notion_token}", "Notion-Version": "2022-06-28"}
    db_id = cfg.notion_database_id
    try:
        r = httpx.get(f"{API}/databases/{db_id}", headers=headers, timeout=30)
        if r.status_code == 200:
            return None
        try:
            msg = r.json().get("message", "")
        except Exception:
            msg = r.text
        if r.status_code == 400 and "is a page" in msg:
            hint = ""
            c = httpx.get(f"{API}/blocks/{db_id}/children?page_size=100", headers=headers, timeout=30)
            if c.status_code == 200:
                for b in c.json().get("results", []):
                    if b.get("type") == "child_database":
                        hint = f" That page contains a database, so use this line in .env instead: NOTION_DATABASE_ID={b['id']}"
                        break
            return ("NOTION_DATABASE_ID is a page id, not a database id." +
                    (hint or " Run `lecture-agent init-notion <page_id>` and put the id it prints in .env."))
        if r.status_code in (401, 403):
            return "Notion rejected the token. Check NOTION_TOKEN in .env."
        if r.status_code == 404:
            return ("Notion cannot find that database. Open it in Notion, click ... then Connections, "
                    "and add your integration.")
        return f"Notion check failed ({r.status_code}): {msg}"
    except httpx.HTTPError as e:
        return f"Could not reach Notion: {e}"

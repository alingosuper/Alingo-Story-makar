"""FastHTML web app for alingo_video_story with SSE progress."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import datetime
import json
import logging
import os
import queue
import threading
import uuid
from pathlib import Path

from fasthtml.common import *  # noqa: F403, F405
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, StreamingResponse

from .components import (
    center_panel,
    character_card,
    character_edit_form,
    chapter_group_html,
    compact_scene_card,
    delete_storyboard_button,
    landing_page,
    left_sidebar,
    mobile_tab_bar,
    new_project_page,
    progress_message,
    prompt_section,
    save_notice,
    scene_card,
    scene_inspector,
    sidebar_chapters_content,
    sidebar_characters_content,
    site_header,
    sse_container,
    story_comparison,
    storyboard_list,
    storyboard_view,
)
from .AlinGo-Prompt_config import configure_AlinGo-Prompt
from .models import Chapter, Storyboard
from .pipeline import (
    run_chapter_continuation_pipeline,
    run_chapter_rewrite,
    run_continuation_pipeline,
    run_modify_pipeline,
    run_pipeline,
    run_rewrite,
    run_story_continuation,
)
from .video import (
    WAN_AVAILABLE,
    combine_videos_to_queue,
    combined_video_path,
    delete_all_videos,
    delete_scene_video,
    generate_video_to_queue,
    pipeline_loaded,
    unload_pipeline,
    video_exists,
    video_path_for,
)
from .video_fal import generate_all_to_queue as fal_generate_all
from .video_fal import generate_video_to_queue as fal_generate
from .video_hailuo import generate_all_to_queue
from .video_hailuo import generate_video_to_queue as hailuo_generate
from .video_venice import generate_all_to_queue as venice_generate_all
from .video_venice import generate_video_to_queue as venice_generate
from .video_meshwell import generate_all_to_queue as meshwell_generate_all
from .video_meshwell import generate_video_to_queue as meshwell_generate
from .video_meshwell import fetch_asset_thumbnail, asset_detail_url, MESHWELL_BASE
from .video_modal import MODAL_AVAILABLE, modal_available
from .video_modal import generate_video_to_queue as modal_generate
from .video_modal import generate_all_to_queue as modal_generate_all
from .segmentation import (
    SAM2_AVAILABLE,
    segment_dir_for,
    segment_scene_to_queue,
    segmentation_available,
)
from .compositor import (
    CharacterLayer,
    compose_scene_to_queue,
    composite_dir_for,
    composite_video_path,
    list_available_characters,
)

logging.basicConfig(
    level=os.environ.get("S2V_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
logging.getLogger("python_multipart").setLevel(logging.WARNING)

configure_AlinGo-Prompt()

STATIC_DIR = Path(__file__).parent / "static"
SAMPLE_STORY = Path(__file__).parent.parent.parent / "sample_story.txt"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = str(DATA_DIR / "alingo_video_story.db")
IMAGES_DIR = DATA_DIR / "images"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Temporary storage for stories between POST /generate|continue and GET /run
# Values are either a plain story string, or a (story, existing_storyboard, row_id) tuple for continuations
_pending: dict[str, str | tuple[str, Storyboard, int]] = {}

# Temporary storage for rewrite jobs between POST trigger and GET /rewrite-run
_pending_rewrite: dict[str, tuple[str, str]] = {}  # job_id -> (story, instruction)

# Temporary storage for modify jobs between POST trigger and GET /modify-run
_pending_modify: dict[
    str, tuple[Storyboard, str, set[int] | None, int]
] = {}  # job_id -> (storyboard, instruction, scene_numbers, row_id)

# Temporary storage for video generation jobs between POST trigger and GET /video-run
_pending_video: dict[
    str, tuple[str, int, int, str, int | None, str | None, int]
] = {}  # job_id -> (prompt, storyboard_id, scene_number, backend, version, first_frame_image, duration_seconds)

# Temporary storage for generate-all jobs
_pending_video_all: dict[
    str, tuple[list[tuple[int, str, int | None]], int, int]
] = {}  # job_id -> (scenes, storyboard_id, duration_seconds)

# Temporary storage for generate-all backend selection
_pending_video_all_backend: dict[str, str] = {}  # job_id -> backend

# Temporary storage for meshwell scene objects (richer data for 3D generation)
_pending_meshwell_scene: dict[str, object] = {}  # job_id -> alingo_video_story Scene

# Temporary storage for combine jobs
_pending_combine: dict[str, tuple[int, list[Path]]] = {}  # job_id -> (storyboard_id, video_paths)

# Temporary storage for chapter-level operations
_pending_chapter_continue: dict[
    str, tuple[str, Storyboard, int, int]
] = {}  # job_id -> (story, storyboard, chapter_number, row_id)
_pending_chapter_auto_continue: dict[
    str, tuple[str, int, str, Storyboard, int, int]
] = {}  # job_id -> (story_text, num_scenes, instruction, storyboard, chapter_number, row_id)
_pending_chapter_rewrite: dict[
    str, tuple[Storyboard, int, str, int]
] = {}  # job_id -> (storyboard, chapter_number, instruction, row_id)
_pending_chapter_modify: dict[
    str, tuple[Storyboard, str, int, int]
] = {}  # job_id -> (storyboard, instruction, chapter_number, row_id)
_pending_add_chapter: dict[str, tuple[str, Storyboard, int]] = {}  # job_id -> (story, storyboard, row_id)

# Cancel events for stoppable video generation jobs
_cancel_events: dict[str, threading.Event] = {}  # job_id -> Event

_css_mtime = int(Path(__file__).parent.joinpath("static", "style.css").stat().st_mtime)

_AUTH_SKIP = [
    r'/login', r'/register', r'/logout',
    r'/style\.css.*', r'/keywords\.js.*', r'/favicon\.ico',
]


def _auth_before(req, session):
    user_id = session.get('user_id')
    if not user_id:
        return RedirectResponse('/login', status_code=303)
    storyboards.xtra(user_id=user_id)
    req.scope['user_id'] = user_id


app, rt, storyboards, StoryboardRow = fast_app(
    DB_PATH,
    before=Beforeware(_auth_before, skip=_AUTH_SKIP),
    pico=False,
    hdrs=(
        Link(rel="stylesheet", href=f"/style.css?v={_css_mtime}", type="text/css"),
        Script(src="https://unpkg.com/htmx-ext-sse@2.2.2/sse.js"),
        Script(src="/keywords.js"),
    ),
    static_path=str(STATIC_DIR),
    id=int,
    storyboard_title=str,
    story_text=str,
    storyboard_json=str,
    created_at=str,
    user_id=int,
    pk="id",
)

# --- Users table & auth routes ---
from fastlite import database as _database  # noqa: E402

from .auth import User, register_auth_routes  # noqa: E402

_db = _database(DB_PATH)
users = _db.create(User, if_not_exists=True)
register_auth_routes(rt, users)


def _get_username(session) -> str:
    """Look up the current user's username from session."""
    uid = session.get('user_id')
    if uid:
        try:
            return users[uid].username
        except Exception:
            pass
    return ""


def _migrate_unclaimed_storyboards():
    """Assign storyboards with user_id=0 or NULL to the first registered user."""
    first = users(limit=1)
    if first:
        for sb_row in storyboards(where="user_id IS NULL OR user_id=0"):
            storyboards.update(id=sb_row.id, user_id=first[0].id)


_migrate_unclaimed_storyboards()


def _sync_meshwell_ids(sb: Storyboard, scene) -> None:
    """Copy meshwell_asset_id from scene characters back to storyboard-level characters."""
    if not hasattr(scene, "characters"):
        return
    for scene_char in scene.characters:
        aid = getattr(scene_char, "meshwell_asset_id", "")
        if not aid:
            continue
        name_lower = scene_char.name.lower()
        for sb_char in sb.characters:
            if sb_char.name.lower() == name_lower and not sb_char.meshwell_asset_id:
                sb_char.meshwell_asset_id = aid
                sb.propagate_character(sb_char)
                break


def _load(row_id: int) -> Storyboard | None:
    """Load a storyboard from the DB by row ID. Returns None if not found."""
    try:
        row = storyboards[row_id]
    except Exception:
        return None
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    _ensure_chapter_story_text(sb, row)
    sb.ensure_characters()
    return sb


def _save_storyboard(sb: Storyboard, row_id: int):
    """Save storyboard to DB, concatenating chapter story_texts into the DB story_text column."""
    row = storyboards[row_id]
    combined_text = "\n\n---\n\n".join(ch.story_text for ch in sb.chapters if ch.story_text)
    storyboards.update(
        StoryboardRow(
            id=row_id,
            storyboard_title=sb.title,
            story_text=combined_text or row.story_text,
            storyboard_json=json.dumps(sb.to_dict()),
            created_at=row.created_at,
        )
    )


def _ensure_chapter_story_text(sb: Storyboard, row) -> bool:
    """Migrate: populate single chapter's story_text from DB column if empty."""
    if len(sb.chapters) == 1 and not sb.chapters[0].story_text and row.story_text:
        sb.chapters[0].story_text = row.story_text
        return True
    return False


def _render_chapters_html(sb: Storyboard, storyboard_id: int) -> str:
    """Render all chapter groups as HTML (used by multiple routes)."""
    return "".join(chapter_group_html(ch, sb, storyboard_id) for ch in sb.chapters)


@rt("/")
def get(session):
    rows = storyboards(order_by="-id")
    return Title("Alingo Video Story"), Div(site_header(active="projects", username=_get_username(session)), landing_page(saved_rows=rows), cls="app-shell")


@rt("/new")
def get(session):  # noqa: F811
    """New project page with textarea form."""
    return Title("New Project — Alingo Video Story"), Div(
        site_header(username=_get_username(session)),
        new_project_page(),
        cls="app-shell",
    )


@rt("/sample")
def get():  # noqa: F811
    text = SAMPLE_STORY.read_text().strip() if SAMPLE_STORY.is_file() else ""
    return Textarea(
        text,
        name="story",
        id="story-input",
        placeholder="Paste your story here...",
        rows=12,
    )


@rt("/generate")
def post(story: str, num_scenes: str = "5-10"):  # noqa: F811
    """Accept story text, stash it, return SSE-wired container."""
    story = story.strip()
    if not story:
        return P("Please enter a story first.", cls="error")
    story_id = str(uuid.uuid4())
    _pending[story_id] = {"story": story, "num_scenes": num_scenes}
    return sse_container(story_id)


@rt("/run")
async def get(id: str):  # noqa: F811
    """SSE endpoint — streams pipeline progress as each step completes."""
    pending = _pending.pop(id, None)
    if not pending:

        async def error_stream():
            yield f"event: progress\ndata: {progress_message('error', 'Story not found — please resubmit.')}\n\n"
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    # Detect whether this is a continuation or a fresh generation
    is_continuation = isinstance(pending, tuple)
    if is_continuation:
        story, existing_sb, row_id = pending
        num_scenes = "5-10"
    elif isinstance(pending, dict):
        story = pending["story"]
        num_scenes = pending.get("num_scenes", "5-10")
        existing_sb = None
        row_id = None
    else:
        story = pending
        num_scenes = "5-10"
        existing_sb = None
        row_id = None

    # Queue lets the sync pipeline generator feed events to the async SSE stream
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            if is_continuation:
                gen = run_continuation_pipeline(story, existing_sb)
            else:
                gen = run_pipeline(story, num_scenes=num_scenes)
            for event in gen:
                q.put(event)
        except Exception as e:
            log.exception("Pipeline error")
            msg = str(e)
            if hasattr(e, "__cause__") and e.__cause__:
                msg = f"{msg}\n\nCaused by: {e.__cause__}"
            q.put(("error", msg, None))
        q.put(None)  # sentinel

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        storyboard = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, sb = item
            if sb is not None:
                storyboard = sb
            html = progress_message(step, message)
            yield f"event: progress\ndata: {html}\n\n"

            # Send live preview whenever the storyboard updates
            if sb is not None:
                yield _sse_event("result", storyboard_view(storyboard))

        # Save completed storyboard to DB
        if storyboard is not None:
            # Populate story_text on the chapter
            if is_continuation and storyboard.chapters:
                new_ch = storyboard.chapters[-1]
                if not new_ch.story_text:
                    new_ch.story_text = story
            elif storyboard.chapters:
                if not storyboard.chapters[0].story_text:
                    storyboard.chapters[0].story_text = story

            if is_continuation and row_id is not None:
                _save_storyboard(storyboard, row_id)
                yield _sse_event("result", storyboard_view(storyboard) + save_notice(row_id))
            else:
                row = storyboards.insert(
                    StoryboardRow(
                        storyboard_title=storyboard.title,
                        story_text=story,
                        storyboard_json=json.dumps(storyboard.to_dict()),
                        created_at=datetime.datetime.now().isoformat(),
                    )
                )
                yield _sse_event("result", storyboard_view(storyboard) + save_notice(row.id))

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboards")
def get(session):  # noqa: F811
    """List all saved storyboards, newest first."""
    rows = storyboards(order_by="-id")
    return Title("Saved Storyboards — Alingo Video Story"), Div(
        site_header(active="projects", username=_get_username(session)),
        Div(
            P(A("← Back to generator", href="/", cls="nav-link")),
            H1("Story", Span("2", cls="accent"), "Video"),
            H2("Saved Storyboards"),
            storyboard_list(rows),
            cls="container",
        ),
        cls="app-shell",
    )


@rt("/storyboards/combine")
def post(storyboard_ids: list[str] = None):  # noqa: F811
    """Combine selected storyboards into one with chapters."""
    if not storyboard_ids or len(storyboard_ids) < 2:
        return Response("Select at least 2 storyboards to combine.", status_code=400)

    ids = [int(sid) for sid in storyboard_ids]
    rows_data = []
    for sid in ids:
        try:
            row = storyboards[sid]
            sb = Storyboard.from_dict(json.loads(row.storyboard_json))
            rows_data.append((row, sb))
        except Exception:
            return Response(f"Storyboard {sid} not found.", status_code=404)

    # Build combined chapters with renumbered chapter_number and scene_number.
    # Track video file mappings so we can carry them over.
    combined_chapters: list[Chapter] = []
    global_scene_num = 1
    chapter_num = 1
    combined_texts = []
    # (old_storyboard_id, old_scene_number, version) → new_scene_number
    video_mappings: list[tuple[int, int, int | None, int]] = []

    for row, sb in rows_data:
        _ensure_chapter_story_text(sb, row)
        for ch in sb.chapters:
            new_scenes = []
            for s in ch.scenes:
                old_scene_num = s.scene_number
                new_scene_num = global_scene_num
                # Track all video files for this scene (versioned + unversioned)
                ver = s.active_version if s.active_version else None
                if ver and video_exists(row.id, old_scene_num, ver):
                    video_mappings.append((row.id, old_scene_num, ver, new_scene_num))
                if video_exists(row.id, old_scene_num):
                    video_mappings.append((row.id, old_scene_num, None, new_scene_num))
                s.scene_number = global_scene_num
                global_scene_num += 1
                new_scenes.append(s)
            new_ch = Chapter(
                chapter_number=chapter_num,
                title=ch.title or sb.title,
                summary=ch.summary or sb.story_summary,
                scenes=new_scenes,
                story_text=ch.story_text or row.story_text,
            )
            combined_chapters.append(new_ch)
            chapter_num += 1
        combined_texts.append(row.story_text)

    combined_sb = Storyboard(
        title=rows_data[0][1].title,
        story_summary=rows_data[0][1].story_summary,
        chapters=combined_chapters,
        global_art_style=rows_data[0][1].global_art_style,
        global_color_palette=list(rows_data[0][1].global_color_palette),
    )

    # Save new combined row
    new_row = storyboards.insert(
        StoryboardRow(
            storyboard_title=combined_sb.title,
            story_text="\n\n---\n\n".join(combined_texts),
            storyboard_json=json.dumps(combined_sb.to_dict()),
            created_at=datetime.datetime.now().isoformat(),
        )
    )

    # Copy video files from originals to the new storyboard
    import shutil

    new_video_dir = video_path_for(new_row.id, 1).parent
    new_video_dir.mkdir(parents=True, exist_ok=True)
    for old_sb_id, old_scene, old_ver, new_scene in video_mappings:
        src = video_path_for(old_sb_id, old_scene, old_ver)
        dst = video_path_for(new_row.id, new_scene, old_ver)
        if src.is_file():
            shutil.copy2(src, dst)

    # Delete originals and their videos
    for sid in ids:
        delete_all_videos(sid)
        storyboards.delete(sid)

    return Response(headers={"HX-Redirect": f"/storyboard/{new_row.id}"})


def _gpu_status_btn():
    """Render the GPU model unload button (only when local backend is available)."""
    if not WAN_AVAILABLE:
        return ""
    loaded = pipeline_loaded()
    if loaded:
        return Div(
            Button(
                "Unload video model (free GPU)",
                hx_post="/gpu-unload",
                hx_target="#gpu-status",
                hx_swap="outerHTML",
                cls="btn btn-secondary btn-small",
            ),
            id="gpu-status",
            style="margin-bottom: 0.5rem;",
        )
    return Div(
        Small("Video model not loaded", style="color: var(--text-secondary);"),
        id="gpu-status",
        style="margin-bottom: 0.5rem;",
    )


@rt("/gpu-unload")
def post():  # noqa: F811
    """Unload the local video pipeline from GPU memory."""
    unloaded = unload_pipeline()
    msg = "Model unloaded — GPU memory freed." if unloaded else "No model was loaded."
    return Div(
        Small(msg, style="color: var(--text-secondary);"),
        id="gpu-status",
        style="margin-bottom: 0.5rem;",
    )


_MOBILE_TABS = (
    '<input type="radio" name="mobile-tab" id="mobile-tab-chapters" class="mobile-tab-input">'
    '<input type="radio" name="mobile-tab" id="mobile-tab-scenes" class="mobile-tab-input" checked>'
    '<input type="radio" name="mobile-tab" id="mobile-tab-inspector" class="mobile-tab-input">'
)


@rt("/storyboard/{id}")
def get(id: int, session):  # noqa: F811
    """Detail view for a single saved storyboard — three-panel workspace."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    migrated = _ensure_chapter_story_text(sb, row)
    if sb.ensure_characters():
        migrated = True
    if migrated:
        _save_storyboard(sb, id)

    # Auto-select first scene for inspector
    first_scene = sb.scenes[0] if sb.scenes else None
    prev_has_vid = False

    return Title(f"{sb.title} — Alingo Video Story"), Div(
        site_header(username=_get_username(session)),
        NotStr(_MOBILE_TABS),
        Div(
            Div(NotStr(left_sidebar(sb, id)), cls="panel-left"),
            Div(
                _gpu_status_btn(),
                NotStr(center_panel(sb, id)),
                NotStr(delete_storyboard_button(id)),
                cls="panel-center",
            ),
            Div(NotStr(scene_inspector(first_scene, sb, id, prev_has_vid)), id="scene-inspector", cls="panel-right"),
            NotStr(mobile_tab_bar()),
            cls="workspace",
        ),
        cls="app-shell",
    )


# --- Sidebar tab routes ---


@rt("/storyboard/{id}/sidebar/chapters")
def get(id: int):  # noqa: F811
    """Return chapters tab content for the sidebar."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    return NotStr(sidebar_chapters_content(sb, id))


@rt("/storyboard/{id}/sidebar/characters")
def get(id: int):  # noqa: F811
    """Return characters tab content for the sidebar."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    return NotStr(sidebar_characters_content(sb, id))


@rt("/storyboard/{id}/character/{idx}/edit")
def get(id: int, idx: int):  # noqa: F811
    """Return inline edit form for a character."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    if idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')
    return NotStr(character_edit_form(sb.characters[idx], idx, id))


@rt("/storyboard/{id}/character/{idx}/card")
def get(id: int, idx: int):  # noqa: F811
    """Return read-only character card (cancel edit)."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    if idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')
    return NotStr(character_card(sb.characters[idx], idx, id))


@rt("/storyboard/{id}/character/{idx}/save")
def post(id: int, idx: int, name: str, role: str, appearance: str, description: str, meshwell_asset_id: str = ""):  # noqa: F811
    """Save character edits and propagate to all scenes."""
    from .models import Character as CharModel

    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    if idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    updated = CharModel(
        name=sb.characters[idx].name,  # keep original name (no rename)
        role=role.strip(),
        appearance=appearance.strip(),
        description=description.strip(),
        meshwell_asset_id=meshwell_asset_id.strip(),
    )
    sb.characters[idx] = updated
    count = sb.propagate_character(updated)
    _save_storyboard(sb, id)
    log.info("Character '%s' updated, propagated to %d scenes", updated.name, count)
    return NotStr(character_card(updated, idx, id))


@rt("/storyboard/{id}/character/{idx}/meshwell-asset")
def delete(id: int, idx: int):  # noqa: F811
    """Unlink a meshwell 3D asset from a character."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    if idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    sb.characters[idx].meshwell_asset_id = ""
    count = sb.propagate_character(sb.characters[idx])
    _save_storyboard(sb, id)
    log.info("Unlinked meshwell asset from '%s', propagated to %d scenes", sb.characters[idx].name, count)
    return NotStr(character_card(sb.characters[idx], idx, id))


@rt("/storyboard/{id}/character/{idx}/assign-segment")
def get(id: int, idx: int):  # noqa: F811
    """Return a picker showing available segmented characters to assign."""
    sb = _load(id)
    if sb is None or idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    characters = list_available_characters(id)
    if not characters:
        return NotStr('<p class="segment-empty">No segmented characters available. Extract characters from a scene first.</p>')

    options = []
    for c in characters:
        ref = f"scene_{c['scene_number']}/char_{c['object_id']}"
        gif_url = f"/segment/{id}/{c['scene_number']}/{c['object_id']}/preview"
        preview = ""
        if c.get("preview_gif"):
            preview = f'<img src="{gif_url}" class="segment-picker-thumb" />'
        options.append(
            f'<div class="segment-picker-option"'
            f' hx-post="/storyboard/{id}/character/{idx}/link-segment?ref={ref}"'
            f' hx-target="#character-card-{idx}" hx-swap="outerHTML"'
            f' title="Assign {ref}">'
            f'{preview}'
            f'<span>Scene {c["scene_number"]} / Char {c["object_id"]}<br>'
            f'<small>{c["num_frames"]} frames</small></span>'
            f'</div>'
        )

    return NotStr(
        f'<div class="segment-picker">'
        f'<div class="segment-picker-label">Select an extracted character:</div>'
        f'<div class="segment-picker-grid">{"".join(options)}</div>'
        f'</div>'
    )


@rt("/storyboard/{id}/character/{idx}/link-segment")
def post(id: int, idx: int, ref: str = ""):  # noqa: F811
    """Link a segmentation to a character."""
    sb = _load(id)
    if sb is None or idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    sb.characters[idx].segment_ref = ref
    sb.propagate_character(sb.characters[idx])
    _save_storyboard(sb, id)
    log.info("Linked segment '%s' to character '%s'", ref, sb.characters[idx].name)
    return NotStr(character_card(sb.characters[idx], idx, id))


@rt("/storyboard/{id}/character/{idx}/segment-ref")
def delete(id: int, idx: int):  # noqa: F811
    """Unlink a segmentation from a character."""
    sb = _load(id)
    if sb is None or idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    sb.characters[idx].segment_ref = ""
    sb.propagate_character(sb.characters[idx])
    _save_storyboard(sb, id)
    log.info("Unlinked segment from character '%s'", sb.characters[idx].name)
    return NotStr(character_card(sb.characters[idx], idx, id))


@rt("/meshwell/asset/{asset_id}/thumbnail")
def get(asset_id: str):  # noqa: F811
    """Proxy meshwell asset thumbnail to avoid CORS issues."""
    from starlette.responses import Response
    img = fetch_asset_thumbnail(asset_id)
    if img:
        return Response(content=img, media_type="image/png")
    # Return a 1x1 transparent PNG on failure
    return Response(status_code=404)


# --- Meshwell 3D asset generation jobs ---
_pending_3d_gen: dict[str, tuple[int, int]] = {}  # job_id -> (storyboard_id, char_index)
_pending_3d_gen_all: dict[str, int] = {}  # job_id -> storyboard_id


@rt("/storyboard/{id}/character/{idx}/generate-3d")
def post(id: int, idx: int):  # noqa: F811
    """Trigger 3D asset generation for a single character — returns SSE progress."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    if idx < 0 or idx >= len(sb.characters):
        return NotStr('<div class="sidebar-empty">Character not found.</div>')

    char = sb.characters[idx]
    if char.meshwell_asset_id:
        return NotStr(character_card(char, idx, id))

    job_id = str(uuid.uuid4())
    _pending_3d_gen[job_id] = (id, idx)

    import html as html_mod
    name = html_mod.escape(char.name)
    return NotStr(
        f'<div class="character-card" id="character-card-{idx}"'
        f' hx-ext="sse" sse-connect="/meshwell-3d-run?id={job_id}" sse-close="close">'
        f'<div class="character-card-header">'
        f'<span class="character-card-name">{name}</span>'
        f'<span class="character-card-role" style="color:var(--accent)">GENERATING</span>'
        f'</div>'
        f'<div class="character-3d-progress" sse-swap="meshwell-3d-progress" hx-swap="innerHTML">'
        f'Starting...</div>'
        f'<div sse-swap="meshwell-3d-card" hx-swap="outerHTML" hx-target="#character-card-{idx}"'
        f' style="display:none"></div>'
        f'</div>'
    )


@rt("/meshwell-3d-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for single character 3D asset generation."""
    from .video_meshwell import _ensure_asset

    job = _pending_3d_gen.pop(id, None)
    if not job:
        async def error_stream():
            yield _sse_event("meshwell-3d-progress", '<span class="error">Job not found.</span>')
            yield "event: close\ndata: \n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    storyboard_id, char_idx = job
    row = storyboards[storyboard_id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()
    char = sb.characters[char_idx]

    q: queue.Queue = queue.Queue()

    def worker():
        asset_id = _ensure_asset(char, q)
        if asset_id:
            q.put(("done", asset_id))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, message = item

            if phase == "done":
                # Save the asset link back to storyboard
                row_fresh = storyboards[storyboard_id]
                sb_fresh = Storyboard.from_dict(json.loads(row_fresh.storyboard_json))
                sb_fresh.ensure_characters()
                sb_fresh.characters[char_idx].meshwell_asset_id = message
                sb_fresh.propagate_character(sb_fresh.characters[char_idx])
                _save_storyboard(sb_fresh, storyboard_id)

                card_html = character_card(sb_fresh.characters[char_idx], char_idx, storyboard_id)
                yield _sse_event("meshwell-3d-card", card_html)
                yield "event: close\ndata: \n\n"
                return
            elif phase == "error":
                yield _sse_event("meshwell-3d-progress", f'<span class="error">{message}</span>')
            else:
                yield _sse_event("meshwell-3d-progress", message)

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/characters/generate-all-3d")
def post(id: int):  # noqa: F811
    """Trigger 3D asset generation for all characters without assets."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()

    unlinked = [(i, c) for i, c in enumerate(sb.characters) if not c.meshwell_asset_id]
    if not unlinked:
        return NotStr(sidebar_characters_content(sb, id))

    job_id = str(uuid.uuid4())
    _pending_3d_gen_all[job_id] = id

    progress_html = (
        f'<div class="character-list" id="characters-tab-content"'
        f' hx-ext="sse" sse-connect="/meshwell-3d-run-all?id={job_id}" sse-close="close">'
        f'<div class="character-3d-progress" sse-swap="meshwell-3d-all-progress" hx-swap="innerHTML">'
        f'Generating 3D assets for {len(unlinked)} characters...</div>'
        f'<div sse-swap="meshwell-3d-all-done" hx-swap="outerHTML" hx-target="#characters-tab-content"'
        f' style="display:none"></div>'
        f'</div>'
    )
    return NotStr(progress_html)


@rt("/meshwell-3d-run-all")
async def get(id: str):  # noqa: F811
    """SSE endpoint for generating all character 3D assets."""
    from .video_meshwell import _ensure_asset

    storyboard_id = _pending_3d_gen_all.pop(id, None)
    if storyboard_id is None:
        async def error_stream():
            yield _sse_event("meshwell-3d-all-progress", '<span class="error">Job not found.</span>')
            yield "event: close\ndata: \n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    row = storyboards[storyboard_id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.ensure_characters()

    unlinked = [(i, c) for i, c in enumerate(sb.characters) if not c.meshwell_asset_id]
    q: queue.Queue = queue.Queue()

    def worker():
        for num, (char_idx, char) in enumerate(unlinked, 1):
            q.put(("progress", f"({num}/{len(unlinked)}) Generating: {char.name}..."))
            asset_id = _ensure_asset(char, q)
            if asset_id:
                # Save incrementally so progress persists even if interrupted
                row_fresh = storyboards[storyboard_id]
                sb_fresh = Storyboard.from_dict(json.loads(row_fresh.storyboard_json))
                sb_fresh.ensure_characters()
                sb_fresh.characters[char_idx].meshwell_asset_id = asset_id
                sb_fresh.propagate_character(sb_fresh.characters[char_idx])
                _save_storyboard(sb_fresh, storyboard_id)
                q.put(("char-done", f"{char.name}: ready ({asset_id})"))
            else:
                q.put(("error", f"{char.name}: generation failed"))
        q.put(("all-done", ""))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        lines = []
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, message = item

            if phase == "all-done":
                # Re-render the full characters sidebar
                sb_fresh = Storyboard.from_dict(json.loads(storyboards[storyboard_id].storyboard_json))
                sb_fresh.ensure_characters()
                final = sidebar_characters_content(sb_fresh, storyboard_id)
                yield _sse_event("meshwell-3d-all-done", final)
                yield "event: close\ndata: \n\n"
                return
            elif phase == "char-done":
                lines.append(f'<div class="progress-line done">&#10004; {message}</div>')
            elif phase == "error":
                lines.append(f'<div class="progress-line">&#10060; {message}</div>')
            else:
                lines.append(f'<div class="progress-line">&#8226; {message}</div>')

            yield _sse_event("meshwell-3d-all-progress", "\n".join(lines))

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- Chapter-level routes ---


@rt("/storyboard/{id}/chapter/{ch}/continue")
def post(id: int, ch: int, story: str):  # noqa: F811
    """Continue an existing chapter — append scenes."""
    story = story.strip()
    if not story:
        return P("Please enter continuation text.", cls="error")
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    _ensure_chapter_story_text(sb, row)
    job_id = str(uuid.uuid4())
    _pending_chapter_continue[job_id] = (story, sb, ch, id)
    return Div(
        Div(
            sse_swap=f"ch-cont-progress-{ch}",
            hx_swap="beforeend",
            cls="video-progress-area",
            id=f"ch-cont-progress-{ch}",
        ),
        Div(sse_swap=f"ch-cont-result-{ch}", id=f"ch-cont-result-{ch}"),
        hx_ext="sse",
        sse_connect=f"/chapter-continue-run?id={job_id}",
        sse_close="close",
    )


@rt("/chapter-continue-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for chapter continuation."""
    job = _pending_chapter_continue.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("ch-cont-progress-0", progress_message("error", "Job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    story, sb, ch_num, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_chapter_continuation_pipeline(story, sb, ch_num):
                q.put(event)
        except Exception as e:
            log.exception("Chapter continuation pipeline error")
            q.put(("error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        storyboard = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, result_sb = item
            if result_sb is not None:
                storyboard = result_sb
            yield _sse_event(f"ch-cont-progress-{ch_num}", progress_message(step, message))
            if result_sb is not None:
                yield _sse_event(f"ch-cont-result-{ch_num}", storyboard_view(storyboard, storyboard_id=row_id))

        if storyboard is not None:
            _save_storyboard(storyboard, row_id)
            yield _sse_event(
                f"ch-cont-result-{ch_num}", storyboard_view(storyboard, storyboard_id=row_id) + save_notice(row_id)
            )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/chapter/{ch}/auto-continue")
def post(id: int, ch: int, num_scenes: int = 3, instruction: str = ""):  # noqa: F811
    """Auto-continue a chapter: generate story text then append scenes."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    _ensure_chapter_story_text(sb, row)
    chapter = sb.chapter_by_number(ch)
    story_text = chapter.story_text if chapter else row.story_text
    job_id = str(uuid.uuid4())
    _pending_chapter_auto_continue[job_id] = (story_text, num_scenes, instruction.strip(), sb, ch, id)
    return Div(
        Div(
            sse_swap=f"ch-auto-cont-progress-{ch}",
            hx_swap="beforeend",
            cls="video-progress-area",
            id=f"ch-auto-cont-progress-{ch}",
        ),
        Div(sse_swap=f"ch-auto-cont-result-{ch}", id=f"ch-auto-cont-result-{ch}"),
        hx_ext="sse",
        sse_connect=f"/chapter-auto-continue-run?id={job_id}",
        sse_close="close",
    )


@rt("/chapter-auto-continue-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for chapter auto-continue."""
    job = _pending_chapter_auto_continue.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("ch-auto-cont-progress-0", progress_message("error", "Job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    story_text, num_scenes, instruction, sb, ch_num, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            # Phase 1: Generate continuation story text
            cont_text = None
            for event in run_story_continuation(story_text, num_scenes, instruction):
                step, message, result = event
                q.put(("continue", step, message, None))
                if step == "done" and result is not None:
                    cont_text = result
            if cont_text is None:
                q.put(("pipeline", "error", "Story continuation produced no result", None))
                q.put(None)
                return
            q.put(("text", "done", cont_text, None))
            # Phase 2: Run chapter continuation pipeline
            for event in run_chapter_continuation_pipeline(cont_text, sb, ch_num):
                step, message, result_sb = event
                q.put(("pipeline", step, message, result_sb))
        except Exception as e:
            log.exception("Chapter auto-continue pipeline error")
            q.put(("pipeline", "error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        storyboard = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, step, message, result_sb = item
            if phase == "text":
                continue
            if phase == "pipeline" and result_sb is not None:
                storyboard = result_sb
            yield _sse_event(f"ch-auto-cont-progress-{ch_num}", progress_message(step, message))
            if result_sb is not None:
                yield _sse_event(f"ch-auto-cont-result-{ch_num}", storyboard_view(storyboard, storyboard_id=row_id))

        if storyboard is not None:
            _save_storyboard(storyboard, row_id)
            yield _sse_event(
                f"ch-auto-cont-result-{ch_num}", storyboard_view(storyboard, storyboard_id=row_id) + save_notice(row_id)
            )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/chapter/{ch}/modify")
def post(id: int, ch: int, instruction: str):  # noqa: F811
    """Apply a modification instruction to all scenes in a chapter."""
    instruction = instruction.strip()
    if not instruction:
        return P("Please enter a modification instruction.", cls="error")
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    job_id = str(uuid.uuid4())
    _pending_chapter_modify[job_id] = (sb, instruction, ch, id)
    return Div(
        Div(
            sse_swap=f"ch-modify-progress-{ch}",
            hx_swap="beforeend",
            cls="video-progress-area",
            id=f"ch-modify-progress-{ch}",
        ),
        Div(sse_swap=f"ch-modify-result-{ch}", id=f"ch-modify-result-{ch}"),
        hx_ext="sse",
        sse_connect=f"/chapter-modify-run?id={job_id}",
        sse_close="close",
    )


@rt("/chapter-modify-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for chapter modification."""
    job = _pending_chapter_modify.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("ch-modify-progress-0", progress_message("error", "Job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    sb, instruction, ch_num, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_modify_pipeline(sb, instruction, chapter_number=ch_num):
                q.put(event)
        except Exception as e:
            log.exception("Chapter modify pipeline error")
            q.put(("error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        final_sb = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, result_sb = item
            if result_sb is not None:
                final_sb = result_sb
            yield _sse_event(f"ch-modify-progress-{ch_num}", progress_message(step, message))

        if final_sb is not None:
            _save_storyboard(final_sb, row_id)
            # Replace compact scene cards via OOB swap
            chapter = final_sb.chapter_by_number(ch_num)
            if chapter:
                oob_cards = ""
                for s in chapter.scenes:
                    card_html = compact_scene_card(s, row_id)
                    oob_cards += card_html.replace(
                        f'id="scene-card-{s.scene_number}"',
                        f'id="scene-card-{s.scene_number}" hx-swap-oob="outerHTML:#scene-card-{s.scene_number}"',
                        1,
                    )
                done_msg = f'<p class="video-done-notice">&#10004; Chapter {ch_num} scenes updated.</p>'
                yield _sse_event(f"ch-modify-result-{ch_num}", done_msg + oob_cards)
            else:
                yield _sse_event(f"ch-modify-result-{ch_num}", storyboard_view(final_sb, storyboard_id=row_id))

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/chapter/{ch}/rewrite")
def post(id: int, ch: int, instruction: str):  # noqa: F811
    """Rewrite a chapter: rewrite story text and regenerate scenes."""
    instruction = instruction.strip()
    if not instruction:
        return P("Please enter a rewrite instruction.", cls="error")
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    _ensure_chapter_story_text(sb, row)
    job_id = str(uuid.uuid4())
    _pending_chapter_rewrite[job_id] = (sb, ch, instruction, id)
    return Div(
        Div(
            sse_swap=f"ch-rewrite-progress-{ch}",
            hx_swap="beforeend",
            cls="video-progress-area",
            id=f"ch-rewrite-progress-{ch}",
        ),
        Div(sse_swap=f"ch-rewrite-result-{ch}", id=f"ch-rewrite-result-{ch}"),
        hx_ext="sse",
        sse_connect=f"/chapter-rewrite-run?id={job_id}",
        sse_close="close",
    )


@rt("/chapter-rewrite-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for chapter rewrite + regeneration."""
    job = _pending_chapter_rewrite.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("ch-rewrite-progress-0", progress_message("error", "Job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    sb, ch_num, instruction, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_chapter_rewrite(sb, ch_num, instruction):
                q.put(event)
        except Exception as e:
            log.exception("Chapter rewrite pipeline error")
            q.put(("error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        storyboard = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, result_sb = item
            if result_sb is not None:
                storyboard = result_sb
            yield _sse_event(f"ch-rewrite-progress-{ch_num}", progress_message(step, message))

        if storyboard is not None:
            _save_storyboard(storyboard, row_id)
            yield _sse_event(
                f"ch-rewrite-result-{ch_num}", storyboard_view(storyboard, storyboard_id=row_id) + save_notice(row_id)
            )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/chapter/{ch}/snapshot-all")
def post(id: int, ch: int):  # noqa: F811
    """Create a new version for every scene in a chapter."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    chapter = sb.chapter_by_number(ch)
    if chapter is None:
        return Div("Chapter not found.", cls="error")
    for scene in chapter.scenes:
        scene.snapshot_version("snapshot for regeneration")
    _save_storyboard(sb, id)
    return NotStr(chapter_group_html(chapter, sb, id))


@rt("/storyboard/{id}/chapter/{ch}/version/{ver}")
def post(id: int, ch: int, ver: int):  # noqa: F811
    """Restore a chapter to a previous version."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    chapter = sb.chapter_by_number(ch)
    if chapter is None:
        return Div("Chapter not found.", cls="error")
    try:
        chapter.restore_version(ver)
    except ValueError as e:
        return Div(str(e), cls="error")
    _save_storyboard(sb, id)
    return NotStr(chapter_group_html(chapter, sb, id))


@rt("/storyboard/{id}/add-chapter")
def post(id: int, story: str):  # noqa: F811
    """Add a new chapter to the storyboard."""
    story = story.strip()
    if not story:
        return P("Please enter a story for the new chapter.", cls="error")
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    _ensure_chapter_story_text(sb, row)
    job_id = str(uuid.uuid4())
    _pending_add_chapter[job_id] = (story, sb, id)
    return Div(
        Div(sse_swap="add-chapter-progress", hx_swap="beforeend", cls="video-progress-area", id="add-chapter-progress"),
        Div(sse_swap="add-chapter-result", id="add-chapter-result"),
        hx_ext="sse",
        sse_connect=f"/add-chapter-run?id={job_id}",
        sse_close="close",
    )


@rt("/add-chapter-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for adding a new chapter (uses run_continuation_pipeline)."""
    job = _pending_add_chapter.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("add-chapter-progress", progress_message("error", "Job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    story, sb, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_continuation_pipeline(story, sb):
                q.put(event)
        except Exception as e:
            log.exception("Add chapter pipeline error")
            q.put(("error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        storyboard = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, result_sb = item
            if result_sb is not None:
                storyboard = result_sb
            yield _sse_event("add-chapter-progress", progress_message(step, message))
            if result_sb is not None:
                yield _sse_event("add-chapter-result", storyboard_view(storyboard))

        if storyboard is not None:
            # Set story_text on the new chapter
            new_chapter = storyboard.chapters[-1] if storyboard.chapters else None
            if new_chapter and not new_chapter.story_text:
                new_chapter.story_text = story
            _save_storyboard(storyboard, row_id)
            yield _sse_event(
                "add-chapter-result", storyboard_view(storyboard, storyboard_id=row_id) + save_notice(row_id)
            )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/chapter/{ch}/remove")
def delete(id: int, ch: int):  # noqa: F811
    """Remove a chapter from the storyboard. Scene numbers stay unchanged."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    sb.chapters = [c for c in sb.chapters if c.chapter_number != ch]
    if not sb.chapters:
        return Div("Cannot remove the last chapter.", cls="error")
    _save_storyboard(sb, id)
    return NotStr(_render_chapters_html(sb, id))


@rt("/storyboard/{id}/scene/{scene_number}/remove")
def delete(id: int, scene_number: int):  # noqa: F811
    """Remove a scene from its chapter. Removes the chapter too if empty (unless last)."""
    from starlette.responses import HTMLResponse

    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    # Find the chapter containing this scene
    target_ch = None
    for ch in sb.chapters:
        if any(s.scene_number == scene_number for s in ch.scenes):
            target_ch = ch
            break
    if target_ch is None:
        return Div("Scene not found.", cls="error")
    # Prevent deleting the very last scene in the storyboard
    total_scenes = sum(len(c.scenes) for c in sb.chapters)
    if total_scenes <= 1:
        return Div("Cannot delete the last remaining scene.", cls="error")
    # Remove the scene
    removed = [s for s in target_ch.scenes if s.scene_number == scene_number]
    target_ch.scenes = [s for s in target_ch.scenes if s.scene_number != scene_number]
    # If chapter is now empty, remove it (safe — we checked total > 1)
    if not target_ch.scenes:
        sb.chapters = [c for c in sb.chapters if c.chapter_number != target_ch.chapter_number]
    # Clean up video files for all versions
    for sc in removed:
        for sv in (sc.versions or []):
            delete_scene_video(id, scene_number, sv.version)
        delete_scene_video(id, scene_number)  # unversioned fallback
    # Clean up reference image
    img = _image_path(id, scene_number)
    if img.is_file():
        img.unlink()
    _save_storyboard(sb, id)
    chapters_html = _render_chapters_html(sb, id)
    oob_clear = '<div id="scene-inspector" hx-swap-oob="innerHTML:#scene-inspector"></div>'
    return HTMLResponse(chapters_html + oob_clear)


@rt("/storyboard/{id}/reorder-chapters")
async def post(id: int, req: Request):  # noqa: F811
    """Reorder chapters based on drag-and-drop. Accepts JSON {order: [ch_num, ...]}."""
    body = await req.json()
    new_order = body.get("order", [])
    if not new_order:
        return Response("No order provided.", status_code=400)
    new_order = [int(n) for n in new_order]

    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))

    # Build a lookup of current chapters by their number
    ch_by_num = {ch.chapter_number: ch for ch in sb.chapters}
    reordered = [ch_by_num[n] for n in new_order if n in ch_by_num]
    if len(reordered) != len(sb.chapters):
        return Response("Invalid chapter order.", status_code=400)

    # Renumber chapters sequentially
    for i, ch in enumerate(reordered, start=1):
        ch.chapter_number = i
    sb.chapters = reordered

    _save_storyboard(sb, id)
    return Response(status_code=200)


@rt("/storyboard/{id}/scene/{scene_number}/inspector")
def get(id: int, scene_number: int):  # noqa: F811
    """Return scene inspector HTML for the right sidebar."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return NotStr('<div class="inspector-empty">Scene not found</div>')
    prev_vid = _prev_scene_has_video(sb, scene_number, id)
    return NotStr(scene_inspector(scene, sb, id, prev_vid))


@rt("/storyboard/{id}/scene/{scene_number}/generate-video")
def post(id: int, scene_number: int, backend: str = "local", continue_from_prev: str = "", duration: str = "5"):  # noqa: F811
    """Trigger video generation for a scene — returns SSE-connected progress area."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None or scene.visual is None:
        return Div("Scene not found or has no visual prompt.", cls="error")

    prompt = scene.visual.video_gen_prompt
    version = scene.active_version if scene.active_version else None

    # Find previous scene's video path for first-frame chaining
    first_frame_image = None
    if continue_from_prev:
        prev_scene = None
        for i, s in enumerate(sb.scenes):
            if s.scene_number == scene_number and i > 0:
                prev_scene = sb.scenes[i - 1]
                break
        if prev_scene:
            from .video_hailuo import extract_last_frame_base64

            prev_ver = prev_scene.active_version if prev_scene.active_version else None
            prev_path = video_path_for(id, prev_scene.scene_number, prev_ver)
            if not prev_path.is_file():
                prev_path = video_path_for(id, prev_scene.scene_number)
            first_frame_image = extract_last_frame_base64(prev_path)

    # Use uploaded reference image if no first_frame from chaining
    if first_frame_image is None and scene.visual.reference_image:
        img_path = _image_path(id, scene_number)
        if img_path.is_file():
            import base64 as b64mod

            first_frame_image = f"data:image/png;base64,{b64mod.b64encode(img_path.read_bytes()).decode()}"

    duration_seconds = int(duration) if duration.isdigit() else 5
    job_id = str(uuid.uuid4())
    _pending_video[job_id] = (prompt, id, scene_number, backend, version, first_frame_image, duration_seconds)
    if backend == "meshwell":
        _pending_meshwell_scene[job_id] = scene

    return Div(
        Div(
            sse_swap="video-progress",
            hx_swap="innerHTML",
            cls="video-progress-area",
            id=f"video-progress-{scene_number}",
        ),
        Button(
            "Stop",
            cls="btn btn-danger btn-sm btn-stop-video",
            hx_post=f"/video-stop?id={job_id}",
            hx_swap="outerHTML",
        ),
        Div(sse_swap="video-result", id=f"video-result-{scene_number}"),
        hx_ext="sse",
        sse_connect=f"/video-run?id={job_id}",
        sse_close="close",
        id=f"video-section-{scene_number}",
        cls="video-section",
    )


@rt("/storyboard/{id}/scene/{scene_number}/update-prompt")
def post(id: int, scene_number: int, video_gen_prompt: str):  # noqa: F811
    """Persist an edited video_gen_prompt for a scene."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None or scene.visual is None:
        return Div("Scene not found.", cls="error")
    scene.visual.video_gen_prompt = video_gen_prompt
    # Also update the active version's visual so chevron navigation stays consistent
    if scene.versions and scene.active_version:
        active_sv = next((v for v in scene.versions if v.version == scene.active_version), None)
        if active_sv and active_sv.visual:
            active_sv.visual.video_gen_prompt = video_gen_prompt
    storyboards.update(
        StoryboardRow(
            id=id,
            storyboard_title=row.storyboard_title,
            story_text=row.story_text,
            storyboard_json=json.dumps(sb.to_dict()),
            created_at=row.created_at,
        )
    )
    return NotStr(prompt_section(video_gen_prompt, scene_number, storyboard_id=id, saved=True))


@rt("/rewrite")
def post(story: str, instruction: str):  # noqa: F811
    """Accept story text and rewrite instruction, return SSE container."""
    story = story.strip()
    instruction = instruction.strip()
    if not story:
        return P("Please enter a story first.", cls="error")
    if not instruction:
        return P("Please enter a rewrite instruction.", cls="error")
    job_id = str(uuid.uuid4())
    _pending_rewrite[job_id] = (story, instruction)
    return Div(
        Div(sse_swap="rewrite-progress", hx_swap="beforeend", cls="video-progress-area", id="rewrite-progress"),
        Div(sse_swap="rewrite-result", id="rewrite-result"),
        hx_ext="sse",
        sse_connect=f"/rewrite-run?id={job_id}",
        sse_close="close",
    )


@rt("/rewrite-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for story rewrite progress."""
    job = _pending_rewrite.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("rewrite-progress", progress_message("error", "Rewrite job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    story, instruction = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_rewrite(story, instruction):
                q.put(event)
        except Exception as e:
            log.exception("Rewrite pipeline error")
            q.put(("error", str(e), None))
        q.put(None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        rewritten = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, result = item
            if step == "done" and result is not None:
                rewritten = result
            yield _sse_event("rewrite-progress", progress_message(step, message))

        if rewritten is not None:
            yield _sse_event("rewrite-result", story_comparison(story, rewritten))

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/scene/{scene_number}/modify")
def post(id: int, scene_number: int, instruction: str):  # noqa: F811
    """Apply a modification instruction to a single scene."""
    instruction = instruction.strip()
    if not instruction:
        return P("Please enter a modification instruction.", cls="error")
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    job_id = str(uuid.uuid4())
    _pending_modify[job_id] = (sb, instruction, {scene_number}, id)
    return Div(
        Div(
            sse_swap="modify-progress",
            hx_swap="beforeend",
            cls="video-progress-area",
            id=f"modify-progress-{scene_number}",
        ),
        Div(sse_swap="modify-result", id=f"modify-result-{scene_number}"),
        hx_ext="sse",
        sse_connect=f"/modify-run?id={job_id}",
        sse_close="close",
    )


@rt("/storyboard/{id}/scene/{scene_number}/version/{version}")
def post(id: int, scene_number: int, version: int):  # noqa: F811
    """Switch a scene to a different version (restore), save to DB, return updated scene card."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return Div("Scene not found.", cls="error")
    try:
        scene.restore_version(version)
    except ValueError as e:
        return Div(str(e), cls="error")
    storyboards.update(
        StoryboardRow(
            id=id,
            storyboard_title=row.storyboard_title,
            story_text=row.story_text,
            storyboard_json=json.dumps(sb.to_dict()),
            created_at=row.created_at,
        )
    )
    prev_vid = _prev_scene_has_video(sb, scene_number, id)
    card_html = compact_scene_card(scene, id, is_selected=True)
    inspector_html = scene_inspector(scene, sb, id, prev_vid)
    oob_inspector = f'<div id="scene-inspector" hx-swap-oob="innerHTML:#scene-inspector">{inspector_html}</div>'
    return NotStr(card_html + oob_inspector)


@rt("/storyboard/{id}")
def delete(id: int, req: Request = None):  # noqa: F811
    """Delete a storyboard and all its videos."""
    delete_all_videos(id)
    storyboards.delete(id)
    # If request comes from project grid, just remove the card
    hx_target = req.headers.get("hx-target", "") if req else ""
    if hx_target.startswith("project-card-"):
        return ""
    return Response(headers={"HX-Redirect": "/"})


@rt("/storyboard/{id}/scene/{scene_number}/delete-video")
def delete(id: int, scene_number: int, version: int = 0):  # noqa: F811
    """Delete a scene's generated video file."""
    ver = version if version > 0 else None
    deleted = delete_scene_video(id, scene_number, ver)
    if not deleted:
        # Try unversioned fallback
        delete_scene_video(id, scene_number)
    # Re-render the full scene card (video section will now show generate buttons)
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return Div("Scene not found.", cls="error")
    # Return compact card retargeted + inspector OOB update
    from starlette.responses import HTMLResponse

    prev_vid = _prev_scene_has_video(sb, scene_number, id)
    card_html = compact_scene_card(scene, id, is_selected=True)
    inspector_html = scene_inspector(scene, sb, id, prev_vid)
    oob_inspector = f'<div id="scene-inspector" hx-swap-oob="innerHTML:#scene-inspector">{inspector_html}</div>'
    return HTMLResponse(
        card_html + oob_inspector,
        headers={"HX-Retarget": f"#scene-card-{scene_number}", "HX-Reswap": "outerHTML"},
    )


@rt("/modify-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for scene modification progress."""
    job = _pending_modify.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("modify-progress", progress_message("error", "Modify job not found."))
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    storyboard, instruction, scene_numbers, row_id = job
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for event in run_modify_pipeline(storyboard, instruction, scene_numbers):
                q.put(event)
        except Exception as e:
            log.exception("Modify pipeline error")
            msg = str(e)
            if hasattr(e, "__cause__") and e.__cause__:
                msg = f"{msg}\n\nCaused by: {e.__cause__}"
            q.put(("error", msg, None))
        q.put(None)  # sentinel

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        final_sb = None
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            step, message, sb = item
            if sb is not None:
                final_sb = sb
            yield _sse_event("modify-progress", progress_message(step, message))

        # Save updated storyboard to DB
        if final_sb is not None:
            row = storyboards[row_id]
            storyboards.update(
                StoryboardRow(
                    id=row_id,
                    storyboard_title=final_sb.title,
                    story_text=row.story_text,
                    storyboard_json=json.dumps(final_sb.to_dict()),
                    created_at=row.created_at,
                )
            )
            if scene_numbers is not None and len(scene_numbers) == 1:
                # Single-scene modify: replace compact card via OOB swap + update inspector
                sn = next(iter(scene_numbers))
                modified_scene = next((s for s in final_sb.scenes if s.scene_number == sn), None)
                if modified_scene:
                    card_html = compact_scene_card(modified_scene, row_id, is_selected=True)
                    oob_html = card_html.replace(
                        f'id="scene-card-{sn}"',
                        f'id="scene-card-{sn}" hx-swap-oob="outerHTML:#scene-card-{sn}"',
                        1,
                    )
                    # Also update inspector
                    inspector_html = scene_inspector(
                        modified_scene, final_sb, row_id, _prev_scene_has_video(final_sb, sn, row_id)
                    )
                    oob_html += f'<div id="scene-inspector" hx-swap-oob="innerHTML:#scene-inspector">{inspector_html}</div>'
                    done_msg = f'<p class="video-done-notice">&#10004; Scene {sn} updated.</p>'
                    yield _sse_event("modify-result", done_msg + oob_html)
                else:
                    yield _sse_event("modify-result", storyboard_view(final_sb, storyboard_id=row_id))
            else:
                # Modify-all: replace each compact card via OOB swap
                oob_cards = ""
                for s in final_sb.scenes:
                    card_html = compact_scene_card(s, row_id)
                    oob_cards += card_html.replace(
                        f'id="scene-card-{s.scene_number}"',
                        f'id="scene-card-{s.scene_number}" hx-swap-oob="outerHTML:#scene-card-{s.scene_number}"',
                        1,
                    )
                done_msg = '<p class="video-done-notice">&#10004; All scenes updated.</p>'
                yield _sse_event("modify-result", done_msg + oob_cards)

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/video-stop")
def post(id: str):  # noqa: F811
    """Signal a running video generation job to stop."""
    evt = _cancel_events.get(id)
    if evt:
        evt.set()
    return Div("Stopping...", cls="progress-line")


@rt("/video-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for video generation progress."""
    job = _pending_video.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("video-progress", '<div class="progress-line">&#10060; Video job not found.</div>')
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    prompt, storyboard_id, scene_number, backend, version, first_frame_image, duration_seconds = job
    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _cancel_events[id] = cancel_event

    def worker():
        try:
            if backend == "hailuo":
                hailuo_generate(
                    prompt,
                    storyboard_id,
                    scene_number,
                    q,
                    first_frame_image=first_frame_image,
                    version=version,
                    cancel_event=cancel_event,
                    duration_seconds=duration_seconds,
                )
            elif backend == "fal":
                fal_generate(
                    prompt,
                    storyboard_id,
                    scene_number,
                    q,
                    version=version,
                    cancel_event=cancel_event,
                    duration_seconds=duration_seconds,
                )
            elif backend == "venice":
                venice_generate(
                    prompt,
                    storyboard_id,
                    scene_number,
                    q,
                    first_frame_image=first_frame_image,
                    version=version,
                    cancel_event=cancel_event,
                    duration_seconds=duration_seconds,
                )
            elif backend == "modal":
                modal_generate(
                    prompt,
                    storyboard_id,
                    scene_number,
                    q,
                    first_frame_image=first_frame_image,
                    version=version,
                    cancel_event=cancel_event,
                    duration_seconds=duration_seconds,
                )
            elif backend == "meshwell":
                meshwell_scene = _pending_meshwell_scene.pop(id, None)
                meshwell_generate(
                    prompt,
                    storyboard_id,
                    scene_number,
                    q,
                    version=version,
                    cancel_event=cancel_event,
                    duration_seconds=duration_seconds,
                    scene=meshwell_scene,
                )
                # Save back any meshwell_asset_id links written to characters
                if meshwell_scene:
                    try:
                        row = storyboards[storyboard_id]
                        sb = Storyboard.from_dict(json.loads(row.storyboard_json))
                        _sync_meshwell_ids(sb, meshwell_scene)
                        _save_storyboard(sb, storyboard_id)
                    except Exception:
                        log.exception("Failed to save meshwell asset links")
            else:
                generate_video_to_queue(
                    prompt, storyboard_id, scene_number, q, version=version, duration_seconds=duration_seconds
                )
        finally:
            _cancel_events.pop(id, None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, message = item

            if phase == "done":
                # Replace progress with video player (cache-bust with mtime)
                ver_arg = version if version else None
                vpath = video_path_for(storyboard_id, scene_number, ver_arg)
                if not vpath.is_file():
                    vpath = video_path_for(storyboard_id, scene_number)
                mtime = int(vpath.stat().st_mtime) if vpath.is_file() else 0
                video_url = f"/video/{storyboard_id}/{scene_number}?t={mtime}"
                if version:
                    video_url = f"/video/{storyboard_id}/{scene_number}?version={version}&t={mtime}"
                player_html = (
                    f'<div class="video-player-wrapper">'
                    f'<video controls width="100%" src="{video_url}"></video>'
                    f"</div>"
                    f'<p class="video-done-notice">&#10004; Video generated successfully.</p>'
                )
                yield _sse_event("video-progress", "")
                yield _sse_event("video-result", player_html)
            elif phase == "cancelled":
                yield _sse_event(
                    "video-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#9632;</span> {message}</div>',
                )
            elif phase == "error":
                yield _sse_event("video-progress", f'<div class="progress-line">&#10060; {message}</div>')
            else:
                icons = {
                    "loading": "&#8987;",
                    "generating": "&#127916;",
                    "saving": "&#128190;",
                    "submitting": "&#128640;",
                    "polling": "&#8987;",
                    "downloading": "&#128190;",
                }
                icon = icons.get(phase, "&#8226;")
                yield _sse_event(
                    "video-progress",
                    f'<div class="progress-line"><span class="progress-icon">{icon}</span> {message}</div>',
                )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/generate-all-video")
def post(id: int, backend: str = "hailuo", duration: str = "5"):  # noqa: F811
    """Trigger sequential video generation for all scenes."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scenes = [
        (s.scene_number, s.visual.video_gen_prompt, s.active_version if s.active_version else None)
        for s in sb.scenes
        if s.visual
    ]
    if not scenes:
        return Div("No scenes with visual prompts found.", cls="error", id="generate-all-area")

    duration_seconds = int(duration) if duration.isdigit() else 5
    job_id = str(uuid.uuid4())
    _pending_video_all[job_id] = (scenes, id, duration_seconds)
    _pending_video_all_backend[job_id] = backend
    if backend == "meshwell":
        # Store full scene objects so meshwell can access character data
        _pending_meshwell_scene[job_id] = [s for s in sb.scenes if s.visual]

    return Div(
        Div(sse_swap="video-all-progress", hx_swap="innerHTML", cls="video-progress-area", id="video-all-progress"),
        Button(
            "Stop",
            cls="btn btn-danger btn-sm btn-stop-video",
            hx_post=f"/video-stop?id={job_id}",
            hx_swap="outerHTML",
        ),
        Div(sse_swap="video-all-result", id="video-all-result"),
        hx_ext="sse",
        sse_connect=f"/video-run-all?id={job_id}",
        sse_close="close",
        id="generate-all-area",
        cls="generate-all-section",
    )


@rt("/video-run-all")
async def get(id: str):  # noqa: F811
    """SSE endpoint for generate-all video progress."""
    job = _pending_video_all.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event(
                "video-all-progress", '<div class="progress-line">&#10060; Generate-all job not found.</div>'
            )
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    scenes, storyboard_id, duration_seconds = job
    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _cancel_events[id] = cancel_event

    def worker():
        try:
            backend = _pending_video_all_backend.pop(id, "hailuo")
            if backend == "fal":
                fal_generate_all(scenes, storyboard_id, q, cancel_event=cancel_event, duration_seconds=duration_seconds)
            elif backend == "venice":
                venice_generate_all(
                    scenes, storyboard_id, q, cancel_event=cancel_event, duration_seconds=duration_seconds
                )
            elif backend == "modal":
                modal_generate_all(
                    scenes, storyboard_id, q, cancel_event=cancel_event, duration_seconds=duration_seconds
                )
            elif backend == "meshwell":
                meshwell_scenes = _pending_meshwell_scene.pop(id, None)
                meshwell_generate_all(
                    scenes, storyboard_id, q, cancel_event=cancel_event, duration_seconds=duration_seconds,
                    scene_objects=meshwell_scenes,
                )
            else:
                generate_all_to_queue(
                    scenes, storyboard_id, q, cancel_event=cancel_event, duration_seconds=duration_seconds
                )
        finally:
            _cancel_events.pop(id, None)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, message = item

            if phase == "done":
                yield _sse_event(
                    "video-all-progress",
                    f'<div class="progress-line done"><span class="progress-icon">&#10004;</span> {message}</div>',
                )
                yield _sse_event(
                    "video-all-result",
                    '<p class="video-done-notice">&#10004; All videos generated. Reload to see them.</p>',
                )
            elif phase == "done-with-errors":
                yield _sse_event(
                    "video-all-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#9888;</span> {message}</div>',
                )
                yield _sse_event(
                    "video-all-result",
                    f'<p class="video-done-notice">&#9888; {message} Reload to see completed videos.</p>',
                )
            elif phase == "scene-done":
                scene_num = None
                # Extract scene number from message like "Scene 3 complete."
                for s_num, *_ in scenes:
                    if f"Scene {s_num}" in message:
                        scene_num = s_num
                        break
                result_html = (
                    f'<div class="progress-line done"><span class="progress-icon">&#10004;</span> {message}</div>'
                )
                if scene_num:
                    vp = video_path_for(storyboard_id, scene_num)
                    mt = int(vp.stat().st_mtime) if vp.is_file() else 0
                    result_html += (
                        f'<div class="video-player-wrapper">'
                        f'<video controls width="100%" src="/video/{storyboard_id}/{scene_num}?t={mt}"></video>'
                        f"</div>"
                    )
                yield _sse_event("video-all-progress", result_html)
            elif phase == "cancelled":
                yield _sse_event(
                    "video-all-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#9632;</span> {message}</div>',
                )
                yield _sse_event(
                    "video-all-result",
                    f'<p class="video-done-notice">&#9632; {message} Reload to see completed videos.</p>',
                )
            elif phase == "error":
                yield _sse_event(
                    "video-all-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#10060;</span> {message}</div>',
                )
            else:
                icons = {
                    "scene-start": "&#127916;",
                    "submitting": "&#128640;",
                    "polling": "&#8987;",
                    "downloading": "&#128190;",
                }
                icon = icons.get(phase, "&#8226;")
                yield _sse_event(
                    "video-all-progress",
                    f'<div class="progress-line"><span class="progress-icon">{icon}</span> {message}</div>',
                )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/video/{storyboard_id}/combined")
def get(storyboard_id: int):  # noqa: F811
    """Serve the combined video file."""
    path = combined_video_path(storyboard_id)
    if not path.is_file():
        return Response("Combined video not found", status_code=404)
    return FileResponse(str(path), media_type="video/mp4")


@rt("/video/{storyboard_id}/{scene_number}")
def get(storyboard_id: int, scene_number: int, version: int = 0):  # noqa: F811
    """Serve a generated video file."""
    ver = version if version > 0 else None
    path = video_path_for(storyboard_id, scene_number, ver)
    if not path.is_file():
        # Fall back to unversioned path for backward compat
        path = video_path_for(storyboard_id, scene_number)
    if not path.is_file():
        return Response("Video not found", status_code=404)
    return FileResponse(
        str(path),
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@rt("/storyboard/{id}/combine-videos")
def post(id: int):  # noqa: F811
    """Combine all scene videos into one file."""
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))

    # Collect video paths in scene order, preferring versioned then unversioned
    video_paths = []
    for s in sb.scenes:
        if not s.visual:
            continue
        ver = s.active_version if s.active_version else None
        path = video_path_for(id, s.scene_number, ver)
        if not path.is_file():
            path = video_path_for(id, s.scene_number)
        if path.is_file():
            video_paths.append(path)

    if len(video_paths) < 2:
        return Div("Need at least 2 scene videos to combine.", cls="error", id="combine-area")

    job_id = str(uuid.uuid4())
    _pending_combine[job_id] = (id, video_paths)

    return Div(
        Div(sse_swap="combine-progress", hx_swap="innerHTML", cls="video-progress-area", id="combine-progress"),
        Div(sse_swap="combine-result", id="combine-result"),
        hx_ext="sse",
        sse_connect=f"/combine-run?id={job_id}",
        sse_close="close",
        id="combine-area",
        cls="generate-all-section",
    )


@rt("/combine-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for video combine progress."""
    job = _pending_combine.pop(id, None)
    if not job:

        async def error_stream():
            yield _sse_event("combine-progress", '<div class="progress-line">&#10060; Combine job not found.</div>')
            yield "event: close\ndata: \n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    storyboard_id, video_paths = job
    q: queue.Queue = queue.Queue()

    def worker():
        combine_videos_to_queue(storyboard_id, video_paths, q)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            phase, message = item

            if phase == "done":
                result_html = (
                    f'<div class="progress-line done"><span class="progress-icon">&#10004;</span>'
                    f" Combined {len(video_paths)} videos successfully.</div>"
                )
                yield _sse_event("combine-progress", result_html)
                download_html = (
                    f'<div class="combine-result">'
                    f'<video controls width="100%" src="/video/{storyboard_id}/combined"></video>'
                    f'<a href="/video/{storyboard_id}/combined" class="btn btn-primary" download="combined.mp4">'
                    f"&#11015; Download combined video</a>"
                    f"</div>"
                )
                yield _sse_event("combine-result", download_html)
            elif phase == "error":
                yield _sse_event(
                    "combine-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#10060;</span> {message}</div>',
                )
            else:
                yield _sse_event(
                    "combine-progress",
                    f'<div class="progress-line"><span class="progress-icon">&#127910;</span> {message}</div>',
                )

        yield "event: close\ndata: \n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _prev_scene_has_video(sb: Storyboard, scene_number: int, storyboard_id: int) -> bool:
    """Check whether the scene before *scene_number* already has a generated video."""
    for i, s in enumerate(sb.scenes):
        if s.scene_number == scene_number and i > 0:
            ps = sb.scenes[i - 1]
            return video_exists(storyboard_id, ps.scene_number, ps.active_version or None) or video_exists(
                storyboard_id, ps.scene_number
            )
    return False


def _sse_event(event_name: str, data: str) -> str:
    """Format a multi-line SSE event. Each line gets its own data: prefix."""
    lines = data.split("\n")
    data_block = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event_name}\n{data_block}\n\n"


# --- Reference image helpers & routes ---


def _image_dir(storyboard_id: int) -> Path:
    """Return (and create) the images directory for a storyboard."""
    d = IMAGES_DIR / str(storyboard_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_path(storyboard_id: int, scene_number: int) -> Path:
    """Return the path for a scene's reference image."""
    return _image_dir(storyboard_id) / f"scene_{scene_number}_ref.png"


@rt("/image/{storyboard_id}/{scene_number}")
def get(storyboard_id: int, scene_number: int):  # noqa: F811
    """Serve a scene's uploaded reference image."""
    path = _image_path(storyboard_id, scene_number)
    if not path.is_file():
        return Response("Not found", status_code=404)
    return FileResponse(str(path), media_type="image/png")


@rt("/storyboard/{id}/scene/{scene_number}/upload-image")
async def post(id: int, scene_number: int, request: Request):  # noqa: F811
    """Upload a reference image for a scene (multipart form)."""
    from PIL import Image
    import io

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return Response("No file uploaded", status_code=400)

    # UploadFile or raw bytes — handle both
    if hasattr(upload, "read"):
        data = await upload.read()
    elif isinstance(upload, bytes):
        data = upload
    else:
        return Response("No file uploaded", status_code=400)

    if len(data) < 8:
        return Response("Please select an image file first", status_code=400)

    # Validate image magic bytes (PNG or JPEG)
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_jpeg = data[:2] == b"\xff\xd8"
    if not is_png and not is_jpeg:
        return Response("File must be PNG or JPEG", status_code=400)

    # Convert JPEG to PNG if needed
    if is_jpeg:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

    dest = _image_path(id, scene_number)
    dest.write_bytes(data)

    # Update model
    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene and scene.visual:
        scene.visual.reference_image = f"scene_{scene_number}_ref.png"
        storyboards.update({"storyboard_json": json.dumps(sb.to_dict())}, id)

    from .components import _video_section

    prev_has_video = False
    for i, s in enumerate(sb.scenes):
        if s.scene_number == scene_number and i > 0:
            prev_s = sb.scenes[i - 1]
            prev_ver = prev_s.active_version if prev_s.active_version else None
            prev_has_video = video_exists(id, prev_s.scene_number, prev_ver) or video_exists(id, prev_s.scene_number)
            break

    html = _video_section(scene, id, prev_has_video=prev_has_video)
    return Response(html, media_type="text/html")


@rt("/storyboard/{id}/scene/{scene_number}/delete-image")
def delete(id: int, scene_number: int):  # noqa: F811
    """Delete a scene's uploaded reference image."""
    path = _image_path(id, scene_number)
    if path.is_file():
        path.unlink()

    row = storyboards[id]
    sb = Storyboard.from_dict(json.loads(row.storyboard_json))
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene and scene.visual:
        scene.visual.reference_image = ""
        storyboards.update({"storyboard_json": json.dumps(sb.to_dict())}, id)

    from .components import _video_section

    prev_has_video = False
    for i, s in enumerate(sb.scenes):
        if s.scene_number == scene_number and i > 0:
            prev_s = sb.scenes[i - 1]
            prev_ver = prev_s.active_version if prev_s.active_version else None
            prev_has_video = video_exists(id, prev_s.scene_number, prev_ver) or video_exists(id, prev_s.scene_number)
            break

    html = _video_section(scene, id, prev_has_video=prev_has_video)
    return Response(html, media_type="text/html")


# --- Character segmentation (SAM2) ---
_pending_segment: dict[str, tuple[int, int, Path, list[dict]]] = {}
_active_segment_cancel: dict[str, threading.Event] = {}


@rt("/storyboard/{id}/scene/{scene_number}/segment")
async def post(id: int, scene_number: int, request: Request):  # noqa: F811
    """Start character segmentation for a scene video.

    Expects JSON body with prompts: [{"object_id": 1, "frame_idx": 0, "points": [[x,y]], "labels": [1]}, ...]
    """
    if not segmentation_available():
        return Response("SAM2 not available", status_code=501)

    sb = _load(id)
    if sb is None:
        return Response("Storyboard not found", status_code=404)

    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return Response("Scene not found", status_code=404)

    version = scene.active_version or None
    vpath = video_path_for(id, scene_number, version)
    if not vpath.is_file():
        vpath = video_path_for(id, scene_number, None)
    if not vpath.is_file():
        return Response("No video found for this scene", status_code=404)

    import json as _json
    try:
        raw = await request.body()
        body = _json.loads(raw) if raw else {}
    except Exception:
        body = {}
    prompts = body.get("prompts", [])

    # If no prompts given, return the interactive prompt UI
    if not prompts:
        return _segment_prompt_ui(id, scene_number, version)

    job_id = str(uuid.uuid4())
    _pending_segment[job_id] = (id, scene_number, vpath, prompts)

    return Div(
        Div(
            sse_swap="segment-progress",
            hx_swap="innerHTML",
            cls="video-progress-area",
            id=f"segment-progress-{scene_number}",
        ),
        hx_ext="sse",
        sse_connect=f"/segment-run?id={job_id}",
        sse_close="close",
    )


@rt("/segment-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for character segmentation progress."""
    job = _pending_segment.pop(id, None)
    if job is None:
        return Response("Job not found", status_code=404)

    storyboard_id, scene_number, vpath, prompts = job

    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _active_segment_cancel[id] = cancel_event

    thread = threading.Thread(
        target=segment_scene_to_queue,
        args=(storyboard_id, scene_number, vpath, prompts, q, cancel_event),
        daemon=True,
    )
    thread.start()

    async def event_stream():
        try:
            while True:
                try:
                    msg_type, message = q.get(timeout=0.3)
                except queue.Empty:
                    if not thread.is_alive():
                        break
                    continue

                html = progress_message(message)
                yield f"event: segment-progress\ndata: {html}\n\n"

                if msg_type in ("complete", "done", "error", "cancelled"):
                    # Show results if completed
                    if msg_type in ("complete", "done"):
                        results_html = _segment_results_html(storyboard_id, scene_number)
                        yield f"event: segment-progress\ndata: {results_html}\n\n"
                    yield "event: close\ndata: \n\n"
                    break
        finally:
            _active_segment_cancel.pop(id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/storyboard/{id}/scene/{scene_number}/segment/frame")
def get(id: int, scene_number: int, frame_idx: int = 0):  # noqa: F811
    """Serve a video frame as JPEG for the segmentation prompt UI."""
    scene = None
    sb = _load(id)
    if sb:
        scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return Response("Scene not found", status_code=404)

    version = scene.active_version or None
    vpath = video_path_for(id, scene_number, version)
    if not vpath.is_file():
        vpath = video_path_for(id, scene_number, None)
    if not vpath.is_file():
        return Response("No video", status_code=404)

    # Extract the requested frame via ffmpeg
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(vpath),
                "-vf", f"select=eq(n\\,{frame_idx})",
                "-frames:v", "1",
                "-f", "image2", "-c:v", "mjpeg",
                "pipe:1",
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return Response(content=result.stdout, media_type="image/jpeg")
    except Exception:
        pass
    return Response("Failed to extract frame", status_code=500)


@rt("/storyboard/{id}/scene/{scene_number}/segment/info")
def get(id: int, scene_number: int):  # noqa: F811
    """Return JSON with video info (frame count, fps, duration) for the segmentation UI."""
    import subprocess
    sb = _load(id)
    if sb is None:
        return Response("{}", media_type="application/json")
    scene = next((s for s in sb.scenes if s.scene_number == scene_number), None)
    if scene is None:
        return Response("{}", media_type="application/json")

    version = scene.active_version or None
    vpath = video_path_for(id, scene_number, version)
    if not vpath.is_file():
        vpath = video_path_for(id, scene_number, None)
    if not vpath.is_file():
        return Response("{}", media_type="application/json")

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-count_frames",
                "-show_entries", "stream=nb_read_frames,r_frame_rate,duration",
                "-of", "json",
                str(vpath),
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            import json as _json
            probe = _json.loads(result.stdout)
            stream = probe.get("streams", [{}])[0]
            nb_frames = int(stream.get("nb_read_frames", 0))
            r_fps = stream.get("r_frame_rate", "24/1")
            num, den = r_fps.split("/")
            fps = round(int(num) / int(den))
            duration = float(stream.get("duration", 0))
            return Response(
                _json.dumps({"frame_count": nb_frames, "fps": fps, "duration": round(duration, 2)}),
                media_type="application/json",
            )
    except Exception:
        pass
    return Response("{}", media_type="application/json")


@rt("/segment/{storyboard_id}/{scene_number}/{object_id}/preview")
def get(storyboard_id: int, scene_number: int, object_id: int):  # noqa: F811
    """Serve a character segmentation preview GIF."""
    gif_path = segment_dir_for(storyboard_id, scene_number) / f"char_{object_id}_preview.gif"
    if gif_path.is_file():
        return FileResponse(gif_path, media_type="image/gif")
    return Response("Not found", status_code=404)


@rt("/segment/{storyboard_id}/{scene_number}/{object_id}/frame/{frame_idx}")
def get(storyboard_id: int, scene_number: int, object_id: int, frame_idx: int):  # noqa: F811
    """Serve a single segmented character frame (RGBA PNG)."""
    from .segmentation import segment_frame_path
    png_path = segment_frame_path(storyboard_id, scene_number, object_id, frame_idx)
    if png_path.is_file():
        return FileResponse(png_path, media_type="image/png")
    return Response("Not found", status_code=404)


def _segment_prompt_ui(storyboard_id: int, scene_number: int, version: int | None) -> str:
    """Return an interactive UI for clicking on a frame to set segmentation prompts."""
    n = scene_number
    frame_url = f"/storyboard/{storyboard_id}/scene/{n}/segment/frame?frame_idx=0"
    info_url = f"/storyboard/{storyboard_id}/scene/{n}/segment/info"
    return f"""
    <div class="segment-prompt-ui" id="segment-ui-{n}">
        <h4>Character Segmentation</h4>
        <p class="segment-instructions">Browse frames with the slider, then click on characters to mark them.
        Left-click = include (green), right-click = exclude (red). Points are tied to the frame you place them on.</p>

        <div class="segment-frame-container" id="segment-frame-{n}">
            <img src="{frame_url}" class="segment-frame-img"
                 id="segment-img-{n}"
                 onclick="s2vSeg.click(event, {n}, 1)"
                 oncontextmenu="s2vSeg.click(event, {n}, 0); return false;" />
            <div class="segment-points-overlay" id="segment-points-{n}"></div>
        </div>

        <div class="segment-scrubber">
            <button class="btn btn-secondary btn-xs" onclick="s2vSeg.nav({storyboard_id}, {n}, -1)" title="Previous frame">&#9664;</button>
            <input type="range" id="segment-slider-{n}" min="0" max="0" value="0"
                   class="segment-slider"
                   oninput="s2vSeg.seek({storyboard_id}, {n}, parseInt(this.value))" />
            <button class="btn btn-secondary btn-xs" onclick="s2vSeg.nav({storyboard_id}, {n}, 1)" title="Next frame">&#9654;</button>
            <span class="segment-frame-label" id="segment-frame-label-{n}">Frame 0 / ?</span>
        </div>

        <div class="segment-controls">
            <label>Character ID: <input type="number" id="segment-obj-id-{n}" value="1" min="1" max="20" class="segment-id-input" /></label>
            <button class="btn btn-secondary btn-sm" onclick="s2vSeg.clear({n})">Clear Points</button>
            <button class="btn btn-primary btn-sm" onclick="s2vSeg.run({storyboard_id}, {n})">Extract Characters</button>
        </div>

        <div class="segment-point-summary" id="segment-summary-{n}"></div>
        <div id="segment-result-{n}"></div>
    </div>
    <script>
    (function() {{
        // Namespace all segmentation state
        if (!window.s2vSeg) window.s2vSeg = {{}};
        const S = window.s2vSeg;

        // State per scene: {{ frameIdx, frameCount, points: [{{x,y,label,object_id,frame_idx}}] }}
        if (!S._state) S._state = {{}};
        S._state[{n}] = {{ frameIdx: 0, frameCount: 0, points: [] }};

        // Fetch video info (frame count) on load
        fetch('{info_url}').then(r => r.json()).then(info => {{
            const st = S._state[{n}];
            st.frameCount = info.frame_count || 1;
            const slider = document.getElementById('segment-slider-{n}');
            slider.max = Math.max(0, st.frameCount - 1);
            S._updateLabel({n});
        }});

        S._updateLabel = function(sceneNum) {{
            const st = S._state[sceneNum];
            const label = document.getElementById('segment-frame-label-' + sceneNum);
            if (label) label.textContent = 'Frame ' + st.frameIdx + ' / ' + (st.frameCount - 1);
        }};

        S._updateSummary = function(sceneNum) {{
            const st = S._state[sceneNum];
            const el = document.getElementById('segment-summary-' + sceneNum);
            if (!el) return;
            if (st.points.length === 0) {{ el.innerHTML = ''; return; }}

            // Group by object_id
            const byObj = {{}};
            st.points.forEach(p => {{
                if (!byObj[p.object_id]) byObj[p.object_id] = [];
                byObj[p.object_id].push(p);
            }});
            let html = '<div class="segment-point-list">';
            for (const [oid, pts] of Object.entries(byObj)) {{
                const frames = [...new Set(pts.map(p => p.frame_idx))].sort((a,b)=>a-b);
                const fg = pts.filter(p => p.label === 1).length;
                const bg = pts.filter(p => p.label === 0).length;
                html += '<div class="segment-point-item">'
                    + '<strong>Char ' + oid + ':</strong> '
                    + fg + ' fg, ' + bg + ' bg point(s) on frame(s) ' + frames.join(', ')
                    + '</div>';
            }}
            html += '</div>';
            el.innerHTML = html;
        }};

        S._showDotsForFrame = function(sceneNum) {{
            const st = S._state[sceneNum];
            const overlay = document.getElementById('segment-points-' + sceneNum);
            const img = document.getElementById('segment-img-' + sceneNum);
            overlay.innerHTML = '';
            // Show dots for current frame
            st.points.filter(p => p.frame_idx === st.frameIdx).forEach(p => {{
                const dot = document.createElement('div');
                dot.className = 'segment-dot ' + (p.label === 1 ? 'segment-dot-fg' : 'segment-dot-bg');
                dot.style.left = (p.xPct * 100) + '%';
                dot.style.top = (p.yPct * 100) + '%';
                dot.title = 'Char ' + p.object_id + (p.label === 1 ? ' (fg)' : ' (bg)');
                overlay.appendChild(dot);
            }});
        }};

        S.seek = function(sbId, sceneNum, frameIdx) {{
            const st = S._state[sceneNum];
            st.frameIdx = Math.max(0, Math.min(frameIdx, st.frameCount - 1));
            const slider = document.getElementById('segment-slider-' + sceneNum);
            slider.value = st.frameIdx;
            S._updateLabel(sceneNum);
            // Load frame image
            const img = document.getElementById('segment-img-' + sceneNum);
            img.src = '/storyboard/' + sbId + '/scene/' + sceneNum + '/segment/frame?frame_idx=' + st.frameIdx;
            // Re-show dots for this frame
            img.onload = function() {{ S._showDotsForFrame(sceneNum); }};
        }};

        S.nav = function(sbId, sceneNum, delta) {{
            const st = S._state[sceneNum];
            S.seek(sbId, sceneNum, st.frameIdx + delta);
        }};

        S.click = function(e, sceneNum, label) {{
            e.preventDefault();
            const st = S._state[sceneNum];
            const img = document.getElementById('segment-img-' + sceneNum);
            const rect = img.getBoundingClientRect();
            const xPct = (e.clientX - rect.left) / rect.width;
            const yPct = (e.clientY - rect.top) / rect.height;
            const x = (xPct * img.naturalWidth).toFixed(1);
            const y = (yPct * img.naturalHeight).toFixed(1);
            const objId = parseInt(document.getElementById('segment-obj-id-' + sceneNum).value) || 1;

            st.points.push({{
                x: parseFloat(x), y: parseFloat(y),
                xPct: xPct, yPct: yPct,
                label: label, object_id: objId,
                frame_idx: st.frameIdx,
            }});

            // Visual indicator
            const overlay = document.getElementById('segment-points-' + sceneNum);
            const dot = document.createElement('div');
            dot.className = 'segment-dot ' + (label === 1 ? 'segment-dot-fg' : 'segment-dot-bg');
            dot.style.left = (xPct * 100) + '%';
            dot.style.top = (yPct * 100) + '%';
            dot.title = 'Char ' + objId + ' frame ' + st.frameIdx + (label === 1 ? ' (fg)' : ' (bg)');
            overlay.appendChild(dot);

            S._updateSummary(sceneNum);
        }};

        S.clear = function(sceneNum) {{
            S._state[sceneNum].points = [];
            document.getElementById('segment-points-' + sceneNum).innerHTML = '';
            S._updateSummary(sceneNum);
        }};

        S.run = function(sbId, sceneNum) {{
            const st = S._state[sceneNum];
            if (st.points.length === 0) {{ alert('Click on characters in the frame first'); return; }}

            // Group points by object_id, preserving which frame each point is on
            const byObj = {{}};
            st.points.forEach(p => {{
                const key = p.object_id + '_' + p.frame_idx;
                if (!byObj[key]) byObj[key] = {{
                    object_id: p.object_id,
                    frame_idx: p.frame_idx,
                    points: [], labels: []
                }};
                byObj[key].points.push([p.x, p.y]);
                byObj[key].labels.push(p.label);
            }});
            const prompts = Object.values(byObj);

            fetch('/storyboard/' + sbId + '/scene/' + sceneNum + '/segment', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{prompts: prompts}})
            }}).then(r => r.text()).then(html => {{
                document.getElementById('segment-result-' + sceneNum).innerHTML = html;
                htmx.process(document.getElementById('segment-result-' + sceneNum));
            }});
        }};
    }})();
    </script>
    """


def _segment_results_html(storyboard_id: int, scene_number: int) -> str:
    """Render HTML showing segmentation results (preview GIFs)."""
    seg_dir = segment_dir_for(storyboard_id, scene_number)
    if not seg_dir.is_dir():
        return "<p>No segmentation results found.</p>"

    gifs = sorted(seg_dir.glob("char_*_preview.gif"))
    if not gifs:
        return "<p>Segmentation complete but no preview GIFs generated.</p>"

    cards = []
    for gif in gifs:
        # Extract object_id from filename: char_1_preview.gif
        name = gif.stem  # char_1_preview
        parts = name.split("_")
        oid = parts[1] if len(parts) >= 2 else "?"
        url = f"/segment/{storyboard_id}/{scene_number}/{oid}/preview"
        cards.append(
            f'<div class="segment-result-card">'
            f'<img src="{url}" alt="Character {oid}" class="segment-preview-gif" />'
            f'<span class="segment-char-label">Character {oid}</span>'
            f'</div>'
        )

    return (
        f'<div class="segment-results">'
        f'<h4>Extracted Characters</h4>'
        f'<div class="segment-results-grid">{"".join(cards)}</div>'
        f'</div>'
    )


# --- Scene Compositor ---
_pending_composite: dict[str, dict] = {}  # job_id -> composite config
_active_composite_cancel: dict[str, threading.Event] = {}


@rt("/storyboard/{id}/compositor")
def get(id: int):  # noqa: F811
    """Show the compositor UI for a storyboard."""
    sb = _load(id)
    if sb is None:
        return Response("Storyboard not found", status_code=404)

    characters = list_available_characters(id)
    return _compositor_page(id, sb, characters)


@rt("/storyboard/{id}/compositor/characters")
def get(id: int):  # noqa: F811
    """Return available characters as JSON for the compositor."""
    characters = list_available_characters(id)
    # Strip non-serializable frame_paths, convert preview_gif to URL
    result = []
    for c in characters:
        entry = {
            "scene_number": c["scene_number"],
            "object_id": c["object_id"],
            "num_frames": c["num_frames"],
        }
        if c.get("preview_gif"):
            entry["preview_url"] = (
                f"/segment/{id}/{c['scene_number']}/{c['object_id']}/preview"
            )
        result.append(entry)
    import json as _json
    return Response(_json.dumps(result), media_type="application/json")


@rt("/storyboard/{id}/compositor/render")
async def post(id: int, request: Request):  # noqa: F811
    """Start compositor rendering.

    Expects JSON body:
    {
        "layers": [
            {"scene_number": 1, "object_id": 1, "x": 0, "y": 0, "scale": 1.0, "flip_h": false},
            ...
        ],
        "canvas_width": 1280,
        "canvas_height": 720,
        "fps": 24,
        "background_color": "#1a1a1a",
        "background_scene": null  // optional: {"storyboard_id": 1, "scene_number": 3}
    }
    """
    sb = _load(id)
    if sb is None:
        return Response("Storyboard not found", status_code=404)

    import json as _json
    try:
        raw = await request.body()
        body = _json.loads(raw) if raw else {}
    except Exception:
        return Response("Invalid JSON", status_code=400)

    layer_defs = body.get("layers", [])
    if not layer_defs:
        return Response("No layers specified", status_code=400)

    canvas_w = body.get("canvas_width", 1280)
    canvas_h = body.get("canvas_height", 720)
    fps = body.get("fps", 24)
    bg_color = body.get("background_color", "#1a1a1a")
    bg_scene = body.get("background_scene")

    # Resolve character frame paths
    all_chars = list_available_characters(id)
    chars_by_key = {(c["scene_number"], c["object_id"]): c for c in all_chars}

    layers: list[CharacterLayer] = []
    max_frames = 0
    for ld in layer_defs:
        key = (ld["scene_number"], ld["object_id"])
        char = chars_by_key.get(key)
        if char is None:
            return Response(f"Character not found: scene {key[0]}, object {key[1]}", status_code=404)

        layer = CharacterLayer(
            frame_paths=char["frame_paths"],
            x=ld.get("x", 0),
            y=ld.get("y", 0),
            scale=ld.get("scale", 1.0),
            flip_h=ld.get("flip_h", False),
        )
        layers.append(layer)
        max_frames = max(max_frames, len(char["frame_paths"]))

    # Resolve background video if specified
    bg_video = None
    if bg_scene:
        bg_sb_id = bg_scene.get("storyboard_id", id)
        bg_scene_num = bg_scene["scene_number"]
        bg_sb = _load(bg_sb_id)
        if bg_sb:
            bg_s = next((s for s in bg_sb.scenes if s.scene_number == bg_scene_num), None)
            if bg_s:
                version = bg_s.active_version if bg_s.versions else None
                vp = video_path_for(bg_sb_id, bg_scene_num, version)
                if vp.is_file():
                    bg_video = vp

    job_id = str(uuid.uuid4())
    _pending_composite[job_id] = {
        "storyboard_id": id,
        "composite_id": job_id[:8],
        "layers": layers,
        "num_frames": max_frames,
        "canvas_width": canvas_w,
        "canvas_height": canvas_h,
        "fps": fps,
        "background_color": bg_color,
        "background_video": bg_video,
    }

    return Div(
        Div(
            sse_swap="composite-progress",
            hx_swap="innerHTML",
            cls="video-progress-area",
            id="composite-progress",
        ),
        hx_ext="sse",
        sse_connect=f"/composite-run?id={job_id}",
        sse_close="close",
    )


@rt("/composite-run")
async def get(id: str):  # noqa: F811
    """SSE endpoint for compositor rendering progress."""
    job = _pending_composite.pop(id, None)
    if job is None:
        return Response("Job not found", status_code=404)

    q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    _active_composite_cancel[id] = cancel_event

    thread = threading.Thread(
        target=compose_scene_to_queue,
        kwargs={
            "storyboard_id": job["storyboard_id"],
            "composite_id": job["composite_id"],
            "layers": job["layers"],
            "num_frames": job["num_frames"],
            "canvas_width": job["canvas_width"],
            "canvas_height": job["canvas_height"],
            "fps": job["fps"],
            "background_color": job["background_color"],
            "background_video": job["background_video"],
            "q": q,
            "cancel_event": cancel_event,
        },
        daemon=True,
    )
    thread.start()

    async def event_stream():
        composite_id = job["composite_id"]
        sb_id = job["storyboard_id"]
        try:
            while True:
                try:
                    msg_type, message = q.get(timeout=0.3)
                except queue.Empty:
                    if not thread.is_alive():
                        break
                    continue

                html = progress_message(message)
                if msg_type == "complete":
                    video_url = f"/composite/{sb_id}/{composite_id}.mp4"
                    html += (
                        f'<div class="composite-result">'
                        f'<video controls autoplay loop class="composite-video">'
                        f'<source src="{video_url}" type="video/mp4">'
                        f'</video></div>'
                    )
                yield f"event: composite-progress\ndata: {html}\n\n"

                if msg_type in ("complete", "error", "cancelled"):
                    yield "event: close\ndata: \n\n"
                    break
        finally:
            _active_composite_cancel.pop(id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@rt("/composite/{storyboard_id}/{filename}")
def get(storyboard_id: int, filename: str):  # noqa: F811
    """Serve a composited video file."""
    composite_id = filename.replace(".mp4", "")
    path = composite_video_path(storyboard_id, composite_id)
    if path.is_file():
        return FileResponse(path, media_type="video/mp4")
    return Response("Not found", status_code=404)


def _compositor_page(storyboard_id: int, sb: Storyboard, characters: list[dict]) -> str:
    """Render the full compositor page."""
    import html as html_mod

    # Build character picker cards
    char_cards = []
    for c in characters:
        preview = ""
        if c.get("preview_gif"):
            url = f"/segment/{storyboard_id}/{c['scene_number']}/{c['object_id']}/preview"
            preview = f'<img src="{url}" class="compositor-char-preview" />'
        else:
            preview = '<div class="compositor-char-placeholder">No preview</div>'

        char_cards.append(
            f'<div class="compositor-char-card" '
            f'data-scene="{c["scene_number"]}" data-objid="{c["object_id"]}" '
            f'data-frames="{c["num_frames"]}">'
            f'{preview}'
            f'<div class="compositor-char-info">'
            f'<strong>Scene {c["scene_number"]}</strong> / Char {c["object_id"]}'
            f'<br><small>{c["num_frames"]} frames</small>'
            f'</div>'
            f'<button class="btn btn-secondary btn-xs" '
            f'onclick="s2vCompAddLayer({storyboard_id}, {c["scene_number"]}, {c["object_id"]})">+ Add</button>'
            f'</div>'
        )

    if not char_cards:
        char_cards_html = '<p class="compositor-empty">No segmented characters yet. Use "Extract Characters" on a scene card first.</p>'
    else:
        char_cards_html = f'<div class="compositor-char-grid">{"".join(char_cards)}</div>'

    # Build scene list for background picker
    scene_options = '<option value="">Solid color</option>'
    for scene in sb.scenes:
        version = scene.active_version if scene.versions else None
        if video_exists(storyboard_id, scene.scene_number, version):
            scene_options += f'<option value="{scene.scene_number}">Scene {scene.scene_number}: {html_mod.escape(scene.title)}</option>'

    return f"""
    <div class="container compositor-container">
        <h2><a href="/storyboard/{storyboard_id}" class="back-link">&larr;</a> Scene Compositor</h2>
        <p class="compositor-subtitle">Layer extracted characters onto backgrounds to create new scenes.</p>

        <div class="compositor-layout">
            <div class="compositor-sidebar">
                <h3>Characters</h3>
                {char_cards_html}

                <h3>Background</h3>
                <div class="compositor-bg-controls">
                    <label>Source:
                        <select id="comp-bg-source" onchange="s2vCompBgChanged()">
                            {scene_options}
                        </select>
                    </label>
                    <label>Color: <input type="color" id="comp-bg-color" value="#1a1a1a" /></label>
                </div>

                <h3>Canvas</h3>
                <div class="compositor-canvas-controls">
                    <label>Width: <input type="number" id="comp-width" value="1280" min="320" max="3840" /></label>
                    <label>Height: <input type="number" id="comp-height" value="720" min="240" max="2160" /></label>
                    <label>FPS: <input type="number" id="comp-fps" value="24" min="1" max="60" /></label>
                </div>
            </div>

            <div class="compositor-main">
                <h3>Layers</h3>
                <div id="comp-layers" class="compositor-layers">
                    <p class="compositor-empty" id="comp-no-layers">Click "+ Add" on a character to add layers.</p>
                </div>

                <div class="compositor-actions">
                    <button class="btn btn-primary" onclick="s2vCompRender({storyboard_id})">Render Composite</button>
                </div>

                <div id="comp-result"></div>
            </div>
        </div>
    </div>

    <script>
    window._compLayers = [];
    window._compLayerCounter = 0;

    function s2vCompAddLayer(sbId, sceneNum, objId) {{
        const id = ++window._compLayerCounter;
        window._compLayers.push({{id, scene_number: sceneNum, object_id: objId, x: 0, y: 0, scale: 1.0, flip_h: false}});
        document.getElementById('comp-no-layers').style.display = 'none';

        const el = document.createElement('div');
        el.className = 'compositor-layer-card';
        el.id = 'comp-layer-' + id;
        el.innerHTML = `
            <strong>Scene ${{sceneNum}} / Char ${{objId}}</strong>
            <div class="compositor-layer-controls">
                <label>X: <input type="number" value="0" onchange="s2vCompUpdateLayer(${{id}}, 'x', this.value)" /></label>
                <label>Y: <input type="number" value="0" onchange="s2vCompUpdateLayer(${{id}}, 'y', this.value)" /></label>
                <label>Scale: <input type="number" value="1.0" step="0.1" min="0.1" max="5" onchange="s2vCompUpdateLayer(${{id}}, 'scale', this.value)" /></label>
                <label><input type="checkbox" onchange="s2vCompUpdateLayer(${{id}}, 'flip_h', this.checked)" /> Flip H</label>
                <button class="btn btn-secondary btn-xs" onclick="s2vCompRemoveLayer(${{id}})">Remove</button>
            </div>
        `;
        document.getElementById('comp-layers').appendChild(el);
    }}

    function s2vCompUpdateLayer(id, field, value) {{
        const layer = window._compLayers.find(l => l.id === id);
        if (!layer) return;
        if (field === 'flip_h') layer[field] = !!value;
        else if (field === 'scale') layer[field] = parseFloat(value);
        else layer[field] = parseInt(value);
    }}

    function s2vCompRemoveLayer(id) {{
        window._compLayers = window._compLayers.filter(l => l.id !== id);
        const el = document.getElementById('comp-layer-' + id);
        if (el) el.remove();
        if (window._compLayers.length === 0) {{
            document.getElementById('comp-no-layers').style.display = '';
        }}
    }}

    function s2vCompBgChanged() {{
        // No-op for now, just read on render
    }}

    function s2vCompRender(sbId) {{
        if (window._compLayers.length === 0) {{
            alert('Add at least one character layer first.');
            return;
        }}

        const bgSource = document.getElementById('comp-bg-source').value;
        const body = {{
            layers: window._compLayers.map(l => ({{
                scene_number: l.scene_number,
                object_id: l.object_id,
                x: l.x,
                y: l.y,
                scale: l.scale,
                flip_h: l.flip_h,
            }})),
            canvas_width: parseInt(document.getElementById('comp-width').value),
            canvas_height: parseInt(document.getElementById('comp-height').value),
            fps: parseInt(document.getElementById('comp-fps').value),
            background_color: document.getElementById('comp-bg-color').value,
        }};

        if (bgSource) {{
            body.background_scene = {{storyboard_id: sbId, scene_number: parseInt(bgSource)}};
        }}

        fetch('/storyboard/' + sbId + '/compositor/render', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(body),
        }}).then(r => r.text()).then(html => {{
            document.getElementById('comp-result').innerHTML = html;
            htmx.process(document.getElementById('comp-result'));
        }});
    }}
    </script>
    """


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Alingo Video Story web UI")
    parser.add_argument("--port", type=int, default=5001, help="Port to serve on (default: 5001)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING"],
        help="Log level (default: INFO, use DEBUG to see full prompts/responses)",
    )
    args = parser.parse_args()

    # Set env var so the reloaded worker process picks up the level
    os.environ["S2V_LOG_LEVEL"] = args.log_level
    logging.getLogger().setLevel(args.log_level)

    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

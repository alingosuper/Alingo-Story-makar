"""FT component functions for the alingo_video_story web UI."""

from fasthtml.common import *  # noqa: F403, F405

from .models import Scene, Storyboard
from .video import WAN_AVAILABLE, combined_video_path, video_exists, video_path_for
from .video_fal import fal_available
from .video_hailuo import hailuo_available
from .video_venice import venice_available
from .video_meshwell import meshwell_available
from .video_modal import modal_available
from .segmentation import segmentation_available

PROMPT_KEYWORDS = {
    "Camera Motion": [
        "Slow dolly in",
        "Steady orbit",
        "Locked-off shot",
        "Tracking shot",
        "Crane shot",
        "Handheld",
        "Push in",
        "Pull back",
        "Pan left to right",
        "Tilt up",
    ],
    "Lighting": [
        "Softbox lighting",
        "Three-point studio setup",
        "Golden hour",
        "Blue hour",
        "Overcast diffused",
        "Harsh sunlight",
        "Candlelight",
        "Neon glow",
        "Moonlit",
        "Rim lighting",
        "Chiaroscuro",
        "Volumetric light rays",
    ],
    "Camera Type": [
        "Medium format camera",
        "Large format photography",
        "35mm film",
        "Anamorphic lens",
        "Macro lens",
        "Wide-angle lens",
        "Telephoto lens",
    ],
    "Visual Style": [
        "Cinematic realism",
        "Anime",
        "Digital art",
        "Fantasy art",
        "Watercolor",
        "Oil painting",
        "Noir",
        "Cyberpunk",
        "Studio Ghibli",
        "Photorealistic",
        "Impressionist",
        "Low-fi retro",
        "Concept art",
    ],
    "Mood / Atmosphere": [
        "Serene",
        "Dramatic",
        "Eerie",
        "Whimsical",
        "Melancholic",
        "Tense",
        "Euphoric",
        "Mysterious",
        "Nostalgic",
        "Epic",
    ],
}


def _keyword_chips(scene_number: int) -> str:
    """Render toggleable keyword chips grouped by category."""
    import html as html_mod

    groups = ""
    for category, keywords in PROMPT_KEYWORDS.items():
        chips = ""
        for kw in keywords:
            escaped = html_mod.escape(kw)
            chips += (
                f'<button type="button" class="kw-chip" '
                f'data-keyword="{escaped}" data-scene="{scene_number}" '
                f'onclick="toggleKw(this)">{escaped}</button>'
            )
        groups += f'<div class="kw-group"><span class="kw-group-label">{html_mod.escape(category)}</span>{chips}</div>'
    return f'<div class="kw-groups">{groups}</div>'


_VIDEO_MODAL_HTML = """
<div id="video-modal" class="video-modal-overlay" style="display:none;" onclick="if(event.target===this)closeVideoModal()">
  <div class="video-modal">
    <button class="video-modal-close" onclick="closeVideoModal()">&times;</button>
    <video id="video-modal-player" controls autoplay></video>
  </div>
</div>
"""

_PROMPT_MODAL_HTML = """
<div id="prompt-modal" class="video-modal-overlay" style="display:none;" onclick="if(event.target===this)closePromptModal()">
  <div class="prompt-modal">
    <button class="video-modal-close" onclick="closePromptModal()">&times;</button>
    <textarea id="prompt-modal-textarea" class="prompt-modal-textarea" rows="16"></textarea>
    <div class="prompt-modal-actions">
        <button class="btn btn-primary btn-sm" onclick="savePromptModal()">Save &amp; Close</button>
        <button class="btn btn-secondary btn-sm" onclick="closePromptModal()">Cancel</button>
    </div>
  </div>
</div>
"""

_VIDEO_MODAL_JS = """
function openVideoModal(src) {
    var modal = document.getElementById('video-modal');
    var player = document.getElementById('video-modal-player');
    player.src = src;
    modal.style.display = 'flex';
    player.play();
    document.addEventListener('keydown', _videoModalEsc);
}
function closeVideoModal() {
    var modal = document.getElementById('video-modal');
    var player = document.getElementById('video-modal-player');
    player.pause();
    player.src = '';
    modal.style.display = 'none';
    document.removeEventListener('keydown', _videoModalEsc);
}
function _videoModalEsc(e) {
    if (e.key === 'Escape') closeVideoModal();
}
// Delegate clicks on compact card video thumbnails to open modal
document.addEventListener('click', function(e) {
    var thumb = e.target.closest('.compact-card-thumb');
    if (thumb) {
        var vid = thumb.querySelector('video');
        if (vid && vid.src) {
            e.preventDefault();
            e.stopPropagation();
            openVideoModal(vid.src);
        }
    }
});
var _promptModalSource = null;
function openPromptModal(textareaId) {
    var src = document.getElementById(textareaId);
    if (!src) return;
    _promptModalSource = src;
    var modal = document.getElementById('prompt-modal');
    var ta = document.getElementById('prompt-modal-textarea');
    ta.value = src.value;
    modal.style.display = 'flex';
    ta.focus();
    document.addEventListener('keydown', _promptModalEsc);
}
function closePromptModal() {
    var modal = document.getElementById('prompt-modal');
    modal.style.display = 'none';
    _promptModalSource = null;
    document.removeEventListener('keydown', _promptModalEsc);
}
function savePromptModal() {
    if (_promptModalSource) {
        _promptModalSource.value = document.getElementById('prompt-modal-textarea').value;
    }
    closePromptModal();
    // Auto-submit the form containing the source textarea
    if (_promptModalSource) {
        var form = _promptModalSource.closest('form');
        if (form) htmx.trigger(form, 'submit');
    }
}
function _promptModalEsc(e) {
    if (e.key === 'Escape') closePromptModal();
}
"""


def site_header(active: str = "", username: str = None) -> FT:
    """Sticky top bar with ALINGO VIDEO STORY logo, nav links, and video modal."""
    projects_cls = "active" if active == "projects" else ""
    user_html = ""
    if username:
        user_html = (
            f'<span class="user-info">{username}</span>'
            f'<a href="/logout" class="nav-logout">Logout</a>'
        )
    return Div(
        Div(
            A(
                NotStr("STORY<span class='accent'>2</span>VIDEO"),
                href="/",
                cls="site-header-logo",
            ),
            Div(
                A("Projects", href="/", cls=projects_cls),
                NotStr(user_html),
                cls="site-header-nav",
            ),
            cls="site-header",
        ),
        NotStr(_VIDEO_MODAL_HTML),
        NotStr(_PROMPT_MODAL_HTML),
        Script(_VIDEO_MODAL_JS),
        Script(_MOBILE_AUTO_SWITCH_JS),
        Script(_FRAME_CAPTURE_JS),
    )


def mobile_tab_bar() -> str:
    """Bottom tab bar for mobile layout — 3 labels targeting hidden radio inputs."""
    return (
        '<div class="mobile-tab-bar">'
        '<label for="mobile-tab-chapters"><span class="tab-icon">☰</span>Chapters</label>'
        '<label for="mobile-tab-scenes"><span class="tab-icon">▦</span>Scenes</label>'
        '<label for="mobile-tab-inspector"><span class="tab-icon">ⓘ</span>Inspector</label>'
        '</div>'
    )


_MOBILE_AUTO_SWITCH_JS = """
document.addEventListener('click', function(e) {
    if (window.innerWidth > 768) return;
    var card = e.target.closest('.compact-scene-card');
    if (card) {
        var radio = document.getElementById('mobile-tab-inspector');
        if (radio) radio.checked = true;
    }
});
"""

_FRAME_CAPTURE_JS = """
function captureFrame(btn, storyboardId, sourceSceneNumber) {
    var video = btn.closest('.video-player-wrapper').querySelector('video');
    if (!video || video.readyState < 2) return;

    /* Capture frame to canvas */
    var canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    /* Build scene picker from cards already in DOM */
    var cards = document.querySelectorAll('.compact-scene-card');
    if (!cards.length) return;

    var overlay = document.createElement('div');
    overlay.className = 'frame-capture-overlay';
    var picker = document.createElement('div');
    picker.className = 'frame-capture-picker';

    /* Preview thumbnail */
    var preview = document.createElement('img');
    preview.src = canvas.toDataURL('image/png');
    preview.className = 'frame-capture-preview';
    picker.appendChild(preview);

    var heading = document.createElement('h4');
    heading.textContent = 'Apply captured frame to:';
    picker.appendChild(heading);

    var list = document.createElement('div');
    list.className = 'frame-capture-list';

    cards.forEach(function(card) {
        var numEl = card.querySelector('.compact-card-number');
        var titleEl = card.querySelector('.compact-card-title');
        if (!numEl) return;
        var match = numEl.textContent.match(/SCN-(\\d+)/);
        if (!match) return;
        var sceneNum = parseInt(match[1], 10);
        var label = numEl.textContent + (titleEl ? ' — ' + titleEl.textContent : '');

        var optBtn = document.createElement('button');
        optBtn.className = 'frame-capture-option';
        optBtn.textContent = label;
        if (sceneNum === sourceSceneNumber) {
            optBtn.textContent += ' (this scene)';
        }
        optBtn.onclick = function() {
            sendCapturedFrame(canvas, storyboardId, sceneNum);
            document.body.removeChild(overlay);
        };
        list.appendChild(optBtn);
    });

    picker.appendChild(list);

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'frame-capture-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = function() { document.body.removeChild(overlay); };
    picker.appendChild(cancelBtn);

    overlay.appendChild(picker);
    overlay.onclick = function(e) {
        if (e.target === overlay) document.body.removeChild(overlay);
    };
    document.body.appendChild(overlay);
}

function sendCapturedFrame(canvas, storyboardId, targetSceneNumber) {
    canvas.toBlob(function(blob) {
        var form = new FormData();
        form.append('file', blob, 'frame.png');
        fetch('/storyboard/' + storyboardId + '/scene/' + targetSceneNumber + '/upload-image', {
            method: 'POST',
            body: form
        }).then(function(r) { return r.text(); })
          .then(function(html) {
            var section = document.getElementById('video-section-' + targetSceneNumber);
            if (section) { section.outerHTML = html; }
        });
    }, 'image/png');
}
"""


def project_card(row) -> FT:
    """Card for the project grid showing title, scene count, date, and actions."""
    created = row.created_at[:10] if row.created_at else ""
    title = row.storyboard_title or "Untitled"
    return Div(
        Div(cls="project-card-thumbnail"),
        Div(
            P(title, cls="project-card-title"),
            Div(
                Span(created),
                cls="project-card-meta",
            ),
            cls="project-card-body",
        ),
        Div(
            A("OPEN STUDIO", href=f"/storyboard/{row.id}"),
            Button(
                NotStr("&#10005;"),
                cls="btn-delete-small",
                hx_delete=f"/storyboard/{row.id}",
                hx_target=f"#project-card-{row.id}",
                hx_swap="outerHTML",
                hx_confirm="Delete this storyboard and all its videos?",
            ),
            cls="project-card-actions",
        ),
        cls="project-card",
        id=f"project-card-{row.id}",
    )


def landing_page(saved_rows=None) -> FT:
    """Project grid workspace — shows saved storyboards as cards with a create-new card."""
    cards = []
    if saved_rows:
        cards = [project_card(row) for row in saved_rows]
    cards.append(
        A(
            Div("+", cls="new-project-card-icon"),
            Div("Create New Project", cls="new-project-card-label"),
            href="/new",
            cls="new-project-card",
        )
    )
    return Div(
        Div("WORKSPACE / ", Span("Recent Projects"), cls="breadcrumb"),
        Div(*cards, cls="project-grid"),
        cls="container",
    )


def new_project_page() -> FT:
    """Centered form page for creating a new storyboard from script text."""
    return Div(
        Div(
            H1("Start your cinematic journey"),
            P("Paste a short story below and generate a visual storyboard.", cls="subtitle"),
            cls="new-project-hero",
        ),
        Form(
            Textarea(
                name="story",
                id="story-input",
                placeholder="Paste your story here...",
                rows=12,
            ),
            Div("0 / 15,000 characters", id="char-count", cls="char-count"),
            Script("""
                function updateCharCount() {
                    const ta = document.getElementById('story-input');
                    const cc = document.getElementById('char-count');
                    if (!ta || !cc) return;
                    const n = ta.value.length;
                    cc.textContent = n.toLocaleString() + ' / 15,000 characters';
                    cc.classList.toggle('over-limit', n > 15000);
                }
                document.addEventListener('input', function(e) {
                    if (e.target.id === 'story-input') updateCharCount();
                });
                document.addEventListener('htmx:afterSwap', function(e) {
                    if (e.detail.target.id === 'story-input' || e.detail.target.querySelector('#story-input')) {
                        updateCharCount();
                    }
                });
                updateCharCount();
            """),
            Div(
                Label(
                    "Scenes: ",
                    Select(
                        Option("~5", value="5"),
                        Option("~10", value="10", selected=True),
                        Option("~15", value="15"),
                        name="num_scenes",
                        cls="scene-count-select",
                    ),
                    cls="scene-count-label",
                ),
                Button(
                    "Load example",
                    type="button",
                    cls="btn btn-secondary",
                    hx_get="/sample",
                    hx_target="#story-input",
                    hx_swap="outerHTML",
                ),
                Button(
                    Span("Create from Script"),
                    Span(cls="spinner", aria_hidden="true"),
                    type="submit",
                    id="generate-btn",
                    cls="btn btn-primary",
                ),
                cls="button-row",
            ),
            hx_post="/generate",
            hx_target="#output-area",
            hx_swap="innerHTML",
            hx_indicator="#generate-btn",
        ),
        NotStr(rewrite_section()),
        Div(id="output-area"),
        cls="new-project-container",
    )


def sse_container(story_id: str) -> FT:
    """Return an SSE-connected container that receives progress and result events."""
    return Div(
        Div(sse_swap="progress", hx_swap="beforeend", id="progress-area"),
        Div(sse_swap="result", id="result-area"),
        hx_ext="sse",
        sse_connect=f"/run?id={story_id}",
        sse_close="close",
    )


def progress_message(step: str, message: str) -> str:
    """Return an HTML string for a single progress line."""
    icons = {
        "decomposition": "&#9998;",  # pencil
        "visual": "&#127912;",  # palette
        "consistency": "&#128270;",  # magnifying glass
        "done": "&#10004;",  # check mark
        "error": "&#10060;",  # cross mark
    }
    icon = icons.get(step, "&#8226;")
    done_cls = " done" if step == "done" else ""
    return f'<div class="progress-line{done_cls}"><span class="progress-icon">{icon}</span> {message}</div>'


def chapter_story_text_section(ch, sb_id) -> str:
    """Collapsible story text display within a chapter."""
    if not ch.story_text:
        return ""
    import html as html_mod

    escaped = html_mod.escape(ch.story_text)
    ch_num = ch.chapter_number
    return (
        f'<div class="chapter-story-text">'
        f'<button type="button" class="modify-toggle"'
        f" onclick=\"let t=document.getElementById('ch-story-text-{ch_num}');"
        f"t.style.display=t.style.display==='none'?'block':'none';\">"
        f"&#128196; Story text</button>"
        f'<div id="ch-story-text-{ch_num}" class="story-text-section" style="display:none;">'
        f"{escaped}"
        f"</div>"
        f"</div>"
    )


def chapter_continuation_section(sb_id, ch_num) -> str:
    """Collapsible 'Continue this chapter' section."""
    form_id = f"ch-cont-form-{ch_num}"
    btn_id = f"ch-cont-btn-{ch_num}"
    output_id = f"ch-cont-output-{ch_num}"
    return (
        f'<button type="button" class="continuation-toggle"'
        f" onclick=\"let f=document.getElementById('{form_id}');"
        f"f.style.display=f.style.display==='none'?'block':'none';\">"
        f"Continue this chapter +</button>"
        f'<div id="{form_id}" style="display:none;">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/continue"'
        f' hx-target="#{output_id}" hx-swap="innerHTML"'
        f' hx-indicator="#{btn_id}"'
        f' class="continuation-form">'
        f'<textarea name="story" placeholder="Paste continuation text for this chapter..." rows="6"'
        f' class="modify-textarea"></textarea>'
        f'<button type="submit" id="{btn_id}" class="btn btn-primary btn-sm">'
        f"<span>Generate continuation</span>"
        f'<span class="spinner" aria-hidden="true"></span>'
        f"</button>"
        f"</form>"
        f'<div id="{output_id}"></div>'
        f"</div>"
    )


def chapter_auto_continuation_section(sb_id, ch_num) -> str:
    """Collapsible 'Auto-continue chapter' section."""
    form_id = f"ch-auto-cont-form-{ch_num}"
    btn_id = f"ch-auto-cont-btn-{ch_num}"
    output_id = f"ch-auto-cont-output-{ch_num}"
    return (
        f'<button type="button" class="continuation-toggle"'
        f" onclick=\"let f=document.getElementById('{form_id}');"
        f"f.style.display=f.style.display==='none'?'block':'none';\">"
        f"Auto-continue chapter +</button>"
        f'<div id="{form_id}" style="display:none;">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/auto-continue"'
        f' hx-target="#{output_id}" hx-swap="innerHTML"'
        f' hx-indicator="#{btn_id}"'
        f' class="continuation-form">'
        f"<label>Number of scenes:"
        f'<input type="number" name="num_scenes" value="3" min="1" max="10"'
        f' class="rewrite-input" style="width:80px; display:inline-block; margin-left:8px;">'
        f"</label>"
        f'<input type="text" name="instruction" class="rewrite-input"'
        f' placeholder="Optional direction (e.g. The hero discovers a hidden cave)...">'
        f'<button type="submit" id="{btn_id}" class="btn btn-primary btn-sm">'
        f"<span>Generate continuation</span>"
        f'<span class="spinner" aria-hidden="true"></span>'
        f"</button>"
        f"</form>"
        f'<div id="{output_id}"></div>'
        f"</div>"
    )


def chapter_modify_section(sb_id, ch_num) -> str:
    """Collapsible 'Modify all scenes in chapter' form."""
    form_id = f"ch-modify-form-{ch_num}"
    btn_id = f"ch-modify-btn-{ch_num}"
    output_id = f"ch-modify-output-{ch_num}"
    return (
        f'<div class="modify-all-section">'
        f'<button type="button" class="modify-toggle modify-all-toggle"'
        f" onclick=\"let f=document.getElementById('{form_id}');"
        f"f.style.display=f.style.display==='none'?'block':'none';\">"
        f"&#9998; Modify all scenes</button>"
        f'<div id="{form_id}" style="display:none;">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/modify"'
        f' hx-target="#{output_id}" hx-swap="innerHTML"'
        f' hx-indicator="#{btn_id}">'
        f'<textarea name="instruction" class="modify-textarea" rows="2"'
        f' placeholder="e.g. Change the art style to watercolor..."></textarea>'
        f'<button type="submit" id="{btn_id}" class="btn btn-secondary btn-sm">'
        f"<span>Apply to chapter scenes</span>"
        f'<span class="spinner" aria-hidden="true"></span>'
        f"</button>"
        f"</form>"
        f'<div id="{output_id}"></div>'
        f"</div>"
        f"</div>"
    )


def chapter_snapshot_button(sb_id, ch_num) -> str:
    """Render a 'New version for all scenes in chapter' button."""
    return (
        f'<button class="btn btn-secondary btn-sm"'
        f' hx-post="/storyboard/{sb_id}/chapter/{ch_num}/snapshot-all"'
        f' hx-target="#chapter-group-{ch_num}"'
        f' hx-swap="outerHTML"'
        f' hx-confirm="Create a new version for all scenes in this chapter?"'
        f' title="Snapshot all scenes in this chapter to a new version">'
        f"&#128247; New version (chapter)</button>"
    )


def chapter_rewrite_section(sb_id, ch_num) -> str:
    """Collapsible 'Rewrite & Regenerate chapter' section."""
    form_id = f"ch-rewrite-form-{ch_num}"
    btn_id = f"ch-rewrite-btn-{ch_num}"
    output_id = f"ch-rewrite-output-{ch_num}"
    return (
        f'<button type="button" class="modify-toggle"'
        f" onclick=\"let f=document.getElementById('{form_id}');"
        f"f.style.display=f.style.display==='none'?'block':'none';\">"
        f"&#9998; Rewrite chapter</button>"
        f'<div id="{form_id}" style="display:none;">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/rewrite"'
        f' hx-target="#{output_id}" hx-swap="innerHTML"'
        f' hx-indicator="#{btn_id}">'
        f'<input type="text" name="instruction" class="rewrite-input"'
        f' placeholder="e.g. Change the setting to cyberpunk Tokyo...">'
        f'<button type="submit" id="{btn_id}" class="btn btn-secondary btn-sm">'
        f"<span>Rewrite &amp; Regenerate</span>"
        f'<span class="spinner" aria-hidden="true"></span>'
        f"</button>"
        f"</form>"
        f'<div id="{output_id}"></div>'
        f"</div>"
    )


def chapter_version_indicator(ch, sb_id) -> str:
    """Render chapter version indicator with prev/next navigation."""
    if not ch.versions or sb_id is None:
        return ""
    ch_num = ch.chapter_number
    total = len(ch.versions)
    cur = ch.active_version

    prev_btn = ""
    next_btn = ""
    if cur > 1:
        prev_ver = cur - 1
        prev_btn = (
            f'<button class="btn-version-nav"'
            f' hx-post="/storyboard/{sb_id}/chapter/{ch_num}/version/{prev_ver}"'
            f' hx-target="#chapter-group-{ch_num}" hx-swap="outerHTML"'
            f">&laquo;</button>"
        )
    if cur < total:
        next_ver = cur + 1
        next_btn = (
            f'<button class="btn-version-nav"'
            f' hx-post="/storyboard/{sb_id}/chapter/{ch_num}/version/{next_ver}"'
            f' hx-target="#chapter-group-{ch_num}" hx-swap="outerHTML"'
            f">&raquo;</button>"
        )

    return f'<span class="version-indicator chapter-version-indicator">{prev_btn} v{cur} of {total} {next_btn}</span>'


def chapter_remove_button(sb_id, ch_num) -> str:
    """Render a remove button for a chapter."""
    return (
        f'<button class="btn-delete-small chapter-remove-btn"'
        f' hx-delete="/storyboard/{sb_id}/chapter/{ch_num}/remove"'
        f' hx-target="#chapter-container"'
        f' hx-swap="innerHTML"'
        f' hx-confirm="Remove this chapter and all its scenes? Scene numbers will not change."'
        f' title="Remove chapter">&#10005;</button>'
    )


def add_chapter_section(sb_id) -> str:
    """Collapsible 'Add chapter' form at the bottom of the storyboard."""
    return (
        '<div class="add-chapter-section">'
        '<button type="button" class="continuation-toggle"'
        " onclick=\"let f=document.getElementById('add-chapter-form');"
        "f.style.display=f.style.display==='none'?'block':'none';\">"
        "Add chapter +</button>"
        '<div id="add-chapter-form" style="display:none;">'
        f'<form hx-post="/storyboard/{sb_id}/add-chapter"'
        ' hx-target="#add-chapter-output" hx-swap="innerHTML"'
        ' hx-indicator="#add-chapter-btn"'
        ' class="continuation-form">'
        '<textarea name="story" placeholder="Paste the next chapter here..." rows="8"'
        ' class="modify-textarea"></textarea>'
        '<button type="submit" id="add-chapter-btn" class="btn btn-primary btn-sm">'
        "<span>Add chapter</span>"
        '<span class="spinner" aria-hidden="true"></span>'
        "</button>"
        "</form>"
        '<div id="add-chapter-output"></div>'
        "</div>"
        "</div>"
    )


def chapter_group_html(ch, storyboard, storyboard_id) -> str:
    """Render a complete chapter group div with header, story text, scenes, and action buttons."""
    import html as html_mod

    ch_num = ch.chapter_number
    all_scenes = storyboard.scenes

    # Chapter header
    ch_title = html_mod.escape(ch.title) if ch.title else ""
    ch_summary = html_mod.escape(ch.summary) if ch.summary else ""

    version_html = chapter_version_indicator(ch, storyboard_id) if storyboard_id is not None else ""
    remove_html = ""
    if storyboard_id is not None and len(storyboard.chapters) > 1:
        remove_html = chapter_remove_button(storyboard_id, ch_num)

    ch_header = f'<div class="chapter-header"><span class="chapter-number">Chapter {ch_num}</span>'
    if ch_title:
        ch_header += f' <span class="chapter-title">{ch_title}</span>'
    ch_header += f" {version_html}{remove_html}</div>"
    if ch_summary:
        ch_header += f'<p class="chapter-summary">{ch_summary}</p>'

    # Story text
    story_text_html = ""
    if storyboard_id is not None:
        story_text_html = chapter_story_text_section(ch, storyboard_id)

    # Scene cards
    chapter_scenes_html = ""
    for s in ch.scenes:
        global_idx = next((i for i, sc in enumerate(all_scenes) if sc is s), 0)
        prev_has_vid = False
        if storyboard_id is not None and global_idx > 0:
            ps = all_scenes[global_idx - 1]
            prev_has_vid = video_exists(storyboard_id, ps.scene_number, ps.active_version or None) or video_exists(
                storyboard_id, ps.scene_number
            )
        chapter_scenes_html += scene_card(s, storyboard_id=storyboard_id, prev_has_video=prev_has_vid)

    # Chapter action buttons
    actions_html = ""
    if storyboard_id is not None:
        actions_html = (
            f'<div class="chapter-actions">'
            f"{chapter_modify_section(storyboard_id, ch_num)}"
            f"{chapter_snapshot_button(storyboard_id, ch_num)}"
            f"{chapter_continuation_section(storyboard_id, ch_num)}"
            f"{chapter_auto_continuation_section(storyboard_id, ch_num)}"
            f"{chapter_rewrite_section(storyboard_id, ch_num)}"
            f"</div>"
        )

    return (
        f'<div class="chapter-group" id="chapter-group-{ch_num}">'
        f"{ch_header}"
        f"{story_text_html}"
        f'<div class="scene-grid">{chapter_scenes_html}</div>'
        f"{actions_html}"
        f"</div>"
    )


def chapter_outline(storyboard: Storyboard) -> str:
    """Render a compact chapter navigation outline. Only shown for 2+ chapters."""
    import html as html_mod

    if len(storyboard.chapters) < 2:
        return ""
    items = ""
    for ch in storyboard.chapters:
        n = ch.chapter_number
        title = html_mod.escape(ch.title) if ch.title else f"Chapter {n}"
        scene_count = len(ch.scenes)
        scene_label = f"{scene_count} scene{'s' if scene_count != 1 else ''}"
        items += (
            f"<li>"
            f'<a class="outline-link" href="#chapter-group-{n}">{title}</a>'
            f'<span class="outline-scene-count">{scene_label}</span>'
            f"</li>"
        )
    return f'<nav class="chapter-outline"><h3>Chapters</h3><ol>{items}</ol></nav>'


def storyboard_view(storyboard: Storyboard, storyboard_id=None) -> str:
    """Return full rendered storyboard HTML as a string."""
    palette_html = "".join(
        f'<span class="color-dot" style="background:{c}" title="{c}"></span>' for c in storyboard.global_color_palette
    )

    # Always render chapter groups
    chapters_html = ""
    for ch in storyboard.chapters:
        chapters_html += chapter_group_html(ch, storyboard, storyboard_id)

    generate_all_html = ""
    if storyboard_id is not None and (hailuo_available() or fal_available() or venice_available() or meshwell_available() or modal_available()):
        any_missing = any(
            s.visual
            and not video_exists(storyboard_id, s.scene_number, s.active_version or None)
            and not video_exists(storyboard_id, s.scene_number)
            for s in storyboard.scenes
        )
        if any_missing:
            duration_js_all = "js:{duration: document.getElementById(&quot;video-duration-all&quot;).value}"
            gen_all_duration = (
                '<select id="video-duration-all" class="video-duration-select">'
                '<option value="5">5s</option>'
                '<option value="6">6s</option>'
                '<option value="10">10s</option>'
                "</select> "
            )
            gen_all_buttons = ""
            if hailuo_available():
                gen_all_buttons += (
                    f'<button class="btn btn-primary btn-generate-all"'
                    f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=hailuo"'
                    f' hx-target="#generate-all-area"'
                    f' hx-swap="outerHTML"'
                    f" hx-vals='{duration_js_all}'>"
                    f"&#9654; Generate All (Hailuo)</button> "
                )
            if fal_available():
                gen_all_buttons += (
                    f'<button class="btn btn-primary btn-generate-all"'
                    f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=fal"'
                    f' hx-target="#generate-all-area"'
                    f' hx-swap="outerHTML"'
                    f" hx-vals='{duration_js_all}'>"
                    f"&#9654; Generate All (Fal)</button> "
                )
            if venice_available():
                gen_all_buttons += (
                    f'<button class="btn btn-primary btn-generate-all"'
                    f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=venice"'
                    f' hx-target="#generate-all-area"'
                    f' hx-swap="outerHTML"'
                    f" hx-vals='{duration_js_all}'>"
                    f"&#9654; Generate All (Venice)</button> "
                )
            if modal_available():
                gen_all_buttons += (
                    f'<button class="btn btn-primary btn-generate-all"'
                    f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=modal"'
                    f' hx-target="#generate-all-area"'
                    f' hx-swap="outerHTML"'
                    f" hx-vals='{duration_js_all}'>"
                    f"&#9654; Generate All (Modal)</button> "
                )
            if meshwell_available():
                gen_all_buttons += (
                    f'<button class="btn btn-meshwell btn-generate-all"'
                    f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=meshwell"'
                    f' hx-target="#generate-all-area"'
                    f' hx-swap="outerHTML"'
                    f" hx-vals='{duration_js_all}'>"
                    f"&#9649; Generate All (Meshwell 3D)</button>"
                )
            generate_all_html = (
                f'<div id="generate-all-area" class="generate-all-section">{gen_all_duration}{gen_all_buttons}</div>'
            )

    combine_html = ""
    if storyboard_id is not None:
        video_count = sum(
            1
            for s in storyboard.scenes
            if s.visual
            and (
                video_exists(storyboard_id, s.scene_number, s.active_version or None)
                or video_exists(storyboard_id, s.scene_number)
            )
        )
        if video_count >= 2:
            combined_exists = combined_video_path(storyboard_id).is_file()
            download_link = ""
            if combined_exists:
                download_link = (
                    f' <a href="/video/{storyboard_id}/combined" class="btn btn-secondary btn-sm"'
                    f' download="combined.mp4">&#11015; Download combined</a>'
                )
            combine_html = (
                f'<div id="combine-area" class="generate-all-section">'
                f'<button class="btn btn-primary"'
                f' hx-post="/storyboard/{storyboard_id}/combine-videos"'
                f' hx-target="#combine-area"'
                f' hx-swap="outerHTML">'
                f"&#127910; Combine {video_count} videos</button>"
                f"{download_link}"
                f"</div>"
            )

    add_chapter_html = ""
    if storyboard_id is not None:
        add_chapter_html = add_chapter_section(storyboard_id)

    outline_html = chapter_outline(storyboard)

    return f"""
    <div class="storyboard">
        <div class="storyboard-header">
            <h2>{storyboard.title}</h2>
            <p class="summary">{storyboard.story_summary}</p>
            <div class="global-meta">
                <span class="meta-label">Art style:</span> {storyboard.global_art_style}
                <span class="meta-sep">|</span>
                <span class="meta-label">Palette:</span> {palette_html}
            </div>
        </div>
        {outline_html}
        <div class="chapter-container" id="chapter-container">
            {chapters_html}
        </div>
        {add_chapter_html}
        {generate_all_html}
        {combine_html}
    </div>
    """


def sidebar_chapters_content(sb: Storyboard, storyboard_id: int) -> str:
    """Chapters tab content for the left sidebar."""
    import html as html_mod

    title = html_mod.escape(sb.title)
    summary = html_mod.escape(sb.story_summary) if sb.story_summary else ""

    # Chapter nav list
    chapter_items = ""
    for i, ch in enumerate(sb.chapters):
        ch_title = html_mod.escape(ch.title) if ch.title else f"Chapter {ch.chapter_number}"
        scene_count = len(ch.scenes)
        scene_label = f"{scene_count} scn"
        # Check if all scenes in chapter have video
        all_have_video = all(
            video_exists(storyboard_id, s.scene_number, s.active_version or None)
            or video_exists(storyboard_id, s.scene_number)
            for s in ch.scenes
            if s.visual
        ) if ch.scenes else False
        badge_cls = "status-badge-done" if all_have_video else "status-badge-pending"
        badge_text = "DONE" if all_have_video else f"{scene_label}"
        active_cls = " active" if i == 0 else ""
        chapter_items += (
            f'<a class="chapter-nav-item{active_cls}" href="#chapter-group-{ch.chapter_number}">'
            f'<span class="chapter-nav-title">{ch_title}</span>'
            f'<span class="status-badge {badge_cls}">{badge_text}</span>'
            f"</a>"
        )

    chapter_nav = f'<ul class="chapter-nav">{chapter_items}</ul>' if chapter_items else ""

    # Story text from first chapter
    story_text_html = ""
    if sb.chapters and sb.chapters[0].story_text:
        escaped_text = html_mod.escape(sb.chapters[0].story_text[:500])
        if len(sb.chapters[0].story_text) > 500:
            escaped_text += "..."
        story_text_html = f'<div class="sidebar-story-text">{escaped_text}</div>'

    # Add chapter section
    add_ch_html = add_chapter_section(storyboard_id)

    return (
        f'<p class="sidebar-title">{title}</p>'
        f'<p class="sidebar-summary">{summary}</p>'
        f"{chapter_nav}"
        f"{story_text_html}"
        f"{add_ch_html}"
    )


def character_card(char, index: int, storyboard_id: int) -> str:
    """Read-only character card for the sidebar."""
    import html as html_mod

    name = html_mod.escape(char.name)
    role = html_mod.escape(char.role) if char.role else ""
    appearance = html_mod.escape(char.appearance) if char.appearance else ""
    description = html_mod.escape(char.description) if char.description else ""

    role_badge = f'<span class="character-card-role">{role}</span>' if role else ""

    appearance_html = f'<div class="character-field"><strong>Look:</strong> {appearance}</div>' if appearance else ""
    description_html = f'<div class="character-field"><strong>Desc:</strong> {description}</div>' if description else ""

    asset_id = getattr(char, "meshwell_asset_id", "") or ""
    if asset_id:
        from .video_meshwell import MESHWELL_BASE
        meshwell_link = f"{MESHWELL_BASE}/#asset-{asset_id}"
        asset_html = (
            f'<div class="character-3d-asset">'
            f'<a href="{meshwell_link}" target="_blank" title="Open in Meshwell">'
            f'<img src="/meshwell/asset/{html_mod.escape(asset_id)}/thumbnail"'
            f' class="character-3d-thumb" alt="3D asset" loading="lazy" />'
            f'</a>'
            f'<div class="character-3d-meta">'
            f'<code>{html_mod.escape(asset_id)}</code>'
            f' <a href="{meshwell_link}" target="_blank" class="meshwell-link"'
            f' title="Open in Meshwell">Open</a>'
            f' <button class="btn btn-secondary btn-xs"'
            f' hx-delete="/storyboard/{storyboard_id}/character/{index}/meshwell-asset"'
            f' hx-target="#character-card-{index}" hx-swap="outerHTML"'
            f' hx-confirm="Unlink 3D asset from this character?"'
            f' title="Unlink 3D asset">Unlink</button>'
            f'</div>'
            f'</div>'
        )
    elif meshwell_available():
        asset_html = (
            f'<div class="character-3d-actions">'
            f'<button class="meshwell-gen-btn"'
            f' hx-post="/storyboard/{storyboard_id}/character/{index}/generate-3d"'
            f' hx-target="#character-card-{index}" hx-swap="outerHTML"'
            f' title="Generate a 3D asset from this character\'s appearance">'
            f'&#9649; Generate 3D</button>'
            f'</div>'
        )
    else:
        asset_html = ""

    # Segmentation reference — show linked preview or assignment UI
    segment_ref = getattr(char, "segment_ref", "") or ""
    segment_html = ""
    if segment_ref:
        # segment_ref is like "scene_1/char_1"
        parts = segment_ref.split("/")
        if len(parts) == 2:
            scene_num = parts[0].replace("scene_", "")
            char_id = parts[1].replace("char_", "")
            gif_url = f"/segment/{storyboard_id}/{scene_num}/{char_id}/preview"
            segment_html = (
                f'<div class="character-segment-ref">'
                f'<img src="{gif_url}" class="character-segment-thumb" alt="Extracted" loading="lazy" />'
                f'<div class="character-segment-meta">'
                f'<small>{html_mod.escape(segment_ref)}</small>'
                f' <button class="btn btn-secondary btn-xs"'
                f' hx-delete="/storyboard/{storyboard_id}/character/{index}/segment-ref"'
                f' hx-target="#character-card-{index}" hx-swap="outerHTML"'
                f' title="Unlink segmentation">Unlink</button>'
                f'</div>'
                f'</div>'
            )
    else:
        # Show assign button — the assignment UI is loaded on demand
        segment_html = (
            f'<div class="character-segment-assign">'
            f'<button class="btn btn-secondary btn-xs"'
            f' hx-get="/storyboard/{storyboard_id}/character/{index}/assign-segment"'
            f' hx-target="#char-segment-picker-{index}" hx-swap="innerHTML"'
            f' title="Link extracted character from segmentation">'
            f'&#9986; Assign Extraction</button>'
            f'<div id="char-segment-picker-{index}"></div>'
            f'</div>'
        )

    return (
        f'<div class="character-card" id="character-card-{index}">'
        f'<div class="character-card-header">'
        f'<span class="character-card-name">{name}</span>'
        f"{role_badge}"
        f'<button class="character-edit-btn"'
        f' hx-get="/storyboard/{storyboard_id}/character/{index}/edit"'
        f' hx-target="#character-card-{index}" hx-swap="outerHTML"'
        f' title="Edit character">&#9998;</button>'
        f"</div>"
        f"{appearance_html}"
        f"{description_html}"
        f"{segment_html}"
        f"{asset_html}"
        f"</div>"
    )


def character_edit_form(char, index: int, storyboard_id: int) -> str:
    """Inline edit form replacing a character card."""
    import html as html_mod

    name = html_mod.escape(char.name)
    role = html_mod.escape(char.role) if char.role else ""
    appearance = html_mod.escape(char.appearance) if char.appearance else ""
    description = html_mod.escape(char.description) if char.description else ""
    meshwell_asset_id = html_mod.escape(getattr(char, "meshwell_asset_id", "") or "")

    return (
        f'<div class="character-card character-card-editing" id="character-card-{index}">'
        f'<form hx-post="/storyboard/{storyboard_id}/character/{index}/save"'
        f' hx-target="#character-card-{index}" hx-swap="outerHTML">'
        f'<div class="character-edit-field">'
        f'<label class="inspector-label">Name</label>'
        f'<input type="text" name="name" value="{name}" class="character-input" readonly />'
        f"</div>"
        f'<div class="character-edit-field">'
        f'<label class="inspector-label">Role</label>'
        f'<input type="text" name="role" value="{role}" class="character-input" />'
        f"</div>"
        f'<div class="character-edit-field">'
        f'<label class="inspector-label">Appearance</label>'
        f'<textarea name="appearance" class="character-textarea" rows="2">{appearance}</textarea>'
        f"</div>"
        f'<div class="character-edit-field">'
        f'<label class="inspector-label">Description</label>'
        f'<textarea name="description" class="character-textarea" rows="2">{description}</textarea>'
        f"</div>"
        f'<div class="character-edit-field">'
        f'<label class="inspector-label">Meshwell Asset ID</label>'
        f'<input type="text" name="meshwell_asset_id" value="{meshwell_asset_id}"'
        f' class="character-input" placeholder="Leave empty to auto-generate" />'
        f"</div>"
        f'<div class="character-edit-actions">'
        f'<button type="submit" class="btn btn-primary btn-sm">Save</button>'
        f'<button type="button" class="btn btn-secondary btn-sm"'
        f' hx-get="/storyboard/{storyboard_id}/character/{index}/card"'
        f' hx-target="#character-card-{index}" hx-swap="outerHTML">Cancel</button>'
        f"</div>"
        f"</form>"
        f"</div>"
    )


def sidebar_characters_content(sb: Storyboard, storyboard_id: int) -> str:
    """Characters tab content for the left sidebar."""
    if not sb.characters:
        return '<div class="sidebar-empty">No characters yet. Generate a storyboard first.</div>'

    cards = "".join(character_card(c, i, storyboard_id) for i, c in enumerate(sb.characters))

    # "Generate All 3D Assets" button if meshwell is available and any character lacks an asset
    gen_all_3d = ""
    if meshwell_available():
        unlinked = sum(1 for c in sb.characters if not getattr(c, "meshwell_asset_id", ""))
        if unlinked > 0:
            gen_all_3d = (
                f'<div class="generate-all-3d-section">'
                f'<button class="btn btn-meshwell btn-sm"'
                f' hx-post="/storyboard/{storyboard_id}/characters/generate-all-3d"'
                f' hx-target="#characters-tab-content" hx-swap="innerHTML"'
                f' title="Generate 3D assets for all {unlinked} characters without one">'
                f'&#9649; Generate All 3D Assets ({unlinked})</button>'
                f'</div>'
            )

    return f'<div class="character-list" id="characters-tab-content">{cards}{gen_all_3d}</div>'


def left_sidebar(sb: Storyboard, storyboard_id: int) -> str:
    """Left sidebar panel — tab switcher between Chapters and Characters."""
    chapters_content = sidebar_chapters_content(sb, storyboard_id)

    return (
        f'<div class="panel-header">Project</div>'
        f'<div class="sidebar-tabs">'
        f'<button class="sidebar-tab active"'
        f' hx-get="/storyboard/{storyboard_id}/sidebar/chapters"'
        f' hx-target="#sidebar-tab-content" hx-swap="innerHTML"'
        f' onclick="this.parentElement.querySelectorAll(\'.sidebar-tab\').forEach(t=>t.classList.remove(\'active\'));this.classList.add(\'active\');"'
        f">Chapters</button>"
        f'<button class="sidebar-tab"'
        f' hx-get="/storyboard/{storyboard_id}/sidebar/characters"'
        f' hx-target="#sidebar-tab-content" hx-swap="innerHTML"'
        f' onclick="this.parentElement.querySelectorAll(\'.sidebar-tab\').forEach(t=>t.classList.remove(\'active\'));this.classList.add(\'active\');"'
        f">Characters</button>"
        f"</div>"
        f'<div id="sidebar-tab-content">{chapters_content}</div>'
    )


def compact_scene_card(scene: Scene, storyboard_id: int, is_selected: bool = False) -> str:
    """Compact horizontal scene card for the center panel. Keeps id='scene-card-{n}' for OOB swaps."""
    import html as html_mod

    n = scene.scene_number
    selected_cls = " compact-card-selected" if is_selected else ""
    title = html_mod.escape(scene.title) if scene.title else f"Scene {n}"
    action = html_mod.escape(scene.action[:80]) if scene.action else ""
    if scene.action and len(scene.action) > 80:
        action += "..."

    # Thumbnail — show video if exists, otherwise gradient placeholder
    thumb_inner = ""
    ver = scene.active_version if scene.active_version else None
    has_vid = video_exists(storyboard_id, n, ver) or video_exists(storyboard_id, n)
    if has_vid:
        if ver and video_exists(storyboard_id, n, ver):
            vpath = video_path_for(storyboard_id, n, ver)
            mtime = int(vpath.stat().st_mtime) if vpath.is_file() else 0
            vid_url = f"/video/{storyboard_id}/{n}?version={ver}&t={mtime}"
        else:
            vpath = video_path_for(storyboard_id, n)
            mtime = int(vpath.stat().st_mtime) if vpath.is_file() else 0
            vid_url = f"/video/{storyboard_id}/{n}?t={mtime}"
        thumb_inner = f'<video src="{vid_url}" muted></video>'
        thumb_inner += '<span class="compact-card-status compact-card-status-done">VIDEO</span>'
    else:
        thumb_inner = '<span class="compact-card-status">NO VIDEO</span>'

    # Metadata row
    meta = ""
    if scene.visual:
        meta_parts = []
        if scene.mood:
            meta_parts.append(f'<span class="meta-tag">{html_mod.escape(scene.mood[:20])}</span>')
        if scene.visual.camera_angle:
            meta_parts.append(f'<span class="meta-tag">{html_mod.escape(scene.visual.camera_angle[:30])}</span>')
        meta_parts.append(f'<span>{scene.duration_seconds}s</span>')
        meta = f'<div class="compact-card-meta-row">{"".join(meta_parts)}</div>'

    return (
        f'<div class="compact-scene-card{selected_cls}" id="scene-card-{n}"'
        f' hx-get="/storyboard/{storyboard_id}/scene/{n}/inspector"'
        f' hx-target="#scene-inspector" hx-swap="innerHTML"'
        f' onclick="document.querySelectorAll(\'.compact-scene-card\').forEach(c=>c.classList.remove(\'compact-card-selected\'));this.classList.add(\'compact-card-selected\');">'
        f'<div class="compact-card-thumb">{thumb_inner}</div>'
        f'<div class="compact-card-body">'
        f'<span class="compact-card-number">SCN-{n:03d}</span>'
        f'<span class="compact-card-title">{title}</span>'
        f'<button class="btn-delete-small scene-remove-btn"'
        f' hx-delete="/storyboard/{storyboard_id}/scene/{n}/remove"'
        f' hx-target="#chapter-container" hx-swap="innerHTML"'
        f' hx-confirm="Delete scene {n}? This cannot be undone."'
        f' onclick="event.stopPropagation();"'
        f' title="Delete scene">&#10005;</button>'
        f'<span class="compact-card-action">{action}</span>'
        f"{meta}"
        f"</div>"
        f"</div>"
    )


def _generate_all_section(sb: Storyboard, storyboard_id: int) -> str:
    """Render the generate-all-videos section for the center panel."""
    if not (hailuo_available() or fal_available() or venice_available() or meshwell_available() or modal_available()):
        return ""
    any_missing = any(
        s.visual
        and not video_exists(storyboard_id, s.scene_number, s.active_version or None)
        and not video_exists(storyboard_id, s.scene_number)
        for s in sb.scenes
    )
    if not any_missing:
        return ""
    duration_js_all = "js:{duration: document.getElementById(&quot;video-duration-all&quot;).value}"
    gen_all_duration = (
        '<select id="video-duration-all" class="video-duration-select">'
        '<option value="5">5s</option>'
        '<option value="6">6s</option>'
        '<option value="10">10s</option>'
        "</select> "
    )
    gen_all_buttons = ""
    if hailuo_available():
        gen_all_buttons += (
            f'<button class="btn btn-primary btn-generate-all"'
            f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=hailuo"'
            f' hx-target="#generate-all-area"'
            f' hx-swap="outerHTML"'
            f" hx-vals='{duration_js_all}'>"
            f"&#9654; Generate All (Hailuo)</button> "
        )
    if fal_available():
        gen_all_buttons += (
            f'<button class="btn btn-primary btn-generate-all"'
            f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=fal"'
            f' hx-target="#generate-all-area"'
            f' hx-swap="outerHTML"'
            f" hx-vals='{duration_js_all}'>"
            f"&#9654; Generate All (Fal)</button> "
        )
    if venice_available():
        gen_all_buttons += (
            f'<button class="btn btn-primary btn-generate-all"'
            f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=venice"'
            f' hx-target="#generate-all-area"'
            f' hx-swap="outerHTML"'
            f" hx-vals='{duration_js_all}'>"
            f"&#9654; Generate All (Venice)</button> "
        )
    if modal_available():
        gen_all_buttons += (
            f'<button class="btn btn-primary btn-generate-all"'
            f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=modal"'
            f' hx-target="#generate-all-area"'
            f' hx-swap="outerHTML"'
            f" hx-vals='{duration_js_all}'>"
            f"&#9654; Generate All (Modal)</button> "
        )
    if meshwell_available():
        gen_all_buttons += (
            f'<button class="btn btn-meshwell btn-generate-all"'
            f' hx-post="/storyboard/{storyboard_id}/generate-all-video?backend=meshwell"'
            f' hx-target="#generate-all-area"'
            f' hx-swap="outerHTML"'
            f" hx-vals='{duration_js_all}'>"
            f"&#9649; Generate All (Meshwell 3D)</button>"
        )
    return f'<div id="generate-all-area" class="generate-all-section">{gen_all_duration}{gen_all_buttons}</div>'


def _combine_section(sb: Storyboard, storyboard_id: int) -> str:
    """Render the combine-videos section for the center panel."""
    video_count = sum(
        1
        for s in sb.scenes
        if s.visual
        and (
            video_exists(storyboard_id, s.scene_number, s.active_version or None)
            or video_exists(storyboard_id, s.scene_number)
        )
    )
    if video_count < 2:
        return ""
    combined_exists = combined_video_path(storyboard_id).is_file()
    download_link = ""
    if combined_exists:
        download_link = (
            f' <a href="/video/{storyboard_id}/combined" class="btn btn-secondary btn-sm"'
            f' download="combined.mp4">&#11015; Download combined</a>'
        )
    compositor_link = ""
    if segmentation_available():
        compositor_link = (
            f' <a href="/storyboard/{storyboard_id}/compositor"'
            f' class="btn btn-secondary btn-sm">&#9986; Compositor</a>'
        )
    return (
        f'<div id="combine-area" class="generate-all-section">'
        f'<button class="btn btn-primary"'
        f' hx-post="/storyboard/{storyboard_id}/combine-videos"'
        f' hx-target="#combine-area"'
        f' hx-swap="outerHTML">'
        f"&#127910; Combine {video_count} videos</button>"
        f"{download_link}"
        f"{compositor_link}"
        f"</div>"
    )


def _chapter_actions_bar(sb: Storyboard, storyboard_id: int, ch_num: int) -> str:
    """Render chapter action buttons — uniform row of toggles, with collapsible forms below."""
    sb_id = storyboard_id
    modify_form_id = f"ch-modify-form-{ch_num}"
    modify_btn_id = f"ch-modify-btn-{ch_num}"
    modify_output_id = f"ch-modify-output-{ch_num}"
    cont_form_id = f"ch-cont-form-{ch_num}"
    cont_btn_id = f"ch-cont-btn-{ch_num}"
    cont_output_id = f"ch-cont-output-{ch_num}"
    auto_cont_form_id = f"ch-auto-cont-form-{ch_num}"
    auto_cont_btn_id = f"ch-auto-cont-btn-{ch_num}"
    auto_cont_output_id = f"ch-auto-cont-output-{ch_num}"
    rewrite_form_id = f"ch-rewrite-form-{ch_num}"
    rewrite_btn_id = f"ch-rewrite-btn-{ch_num}"
    rewrite_output_id = f"ch-rewrite-output-{ch_num}"

    toggle = (
        "let f=document.getElementById('{fid}');"
        "f.style.display=f.style.display==='none'?'block':'none';"
    )

    buttons_row = (
        f'<div class="chapter-actions-row">'
        f'<button type="button" class="chapter-action-btn" onclick="{toggle.format(fid=modify_form_id)}">'
        f"Modify all</button>"
        f'<button class="chapter-action-btn"'
        f' hx-post="/storyboard/{sb_id}/chapter/{ch_num}/snapshot-all"'
        f' hx-target="#chapter-group-{ch_num}" hx-swap="outerHTML"'
        f' hx-confirm="Create a new version for all scenes in this chapter?"'
        f' title="Snapshot all scenes to a new version">'
        f"Snapshot</button>"
        f'<button type="button" class="chapter-action-btn" onclick="{toggle.format(fid=cont_form_id)}">'
        f"Continue</button>"
        f'<button type="button" class="chapter-action-btn" onclick="{toggle.format(fid=auto_cont_form_id)}">'
        f"Auto-continue</button>"
        f'<button type="button" class="chapter-action-btn" onclick="{toggle.format(fid=rewrite_form_id)}">'
        f"Rewrite</button>"
        f"</div>"
    )

    # Hidden forms that expand below
    forms = (
        # Modify all
        f'<div id="{modify_form_id}" style="display:none;" class="chapter-action-form">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/modify"'
        f' hx-target="#{modify_output_id}" hx-swap="innerHTML" hx-indicator="#{modify_btn_id}">'
        f'<textarea name="instruction" class="modify-textarea" rows="2"'
        f' placeholder="e.g. Change the art style to watercolor..."></textarea>'
        f'<button type="submit" id="{modify_btn_id}" class="btn btn-primary btn-sm">'
        f'<span>Apply to chapter</span><span class="spinner" aria-hidden="true"></span></button>'
        f"</form>"
        f'<div id="{modify_output_id}"></div>'
        f"</div>"
        # Continue
        f'<div id="{cont_form_id}" style="display:none;" class="chapter-action-form">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/continue"'
        f' hx-target="#{cont_output_id}" hx-swap="innerHTML" hx-indicator="#{cont_btn_id}"'
        f' class="continuation-form">'
        f'<textarea name="story" placeholder="Paste continuation text..." rows="6" class="modify-textarea"></textarea>'
        f'<button type="submit" id="{cont_btn_id}" class="btn btn-primary btn-sm">'
        f'<span>Generate</span><span class="spinner" aria-hidden="true"></span></button>'
        f"</form>"
        f'<div id="{cont_output_id}"></div>'
        f"</div>"
        # Auto-continue
        f'<div id="{auto_cont_form_id}" style="display:none;" class="chapter-action-form">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/auto-continue"'
        f' hx-target="#{auto_cont_output_id}" hx-swap="innerHTML" hx-indicator="#{auto_cont_btn_id}"'
        f' class="continuation-form">'
        f'<label>Scenes: <input type="number" name="num_scenes" value="3" min="1" max="10"'
        f' class="rewrite-input" style="width:60px;display:inline-block;margin-left:4px;"></label> '
        f'<input type="text" name="instruction" class="rewrite-input" style="margin-top:0.4rem;"'
        f' placeholder="Optional direction...">'
        f'<button type="submit" id="{auto_cont_btn_id}" class="btn btn-primary btn-sm" style="margin-top:0.4rem;">'
        f'<span>Generate</span><span class="spinner" aria-hidden="true"></span></button>'
        f"</form>"
        f'<div id="{auto_cont_output_id}"></div>'
        f"</div>"
        # Rewrite
        f'<div id="{rewrite_form_id}" style="display:none;" class="chapter-action-form">'
        f'<form hx-post="/storyboard/{sb_id}/chapter/{ch_num}/rewrite"'
        f' hx-target="#{rewrite_output_id}" hx-swap="innerHTML" hx-indicator="#{rewrite_btn_id}">'
        f'<input type="text" name="instruction" class="rewrite-input"'
        f' placeholder="e.g. Change the setting to cyberpunk Tokyo...">'
        f'<button type="submit" id="{rewrite_btn_id}" class="btn btn-primary btn-sm" style="margin-top:0.4rem;">'
        f'<span>Rewrite &amp; Regenerate</span><span class="spinner" aria-hidden="true"></span></button>'
        f"</form>"
        f'<div id="{rewrite_output_id}"></div>'
        f"</div>"
    )

    return f'<div class="chapter-actions">{buttons_row}{forms}</div>'


_CHAPTER_DRAG_JS = """
<script>
(function() {
    var container = document.getElementById('chapter-container');
    if (!container) return;
    var dragItem = null;

    // Only allow drag when mousedown starts on the handle
    container.addEventListener('mousedown', function(e) {
        var handle = e.target.closest('.chapter-drag-handle');
        if (!handle) return;
        var item = handle.closest('.chapter-drag-item');
        if (item) item.setAttribute('draggable', 'true');
    });
    document.addEventListener('mouseup', function() {
        container.querySelectorAll('.chapter-drag-item[draggable]').forEach(function(el) {
            el.removeAttribute('draggable');
        });
    });

    container.addEventListener('dragstart', function(e) {
        var item = e.target.closest('.chapter-drag-item');
        if (!item) return;
        dragItem = item;
        item.classList.add('chapter-dragging');
        e.dataTransfer.effectAllowed = 'move';
    });

    container.addEventListener('dragend', function(e) {
        if (dragItem) {
            dragItem.classList.remove('chapter-dragging');
            dragItem.removeAttribute('draggable');
        }
        container.querySelectorAll('.chapter-drag-item').forEach(function(el) {
            el.classList.remove('chapter-drag-over');
        });
        dragItem = null;
    });

    container.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        var target = e.target.closest('.chapter-drag-item');
        if (!target || target === dragItem) return;
        container.querySelectorAll('.chapter-drag-item').forEach(function(el) {
            el.classList.remove('chapter-drag-over');
        });
        target.classList.add('chapter-drag-over');
    });

    container.addEventListener('dragleave', function(e) {
        var target = e.target.closest('.chapter-drag-item');
        if (target) target.classList.remove('chapter-drag-over');
    });

    container.addEventListener('drop', function(e) {
        e.preventDefault();
        var target = e.target.closest('.chapter-drag-item');
        if (!target || !dragItem || target === dragItem) return;
        target.classList.remove('chapter-drag-over');

        var rect = target.getBoundingClientRect();
        var midY = rect.top + rect.height / 2;
        if (e.clientY < midY) {
            container.insertBefore(dragItem, target);
        } else {
            container.insertBefore(dragItem, target.nextSibling);
        }

        var items = container.querySelectorAll('.chapter-drag-item');
        var order = [];
        items.forEach(function(el) { order.push(el.dataset.chapter); });
        var sbId = container.dataset.storyboardId;

        fetch('/storyboard/' + sbId + '/reorder-chapters', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({order: order})
        }).then(function(resp) {
            if (resp.ok) { window.location.reload(); }
        });
    });
})();
</script>
"""


def center_panel(sb: Storyboard, storyboard_id: int) -> str:
    """Center panel — compact scene cards with chapter dividers and action bars."""
    import html as html_mod

    html_parts = ['<div class="panel-header">Scenes</div>']

    # Global metadata
    palette_html = "".join(
        f'<span class="color-dot" style="background:{c}" title="{c}"></span>' for c in sb.global_color_palette
    )
    html_parts.append(
        f'<div class="global-meta" style="margin-bottom:1rem;">'
        f'<span class="meta-label">Art style:</span> {sb.global_art_style}'
        f'<span class="meta-sep">|</span>'
        f'<span class="meta-label">Palette:</span> {palette_html}'
        f"</div>"
    )

    multi_chapter = len(sb.chapters) > 1
    html_parts.append(f'<div id="chapter-container" data-storyboard-id="{storyboard_id}">')
    for ch in sb.chapters:
        ch_title = html_mod.escape(ch.title) if ch.title else f"Chapter {ch.chapter_number}"
        version_html = chapter_version_indicator(ch, storyboard_id)
        remove_html = ""
        if multi_chapter:
            remove_html = chapter_remove_button(storyboard_id, ch.chapter_number)

        drag_handle = ""
        drag_attr = ""
        if multi_chapter:
            drag_handle = '<span class="chapter-drag-handle" title="Drag to reorder">&#9776;</span>'
            drag_attr = ' draggable="true"'

        html_parts.append(
            f'<div id="chapter-group-{ch.chapter_number}" class="chapter-drag-item" data-chapter="{ch.chapter_number}"{drag_attr}>'
            f'<div class="chapter-divider">{drag_handle}CHAPTER {ch.chapter_number:02d} --- {ch_title} {version_html}{remove_html}</div>'
        )

        # Compact scene cards
        for i, s in enumerate(ch.scenes):
            is_first = (ch == sb.chapters[0] and i == 0)
            html_parts.append(compact_scene_card(s, storyboard_id, is_selected=is_first))

        # Chapter actions
        html_parts.append(_chapter_actions_bar(sb, storyboard_id, ch.chapter_number))
        html_parts.append("</div>")

    html_parts.append("</div>")

    # Generate-all and combine sections
    html_parts.append(_generate_all_section(sb, storyboard_id))
    html_parts.append(_combine_section(sb, storyboard_id))

    if multi_chapter:
        html_parts.append(_CHAPTER_DRAG_JS)

    return "".join(html_parts)


def scene_inspector(scene, sb: Storyboard, storyboard_id: int, prev_has_video: bool = False) -> str:
    """Right sidebar inspector — full scene detail view."""
    if scene is None:
        return '<div class="inspector-empty">Select a scene to inspect</div>'

    import html as html_mod
    n = scene.scene_number
    parts = [f'<div class="panel-header">Inspector &mdash; SCN-{n:03d}</div>']

    # Title, setting, action, mood
    parts.append(f'<div class="inspector-section">')
    parts.append(f'<h3 class="inspector-title">{html_mod.escape(scene.title)}</h3>')
    if scene.setting:
        parts.append(f'<div class="inspector-label">Setting</div>')
        parts.append(f'<div class="inspector-field">{html_mod.escape(scene.setting)}</div>')
    if scene.action:
        parts.append(f'<div class="inspector-label">Action</div>')
        parts.append(f'<div class="inspector-field">{html_mod.escape(scene.action)}</div>')
    if scene.mood:
        parts.append(f'<div class="inspector-label">Mood</div>')
        parts.append(f'<div class="inspector-field">{html_mod.escape(scene.mood)}</div>')
    parts.append("</div>")

    # Characters
    if scene.characters:
        parts.append('<div class="inspector-section">')
        parts.append('<div class="inspector-label">Characters</div>')
        for c in scene.characters:
            parts.append(
                f'<div class="inspector-field"><strong>{html_mod.escape(c.name)}</strong>'
                f' <span style="color:var(--fg2)">({html_mod.escape(c.role)})</span>'
                f" &mdash; {html_mod.escape(c.appearance)}</div>"
            )
        parts.append("</div>")

    # Dialogue
    if scene.dialogue:
        parts.append('<div class="inspector-section">')
        parts.append('<div class="inspector-label">Dialogue</div>')
        for d in scene.dialogue:
            parts.append(f'<div class="inspector-field" style="font-style:italic">&ldquo;{html_mod.escape(str(d))}&rdquo;</div>')
        parts.append("</div>")

    # Modify section
    parts.append(f'<div class="inspector-section">{modify_scene_section(n, storyboard_id)}</div>')

    # Visual details
    if scene.visual:
        v = scene.visual
        palette_dots = "".join(
            f'<span class="color-dot" style="background:{c}" title="{c}"></span>' for c in v.color_palette
        )
        parts.append('<div class="inspector-section">')
        parts.append('<div class="inspector-label">Visual Details</div>')
        parts.append('<div class="inspector-visual-grid">')
        parts.append(f'<div><span class="meta-label">Camera:</span> {html_mod.escape(v.camera_angle)}</div>')
        parts.append(f'<div><span class="meta-label">Lighting:</span> {html_mod.escape(v.lighting)}</div>')
        parts.append(f'<div><span class="meta-label">Art style:</span> {html_mod.escape(v.art_style)}</div>')
        parts.append(f'<div><span class="meta-label">Motion:</span> {html_mod.escape(v.motion)}</div>')
        parts.append(f'<div><span class="meta-label">Palette:</span> {palette_dots}</div>')
        parts.append("</div>")
        parts.append("</div>")

        # Keyword chips
        parts.append(f'<div class="inspector-section">{_keyword_chips(n)}</div>')

        # Prompt section
        parts.append(f'<div class="inspector-section">{prompt_section(v.video_gen_prompt, n, storyboard_id)}</div>')

    # Version indicator & history
    version_html = _version_indicator(scene, storyboard_id)
    version_history_html = _version_history(scene, storyboard_id)
    if version_html or version_history_html:
        parts.append(f'<div class="inspector-section">{version_html}{version_history_html}</div>')

    # Video section
    video_html = _video_section(scene, storyboard_id, prev_has_video=prev_has_video)
    if video_html:
        parts.append(f'<div class="inspector-section" id="video-section-{n}-inspector">{video_html}</div>')

    # Segmentation button (only when SAM2 available and scene has a video)
    if segmentation_available():
        version = scene.active_version or None
        has_video = video_exists(storyboard_id, n, version)
        if not has_video and version is not None:
            has_video = video_exists(storyboard_id, n, None)
        if has_video:
            parts.append(
                f'<div class="inspector-section segment-section">'
                f'<button class="btn btn-secondary btn-sm"'
                f' hx-post="/storyboard/{storyboard_id}/scene/{n}/segment"'
                f' hx-target="#segment-area-{n}"'
                f' hx-swap="innerHTML">'
                f'&#9986; Extract Characters</button>'
                f'<div id="segment-area-{n}"></div>'
                f'</div>'
            )

    return "".join(parts)


def delete_storyboard_button(storyboard_id: int) -> str:
    """Render a delete button for the storyboard detail page."""
    return (
        f'<div class="delete-storyboard-section">'
        f'<button class="btn btn-danger"'
        f' hx-delete="/storyboard/{storyboard_id}"'
        f' hx-confirm="Delete this storyboard and all its videos? This cannot be undone."'
        f">&#128465; Delete storyboard</button>"
        f"</div>"
    )


def save_notice(storyboard_id: int) -> str:
    """Green banner shown after a storyboard is saved to the database."""
    return f'<div class="save-notice">Saved! <a href="/storyboard/{storyboard_id}">View permalink</a></div>'


def storyboard_list(rows) -> FT:
    """Render a list of saved storyboard cards."""
    if not rows:
        return P("No saved storyboards yet.", cls="subtitle")
    cards = [storyboard_list_card(row) for row in rows]
    combine_btn = Div(
        Button(
            "Combine selected",
            cls="btn btn-primary",
            id="combine-storyboards-btn",
            style="display:none;",
            type="button",
            onclick="openCombineModal()",
        ),
        cls="combine-storyboards-row",
    )
    modal = NotStr(_combine_reorder_modal())
    script = Script(_COMBINE_REORDER_JS)
    return Div(*cards, combine_btn, modal, script, cls="storyboard-list")


_COMBINE_REORDER_JS = """
(function() {
    // Show/hide the "Combine selected" button based on checkbox count
    document.addEventListener('change', function(e) {
        if (!e.target.classList.contains('combine-checkbox')) return;
        var checked = document.querySelectorAll('.combine-checkbox:checked');
        var btn = document.getElementById('combine-storyboards-btn');
        if (btn) btn.style.display = checked.length >= 2 ? 'inline-block' : 'none';
    });

    // Drag-and-drop state
    var dragItem = null;

    window.openCombineModal = function() {
        var modal = document.getElementById('combine-modal');
        var list = document.getElementById('combine-reorder-list');
        list.innerHTML = '';
        var checked = document.querySelectorAll('.combine-checkbox:checked');
        checked.forEach(function(cb) {
            var item = document.getElementById('storyboard-item-' + cb.value);
            var title = item ? item.querySelector('.list-card-title').textContent : 'Storyboard ' + cb.value;
            var li = document.createElement('li');
            li.className = 'reorder-item';
            li.setAttribute('draggable', 'true');
            li.dataset.id = cb.value;
            li.innerHTML = '<span class="reorder-handle">&#9776;</span> ' +
                '<span class="reorder-title">' + title + '</span>';
            list.appendChild(li);
        });
        modal.style.display = 'flex';
        initDrag(list);
    };

    window.closeCombineModal = function() {
        document.getElementById('combine-modal').style.display = 'none';
    };

    window.confirmCombine = function() {
        var items = document.querySelectorAll('#combine-reorder-list .reorder-item');
        var form = document.getElementById('combine-hidden-form');
        // Clear old hidden inputs
        form.querySelectorAll('input[name="storyboard_ids"]').forEach(function(el) { el.remove(); });
        items.forEach(function(li) {
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'storyboard_ids';
            input.value = li.dataset.id;
            form.appendChild(input);
        });
        closeCombineModal();
        htmx.trigger(form, 'submit');
    };

    function initDrag(list) {
        list.querySelectorAll('.reorder-item').forEach(function(item) {
            item.addEventListener('dragstart', function(e) {
                dragItem = this;
                this.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            item.addEventListener('dragend', function() {
                this.classList.remove('dragging');
                dragItem = null;
                // Remove all drop indicators
                list.querySelectorAll('.reorder-item').forEach(function(el) {
                    el.classList.remove('drag-over-above', 'drag-over-below');
                });
            });
            item.addEventListener('dragover', function(e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (this === dragItem) return;
                var rect = this.getBoundingClientRect();
                var midY = rect.top + rect.height / 2;
                // Clear indicators on all items
                list.querySelectorAll('.reorder-item').forEach(function(el) {
                    el.classList.remove('drag-over-above', 'drag-over-below');
                });
                if (e.clientY < midY) {
                    this.classList.add('drag-over-above');
                } else {
                    this.classList.add('drag-over-below');
                }
            });
            item.addEventListener('dragleave', function() {
                this.classList.remove('drag-over-above', 'drag-over-below');
            });
            item.addEventListener('drop', function(e) {
                e.preventDefault();
                if (this === dragItem) return;
                var rect = this.getBoundingClientRect();
                var midY = rect.top + rect.height / 2;
                if (e.clientY < midY) {
                    list.insertBefore(dragItem, this);
                } else {
                    list.insertBefore(dragItem, this.nextSibling);
                }
                this.classList.remove('drag-over-above', 'drag-over-below');
            });
        });
    }

    // Touch drag support for mobile
    var touchItem = null, touchClone = null, touchStartY = 0;
    document.addEventListener('touchstart', function(e) {
        var item = e.target.closest('.reorder-item');
        if (!item || !document.getElementById('combine-modal').contains(item)) return;
        touchItem = item;
        touchStartY = e.touches[0].clientY;
        item.classList.add('dragging');
    }, {passive: true});
    document.addEventListener('touchmove', function(e) {
        if (!touchItem) return;
        e.preventDefault();
        var list = document.getElementById('combine-reorder-list');
        var touchY = e.touches[0].clientY;
        var items = Array.from(list.querySelectorAll('.reorder-item'));
        items.forEach(function(el) { el.classList.remove('drag-over-above', 'drag-over-below'); });
        for (var i = 0; i < items.length; i++) {
            if (items[i] === touchItem) continue;
            var rect = items[i].getBoundingClientRect();
            if (touchY > rect.top && touchY < rect.bottom) {
                var midY = rect.top + rect.height / 2;
                if (touchY < midY) {
                    items[i].classList.add('drag-over-above');
                } else {
                    items[i].classList.add('drag-over-below');
                }
                break;
            }
        }
    }, {passive: false});
    document.addEventListener('touchend', function(e) {
        if (!touchItem) return;
        var list = document.getElementById('combine-reorder-list');
        var touchY = e.changedTouches[0].clientY;
        var items = Array.from(list.querySelectorAll('.reorder-item'));
        for (var i = 0; i < items.length; i++) {
            if (items[i] === touchItem) continue;
            var rect = items[i].getBoundingClientRect();
            if (touchY > rect.top && touchY < rect.bottom) {
                var midY = rect.top + rect.height / 2;
                if (touchY < midY) {
                    list.insertBefore(touchItem, items[i]);
                } else {
                    list.insertBefore(touchItem, items[i].nextSibling);
                }
                break;
            }
        }
        touchItem.classList.remove('dragging');
        items.forEach(function(el) { el.classList.remove('drag-over-above', 'drag-over-below'); });
        touchItem = null;
    });
})();
"""


def _combine_reorder_modal() -> str:
    """Render the hidden modal for drag-to-reorder before combining storyboards."""
    return (
        '<div id="combine-modal" class="combine-modal-overlay" style="display:none;">'
        '<div class="combine-modal">'
        "<h3>Order chapters</h3>"
        '<p class="combine-modal-hint">Drag to reorder. Top = first chapter.</p>'
        '<ul id="combine-reorder-list" class="reorder-list"></ul>'
        '<div class="combine-modal-warning">'
        "Originals will be deleted. Existing videos will be <strong>carried over</strong> to the combined storyboard."
        "</div>"
        '<div class="combine-modal-buttons">'
        '<button type="button" class="btn btn-secondary" onclick="closeCombineModal()">Cancel</button>'
        '<button type="button" class="btn btn-primary" onclick="confirmCombine()">Combine</button>'
        "</div>"
        '<form id="combine-hidden-form" hx-post="/storyboards/combine" style="display:none;"></form>'
        "</div>"
        "</div>"
    )


def storyboard_list_card(row) -> FT:
    """Clickable card with title and date for the storyboard list."""
    created = row.created_at[:16].replace("T", " ") if row.created_at else ""
    return Div(
        Input(
            type="checkbox",
            name="storyboard_ids",
            value=str(row.id),
            cls="combine-checkbox",
        ),
        A(
            Div(
                Div(Strong(row.storyboard_title or "Untitled"), cls="list-card-title"),
                Div(created, cls="list-card-date"),
                cls="list-card-content",
            ),
            href=f"/storyboard/{row.id}",
            cls="list-card-link",
        ),
        Button(
            NotStr("&#10005;"),
            cls="btn-delete-small",
            hx_delete=f"/storyboard/{row.id}",
            hx_target=f"#storyboard-item-{row.id}",
            hx_swap="outerHTML",
            hx_confirm="Delete this storyboard and all its videos?",
        ),
        cls="storyboard-list-item",
        id=f"storyboard-item-{row.id}",
    )


def prompt_section(video_gen_prompt: str, scene_number: int, storyboard_id=None, saved: bool = False) -> str:
    """Render the Video Gen Prompt section — editable form on detail pages, static <pre> during SSE."""
    import html as html_mod

    n = scene_number
    escaped = html_mod.escape(video_gen_prompt)
    if storyboard_id is not None:
        saved_flash = '<span class="prompt-saved-flash">Saved!</span>' if saved else ""
        return (
            f'<div class="scene-section" id="prompt-section-{n}">'
            f'<div class="prompt-header"><h4>Video Gen Prompt {saved_flash}</h4>'
            f'<button type="button" class="btn-expand-prompt" onclick="openPromptModal(\'prompt-textarea-{n}\')" title="Expand">&#x26F6;</button></div>'
            f'<form hx-post="/storyboard/{storyboard_id}/scene/{n}/update-prompt"'
            f' hx-target="#prompt-section-{n}" hx-swap="outerHTML">'
            f'<textarea id="prompt-textarea-{n}" name="video_gen_prompt" class="prompt-textarea" rows="4">{escaped}</textarea>'
            f'<button type="submit" class="btn btn-secondary btn-sm">Save</button>'
            f"</form>"
            f"</div>"
        )
    return f'<div class="scene-section"><h4>Video Gen Prompt</h4><pre class="video-prompt">{escaped}</pre></div>'


def modify_scene_section(scene_number: int, storyboard_id: int) -> str:
    """Collapsible 'Modify with prompt' form inside a scene card (detail pages only)."""
    return (
        f'<div class="modify-scene-section" id="modify-section-{scene_number}">'
        f'<button type="button" class="modify-toggle" '
        f"onclick=\"let f=document.getElementById('modify-form-{scene_number}');"
        f"f.style.display=f.style.display==='none'?'block':'none';\">"
        f"&#9998; Modify with prompt</button>"
        f'<div id="modify-form-{scene_number}" style="display:none;">'
        f'<form hx-post="/storyboard/{storyboard_id}/scene/{scene_number}/modify"'
        f' hx-target="#modify-output-{scene_number}" hx-swap="innerHTML"'
        f' hx-indicator="#modify-btn-{scene_number}">'
        f'<textarea name="instruction" class="modify-textarea" rows="2"'
        f' placeholder="e.g. Make the lighting more dramatic..."></textarea>'
        f'<button type="submit" id="modify-btn-{scene_number}"'
        f' class="btn btn-secondary btn-sm">'
        f"<span>Apply</span>"
        f'<span class="spinner" aria-hidden="true"></span>'
        f"</button>"
        f"</form>"
        f'<div id="modify-output-{scene_number}"></div>'
        f"</div>"
        f"</div>"
    )


def _version_indicator(scene: Scene, storyboard_id) -> str:
    """Render version indicator with prev/next navigation buttons."""
    if not scene.versions or storyboard_id is None:
        return ""
    n = scene.scene_number
    total = len(scene.versions)
    cur = scene.active_version

    prev_btn = ""
    next_btn = ""
    if cur > 1:
        prev_ver = cur - 1
        prev_btn = (
            f'<button class="btn-version-nav"'
            f' hx-post="/storyboard/{storyboard_id}/scene/{n}/version/{prev_ver}"'
            f' hx-target="#scene-card-{n}" hx-swap="outerHTML"'
            f">&laquo;</button>"
        )
    if cur < total:
        next_ver = cur + 1
        next_btn = (
            f'<button class="btn-version-nav"'
            f' hx-post="/storyboard/{storyboard_id}/scene/{n}/version/{next_ver}"'
            f' hx-target="#scene-card-{n}" hx-swap="outerHTML"'
            f">&raquo;</button>"
        )

    return f'<span class="version-indicator">{prev_btn} v{cur} of {total} {next_btn}</span>'


def _version_history(scene: Scene, storyboard_id) -> str:
    """Render collapsible version history section."""
    if not scene.versions or storyboard_id is None or len(scene.versions) <= 1:
        return ""
    import html as html_mod

    n = scene.scene_number
    rows = ""
    for v in scene.versions:
        ts = v.timestamp[:16].replace("T", " ") if v.timestamp else ""
        instr = html_mod.escape(v.instruction)
        active_cls = ' class="version-active"' if v.version == scene.active_version else ""
        restore_btn = ""
        if v.version != scene.active_version:
            restore_btn = (
                f'<button class="btn btn-secondary btn-sm"'
                f' hx-post="/storyboard/{storyboard_id}/scene/{n}/version/{v.version}"'
                f' hx-target="#scene-card-{n}" hx-swap="outerHTML"'
                f">Restore</button>"
            )
        rows += f"<tr{active_cls}><td>v{v.version}</td><td>{instr}</td><td>{ts}</td><td>{restore_btn}</td></tr>"
    return (
        f'<div class="version-history-section">'
        f'<button type="button" class="modify-toggle"'
        f" onclick=\"let t=document.getElementById('version-history-{n}');"
        f"t.style.display=t.style.display==='none'?'block':'none';\">"
        f"&#128336; Version history</button>"
        f'<div id="version-history-{n}" style="display:none;">'
        f'<table class="version-history-table">'
        f"<thead><tr><th>Ver</th><th>Instruction</th><th>Time</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        f"</table>"
        f"</div>"
        f"</div>"
    )


def scene_card(scene: Scene, storyboard_id=None, prev_has_video: bool = False) -> str:
    """Render a single scene card as HTML string."""
    n = scene.scene_number

    chars_html = ""
    if scene.characters:
        char_items = "".join(
            f'<li><strong>{c.name}</strong> <span class="role">({c.role})</span> &mdash; {c.appearance}</li>'
            for c in scene.characters
        )
        chars_html = f'<div class="scene-section"><h4>Characters</h4><ul>{char_items}</ul></div>'

    dialogue_html = ""
    if scene.dialogue:
        lines = "".join(f"<li>{d}</li>" for d in scene.dialogue)
        dialogue_html = f'<div class="scene-section"><h4>Dialogue</h4><ul class="dialogue">{lines}</ul></div>'

    visual_html = ""
    if scene.visual:
        v = scene.visual
        palette_dots = "".join(
            f'<span class="color-dot" style="background:{c}" title="{c}"></span>' for c in v.color_palette
        )
        prompt_html = prompt_section(v.video_gen_prompt, scene.scene_number, storyboard_id)
        chips_html = _keyword_chips(scene.scene_number) if storyboard_id is not None else ""
        visual_html = f"""
        <div class="scene-section visual-details">
            <h4>Visual Details</h4>
            <div class="visual-grid">
                <div><span class="meta-label">Camera:</span> {v.camera_angle}</div>
                <div><span class="meta-label">Lighting:</span> {v.lighting}</div>
                <div><span class="meta-label">Art style:</span> {v.art_style}</div>
                <div><span class="meta-label">Motion:</span> {v.motion}</div>
                <div><span class="meta-label">Palette:</span> {palette_dots}</div>
            </div>
        </div>
        {chips_html}
        {prompt_html}
        """

    modify_html = ""
    if storyboard_id is not None:
        modify_html = modify_scene_section(scene.scene_number, storyboard_id)

    video_html = _video_section(scene, storyboard_id, prev_has_video=prev_has_video)
    version_html = _version_indicator(scene, storyboard_id)
    version_history_html = _version_history(scene, storyboard_id)

    segment_html = ""
    if storyboard_id is not None and segmentation_available():
        version = scene.active_version or None
        has_video = video_exists(storyboard_id, scene.scene_number, version)
        if not has_video and version is not None:
            # Fall back to unversioned video path
            has_video = video_exists(storyboard_id, scene.scene_number, None)
        if has_video:
            segment_html = (
                f'<div class="scene-section segment-section">'
                f'<button class="btn btn-secondary btn-sm"'
                f' hx-post="/storyboard/{storyboard_id}/scene/{scene.scene_number}/segment"'
                f' hx-target="#segment-area-{scene.scene_number}"'
                f' hx-swap="innerHTML">'
                f'&#9986; Extract Characters</button>'
                f'<div id="segment-area-{scene.scene_number}"></div>'
                f'</div>'
            )

    source_text_html = ""
    if scene.source_text:
        source_text_html = (
            f'<div class="scene-section scene-source-text">'
            f'<h4>Story Text</h4>'
            f'<blockquote>{scene.source_text}</blockquote>'
            f'</div>'
        )

    return f"""
    <div class="scene-card" id="scene-card-{n}">
        <div class="scene-header">
            <span class="scene-number">Scene {scene.scene_number}</span>
            {version_html}
            <h3>{scene.title}</h3>
            <span class="duration">{scene.duration_seconds}s</span>
        </div>
        {source_text_html}
        <div class="scene-section">
            <h4>Setting</h4>
            <p>{scene.setting}</p>
        </div>
        <div class="scene-section">
            <h4>Action</h4>
            <p>{scene.action}</p>
        </div>
        <div class="scene-section">
            <h4>Mood</h4>
            <p>{scene.mood}</p>
        </div>
        {chars_html}
        {dialogue_html}
        {visual_html}
        {modify_html}
        {version_history_html}
        {video_html}
        {segment_html}
    </div>
    """


def _duration_select(scene_number: int) -> str:
    """Render a duration dropdown for video generation."""
    return (
        f'<select id="video-duration-{scene_number}" class="video-duration-select">'
        f'<option value="5">5s</option>'
        f'<option value="6">6s</option>'
        f'<option value="10">10s</option>'
        f"</select>"
    )


def _backend_select(scene_number: int) -> str:
    """Render a backend dropdown for video generation."""
    options = ""
    if hailuo_available():
        options += '<option value="hailuo">Hailuo</option>'
    if fal_available():
        options += '<option value="fal">Fal</option>'
    if venice_available():
        options += '<option value="venice">Venice</option>'
    if modal_available():
        options += '<option value="modal">Modal</option>'
    if meshwell_available():
        options += '<option value="meshwell">Meshwell 3D</option>'
    if WAN_AVAILABLE:
        options += '<option value="local">Local</option>'
    return (
        f'<select id="video-backend-{scene_number}" class="video-duration-select">'
        f"{options}"
        f"</select>"
    )


def _video_gen_controls(storyboard_id, n, regenerate=False, prev_has_video=False) -> str:
    """Build a compact video generation control row.

    Layout: [duration] [backend] [Generate] [Continue from prev]
    Uses JS to read the selected backend from the dropdown and build the POST URL.
    """
    backends = []
    if hailuo_available():
        backends.append("hailuo")
    if fal_available():
        backends.append("fal")
    if venice_available():
        backends.append("venice")
    if modal_available():
        backends.append("modal")
    if meshwell_available():
        backends.append("meshwell")
    if WAN_AVAILABLE:
        backends.append("local")
    if not backends:
        return ""

    label = "&#8635; Regen" if regenerate else "&#9654; Generate"
    btn_cls = "btn btn-secondary btn-sm btn-generate-video" if regenerate else "btn btn-primary btn-sm btn-generate-video"

    duration_html = _duration_select(n)
    backend_html = _backend_select(n)

    # JS onclick builds the hx-post URL dynamically from the backend select
    gen_js = (
        f"let b=document.getElementById('video-backend-{n}').value,"
        f"d=document.getElementById('video-duration-{n}').value;"
        f"this.setAttribute('hx-post','/storyboard/{storyboard_id}/scene/{n}/generate-video?backend='+b);"
        f"this.setAttribute('hx-vals','{{\"duration\":\"'+d+'\"}}');"
        f"htmx.process(this);htmx.trigger(this,'click');"
    )
    # Simpler approach: use a hidden form-like pattern with JS to set the URL before HTMX fires
    gen_button = (
        f'<button class="{btn_cls}"'
        f' hx-post="/storyboard/{storyboard_id}/scene/{n}/generate-video?backend={backends[0]}"'
        f' hx-target="#video-section-{n}"'
        f' hx-swap="outerHTML"'
        f""" hx-vals='js:{{duration: document.getElementById("video-duration-{n}").value}}'"""
        f' onclick="'
        f"let b=document.getElementById(&quot;video-backend-{n}&quot;).value;"
        f"this.setAttribute(&quot;hx-post&quot;,&quot;/storyboard/{storyboard_id}/scene/{n}/generate-video?backend=&quot;+b);"
        f"htmx.process(this);"
        f'">'
        f"{label}</button>"
    )

    # Continue-from-prev button (only if prev scene has video and a chaining backend is available)
    cont_button = ""
    chaining_backends = [b for b in backends if b in ("hailuo", "venice")]
    if prev_has_video and chaining_backends:
        cont_button = (
            f'<button class="btn btn-secondary btn-sm btn-generate-video"'
            f' hx-post="/storyboard/{storyboard_id}/scene/{n}/generate-video?backend={chaining_backends[0]}&continue_from_prev=1"'
            f' hx-target="#video-section-{n}"'
            f' hx-swap="outerHTML"'
            f""" hx-vals='js:{{duration: document.getElementById("video-duration-{n}").value}}'"""
            f' onclick="'
            f"let b=document.getElementById(&quot;video-backend-{n}&quot;).value;"
            f"this.setAttribute(&quot;hx-post&quot;,&quot;/storyboard/{storyboard_id}/scene/{n}/generate-video?backend=&quot;+b+&quot;&amp;continue_from_prev=1&quot;);"
            f"htmx.process(this);"
            f'"'
            f' title="Uses the last frame of the previous scene as first frame">'
            f"&#9654; Continue</button>"
        )

    return (
        f'<div class="video-gen-controls">'
        f"{duration_html} {backend_html} {gen_button} {cont_button}"
        f"</div>"
    )


def _reference_image_html(scene: Scene, storyboard_id) -> str:
    """Render upload form or thumbnail for a scene's reference image."""
    n = scene.scene_number

    if scene.visual and scene.visual.reference_image:
        # Show thumbnail + delete button
        return (
            f'<div class="reference-image-area">'
            f'<div class="reference-image-preview">'
            f'<img src="/image/{storyboard_id}/{n}" alt="Reference image">'
            f'<span class="reference-image-label">Reference image</span>'
            f'<button class="btn-delete-small" '
            f'hx-delete="/storyboard/{storyboard_id}/scene/{n}/delete-image" '
            f'hx-target="#video-section-{n}" hx-swap="outerHTML" '
            f'title="Remove reference image">&#10005;</button>'
            f'</div>'
            f'</div>'
        )
    else:
        # Show compact upload form
        return (
            f'<div class="reference-image-area">'
            f'<form class="reference-image-upload" '
            f'hx-post="/storyboard/{storyboard_id}/scene/{n}/upload-image" '
            f'hx-target="#video-section-{n}" hx-swap="outerHTML" '
            f'hx-encoding="multipart/form-data">'
            f'<input type="file" name="file" accept="image/*">'
            f'<button type="submit" class="btn btn-secondary btn-sm">Upload ref</button>'
            f'</form>'
            f'</div>'
        )


def _video_section(scene: Scene, storyboard_id, prev_has_video: bool = False) -> str:
    """Render the video player or generate button for a scene card."""
    if storyboard_id is None or not scene.visual:
        return ""

    n = scene.scene_number
    ver = scene.active_version if scene.active_version else None

    # Check versioned path first, then fall back to unversioned (backward compat)
    has_versioned = ver and video_exists(storyboard_id, n, ver)
    has_unversioned = video_exists(storyboard_id, n)

    if has_versioned or has_unversioned:
        if has_versioned:
            vpath = video_path_for(storyboard_id, n, ver)
            mtime = int(vpath.stat().st_mtime) if vpath.is_file() else 0
            video_url = f"/video/{storyboard_id}/{n}?version={ver}&t={mtime}"
            delete_url = f"/storyboard/{storyboard_id}/scene/{n}/delete-video?version={ver}"
            label = f"Generated Video (v{ver})"
        else:
            vpath = video_path_for(storyboard_id, n)
            mtime = int(vpath.stat().st_mtime) if vpath.is_file() else 0
            video_url = f"/video/{storyboard_id}/{n}?t={mtime}"
            delete_url = f"/storyboard/{storyboard_id}/scene/{n}/delete-video"
            label = "Generated Video"

        regen_html = _video_gen_controls(storyboard_id, n, regenerate=True, prev_has_video=prev_has_video)
        ref_html = _reference_image_html(scene, storyboard_id)

        return f"""
        <div class="video-section" id="video-section-{n}">
            {ref_html}
            <div class="video-section-header">
                <h4>{label}</h4>
                <button class="btn-delete-small" hx-delete="{delete_url}"
                    hx-target="#video-section-{n}" hx-swap="outerHTML"
                    hx-confirm="Delete this video?">&#10005;</button>
            </div>
            <div class="video-player-wrapper video-player-clickable">
                <video controls width="100%" src="{video_url}"></video>
                <button type="button" class="video-capture-btn" onclick="captureFrame(this, {storyboard_id}, {n})" title="Use current frame as reference image">&#x2299;</button>
                <button type="button" class="video-expand-btn" onclick="openVideoModal(this.closest('.video-player-wrapper').querySelector('video').src)" title="Expand">&#x26F6;</button>
            </div>
            {regen_html}
        </div>
        """

    controls = _video_gen_controls(storyboard_id, n, regenerate=False, prev_has_video=prev_has_video)
    if not controls:
        return ""

    ref_html = _reference_image_html(scene, storyboard_id)
    return f"""
        <div class="video-section" id="video-section-{n}">
            {ref_html}
            {controls}
        </div>
        """


def rewrite_section() -> str:
    """Collapsible 'Rewrite story' section below the textarea on the landing page."""
    return (
        '<div class="rewrite-section">'
        '<button type="button" class="modify-toggle"'
        " onclick=\"let f=document.getElementById('rewrite-form');"
        "f.style.display=f.style.display==='none'?'block':'none';\">"
        "&#9998; Rewrite story with prompt</button>"
        '<div id="rewrite-form" style="display:none;">'
        '<form hx-post="/rewrite" hx-target="#rewrite-output" hx-swap="innerHTML"'
        ' hx-indicator="#rewrite-btn" hx-include="#story-input">'
        '<input type="text" name="instruction" class="rewrite-input"'
        ' placeholder="e.g. Set this in space instead of medieval Europe...">'
        '<button type="submit" id="rewrite-btn" class="btn btn-secondary btn-sm">'
        "<span>Rewrite</span>"
        '<span class="spinner" aria-hidden="true"></span>'
        "</button>"
        "</form>"
        '<div id="rewrite-output"></div>'
        "</div>"
        "</div>"
    )


def story_comparison(original: str, rewritten: str) -> str:
    """Side-by-side comparison of original and rewritten story text."""
    import html as html_mod

    escaped_original = html_mod.escape(original)
    escaped_rewritten = html_mod.escape(rewritten)
    return (
        '<div class="story-compare">'
        '<div class="story-compare-col">'
        "<h4>Original</h4>"
        f'<div class="story-compare-text">{escaped_original}</div>'
        "</div>"
        '<div class="story-compare-col">'
        "<h4>Rewritten</h4>"
        f'<div class="story-compare-text">{escaped_rewritten}</div>'
        "</div>"
        "</div>"
        '<button type="button" class="btn btn-primary" onclick="'
        "document.getElementById('story-input').value = "
        "document.querySelector('.story-compare-col:last-child .story-compare-text').textContent;"
        "document.getElementById('rewrite-output').innerHTML = "
        "'<p class=\\'video-done-notice\\'>&#10004; Rewritten version loaded into editor.</p>';"
        'if (typeof updateCharCount === &quot;function&quot;) updateCharCount();">'
        "Use rewritten version"
        "</button>"
    )

"""
Video segment tagger — split a video into segments, tag them, export a training dataset.

Usage:
    python training/tagger.py /path/to/video.mp4 [--segment-duration 4] [--port 5050]

    # Or split by scene detection instead of fixed intervals:
    python training/tagger.py /path/to/video.mp4 --scene-detect [--threshold 27]

Opens a local web UI where you can:
  - Preview each segment
  - Assign shot types and write prompts
  - Mark segments to skip
  - Export as a dataset ready for modal_train_lora.py
"""

import argparse
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from fasthtml.common import *

log = logging.getLogger(__name__)

# ── Shot type options ────────────────────────────────────────────────────────

SHOT_TYPES = [
    "close-up",
    "extreme-close-up",
    "medium",
    "wide",
    "extreme-wide",
    "low-angle",
    "high-angle",
    "overhead",
    "dutch",
    "tracking",
    "dolly",
    "pov",
    "first-person-pov",
    "over-the-shoulder",
    "two-shot",
    "insert",
    "other",
]

# ── Video splitting ──────────────────────────────────────────────────────────


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def split_fixed_intervals(video_path: str, segment_duration: float, output_dir: Path) -> list[dict]:
    """Split video into fixed-length segments."""
    duration = get_video_duration(video_path)
    segments = []
    idx = 0

    t = 0.0
    while t < duration:
        seg_dur = min(segment_duration, duration - t)
        if seg_dur < 0.5:  # skip tiny trailing segments
            break

        idx += 1
        out_file = output_dir / f"seg_{idx:04d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-t", str(seg_dur), "-c:v", "libx264", "-preset", "ultrafast",
             "-an", str(out_file)],
            capture_output=True, timeout=120,
        )

        segments.append({
            "id": idx,
            "file": out_file.name,
            "start": round(t, 2),
            "end": round(t + seg_dur, 2),
            "duration": round(seg_dur, 2),
            "shot_type": "",
            "prompt": "",
            "skip": False,
        })
        t += segment_duration

    return segments


def split_scene_detect(video_path: str, output_dir: Path, threshold: float = 27.0) -> list[dict]:
    """Split video by scene detection using ffmpeg's scene filter."""
    # First pass: detect scene change timestamps
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_frames", "-of", "json",
         "-f", "lavfi", f"movie={video_path},select=gt(scene\\,{threshold / 100})"],
        capture_output=True, text=True, timeout=300,
    )

    timestamps = [0.0]
    if result.returncode == 0 and result.stdout.strip():
        try:
            frames = json.loads(result.stdout).get("frames", [])
            for frame in frames:
                ts = float(frame.get("pts_time", frame.get("pkt_pts_time", 0)))
                if ts - timestamps[-1] > 0.5:  # minimum 0.5s between cuts
                    timestamps.append(ts)
        except (json.JSONDecodeError, ValueError):
            pass

    duration = get_video_duration(video_path)
    timestamps.append(duration)

    # Second pass: cut at detected boundaries
    segments = []
    for i in range(len(timestamps) - 1):
        start = timestamps[i]
        end = timestamps[i + 1]
        seg_dur = end - start

        if seg_dur < 0.5:
            continue

        idx = len(segments) + 1
        out_file = output_dir / f"seg_{idx:04d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", video_path,
             "-t", str(seg_dur), "-c:v", "libx264", "-preset", "ultrafast",
             "-an", str(out_file)],
            capture_output=True, timeout=120,
        )

        segments.append({
            "id": idx,
            "file": out_file.name,
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(seg_dur, 2),
            "shot_type": "",
            "prompt": "",
            "skip": False,
        })

    return segments


# ── UI Components ────────────────────────────────────────────────────────────


def segment_card(seg: dict, total: int) -> FT:
    """Render a single segment card."""
    skip_cls = " opacity-40" if seg["skip"] else ""
    return Div(
        # Video preview
        Div(
            Video(
                Source(src=f"/video/{seg['file']}", type="video/mp4"),
                controls=True, preload="metadata", loop=True, muted=True,
                style="width:100%; border-radius:8px;",
            ),
            P(f"Segment {seg['id']}/{total}  ·  {seg['start']}s – {seg['end']}s  ({seg['duration']}s)",
              style="margin:4px 0; font-size:0.85em; color:#888;"),
            style="margin-bottom:8px;",
        ),
        # Tag form
        Form(
            # Shot type selector
            Div(
                Label("Shot type", _for=f"shot_{seg['id']}",
                      style="font-weight:600; font-size:0.9em;"),
                Select(
                    Option("— select —", value="", selected=not seg["shot_type"]),
                    *[Option(st, value=st, selected=(st == seg["shot_type"])) for st in SHOT_TYPES],
                    name="shot_type", id=f"shot_{seg['id']}",
                    style="width:100%; padding:6px; border-radius:6px; border:1px solid #555; background:#1e1e1e; color:#eee;",
                ),
                style="margin-bottom:8px;",
            ),
            # Prompt
            Div(
                Label("Prompt", _for=f"prompt_{seg['id']}",
                      style="font-weight:600; font-size:0.9em;"),
                Textarea(
                    seg["prompt"],
                    name="prompt", id=f"prompt_{seg['id']}",
                    rows=2, placeholder="Describe the visual content...",
                    style="width:100%; padding:6px; border-radius:6px; border:1px solid #555; background:#1e1e1e; color:#eee; resize:vertical; box-sizing:border-box;",
                ),
                style="margin-bottom:8px;",
            ),
            # Skip checkbox + save
            Div(
                Label(
                    Input(type="checkbox", name="skip", checked=seg["skip"],
                          style="margin-right:6px;"),
                    "Skip this segment",
                    style="font-size:0.9em; color:#aaa; cursor:pointer;",
                ),
                Button("Save", type="submit",
                       style="padding:6px 16px; border-radius:6px; background:#4a9eff; color:#fff; border:none; cursor:pointer; font-size:0.9em;"),
                style="display:flex; justify-content:space-between; align-items:center;",
            ),
            hx_post=f"/tag/{seg['id']}", hx_target=f"#seg-{seg['id']}", hx_swap="outerHTML",
        ),
        id=f"seg-{seg['id']}",
        style=f"background:#2a2a2a; border-radius:12px; padding:16px; margin-bottom:16px;{skip_cls}",
    )


def stats_bar(segments: list[dict]) -> FT:
    """Summary stats."""
    total = len(segments)
    tagged = sum(1 for s in segments if s["shot_type"])
    skipped = sum(1 for s in segments if s["skip"])
    exportable = sum(1 for s in segments if s["shot_type"] and not s["skip"])

    return Div(
        Span(f"{total} segments", style="margin-right:16px;"),
        Span(f"{tagged} tagged", style="margin-right:16px; color:#4a9eff;"),
        Span(f"{skipped} skipped", style="margin-right:16px; color:#888;"),
        Span(f"{exportable} exportable", style="color:#4caf50; font-weight:600;"),
        id="stats",
        style="padding:12px 16px; background:#1e1e1e; border-radius:8px; margin-bottom:16px; font-size:0.9em;",
    )


def keyboard_nav_script() -> FT:
    """JS for keyboard shortcuts: j/k to scroll between segments, s to skip toggle."""
    return Script("""
    let currentSeg = 0;
    const segs = () => document.querySelectorAll('[id^="seg-"]');

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
        const s = segs();
        if (e.key === 'j' || e.key === 'ArrowDown') {
            e.preventDefault();
            currentSeg = Math.min(currentSeg + 1, s.length - 1);
            s[currentSeg]?.scrollIntoView({behavior: 'smooth', block: 'center'});
        } else if (e.key === 'k' || e.key === 'ArrowUp') {
            e.preventDefault();
            currentSeg = Math.max(currentSeg - 1, 0);
            s[currentSeg]?.scrollIntoView({behavior: 'smooth', block: 'center'});
        }
    });
    """)


# ── App ──────────────────────────────────────────────────────────────────────


def create_app(segments: list[dict], segments_dir: Path, source_video: str, output_dir: Path):
    app, rt = fast_app(
        live=False,
        hdrs=(
            Meta(name="color-scheme", content="dark"),
            Style("""
                body { background: #181818; color: #eee; font-family: -apple-system, system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 16px; }
                h1 { font-size: 1.4em; }
                a { color: #4a9eff; }
                video { background: #000; }
            """),
            keyboard_nav_script(),
        ),
    )

    @rt("/")
    def get():
        return Title("Video Tagger"), Div(
            H1("Video Tagger"),
            P(f"Source: ", Code(source_video), style="font-size:0.85em; color:#888;"),
            stats_bar(segments),
            # Bulk actions
            Div(
                A("Export Dataset", href="/export", target="_blank",
                  style="padding:8px 20px; background:#4caf50; color:#fff; border-radius:6px; text-decoration:none; font-weight:600;"),
                Span(" "),
                A("Export & Resize (480p)", href="/export?resize=1", target="_blank",
                  style="padding:8px 20px; background:#ff9800; color:#fff; border-radius:6px; text-decoration:none; font-weight:600;"),
                P("Keyboard: j/k to navigate, click into fields to edit",
                  style="font-size:0.8em; color:#666; margin-top:8px;"),
                style="margin-bottom:16px;",
            ),
            *[segment_card(seg, len(segments)) for seg in segments],
        )

    @rt("/video/{filename}")
    def get(filename: str):
        from starlette.responses import FileResponse
        path = segments_dir / filename
        if not path.exists():
            return Response("Not found", status_code=404)
        return FileResponse(str(path), media_type="video/mp4")

    @rt("/tag/{seg_id}")
    def post(seg_id: int, shot_type: str = "", prompt: str = "", skip: bool = False):
        for seg in segments:
            if seg["id"] == seg_id:
                seg["shot_type"] = shot_type
                seg["prompt"] = prompt
                seg["skip"] = skip
                # Auto-save state
                _save_state(segments, segments_dir)
                return segment_card(seg, len(segments)), stats_bar(segments), Script("htmx.ajax('GET', '/stats', {target:'#stats', swap:'outerHTML'})")
        return Response("Not found", status_code=404)

    @rt("/stats")
    def get():
        return stats_bar(segments)

    @rt("/export")
    def get(resize: int = 0):
        from starlette.responses import JSONResponse

        exportable = [s for s in segments if s["shot_type"] and not s["skip"]]
        if not exportable:
            return Response("No tagged segments to export. Tag some segments first.", status_code=400)

        dataset_dir = output_dir / "dataset"
        videos_out = dataset_dir / "videos"
        videos_out.mkdir(parents=True, exist_ok=True)

        entries = []
        for i, seg in enumerate(exportable, 1):
            src = segments_dir / seg["file"]
            dst_name = f"clip_{i:04d}.mp4"
            dst = videos_out / dst_name

            if resize:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src),
                     "-vf", "scale=832:480:force_original_aspect_ratio=decrease,pad=832:480:(ow-iw)/2:(oh-ih)/2",
                     "-c:v", "libx264", "-preset", "fast", "-an", str(dst)],
                    capture_output=True, timeout=120,
                )
            else:
                import shutil
                shutil.copy2(src, dst)

            shot_desc = seg["shot_type"].replace("-", " ") + " shot"
            prompt = f"{shot_desc}, {seg['prompt']}" if seg["prompt"] else shot_desc

            entries.append({
                "file": f"videos/{dst_name}",
                "prompt": prompt,
                "shot_type": seg["shot_type"],
                "start": seg["start"],
                "end": seg["end"],
            })

        metadata_path = dataset_dir / "metadata.jsonl"
        with open(metadata_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        return JSONResponse({
            "status": "ok",
            "exported": len(entries),
            "output_dir": str(dataset_dir),
            "metadata": str(metadata_path),
            "next_steps": [
                f"modal volume create s2v-training-data",
                f"modal volume put s2v-training-data {dataset_dir} /dataset",
                f"modal run training/modal_train_lora.py",
            ],
        })

    return app


def _save_state(segments: list[dict], segments_dir: Path):
    """Persist tagging state so it survives restarts."""
    state_path = segments_dir / "tagger_state.json"
    with open(state_path, "w") as f:
        json.dump(segments, f, indent=2)


def _load_state(segments_dir: Path) -> list[dict] | None:
    """Load previous tagging state if it exists."""
    state_path = segments_dir / "tagger_state.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return None


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Split and tag video segments for LoRA training")
    parser.add_argument("video", type=str, help="Path to source video file")
    parser.add_argument("--segment-duration", type=float, default=4.0,
                        help="Segment duration in seconds (fixed interval mode)")
    parser.add_argument("--scene-detect", action="store_true",
                        help="Use scene detection instead of fixed intervals")
    parser.add_argument("--threshold", type=float, default=27.0,
                        help="Scene detection threshold (0-100, lower = more sensitive)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory (default: ./tagger_output/<video_name>)")
    parser.add_argument("--port", type=int, default=5050,
                        help="Web UI port")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from previous session (skip re-splitting)")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: video not found: {video_path}")
        return

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path("tagger_output") / video_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing state
    existing_state = _load_state(segments_dir)
    if args.resume and existing_state:
        print(f"Resuming previous session with {len(existing_state)} segments")
        segments = existing_state
    else:
        # Split video
        if args.scene_detect:
            print(f"Splitting by scene detection (threshold={args.threshold})...")
            segments = split_scene_detect(str(video_path), segments_dir, args.threshold)
        else:
            print(f"Splitting into {args.segment_duration}s segments...")
            segments = split_fixed_intervals(str(video_path), args.segment_duration, segments_dir)

        print(f"Created {len(segments)} segments in {segments_dir}")
        _save_state(segments, segments_dir)

    # Start web UI
    app = create_app(segments, segments_dir, str(video_path), output_dir)
    print(f"\nOpen http://localhost:{args.port} to start tagging")
    print(f"State auto-saves to {segments_dir / 'tagger_state.json'}")
    serve(appname=app, port=args.port)


if __name__ == "__main__":
    main()

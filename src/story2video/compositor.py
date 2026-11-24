"""Scene compositor — layer segmented characters onto backgrounds.

Takes RGBA character frame sequences from SAM2 segmentation and composites
them onto a background (solid color, static image, or video frames) to
produce a new video.
"""

import logging
import queue
import subprocess
import threading
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
COMPOSITES_DIR = DATA_DIR / "composites"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def composite_dir_for(storyboard_id: int) -> Path:
    """Return the directory for composited outputs."""
    return COMPOSITES_DIR / str(storyboard_id)


def composite_video_path(storyboard_id: int, composite_id: str) -> Path:
    """Return the output path for a composited video."""
    return composite_dir_for(storyboard_id) / f"{composite_id}.mp4"


# ---------------------------------------------------------------------------
# Background sources
# ---------------------------------------------------------------------------

def _load_background_color(color: str, width: int, height: int) -> Image.Image:
    """Create a solid-color background image."""
    return Image.new("RGBA", (width, height), color)


def _extract_video_frames(video_path: Path, output_dir: Path) -> list[Path]:
    """Extract all frames from a video as JPEG files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-q:v", "2", str(output_dir / "frame_%06d.jpg")],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        log.error("Frame extraction failed: %s", result.stderr.decode()[:300])
        return []
    return sorted(output_dir.glob("frame_*.jpg"))


# ---------------------------------------------------------------------------
# Layer definition
# ---------------------------------------------------------------------------

class CharacterLayer:
    """A character to composite onto the scene.

    Attributes:
        frame_paths: Ordered list of RGBA PNG paths (one per video frame).
        x: Horizontal offset (pixels from left edge of canvas).
        y: Vertical offset (pixels from top edge of canvas).
        scale: Scale factor (1.0 = original size).
        flip_h: Whether to flip horizontally.
    """

    def __init__(
        self,
        frame_paths: list[Path],
        x: int = 0,
        y: int = 0,
        scale: float = 1.0,
        flip_h: bool = False,
    ):
        self.frame_paths = frame_paths
        self.x = x
        self.y = y
        self.scale = scale
        self.flip_h = flip_h

    def get_frame(self, idx: int) -> Image.Image | None:
        """Load and transform a frame by index. Returns None if out of range."""
        if idx >= len(self.frame_paths):
            # Hold last frame if character sequence is shorter than background
            if not self.frame_paths:
                return None
            idx = len(self.frame_paths) - 1

        img = Image.open(self.frame_paths[idx]).convert("RGBA")

        if self.scale != 1.0:
            new_w = max(1, int(img.width * self.scale))
            new_h = max(1, int(img.height * self.scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        if self.flip_h:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)

        return img


# ---------------------------------------------------------------------------
# Core compositing
# ---------------------------------------------------------------------------

def composite_frames(
    layers: list[CharacterLayer],
    num_frames: int,
    canvas_width: int,
    canvas_height: int,
    background_color: str = "#000000",
    background_image: Path | None = None,
    background_video_frames: list[Path] | None = None,
    output_dir: Path | None = None,
    q: queue.Queue | None = None,
) -> list[Path]:
    """Composite character layers onto a background for each frame.

    Priority for background (first match wins):
        1. background_video_frames — per-frame backgrounds from a video
        2. background_image — a single static image stretched to canvas size
        3. background_color — solid color fill

    Args:
        layers: Character layers to composite (drawn in order, first = bottom).
        num_frames: Total number of output frames.
        canvas_width: Output width in pixels.
        canvas_height: Output height in pixels.
        background_color: Hex color for solid background.
        background_image: Path to a static background image.
        background_video_frames: List of per-frame background image paths.
        output_dir: Directory to write output frames. Auto-created if needed.
        q: Optional progress queue.

    Returns:
        List of output frame paths (PNG).
    """
    if output_dir is None:
        output_dir = COMPOSITES_DIR / "_tmp_composite"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Preload static background if provided
    static_bg = None
    if background_image and background_image.is_file():
        static_bg = Image.open(background_image).convert("RGBA")
        static_bg = static_bg.resize((canvas_width, canvas_height), Image.LANCZOS)

    output_paths: list[Path] = []

    for fidx in range(num_frames):
        # Build background
        if background_video_frames and fidx < len(background_video_frames):
            bg = Image.open(background_video_frames[fidx]).convert("RGBA")
            bg = bg.resize((canvas_width, canvas_height), Image.LANCZOS)
        elif static_bg is not None:
            bg = static_bg.copy()
        else:
            bg = _load_background_color(background_color, canvas_width, canvas_height)

        # Composite each character layer
        for layer in layers:
            char_frame = layer.get_frame(fidx)
            if char_frame is None:
                continue
            # Paste with alpha compositing at (x, y)
            bg.paste(char_frame, (layer.x, layer.y), char_frame)

        # Save composited frame
        out_path = output_dir / f"comp_{fidx:06d}.png"
        bg.convert("RGB").save(out_path)
        output_paths.append(out_path)

        if q and fidx % 10 == 0:
            q.put(("progress", f"Composited frame {fidx + 1}/{num_frames}"))

    return output_paths


def frames_to_video(
    frame_paths: list[Path],
    output_path: Path,
    fps: int = 24,
) -> bool:
    """Encode a sequence of frames into an MP4 video using ffmpeg.

    Returns True on success.
    """
    if not frame_paths:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_dir = frame_paths[0].parent
    pattern = frame_paths[0].name.replace("000000", "%06d")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(frame_dir / pattern),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            str(output_path),
        ],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        log.error("ffmpeg encode failed: %s", result.stderr.decode()[:300])
        return False
    return True


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def compose_scene_to_queue(
    storyboard_id: int,
    composite_id: str,
    layers: list[CharacterLayer],
    num_frames: int,
    canvas_width: int,
    canvas_height: int,
    fps: int = 24,
    background_color: str = "#000000",
    background_image: Path | None = None,
    background_video: Path | None = None,
    q: queue.Queue | None = None,
    cancel_event: threading.Event | None = None,
):
    """Composite and encode a scene in a background thread.

    Writes progress tuples to `q` as (type, message).
    """
    try:
        if cancel_event and cancel_event.is_set():
            if q:
                q.put(("cancelled", "Compositing cancelled"))
            return

        # Extract background video frames if provided
        bg_frames = None
        if background_video and background_video.is_file():
            if q:
                q.put(("progress", "Extracting background video frames..."))
            bg_dir = composite_dir_for(storyboard_id) / f"_bg_{composite_id}"
            bg_frame_paths = _extract_video_frames(background_video, bg_dir)
            if bg_frame_paths:
                bg_frames = bg_frame_paths
                # Match frame count to background video length
                num_frames = len(bg_frame_paths)

        if q:
            q.put(("progress", f"Compositing {len(layers)} character(s) onto {num_frames} frames..."))

        # Composite frames
        comp_dir = composite_dir_for(storyboard_id) / f"_frames_{composite_id}"
        frame_paths = composite_frames(
            layers=layers,
            num_frames=num_frames,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            background_color=background_color,
            background_image=background_image,
            background_video_frames=bg_frames,
            output_dir=comp_dir,
            q=q,
        )

        if not frame_paths:
            if q:
                q.put(("error", "No frames composited"))
            return

        # Encode to video
        if q:
            q.put(("progress", "Encoding video..."))
        out_path = composite_video_path(storyboard_id, composite_id)
        ok = frames_to_video(frame_paths, out_path, fps=fps)

        if not ok:
            if q:
                q.put(("error", "Video encoding failed"))
            return

        # Clean up intermediate frame directories
        import shutil
        shutil.rmtree(comp_dir, ignore_errors=True)
        if bg_frames:
            bg_dir = composite_dir_for(storyboard_id) / f"_bg_{composite_id}"
            shutil.rmtree(bg_dir, ignore_errors=True)

        if q:
            q.put(("complete", f"Composite video saved: {out_path}"))

    except Exception as e:
        log.exception("Compositing failed")
        if q:
            q.put(("error", f"Compositing failed: {e}"))


def list_available_characters(storyboard_id: int) -> list[dict]:
    """List all segmented characters available for compositing.

    Returns list of dicts with:
        scene_number, object_id, num_frames, preview_gif (path or None)
    """
    from .segmentation import SEGMENTS_DIR

    sb_dir = SEGMENTS_DIR / str(storyboard_id)
    if not sb_dir.is_dir():
        return []

    characters = []
    for scene_dir in sorted(sb_dir.iterdir()):
        if not scene_dir.is_dir() or not scene_dir.name.startswith("scene_"):
            continue
        scene_num = int(scene_dir.name.split("_")[1])

        for char_dir in sorted(scene_dir.iterdir()):
            if not char_dir.is_dir() or not char_dir.name.startswith("char_"):
                continue
            obj_id = int(char_dir.name.split("_")[1])
            frames = sorted(char_dir.glob("frame_*.png"))

            gif_path = scene_dir / f"char_{obj_id}_preview.gif"
            characters.append({
                "scene_number": scene_num,
                "object_id": obj_id,
                "num_frames": len(frames),
                "frame_paths": frames,
                "preview_gif": gif_path if gif_path.is_file() else None,
            })

    return characters

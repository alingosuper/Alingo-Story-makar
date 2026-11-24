"""SAM2-based character segmentation from generated scene videos.

Extracts character masks from video frames using Meta's Segment Anything
Model 2 (SAM2). Supports both automatic (grounded) and interactive (point/box
prompt) segmentation, with per-frame mask propagation across the clip.

Output: transparent-background PNGs (RGBA) per character per frame, plus an
optional composited sprite-sheet or GIF for quick preview.
"""

import logging
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag — safe to import without sam2/torch installed
# ---------------------------------------------------------------------------
try:
    import numpy as np
    import torch
    from PIL import Image

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

try:
    from sam2.build_sam import build_sam2_video_predictor  # type: ignore[import-untyped]

    SAM2_AVAILABLE = True
except ImportError:
    SAM2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# HuggingFace repo ID and checkpoint filename for SAM2
SAM2_HF_REPO = os.environ.get("SAM2_HF_REPO", "facebook/sam2.1-hiera-large")
SAM2_HF_FILENAME = os.environ.get("SAM2_HF_FILENAME", "sam2.1_hiera_large.pt")
SAM2_MODEL_CFG = os.environ.get(
    "SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_l.yaml"
)
DEVICE = os.environ.get("SAM2_DEVICE", "cuda" if _TORCH_AVAILABLE and torch.cuda.is_available() else "cpu")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
SEGMENTS_DIR = DATA_DIR / "segments"

# Lazy singleton predictor
_predictor = None
_predictor_lock = threading.Lock()


def segmentation_available() -> bool:
    """Check if SAM2 segmentation is available."""
    return SAM2_AVAILABLE and _TORCH_AVAILABLE


# ---------------------------------------------------------------------------
# Predictor management
# ---------------------------------------------------------------------------

def _resolve_checkpoint() -> str:
    """Download the SAM2 checkpoint from HuggingFace Hub if needed, return local path."""
    from huggingface_hub import hf_hub_download

    log.info("Resolving SAM2 checkpoint: %s / %s", SAM2_HF_REPO, SAM2_HF_FILENAME)
    local_path = hf_hub_download(repo_id=SAM2_HF_REPO, filename=SAM2_HF_FILENAME)
    log.info("SAM2 checkpoint at: %s", local_path)
    return local_path


def _patch_bfloat16_offload(predictor):
    """Monkey-patch SAM2 to skip bfloat16 cast on memory features.

    SAM2's video predictor hardcodes `maskmem_features.to(torch.bfloat16)` before
    CPU offloading (lines 781/833 in sam2_video_predictor.py). When all model
    weights are float32, these bfloat16 features cause dtype mismatches in the
    memory attention cross-attention layer during `propagate_in_video`.

    We patch `_run_memory_encoder` to keep features in float32.
    """
    original_run_memory_encoder = predictor._run_memory_encoder

    def _patched_run_memory_encoder(*args, **kwargs):
        maskmem_features, maskmem_pos_enc = original_run_memory_encoder(*args, **kwargs)
        if maskmem_features is not None and maskmem_features.dtype != torch.float32:
            maskmem_features = maskmem_features.float()
        return maskmem_features, maskmem_pos_enc

    predictor._run_memory_encoder = _patched_run_memory_encoder
    log.info("Patched SAM2 _run_memory_encoder to keep float32 (skip bfloat16 offload cast)")


def _get_predictor():
    """Lazy-load the SAM2 video predictor singleton."""
    global _predictor
    if _predictor is not None:
        return _predictor
    with _predictor_lock:
        if _predictor is not None:
            return _predictor
        if not segmentation_available():
            raise RuntimeError("SAM2 is not installed. Install with: pip install sam2")
        ckpt_path = _resolve_checkpoint()
        log.info("Loading SAM2 predictor with config %s", SAM2_MODEL_CFG)
        # Load on CPU first, cast everything to float32, then move to device.
        # SAM2 large checkpoint uses bfloat16 which causes dtype mismatches
        # with float32 frame data during propagation.
        _predictor = build_sam2_video_predictor(
            SAM2_MODEL_CFG,
            ckpt_path,
            device="cpu",
        )
        _predictor = _predictor.float()  # cast all parameters AND buffers to float32
        _patch_bfloat16_offload(_predictor)  # prevent bfloat16 memory feature casts
        _predictor = _predictor.to(DEVICE)
        log.info("SAM2 predictor loaded on %s (float32)", DEVICE)
        return _predictor


# ---------------------------------------------------------------------------
# Frame extraction (video → JPEG directory for SAM2)
# ---------------------------------------------------------------------------

def _extract_frames(video_path: Path, output_dir: Path, fps: int = 0) -> int:
    """Extract frames from a video file using ffmpeg.

    Filenames are plain zero-padded numbers (e.g. ``000001.jpg``) because
    SAM2's ``load_video_frames`` sorts by ``int(stem)`` and rejects prefixes.

    Args:
        video_path: Path to the .mp4 file.
        output_dir: Directory to write NNNNNN.jpg files into.
        fps: Frames per second to extract. 0 = extract all frames.

    Returns:
        Number of frames extracted.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-i", str(video_path)]
    if fps > 0:
        cmd += ["-vf", f"fps={fps}"]
    # SAM2 expects filenames like 000001.jpg (pure numeric stems)
    cmd += ["-q:v", "2", str(output_dir / "%06d.jpg")]

    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        log.error("ffmpeg frame extraction failed: %s", result.stderr.decode()[:500])
        return 0

    frames = sorted(output_dir.glob("*.jpg"))
    return len(frames)


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

def segment_dir_for(storyboard_id: int, scene_number: int) -> Path:
    """Return the directory for segmentation outputs."""
    return SEGMENTS_DIR / str(storyboard_id) / f"scene_{scene_number}"


def segment_frame_path(
    storyboard_id: int, scene_number: int, object_id: int, frame_idx: int
) -> Path:
    """Return path for a single segmented frame (RGBA PNG)."""
    d = segment_dir_for(storyboard_id, scene_number) / f"char_{object_id}"
    return d / f"frame_{frame_idx:06d}.png"


# ---------------------------------------------------------------------------
# Core segmentation
# ---------------------------------------------------------------------------

def segment_characters(
    video_path: Path,
    storyboard_id: int,
    scene_number: int,
    prompts: list[dict],
    q: queue.Queue | None = None,
) -> dict[int, list[Path]]:
    """Segment characters from a video using SAM2.

    Args:
        video_path: Path to the scene .mp4 file.
        storyboard_id: For output directory naming.
        scene_number: For output directory naming.
        prompts: List of prompt dicts, each with:
            - "object_id": int — unique ID for this character
            - "frame_idx": int — which frame to prompt on (0-based)
            - "points": list of [x, y] — positive click points (optional)
            - "labels": list of 0/1 — 1=foreground, 0=background (matches points)
            - "box": [x1, y1, x2, y2] — bounding box prompt (optional)
        q: Optional queue for progress messages (type, message).

    Returns:
        Dict mapping object_id → list of output PNG paths (one per frame).
    """
    if not segmentation_available():
        if q:
            q.put(("error", "SAM2 is not available"))
        return {}

    def emit(msg_type: str, msg: str):
        log.info("[segment] %s: %s", msg_type, msg)
        if q:
            q.put((msg_type, msg))

    # Step 1: Extract frames to a temp directory
    emit("progress", "Extracting video frames...")
    frames_dir = segment_dir_for(storyboard_id, scene_number) / "_frames"
    num_frames = _extract_frames(video_path, frames_dir)
    if num_frames == 0:
        emit("error", "No frames extracted from video")
        return {}
    emit("progress", f"Extracted {num_frames} frames")

    # Step 2: Initialize SAM2 predictor with video frames
    emit("progress", "Initializing SAM2 predictor...")
    predictor = _get_predictor()
    state = predictor.init_state(video_path=str(frames_dir))

    # Step 3: Add prompts for each character
    for prompt in prompts:
        obj_id = prompt["object_id"]
        frame_idx = prompt.get("frame_idx", 0)

        points = prompt.get("points")
        labels = prompt.get("labels")
        box = prompt.get("box")

        kwargs = {"frame_idx": frame_idx, "obj_id": obj_id}
        if points is not None:
            kwargs["points"] = np.array(points, dtype=np.float32)
            kwargs["labels"] = np.array(labels or [1] * len(points), dtype=np.int32)
        if box is not None:
            kwargs["box"] = np.array(box, dtype=np.float32)

        predictor.add_new_points_or_box(state, **kwargs)
        emit("progress", f"Added prompt for character {obj_id} on frame {frame_idx}")

    # Step 4: Propagate masks across all frames
    emit("progress", "Propagating masks across video frames...")
    # Collect masks: {object_id: {frame_idx: mask_array}}
    masks_by_object: dict[int, dict[int, np.ndarray]] = {}

    for frame_idx, object_ids, masks in predictor.propagate_in_video(state):
        for oid_tensor, mask_tensor in zip(object_ids, masks):
            oid = int(oid_tensor)
            # mask shape: (1, H, W) — squeeze to (H, W), threshold at 0.5
            mask = (mask_tensor[0] > 0.5).cpu().numpy()
            if oid not in masks_by_object:
                masks_by_object[oid] = {}
            masks_by_object[oid][frame_idx] = mask

    emit("progress", f"Propagated masks for {len(masks_by_object)} character(s) across {num_frames} frames")

    # Step 5: Apply masks to original frames → RGBA PNGs
    emit("progress", "Extracting character cutouts...")
    frame_files = sorted(frames_dir.glob("*.jpg"))
    results: dict[int, list[Path]] = {}

    for oid, frame_masks in masks_by_object.items():
        char_dir = segment_dir_for(storyboard_id, scene_number) / f"char_{oid}"
        char_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []

        for fidx in sorted(frame_masks.keys()):
            if fidx >= len(frame_files):
                continue
            mask = frame_masks[fidx]
            frame_img = Image.open(frame_files[fidx]).convert("RGB")
            frame_np = np.array(frame_img)

            # Create RGBA image with transparent background
            rgba = np.zeros((*frame_np.shape[:2], 4), dtype=np.uint8)
            rgba[..., :3] = frame_np
            rgba[..., 3] = (mask * 255).astype(np.uint8)

            out_path = char_dir / f"frame_{fidx:06d}.png"
            Image.fromarray(rgba, "RGBA").save(out_path, optimize=True)
            paths.append(out_path)

        results[oid] = paths
        emit("progress", f"Character {oid}: saved {len(paths)} frames")

    # Clean up source frames (keep only the segmented outputs)
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)

    emit("done", f"Segmentation complete: {len(results)} character(s)")
    return results


def create_character_gif(
    frame_paths: list[Path],
    output_path: Path,
    fps: int = 12,
) -> Path | None:
    """Create an animated GIF preview from segmented character frames.

    Args:
        frame_paths: List of RGBA PNG paths (in order).
        output_path: Where to write the .gif file.
        fps: Frames per second for the GIF.

    Returns:
        The output path on success, None on failure.
    """
    if not frame_paths:
        return None
    try:
        frames = [Image.open(p) for p in frame_paths]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=1000 // fps,
            loop=0,
            disposal=2,  # clear frame before drawing next (for transparency)
        )
        return output_path
    except Exception as e:
        log.error("Failed to create GIF: %s", e)
        return None


# ---------------------------------------------------------------------------
# High-level API for the web app
# ---------------------------------------------------------------------------

def segment_scene_to_queue(
    storyboard_id: int,
    scene_number: int,
    video_path: Path,
    prompts: list[dict],
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
):
    """Run character segmentation in a background thread (for SSE streaming).

    Writes progress tuples to `q` as (type, message).
    On completion, writes ("done", summary) or ("error", message).
    """
    try:
        if cancel_event and cancel_event.is_set():
            q.put(("cancelled", "Segmentation cancelled"))
            return

        results = segment_characters(
            video_path=video_path,
            storyboard_id=storyboard_id,
            scene_number=scene_number,
            prompts=prompts,
            q=q,
        )

        # Generate preview GIFs for each character
        for oid, paths in results.items():
            if paths:
                gif_path = segment_dir_for(storyboard_id, scene_number) / f"char_{oid}_preview.gif"
                gif = create_character_gif(paths, gif_path)
                if gif:
                    q.put(("preview", f"char_{oid}:{gif}"))

        q.put(("complete", f"Segmented {len(results)} character(s) from scene {scene_number}"))

    except Exception as e:
        log.exception("Segmentation failed for scene %d", scene_number)
        q.put(("error", f"Segmentation failed: {e}"))

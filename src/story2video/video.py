"""Wan 2.1 video generation — lazy model loading + on-demand generation."""

import logging
import queue
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Feature flag — safe to import even without torch/diffusers installed
try:
    import torch
    from diffusers import WanPipeline
    from diffusers.utils import export_to_video

    WAN_AVAILABLE = True
except ImportError:
    WAN_AVAILABLE = False

# Lazy singleton pipeline
_pipeline = None
_pipeline_lock = threading.Lock()

# Concurrency guard — only one generation at a time on one GPU
_generation_lock = threading.Lock()

import os

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "videos"

MODEL_ID = os.environ.get("WAN_MODEL", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers")

NEGATIVE_PROMPT = (
    "Bright tones, overexposed, static, blurred details, subtitles, style, "
    "works, paintings, images, static, overall gray, worst quality, "
    "low quality, JPEG artifacts, ugly, duplicate, morbid, mutilated, "
    "extra fingers, mutated hands, poorly drawn hands, poorly drawn face, "
    "mutation, deformed, blurry, dehydrated, bad anatomy, bad proportions, "
    "extra limbs, cloned face, disfigured, gross proportions, malformed limbs, "
    "missing arms, missing legs, extra arms, extra legs, fused fingers, "
    "too many fingers, long neck, "
    "nsfw, nude, nudity, gore, blood, graphic violence, suggestive, provocative"
)


def video_path_for(storyboard_id: int, scene_number: int, version: int | None = None) -> Path:
    """Return the filesystem path for a scene's generated video."""
    if version is not None:
        return DATA_DIR / str(storyboard_id) / f"scene_{scene_number}_v{version}.mp4"
    return DATA_DIR / str(storyboard_id) / f"scene_{scene_number}.mp4"


def video_exists(storyboard_id: int, scene_number: int, version: int | None = None) -> bool:
    """Check if a video has already been generated for this scene."""
    return video_path_for(storyboard_id, scene_number, version).is_file()


def storyboard_video_dir(storyboard_id: int) -> Path:
    """Return the directory containing all videos for a storyboard."""
    return DATA_DIR / str(storyboard_id)


def delete_scene_video(storyboard_id: int, scene_number: int, version: int | None = None) -> bool:
    """Delete a scene's video file. Returns True if a file was deleted."""
    path = video_path_for(storyboard_id, scene_number, version)
    if path.is_file():
        path.unlink()
        return True
    return False


def delete_all_videos(storyboard_id: int) -> int:
    """Delete all videos for a storyboard. Returns the number of files deleted."""
    import shutil

    video_dir = storyboard_video_dir(storyboard_id)
    if not video_dir.is_dir():
        return 0
    count = sum(1 for f in video_dir.iterdir() if f.is_file())
    shutil.rmtree(video_dir)
    return count


def combined_video_path(storyboard_id: int) -> Path:
    """Return the filesystem path for a storyboard's combined video."""
    return DATA_DIR / str(storyboard_id) / "combined.mp4"


def combine_videos_to_queue(
    storyboard_id: int,
    scene_videos: list[Path],
    q: queue.Queue,
) -> None:
    """Concatenate scene videos into one combined video using ffmpeg.

    Pushes (phase, message) tuples:
      "combining" — progress
      "done"      — complete (message is the output path)
      "error"     — something went wrong

    Pushes None sentinel when finished.
    """
    import subprocess
    import tempfile

    if not scene_videos:
        q.put(("error", "No scene videos to combine."))
        q.put(None)
        return

    try:
        q.put(("combining", f"Combining {len(scene_videos)} videos..."))

        out_path = combined_video_path(storyboard_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write concat file list
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=str(out_path.parent)) as f:
            for vp in scene_videos:
                # ffmpeg concat demuxer needs escaped single quotes in paths
                escaped = str(vp).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
            concat_path = f.name

        q.put(("combining", "Running ffmpeg concat..."))
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_path,
                "-c",
                "copy",
                str(out_path),
            ],
            capture_output=True,
            timeout=300,
        )

        # Clean up temp file
        Path(concat_path).unlink(missing_ok=True)

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[-500:]
            q.put(("error", f"ffmpeg failed: {stderr}"))
            q.put(None)
            return

        q.put(("done", str(out_path)))

    except Exception as e:
        log.exception("Video combine failed")
        q.put(("error", f"Combine failed: {e}"))
    finally:
        q.put(None)


def get_pipeline():
    """Load the Wan 2.1 pipeline on first call (lazy singleton)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        log.info("Loading Wan 2.1 pipeline %s ...", MODEL_ID)
        from diffusers import AutoencoderKLWan

        # VAE must be float32 per official recommendation; pipeline uses bfloat16
        vae = AutoencoderKLWan.from_pretrained(MODEL_ID, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(
            MODEL_ID,
            vae=vae,
            torch_dtype=torch.bfloat16,
        )
        # Fix tied weights bug in transformers>=5.0 — embed_tokens must
        # share the same tensor as shared, but newer versions leave it
        # zero-initialized, producing all-zero encoder output.
        te = pipe.text_encoder
        if te.shared.weight.data_ptr() != te.encoder.embed_tokens.weight.data_ptr():
            te.encoder.embed_tokens.weight = te.shared.weight
            log.info("Fixed text encoder tied weights (embed_tokens ← shared).")

        if "1.3B" in MODEL_ID or "1.3b" in MODEL_ID:
            # 1.3B fits comfortably in 24GB VRAM — load directly to GPU
            pipe.to("cuda")
        else:
            # Larger models need CPU offloading
            pipe.enable_sequential_cpu_offload()
        pipe.vae.enable_tiling()
        # Set flow_shift for better quality (3.0 for 480p, 5.0 for 720p)
        pipe.scheduler.config.flow_shift = 3.0
        _pipeline = pipe
        log.info("Wan 2.1 pipeline loaded (%s).", MODEL_ID)
        return _pipeline


def unload_pipeline() -> bool:
    """Unload the Wan pipeline from GPU memory. Returns True if a pipeline was unloaded."""
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            return False
        del _pipeline
        _pipeline = None
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    log.info("Wan 2.1 pipeline unloaded, GPU memory freed.")
    return True


def pipeline_loaded() -> bool:
    """Return True if the pipeline is currently loaded in memory."""
    return _pipeline is not None


def generate_video_to_queue(
    prompt: str,
    storyboard_id: int,
    scene_number: int,
    q: queue.Queue,
    version: int | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate a video and push progress tuples to the queue.

    Pushes (phase, message) tuples:
      "loading"    — model loading
      "generating" — step X/50
      "saving"     — encoding MP4
      "done"       — complete (message is the video path)
      "error"      — something went wrong

    Pushes None sentinel when finished.
    """
    if not WAN_AVAILABLE:
        q.put(("error", "Video generation not available — torch/diffusers not installed."))
        q.put(None)
        return

    acquired = _generation_lock.acquire(blocking=False)
    if not acquired:
        q.put(("error", "Another video is currently generating. Please wait and try again."))
        q.put(None)
        return

    try:
        # Load model
        q.put(("loading", f"Loading {MODEL_ID}..."))
        pipe = get_pipeline()
        q.put(("loading", "Model loaded."))

        num_steps = 50

        # Callback for step progress
        def on_step_end(pipe_self, step, timestep, callback_kwargs):
            q.put(("generating", f"Generating — step {step + 1}/{num_steps}"))
            return callback_kwargs

        # Convert duration to frames (16 fps, must be 4k+1 for Wan)
        fps = 16
        num_frames = max(17, round(duration_seconds * fps / 4) * 4 + 1)
        q.put(("generating", f"Generating {num_frames} frames ({duration_seconds}s) — step 0/{num_steps}"))

        output = pipe(
            prompt=prompt,
            negative_prompt=NEGATIVE_PROMPT,
            height=480,
            width=832,
            num_frames=num_frames,
            guidance_scale=6.0,
            num_inference_steps=num_steps,
            callback_on_step_end=on_step_end,
        )

        # Save video
        q.put(("saving", "Saving video..."))
        out_path = video_path_for(storyboard_id, scene_number, version)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        export_to_video(output.frames[0], str(out_path), fps=16)

        q.put(("done", str(out_path)))

    except Exception as e:
        log.exception("Video generation failed")
        q.put(("error", f"Video generation failed: {e}"))
    finally:
        _generation_lock.release()
        q.put(None)

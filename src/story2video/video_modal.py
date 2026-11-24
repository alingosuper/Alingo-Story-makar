"""Modal cloud video generation — Wan 2.1 (1.3B or 14B) with optional LoRA adapters.

Runs inference on Modal GPUs. The model stays warm between calls via
Modal's container keep-alive, so sequential scene generation is fast
after the first cold start.

Deploys two endpoints:
  - WanInference14B on A100-80GB (high quality)
  - WanInference1_3B on A10G (cheap, same quality as local)

Setup:
    1. pip install modal
    2. modal token new
    3. Deploy: modal deploy src/alingo_video_story/video_modal.py

Environment variables (set locally, read by client functions):
    MODAL_WAN_MODEL — "14b" (default) or "1.3b" to select which endpoint to call
    MODAL_LORA_PATH — path on the Modal volume to a LoRA adapter (optional)
    MODAL_LORA_ALPHA — LoRA scaling factor 0.0-1.0 (default: 1.0)
"""

import logging
import os
import queue
import threading
from pathlib import Path

import modal

try:
    from .video import video_path_for, video_exists
except ImportError:
    # When Modal loads this file directly (modal deploy), relative imports fail.
    # These functions are only needed client-side, not in the Modal container.
    from pathlib import Path as _Path

    _DATA_DIR = _Path(__file__).parent.parent.parent / "data" / "videos"

    def video_path_for(storyboard_id: int, scene_number: int, version: int | None = None) -> _Path:
        if version is not None:
            return _DATA_DIR / str(storyboard_id) / f"scene_{scene_number}_v{version}.mp4"
        return _DATA_DIR / str(storyboard_id) / f"scene_{scene_number}.mp4"

    def video_exists(storyboard_id: int, scene_number: int, version: int | None = None) -> bool:
        return video_path_for(storyboard_id, scene_number, version).is_file()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modal infrastructure
# ---------------------------------------------------------------------------

VOLUME_NAME = "s2v-training-data"
MODEL_CACHE_NAME = "s2v-model-cache"

app = modal.App("s2v-inference")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
model_cache = modal.Volume.from_name(MODEL_CACHE_NAME, create_if_missing=True)

inference_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.4.0",
        "diffusers>=0.32.0",
        "transformers>=4.40.0",
        "accelerate>=0.25.0",
        "peft>=0.14.0",
        "sentencepiece",
        "ftfy",
        "imageio",
        "imageio-ffmpeg",
    )
    .apt_install("ffmpeg")
)

VOLUME_PATH = "/data"
MODEL_CACHE_PATH = "/model-cache"

NEGATIVE_PROMPT = (
    "Bright tones, overexposed, static, blurred details, subtitles, style, "
    "works, paintings, images, static, overall gray, worst quality, "
    "low quality, JPEG artifacts, ugly, duplicate, morbid, mutilated, "
    "extra fingers, mutated hands, poorly drawn hands, poorly drawn face, "
    "mutation, deformed, blurry, dehydrated, bad anatomy, bad proportions, "
    "extra limbs, cloned face, disfigured, gross proportions, malformed limbs, "
    "missing arms, missing legs, extra arms, extra legs, fused fingers, "
    "too many fingers, long neck"
)


# ---------------------------------------------------------------------------
# Shared model loading logic
# ---------------------------------------------------------------------------

def _load_wan_pipeline(model_id: str, lora_path: str | None = None):
    """Load a Wan 2.1 pipeline with optional LoRA. Returns the pipeline."""
    import gc
    import torch
    from diffusers import AutoencoderKLWan, WanPipeline

    os.environ["HF_HOME"] = MODEL_CACHE_PATH

    is_14b = "14B" in model_id or "14b" in model_id

    print(f"Loading {model_id}...")
    vae = AutoencoderKLWan.from_pretrained(
        model_id, subfolder="vae", torch_dtype=torch.float32
    )
    pipe = WanPipeline.from_pretrained(
        model_id, vae=vae, torch_dtype=torch.bfloat16
    )

    # Fix tied weights bug (same as local video.py)
    te = pipe.text_encoder
    if te.shared.weight.data_ptr() != te.encoder.embed_tokens.weight.data_ptr():
        te.encoder.embed_tokens.weight = te.shared.weight
        print("Fixed text encoder tied weights")

    if is_14b:
        pipe.enable_sequential_cpu_offload()
    else:
        pipe.to("cuda")

    pipe.vae.enable_tiling()
    pipe.scheduler.config.flow_shift = 3.0

    # Apply LoRA if configured
    if lora_path:
        from peft import PeftModel
        adapter_path = os.path.join(VOLUME_PATH, lora_path)
        if os.path.exists(adapter_path):
            print(f"Loading LoRA adapter from {adapter_path}")
            pipe.transformer = PeftModel.from_pretrained(
                pipe.transformer, adapter_path
            )
            lora_alpha = float(os.environ.get("MODAL_LORA_ALPHA", "1.0"))
            print(f"LoRA loaded (alpha={lora_alpha})")
        else:
            print(f"WARNING: LoRA path not found: {adapter_path}")

    model_cache.commit()
    print("Model ready")
    return pipe


def _generate_video(pipe, prompt: str, height: int, width: int,
                    num_frames: int, num_steps: int, guidance_scale: float,
                    fps: int) -> bytes:
    """Run generation and return MP4 bytes."""
    import tempfile
    from diffusers.utils import export_to_video

    output = pipe(
        prompt=prompt,
        negative_prompt=NEGATIVE_PROMPT,
        height=height,
        width=width,
        num_frames=num_frames,
        guidance_scale=guidance_scale,
        num_inference_steps=num_steps,
    )

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as f:
        export_to_video(output.frames[0], f.name, fps=fps)
        return Path(f.name).read_bytes()


# ---------------------------------------------------------------------------
# 14B endpoint — A100-80GB
# ---------------------------------------------------------------------------

@app.cls(
    image=inference_image,
    gpu="A100-80GB",
    volumes={VOLUME_PATH: volume, MODEL_CACHE_PATH: model_cache},
    timeout=600,
    scaledown_window=300,
)
class WanInference14B:
    @modal.enter()
    def load_model(self):
        self.pipe = _load_wan_pipeline(
            "Wan-AI/Wan2.1-T2V-14B-Diffusers",
            lora_path=os.environ.get("MODAL_LORA_PATH"),
        )

    @modal.method()
    def generate(self, prompt: str, height: int = 480, width: int = 832,
                 num_frames: int = 81, num_steps: int = 50,
                 guidance_scale: float = 6.0, fps: int = 16) -> bytes:
        return _generate_video(
            self.pipe, prompt, height, width, num_frames, num_steps,
            guidance_scale, fps,
        )


# ---------------------------------------------------------------------------
# 1.3B endpoint — A10G (cheaper, faster cold start)
# ---------------------------------------------------------------------------

@app.cls(
    image=inference_image,
    gpu="A10G",
    volumes={VOLUME_PATH: volume, MODEL_CACHE_PATH: model_cache},
    timeout=600,
    scaledown_window=300,
)
class WanInference1_3B:
    @modal.enter()
    def load_model(self):
        self.pipe = _load_wan_pipeline(
            "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
            lora_path=os.environ.get("MODAL_LORA_PATH"),
        )

    @modal.method()
    def generate(self, prompt: str, height: int = 480, width: int = 832,
                 num_frames: int = 81, num_steps: int = 50,
                 guidance_scale: float = 6.0, fps: int = 16) -> bytes:
        return _generate_video(
            self.pipe, prompt, height, width, num_frames, num_steps,
            guidance_scale, fps,
        )


# ---------------------------------------------------------------------------
# Local client functions — match the interface of other video backends
# ---------------------------------------------------------------------------

def _get_model_size() -> str:
    """Get configured model size from env var."""
    return os.environ.get("MODAL_WAN_MODEL", "14b").lower()


def _get_inference_client():
    """Get a handle to the appropriate Modal inference class."""
    size = _get_model_size()
    if size in ("1.3b", "1.3"):
        cls = modal.Cls.from_name("s2v-inference", "WanInference1_3B")
    else:
        cls = modal.Cls.from_name("s2v-inference", "WanInference14B")
    return cls()


def modal_available() -> bool:
    """Check if Modal is configured."""
    try:
        import modal
        return True
    except ImportError:
        return False


MODAL_AVAILABLE = modal_available()


def generate_video_to_queue(
    prompt: str,
    storyboard_id: int,
    scene_number: int,
    q: queue.Queue,
    first_frame_image: str | None = None,
    version: int | None = None,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate a video via Modal and push progress to the queue.

    Same interface as video_hailuo.generate_video_to_queue.
    """
    try:
        model_size = _get_model_size()
        gpu = "A10G" if model_size in ("1.3b", "1.3") else "A100"
        q.put(("loading", f"Connecting to Modal ({model_size} on {gpu})..."))

        inference = _get_inference_client()

        fps = 16
        num_frames = max(17, round(duration_seconds * fps / 4) * 4 + 1)

        q.put(("generating", f"Generating {num_frames} frames ({duration_seconds}s) — Wan {model_size} on Modal {gpu}..."))

        video_bytes = inference.generate.remote(
            prompt=prompt,
            num_frames=num_frames,
            fps=fps,
        )

        if cancel_event and cancel_event.is_set():
            q.put(("error", "Generation cancelled."))
            q.put(None)
            return

        q.put(("saving", "Saving video..."))
        out_path = video_path_for(storyboard_id, scene_number, version)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(video_bytes)

        q.put(("done", str(out_path)))

    except Exception as e:
        log.exception("Modal video generation failed")
        q.put(("error", f"Modal generation failed: {e}"))
    finally:
        q.put(None)


def generate_all_to_queue(
    scenes: list[tuple[int, str, int | None]],
    storyboard_id: int,
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate videos for all scenes sequentially via Modal.

    Args:
        scenes: List of (scene_number, prompt, version) tuples.
        storyboard_id: The storyboard ID.
        q: Queue to push progress tuples.
        cancel_event: Optional event to cancel generation.
        duration_seconds: Duration per video.
    """
    try:
        total = len(scenes)
        model_size = _get_model_size()
        q.put(("generating", f"Generating {total} videos via Modal (Wan {model_size})..."))

        inference = _get_inference_client()
        fps = 16
        num_frames = max(17, round(duration_seconds * fps / 4) * 4 + 1)

        for i, (scene_number, prompt, version) in enumerate(scenes):
            if cancel_event and cancel_event.is_set():
                q.put(("error", f"Cancelled after {i}/{total} videos."))
                q.put(None)
                return

            if video_exists(storyboard_id, scene_number, version):
                q.put(("generating", f"Scene {scene_number} already exists, skipping ({i+1}/{total})"))
                continue

            q.put(("generating", f"Generating scene {scene_number} ({i+1}/{total})..."))

            video_bytes = inference.generate.remote(
                prompt=prompt,
                num_frames=num_frames,
                fps=fps,
            )

            out_path = video_path_for(storyboard_id, scene_number, version)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(video_bytes)

            q.put(("generating", f"Scene {scene_number} done ({i+1}/{total})"))

        q.put(("done", f"All {total} videos generated."))

    except Exception as e:
        log.exception("Modal batch generation failed")
        q.put(("error", f"Modal generation failed: {e}"))
    finally:
        q.put(None)

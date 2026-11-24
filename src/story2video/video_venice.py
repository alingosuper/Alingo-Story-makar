"""Venice.ai cloud video generation — queue, poll, retrieve."""

import logging
import os
import queue
import threading
import time
from pathlib import Path

import requests

from .video import video_exists, video_path_for
from .video_hailuo import extract_last_frame_base64

log = logging.getLogger(__name__)

API_BASE = "https://api.venice.ai/api/v1"
VENICE_MODEL = os.environ.get("VENICE_MODEL", "wan-2.5-preview-text-to-video")
VENICE_I2V_MODEL = os.environ.get("VENICE_I2V_MODEL", "wan-2.5-preview-image-to-video")


def _get_api_key() -> str:
    return os.environ.get("VENICE_API_KEY", "")


def venice_available() -> bool:
    return bool(_get_api_key())


VENICE_AVAILABLE: bool = venice_available()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


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
    """Generate a video via Venice.ai and push progress tuples to the queue.

    Pushes (phase, message) tuples:
      "submitting"  — sending request to Venice
      "polling"     — waiting for completion
      "downloading" — retrieving the finished video
      "done"        — complete (message is the video path)
      "error"       — something went wrong

    Pushes None sentinel when finished.
    """
    if not venice_available():
        q.put(("error", "Venice not available — VENICE_API_KEY not set."))
        q.put(None)
        return

    try:
        # 1. Submit to queue — use I2V model when chaining from previous scene
        # Venice accepts "5s" or "10s"
        venice_duration = "10s" if duration_seconds >= 8 else "5s"
        if first_frame_image:
            model = VENICE_I2V_MODEL
            q.put(("submitting", f"Submitting to Venice ({model}, {venice_duration}, continue from prev)..."))
            body = {
                "model": model,
                "prompt": prompt,
                "image_url": first_frame_image,
                "duration": venice_duration,
                "resolution": "480p",
            }
        else:
            model = VENICE_MODEL
            q.put(("submitting", f"Submitting to Venice ({model}, {venice_duration})..."))
            body = {
                "model": model,
                "prompt": prompt,
                "duration": venice_duration,
                "resolution": "480p",
                "aspect_ratio": "16:9",
            }
        resp = requests.post(
            f"{API_BASE}/video/queue",
            headers=_headers(),
            json=body,
            timeout=30,
        )
        if resp.status_code != 200:
            try:
                err = resp.json()
                issues = err.get("issues", [])
                msg = "; ".join(i.get("message", "") for i in issues) or str(err)
            except Exception:
                msg = resp.text[:500]
            q.put(("error", f"Venice API error ({resp.status_code}): {msg}"))
            q.put(None)
            return
        data = resp.json()

        queue_id = data.get("queue_id")
        if not queue_id:
            q.put(("error", f"Venice returned no queue_id. Response: {data}"))
            q.put(None)
            return

        # 2. Poll until completion
        q.put(("polling", "Queued, waiting for processing..."))
        while True:
            time.sleep(5)
            if cancel_event and cancel_event.is_set():
                q.put(("cancelled", "Video generation stopped."))
                q.put(None)
                return

            poll_resp = requests.post(
                f"{API_BASE}/video/retrieve",
                headers=_headers(),
                json={
                    "model": VENICE_MODEL,
                    "queue_id": queue_id,
                    "delete_media_on_completion": True,
                },
                timeout=120,
            )

            if poll_resp.status_code == 404:
                q.put(("error", "Venice: media not found (expired or deleted)."))
                q.put(None)
                return
            if poll_resp.status_code == 422:
                q.put(("error", "Venice: content policy violation."))
                q.put(None)
                return
            if poll_resp.status_code == 503:
                q.put(("polling", "Venice: model at capacity, retrying..."))
                continue
            poll_resp.raise_for_status()

            content_type = poll_resp.headers.get("Content-Type", "")

            if "video/mp4" in content_type:
                # Video is ready — response body is the video file
                q.put(("downloading", "Saving video..."))
                out_path = video_path_for(storyboard_id, scene_number, version)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(poll_resp.content)
                q.put(("done", str(out_path)))
                return
            else:
                # Still processing — JSON response with status
                try:
                    status_data = poll_resp.json()
                    status = status_data.get("status", "PROCESSING")
                    elapsed = status_data.get("execution_duration", 0)
                    avg = status_data.get("average_execution_time", 0)
                    if avg:
                        q.put(("polling", f"Processing... ({elapsed // 1000}s / ~{avg // 1000}s est.)"))
                    else:
                        q.put(("polling", f"Processing... ({elapsed // 1000}s elapsed)"))
                except Exception:
                    q.put(("polling", "Processing..."))

    except Exception as e:
        log.exception("Venice video generation failed")
        q.put(("error", f"Venice generation failed: {e}"))
    finally:
        q.put(None)


def generate_all_to_queue(
    scenes: list[tuple[int, str, int | None]],
    storyboard_id: int,
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate videos for all scenes sequentially via Venice.ai.

    Args:
        scenes: List of (scene_number, prompt, version) tuples.
        storyboard_id: The storyboard DB id.
        q: Queue for progress updates.

    Pushes (phase, message) tuples. Phases include:
        "scene-start", "submitting", "polling", "downloading",
        "scene-done", "done", "error"

    Pushes None sentinel when all scenes are finished.
    """
    if not venice_available():
        q.put(("error", "Venice not available — VENICE_API_KEY not set."))
        q.put(None)
        return

    total = len(scenes)
    prev_video_path: Path | None = None
    failed_scenes: list[int] = []

    for idx, (scene_number, prompt, version) in enumerate(scenes, 1):
        if cancel_event and cancel_event.is_set():
            q.put(("cancelled", "Video generation stopped."))
            q.put(None)
            return

        if video_exists(storyboard_id, scene_number, version):
            q.put(("scene-done", f"Scene {scene_number} already has a video, skipping."))
            prev_video_path = video_path_for(storyboard_id, scene_number, version)
            continue

        q.put(("scene-start", f"Starting scene {scene_number} ({idx} of {total})..."))

        # Extract last frame from previous scene for chaining
        first_frame_image = None
        if prev_video_path is not None:
            first_frame_image = extract_last_frame_base64(prev_video_path)
            if first_frame_image:
                q.put(("submitting", "Using last frame from previous scene for continuity."))

        try:
            venice_duration = "10s" if duration_seconds >= 8 else "5s"
            if first_frame_image:
                model = VENICE_I2V_MODEL
                q.put(
                    (
                        "submitting",
                        f"Scene {scene_number}: Submitting to Venice ({model}, {venice_duration}, continue from prev)...",
                    )
                )
                body = {
                    "model": model,
                    "prompt": prompt,
                    "image_url": first_frame_image,
                    "duration": venice_duration,
                    "resolution": "480p",
                }
            else:
                model = VENICE_MODEL
                q.put(("submitting", f"Scene {scene_number}: Submitting to Venice ({model}, {venice_duration})..."))
                body = {
                    "model": model,
                    "prompt": prompt,
                    "duration": venice_duration,
                    "resolution": "480p",
                    "aspect_ratio": "16:9",
                }
            resp = requests.post(
                f"{API_BASE}/video/queue",
                headers=_headers(),
                json=body,
                timeout=30,
            )
            if resp.status_code != 200:
                try:
                    err = resp.json()
                    issues = err.get("issues", [])
                    msg = "; ".join(i.get("message", "") for i in issues) or str(err)
                except Exception:
                    msg = resp.text[:500]
                q.put(("error", f"Scene {scene_number}: Venice API error ({resp.status_code}): {msg}"))
                failed_scenes.append(scene_number)
                continue
            data = resp.json()

            queue_id = data.get("queue_id")
            if not queue_id:
                q.put(("error", f"Scene {scene_number}: Venice returned no queue_id."))
                failed_scenes.append(scene_number)
                continue

            # Poll
            q.put(("polling", f"Scene {scene_number}: Queued, waiting..."))
            video_saved = False
            while True:
                time.sleep(5)
                if cancel_event and cancel_event.is_set():
                    q.put(("cancelled", "Video generation stopped."))
                    q.put(None)
                    return

                poll_resp = requests.post(
                    f"{API_BASE}/video/retrieve",
                    headers=_headers(),
                    json={
                        "model": VENICE_MODEL,
                        "queue_id": queue_id,
                        "delete_media_on_completion": True,
                    },
                    timeout=120,
                )

                if poll_resp.status_code == 404:
                    q.put(("error", f"Scene {scene_number}: Media not found."))
                    break
                if poll_resp.status_code == 422:
                    q.put(("error", f"Scene {scene_number}: Content policy violation."))
                    break
                if poll_resp.status_code == 503:
                    q.put(("polling", f"Scene {scene_number}: Model at capacity, retrying..."))
                    continue
                poll_resp.raise_for_status()

                content_type = poll_resp.headers.get("Content-Type", "")

                if "video/mp4" in content_type:
                    q.put(("downloading", f"Scene {scene_number}: Saving video..."))
                    out_path = video_path_for(storyboard_id, scene_number, version)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(poll_resp.content)
                    video_saved = True
                    break
                else:
                    try:
                        status_data = poll_resp.json()
                        elapsed = status_data.get("execution_duration", 0)
                        avg = status_data.get("average_execution_time", 0)
                        if avg:
                            q.put(
                                (
                                    "polling",
                                    f"Scene {scene_number}: Processing... ({elapsed // 1000}s / ~{avg // 1000}s est.)",
                                )
                            )
                        else:
                            q.put(("polling", f"Scene {scene_number}: Processing... ({elapsed // 1000}s elapsed)"))
                    except Exception:
                        q.put(("polling", f"Scene {scene_number}: Processing..."))

            if video_saved:
                prev_video_path = out_path
                q.put(("scene-done", f"Scene {scene_number} complete."))
            else:
                failed_scenes.append(scene_number)

        except Exception as e:
            log.exception("Scene %d generation failed", scene_number)
            q.put(("error", f"Scene {scene_number} failed: {e}"))
            failed_scenes.append(scene_number)

    if failed_scenes:
        failed_str = ", ".join(str(s) for s in failed_scenes)
        q.put(("done-with-errors", f"Finished with {len(failed_scenes)} error(s) (scene(s) {failed_str})."))
    else:
        q.put(("done", "All scenes generated."))
    q.put(None)

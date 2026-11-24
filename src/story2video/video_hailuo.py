"""Hailuo (MiniMax) cloud video generation — submit, poll, download."""

import base64
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import requests

from .video import video_exists, video_path_for

log = logging.getLogger(__name__)

API_BASE = "https://api.minimax.io/v1"
MODEL = "MiniMax-Hailuo-2.3"


def _get_api_key() -> str:
    return os.environ.get("MINIMAX_API_KEY", "")


def hailuo_available() -> bool:
    return bool(_get_api_key())


# Module-level constant for simple checks — but prefer hailuo_available() for runtime checks
HAILUO_AVAILABLE: bool = hailuo_available()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


def extract_last_frame_base64(video_path: Path) -> str | None:
    """Extract the last frame of a video as a base64-encoded PNG data URI.

    Returns a string like 'data:image/png;base64,...' or None on failure.
    """
    if not video_path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-sseof",
                "-0.1",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-f",
                "image2",
                "-c:v",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning("ffmpeg last-frame extraction failed: %s", result.stderr.decode(errors="replace"))
            return None
        return "data:image/png;base64," + base64.b64encode(result.stdout).decode()
    except Exception:
        log.exception("Failed to extract last frame from %s", video_path)
        return None


def generate_video_to_queue(
    prompt: str,
    storyboard_id: int,
    scene_number: int,
    q: queue.Queue,
    first_frame_image: str | None = None,
    version: int | None = None,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 6,
) -> None:
    """Generate a video via Hailuo API and push progress tuples to the queue.

    Pushes (phase, message) tuples:
      "submitting"  — sending request to Hailuo
      "polling"     — waiting for completion (with status updates)
      "downloading" — fetching the finished video
      "done"        — complete (message is the video path)
      "error"       — something went wrong

    Pushes None sentinel when finished.
    """
    if not HAILUO_AVAILABLE:
        q.put(("error", "Hailuo not available — MINIMAX_API_KEY not set."))
        q.put(None)
        return

    try:
        # 1. Submit generation request
        q.put(("submitting", "Submitting to Hailuo..."))
        body = {
            "model": MODEL,
            "prompt": prompt,
            "resolution": "768P",
            "duration": duration_seconds,
            "prompt_optimizer": True,
        }
        if first_frame_image:
            body["first_frame_image"] = first_frame_image
        resp = requests.post(
            f"{API_BASE}/video_generation",
            headers=_headers(),
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Check for API-level error (e.g. sensitive content)
        base_resp = data.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code == 1026:
            q.put(("error", "Prompt was flagged as sensitive by Hailuo. Try rephrasing the video_gen_prompt."))
            q.put(None)
            return
        if status_code != 0:
            q.put(("error", f"Hailuo API error: {base_resp.get('status_msg', 'unknown error')}"))
            q.put(None)
            return

        task_id = data.get("task_id")
        if not task_id:
            q.put(("error", "Hailuo returned no task_id."))
            q.put(None)
            return

        # 2. Poll until completion
        q.put(("polling", "Polling... (Queueing)"))
        while True:
            time.sleep(5)
            if cancel_event and cancel_event.is_set():
                q.put(("cancelled", "Video generation stopped."))
                q.put(None)
                return
            poll_resp = requests.get(
                f"{API_BASE}/query/video_generation",
                headers=_headers(),
                params={"task_id": task_id},
                timeout=30,
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()

            poll_base = poll_data.get("base_resp", {})
            poll_status_code = poll_base.get("status_code", 0)
            if poll_status_code == 1026:
                q.put(("error", "Prompt was flagged as sensitive by Hailuo. Try rephrasing the video_gen_prompt."))
                q.put(None)
                return
            if poll_status_code != 0:
                q.put(("error", f"Hailuo poll error: {poll_base.get('status_msg', 'unknown error')}"))
                q.put(None)
                return

            status = poll_data.get("status", "Unknown")

            if status == "Success":
                file_id = poll_data.get("file_id")
                if not file_id:
                    q.put(("error", "Hailuo returned Success but no file_id."))
                    q.put(None)
                    return
                break
            elif status == "Fail":
                q.put(("error", f"Hailuo generation failed: {poll_data}"))
                q.put(None)
                return
            else:
                q.put(("polling", f"Polling... ({status})"))

        # 3. Retrieve download URL then download the video
        q.put(("downloading", "Retrieving download URL..."))
        meta_resp = requests.get(
            f"{API_BASE}/files/retrieve",
            headers=_headers(),
            params={"file_id": file_id},
            timeout=30,
        )
        meta_resp.raise_for_status()
        meta_data = meta_resp.json()

        meta_base = meta_data.get("base_resp", {})
        if meta_base.get("status_code", 0) != 0:
            q.put(("error", f"File retrieve failed: {meta_base.get('status_msg', 'unknown error')}"))
            q.put(None)
            return

        download_url = meta_data.get("file", {}).get("download_url")
        if not download_url:
            q.put(("error", "Hailuo returned no download_url for the video."))
            q.put(None)
            return

        q.put(("downloading", "Downloading video..."))
        dl_resp = requests.get(download_url, timeout=120)
        dl_resp.raise_for_status()

        out_path = video_path_for(storyboard_id, scene_number, version)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(dl_resp.content)

        q.put(("done", str(out_path)))

    except Exception as e:
        log.exception("Hailuo video generation failed")
        q.put(("error", f"Hailuo generation failed: {e}"))
    finally:
        q.put(None)


def generate_all_to_queue(
    scenes: list[tuple[int, str, int | None]],
    storyboard_id: int,
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 6,
) -> None:
    """Generate videos for all scenes sequentially with first-frame chaining.

    Args:
        scenes: List of (scene_number, prompt, version) tuples.
        storyboard_id: The storyboard DB id.
        q: Queue for progress updates.

    Pushes (phase, message) tuples. Phases include:
        "scene-start", "submitting", "polling", "downloading",
        "scene-done", "done", "error"

    Pushes None sentinel when all scenes are finished.
    """
    if not hailuo_available():
        q.put(("error", "Hailuo not available — MINIMAX_API_KEY not set."))
        q.put(None)
        return

    total = len(scenes)
    prev_video_path: Path | None = None
    failed_scenes: list[int] = []

    for idx, (scene_number, prompt, version) in enumerate(scenes, 1):
        # Skip scenes that already have a video
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
            # Submit
            q.put(("submitting", "Submitting to Hailuo..."))
            body = {
                "model": MODEL,
                "prompt": prompt,
                "resolution": "768P",
                "duration": duration_seconds,
                "prompt_optimizer": True,
            }
            if first_frame_image:
                body["first_frame_image"] = first_frame_image

            resp = requests.post(
                f"{API_BASE}/video_generation",
                headers=_headers(),
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            base_resp = data.get("base_resp", {})
            status_code = base_resp.get("status_code", 0)
            if status_code == 1026:
                q.put(("error", f"Scene {scene_number}: Prompt flagged as sensitive. Skipping."))
                failed_scenes.append(scene_number)
                continue
            if status_code != 0:
                q.put(("error", f"Scene {scene_number}: {base_resp.get('status_msg', 'unknown error')}"))
                failed_scenes.append(scene_number)
                continue

            task_id = data.get("task_id")
            if not task_id:
                q.put(("error", f"Scene {scene_number}: No task_id returned."))
                failed_scenes.append(scene_number)
                continue

            # Poll
            q.put(("polling", f"Scene {scene_number}: Polling... (Queueing)"))
            file_id = None
            while True:
                time.sleep(5)
                if cancel_event and cancel_event.is_set():
                    q.put(("cancelled", "Video generation stopped."))
                    q.put(None)
                    return
                poll_resp = requests.get(
                    f"{API_BASE}/query/video_generation",
                    headers=_headers(),
                    params={"task_id": task_id},
                    timeout=30,
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()

                poll_base = poll_data.get("base_resp", {})
                poll_status_code = poll_base.get("status_code", 0)
                if poll_status_code == 1026:
                    q.put(("error", f"Scene {scene_number}: Flagged as sensitive during generation."))
                    break
                if poll_status_code != 0:
                    q.put(("error", f"Scene {scene_number}: {poll_base.get('status_msg', 'unknown error')}"))
                    break

                status = poll_data.get("status", "Unknown")
                if status == "Success":
                    file_id = poll_data.get("file_id")
                    if not file_id:
                        q.put(("error", f"Scene {scene_number}: Success but no file_id."))
                    break
                elif status == "Fail":
                    q.put(("error", f"Scene {scene_number}: Generation failed."))
                    break
                else:
                    q.put(("polling", f"Scene {scene_number}: Polling... ({status})"))

            if not file_id:
                failed_scenes.append(scene_number)
                continue

            # Retrieve download URL then download
            q.put(("downloading", f"Scene {scene_number}: Retrieving download URL..."))
            meta_resp = requests.get(
                f"{API_BASE}/files/retrieve",
                headers=_headers(),
                params={"file_id": file_id},
                timeout=30,
            )
            meta_resp.raise_for_status()
            meta_data = meta_resp.json()

            meta_base = meta_data.get("base_resp", {})
            if meta_base.get("status_code", 0) != 0:
                q.put(
                    (
                        "error",
                        f"Scene {scene_number}: File retrieve failed — {meta_base.get('status_msg', 'unknown error')}",
                    )
                )
                failed_scenes.append(scene_number)
                continue

            download_url = meta_data.get("file", {}).get("download_url")
            if not download_url:
                q.put(("error", f"Scene {scene_number}: No download_url returned."))
                failed_scenes.append(scene_number)
                continue

            q.put(("downloading", f"Scene {scene_number}: Downloading video..."))
            dl_resp = requests.get(download_url, timeout=120)
            dl_resp.raise_for_status()

            out_path = video_path_for(storyboard_id, scene_number, version)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(dl_resp.content)

            prev_video_path = out_path
            q.put(("scene-done", f"Scene {scene_number} complete."))

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

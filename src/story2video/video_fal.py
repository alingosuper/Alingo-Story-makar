"""Fal.ai cloud video generation — submit, poll, download."""

import logging
import os
import queue
import threading

import fal_client
import requests

from .video import video_exists, video_path_for

log = logging.getLogger(__name__)

FAL_MODEL = os.environ.get("FAL_MODEL", "fal-ai/wan-t2v")
FAL_RESOLUTION = os.environ.get("FAL_RESOLUTION", "480p")


def fal_available() -> bool:
    return bool(os.environ.get("FAL_KEY", ""))


FAL_AVAILABLE: bool = fal_available()


def generate_video_to_queue(
    prompt: str,
    storyboard_id: int,
    scene_number: int,
    q: queue.Queue,
    version: int | None = None,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate a video via fal.ai and push progress tuples to the queue.

    Pushes (phase, message) tuples:
      "submitting"  — sending request to fal
      "polling"     — waiting for completion
      "downloading" — fetching the finished video
      "done"        — complete (message is the video path)
      "error"       — something went wrong

    Pushes None sentinel when finished.
    """
    if not fal_available():
        q.put(("error", "Fal not available — FAL_KEY not set."))
        q.put(None)
        return

    try:
        q.put(("submitting", f"Submitting to fal ({FAL_MODEL})..."))

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress):
                logs = update.logs or []
                for log_entry in logs:
                    msg = log_entry.get("message", "") if isinstance(log_entry, dict) else str(log_entry)
                    if msg:
                        q.put(("polling", f"Fal: {msg}"))
            elif isinstance(update, fal_client.Queued):
                pos = getattr(update, "position", None)
                if pos is not None:
                    q.put(("polling", f"Queued (position {pos})..."))
                else:
                    q.put(("polling", "Queued..."))

        num_frames = max(17, round(duration_seconds * 16 / 4) * 4 + 1)
        result = fal_client.subscribe(
            FAL_MODEL,
            arguments={
                "prompt": prompt,
                "num_frames": num_frames,
                "frames_per_second": 16,
                "resolution": FAL_RESOLUTION,
                "num_inference_steps": 30,
                "enable_safety_checker": False,
            },
            with_logs=True,
            on_queue_update=on_queue_update,
        )

        if cancel_event and cancel_event.is_set():
            q.put(("cancelled", "Video generation stopped."))
            q.put(None)
            return

        video_url = result.get("video", {}).get("url")
        if not video_url:
            q.put(("error", f"Fal returned no video URL. Response: {result}"))
            q.put(None)
            return

        q.put(("downloading", "Downloading video..."))
        dl_resp = requests.get(video_url, timeout=120)
        dl_resp.raise_for_status()

        out_path = video_path_for(storyboard_id, scene_number, version)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(dl_resp.content)

        q.put(("done", str(out_path)))

    except Exception as e:
        log.exception("Fal video generation failed")
        q.put(("error", f"Fal generation failed: {e}"))
    finally:
        q.put(None)


def generate_all_to_queue(
    scenes: list[tuple[int, str, int | None]],
    storyboard_id: int,
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
) -> None:
    """Generate videos for all scenes sequentially via fal.ai.

    Args:
        scenes: List of (scene_number, prompt, version) tuples.
        storyboard_id: The storyboard DB id.
        q: Queue for progress updates.

    Pushes (phase, message) tuples. Phases include:
        "scene-start", "submitting", "polling", "downloading",
        "scene-done", "done", "error"

    Pushes None sentinel when all scenes are finished.
    """
    if not fal_available():
        q.put(("error", "Fal not available — FAL_KEY not set."))
        q.put(None)
        return

    total = len(scenes)
    failed_scenes: list[int] = []

    for idx, (scene_number, prompt, version) in enumerate(scenes, 1):
        if cancel_event and cancel_event.is_set():
            q.put(("cancelled", "Video generation stopped."))
            q.put(None)
            return

        if video_exists(storyboard_id, scene_number, version):
            q.put(("scene-done", f"Scene {scene_number} already has a video, skipping."))
            continue

        q.put(("scene-start", f"Starting scene {scene_number} ({idx} of {total})..."))

        try:
            q.put(("submitting", f"Scene {scene_number}: Submitting to fal ({FAL_MODEL})..."))

            def on_queue_update(update, sn=scene_number):
                if isinstance(update, fal_client.InProgress):
                    logs = update.logs or []
                    for log_entry in logs:
                        msg = log_entry.get("message", "") if isinstance(log_entry, dict) else str(log_entry)
                        if msg:
                            q.put(("polling", f"Scene {sn}: {msg}"))
                elif isinstance(update, fal_client.Queued):
                    pos = getattr(update, "position", None)
                    if pos is not None:
                        q.put(("polling", f"Scene {sn}: Queued (position {pos})..."))
                    else:
                        q.put(("polling", f"Scene {sn}: Queued..."))

            num_frames = max(17, round(duration_seconds * 16 / 4) * 4 + 1)
            result = fal_client.subscribe(
                FAL_MODEL,
                arguments={
                    "prompt": prompt,
                    "num_frames": num_frames,
                    "frames_per_second": 16,
                    "resolution": FAL_RESOLUTION,
                    "num_inference_steps": 30,
                    "enable_safety_checker": False,
                },
                with_logs=True,
                on_queue_update=on_queue_update,
            )

            if cancel_event and cancel_event.is_set():
                q.put(("cancelled", "Video generation stopped."))
                q.put(None)
                return

            video_url = result.get("video", {}).get("url")
            if not video_url:
                q.put(("error", f"Scene {scene_number}: Fal returned no video URL."))
                failed_scenes.append(scene_number)
                continue

            q.put(("downloading", f"Scene {scene_number}: Downloading video..."))
            dl_resp = requests.get(video_url, timeout=120)
            dl_resp.raise_for_status()

            out_path = video_path_for(storyboard_id, scene_number, version)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(dl_resp.content)

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

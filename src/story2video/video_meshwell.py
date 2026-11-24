"""Meshwell 3D scene backend — generate videos via meshwell's asset/scene pipeline.

Follows the same generate_video_to_queue / generate_all_to_queue pattern as other backends.

Requires a running meshwell server (default http://localhost:8420).
"""

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import requests

from .video import video_exists, video_path_for

log = logging.getLogger(__name__)

MESHWELL_BASE = os.environ.get("MESHWELL_URL", "http://localhost:8420")

# Persistent registry: character name -> meshwell asset_id
# Loaded/saved to DATA_DIR/meshwell_assets.json
_asset_registry: dict[str, str] = {}
_registry_path: Path | None = None


def _data_dir() -> Path:
    return Path(os.environ.get("S2V_DATA_DIR", "data"))


def _load_registry() -> None:
    global _asset_registry, _registry_path
    _registry_path = _data_dir() / "meshwell_assets.json"
    if _registry_path.exists():
        _asset_registry = json.loads(_registry_path.read_text())
    else:
        _asset_registry = {}


def _save_registry() -> None:
    if _registry_path is None:
        _load_registry()
    _registry_path.parent.mkdir(parents=True, exist_ok=True)
    _registry_path.write_text(json.dumps(_asset_registry, indent=2))


def meshwell_available() -> bool:
    """Check if the meshwell server is reachable."""
    try:
        resp = requests.get(f"{MESHWELL_BASE}/api/assets", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


MESHWELL_AVAILABLE: bool = meshwell_available()


def asset_thumbnail_url(asset_id: str) -> str:
    """Return the meshwell URL for an asset's thumbnail image."""
    return f"{MESHWELL_BASE}/data/assets/{asset_id}/thumbnail.png"


def asset_detail_url(asset_id: str) -> str:
    """Return the meshwell URL to view an asset (the main UI)."""
    return f"{MESHWELL_BASE}/#asset-{asset_id}"


def fetch_asset_thumbnail(asset_id: str) -> bytes | None:
    """Fetch the thumbnail image bytes from meshwell. Returns None on failure."""
    try:
        resp = requests.get(asset_thumbnail_url(asset_id), timeout=5)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def _api(method: str, path: str, **kwargs) -> requests.Response:
    """Make an API call to meshwell."""
    url = f"{MESHWELL_BASE}{path}"
    kwargs.setdefault("timeout", 120)
    return requests.request(method, url, **kwargs)


def _validate_asset(asset_id: str) -> bool:
    """Check if an asset_id still exists and is complete in meshwell."""
    resp = _api("GET", f"/api/assets/{asset_id}")
    if resp.status_code != 200:
        return False
    data = resp.json()
    return "error" not in data and data.get("stage") == "complete"


OLLAMA_BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


def _rewrite_prompt_via_llm(name: str, appearance: str, description: str) -> str | None:
    """Use a local LLM to rewrite narrative character descriptions into visual prompts."""
    system = (
        "You rewrite character descriptions into concise physical appearance prompts "
        "for 3D character generation. Output ONLY the visual description, no explanations. "
        "Focus on: body type, clothing, hair, skin tone, accessories, pose. "
        "Omit: personality, emotions, backstory, motivations. "
        "Keep it under 20 words."
    )
    user_msg = f"Name: {name}\nAppearance: {appearance}\nDescription: {description}"

    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": user_msg, "system": system, "stream": False},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            if text and len(text) > 5:
                return text
    except Exception as e:
        log.debug("LLM prompt rewrite failed: %s", e)
    return None


def _build_asset_prompt(name: str, appearance: str, description: str) -> str:
    """Build a 3D asset generation prompt from character data.

    Uses a local LLM (Qwen via Ollama) to rewrite narrative descriptions into
    visual prompts suitable for SDXL → TripoSR. Falls back to simple filtering.
    """
    # Try LLM rewrite first
    rewritten = _rewrite_prompt_via_llm(name, appearance, description)
    if rewritten:
        log.info("LLM rewrote prompt for '%s': %s", name, rewritten)
        return f"a 3D character: {rewritten}"

    # Fallback: combine available physical descriptions
    parts = []
    if appearance:
        parts.append(appearance.rstrip("."))
    if description:
        parts.append(description.rstrip("."))
    if not parts:
        parts.append(name)
    else:
        parts.insert(0, name)

    combined = ", ".join(parts)
    return f"a 3D character: {combined}"


def _ensure_asset(character, q: queue.Queue) -> str | None:
    """Ensure a meshwell asset exists for this character. Returns asset_id or None.

    Args:
        character: A alingo_video_story Character object (with name, appearance, meshwell_asset_id)
                   or a plain string (used as both name and prompt).
    """
    if not _asset_registry:
        _load_registry()

    # Extract fields — support both Character objects and plain strings
    if hasattr(character, "name"):
        name = character.name
        appearance = getattr(character, "appearance", "")
        linked_id = getattr(character, "meshwell_asset_id", "")
    else:
        name = str(character)
        appearance = ""
        linked_id = ""

    # Priority 1: meshwell_asset_id on the Character model
    if linked_id:
        if _validate_asset(linked_id):
            return linked_id
        else:
            q.put(("polling", f"Linked asset for '{name}' no longer exists, regenerating..."))

    # Priority 2: file-based registry (legacy / fallback)
    if name.lower() in _asset_registry:
        asset_id = _asset_registry[name.lower()]
        if _validate_asset(asset_id):
            # Backfill the Character model if possible
            if hasattr(character, "meshwell_asset_id"):
                character.meshwell_asset_id = asset_id
            return asset_id
        del _asset_registry[name.lower()]

    # Generate a new asset — build a 3D-appropriate prompt from character data
    description = getattr(character, "description", "") if hasattr(character, "name") else ""
    prompt = _build_asset_prompt(name, appearance, description)
    q.put(("submitting", f"Generating 3D asset for '{name}'..."))

    resp = _api("POST", "/api/generate", json={"prompt": prompt})
    if resp.status_code != 200:
        q.put(("error", f"Failed to start asset generation: {resp.text[:200]}"))
        return None

    asset_id = resp.json().get("asset_id")
    if not asset_id:
        q.put(("error", "Meshwell returned no asset_id"))
        return None

    # Poll until asset is complete
    q.put(("polling", f"Generating 3D asset '{name}' (this takes 3-5 min)..."))
    while True:
        time.sleep(5)
        resp = _api("GET", f"/api/assets/{asset_id}")
        if resp.status_code != 200:
            continue
        data = resp.json()
        stage = data.get("stage", "")
        if stage == "complete":
            q.put(("polling", f"Asset '{name}' ready."))
            break
        elif stage == "failed":
            q.put(("error", f"Asset generation failed: {data.get('error', 'unknown')}"))
            return None
        else:
            q.put(("polling", f"Asset '{name}': {stage}..."))

    # Write back to Character model and file registry
    if hasattr(character, "meshwell_asset_id"):
        character.meshwell_asset_id = asset_id
    _asset_registry[name.lower()] = asset_id
    _save_registry()
    return asset_id


def _map_lighting(visual_lighting: str) -> str:
    """Map alingo_video_story lighting description to a meshwell preset name."""
    ll = visual_lighting.lower()
    if any(w in ll for w in ("night", "dark", "moon", "dim")):
        return "night"
    if any(w in ll for w in ("sunset", "dusk", "dawn", "golden")):
        return "sunset"
    if any(w in ll for w in ("dramatic", "spotlight", "contrast", "noir")):
        return "dramatic"
    if any(w in ll for w in ("bright", "day", "sun", "outdoor", "natural")):
        return "bright_day"
    return "studio"


def _map_camera_angle(camera_angle: str) -> dict:
    """Map alingo_video_story camera angle description to meshwell camera params."""
    cl = camera_angle.lower()

    # Defaults: medium shot
    pos = [0.0, -3.0, 1.5]
    look_at = [0.0, 0.0, 1.0]
    fov = 50.0

    if any(w in cl for w in ("close", "closeup", "close-up", "detail")):
        pos = [0.0, -1.5, 1.5]
        look_at = [0.0, 0.0, 1.5]
        fov = 35.0
    elif any(w in cl for w in ("wide", "establish", "panoram", "aerial")):
        pos = [0.0, -6.0, 3.0]
        look_at = [0.0, 0.0, 0.5]
        fov = 70.0
    elif any(w in cl for w in ("low", "ground", "worm")):
        pos = [0.0, -3.0, 0.3]
        look_at = [0.0, 0.0, 1.5]
        fov = 50.0
    elif any(w in cl for w in ("high", "bird", "overhead", "top")):
        pos = [0.0, -2.0, 4.0]
        look_at = [0.0, 0.0, 0.5]
        fov = 50.0
    elif any(w in cl for w in ("side", "profile")):
        pos = [3.0, 0.0, 1.5]
        look_at = [0.0, 0.0, 1.0]
        fov = 50.0

    return {"position": pos, "look_at": look_at, "fov": fov}


def _build_scene(
    scene,  # alingo_video_story Scene
    asset_ids: dict[str, str],  # character_name -> meshwell asset_id
    q: queue.Queue,
) -> str | None:
    """Create a meshwell scene from alingo_video_story scene data. Returns scene_id or None."""
    # Build placements — one per character
    placements = []
    spacing = 1.5
    offset = -spacing * (len(asset_ids) - 1) / 2
    for i, (char_name, aid) in enumerate(asset_ids.items()):
        x = offset + i * spacing
        placements.append({
            "asset_id": aid,
            "position": [x, 0, 0],
            "rotation_deg": [0, 0, 0],
            "scale": [1, 1, 1],
            "pose": scene.action if scene.action else None,
        })

    # Camera from visual prompt
    visual = scene.visual
    cam = _map_camera_angle(visual.camera_angle if visual else "")
    lighting = _map_lighting(visual.lighting if visual else "")

    keyframes = [{
        "index": 0,
        "camera_position": cam["position"],
        "camera_rotation_deg": [30, 0, 0],
        "look_at": cam["look_at"],
        "fov": cam["fov"],
    }]

    # If motion described, add a second keyframe for camera movement
    if visual and visual.motion:
        ml = visual.motion.lower()
        end_cam = dict(cam)
        if any(w in ml for w in ("zoom in", "push in", "dolly in")):
            end_cam["position"] = [p * 0.6 for p in cam["position"]]
            end_cam["fov"] = cam["fov"] - 10
        elif any(w in ml for w in ("zoom out", "pull out", "dolly out")):
            end_cam["position"] = [p * 1.5 for p in cam["position"]]
            end_cam["fov"] = cam["fov"] + 10
        elif any(w in ml for w in ("pan left",)):
            end_cam["position"] = [cam["position"][0] - 2, cam["position"][1], cam["position"][2]]
        elif any(w in ml for w in ("pan right",)):
            end_cam["position"] = [cam["position"][0] + 2, cam["position"][1], cam["position"][2]]

        if end_cam != cam:
            keyframes.append({
                "index": 1,
                "camera_position": end_cam["position"],
                "camera_rotation_deg": [30, 0, 0],
                "look_at": end_cam.get("look_at", cam["look_at"]),
                "fov": end_cam.get("fov", cam["fov"]),
            })

    description = f"{scene.title}: {scene.setting}" if scene.setting else scene.title

    q.put(("submitting", "Creating meshwell scene..."))
    resp = _api("POST", "/api/scenes", json={
        "description": description or "story scene",
        "placements": placements,
        "keyframes": keyframes,
        "lighting": lighting,
    })
    if resp.status_code != 200:
        q.put(("error", f"Failed to create scene: {resp.text[:200]}"))
        return None

    scene_id = resp.json().get("id")
    if not scene_id:
        q.put(("error", "Meshwell returned no scene id"))
        return None

    return scene_id


def _compose_and_export(
    scene_id: str,
    duration_seconds: float,
    q: queue.Queue,
    storyboard_id: int,
    scene_number: int,
    version: int | None,
) -> str | None:
    """Compose a meshwell scene, export video, and save to alingo_video_story video path.

    Returns the output video path or None.
    """
    # Compose with interpolation for smooth camera
    q.put(("submitting", "Composing 3D scene (Blender render)..."))
    resp = _api("POST", f"/api/scenes/{scene_id}/compose", json={"interpolate": True})
    if resp.status_code != 200:
        q.put(("error", f"Compose failed: {resp.text[:200]}"))
        return None

    # Poll scene status until composed
    q.put(("polling", "Rendering scene..."))
    while True:
        time.sleep(3)
        resp = _api("GET", f"/api/scenes/{scene_id}")
        if resp.status_code != 200:
            continue
        data = resp.json()
        status = data.get("status", "")
        if status == "composed":
            break
        elif status == "failed":
            q.put(("error", f"Scene composition failed: {data.get('error', 'unknown')}"))
            return None
        else:
            q.put(("polling", f"Rendering: {status}..."))

    # Export video
    fps = max(12, int(24 * duration_seconds / 5))  # scale fps to match duration
    fps = min(fps, 24)  # cap at 24
    q.put(("downloading", "Encoding video..."))
    resp = _api("POST", f"/api/scenes/{scene_id}/export-video", json={"fps": fps})
    if resp.status_code != 200:
        q.put(("error", f"Video export failed: {resp.text[:200]}"))
        return None

    result = resp.json()
    if "error" in result:
        q.put(("error", f"Video export error: {result['error']}"))
        return None

    meshwell_video = Path(result["video_path"])
    if not meshwell_video.exists():
        q.put(("error", f"Exported video not found: {meshwell_video}"))
        return None

    # Copy to alingo_video_story's expected location
    out_path = video_path_for(storyboard_id, scene_number, version)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(meshwell_video, out_path)

    return str(out_path)


def generate_video_to_queue(
    prompt: str,
    storyboard_id: int,
    scene_number: int,
    q: queue.Queue,
    first_frame_image: str | None = None,
    version: int | None = None,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
    *,
    scene=None,  # alingo_video_story Scene object (optional, for richer mapping)
    characters: list | None = None,  # alingo_video_story Character objects
) -> None:
    """Generate a video via meshwell's 3D pipeline.

    Pushes (phase, message) tuples to the queue following the standard backend pattern.
    Pushes None sentinel when finished.
    """
    try:
        if not meshwell_available():
            q.put(("error", f"Meshwell server not reachable at {MESHWELL_BASE}"))
            q.put(None)
            return

        if cancel_event and cancel_event.is_set():
            q.put(("cancelled", "Cancelled."))
            q.put(None)
            return

        # Step 1: Ensure assets exist for each character
        scene_characters = []
        if scene and hasattr(scene, "characters"):
            scene_characters = scene.characters
        elif characters:
            scene_characters = characters

        asset_ids: dict[str, str] = {}
        if scene_characters:
            for char in scene_characters:
                if cancel_event and cancel_event.is_set():
                    q.put(("cancelled", "Cancelled."))
                    q.put(None)
                    return
                aid = _ensure_asset(char, q)
                name = char.name if hasattr(char, "name") else str(char)
                if aid:
                    asset_ids[name] = aid
        else:
            # No characters — generate a generic asset from the prompt
            aid = _ensure_asset(prompt, q)
            if aid:
                asset_ids["scene_object"] = aid

        if not asset_ids:
            q.put(("error", "No assets could be created"))
            q.put(None)
            return

        # Step 2: Create scene
        if scene:
            scene_id = _build_scene(scene, asset_ids, q)
        else:
            # Minimal scene from prompt only
            placements = [
                {"asset_id": aid, "position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}
                for aid in asset_ids.values()
            ]
            q.put(("submitting", "Creating scene..."))
            resp = _api("POST", "/api/scenes", json={
                "description": prompt,
                "placements": placements,
                "keyframes": [{"index": 0, "camera_position": [0, -3, 1.5], "camera_rotation_deg": [30, 0, 0], "look_at": [0, 0, 1], "fov": 50}],
                "lighting": "studio",
            })
            scene_id = resp.json().get("id") if resp.status_code == 200 else None

        if not scene_id:
            q.put(("error", "Failed to create scene"))
            q.put(None)
            return

        # Step 3: Compose and export
        result = _compose_and_export(
            scene_id, duration_seconds, q, storyboard_id, scene_number, version,
        )

        if result:
            q.put(("done", result))
        # error already reported by _compose_and_export

    except Exception as e:
        log.exception("Meshwell video generation failed")
        q.put(("error", f"Meshwell generation failed: {e}"))
    finally:
        q.put(None)


def generate_all_to_queue(
    scenes: list[tuple[int, str, int | None]],
    storyboard_id: int,
    q: queue.Queue,
    cancel_event: threading.Event | None = None,
    duration_seconds: int = 5,
    *,
    scene_objects: list | None = None,  # Parallel list of alingo_video_story Scene objects
) -> None:
    """Generate videos for all scenes sequentially via meshwell.

    Args:
        scenes: List of (scene_number, prompt, version) tuples.
        storyboard_id: The storyboard DB id.
        q: Queue for progress updates.
        scene_objects: Optional list of alingo_video_story Scene objects (parallel to scenes).
    """
    try:
        if not meshwell_available():
            q.put(("error", f"Meshwell server not reachable at {MESHWELL_BASE}"))
            q.put(None)
            return

        total = len(scenes)
        failed_scenes: list[int] = []

        # Build a name->asset_id map that persists across scenes so assets are reused
        shared_asset_ids: dict[str, str] = {}

        for idx, (scene_number, prompt, version) in enumerate(scenes, 1):
            if cancel_event and cancel_event.is_set():
                q.put(("cancelled", "Video generation stopped."))
                q.put(None)
                return

            if video_exists(storyboard_id, scene_number, version):
                q.put(("scene-done", f"Scene {scene_number} already has a video, skipping."))
                continue

            q.put(("scene-start", f"Starting scene {scene_number} ({idx} of {total})..."))

            # Pre-fill asset IDs from previous scenes onto this scene's characters
            scene_obj = scene_objects[idx - 1] if scene_objects and idx - 1 < len(scene_objects) else None
            if scene_obj and hasattr(scene_obj, "characters"):
                for char in scene_obj.characters:
                    name_lower = char.name.lower() if hasattr(char, "name") else ""
                    if name_lower and name_lower in shared_asset_ids and not getattr(char, "meshwell_asset_id", ""):
                        char.meshwell_asset_id = shared_asset_ids[name_lower]

            # Use a sub-queue to capture per-scene progress
            sub_q: queue.Queue = queue.Queue()

            generate_video_to_queue(
                prompt=prompt,
                storyboard_id=storyboard_id,
                scene_number=scene_number,
                q=sub_q,
                version=version,
                cancel_event=cancel_event,
                duration_seconds=duration_seconds,
                scene=scene_obj,
            )

            # Collect asset IDs from this scene's characters for reuse in later scenes
            if scene_obj and hasattr(scene_obj, "characters"):
                for char in scene_obj.characters:
                    name_lower = char.name.lower() if hasattr(char, "name") else ""
                    aid = getattr(char, "meshwell_asset_id", "")
                    if name_lower and aid:
                        shared_asset_ids[name_lower] = aid

            # Relay sub-queue messages to main queue
            success = False
            while True:
                msg = sub_q.get()
                if msg is None:
                    break
                phase, text = msg
                if phase == "done":
                    success = True
                    q.put(("scene-done", f"Scene {scene_number} complete."))
                elif phase == "error":
                    q.put(("error", f"Scene {scene_number}: {text}"))
                else:
                    q.put((phase, f"Scene {scene_number}: {text}"))

            if not success:
                failed_scenes.append(scene_number)

        if failed_scenes:
            failed_str = ", ".join(str(s) for s in failed_scenes)
            q.put(("done-with-errors", f"Finished with {len(failed_scenes)} error(s) (scene(s) {failed_str})."))
        else:
            q.put(("done", "All scenes generated via meshwell."))

    except Exception as e:
        log.exception("Meshwell generate-all failed")
        q.put(("error", f"Meshwell generate-all failed: {e}"))
    finally:
        q.put(None)

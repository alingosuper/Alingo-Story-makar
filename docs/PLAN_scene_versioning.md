# Scene Versioning System

## Context

The modify-with-prompt feature overwrites scene data in place. Users want to compare before/after, restore previous versions, and keep generated videos associated with specific scene versions.

## Design Decisions (confirmed by user)

- **Video files versioned**: `scene_{n}_v{v}.mp4` — old videos preserved when a scene is modified
- **Everything versioned**: initial pipeline creates v1, each modify creates v2, v3..., continuation scenes start at v1
- **Auto-migrate on load**: loading a versionless storyboard wraps current state as v1

## Recommendation: Option A — Version history on the Scene dataclass

Everything already lives in a single `storyboard_json` blob — no schema migration needed. Serialization stays self-contained via `to_dict()`/`from_dict()`.

## Data model sketch

```python
@dataclass
class SceneVersion:
    version: int
    instruction: str       # "initial", or the modify instruction
    timestamp: str         # ISO datetime
    title: str
    setting: str
    characters: list[Character]
    action: str
    mood: str
    dialogue: list[str]
    duration_seconds: float
    visual: VisualPrompt | None = None

@dataclass
class Scene:
    scene_number: int
    active_version: int = 1
    versions: list[SceneVersion] = field(default_factory=list)
    # Current fields become computed from active version,
    # or keep them as-is for backward compat and sync on save
```

## Implementation Steps

### Step 1 — Data model changes (`models.py`)

- Add `SceneVersion` dataclass with all mutable scene fields + metadata (version, instruction, timestamp)
- Update `Scene` with `active_version: int` and `versions: list[SceneVersion]` fields
- Add helper methods: `Scene.current_version()`, `Scene.add_version()`, `Scene.restore_version(n)`
- Auto-migration logic in `Scene.from_dict()`: if no `versions` key, wrap current fields as v1

### Step 2 — Video path versioning (`video.py`, `video_hailuo.py`)

- Update `video_path_for()` to accept optional version parameter: `scene_{n}_v{v}.mp4`
- Update `video_exists()` similarly
- Old unversioned paths (`scene_{n}.mp4`) still work for backward compat

### Step 3 — Pipeline versioning (`pipeline.py`)

- Wrap scene creation in version entries for initial generation (v1)
- Continuation pipeline: new scenes start at v1
- Modify pipeline: creates new version (v2, v3, ...) with the modification instruction as metadata

### Step 4 — UI components (`components.py`)

- Version indicator on each scene card ("v2 of 3") with prev/next arrows
- Collapsible "Version history" section showing the instruction that created each version
- "Restore" button to switch active version

### Step 5 — Routes (`app.py`)

- Add `/storyboard/{id}/scene/{n}/version/{v}` route for switching active version
- Update video routes to be version-aware: `/video/{id}/{scene}/{version}`
- Update modify routes to create new versions instead of overwriting

## Files to modify

- `models.py` — SceneVersion dataclass, Scene updates, migration
- `video.py` — versioned video paths
- `video_hailuo.py` — versioned video paths
- `pipeline.py` — version creation in all pipeline flows
- `components.py` — version UI elements
- `app.py` — version-aware routes

## Files NOT modified

- `AlinGo-Prompt_modules.py` — no changes needed
- `AlinGo-Prompt_config.py` — no changes needed
- `pyproject.toml` — no changes needed

## Verification

1. Load an existing (pre-versioning) storyboard — should auto-migrate, show "v1 of 1"
2. Run initial pipeline on a new story — all scenes should be v1
3. Modify a scene — should create v2, old v1 preserved
4. Switch back to v1 — scene card shows v1 content
5. Generate video on v2 — saved as `scene_{n}_v2.mp4`
6. Switch to v1 and generate — saved as `scene_{n}_v1.mp4`
7. Continue a story — new scenes start at v1, existing scenes unchanged

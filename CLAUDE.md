# Alingo Video Story

LLM-powered pipeline that converts short stories into visual video storyboards.

## Architecture

- **Backend**: Python 3.12+, DSPy for LLM orchestration, local Ollama (Qwen3.5:9B)
- **Web UI**: FastHTML + HTMX with real-time SSE progress streaming
- **Video**: Two backends — local Wan 2.1 (Diffusers) and cloud Hailuo/MiniMax API
- **Database**: SQLite (`data/alingo_video_story.db`), storyboards stored as JSON blobs
- **Package manager**: uv

## Pipeline Flow

Story text → Scene Decomposition (DSPy) → Visual Prompt Generation → Consistency Pass → SQLite → Web UI (view/edit/modify/generate video)

## Key Source Files

| File | Purpose |
|------|---------|
| `src/alingo_video_story/app.py` | FastHTML web app, routes, SSE endpoints |
| `src/alingo_video_story/pipeline.py` | LLM pipeline orchestration (generate, continue, modify) |
| `src/alingo_video_story/models.py` | Dataclasses: Storyboard, Chapter, ChapterVersion, Scene, SceneVersion, VisualPrompt |
| `src/alingo_video_story/AlinGo-Prompt_modules.py` | DSPy signatures & modules (decomposition, visuals, consistency) |
| `src/alingo_video_story/components.py` | FastHTML UI components |
| `src/alingo_video_story/video.py` | Wan 2.1 local video generation |
| `src/alingo_video_story/video_hailuo.py` | Hailuo/MiniMax cloud video API |
| `src/alingo_video_story/prompts.py` | Prompt templates |
| `src/alingo_video_story/AlinGo-Prompt_config.py` | DSPy/Ollama configuration |

## Running

```bash
# Prerequisites: Ollama running on localhost:11434 with qwen3.5:9b model, ffmpeg installed

# Install
uv sync

# Start web UI
alingo_video_story-web --port 5001 --log-level INFO

# CLI (legacy)
alingo_video_story sample_story.txt --output-dir ./output
```

## Environment Variables

- `OLLAMA_BASE` — Ollama endpoint (default: `http://localhost:11434`)
- `OLLAMA_MODEL` — LLM model (default: `qwen3.5:9b`)
- `MINIMAX_API_KEY` — For Hailuo cloud video backend (optional)
- `S2V_LOG_LEVEL` — Logging level (default: `INFO`)

## Chapter Hierarchy

Storyboards use a Story → Chapter → Scene hierarchy. All operations (continue, auto-continue, modify, snapshot, rewrite) are scoped to individual chapters. Storyboard-level operations (generate-all videos, combine-videos, delete) remain at the storyboard level.

- **Chapter.story_text** — each chapter stores its own story text; the DB `story_text` column is a concatenation of all chapter texts
- **Add chapter** — creates a new chapter via `run_continuation_pipeline` (new chapter number, new scenes)
- **Continue chapter** — appends scenes to an existing chapter via `run_chapter_continuation_pipeline`
- **Rewrite chapter** — rewrites story text + regenerates scenes, creating chapter versions via `run_chapter_rewrite`
- **Remove chapter** — deletes a chapter; scene numbers stay globally unique (no renumbering)
- Scene numbering is globally unique: new scenes get `max(all_scene_numbers) + 1`
- Old storyboards auto-migrate: `_ensure_chapter_story_text()` populates `Chapter.story_text` from DB column on first access

## Scene Versioning

Scenes support non-destructive versioning. Modifications create new versions (v1, v2, ...) preserving history. Videos are named `scene_{n}_v{v}.mp4`. Pre-versioning storyboards auto-migrate to v1 on load.

## Chapter Versioning

Chapters support non-destructive versioning via `ChapterVersion`. Rewrites snapshot the full chapter state (story_text, title, summary, scenes) before and after. Users can navigate between chapter versions to restore previous states.

## Route Structure

### Chapter-scoped routes (`/storyboard/{id}/chapter/{ch}/...`)
- `POST .../continue` — continue chapter (append scenes)
- `POST .../auto-continue` — auto-generate continuation text then append scenes
- `POST .../modify` — modify all scenes in chapter
- `POST .../rewrite` — rewrite chapter text + regenerate scenes
- `POST .../snapshot-all` — snapshot all scenes in chapter
- `POST .../version/{ver}` — restore chapter to a previous version
- `DELETE .../remove` — remove chapter

### Storyboard-scoped routes
- `POST /storyboard/{id}/add-chapter` — add a new chapter
- `POST /storyboard/{id}/generate-all-video` — generate videos for all scenes
- `POST /storyboard/{id}/combine-videos` — combine all scene videos
- `DELETE /storyboard/{id}` — delete storyboard

### SSE endpoints
- `/chapter-continue-run`, `/chapter-auto-continue-run`, `/chapter-modify-run`, `/chapter-rewrite-run`, `/add-chapter-run`
- `/modify-run` (single-scene modify), `/video-run`, `/video-run-all`, `/combine-run`, `/run` (initial generation)

## Testing

No formal test framework. Manual testing via the web UI with `sample_story.txt`.

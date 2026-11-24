#!/usr/bin/env python3
"""Story-to-video storyboard pipeline.

Usage:
    python alingo_video_story/pipeline.py alingo_video_story/sample_story.txt
    python alingo_video_story/pipeline.py alingo_video_story/sample_story.txt --output-dir ./output
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Generator

from .AlinGo-Prompt_modules import (
    ConsistencyEnforcer,
    ContinuationDecomposer,
    LongStoryDecomposer,
    SceneDecomposer,
    SceneModifier,
    StoryContinuer,
    StoryRewriter,
    VisualPromptGenerator,
)
from .models import Chapter, Character, Scene, Storyboard, VisualPrompt
from .output import write_json, write_markdown

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module singletons (created once, reused across calls)
# ---------------------------------------------------------------------------
_decomposer = SceneDecomposer()
_continuation_decomposer = ContinuationDecomposer()
_visual_generator = VisualPromptGenerator()
_consistency_enforcer = ConsistencyEnforcer()
_scene_modifier = SceneModifier()
_story_rewriter = StoryRewriter()
_story_continuer = StoryContinuer()
_long_decomposer: LongStoryDecomposer | None = None  # lazy-init (needs Deno)

LONG_STORY_THRESHOLD = 15_000  # characters

MAX_RETRIES = 3


def _call_with_retry(module, retries=MAX_RETRIES, **kwargs):
    """Call a DSPy module with retries on parse/validation errors."""
    last_error = None
    for attempt in range(retries):
        try:
            return module(**kwargs)
        except Exception as e:
            last_error = e
            log.warning("DSPy call failed (attempt %d/%d): %s", attempt + 1, retries, e)
    raise last_error  # type: ignore[misc]


def _get_long_decomposer() -> LongStoryDecomposer:
    global _long_decomposer
    if _long_decomposer is None:
        _long_decomposer = LongStoryDecomposer()
    return _long_decomposer


# ---------------------------------------------------------------------------
# Helpers: map DSPy typed output → existing dataclass models
# ---------------------------------------------------------------------------


def _characters_from_AlinGo-Prompt(chars) -> list[Character]:
    return [
        Character(
            name=c.name,
            description=c.description,
            appearance=c.appearance,
            role=c.role,
        )
        for c in chars
    ]


def _scene_from_AlinGo-Prompt(s) -> Scene:
    scene = Scene(
        scene_number=s.scene_number,
        title=s.title,
        setting=s.setting,
        characters=_characters_from_AlinGo-Prompt(s.characters),
        action=s.action,
        mood=s.mood,
        dialogue=list(s.dialogue),
        duration_seconds=s.duration_seconds,
        source_text=getattr(s, "source_text", "") or "",
    )
    # NOTE: snapshot_version is NOT called here because visuals haven't been
    # generated yet. The initial snapshot is taken after the consistency pass
    # in run_pipeline / run_continuation_pipeline so that v1 captures the
    # complete scene state including the visual prompt.
    return scene


def _detail_present(detail: str, prompt_lower: str) -> bool:
    """Check if the key words of a visual detail are already in the prompt."""
    # Split into individual words/phrases and check if the main terms appear
    words = [w.strip(",.;:()") for w in detail.lower().split()]
    # Filter out very short/common words
    keywords = [w for w in words if len(w) > 3]
    if not keywords:
        return detail.lower() in prompt_lower
    # Consider it present if most keywords already appear
    found = sum(1 for w in keywords if w in prompt_lower)
    return found >= len(keywords) * 0.6


def _enrich_video_gen_prompt(prompt: str, camera: str, lighting: str, art_style: str, motion: str) -> str:
    """Append visual details to the video_gen_prompt if the LLM dropped them."""
    prompt_lower = prompt.lower()
    suffixes = []
    if camera and not _detail_present(camera, prompt_lower):
        suffixes.append(f"Camera: {camera}.")
    if lighting and not _detail_present(lighting, prompt_lower):
        suffixes.append(f"Lighting: {lighting}.")
    if art_style and not _detail_present(art_style, prompt_lower):
        suffixes.append(f"Style: {art_style}.")
    if motion and not _detail_present(motion, prompt_lower):
        suffixes.append(f"Motion: {motion}.")
    if suffixes:
        return prompt.rstrip() + " " + " ".join(suffixes)
    return prompt


def _visual_from_AlinGo-Prompt(v) -> VisualPrompt:
    prompt = _enrich_video_gen_prompt(
        v.video_gen_prompt,
        v.camera_angle,
        v.lighting,
        v.art_style,
        v.motion,
    )
    return VisualPrompt(
        camera_angle=v.camera_angle,
        lighting=v.lighting,
        color_palette=list(v.color_palette),
        art_style=v.art_style,
        motion=v.motion,
        video_gen_prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def step_scene_decomposition(story: str, num_scenes: str = "5-10") -> Storyboard:
    """Step 1: Break story into scenes via DSPy."""
    if len(story) > LONG_STORY_THRESHOLD:
        log.info("Story exceeds %d chars — using RLM decomposer", LONG_STORY_THRESHOLD)
        prediction = _call_with_retry(_get_long_decomposer(), story=story)
    else:
        prediction = _call_with_retry(_decomposer, story=story, num_scenes=num_scenes)

    result = prediction.result

    scenes = [_scene_from_AlinGo-Prompt(s) for s in result.scenes]
    chapter = Chapter(chapter_number=1, scenes=scenes)
    storyboard = Storyboard(
        title=result.title,
        story_summary=result.story_summary,
        chapters=[chapter],
    )

    return storyboard


def _dialogue_summary(scene) -> str:
    """Format dialogue lines for the visual prompt generator."""
    if not scene.dialogue:
        return "none"
    return " / ".join(scene.dialogue)


def step_visual_prompts(storyboard: Storyboard) -> Storyboard:
    """Step 2: Generate visual prompt for each scene."""
    for scene in storyboard.scenes:
        char_summary = ", ".join(f"{c.name} ({c.appearance})" for c in scene.characters)
        prediction = _call_with_retry(
            _visual_generator,
            scene_number=scene.scene_number,
            title=scene.title,
            setting=scene.setting,
            action=scene.action,
            mood=scene.mood,
            characters=char_summary or "none specified",
            dialogue=_dialogue_summary(scene),
        )
        scene.visual = _visual_from_AlinGo-Prompt(prediction.result)

    return storyboard


def step_consistency_pass(
    storyboard: Storyboard,
    only_scenes: set[int] | None = None,
) -> Storyboard:
    """Step 3: Review and enforce visual consistency across scenes."""
    # Build a flat scenes list for the LLM (it doesn't need chapter structure)
    sb_data = storyboard.to_dict()
    flat_scenes = []
    for ch in sb_data.get("chapters", []):
        for s in ch.get("scenes", []):
            s.pop("versions", None)
            s.pop("active_version", None)
            flat_scenes.append(s)
    # Replace chapters with flat scenes for LLM consumption
    llm_data = {k: v for k, v in sb_data.items() if k != "chapters"}
    llm_data["scenes"] = flat_scenes
    storyboard_json = json.dumps(llm_data, indent=2)
    prediction = _call_with_retry(_consistency_enforcer, storyboard_json=storyboard_json)
    result = prediction.result

    storyboard.global_art_style = result.global_art_style
    storyboard.global_color_palette = list(result.global_color_palette)

    corrections = {s.get("scene_number"): s for s in result.scenes}

    for scene in storyboard.scenes:
        correction = corrections.get(scene.scene_number)
        if not correction:
            continue
        if only_scenes is not None and scene.scene_number not in only_scenes:
            continue

        corrected_chars = correction.get("characters", [])
        if corrected_chars:
            scene.characters = [
                Character(
                    name=c.get("name", "Unknown"),
                    description=c.get("description", ""),
                    appearance=c.get("appearance", ""),
                    role=c.get("role", ""),
                )
                for c in corrected_chars
            ]

        v = correction.get("visual", {})
        if v and scene.visual:
            scene.visual.camera_angle = v.get("camera_angle", scene.visual.camera_angle)
            scene.visual.lighting = v.get("lighting", scene.visual.lighting)
            scene.visual.color_palette = v.get("color_palette", scene.visual.color_palette)
            scene.visual.art_style = v.get("art_style", scene.visual.art_style)
            scene.visual.motion = v.get("motion", scene.visual.motion)
            scene.visual.video_gen_prompt = v.get("video_gen_prompt", scene.visual.video_gen_prompt)

    return storyboard


# ---------------------------------------------------------------------------
# Pipeline generators (yield progress tuples for SSE streaming)
# ---------------------------------------------------------------------------


def run_pipeline(story: str, num_scenes: str = "5-10") -> Generator[tuple[str, str, Storyboard | None], None, None]:
    """Yield (step_name, message, storyboard_or_none) tuples as pipeline progresses."""
    yield ("decomposition", f"Decomposing story into ~{num_scenes} scenes...", None)
    storyboard = step_scene_decomposition(story, num_scenes=num_scenes)
    yield ("decomposition", f"{len(storyboard.scenes)} scenes extracted", storyboard)

    for scene in storyboard.scenes:
        yield ("visual", f"Generating visuals for scene {scene.scene_number}...", None)
        char_summary = ", ".join(f"{c.name} ({c.appearance})" for c in scene.characters)
        prediction = _call_with_retry(
            _visual_generator,
            scene_number=scene.scene_number,
            title=scene.title,
            setting=scene.setting,
            action=scene.action,
            mood=scene.mood,
            characters=char_summary or "none specified",
            dialogue=_dialogue_summary(scene),
        )
        scene.visual = _visual_from_AlinGo-Prompt(prediction.result)
        yield ("visual", f"Scene {scene.scene_number}: {scene.visual.art_style}", storyboard)
    yield ("visual", "Visual prompts complete", storyboard)

    yield ("consistency", "Running consistency pass...", None)
    storyboard = step_consistency_pass(storyboard)
    yield ("consistency", f"Consistency pass complete — art style: {storyboard.global_art_style}", storyboard)

    # Populate story-level character registry from scenes
    storyboard.ensure_characters()

    # Snapshot v1 now that scenes have their final visuals
    for scene in storyboard.scenes:
        if not scene.versions:
            scene.snapshot_version("initial")

    yield ("done", "Pipeline complete", storyboard)


def run_continuation_pipeline(
    story: str, existing_storyboard: Storyboard
) -> Generator[tuple[str, str, Storyboard | None], None, None]:
    """Yield (step_name, message, storyboard_or_none) tuples for a continuation chapter."""
    # Extract context from existing storyboard
    last_scene = existing_storyboard.scenes[-1] if existing_storyboard.scenes else None
    last_scene_number = last_scene.scene_number if last_scene else 0
    next_scene_number = last_scene_number + 1

    # Build character summary — prefer story-level characters, fall back to scene aggregation
    if existing_storyboard.characters:
        characters_summary = ", ".join(
            f"{c.name} ({c.appearance})" for c in existing_storyboard.characters
        ) or "none established"
    else:
        seen_chars: dict[str, Character] = {}
        for scene in existing_storyboard.scenes:
            for c in scene.characters:
                seen_chars[c.name] = c
        characters_summary = ", ".join(f"{c.name} ({c.appearance})" for c in seen_chars.values()) or "none established"

    last_scene_summary = f"{last_scene.title} — {last_scene.setting}. {last_scene.action}" if last_scene else "N/A"

    # Step 1: Decompose new chapter with context
    yield ("decomposition", "Decomposing new chapter into scenes...", None)
    prediction = _call_with_retry(
        _continuation_decomposer,
        title=existing_storyboard.title,
        global_art_style=existing_storyboard.global_art_style or "not yet established",
        global_color_palette=", ".join(existing_storyboard.global_color_palette) or "not yet established",
        characters_summary=characters_summary,
        last_scene_number=last_scene_number,
        last_scene_summary=last_scene_summary,
        next_scene_number=next_scene_number,
        story=story,
    )
    result = prediction.result

    new_scenes: list[Scene] = []
    for s in result.scenes:
        new_scenes.append(_scene_from_AlinGo-Prompt(s))

    # Force-renumber new scenes so they never collide with existing ones
    # (the LLM sometimes ignores the starting number instruction)
    for i, scene in enumerate(new_scenes):
        scene.scene_number = next_scene_number + i

    # Create a new chapter for the continuation
    next_chapter_number = (existing_storyboard.chapters[-1].chapter_number + 1) if existing_storyboard.chapters else 1
    new_chapter = Chapter(chapter_number=next_chapter_number, scenes=new_scenes)

    # Combine existing chapters + new chapter
    combined = Storyboard(
        title=existing_storyboard.title,
        story_summary=existing_storyboard.story_summary,
        chapters=list(existing_storyboard.chapters) + [new_chapter],
        global_art_style=existing_storyboard.global_art_style,
        global_color_palette=list(existing_storyboard.global_color_palette),
    )
    yield ("decomposition", f"{len(new_scenes)} new scenes extracted (total: {len(combined.scenes)})", combined)

    # Step 2: Visual prompts for new scenes only
    for scene in new_scenes:
        yield ("visual", f"Generating visuals for scene {scene.scene_number}...", None)
        char_summary = ", ".join(f"{c.name} ({c.appearance})" for c in scene.characters)
        prediction = _call_with_retry(
            _visual_generator,
            scene_number=scene.scene_number,
            title=scene.title,
            setting=scene.setting,
            action=scene.action,
            mood=scene.mood,
            characters=char_summary or "none specified",
            dialogue=_dialogue_summary(scene),
        )
        scene.visual = _visual_from_AlinGo-Prompt(prediction.result)
        yield ("visual", f"Scene {scene.scene_number}: {scene.visual.art_style}", combined)
    yield ("visual", "Visual prompts complete", combined)

    # Step 3: Consistency pass on the FULL combined storyboard
    yield ("consistency", "Running consistency pass on full storyboard...", None)
    new_scene_numbers = {s.scene_number for s in new_scenes}
    combined = step_consistency_pass(combined, only_scenes=new_scene_numbers)
    yield ("consistency", f"Consistency pass complete — art style: {combined.global_art_style}", combined)

    # Merge new characters into story-level registry
    combined.merge_new_characters(new_scenes)

    # Snapshot v1 for new scenes now that they have their final visuals
    for scene in new_scenes:
        if not scene.versions:
            scene.snapshot_version("initial")

    yield ("done", "Continuation complete", combined)


def run_chapter_continuation_pipeline(
    story: str,
    existing_storyboard: Storyboard,
    chapter_number: int,
) -> Generator[tuple[str, str, Storyboard | None], None, None]:
    """Yield (step_name, message, storyboard_or_none) tuples — appends scenes to an existing chapter."""
    target_chapter = existing_storyboard.chapter_by_number(chapter_number)
    if target_chapter is None:
        yield ("error", f"Chapter {chapter_number} not found", None)
        return

    # Context from the target chapter's last scene
    last_scene = target_chapter.scenes[-1] if target_chapter.scenes else None
    # Global max scene number for renumbering
    all_scenes = existing_storyboard.scenes
    last_scene_number = max((s.scene_number for s in all_scenes), default=0)
    next_scene_number = last_scene_number + 1

    # Build character summary — prefer story-level characters, fall back to scene aggregation
    if existing_storyboard.characters:
        characters_summary = ", ".join(
            f"{c.name} ({c.appearance})" for c in existing_storyboard.characters
        ) or "none established"
    else:
        seen_chars: dict[str, Character] = {}
        for scene in all_scenes:
            for c in scene.characters:
                seen_chars[c.name] = c
        characters_summary = ", ".join(f"{c.name} ({c.appearance})" for c in seen_chars.values()) or "none established"

    last_scene_summary = f"{last_scene.title} — {last_scene.setting}. {last_scene.action}" if last_scene else "N/A"

    # Step 1: Decompose continuation with context
    yield ("decomposition", f"Decomposing continuation for chapter {chapter_number}...", None)
    prediction = _call_with_retry(
        _continuation_decomposer,
        title=existing_storyboard.title,
        global_art_style=existing_storyboard.global_art_style or "not yet established",
        global_color_palette=", ".join(existing_storyboard.global_color_palette) or "not yet established",
        characters_summary=characters_summary,
        last_scene_number=last_scene_number,
        last_scene_summary=last_scene_summary,
        next_scene_number=next_scene_number,
        story=story,
    )
    result = prediction.result

    new_scenes: list[Scene] = []
    for s in result.scenes:
        new_scenes.append(_scene_from_AlinGo-Prompt(s))

    # Force-renumber
    for i, scene in enumerate(new_scenes):
        scene.scene_number = next_scene_number + i

    # Append to target chapter
    target_chapter.scenes.extend(new_scenes)
    # Update chapter story_text
    if target_chapter.story_text:
        target_chapter.story_text += "\n\n" + story
    else:
        target_chapter.story_text = story

    yield (
        "decomposition",
        f"{len(new_scenes)} new scenes added to chapter {chapter_number} (total: {len(all_scenes) + len(new_scenes)})",
        existing_storyboard,
    )

    # Step 2: Visual prompts for new scenes
    for scene in new_scenes:
        yield ("visual", f"Generating visuals for scene {scene.scene_number}...", None)
        char_summary = ", ".join(f"{c.name} ({c.appearance})" for c in scene.characters)
        prediction = _call_with_retry(
            _visual_generator,
            scene_number=scene.scene_number,
            title=scene.title,
            setting=scene.setting,
            action=scene.action,
            mood=scene.mood,
            characters=char_summary or "none specified",
            dialogue=_dialogue_summary(scene),
        )
        scene.visual = _visual_from_AlinGo-Prompt(prediction.result)
        yield ("visual", f"Scene {scene.scene_number}: {scene.visual.art_style}", existing_storyboard)
    yield ("visual", "Visual prompts complete", existing_storyboard)

    # Step 3: Consistency pass on full storyboard
    yield ("consistency", "Running consistency pass on full storyboard...", None)
    new_scene_numbers = {s.scene_number for s in new_scenes}
    existing_storyboard = step_consistency_pass(existing_storyboard, only_scenes=new_scene_numbers)
    yield (
        "consistency",
        f"Consistency pass complete — art style: {existing_storyboard.global_art_style}",
        existing_storyboard,
    )

    # Merge new characters into story-level registry
    existing_storyboard.merge_new_characters(new_scenes)

    # Snapshot v1 for new scenes
    for scene in new_scenes:
        if not scene.versions:
            scene.snapshot_version("initial")

    yield ("done", "Chapter continuation complete", existing_storyboard)


def run_chapter_rewrite(
    storyboard: Storyboard,
    chapter_number: int,
    instruction: str,
) -> Generator[tuple[str, str, Storyboard | None], None, None]:
    """Rewrite a chapter: rewrite its story_text, regenerate scenes, manage chapter versions."""
    chapter = storyboard.chapter_by_number(chapter_number)
    if chapter is None:
        yield ("error", f"Chapter {chapter_number} not found", None)
        return

    if not chapter.story_text:
        yield ("error", f"Chapter {chapter_number} has no story text to rewrite", None)
        return

    # Phase 1: Rewrite the story text
    yield ("rewrite", f"Rewriting chapter {chapter_number} story...", None)
    rewritten = None
    for event in run_rewrite(chapter.story_text, instruction):
        step, message, result = event
        yield ("rewrite", message, None)
        if step == "done" and result is not None:
            rewritten = result

    if rewritten is None:
        yield ("error", "Rewrite produced no result", None)
        return

    # Phase 2: Snapshot current chapter state
    chapter.snapshot_version(f"before: {instruction}")

    # Phase 3: Run full pipeline on rewritten text to get new scenes
    yield ("pipeline", "Regenerating scenes from rewritten text...", None)
    new_storyboard = None
    for event in run_pipeline(rewritten):  # type: ignore[assignment]
        step, message, sb = event
        yield ("pipeline", message, None)
        if sb is not None:
            new_storyboard = sb

    if new_storyboard is None:
        yield ("error", "Pipeline produced no result", None)
        return

    # Phase 4: Replace chapter content with new scenes (renumbered globally)
    all_other_scenes = [s for ch in storyboard.chapters if ch.chapter_number != chapter_number for s in ch.scenes]
    max_other = max((s.scene_number for s in all_other_scenes), default=0)
    next_num = max_other + 1
    for s in new_storyboard.scenes:  # type: ignore[attr-defined]
        s.scene_number = next_num
        next_num += 1

    chapter.story_text = rewritten
    chapter.title = new_storyboard.chapters[0].title if new_storyboard.chapters else chapter.title  # type: ignore[attr-defined]
    chapter.summary = new_storyboard.story_summary  # type: ignore[attr-defined]
    chapter.scenes = list(new_storyboard.scenes)  # type: ignore[attr-defined]

    # Merge new characters into story-level registry
    storyboard.merge_new_characters(chapter.scenes)

    # Snapshot new state
    chapter.snapshot_version(instruction)

    yield ("done", f"Chapter {chapter_number} rewritten", storyboard)


def run_modify_pipeline(
    storyboard: Storyboard,
    instruction: str,
    scene_numbers: set[int] | None = None,
    chapter_number: int | None = None,
) -> Generator[tuple[str, str, Storyboard | None], None, None]:
    """Yield (step_name, message, storyboard_or_none) tuples as scene modification progresses."""
    if chapter_number is not None:
        chapter = storyboard.chapter_by_number(chapter_number)
        pool = chapter.scenes if chapter else []
    else:
        pool = storyboard.scenes
    targets = [s for s in pool if scene_numbers is None or s.scene_number in scene_numbers]

    global_context = (
        f"Title: {storyboard.title}, "
        f"Art style: {storyboard.global_art_style}, "
        f"Palette: {', '.join(storyboard.global_color_palette)}"
    )

    yield ("modify", f"Applying modification to {len(targets)} scene(s)...", None)
    for scene in targets:
        yield ("modify", f"Modifying scene {scene.scene_number}...", None)
        # Strip version history from the JSON sent to the LLM — it's irrelevant
        # to the modification and bloats/confuses the prompt
        scene_data = scene.to_dict()
        scene_data.pop("versions", None)
        scene_data.pop("active_version", None)
        scene_json = json.dumps(scene_data, indent=2)
        prediction = _call_with_retry(
            _scene_modifier,
            scene_json=scene_json,
            global_context=global_context,
            instruction=instruction,
        )
        result = prediction.result
        # Update scene in-place (preserve source_text — it ties to the original story)
        scene.title = result.title
        scene.setting = result.setting
        scene.characters = _characters_from_AlinGo-Prompt(result.characters)
        scene.action = result.action
        scene.mood = result.mood
        scene.dialogue = list(result.dialogue)
        scene.duration_seconds = result.duration_seconds
        if result.visual:
            scene.visual = _visual_from_AlinGo-Prompt(result.visual)
        scene.snapshot_version(instruction)
        yield ("modify", f"Scene {scene.scene_number} updated", storyboard)

    yield ("done", "Modification complete", storyboard)


def run_rewrite(story: str, instruction: str) -> Generator[tuple[str, str, str | None], None, None]:
    """Yield (step_name, message, rewritten_text_or_none) tuples as rewrite progresses."""
    yield ("rewrite", "Rewriting story...", None)
    prediction = _call_with_retry(_story_rewriter, story=story, instruction=instruction)
    rewritten = prediction.result
    yield ("done", "Story rewritten", rewritten)


def run_story_continuation(
    story: str, num_scenes: int, instruction: str
) -> Generator[tuple[str, str, str | None], None, None]:
    """Yield (step_name, message, continuation_text_or_none) tuples as story continuation progresses."""
    yield ("continue", "Generating story continuation...", None)
    prediction = _call_with_retry(_story_continuer, story=story, num_scenes=num_scenes, instruction=instruction)
    continuation = prediction.result
    yield ("done", "Story continuation generated", continuation)


def step_output(storyboard: Storyboard, output_dir: str):
    """Step 4: Write JSON and Markdown output files."""
    json_path = write_json(storyboard, output_dir)
    md_path = write_markdown(storyboard, output_dir)
    return json_path, md_path


def main():
    from dotenv import load_dotenv

    load_dotenv()
    import logging as _logging

    parser = argparse.ArgumentParser(description="Generate a video storyboard from a short story using a local LLM.")
    parser.add_argument("story_file", help="Path to a text file containing the story")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for output files (default: current directory)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING"],
        help="Log level (default: INFO, use DEBUG to see full prompts/responses)",
    )
    args = parser.parse_args()

    _logging.basicConfig(
        level=getattr(_logging, args.log_level),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Configure DSPy before running the pipeline
    from .AlinGo-Prompt_config import configure_AlinGo-Prompt

    configure_AlinGo-Prompt()

    if not os.path.isfile(args.story_file):
        print(f"Error: file not found: {args.story_file}")
        sys.exit(1)

    with open(args.story_file) as f:
        story = f.read().strip()

    if not story:
        print("Error: story file is empty")
        sys.exit(1)

    print(f"Reading story from {args.story_file} ({len(story)} chars)")
    print()

    storyboard = None
    for step, message, sb in run_pipeline(story):
        print(f"[{step}] {message}")
        if sb is not None:
            storyboard = sb

    print()
    print("Step 4/4: Writing output files...")
    json_path, md_path = step_output(storyboard, args.output_dir)  # type: ignore[arg-type]
    print(f"  -> {json_path}")
    print(f"  -> {md_path}")

    print()
    print("Done!")


if __name__ == "__main__":
    main()

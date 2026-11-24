"""JSON and Markdown output formatters."""

import json
import os

from .models import Storyboard


def write_json(storyboard: Storyboard, output_dir: str) -> str:
    path = os.path.join(output_dir, "storyboard.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(storyboard.to_dict(), f, indent=2)
    return path


def write_markdown(storyboard: Storyboard, output_dir: str) -> str:
    path = os.path.join(output_dir, "storyboard.md")
    os.makedirs(output_dir, exist_ok=True)

    lines = []
    lines.append(f"# {storyboard.title}\n")
    lines.append(f"**Summary:** {storyboard.story_summary}\n")

    if storyboard.global_art_style:
        lines.append(f"**Art Style:** {storyboard.global_art_style}\n")
    if storyboard.global_color_palette:
        lines.append(f"**Color Palette:** {', '.join(storyboard.global_color_palette)}\n")

    lines.append("---\n")

    for scene in storyboard.scenes:
        lines.append(f"## Scene {scene.scene_number}: {scene.title}\n")
        lines.append(f"**Setting:** {scene.setting}\n")
        lines.append(f"**Mood:** {scene.mood}\n")
        lines.append(f"**Duration:** {scene.duration_seconds}s\n")

        if scene.characters:
            lines.append("### Characters\n")
            for c in scene.characters:
                lines.append(f"- **{c.name}** ({c.role}): {c.appearance}\n")

        lines.append(f"### Action\n\n{scene.action}\n")

        if scene.dialogue:
            lines.append("### Dialogue\n")
            for d in scene.dialogue:
                lines.append(f"> {d}\n")

        if scene.visual:
            lines.append("### Visual Details\n")
            lines.append(f"- **Camera:** {scene.visual.camera_angle}")
            lines.append(f"- **Lighting:** {scene.visual.lighting}")
            lines.append(f"- **Art Style:** {scene.visual.art_style}")
            lines.append(f"- **Motion:** {scene.visual.motion}")
            if scene.visual.color_palette:
                lines.append(f"- **Palette:** {', '.join(scene.visual.color_palette)}")
            lines.append(f"\n**Video Gen Prompt:**\n\n> {scene.visual.video_gen_prompt}\n")

        lines.append("---\n")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path

"""Shared fixtures for alingo_video_story tests."""

import pytest

from alingo_video_story.models import (
    Chapter,
    Character,
    Scene,
    Storyboard,
    VisualPrompt,
)


@pytest.fixture
def sample_character() -> Character:
    return Character(
        name="Alice",
        description="A curious young woman",
        appearance="Short brown hair, blue eyes, wearing a white dress",
        role="protagonist",
    )


@pytest.fixture
def sample_visual() -> VisualPrompt:
    return VisualPrompt(
        camera_angle="medium close-up",
        lighting="warm golden hour",
        color_palette=["amber", "deep green", "cream"],
        art_style="watercolor illustration",
        motion="slow pan right",
        video_gen_prompt="A young woman walks through a sunlit garden, watercolor style, warm golden hour lighting, slow pan right.",
    )


@pytest.fixture
def sample_scene(sample_character: Character, sample_visual: VisualPrompt) -> Scene:
    return Scene(
        scene_number=1,
        title="The Garden Path",
        setting="A lush garden at sunset",
        characters=[sample_character],
        action="Alice walks slowly along the garden path, pausing to smell the roses.",
        mood="peaceful",
        dialogue=["Alice: What a beautiful evening."],
        duration_seconds=6.0,
        visual=sample_visual,
    )


@pytest.fixture
def sample_chapter(sample_scene: Scene) -> Chapter:
    scene2 = Scene(
        scene_number=2,
        title="The Old Gate",
        setting="A rusty iron gate at the garden's edge",
        characters=[
            Character(
                name="Alice", description="A curious young woman", appearance="Short brown hair", role="protagonist"
            )
        ],
        action="Alice discovers a hidden gate overgrown with ivy.",
        mood="mysterious",
        dialogue=["Alice: I wonder where this leads..."],
        duration_seconds=5.0,
        visual=VisualPrompt(
            camera_angle="wide shot",
            lighting="dappled twilight",
            color_palette=["rust", "dark green", "violet"],
            art_style="watercolor illustration",
            motion="slow zoom in",
            video_gen_prompt="A rusty gate covered in ivy at twilight, watercolor style, slow zoom in.",
        ),
    )
    return Chapter(
        chapter_number=1,
        title="Into the Garden",
        summary="Alice explores a mysterious garden at sunset.",
        scenes=[sample_scene, scene2],
        story_text="Alice wandered into the garden as the sun began to set...",
    )


@pytest.fixture
def sample_storyboard(sample_chapter: Chapter) -> Storyboard:
    chapter2 = Chapter(
        chapter_number=2,
        title="Beyond the Gate",
        summary="Alice ventures through the gate into a magical forest.",
        scenes=[
            Scene(
                scene_number=3,
                title="The Forest Entrance",
                setting="A dense, luminous forest",
                characters=[
                    Character(
                        name="Alice",
                        description="A curious young woman",
                        appearance="Short brown hair",
                        role="protagonist",
                    )
                ],
                action="Alice steps through the gate into a glowing forest.",
                mood="wonder",
                dialogue=[],
                duration_seconds=7.0,
                visual=VisualPrompt(
                    camera_angle="tracking shot",
                    lighting="bioluminescent glow",
                    color_palette=["electric blue", "emerald", "silver"],
                    art_style="watercolor illustration",
                    motion="steady forward tracking",
                    video_gen_prompt="A girl enters a bioluminescent forest, watercolor style, tracking shot.",
                ),
            ),
        ],
        story_text="Beyond the gate, a forest of impossible light stretched out before her...",
    )
    return Storyboard(
        title="Alice's Garden Adventure",
        story_summary="A young woman discovers a magical world hidden behind a garden gate.",
        chapters=[sample_chapter, chapter2],
        global_art_style="watercolor illustration",
        global_color_palette=["amber", "deep green", "violet", "cream"],
    )

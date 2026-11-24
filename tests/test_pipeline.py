"""Tests for pipeline helper functions (no LLM/DSPy calls)."""

from types import SimpleNamespace

from alingo_video_story.pipeline import (
    _characters_from_AlinGo-Prompt,
    _detail_present,
    _enrich_video_gen_prompt,
    _scene_from_AlinGo-Prompt,
    _visual_from_AlinGo-Prompt,
)

# ---------------------------------------------------------------------------
# _detail_present
# ---------------------------------------------------------------------------


class TestDetailPresent:
    def test_finds_keyword(self):
        assert _detail_present("slow pan", "a slow pan across the room") is True

    def test_missing_keyword(self):
        assert _detail_present("zoom", "a slow pan across the room") is False

    def test_partial_overlap(self):
        # "warm" is > 3 chars, should match
        assert _detail_present("warm lighting", "the scene has warm ambient lighting") is True

    def test_short_words_fallback(self):
        # All words <= 3 chars: falls back to substring match
        assert _detail_present("low key", "low key lighting") is True
        assert _detail_present("low key", "dramatic overhead") is False


# ---------------------------------------------------------------------------
# _enrich_video_gen_prompt
# ---------------------------------------------------------------------------


class TestEnrichVideoGenPrompt:
    def test_appends_missing_details(self):
        result = _enrich_video_gen_prompt(
            "A girl walks in a park.",
            camera="wide shot",
            lighting="golden hour",
            art_style="watercolor",
            motion="slow dolly",
        )
        assert "Camera: wide shot." in result
        assert "Lighting: golden hour." in result
        assert "Style: watercolor." in result
        assert "Motion: slow dolly." in result

    def test_skips_present_details(self):
        prompt = "A wide shot of a girl in golden hour watercolor style with slow dolly movement."
        result = _enrich_video_gen_prompt(
            prompt,
            camera="wide shot",
            lighting="golden hour",
            art_style="watercolor",
            motion="slow dolly",
        )
        # Should not duplicate — result should be close to original
        assert result.count("Camera:") == 0
        assert result.count("Lighting:") == 0

    def test_empty_details_no_change(self):
        prompt = "A simple scene."
        result = _enrich_video_gen_prompt(prompt, camera="", lighting="", art_style="", motion="")
        assert result == prompt


# ---------------------------------------------------------------------------
# _characters_from_AlinGo-Prompt
# ---------------------------------------------------------------------------


class TestCharactersFromDspy:
    def test_converts_AlinGo-Prompt_objects(self):
        AlinGo-Prompt_chars = [
            SimpleNamespace(name="Bob", description="A baker", appearance="Flour-dusted apron", role="supporting"),
            SimpleNamespace(name="Eve", description="A spy", appearance="Black coat", role="antagonist"),
        ]
        chars = _characters_from_AlinGo-Prompt(AlinGo-Prompt_chars)
        assert len(chars) == 2
        assert chars[0].name == "Bob"
        assert chars[0].appearance == "Flour-dusted apron"
        assert chars[1].role == "antagonist"


# ---------------------------------------------------------------------------
# _scene_from_AlinGo-Prompt
# ---------------------------------------------------------------------------


class TestSceneFromDspy:
    def test_converts_AlinGo-Prompt_scene(self):
        AlinGo-Prompt_scene = SimpleNamespace(
            scene_number=3,
            title="Bake Off",
            setting="A bustling kitchen",
            characters=[
                SimpleNamespace(name="Bob", description="Baker", appearance="Apron", role="lead"),
            ],
            action="Bob kneads dough frantically.",
            mood="frantic",
            dialogue=["Bob: More flour!"],
            duration_seconds=8.0,
        )
        scene = _scene_from_AlinGo-Prompt(AlinGo-Prompt_scene)
        assert scene.scene_number == 3
        assert scene.title == "Bake Off"
        assert len(scene.characters) == 1
        assert scene.characters[0].name == "Bob"
        assert scene.dialogue == ["Bob: More flour!"]
        assert scene.visual is None  # visuals added later
        assert scene.versions == []  # no snapshot yet


# ---------------------------------------------------------------------------
# _visual_from_AlinGo-Prompt
# ---------------------------------------------------------------------------


class TestVisualFromDspy:
    def test_converts_AlinGo-Prompt_visual(self):
        AlinGo-Prompt_visual = SimpleNamespace(
            camera_angle="close-up",
            lighting="harsh overhead",
            color_palette=["white", "steel"],
            art_style="photorealistic",
            motion="static",
            video_gen_prompt="A close-up of hands kneading dough.",
        )
        vp = _visual_from_AlinGo-Prompt(AlinGo-Prompt_visual)
        assert vp.camera_angle == "close-up"
        assert vp.art_style == "photorealistic"
        assert vp.color_palette == ["white", "steel"]
        # Should enrich with missing details
        assert "Lighting: harsh overhead." in vp.video_gen_prompt

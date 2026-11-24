"""Tests for alingo_video_story.models — round-trips, versioning, migrations."""

import pytest

from alingo_video_story.models import (
    Chapter,
    Character,
    Scene,
    Storyboard,
    VisualPrompt,
)

# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


class TestCharacter:
    def test_round_trip(self, sample_character: Character):
        d = sample_character.to_dict()
        restored = Character.from_dict(d)
        assert restored.name == sample_character.name
        assert restored.description == sample_character.description
        assert restored.appearance == sample_character.appearance
        assert restored.role == sample_character.role

    def test_from_dict_defaults(self):
        c = Character.from_dict({})
        assert c.name == "Unknown"
        assert c.description == ""
        assert c.appearance == ""
        assert c.role == ""


# ---------------------------------------------------------------------------
# VisualPrompt
# ---------------------------------------------------------------------------


class TestVisualPrompt:
    def test_round_trip(self, sample_visual: VisualPrompt):
        d = sample_visual.to_dict()
        restored = VisualPrompt.from_dict(d)
        assert restored.camera_angle == sample_visual.camera_angle
        assert restored.lighting == sample_visual.lighting
        assert restored.color_palette == sample_visual.color_palette
        assert restored.art_style == sample_visual.art_style
        assert restored.motion == sample_visual.motion
        assert restored.video_gen_prompt == sample_visual.video_gen_prompt

    def test_empty_fields(self):
        vp = VisualPrompt.from_dict({})
        assert vp.camera_angle == ""
        assert vp.lighting == ""
        assert vp.color_palette == []
        assert vp.art_style == ""
        assert vp.motion == ""
        assert vp.video_gen_prompt == ""


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------


class TestScene:
    def test_round_trip(self, sample_scene: Scene):
        d = sample_scene.to_dict()
        restored = Scene.from_dict(d)
        assert restored.scene_number == sample_scene.scene_number
        assert restored.title == sample_scene.title
        assert restored.setting == sample_scene.setting
        assert restored.action == sample_scene.action
        assert restored.mood == sample_scene.mood
        assert restored.dialogue == sample_scene.dialogue
        assert restored.duration_seconds == sample_scene.duration_seconds
        assert len(restored.characters) == len(sample_scene.characters)
        assert restored.characters[0].name == sample_scene.characters[0].name
        assert restored.visual is not None
        assert restored.visual.camera_angle == sample_scene.visual.camera_angle

    def test_snapshot_version(self, sample_scene: Scene):
        # No versions initially (fixture doesn't snapshot)
        assert sample_scene.versions == []
        sv = sample_scene.snapshot_version("initial")
        assert sv.version == 1
        assert sv.instruction == "initial"
        assert sv.title == sample_scene.title
        assert sample_scene.active_version == 1
        assert len(sample_scene.versions) == 1

        # Second snapshot
        sample_scene.title = "Modified Title"
        sv2 = sample_scene.snapshot_version("changed title")
        assert sv2.version == 2
        assert sv2.title == "Modified Title"
        assert sample_scene.active_version == 2
        # First snapshot still has original title
        assert sample_scene.versions[0].title == "The Garden Path"

    def test_restore_version(self, sample_scene: Scene):
        sample_scene.snapshot_version("initial")
        original_title = sample_scene.title

        sample_scene.title = "New Title"
        sample_scene.action = "New action"
        sample_scene.snapshot_version("modified")

        sample_scene.restore_version(1)
        assert sample_scene.title == original_title
        assert sample_scene.active_version == 1

    def test_restore_invalid_version(self, sample_scene: Scene):
        with pytest.raises(ValueError, match="Version 99 not found"):
            sample_scene.restore_version(99)

    def test_from_dict_auto_migrates_to_v1(self):
        """A versionless scene dict should auto-migrate to v1 on load."""
        d = {
            "scene_number": 5,
            "title": "Legacy Scene",
            "setting": "Old format",
            "characters": [],
            "action": "Something happens",
            "mood": "neutral",
            "dialogue": [],
            "duration_seconds": 5.0,
        }
        scene = Scene.from_dict(d)
        assert len(scene.versions) == 1
        assert scene.versions[0].version == 1
        assert scene.versions[0].instruction == "initial"
        assert scene.active_version == 1

    def test_from_dict_with_versions(self):
        """Existing versions in the dict should be preserved, not duplicated."""
        d = {
            "scene_number": 1,
            "title": "Versioned",
            "setting": "Test",
            "characters": [],
            "action": "Act",
            "mood": "happy",
            "dialogue": [],
            "duration_seconds": 4.0,
            "active_version": 2,
            "versions": [
                {
                    "version": 1,
                    "instruction": "initial",
                    "timestamp": "2025-01-01",
                    "title": "V1",
                    "setting": "S1",
                    "characters": [],
                    "action": "A1",
                    "mood": "M1",
                    "dialogue": [],
                    "duration_seconds": 4.0,
                },
                {
                    "version": 2,
                    "instruction": "edit",
                    "timestamp": "2025-01-02",
                    "title": "V2",
                    "setting": "S2",
                    "characters": [],
                    "action": "A2",
                    "mood": "M2",
                    "dialogue": [],
                    "duration_seconds": 5.0,
                },
            ],
        }
        scene = Scene.from_dict(d)
        assert len(scene.versions) == 2
        assert scene.active_version == 2
        assert scene.versions[0].title == "V1"
        assert scene.versions[1].title == "V2"

    def test_to_dict_omits_empty_versions(self):
        """When no versions exist, versions/active_version should be absent from dict."""
        scene = Scene(scene_number=1)
        d = scene.to_dict()
        assert "versions" not in d
        assert "active_version" not in d


# ---------------------------------------------------------------------------
# Chapter
# ---------------------------------------------------------------------------


class TestChapter:
    def test_round_trip(self, sample_chapter: Chapter):
        d = sample_chapter.to_dict()
        restored = Chapter.from_dict(d)
        assert restored.chapter_number == sample_chapter.chapter_number
        assert restored.title == sample_chapter.title
        assert restored.summary == sample_chapter.summary
        assert restored.story_text == sample_chapter.story_text
        assert len(restored.scenes) == len(sample_chapter.scenes)

    def test_snapshot_version(self, sample_chapter: Chapter):
        cv = sample_chapter.snapshot_version("initial")
        assert cv.version == 1
        assert cv.title == sample_chapter.title
        assert cv.story_text == sample_chapter.story_text
        assert len(cv.scenes) == len(sample_chapter.scenes)
        assert sample_chapter.active_version == 1

    def test_restore_version(self, sample_chapter: Chapter):
        sample_chapter.snapshot_version("initial")
        original_title = sample_chapter.title
        original_scene_count = len(sample_chapter.scenes)

        sample_chapter.title = "Changed"
        sample_chapter.scenes = []
        sample_chapter.snapshot_version("cleared")

        sample_chapter.restore_version(1)
        assert sample_chapter.title == original_title
        assert len(sample_chapter.scenes) == original_scene_count
        assert sample_chapter.active_version == 1

    def test_restore_invalid_version(self, sample_chapter: Chapter):
        with pytest.raises(ValueError, match="Version 99 not found"):
            sample_chapter.restore_version(99)


# ---------------------------------------------------------------------------
# Storyboard
# ---------------------------------------------------------------------------


class TestStoryboard:
    def test_round_trip(self, sample_storyboard: Storyboard):
        d = sample_storyboard.to_dict()
        restored = Storyboard.from_dict(d)
        assert restored.title == sample_storyboard.title
        assert restored.story_summary == sample_storyboard.story_summary
        assert restored.global_art_style == sample_storyboard.global_art_style
        assert restored.global_color_palette == sample_storyboard.global_color_palette
        assert len(restored.chapters) == len(sample_storyboard.chapters)
        assert len(restored.scenes) == len(sample_storyboard.scenes)

    def test_scenes_property(self, sample_storyboard: Storyboard):
        flat = sample_storyboard.scenes
        assert len(flat) == 3
        assert flat[0].scene_number == 1
        assert flat[1].scene_number == 2
        assert flat[2].scene_number == 3

    def test_chapter_by_number_found(self, sample_storyboard: Storyboard):
        ch = sample_storyboard.chapter_by_number(1)
        assert ch is not None
        assert ch.title == "Into the Garden"

    def test_chapter_by_number_not_found(self, sample_storyboard: Storyboard):
        ch = sample_storyboard.chapter_by_number(99)
        assert ch is None

    def test_from_dict_legacy_flat_scenes(self):
        """Old format with flat 'scenes' list (no chapters) should auto-migrate."""
        d = {
            "title": "Legacy",
            "story_summary": "Old format",
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "S1",
                    "setting": "Here",
                    "characters": [],
                    "action": "Go",
                    "mood": "ok",
                    "dialogue": [],
                    "duration_seconds": 5.0,
                },
            ],
            "global_art_style": "pencil",
            "global_color_palette": ["gray"],
        }
        sb = Storyboard.from_dict(d)
        assert len(sb.chapters) == 1
        assert sb.chapters[0].chapter_number == 1
        assert len(sb.chapters[0].scenes) == 1
        assert sb.scenes[0].title == "S1"

    def test_to_dict_omits_empty_chapter_versions(self, sample_chapter: Chapter):
        """Chapter with no versions should not have versions/active_version in dict."""
        d = sample_chapter.to_dict()
        assert "versions" not in d
        assert "active_version" not in d

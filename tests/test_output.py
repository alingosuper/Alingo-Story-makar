"""Tests for JSON and Markdown output."""

import json

from alingo_video_story.models import Storyboard
from alingo_video_story.output import write_json, write_markdown


class TestWriteJson:
    def test_creates_valid_json(self, sample_storyboard: Storyboard, tmp_path):
        path = write_json(sample_storyboard, str(tmp_path))
        assert path.endswith("storyboard.json")

        with open(path) as f:
            data = json.load(f)

        assert data["title"] == sample_storyboard.title
        restored = Storyboard.from_dict(data)
        assert len(restored.scenes) == len(sample_storyboard.scenes)


class TestWriteMarkdown:
    def test_creates_md_with_headings(self, sample_storyboard: Storyboard, tmp_path):
        path = write_markdown(sample_storyboard, str(tmp_path))
        assert path.endswith("storyboard.md")

        with open(path) as f:
            content = f.read()

        assert f"# {sample_storyboard.title}" in content
        assert "## Scene 1:" in content
        assert "**Setting:**" in content
        assert "**Video Gen Prompt:**" in content

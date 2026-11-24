"""Data structures for the story-to-video pipeline."""

import datetime
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Character:
    name: str
    description: str = ""
    appearance: str = ""
    role: str = ""
    meshwell_asset_id: str = ""  # linked 3D asset from meshwell
    segment_ref: str = ""  # linked segmentation, e.g. "scene_1/char_1"

    def to_dict(self) -> dict:
        d = asdict(self)
        # Only include optional fields when non-empty to keep JSON clean
        if not d.get("meshwell_asset_id"):
            d.pop("meshwell_asset_id", None)
        if not d.get("segment_ref"):
            d.pop("segment_ref", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        return cls(
            name=d.get("name", "Unknown"),
            description=d.get("description", ""),
            appearance=d.get("appearance", ""),
            role=d.get("role", ""),
            meshwell_asset_id=d.get("meshwell_asset_id", ""),
            segment_ref=d.get("segment_ref", ""),
        )


@dataclass
class VisualPrompt:
    camera_angle: str = ""
    lighting: str = ""
    color_palette: list[str] = field(default_factory=list)
    art_style: str = ""
    motion: str = ""
    video_gen_prompt: str = ""
    reference_image: str = ""  # filename like "scene_1_ref.png", empty if none

    def to_dict(self) -> dict:
        d = asdict(self)
        # Only include reference_image when non-empty to keep JSON clean
        if not d.get("reference_image"):
            d.pop("reference_image", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VisualPrompt":
        return cls(
            camera_angle=d.get("camera_angle", ""),
            lighting=d.get("lighting", ""),
            color_palette=d.get("color_palette", []),
            art_style=d.get("art_style", ""),
            motion=d.get("motion", ""),
            video_gen_prompt=d.get("video_gen_prompt", ""),
            reference_image=d.get("reference_image", ""),
        )


@dataclass
class SceneVersion:
    version: int
    instruction: str  # "initial" or the modify instruction
    timestamp: str  # ISO datetime
    title: str
    setting: str
    characters: list[Character]
    action: str
    mood: str
    dialogue: list[str]
    duration_seconds: float
    visual: VisualPrompt | None = None
    source_text: str = ""  # excerpt from the original story this scene is based on

    def to_dict(self) -> dict:
        d = {
            "version": self.version,
            "instruction": self.instruction,
            "timestamp": self.timestamp,
            "title": self.title,
            "setting": self.setting,
            "characters": [c.to_dict() for c in self.characters],
            "action": self.action,
            "mood": self.mood,
            "dialogue": list(self.dialogue),
            "duration_seconds": self.duration_seconds,
        }
        if self.visual is not None:
            d["visual"] = self.visual.to_dict()
        if self.source_text:
            d["source_text"] = self.source_text
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SceneVersion":
        characters = [Character.from_dict(c) for c in d.get("characters", [])]
        visual_data = d.get("visual")
        visual = VisualPrompt.from_dict(visual_data) if visual_data else None
        return cls(
            version=d.get("version", 1),
            instruction=d.get("instruction", "initial"),
            timestamp=d.get("timestamp", ""),
            title=d.get("title", ""),
            setting=d.get("setting", ""),
            characters=characters,
            action=d.get("action", ""),
            mood=d.get("mood", ""),
            dialogue=d.get("dialogue", []),
            duration_seconds=d.get("duration_seconds", 5.0),
            visual=visual,
            source_text=d.get("source_text", ""),
        )


@dataclass
class Scene:
    scene_number: int
    title: str = ""
    setting: str = ""
    characters: list[Character] = field(default_factory=list)
    action: str = ""
    mood: str = ""
    dialogue: list[str] = field(default_factory=list)
    duration_seconds: float = 5.0
    visual: VisualPrompt | None = None
    active_version: int = 0
    versions: list[SceneVersion] = field(default_factory=list)
    source_text: str = ""  # excerpt from the original story this scene is based on

    def snapshot_version(self, instruction: str) -> SceneVersion:
        """Capture current flat fields as a new version entry.

        The visual is deep-copied so the version is a true independent snapshot.
        """
        next_ver = (self.versions[-1].version + 1) if self.versions else 1
        # Deep-copy visual so edits to the flat field don't silently mutate the version
        visual_copy = VisualPrompt.from_dict(self.visual.to_dict()) if self.visual else None
        sv = SceneVersion(
            version=next_ver,
            instruction=instruction,
            timestamp=datetime.datetime.now().isoformat(),
            title=self.title,
            setting=self.setting,
            characters=[Character.from_dict(c.to_dict()) for c in self.characters],
            action=self.action,
            mood=self.mood,
            dialogue=list(self.dialogue),
            duration_seconds=self.duration_seconds,
            visual=visual_copy,
            source_text=self.source_text,
        )
        self.versions.append(sv)
        self.active_version = next_ver
        return sv

    def restore_version(self, version: int) -> None:
        """Copy fields from the matching SceneVersion back to flat fields."""
        sv = next((v for v in self.versions if v.version == version), None)
        if sv is None:
            raise ValueError(f"Version {version} not found for scene {self.scene_number}")
        self.title = sv.title
        self.setting = sv.setting
        self.characters = list(sv.characters)
        self.action = sv.action
        self.mood = sv.mood
        self.dialogue = list(sv.dialogue)
        self.duration_seconds = sv.duration_seconds
        self.visual = sv.visual
        self.source_text = sv.source_text
        self.active_version = version

    def to_dict(self) -> dict:
        d = {
            "scene_number": self.scene_number,
            "title": self.title,
            "setting": self.setting,
            "characters": [c.to_dict() for c in self.characters],
            "action": self.action,
            "mood": self.mood,
            "dialogue": list(self.dialogue),
            "duration_seconds": self.duration_seconds,
        }
        if self.visual is not None:
            d["visual"] = self.visual.to_dict()
        if self.source_text:
            d["source_text"] = self.source_text
        if self.versions:
            d["active_version"] = self.active_version
            d["versions"] = [v.to_dict() for v in self.versions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        characters = [Character.from_dict(c) for c in d.get("characters", [])]
        visual_data = d.get("visual")
        visual = VisualPrompt.from_dict(visual_data) if visual_data else None
        scene = cls(
            scene_number=d.get("scene_number", 0),
            title=d.get("title", ""),
            setting=d.get("setting", ""),
            characters=characters,
            action=d.get("action", ""),
            mood=d.get("mood", ""),
            dialogue=d.get("dialogue", []),
            duration_seconds=d.get("duration_seconds", 5.0),
            visual=visual,
            source_text=d.get("source_text", ""),
        )
        if "versions" in d:
            scene.versions = [SceneVersion.from_dict(v) for v in d["versions"]]
            scene.active_version = d.get("active_version", scene.versions[-1].version if scene.versions else 0)
        elif scene.title:
            # Auto-migrate versionless scenes: create v1 from current fields
            scene.snapshot_version("initial")
        return scene


@dataclass
class Chapter:
    chapter_number: int
    title: str = ""
    summary: str = ""
    scenes: list[Scene] = field(default_factory=list)
    story_text: str = ""
    active_version: int = 0
    versions: list["ChapterVersion"] = field(default_factory=list)

    def snapshot_version(self, instruction: str) -> "ChapterVersion":
        """Deep-copy current chapter state (story_text, title, summary, scenes) as a new version."""
        next_ver = (self.versions[-1].version + 1) if self.versions else 1
        scenes_copy = [Scene.from_dict(s.to_dict()) for s in self.scenes]
        cv = ChapterVersion(
            version=next_ver,
            instruction=instruction,
            timestamp=datetime.datetime.now().isoformat(),
            story_text=self.story_text,
            title=self.title,
            summary=self.summary,
            scenes=scenes_copy,
        )
        self.versions.append(cv)
        self.active_version = next_ver
        return cv

    def restore_version(self, version: int) -> None:
        """Restore title, summary, story_text, scenes from a ChapterVersion."""
        cv = next((v for v in self.versions if v.version == version), None)
        if cv is None:
            raise ValueError(f"Version {version} not found for chapter {self.chapter_number}")
        self.title = cv.title
        self.summary = cv.summary
        self.story_text = cv.story_text
        self.scenes = [Scene.from_dict(s.to_dict()) for s in cv.scenes]
        self.active_version = version

    def to_dict(self) -> dict:
        d = {
            "chapter_number": self.chapter_number,
            "title": self.title,
            "summary": self.summary,
            "scenes": [s.to_dict() for s in self.scenes],
        }
        if self.story_text:
            d["story_text"] = self.story_text
        if self.versions:
            d["active_version"] = self.active_version
            d["versions"] = [v.to_dict() for v in self.versions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        scenes = [Scene.from_dict(s) for s in d.get("scenes", [])]
        ch = cls(
            chapter_number=d.get("chapter_number", 1),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            scenes=scenes,
            story_text=d.get("story_text", ""),
        )
        if "versions" in d:
            ch.versions = [ChapterVersion.from_dict(v) for v in d["versions"]]
            ch.active_version = d.get("active_version", ch.versions[-1].version if ch.versions else 0)
        return ch


@dataclass
class ChapterVersion:
    version: int
    instruction: str
    timestamp: str
    story_text: str
    title: str
    summary: str
    scenes: list[Scene]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "instruction": self.instruction,
            "timestamp": self.timestamp,
            "story_text": self.story_text,
            "title": self.title,
            "summary": self.summary,
            "scenes": [s.to_dict() for s in self.scenes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterVersion":
        scenes = [Scene.from_dict(s) for s in d.get("scenes", [])]
        return cls(
            version=d.get("version", 1),
            instruction=d.get("instruction", "initial"),
            timestamp=d.get("timestamp", ""),
            story_text=d.get("story_text", ""),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            scenes=scenes,
        )


@dataclass
class Storyboard:
    title: str = ""
    story_summary: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    global_art_style: str = ""
    global_color_palette: list[str] = field(default_factory=list)

    @property
    def scenes(self) -> list[Scene]:
        """Flat list of all scenes across all chapters."""
        return [s for ch in self.chapters for s in ch.scenes]

    def chapter_by_number(self, ch_num: int) -> Optional["Chapter"]:
        """Find a chapter by its chapter_number."""
        return next((ch for ch in self.chapters if ch.chapter_number == ch_num), None)

    def to_dict(self) -> dict:
        d = {
            "title": self.title,
            "story_summary": self.story_summary,
            "chapters": [ch.to_dict() for ch in self.chapters],
            "global_art_style": self.global_art_style,
            "global_color_palette": list(self.global_color_palette),
        }
        if self.characters:
            d["characters"] = [c.to_dict() for c in self.characters]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Storyboard":
        if "chapters" in d:
            chapters = [Chapter.from_dict(ch) for ch in d["chapters"]]
        else:
            # Backward compat: old format with flat "scenes" list
            scenes = [Scene.from_dict(s) for s in d.get("scenes", [])]
            chapters = [Chapter(chapter_number=1, scenes=scenes)] if scenes else []
        characters = [Character.from_dict(c) for c in d.get("characters", [])]
        return cls(
            title=d.get("title", ""),
            story_summary=d.get("story_summary", ""),
            chapters=chapters,
            characters=characters,
            global_art_style=d.get("global_art_style", ""),
            global_color_palette=d.get("global_color_palette", []),
        )

    def ensure_characters(self) -> bool:
        """Auto-populate story-level characters from scene data when empty.

        Deduplicates by name (case-insensitive, last-seen wins).
        Returns True if migration happened.
        """
        if self.characters:
            return False
        seen: dict[str, Character] = {}
        for scene in self.scenes:
            for c in scene.characters:
                seen[c.name.lower()] = Character(
                    name=c.name,
                    description=c.description,
                    appearance=c.appearance,
                    role=c.role,
                )
        if not seen:
            return False
        self.characters = list(seen.values())
        return True

    def propagate_character(self, updated: Character) -> int:
        """Update all scenes containing a character with matching name (case-insensitive).

        Returns count of scenes updated.
        """
        count = 0
        name_lower = updated.name.lower()
        for scene in self.scenes:
            for i, c in enumerate(scene.characters):
                if c.name.lower() == name_lower:
                    scene.characters[i] = Character(
                        name=updated.name,
                        description=updated.description,
                        appearance=updated.appearance,
                        role=updated.role,
                        meshwell_asset_id=updated.meshwell_asset_id,
                        segment_ref=updated.segment_ref,
                    )
                    count += 1
                    break
        return count

    def merge_new_characters(self, new_scenes: list["Scene"]) -> None:
        """Merge characters from new scenes into the story-level list (skip existing names)."""
        existing_names = {c.name.lower() for c in self.characters}
        for scene in new_scenes:
            for c in scene.characters:
                if c.name.lower() not in existing_names:
                    self.characters.append(Character(
                        name=c.name,
                        description=c.description,
                        appearance=c.appearance,
                        role=c.role,
                    ))
                    existing_names.add(c.name.lower())

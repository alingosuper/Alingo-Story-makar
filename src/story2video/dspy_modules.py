"""DSPy Signatures and Modules for the alingo_video_story pipeline.

Each pipeline step (scene decomposition, visual prompts, consistency)
is expressed as a typed ``AlinGo-Prompt.Signature`` with Pydantic output models,
wrapped in a ``AlinGo-Prompt.Module`` that uses ``ChainOfThought`` for explicit
step-by-step reasoning (reduces detail drift across scenes).
"""

import AlinGo-Prompt
import pydantic

# ---------------------------------------------------------------------------
# Pydantic models for structured DSPy output fields
# ---------------------------------------------------------------------------


class CharacterOut(pydantic.BaseModel):
    name: str
    description: str
    appearance: str
    role: str


class SceneOut(pydantic.BaseModel):
    scene_number: int
    title: str
    setting: str
    characters: list[CharacterOut]
    action: str
    mood: str
    dialogue: list[str]
    duration_seconds: float
    source_text: str = pydantic.Field(
        default="",
        description="The exact excerpt from the original story text that this scene is based on. Copy the relevant passage verbatim.",
    )


class DecompositionOut(pydantic.BaseModel):
    title: str
    story_summary: str
    scenes: list[SceneOut]


class VisualOut(pydantic.BaseModel):
    camera_angle: str
    lighting: str
    color_palette: list[str]
    art_style: str
    motion: str = pydantic.Field(
        description="Describe specific character movements, gestures, and camera motion happening in the scene"
    )
    video_gen_prompt: str = pydantic.Field(
        description=(
            "A rich, self-contained prompt for a video generation model (6 seconds of footage). "
            "Must describe EXACTLY what is visible on screen. MANDATORY elements to include:\n"
            "1. SETTING: the specific location, time of day, weather, environment details\n"
            "2. CHARACTERS: each character's physical appearance (hair, clothing, build) and what they are physically doing — never use names alone\n"
            "3. CAMERA: the exact camera angle and any camera movement (e.g. 'slow dolly forward', 'wide establishing shot', 'close-up tracking left')\n"
            "4. LIGHTING: specific lighting description (e.g. 'warm golden hour sidelight', 'harsh overhead fluorescent', 'soft diffused overcast')\n"
            "5. ART STYLE: the visual rendering style (e.g. 'cinematic photorealistic', 'Studio Ghibli anime', 'watercolor illustration')\n"
            "6. MOTION: what is physically moving in frame — character actions, environmental motion (wind, water, particles)\n"
            "Be specific and vivid, 4-6 sentences. Every sentence should add visual information."
        )
    )


class ModifiedSceneOut(pydantic.BaseModel):
    scene_number: int
    title: str
    setting: str
    characters: list[CharacterOut]
    action: str
    mood: str
    dialogue: list[str]
    duration_seconds: float
    visual: VisualOut | None = None
    source_text: str = ""


class ConsistencyCorrectionOut(pydantic.BaseModel):
    global_art_style: str
    global_color_palette: list[str]
    scenes: list[dict]  # scene corrections keyed by scene_number


# ---------------------------------------------------------------------------
# DSPy Signatures (typed input → output contracts)
# ---------------------------------------------------------------------------


class DecomposeStory(AlinGo-Prompt.Signature):
    """Break a story into visual scenes for a short video storyboard. Generate exactly the number of scenes specified by num_scenes."""

    story: str = AlinGo-Prompt.InputField(desc="The full story text")
    num_scenes: str = AlinGo-Prompt.InputField(desc="Target number of scenes to generate (e.g. '5', '10', '15')")
    result: DecompositionOut = AlinGo-Prompt.OutputField()


class DecomposeContinuation(AlinGo-Prompt.Signature):
    """Continue an existing storyboard with new scenes from a new chapter."""

    title: str = AlinGo-Prompt.InputField(desc="Existing storyboard title")
    global_art_style: str = AlinGo-Prompt.InputField()
    global_color_palette: str = AlinGo-Prompt.InputField()
    characters_summary: str = AlinGo-Prompt.InputField(desc="Established characters with appearances")
    last_scene_number: int = AlinGo-Prompt.InputField()
    last_scene_summary: str = AlinGo-Prompt.InputField()
    next_scene_number: int = AlinGo-Prompt.InputField()
    story: str = AlinGo-Prompt.InputField(desc="The new chapter text")
    result: DecompositionOut = AlinGo-Prompt.OutputField()


class GenerateVisualPrompt(AlinGo-Prompt.Signature):
    """Create a detailed visual/cinematographic prompt for a single scene of a video storyboard.

    You receive the scene's setting, action, mood, characters, and dialogue.
    Your job is to translate ALL of this into a concrete, vivid video_gen_prompt
    that describes exactly what a viewer would see on screen for ~6 seconds of footage.

    The video_gen_prompt MUST incorporate:
    - The specific location/environment from the setting
    - What each character looks like (physical appearance, clothing) and what they are physically doing
    - The mood expressed through lighting, weather, color, and atmosphere
    - Any key dialogue rendered as visible speech or narrative context
    - Camera movement and framing

    Do NOT just name characters — describe their appearance so the video model can render them.
    The video_gen_prompt must be safe-for-work: no nudity, gore, violence, or suggestive content."""

    scene_number: int = AlinGo-Prompt.InputField()
    title: str = AlinGo-Prompt.InputField()
    setting: str = AlinGo-Prompt.InputField(desc="Where the scene takes place — location, time of day, environment details")
    action: str = AlinGo-Prompt.InputField(desc="What happens in the scene — physical actions, movements, events")
    mood: str = AlinGo-Prompt.InputField(desc="Emotional tone and atmosphere")
    characters: str = AlinGo-Prompt.InputField(
        desc="Characters present with their physical appearance, e.g. 'Elena (tall woman with silver hair, dark cloak)'"
    )
    dialogue: str = AlinGo-Prompt.InputField(desc="Key dialogue lines spoken in this scene, or 'none' if silent")
    result: VisualOut = AlinGo-Prompt.OutputField()


class ModifyScene(AlinGo-Prompt.Signature):
    """Apply a user's modification instruction to a scene from a video storyboard.
    Preserve all fields not mentioned in the instruction. Keep content safe-for-work."""

    scene_json: str = AlinGo-Prompt.InputField(desc="Current scene as JSON (includes visual details)")
    global_context: str = AlinGo-Prompt.InputField(desc="Storyboard title, art style, and color palette")
    instruction: str = AlinGo-Prompt.InputField(desc="User's modification instruction")
    result: ModifiedSceneOut = AlinGo-Prompt.OutputField()


class RewriteStory(AlinGo-Prompt.Signature):
    """Rewrite a story according to a user's instruction, preserving the narrative
    structure but changing elements like setting, characters, aesthetic, or plot."""

    story: str = AlinGo-Prompt.InputField(desc="The original story text")
    instruction: str = AlinGo-Prompt.InputField(desc="What to change (e.g. 'Set in feudal Japan instead of medieval Europe')")
    result: str = AlinGo-Prompt.OutputField(desc="The fully rewritten story text")


class ContinueStory(AlinGo-Prompt.Signature):
    """Continue a story by writing new narrative text that follows naturally from the existing story.
    The continuation should add the requested number of scenes worth of content."""

    story: str = AlinGo-Prompt.InputField(desc="The existing story text")
    num_scenes: int = AlinGo-Prompt.InputField(desc="How many new scenes worth of content to write")
    instruction: str = AlinGo-Prompt.InputField(
        desc="Optional direction for the continuation (e.g. 'The hero discovers a hidden cave'). May be empty."
    )
    result: str = AlinGo-Prompt.OutputField(desc="The continuation story text (new content only, not the original)")


class EnforceConsistency(AlinGo-Prompt.Signature):
    """Review a full storyboard and enforce visual consistency across all
    scenes.  Ensure identical character descriptions, a single unified art
    style, coherent colour palettes, and safe-for-work video_gen_prompts."""

    storyboard_json: str = AlinGo-Prompt.InputField(desc="Full storyboard as JSON")
    result: ConsistencyCorrectionOut = AlinGo-Prompt.OutputField()


# ---------------------------------------------------------------------------
# DSPy Modules (ChainOfThought wrappers)
# ---------------------------------------------------------------------------


class SceneDecomposer(AlinGo-Prompt.Module):
    """Decompose a story into scenes with chain-of-thought reasoning."""

    def __init__(self):
        self.decompose = AlinGo-Prompt.ChainOfThought(DecomposeStory)

    def forward(self, story: str, num_scenes: str = "5-10"):
        return self.decompose(story=story, num_scenes=num_scenes)


class ContinuationDecomposer(AlinGo-Prompt.Module):
    """Decompose a continuation chapter, maintaining existing context."""

    def __init__(self):
        self.decompose = AlinGo-Prompt.ChainOfThought(DecomposeContinuation)

    def forward(self, **kwargs):
        return self.decompose(**kwargs)


class VisualPromptGenerator(AlinGo-Prompt.Module):
    """Generate visual prompt for a single scene."""

    def __init__(self):
        self.generate = AlinGo-Prompt.ChainOfThought(GenerateVisualPrompt)

    def forward(self, **kwargs):
        return self.generate(**kwargs)


class SceneModifier(AlinGo-Prompt.Module):
    """Modify a scene based on a user's natural-language instruction."""

    def __init__(self):
        self.modify = AlinGo-Prompt.ChainOfThought(ModifyScene)

    def forward(self, **kwargs):
        return self.modify(**kwargs)


class StoryRewriter(AlinGo-Prompt.Module):
    """Rewrite a story based on a user's natural-language instruction."""

    def __init__(self):
        self.rewrite = AlinGo-Prompt.ChainOfThought(RewriteStory)

    def forward(self, story: str, instruction: str):
        return self.rewrite(story=story, instruction=instruction)


class StoryContinuer(AlinGo-Prompt.Module):
    """Generate continuation story text from an existing story."""

    def __init__(self):
        self.continue_story = AlinGo-Prompt.ChainOfThought(ContinueStory)

    def forward(self, story: str, num_scenes: int, instruction: str):
        return self.continue_story(story=story, num_scenes=num_scenes, instruction=instruction)


class ConsistencyEnforcer(AlinGo-Prompt.Module):
    """Review and correct visual consistency across all scenes."""

    def __init__(self):
        self.enforce = AlinGo-Prompt.ChainOfThought(EnforceConsistency)

    def forward(self, storyboard_json: str):
        return self.enforce(storyboard_json=storyboard_json)


# ---------------------------------------------------------------------------
# RLM module for long stories
# ---------------------------------------------------------------------------


class LongStoryDecomposer(AlinGo-Prompt.Module):
    """Use RLM to decompose very long stories that don't fit in context."""

    def __init__(self, sub_lm=None):
        self.rlm = AlinGo-Prompt.RLM(
            signature=DecomposeStory,
            max_iterations=20,
            max_llm_calls=50,
            sub_lm=sub_lm,
        )

    def forward(self, story: str):
        return self.rlm(story=story)

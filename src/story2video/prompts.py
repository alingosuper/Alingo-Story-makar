"""LLM prompt templates for each pipeline step."""

SCENE_DECOMPOSITION_SYSTEM = "You are a professional screenwriter and storyboard artist. You analyze stories and break them into visual scenes suitable for video production."

SCENE_DECOMPOSITION_PROMPT = """\
Break the following story into 3-10 scenes for a short video storyboard.

For each scene, provide:
- title: short descriptive title
- setting: where the scene takes place
- characters: list of characters present, each with name, description, appearance, and role
- action: what happens in the scene
- mood: emotional tone
- dialogue: list of spoken lines (empty list if none)
- duration_seconds: suggested duration (3-15 seconds)

Return JSON in this exact format:
{{
  "title": "overall story title",
  "story_summary": "one paragraph summary",
  "scenes": [
    {{
      "scene_number": 1,
      "title": "scene title",
      "setting": "description of location",
      "characters": [
        {{"name": "Name", "description": "who they are", "appearance": "visual description", "role": "protagonist/antagonist/supporting"}}
      ],
      "action": "what happens",
      "mood": "emotional tone",
      "dialogue": ["Character: line of dialogue"],
      "duration_seconds": 5.0
    }}
  ]
}}

STORY:
{story}
"""

VISUAL_PROMPT_SYSTEM = "You are a cinematographer and visual effects artist. You create detailed visual descriptions for video generation from scene descriptions."

VISUAL_PROMPT_TEMPLATE = """\
Generate a detailed visual prompt for this scene from a storyboard.

Scene {scene_number}: {title}
Setting: {setting}
Action: {action}
Mood: {mood}
Characters: {characters}

Return JSON in this exact format:
{{
  "camera_angle": "e.g. wide establishing shot, close-up, over-the-shoulder, low angle",
  "lighting": "e.g. golden hour, dramatic side-lighting, moonlit, overcast diffused",
  "color_palette": ["color1", "color2", "color3"],
  "art_style": "e.g. cinematic realism, watercolor animation, noir, Studio Ghibli-inspired",
  "motion": "camera and subject movement description",
  "video_gen_prompt": "A single detailed paragraph ready to paste into a video generation tool. Include all visual details: setting, characters (fully clothed in appropriate attire), lighting, camera, mood, action. Keep the description safe-for-work: no nudity, gore, violence, or suggestive content. Focus on artistic composition and cinematic storytelling."
}}
"""

CONTINUATION_DECOMPOSITION_PROMPT = """\
You are continuing an existing storyboard with a new chapter.

EXISTING STORYBOARD CONTEXT:
- Title: {title}
- Art style: {global_art_style}
- Color palette: {global_color_palette}
- Established characters: {characters_summary}
- Last scene (#{last_scene_number}): {last_scene_summary}

Break the following NEW CHAPTER into 3-10 scenes that continue from scene {next_scene_number}.
Maintain the same characters, visual style, and tone.

For each scene, provide:
- title: short descriptive title
- setting: where the scene takes place
- characters: list of characters present, each with name, description, appearance, and role
- action: what happens in the scene
- mood: emotional tone
- dialogue: list of spoken lines (empty list if none)
- duration_seconds: suggested duration (3-15 seconds)

Return JSON in this exact format:
{{
  "scenes": [
    {{
      "scene_number": {next_scene_number},
      "title": "scene title",
      "setting": "description of location",
      "characters": [
        {{"name": "Name", "description": "who they are", "appearance": "visual description", "role": "protagonist/antagonist/supporting"}}
      ],
      "action": "what happens",
      "mood": "emotional tone",
      "dialogue": ["Character: line of dialogue"],
      "duration_seconds": 5.0
    }}
  ]
}}

NEW CHAPTER:
{story}
"""

CONSISTENCY_SYSTEM = "You are a continuity supervisor for film production. You ensure visual consistency across all scenes in a storyboard."

CONSISTENCY_PROMPT = """\
Review this storyboard and enforce visual consistency across all scenes.

Fix the following:
1. Character descriptions must be identical across scenes (same clothes, hair, features)
2. Choose ONE art style for the entire storyboard and apply it to all scenes
3. Ensure color palettes are coherent — recurring locations should have consistent colors
4. Update each scene's video_gen_prompt to reflect the consistent descriptions
5. Ensure all video_gen_prompts are safe-for-work — no nudity, gore, explicit violence, or suggestive content. Characters should be appropriately dressed. Focus on artistic storytelling.

Return the full corrected storyboard as JSON with this format:
{{
  "global_art_style": "the unified art style",
  "global_color_palette": ["color1", "color2", "color3", "color4"],
  "scenes": [
    {{
      "scene_number": 1,
      "characters": [
        {{"name": "Name", "description": "consistent description", "appearance": "consistent appearance", "role": "role"}}
      ],
      "visual": {{
        "camera_angle": "...",
        "lighting": "...",
        "color_palette": ["..."],
        "art_style": "the unified art style",
        "motion": "...",
        "video_gen_prompt": "updated prompt with consistent descriptions"
      }}
    }}
  ]
}}

CURRENT STORYBOARD:
{storyboard_json}
"""

"""
Prepare a training dataset from a directory of tagged video clips.

Usage:
    python training/prepare_dataset.py ./raw_clips ./dataset

Expected input structure (raw_clips/):
    low-angle/
        castle_dawn.mp4
        hero_entrance.mp4
    close-up/
        face_reaction.mp4
        hand_detail.mp4
    wide/
        landscape_pan.mp4
    overhead/
        table_scene.mp4

Each subdirectory name becomes the shot-type tag prepended to prompts.
You can optionally include a prompt.txt alongside each clip for a custom prompt,
otherwise a default prompt is generated from the filename and directory.

Output structure (dataset/):
    metadata.jsonl
    videos/
        clip_001.mp4  (trimmed/resized copies)
        clip_002.mp4
        ...
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path


SHOT_TYPE_PROMPTS = {
    "low-angle": "low-angle shot looking upward",
    "high-angle": "high-angle shot looking downward",
    "close-up": "close-up shot with shallow depth of field",
    "extreme-close-up": "extreme close-up macro shot",
    "wide": "wide establishing shot",
    "overhead": "overhead bird's-eye view shot",
    "dutch": "Dutch angle tilted shot",
    "tracking": "smooth tracking shot following the subject",
    "dolly": "dolly zoom shot",
    "pov": "first-person point of view shot",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def trim_and_resize(input_path: Path, output_path: Path, max_duration: float = 4.0,
                    height: int = 480, width: int = 832) -> bool:
    """Trim video to max_duration and resize using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(max_duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-an",  # strip audio
        "-c:v", "libx264",
        "-preset", "fast",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    return result.returncode == 0


def filename_to_description(filename: str) -> str:
    """Convert a filename like 'castle_dawn.mp4' to 'castle dawn'."""
    return Path(filename).stem.replace("_", " ").replace("-", " ")


def main():
    parser = argparse.ArgumentParser(description="Prepare video LoRA training dataset")
    parser.add_argument("input_dir", type=Path, help="Directory of tagged video clips")
    parser.add_argument("output_dir", type=Path, help="Output dataset directory")
    parser.add_argument("--max-duration", type=float, default=4.0, help="Max clip duration in seconds")
    parser.add_argument("--no-resize", action="store_true", help="Skip resizing (use if clips are already correct size)")
    args = parser.parse_args()

    output_dir = args.output_dir
    videos_dir = output_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    clip_idx = 0

    for subdir in sorted(args.input_dir.iterdir()):
        if not subdir.is_dir():
            continue

        shot_type = subdir.name.lower()
        shot_description = SHOT_TYPE_PROMPTS.get(shot_type, f"{shot_type} shot")

        for video_file in sorted(subdir.iterdir()):
            if video_file.suffix.lower() not in VIDEO_EXTENSIONS:
                continue

            clip_idx += 1
            output_filename = f"clip_{clip_idx:04d}.mp4"
            output_path = videos_dir / output_filename

            # Check for custom prompt
            prompt_file = video_file.with_suffix(".txt")
            if prompt_file.exists():
                custom_prompt = prompt_file.read_text().strip()
                prompt = f"{shot_description}, {custom_prompt}"
            else:
                scene_desc = filename_to_description(video_file.name)
                prompt = f"{shot_description}, {scene_desc}"

            # Process video
            if args.no_resize:
                shutil.copy2(video_file, output_path)
                success = True
            else:
                print(f"  Processing {video_file.name} -> {output_filename}")
                success = trim_and_resize(video_file, output_path, args.max_duration)

            if success:
                entries.append({
                    "file": f"videos/{output_filename}",
                    "prompt": prompt,
                    "shot_type": shot_type,
                    "source": str(video_file),
                })
            else:
                print(f"  WARNING: Failed to process {video_file}")

    # Write metadata
    metadata_path = output_dir / "metadata.jsonl"
    with open(metadata_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    print(f"\nDataset ready: {len(entries)} clips in {output_dir}")
    print(f"  metadata: {metadata_path}")
    print(f"  videos:   {videos_dir}")
    print(f"\nNext steps:")
    print(f"  modal volume create s2v-training-data")
    print(f"  modal volume put s2v-training-data {output_dir} /dataset")
    print(f"  modal run training/modal_train_lora.py")


if __name__ == "__main__":
    main()

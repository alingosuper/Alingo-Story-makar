"""
Utility to load trained LoRA adapters into the Wan 2.1 inference pipeline.

Usage from your code:
    from training.load_lora import apply_lora, remove_lora

    # Apply a shot-type LoRA
    apply_lora("./lora_weights/low-angle/latest")

    # Generate video as normal...

    # Switch to a different LoRA
    apply_lora("./lora_weights/close-up/latest")

    # Or remove LoRA entirely
    remove_lora()
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def apply_lora(adapter_path: str | Path, adapter_name: str = "shot_style", alpha: float = 1.0):
    """Load a LoRA adapter into the active Wan pipeline.

    Args:
        adapter_path: Path to the saved PEFT adapter directory.
        adapter_name: Name for this adapter (used for switching between multiple).
        alpha: Scaling factor for the adapter (0.0 = no effect, 1.0 = full).
    """
    from alingo_video_story.video import get_pipeline

    pipe = get_pipeline()
    transformer = pipe.transformer

    adapter_path = Path(adapter_path)
    if not adapter_path.exists():
        raise FileNotFoundError(f"LoRA adapter not found: {adapter_path}")

    # Check if PEFT is already applied
    if hasattr(transformer, "peft_config"):
        # Already has PEFT — load as additional adapter or update
        if adapter_name in transformer.peft_config:
            transformer.delete_adapter(adapter_name)

        transformer.load_adapter(str(adapter_path), adapter_name=adapter_name)
        transformer.set_adapter(adapter_name)
    else:
        # First time — use PeftModel.from_pretrained
        from peft import PeftModel
        pipe.transformer = PeftModel.from_pretrained(
            transformer, str(adapter_path), adapter_name=adapter_name
        )

    # Scale the adapter
    if alpha != 1.0:
        pipe.transformer.set_adapter(adapter_name)
        for module in pipe.transformer.modules():
            if hasattr(module, "scaling"):
                module.scaling[adapter_name] = alpha

    log.info("Applied LoRA adapter '%s' from %s (alpha=%.2f)", adapter_name, adapter_path, alpha)


def remove_lora():
    """Remove all LoRA adapters from the active pipeline, restoring base weights."""
    from alingo_video_story.video import get_pipeline

    pipe = get_pipeline()
    transformer = pipe.transformer

    if hasattr(transformer, "peft_config"):
        pipe.transformer = transformer.merge_and_unload()
        log.info("Removed LoRA adapters, restored base model")
    else:
        log.info("No LoRA adapters to remove")


def list_available_loras(lora_dir: str | Path = "./lora_weights") -> list[dict]:
    """List available LoRA adapters in a directory."""
    lora_dir = Path(lora_dir)
    adapters = []
    if not lora_dir.exists():
        return adapters

    for path in sorted(lora_dir.iterdir()):
        if path.is_dir():
            config_file = path / "latest" / "adapter_config.json"
            if not config_file.exists():
                config_file = path / "adapter_config.json"
            if config_file.exists():
                import json
                config = json.loads(config_file.read_text())
                adapters.append({
                    "name": path.name,
                    "path": str(path / "latest") if (path / "latest").exists() else str(path),
                    "rank": config.get("r", "?"),
                })
    return adapters

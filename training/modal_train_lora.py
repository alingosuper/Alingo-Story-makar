"""
Modal script for LoRA fine-tuning Wan 2.1 on shot-composition datasets.

Supports both 1.3B (lightweight) and 14B (high quality) model variants.
The 14B model requires an A100-80GB or H100; the 1.3B fits on smaller GPUs.

Usage:
    # First time: create the Modal volume and upload your training data
    modal volume create s2v-training-data
    modal volume put s2v-training-data ./dataset /dataset

    # Train with 1.3B (default)
    modal run training/modal_train_lora.py

    # Train with 14B
    modal run training/modal_train_lora.py --model-id Wan-AI/Wan2.1-T2V-14B-Diffusers

    # Download the trained adapter
    modal volume get s2v-training-data /checkpoints/latest ./lora_weights

Dataset layout expected on the volume:
    /dataset/
        metadata.jsonl          # one JSON object per line
        videos/
            clip_001.mp4
            clip_002.mp4
            ...

Each line in metadata.jsonl:
    {"file": "videos/clip_001.mp4", "prompt": "a sweeping low-angle shot of a castle at dawn"}
"""

import modal

# ---------------------------------------------------------------------------
# Modal infrastructure
# ---------------------------------------------------------------------------

VOLUME_NAME = "s2v-training-data"
TIMEOUT_HOURS = 6

app = modal.App("s2v-lora-train")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# Cache the HF model weights so we don't re-download every run
model_cache = modal.Volume.from_name("s2v-model-cache", create_if_missing=True)

training_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.4.0",
        "diffusers>=0.32.0",
        "transformers>=4.40.0",
        "accelerate>=0.25.0",
        "peft>=0.14.0",
        "bitsandbytes>=0.45.0",
        "sentencepiece",
        "ftfy",
        "imageio",
        "imageio-ffmpeg",
        "decord",
        "wandb",
    )
    .apt_install("ffmpeg")
)

VOLUME_PATH = "/data"
MODEL_CACHE_PATH = "/model-cache"
DATASET_PATH = f"{VOLUME_PATH}/dataset"
CHECKPOINT_PATH = f"{VOLUME_PATH}/checkpoints"

# ---------------------------------------------------------------------------
# Training config
# ---------------------------------------------------------------------------

from dataclasses import dataclass

# Model presets — maps shorthand names to HF model IDs and recommended settings
MODEL_PRESETS = {
    "1.3b": {
        "model_id": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "gpu": "A100-80GB",
        "gradient_checkpointing": False,
        "num_frames": 17,
    },
    "14b": {
        "model_id": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
        "gpu": "A100-80GB",
        "gradient_checkpointing": True,  # required to fit in 80GB
        "num_frames": 17,
    },
}


def _resolve_gpu(model_id: str) -> str:
    """Pick GPU based on model size."""
    if "14B" in model_id or "14b" in model_id:
        return "A100-80GB"
    return "A100-80GB"  # use A100 for everything on Modal (good price/perf)


@dataclass
class TrainConfig:
    model_id: str = "Wan-AI/Wan2.1-T2V-14B-Diffusers"

    # LoRA
    lora_rank: int = 32
    lora_alpha: int = 32
    lora_target_modules: tuple = (
        "to_q", "to_k", "to_v", "to_out.0",  # attention
    )

    # Training
    learning_rate: float = 1e-4
    num_epochs: int = 50
    gradient_accumulation_steps: int = 4
    max_grad_norm: float = 1.0
    warmup_steps: int = 100
    gradient_checkpointing: bool = True  # essential for 14B
    seed: int = 42

    # Video
    num_frames: int = 17  # minimum for Wan (4k+1)
    height: int = 480
    width: int = 832
    fps: int = 16

    # Logging
    log_every: int = 10
    save_every_epoch: int = 5
    use_wandb: bool = False
    wandb_project: str = "s2v-lora"


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def load_dataset(config: TrainConfig):
    """Load video-prompt pairs from the volume."""
    import json
    from pathlib import Path

    metadata_path = Path(DATASET_PATH) / "metadata.jsonl"
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"No metadata.jsonl found at {metadata_path}. "
            "Upload your dataset to the Modal volume first."
        )

    entries = []
    with open(metadata_path) as f:
        for line in f:
            entry = json.loads(line.strip())
            video_path = Path(DATASET_PATH) / entry["file"]
            if video_path.exists():
                entries.append({"video_path": str(video_path), "prompt": entry["prompt"]})
            else:
                print(f"Warning: skipping missing file {video_path}")

    print(f"Loaded {len(entries)} training samples")
    return entries


def load_video_frames(video_path: str, num_frames: int, height: int, width: int):
    """Load and preprocess video frames using decord."""
    import numpy as np
    import torch
    from decord import VideoReader, cpu

    vr = VideoReader(video_path, ctx=cpu(0), height=height, width=width)
    total_frames = len(vr)

    # Sample num_frames evenly spaced
    if total_frames >= num_frames:
        indices = np.linspace(0, total_frames - 1, num_frames, dtype=int)
    else:
        # Repeat last frame if video is too short
        indices = list(range(total_frames)) + [total_frames - 1] * (num_frames - total_frames)

    frames = vr.get_batch(indices).asnumpy()  # (T, H, W, C)

    # Normalize to [-1, 1]
    frames = torch.from_numpy(frames).float() / 127.5 - 1.0

    # (T, H, W, C) -> (C, T, H, W)
    frames = frames.permute(3, 0, 1, 2)
    return frames


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

@app.function(
    image=training_image,
    gpu="A100-80GB",
    volumes={VOLUME_PATH: volume, MODEL_CACHE_PATH: model_cache},
    timeout=TIMEOUT_HOURS * 3600,
    secrets=[modal.Secret.from_name("wandb-secret", required=False)],
)
def train(config: TrainConfig = TrainConfig()):
    import gc
    import os
    import random
    from pathlib import Path

    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from peft import LoraConfig, get_peft_model

    os.environ["HF_HOME"] = MODEL_CACHE_PATH
    torch.manual_seed(config.seed)

    is_14b = "14B" in config.model_id or "14b" in config.model_id

    # -- W&B --
    if config.use_wandb:
        import wandb
        wandb.init(project=config.wandb_project, config=vars(config))

    # -- Load dataset --
    dataset = load_dataset(config)
    if not dataset:
        raise ValueError("No training samples found")

    # -- Load model --
    print(f"Loading {config.model_id} ({'14B — gradient checkpointing enabled' if is_14b else '1.3B'})...")

    vae = AutoencoderKLWan.from_pretrained(
        config.model_id, subfolder="vae", torch_dtype=torch.float32
    )
    pipe = WanPipeline.from_pretrained(
        config.model_id, vae=vae, torch_dtype=torch.bfloat16
    )

    # Persist model weights to cache volume so next run skips download
    model_cache.commit()

    # We need the individual components for training
    transformer = pipe.transformer
    text_encoder = pipe.text_encoder
    tokenizer = pipe.tokenizer
    vae = pipe.vae

    # Free the pipeline shell
    del pipe
    gc.collect()

    # Freeze everything except what LoRA will adapt
    vae.requires_grad_(False)
    text_encoder.requires_grad_(False)
    transformer.requires_grad_(False)

    # -- Pre-encode all text prompts (on GPU, then offload) --
    print("Encoding text prompts...")
    text_encoder.to("cuda")
    text_encoder.eval()
    prompt_embeds_cache = {}
    for entry in dataset:
        prompt = entry["prompt"]
        if prompt not in prompt_embeds_cache:
            tokens = tokenizer(
                prompt,
                padding="max_length",
                max_length=tokenizer.model_max_length,
                truncation=True,
                return_tensors="pt",
            ).to("cuda")
            with torch.no_grad():
                prompt_embeds = text_encoder(**tokens).last_hidden_state
            prompt_embeds_cache[prompt] = prompt_embeds.cpu()

    # Free text encoder entirely
    text_encoder.to("cpu")
    del text_encoder
    gc.collect()
    torch.cuda.empty_cache()
    print(f"  Cached {len(prompt_embeds_cache)} unique prompts")

    # -- Pre-encode all video latents (saves VRAM during training) --
    print("Encoding video latents...")
    vae.to("cuda")
    vae.eval()
    latents_cache = {}
    for entry in dataset:
        vpath = entry["video_path"]
        if vpath not in latents_cache:
            pixel_values = load_video_frames(
                vpath, config.num_frames, config.height, config.width
            ).unsqueeze(0).to("cuda")  # (1, C, T, H, W)
            with torch.no_grad():
                latents = vae.encode(pixel_values.float()).latent_dist.sample()
                latents = latents * vae.config.scaling_factor
            latents_cache[vpath] = latents.cpu()
            del pixel_values, latents
            torch.cuda.empty_cache()
    print(f"  Cached {len(latents_cache)} video latents")

    # Free VAE entirely — not needed during training
    vae.to("cpu")
    del vae
    gc.collect()
    torch.cuda.empty_cache()

    # -- Move transformer to GPU --
    transformer.to("cuda")

    # -- Enable gradient checkpointing for 14B --
    if config.gradient_checkpointing:
        transformer.enable_gradient_checkpointing()
        print("  Gradient checkpointing enabled")

    # -- Apply LoRA to transformer --
    lora_config = LoraConfig(
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        target_modules=list(config.lora_target_modules),
        lora_dropout=0.05,
    )
    transformer = get_peft_model(transformer, lora_config)
    transformer.print_trainable_parameters()

    # -- Optimizer --
    trainable_params = [p for p in transformer.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable_params, lr=config.learning_rate, weight_decay=1e-2)

    # -- LR scheduler --
    total_steps = len(dataset) * config.num_epochs // config.gradient_accumulation_steps
    lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(total_steps, 1))

    # -- Training loop --
    checkpoint_dir = Path(CHECKPOINT_PATH)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    global_step = 0
    transformer.train()

    for epoch in range(config.num_epochs):
        random.shuffle(dataset)

        epoch_loss = 0.0
        num_batches = 0

        for i, entry in enumerate(dataset):
            # Load pre-cached latents and prompt embeds
            latents = latents_cache[entry["video_path"]].to("cuda")
            prompt_embeds = prompt_embeds_cache[entry["prompt"]].to("cuda")

            # Sample noise and timesteps (flow matching)
            noise = torch.randn_like(latents)
            batch_size = latents.shape[0]

            # Uniform timestep sampling for flow matching
            timesteps = torch.rand(batch_size, device="cuda")
            sigmas = timesteps.view(-1, 1, 1, 1, 1)

            # Flow matching interpolation: noisy = (1 - sigma) * latents + sigma * noise
            noisy_latents = (1 - sigmas) * latents + sigmas * noise

            # Predict velocity (target is noise - latents for flow matching)
            target = noise - latents

            # Forward pass through transformer
            model_pred = transformer(
                hidden_states=noisy_latents.to(torch.bfloat16),
                encoder_hidden_states=prompt_embeds.to(torch.bfloat16),
                timestep=timesteps * 1000,  # scheduler expects scaled timesteps
            )

            # Handle different output types
            if hasattr(model_pred, "sample"):
                model_pred = model_pred.sample

            # MSE loss
            loss = torch.nn.functional.mse_loss(
                model_pred.float(), target.float(), reduction="mean"
            )
            loss = loss / config.gradient_accumulation_steps
            loss.backward()

            epoch_loss += loss.item() * config.gradient_accumulation_steps
            num_batches += 1

            if (i + 1) % config.gradient_accumulation_steps == 0:
                torch.nn.utils.clip_grad_norm_(trainable_params, config.max_grad_norm)
                optimizer.step()
                lr_scheduler.step()
                optimizer.zero_grad()
                global_step += 1

                if global_step % config.log_every == 0:
                    avg_loss = epoch_loss / num_batches
                    lr = optimizer.param_groups[0]["lr"]
                    vram_gb = torch.cuda.max_memory_allocated() / 1e9
                    print(f"  step {global_step} | loss {avg_loss:.4f} | lr {lr:.2e} | VRAM {vram_gb:.1f}GB")
                    if config.use_wandb:
                        import wandb
                        wandb.log({"loss": avg_loss, "lr": lr, "step": global_step, "vram_gb": vram_gb})

            # Free per-step tensors
            del latents, prompt_embeds, noise, noisy_latents, model_pred, target
            torch.cuda.empty_cache()

        avg_epoch_loss = epoch_loss / max(num_batches, 1)
        print(f"Epoch {epoch + 1}/{config.num_epochs} — avg loss: {avg_epoch_loss:.4f}")

        # Save checkpoint
        if (epoch + 1) % config.save_every_epoch == 0 or (epoch + 1) == config.num_epochs:
            save_path = checkpoint_dir / f"epoch_{epoch + 1}"
            transformer.save_pretrained(save_path)
            print(f"  Saved checkpoint to {save_path}")

            # Also save as "latest" for easy retrieval
            latest_path = checkpoint_dir / "latest"
            transformer.save_pretrained(latest_path)

            volume.commit()

    print("Training complete!")
    print(f"Final adapter saved to {checkpoint_dir / 'latest'}")

    if config.use_wandb:
        import wandb
        wandb.finish()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(
    model: str = "14b",
    rank: int = 32,
    epochs: int = 50,
    lr: float = 1e-4,
    wandb: bool = False,
):
    # Resolve preset shorthand ("14b", "1.3b") or accept full HF model ID
    if model.lower() in MODEL_PRESETS:
        preset = MODEL_PRESETS[model.lower()]
        model_id = preset["model_id"]
        gradient_checkpointing = preset["gradient_checkpointing"]
    else:
        model_id = model
        gradient_checkpointing = "14B" in model or "14b" in model

    config = TrainConfig(
        model_id=model_id,
        lora_rank=rank,
        num_epochs=epochs,
        learning_rate=lr,
        gradient_checkpointing=gradient_checkpointing,
        use_wandb=wandb,
    )
    print(f"Training {model_id} with LoRA rank {rank}, {epochs} epochs")
    train.remote(config)

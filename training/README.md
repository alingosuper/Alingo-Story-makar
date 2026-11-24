# Modal Training & Inference

Run Wan 2.1 video generation and LoRA fine-tuning on Modal's cloud GPUs.

## Prerequisites

```bash
uv add modal
modal token new
```

## Inference

### Deploy

Deploy both model endpoints (14B and 1.3B) with a single command:

```bash
modal deploy src/alingo_video_story/video_modal.py
```

This creates two endpoints:
- **WanInference14B** — A100-80GB, high quality
- **WanInference1_3B** — A10G, cheaper, same quality as local

Both stay warm for 5 minutes between calls. You only pay for active compute time.

### Configure

Set `MODAL_WAN_MODEL` to choose which endpoint to use:

```bash
# 14B (default) — best quality
MODAL_WAN_MODEL=14b alingo_video_story-web --port 5001

# 1.3B — cheaper, frees up your local GPU for other models
MODAL_WAN_MODEL=1.3b alingo_video_story-web --port 5001
```

Select "Modal" as the video backend in the web UI when generating videos.

### With a LoRA adapter

If you've trained a LoRA adapter (see below), set these env vars before deploying:

```bash
MODAL_LORA_PATH=checkpoints/latest modal deploy src/alingo_video_story/video_modal.py
```

`MODAL_LORA_PATH` is relative to the `s2v-training-data` Modal volume. You can also set `MODAL_LORA_ALPHA` (0.0-1.0, default 1.0) to control adapter strength.

## LoRA Training

### 1. Prepare a dataset

Organize source clips by shot type:

```
raw_clips/
  low-angle/
    castle_dawn.mp4
    hero_entrance.mp4
  close-up/
    face_reaction.mp4
  wide/
    landscape_pan.mp4
```

Optionally place a `.txt` file next to any clip for a custom prompt (e.g. `castle_dawn.txt`). Otherwise a prompt is generated from the directory name and filename.

**Option A: Use the tagger UI** (recommended for splitting longer videos):

```bash
python training/tagger.py /path/to/video.mp4 --scene-detect --port 5050
```

Open http://localhost:5050, tag each segment with a shot type and prompt, then click "Export Dataset".

**Option B: Use the batch script** (for pre-organized clips):

```bash
python training/prepare_dataset.py ./raw_clips ./dataset
```

Both produce the same output format:

```
dataset/
  metadata.jsonl
  videos/
    clip_0001.mp4
    clip_0002.mp4
```

### 2. Upload to Modal

```bash
modal volume create s2v-training-data
modal volume put s2v-training-data ./dataset /dataset
```

### 3. Train

```bash
# 14B model (default) — requires A100-80GB
modal run training/modal_train_lora.py

# 1.3B model
modal run training/modal_train_lora.py --model 1.3b

# Custom settings
modal run training/modal_train_lora.py --model 14b --rank 64 --epochs 100 --lr 5e-5
```

Training logs VRAM usage and loss per step. Checkpoints are saved to the Modal volume every 5 epochs and at the end of training.

To enable Weights & Biases logging:

```bash
modal secret create wandb-secret WANDB_API_KEY=<your-key>
modal run training/modal_train_lora.py --wandb
```

### 4. Download adapter weights

```bash
modal volume get s2v-training-data /checkpoints/latest ./lora_weights
```

The adapter is ~50-200 MB depending on rank.

### 5. Use the adapter

**For Modal inference** — redeploy with the LoRA path:

```bash
MODAL_LORA_PATH=checkpoints/latest modal deploy src/alingo_video_story/video_modal.py
```

**For local inference** (1.3B only, on your 4090):

```python
from training.load_lora import apply_lora
apply_lora("./lora_weights")
# generate videos as normal
```

## Cost estimates

| Operation | GPU | ~Cost |
|-----------|-----|-------|
| 14B inference (5s video) | A100-80GB | ~$0.05-0.10 per video |
| 1.3B inference (5s video) | A10G | ~$0.01-0.03 per video |
| 14B LoRA training (200 clips, 50 epochs) | A100-80GB | ~$8-15 total |
| 1.3B LoRA training (200 clips, 50 epochs) | A100-80GB | ~$3-6 total |

Costs based on Modal's per-second billing. Container idle time (up to 5 min between calls) is also billed.

## Files

| File | Purpose |
|------|---------|
| `modal_train_lora.py` | LoRA fine-tuning script (runs on Modal) |
| `tagger.py` | Web UI for splitting and tagging video segments |
| `prepare_dataset.py` | Batch dataset preparation from organized clips |
| `load_lora.py` | Load LoRA adapters into the local Wan pipeline |
| `src/alingo_video_story/video_modal.py` | Modal inference backend (integrates with web UI) |

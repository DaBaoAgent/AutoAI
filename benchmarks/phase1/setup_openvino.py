from __future__ import annotations

import argparse
import json
from pathlib import Path

import openvino
from huggingface_hub import HfApi, snapshot_download


PHASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-id",
        default="OpenVINO/whisper-small-int8-ov",
    )
    args = parser.parse_args()
    model_dir = PHASE_DIR / "models" / args.repo_id.replace("/", "-")
    model_dir.mkdir(parents=True, exist_ok=True)
    info = HfApi().model_info(args.repo_id)
    snapshot_download(
        repo_id=args.repo_id,
        revision=info.sha,
        local_dir=model_dir,
    )
    metadata = {
        "repo_id": args.repo_id,
        "revision": info.sha,
        "openvino_devices": openvino.Core().available_devices,
    }
    (model_dir / ".model-source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

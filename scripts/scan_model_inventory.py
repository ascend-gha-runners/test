#!/usr/bin/env python3
"""
Scan local ModelScope / HuggingFace model caches and emit an inventory JSON.

Output layout (one JSON per runner, consumed by compare_model_inventory.py):

  {
    "runner": "...", "cluster": "...", "arch": "...", "host": "...",
    "scan_time": "...",
    "modelscope":  { "<org>/<model>": {"size_bytes": N, "files": N}, ... },
    "huggingface": { "<org>/<model>": {"size_bytes": N, "files": N}, ... },
    "incomplete":  { "modelscope": [...], "huggingface": ["<model>": n_blobs] }
  }

Disk layouts handled:
  * ModelScope : ~/.cache/modelscope/hub/models/<org>/<model>
                 (older versions may drop the extra `models/` level)
  * HuggingFace: ~/.cache/huggingface/hub/models--<org>--<model>
                 (datasets--/spaces-- are ignored — models only)

Notes:
  * Directory names on disk are used verbatim as the model id. ModelScope
    encodes '.' as '___' (e.g. `Qwen/Qwen2.5-7B` -> `Qwen/Qwen2___5-7B`),
    which is stable across runners so cross-runner comparison still holds.
  * Symlinks (HF snapshot entries point into blobs/) are skipped to avoid
    double-counting; only real files contribute to size/file count.
"""

import argparse
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone

SKIP_DIRS = {"._____temp", ".locks"}


def dir_stats(path, compute_size=True):
    """Return (total_bytes, file_count) for regular (non-symlink) files."""
    if not compute_size:
        return None, None
    total = 0
    files = 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            fp = os.path.join(root, n)
            if os.path.islink(fp):
                continue
            try:
                total += os.stat(fp).st_size
                files += 1
            except OSError:
                continue
    return total, files


def scan_modelscope(root, compute_size=True):
    models = {}
    incomplete = []
    if not os.path.isdir(root):
        return models, incomplete

    # Newer ModelScope: <hub>/models/<org>/<model>; older: <hub>/<org>/<model>
    nested = os.path.join(root, "models")
    parent = nested if os.path.isdir(nested) else root

    for org in sorted(os.listdir(parent)):
        if org.startswith("."):
            continue
        org_dir = os.path.join(parent, org)
        if not os.path.isdir(org_dir) or os.path.islink(org_dir):
            continue
        # ModelScope creates a human-readable symlink (dots preserved) next to
        # the canonical `___`-encoded directory; skip the alias to avoid dupes.
        subdirs = [
            d
            for d in sorted(os.listdir(org_dir))
            if not d.startswith(".")
            and not os.path.islink(os.path.join(org_dir, d))
            and os.path.isdir(os.path.join(org_dir, d))
        ]
        if subdirs:
            for model in subdirs:
                size, files = dir_stats(os.path.join(org_dir, model), compute_size)
                models[f"{org}/{model}"] = {"size_bytes": size, "files": files}
        else:
            # Single-segment model id (no org level).
            size, files = dir_stats(org_dir, compute_size)
            models[org] = {"size_bytes": size, "files": files}

    # In-progress downloads (ModelScope uses ._____temp/<org>/<model>).
    for base in (parent, root):
        temp = os.path.join(base, "._____temp")
        if os.path.isdir(temp):
            for org in os.listdir(temp):
                org_dir = os.path.join(temp, org)
                if os.path.isdir(org_dir):
                    for model in os.listdir(org_dir):
                        incomplete.append(f"{org}/{model}")
    return models, sorted(set(incomplete))


def scan_huggingface(root, compute_size=True):
    models = {}
    incomplete = {}
    if not os.path.isdir(root):
        return models, incomplete

    for name in sorted(os.listdir(root)):
        if name.startswith(".") or not name.startswith("models--"):
            continue
        full = os.path.join(root, name)
        if not os.path.isdir(full) or os.path.islink(full):
            continue
        rest = name[len("models--") :]
        mid = f"{rest.split('--', 1)[0]}/{rest.split('--', 1)[1]}" if "--" in rest else rest
        size, files = dir_stats(full, compute_size)

        # .incomplete blobs signal an interrupted download.
        inc = 0
        blobs = os.path.join(full, "blobs")
        if os.path.isdir(blobs):
            inc = sum(1 for b in os.listdir(blobs) if b.endswith(".incomplete"))
        if inc:
            incomplete[mid] = inc
        models[mid] = {"size_bytes": size, "files": files}

    return models, incomplete


def main():
    parser = argparse.ArgumentParser(description="Scan ModelScope/HuggingFace caches")
    parser.add_argument(
        "--modelscope-root",
        default=os.path.expanduser("~/.cache/modelscope/hub"),
        help="ModelScope hub root (default: ~/.cache/modelscope/hub)",
    )
    parser.add_argument(
        "--huggingface-root",
        default=os.path.expanduser("~/.cache/huggingface/hub"),
        help="HuggingFace hub root (default: ~/.cache/huggingface/hub)",
    )
    parser.add_argument("--runner", default="", help="Runner name/label for the report")
    parser.add_argument("--cluster", default="", help="Cluster name for the report")
    parser.add_argument("--output-json", default="model_inventory.json")
    parser.add_argument(
        "--no-size",
        action="store_true",
        help="Skip recursive size/file counting (faster on huge caches)",
    )
    args = parser.parse_args()

    compute_size = not args.no_size
    ms_models, ms_inc = scan_modelscope(args.modelscope_root, compute_size)
    hf_models, hf_inc = scan_huggingface(args.huggingface_root, compute_size)

    report = {
        "runner": args.runner,
        "cluster": args.cluster,
        "arch": platform.machine(),
        "host": socket.gethostname(),
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "modelscope_root": args.modelscope_root,
        "huggingface_root": args.huggingface_root,
        "modelscope": ms_models,
        "huggingface": hf_models,
        "incomplete": {"modelscope": ms_inc, "huggingface": hf_inc},
    }

    with open(args.output_json, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    print(f"runner={args.runner} cluster={args.cluster} arch={report['arch']}")
    print(f"modelscope_root={args.modelscope_root} models={len(ms_models)} incomplete={len(ms_inc)}")
    print(f"huggingface_root={args.huggingface_root} models={len(hf_models)} incomplete={len(hf_inc)}")
    print(f"wrote {args.output_json}")
    for mid in sorted(ms_models):
        print(f"  [modelscope ] {mid}")
    for mid in sorted(hf_models):
        print(f"  [huggingface] {mid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

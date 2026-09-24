#!/usr/bin/env python3
"""
Scan local ModelScope / HuggingFace model caches and emit an inventory JSON.

Output layout (one JSON per runner, consumed by compare_model_inventory.py):

  {
    "runner": "...", "cluster": "...", "arch": "...", "host": "...",
    "scan_time": "...",
    "modelscope":  { "<org>/<model>": {"size_bytes": N, "files": N, "stub": bool}, ... },
    "huggingface": { "<org>/<model>": {"size_bytes": N, "files": N, "complete": bool}, ... },
    "trash":       { "modelscope": ["<org>/<model>", ...], "huggingface": [...] },
    "incomplete":  { "huggingface": {"<org>/<model>": n_incomplete_blobs} }
  }

Disk layouts handled:
  * ModelScope : ~/.cache/modelscope/hub/models/<org>/<model>
                 (older versions may drop the extra `models/` level)
  * HuggingFace: ~/.cache/huggingface/hub/models--<org>--<model>
                 (datasets--/spaces-- are ignored — models only)

Noise filtering (learned from real runner caches):
  * `*.deleteable` directories are a deletion trash-can -> collected separately,
    never counted as models.
  * Directories with zero regular files are phantom/stub entries -> dropped.
  * HuggingFace completeness is judged by the presence of a non-empty
    `snapshots/` directory (a stub has only `refs/`, tens of bytes).
  * ModelScope has no reliable completeness marker (`._____temp` coexists with
    fully-downloaded models), so a size threshold marks obvious stubs.
  * Symlinks (ModelScope human-readable aliases, HF snapshot links) are skipped
    to avoid double counting.

Notes:
  * Directory names on disk are used verbatim as the model id. ModelScope
    encodes '.' as '___' (e.g. `Qwen/Qwen2.5-7B` -> `Qwen/Qwen2___5-7B`),
    which is stable across runners so cross-runner comparison still holds.
"""

import argparse
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone

SKIP_DIRS = {"._____temp", ".locks"}
TRASH_SUFFIX = ".deleteable"
# ModelScope stub heuristic: a "model" smaller than this is almost certainly a
# pointer/partial download, not a usable model. Configurable via --min-model-bytes.
DEFAULT_MIN_MODEL_BYTES = 1024 * 1024  # 1 MiB


def is_trash(name):
    return name.endswith(TRASH_SUFFIX)


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


def hf_has_snapshot(path):
    """A completed HF cache entry has snapshots/<rev>/ with real files."""
    snap = os.path.join(path, "snapshots")
    if not os.path.isdir(snap):
        return False
    for rev in os.listdir(snap):
        rev_dir = os.path.join(snap, rev)
        if not os.path.isdir(rev_dir):
            continue
        for root, _dirs, names in os.walk(rev_dir):
            for n in names:
                fp = os.path.join(root, n)
                if os.path.islink(fp) or os.path.isfile(fp):
                    return True
    return False


def scan_modelscope(root, compute_size=True, min_model_bytes=DEFAULT_MIN_MODEL_BYTES):
    models = {}
    trash = []
    if not os.path.isdir(root):
        return models, sorted(trash)

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
        for model in sorted(os.listdir(org_dir)):
            if model.startswith("."):
                continue
            mpath = os.path.join(org_dir, model)
            if os.path.islink(mpath) or not os.path.isdir(mpath):
                continue
            mid_org = org.strip()
            mid_model = model.strip()
            if is_trash(model):
                trash.append(f"{mid_org}/{mid_model[: -len(TRASH_SUFFIX)]}")
                continue
            size, files = dir_stats(mpath, compute_size)
            if files == 0:
                continue
            models[f"{mid_org}/{mid_model}"] = {
                "size_bytes": size,
                "files": files,
                "stub": bool(compute_size and (size or 0) < min_model_bytes),
            }
    return models, sorted(set(trash))


def scan_huggingface(root, compute_size=True):
    models = {}
    trash = []
    incomplete = {}
    if not os.path.isdir(root):
        return models, sorted(trash), incomplete

    for name in sorted(os.listdir(root)):
        if name.startswith(".") or not name.startswith("models--"):
            continue
        full = os.path.join(root, name)
        if not os.path.isdir(full) or os.path.islink(full):
            continue
        rest = name[len("models--") :]
        if "--" in rest:
            org, model = rest.split("--", 1)
        else:
            org, model = "", rest
        mid_org = org.strip()
        mid_model = model.strip()
        mid = f"{mid_org}/{mid_model}" if mid_org else mid_model
        if is_trash(model):
            trash.append(mid[: -len(TRASH_SUFFIX)])
            continue
        size, files = dir_stats(full, compute_size)
        if files == 0:
            continue

        # .incomplete blobs signal an interrupted download.
        inc = 0
        blobs = os.path.join(full, "blobs")
        if os.path.isdir(blobs):
            inc = sum(1 for b in os.listdir(blobs) if b.endswith(".incomplete"))
        if inc:
            incomplete[mid] = inc
        models[mid] = {
            "size_bytes": size,
            "files": files,
            "complete": hf_has_snapshot(full),
        }
    return models, sorted(set(trash)), incomplete


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
    parser.add_argument(
        "--min-model-bytes",
        type=int,
        default=DEFAULT_MIN_MODEL_BYTES,
        help="ModelScope stub threshold in bytes (default 1 MiB)",
    )
    args = parser.parse_args()

    compute_size = not args.no_size
    ms_models, ms_trash = scan_modelscope(
        args.modelscope_root, compute_size, args.min_model_bytes
    )
    hf_models, hf_trash, hf_inc = scan_huggingface(args.huggingface_root, compute_size)

    report = {
        "runner": args.runner,
        "cluster": args.cluster,
        "arch": platform.machine(),
        "host": socket.gethostname(),
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "modelscope_root": args.modelscope_root,
        "huggingface_root": args.huggingface_root,
        "min_model_bytes": args.min_model_bytes,
        "modelscope": ms_models,
        "huggingface": hf_models,
        "trash": {"modelscope": ms_trash, "huggingface": hf_trash},
        "incomplete": {"huggingface": hf_inc},
    }

    with open(args.output_json, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    ms_stub = sum(1 for v in ms_models.values() if v.get("stub"))
    hf_stub = sum(1 for v in hf_models.values() if not v.get("complete"))
    print(f"runner={args.runner} cluster={args.cluster} arch={report['arch']}")
    print(
        f"modelscope_root={args.modelscope_root} models={len(ms_models)} "
        f"stub={ms_stub} trash={len(ms_trash)}"
    )
    print(
        f"huggingface_root={args.huggingface_root} models={len(hf_models)} "
        f"incomplete/no-snapshot={hf_stub} trash={len(hf_trash)}"
    )
    print(f"wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

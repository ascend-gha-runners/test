#!/usr/bin/env python3
"""
Compare model inventories across runners.

By default only the ModelScope inventory is compared (`--sources modelscope`).
Baseline defaults to cluster `hk-001` (the amd64 CPU runner). For every other
runner it reports models that exist in the baseline but are MISSING there, plus
models that are EXTRA (informational only).

Baseline models smaller than `--min-model-bytes` (default 10 GiB) are ignored
entirely: they are partial/stub copies and must not drive a "missing" verdict.

Assertion grading (see AGENTS.md):
  * HARD : a *usable* baseline model missing on another runner   -> exit 1
  * SOFT : same model id, size differs beyond tolerance          -> WARN
  * INFO : extra models, `*.deleteable` trash, or sub-threshold  -> recorded only

Noise handling (see scan_model_inventory.py):
  * Models present on the other runner only as `<name>.deleteable` are treated
    as intentionally removed, not as "extra".
  * If the baseline inventory is missing/empty the comparison is skipped (WARN)
    instead of failing.
"""

import argparse
import glob
import json
import os
import sys

SOURCES = {
    "modelscope": "ModelScope",
    "huggingface": "HuggingFace",
}
# Baseline models smaller than this are ignored entirely (partial/stub copies).
DEFAULT_MIN_BASELINE_BYTES = 10 * 1024 ** 3  # 10 GiB


def fmt_size(n):
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PiB"


def is_ignored(source, meta, min_bytes):
    """True when a baseline entry must be skipped (stub / incomplete copy)."""
    if source == "huggingface":
        return not (meta or {}).get("complete", False)
    return ((meta or {}).get("size_bytes") or 0) < min_bytes


def load_reports(reports_dir):
    pattern = os.path.join(reports_dir, "**", "model_inventory_*.json")
    reports = []
    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(path, encoding="utf-8") as fp:
                report = json.load(fp)
            report["_file"] = path
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"WARN: 无法解析 {path}: {exc}", file=sys.stderr)
    return reports


def pick_baseline(reports, baseline_cluster):
    for r in reports:
        if r.get("cluster") == baseline_cluster:
            return r
    key = baseline_cluster.replace("-", "").lower()
    for r in reports:
        runner = (r.get("runner") or "").lower()
        if key and key in runner.replace("-", ""):
            return r
    return None


def main():
    parser = argparse.ArgumentParser(description="Compare model inventories")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--baseline-cluster", default="hk-001")
    parser.add_argument(
        "--sources",
        default="modelscope",
        help="逗号分隔的模型源,默认仅 modelscope (可选 modelscope,huggingface)",
    )
    parser.add_argument(
        "--min-model-bytes",
        type=int,
        default=DEFAULT_MIN_BASELINE_BYTES,
        help="基线小于该体积的模型直接忽略 (默认 10GiB)",
    )
    parser.add_argument(
        "--size-tolerance",
        type=float,
        default=0.01,
        help="Relative size mismatch tolerance before WARN (default 0.01 = 1%%)",
    )
    args = parser.parse_args()

    selected = []
    for name in args.sources.split(","):
        name = name.strip().lower()
        if name in SOURCES:
            selected.append((name, SOURCES[name]))
    if not selected:
        print(f"WARN: --sources '{args.sources}' 无法识别,跳过")
        return 0

    reports = load_reports(args.reports_dir)
    if not reports:
        print("WARN: 未找到任何 model_inventory_*.json,跳过比对")
        return 0

    baseline = pick_baseline(reports, args.baseline_cluster)
    if baseline is None:
        print(f"WARN: 未找到基线集群 {args.baseline_cluster} 的清单,跳过比对")
        return 0

    others = [r for r in reports if r is not baseline]
    base_name = baseline.get("runner") or baseline.get("cluster") or "baseline"

    hard_missing = []    # (source_label, model, runner)
    soft_size = []       # (source_label, model, runner, base_size, size)
    info_extra = []      # (source_label, model, runner, is_stub)
    info_trash = []      # (source_label, model, runner)
    info_base_stub = []  # (source_label, model, runner, base_size)
    summary_rows = []    # (source_label, base_usable, runner, n_missing, n_extra, n_size)
    baseline_empty = []

    for source, label in selected:
        base_models = baseline.get(source) or {}
        base_usable = {
            m: v for m, v in base_models.items() if not is_ignored(source, v, args.min_model_bytes)
        }
        if not base_models:
            baseline_empty.append(label)

        for other in others:
            runner = other.get("runner") or other.get("cluster") or "unknown"
            other_models = other.get(source) or {}
            other_trash = set((other.get("trash") or {}).get(source) or [])

            missing, trashed, base_stub = [], [], []
            for mid, meta in base_models.items():
                if mid in other_models:
                    continue
                if mid in other_trash:
                    trashed.append(mid)
                elif is_ignored(source, meta, args.min_model_bytes):
                    base_stub.append(mid)
                else:
                    missing.append((mid, (meta or {}).get("size_bytes"), (meta or {}).get("files")))

            extra = [m for m in other_models if m not in base_models]

            size_mismatch = []
            for mid in sorted(set(base_models) & set(other_models)):
                b = (base_models.get(mid) or {}).get("size_bytes")
                o = (other_models.get(mid) or {}).get("size_bytes")
                if not b or not o:
                    continue
                if is_ignored(source, base_models[mid], args.min_model_bytes) or is_ignored(
                    source, other_models[mid], args.min_model_bytes
                ):
                    continue
                if abs(b - o) / max(b, o) > args.size_tolerance:
                    size_mismatch.append((mid, b, o))

            for mid, bsize, bfiles in sorted(missing):
                hard_missing.append((label, mid, runner, bsize, bfiles))
            for mid in sorted(extra):
                info_extra.append(
                    (label, mid, runner, is_ignored(source, other_models[mid], args.min_model_bytes))
                )
            for mid in sorted(trashed):
                info_trash.append((label, mid, runner))
            for mid in sorted(base_stub):
                info_base_stub.append(
                    (label, mid, runner, (base_models.get(mid) or {}).get("size_bytes"))
                )
            for mid, b, o in sorted(size_mismatch):
                soft_size.append((label, mid, runner, b, o))

            summary_rows.append(
                (label, len(base_usable), runner, len(missing), len(extra), len(size_mismatch))
            )

    lines = [
        "## 🧩 Runner 模型缓存一致性比对 (Model Cache Consistency)",
        "",
        f"> **基线 (baseline)**: `{base_name}` / `{baseline.get('cluster', '')}` "
        f"| **模型源**: `{','.join(l for _, l in selected)}` "
        f"| **基线忽略阈值**: `<{fmt_size(args.min_model_bytes)}` "
        f"| **对比 runner 数**: `{len(others)}`",
        f"> **判定**: 可用模型缺失=硬失败, 体积不一致=WARN, 回收站/多出/阈值以下=信息项",
        "",
        "| 模型源 | 基线可用模型数 | 对比 Runner | 缺失 | 多出 | 体积不一致 | 结论 |",
        "|---|---|---|---|---|---|---|",
    ]
    for source_label, base_usable, runner, n_missing, n_extra, n_size in summary_rows:
        status = "✅ 一致" if n_missing == 0 else "❌ 缺失"
        if n_missing == 0 and n_size > 0:
            status = "⚠️ 体积差异"
        lines.append(
            f"| {source_label} | {base_usable} | `{runner}` | {n_missing} | {n_extra} | {n_size} | {status} |"
        )
    lines.append("")

    if baseline_empty:
        lines.append(
            f"> ⚠️ 基线在以下模型源上没有采集到任何模型,已跳过相关比对: {', '.join(baseline_empty)}\n"
        )

    if hard_missing:
        lines.extend(
            [
                "### ❌ 基线存在且可用、但在其他 runner 上缺失的模型",
                "",
                "| 模型源 | 模型 | 缺失于 Runner | 基线大小 | 基线文件数 |",
                "|---|---|---|---|---|",
            ]
        )
        for source_label, mid, runner, bsize, bfiles in hard_missing:
            lines.append(
                f"| {source_label} | `{mid}` | `{runner}` | {fmt_size(bsize)} | {bfiles} |"
            )
        lines.append("")
    else:
        lines.append("> ✅ **基线可用模型在对比 runner 上均存在,无缺失。**\n")

    if soft_size:
        lines.extend(
            [
                "### ⚠️ 同名模型体积不一致 (WARN,不阻塞)",
                "",
                "| 模型源 | 模型 | Runner | 基线大小 | 该 Runner 大小 |",
                "|---|---|---|---|---|",
            ]
        )
        for source_label, mid, runner, b, o in soft_size:
            lines.append(
                f"| {source_label} | `{mid}` | `{runner}` | {fmt_size(b)} | {fmt_size(o)} |"
            )
        lines.append("")

    if info_trash:
        lines.extend(
            [
                "### ♻️ 已在对比 runner 标记删除 (`*.deleteable`,信息项)",
                "",
                "| 模型源 | 模型 | Runner |",
                "|---|---|---|",
            ]
        )
        for source_label, mid, runner in info_trash:
            lines.append(f"| {source_label} | `{mid}` | `{runner}` |")
        lines.append("")

    if info_base_stub:
        ignored_runners = {}
        for _label, _mid, runner, _size in info_base_stub:
            ignored_runners[runner] = ignored_runners.get(runner, 0) + 1
        detail = ", ".join(f"`{r}`={n}" for r, n in sorted(ignored_runners.items()))
        lines.append(
            f"> 🪶 基线小于阈值 `<{fmt_size(args.min_model_bytes)}` 的模型已直接忽略"
            f"(不参与缺失判定): {detail}\n"
        )

    if info_extra:
        lines.extend(
            [
                "### ℹ️ 非基线 runner 多出的模型 (仅记录)",
                "",
                "| 模型源 | 模型 | Runner | 备注 |",
                "|---|---|---|---|",
            ]
        )
        for source_label, mid, runner, stub in info_extra:
            lines.append(
                f"| {source_label} | `{mid}` | `{runner}` | {'stub/未完成' if stub else '完整'} |"
            )
        lines.append("")

    summary_text = "\n".join(lines)
    print(summary_text)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as fp:
                fp.write(summary_text + "\n")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: 写入 GITHUB_STEP_SUMMARY 失败: {exc}", file=sys.stderr)

    if hard_missing:
        print(f"\nFAIL: 共发现 {len(hard_missing)} 个基线可用模型在其他 runner 上缺失")
        return 1
    print("\nPASS: 基线可用模型在其他 runner 上无缺失")
    return 0


if __name__ == "__main__":
    sys.exit(main())

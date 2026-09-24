#!/usr/bin/env python3
"""
Compare ModelScope / HuggingFace model inventories across runners.

Baseline defaults to cluster `hk-001` (the amd64 CPU runner). For every other
runner it reports models that exist in the baseline but are MISSING there, plus
models that are EXTRA (informational only).

Assertion grading (see AGENTS.md):
  * HARD : a *usable* baseline model missing on another runner   -> exit 1
  * SOFT : same model id, size differs beyond tolerance          -> WARN
  * INFO : extra models, `*.deleteable` trash, or baseline stubs -> recorded only

Noise handling (see scan_model_inventory.py):
  * Baseline entries flagged as stubs (HF without snapshots / ModelScope below
    the size threshold) are NOT required on other runners — they only mean the
    baseline copy itself is incomplete.
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

SOURCES = [("modelscope", "ModelScope"), ("huggingface", "HuggingFace")]


def fmt_size(n):
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PiB"


def is_stub(source, meta):
    if source == "huggingface":
        return not (meta or {}).get("complete", False)
    return bool((meta or {}).get("stub", False))


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
        "--size-tolerance",
        type=float,
        default=0.01,
        help="Relative size mismatch tolerance before WARN (default 0.01 = 1%%)",
    )
    args = parser.parse_args()

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

    for source, label in SOURCES:
        base_models = baseline.get(source) or {}
        base_usable = {m: v for m, v in base_models.items() if not is_stub(source, v)}
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
                elif is_stub(source, meta):
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
                if is_stub(source, base_models[mid]) or is_stub(source, other_models[mid]):
                    continue
                if abs(b - o) / max(b, o) > args.size_tolerance:
                    size_mismatch.append((mid, b, o))

            for mid, bsize, bfiles in sorted(missing):
                hard_missing.append((label, mid, runner, bsize, bfiles))
            for mid in sorted(extra):
                info_extra.append((label, mid, runner, is_stub(source, other_models[mid])))
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
        f"| **对比 runner 数**: `{len(others)}` "
        f"| **判定**: 可用模型缺失=硬失败, 体积不一致=WARN, 回收站/多出/基线残缺=信息项",
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
        lines.extend(
            [
                "### 🪶 基线自身为 stub/未完整下载,不作为缺失判定 (信息项)",
                "",
                "| 模型源 | 模型 | 缺失于 Runner | 基线大小 |",
                "|---|---|---|---|",
            ]
        )
        for source_label, mid, runner, size in info_base_stub:
            lines.append(f"| {source_label} | `{mid}` | `{runner}` | {fmt_size(size)} |")
        lines.append("")

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

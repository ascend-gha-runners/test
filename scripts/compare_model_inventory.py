#!/usr/bin/env python3
"""
Compare ModelScope / HuggingFace model inventories across runners.

Baseline defaults to cluster `hk-001` (the amd64 CPU runner). For every other
runner it reports models that exist in the baseline but are MISSING there, plus
models that are EXTRA (informational only).

Assertion grading (see AGENTS.md):
  * HARD : a baseline model missing on another runner  -> exit 1
  * SOFT : same model id, different size               -> WARN, does not fail
  * INFO : extra models on a non-baseline runner        -> recorded, no failure

If the baseline inventory is missing/empty the comparison is skipped (WARN)
instead of failing, so a transient cache-mount problem is not reported as a
model-missing regression.
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

    hard_missing = []   # (source_label, model, runner_label)
    soft_size = []      # (source_label, model, runner_label, base_size, size)
    info_extra = []     # (source_label, model, runner_label)
    summary_rows = []   # (source_label, base_count, runner_label, n_missing, n_extra, n_sizemismatch)
    baseline_empty = []

    for source, label in SOURCES:
        base_models = baseline.get(source) or {}
        if not base_models:
            baseline_empty.append(label)
        base_ids = set(base_models)

        for other in others:
            runner = other.get("runner") or other.get("cluster") or "unknown"
            other_models = other.get(source) or {}
            ids = set(other_models)
            missing = sorted(base_ids - ids)
            extra = sorted(ids - base_ids)

            size_mismatch = []
            for mid in sorted(base_ids & ids):
                b = (base_models.get(mid) or {}).get("size_bytes")
                o = (other_models.get(mid) or {}).get("size_bytes")
                if b and o and b != o:
                    size_mismatch.append((mid, b, o))

            for mid in missing:
                hard_missing.append((label, mid, runner))
            for mid in extra:
                info_extra.append((label, mid, runner))
            for mid, b, o in size_mismatch:
                soft_size.append((label, mid, runner, b, o))

            summary_rows.append(
                (
                    label,
                    len(base_ids),
                    runner,
                    len(missing),
                    len(extra),
                    len(size_mismatch),
                )
            )

    lines = [
        "## 🧩 Runner 模型缓存一致性比对 (Model Cache Consistency)",
        "",
        f"> **基线 (baseline)**: `{base_name}` / `{baseline.get('cluster', '')}` "
        f"| **对比 runner 数**: `{len(others)}` "
        f"| **判定**: 缺失=硬断言失败, 体积不一致=WARN, 多出=信息项",
        "",
        "| 模型源 | 基线模型数 | 对比 Runner | 缺失 | 多出 | 体积不一致 | 结论 |",
        "|---|---|---|---|---|---|---|",
    ]
    for source_label, base_count, runner, n_missing, n_extra, n_size in summary_rows:
        status = "✅ 一致" if n_missing == 0 else "❌ 缺失"
        if n_missing == 0 and n_size > 0:
            status = "⚠️ 体积差异"
        lines.append(
            f"| {source_label} | {base_count} | `{runner}` | {n_missing} | {n_extra} | {n_size} | {status} |"
        )
    lines.append("")

    if baseline_empty:
        lines.append(
            f"> ⚠️ 基线在以下模型源上没有采集到任何模型,已跳过相关比对: {', '.join(baseline_empty)}\n"
        )

    if hard_missing:
        lines.extend(
            [
                "### ❌ 基线存在但在其他 runner 上缺失的模型",
                "",
                "| 模型源 | 模型 | 缺失于 Runner |",
                "|---|---|---|",
            ]
        )
        for source_label, mid, runner in hard_missing:
            lines.append(f"| {source_label} | `{mid}` | `{runner}` |")
        lines.append("")
    else:
        lines.append("> ✅ **所有基线模型在对比 runner 上均存在,无缺失。**\n")

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

    if info_extra:
        lines.extend(
            [
                "### ℹ️ 非基线 runner 多出的模型 (仅记录)",
                "",
                "| 模型源 | 模型 | Runner |",
                "|---|---|---|",
            ]
        )
        for source_label, mid, runner in info_extra:
            lines.append(f"| {source_label} | `{mid}` | `{runner}` |")
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
        print(f"\nFAIL: 共发现 {len(hard_missing)} 个基线模型在其他 runner 上缺失")
        return 1
    print("\nPASS: 基线模型在其他 runner 上无缺失")
    return 0


if __name__ == "__main__":
    sys.exit(main())

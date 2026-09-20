#!/usr/bin/env python3
"""
Aggregate Multi-Cluster PyTorch Index Scan Reports
Reads all scan_report_*.json files produced by cross-cluster runner jobs,
generates a unified summary table, and reports overall cache health.
"""

import argparse
import glob
import json
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Aggregate multi-cluster PEP 658 scan reports"
    )
    parser.add_argument(
        "--reports-dir",
        default=".",
        help="Directory containing downloaded report artifacts or json files",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    pattern = os.path.join(args.reports_dir, "**", "scan_report_*.json")
    json_files = glob.glob(pattern, recursive=True)

    if not json_files:
        # Also check root of reports-dir
        json_files = glob.glob(os.path.join(args.reports_dir, "scan_report_*.json"))

    if not json_files:
        print(f"Warning: No scan_report_*.json files found under {args.reports_dir}")
        sys.exit(0)

    reports = []
    for jf in sorted(json_files):
        try:
            with open(jf, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                reports.append(data)
        except Exception as e:
            print(f"Failed to load {jf}: {e}", file=sys.stderr)

    if not reports:
        print("No valid reports loaded.")
        sys.exit(0)

    total_clusters = len(reports)
    all_passed = True
    any_failures = []

    lines = [
        "## 🌐 跨集群 Nginx 缓存健康度总览 (Cross-Cluster PyTorch Cache Health)",
        "",
        f"> **被检集群总数**: `{total_clusters}` | **检测标准**: PEP 658 元数据 SHA-256 完整性 + 无污染破坏行 + RFC 822 规范",
        "",
        "| 集群 (Cluster) | Runner | 缓存源 (Index URL) | 总包数 | 含 PEP 658 元数据 | 校验文件数 | 通过 | 损坏/失败 | 状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for r in reports:
        c = r.get("cluster", "unknown")
        run = r.get("runner", "unknown")
        u = r.get("index_url", "")
        tp = r.get("total_packages", 0)
        pwm = r.get("packages_with_metadata", 0)
        tc = r.get("total_checked", 0)
        p = r.get("passed", 0)
        fl = r.get("failed", 0)
        status = "✅ 正常 (Healthy)" if fl == 0 else "❌ 异常 (Unhealthy)"
        if fl > 0:
            all_passed = False
            for f in r.get("failures", []):
                any_failures.append({
                    "cluster": c,
                    "runner": run,
                    "package": f.get("package", ""),
                    "wheel": f.get("wheel", ""),
                    "errors": f.get("errors", []),
                })
        lines.append(
            f"| **{c}** | `{run}` | `{u}` | {tp} | {pwm} | {tc} | {p} | {fl} | {status} |"
        )

    lines.append("")

    if any_failures:
        lines.extend([
            "### ❌ 跨集群损坏或异常详情",
            "",
            "| 集群 | Runner | Package | Wheel | 异常信息 |",
            "|---|---|---|---|---|",
        ])
        for af in any_failures:
            err_str = "<br>".join(af["errors"])
            lines.append(
                f"| **{af['cluster']}** | `{af['runner']}` | `{af['package']}` | `{af['wheel']}` | {err_str} |"
            )
        lines.append("")
    else:
        lines.append("> ✅ **所有已测集群的 Nginx PyTorch 缓存均处于健康状态，未发现任何元数据损坏或校验和不匹配。**\n")

    summary_text = "\n".join(lines)
    print(summary_text)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as fp:
                fp.write(summary_text + "\n")
        except Exception as e:
            print(f"Failed to write GITHUB_STEP_SUMMARY: {e}", file=sys.stderr)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()

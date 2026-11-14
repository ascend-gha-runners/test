#!/usr/bin/env python3
"""Select exactly one existing GitHub Actions runner per target cluster.

Prefer CPU to avoid consuming NPU capacity; fall back to the existing
NPU runner only in clusters without any configured CPU runner.
This selects CONFIGURED labels, not live runner availability.
"""
import argparse
import json
import sys
from pathlib import Path


def select_runners(config, cluster="all"):
    runners = config.get("runners", [])
    by_cluster = {}
    for runner in runners:
        name = runner.get("cluster")
        labels = runner.get("labels")
        if (not name or not isinstance(labels, list) or len(labels) < 2 or
                not all(isinstance(label, str) and label for label in labels) or
                runner.get("type") not in {"cpu", "npu"}):
            raise ValueError(f"Invalid runner declaration: {runner!r}")
        by_cluster.setdefault(name, []).append(runner)

    if not by_cluster:
        raise ValueError("No runners configured")

    if cluster != "all" and cluster not in by_cluster:
        raise ValueError(f"Unknown cluster: {cluster}")

    clusters = sorted(by_cluster) if cluster == "all" else [cluster]
    selected = []
    for name in clusters:
        runner = sorted(
            by_cluster[name],
            key=lambda r: (r["type"] != "cpu", r.get("name", ""), r["labels"]),
        )[0]
        selected.append({
            "cluster": name,
            "name": runner["name"],
            "type": runner["type"],
            "arch": runner.get("arch", ""),
            "labels": runner["labels"],
        })
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=".github/config/runners.json")
    parser.add_argument("--cluster", default="all")
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        selected = select_runners(config, args.cluster)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as err:
        print(f"Matrix selection failed: {err}", file=sys.stderr)
        return 1
    print(json.dumps({"include": selected}, ensure_ascii=False, separators=(",", ":")))
    print(
        f"Selected {len(selected)} clusters "
        f"({sum(r['type'] == 'cpu' for r in selected)} CPU, "
        f"{sum(r['type'] == 'npu' for r in selected)} NPU fallback)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

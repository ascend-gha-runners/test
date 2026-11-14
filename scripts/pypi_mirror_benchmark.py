#!/usr/bin/env python3
"""PyPI mirror stability and throughput probe from the current runner's egress.

Runs WITHOUT an HTTP proxy or pip cache. It does not measure Squid pod egress:
use a pod-local probe for that distinct network perspective.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

MIRRORS = {
    "Huawei": "https://repo.huaweicloud.com/repository/pypi/simple/",
    "Tencent": "https://mirrors.cloud.tencent.com/pypi/simple/",
    "Aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "Volcengine": "https://mirrors.volces.com/pypi/simple/",
    "TUNA": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "USTC": "https://pypi.mirrors.ustc.edu.cn/simple/",
    "SJTU": "https://mirror.sjtu.edu.cn/pypi/web/simple/",
    "NJU": "https://mirror.nju.edu.cn/pypi/web/simple/",
    "BFSU": "https://mirrors.bfsu.edu.cn/pypi/web/simple/",
    "Official": "https://pypi.org/simple/",
}
PACKAGES = {
    "small": ("asttokens", "asttokens-3.0.2-py3-none-any.whl", False),
    "medium": ("numpy", "numpy-2.3.4-cp312-cp312-", True),
    "large": ("pyarrow", "pyarrow-21.0.0-cp312-cp312-", True),
}
FIELDS = ("time_utc", "mirror", "size", "concurrency", "request", "ok", "rc",
          "http", "bytes", "connect_s", "ttfb_s", "total_s", "mib_s",
          "remote_ip", "effective_url", "sha256_ok", "error")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def curl(args, max_time, capture=True):
    command = ["curl", "--noproxy", "*", "--proxy", "", "--retry", "0",
               "--connect-timeout", "5", "--max-time", str(max_time),
               "--fail", "--location", "--silent", "--show-error", *args]
    return subprocess.run(command, capture_output=capture, text=capture,
                          timeout=max_time + 10, check=False)


def wheel_from_index(mirror, size):
    package, prefix, arm64 = PACKAGES[size]
    index = MIRRORS[mirror] + package + "/"
    try:
        response = curl([index], 20)
        if response.returncode:
            return None, "index_http_or_network_error"
        links = Links()
        links.feed(response.stdout)
        for href in links.links:
            filename = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
            if not (filename == prefix if not arm64 else
                    filename.startswith(prefix) and "aarch64" in filename and filename.endswith(".whl")):
                continue
            digest = parse_qs(urlsplit(href).fragment).get("sha256", [None])[0]
            if not digest or len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                return None, "index_sha256_not_advertised"
            return (urljoin(index, href).split("#", 1)[0], digest.lower(), filename), "ready"
        return None, "wheel_not_in_index"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None, "index_timeout_or_parse_error"


def download(mirror, size, concurrency, number, url, sha, timeout):
    timestamp = datetime.now(timezone.utc).isoformat()
    record = dict(time_utc=timestamp, mirror=mirror, size=size,
                  concurrency=concurrency, request=number, ok=False,
                  rc=-1, http="000", bytes=0, connect_s=0.0, ttfb_s=0.0,
                  total_s=0.0, mib_s=0.0, remote_ip="", effective_url="",
                  sha256_ok=False, error="")
    fd, path = tempfile.mkstemp(prefix="pypi-bench-")
    os.close(fd)
    start = time.monotonic()
    try:
        fmt = "%{http_code}|%{size_download}|%{time_connect}|%{time_starttransfer}|%{time_total}|%{speed_download}|%{remote_ip}|%{url_effective}"
        res = curl(["--output", path, "--write-out", fmt, url], timeout)
        record["rc"] = res.returncode
        record["error"] = res.stderr.strip()[-240:]
        parts = res.stdout.strip().split("|")
        if len(parts) == 8:
            (record["http"], count, connect, ttfb, total, speed,
             record["remote_ip"], record["effective_url"]) = parts
            record.update(bytes=int(float(count)), connect_s=float(connect),
                          ttfb_s=float(ttfb), total_s=float(total),
                          mib_s=float(speed) / 1048576)
        if res.returncode == 0 and record["http"] == "200":
            hasher = hashlib.sha256()
            with open(path, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            record["sha256_ok"] = hasher.hexdigest() == sha
            record["ok"] = record["sha256_ok"]
            if not record["sha256_ok"]:
                record["error"] = "sha256_mismatch"
    except (ValueError, OSError, subprocess.TimeoutExpired) as err:
        record["error"] = str(err)[-240:]
        record["total_s"] = round(time.monotonic() - start, 3)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return record


def p90(values):
    values = sorted(values)
    return values[max(0, math.ceil(0.9 * len(values)) - 1)] if values else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirrors", default=",".join(MIRRORS))
    parser.add_argument("--sizes", default="small")
    parser.add_argument("--concurrency", default="1")
    parser.add_argument("--requests", type=int, default=3,
                        help="Total requests per mirror/size/concurrency, NOT per worker")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", default="benchmark-results")
    args = parser.parse_args()
    mirrors = [v.strip() for v in args.mirrors.split(",") if v.strip()]
    sizes = [v.strip() for v in args.sizes.split(",") if v.strip()]
    levels = [int(v) for v in args.concurrency.split(",")]
    if (args.requests < 1 or args.requests > 32 or
            args.timeout < 5 or args.timeout > 180 or
            not levels or any(v < 1 or v > 8 for v in levels) or
            args.requests < max(levels)):
        parser.error("requests must be >= max concurrency (<=8) and <=32; timeout 5..180")
    if not mirrors or any(v not in MIRRORS for v in mirrors):
        parser.error("Invalid mirror name")
    if not sizes or any(v not in PACKAGES for v in sizes):
        parser.error("Invalid size")

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    selected, probes, hashes = {}, [], {}
    # Discover every file via the mirror's own PEP 503 index. Do not assume
    # official PyPI /packages paths exist on other mirrors.
    for size in sizes:
        for mirror in mirrors:
            wheel, status = wheel_from_index(mirror, size)
            if wheel:
                url, digest, filename = wheel
                if size in hashes and hashes[size] != digest:
                    status = "sha256_differs_across_mirrors"
                else:
                    hashes[size] = digest
                    selected[(mirror, size)] = (url, digest)
            probes.append(dict(mirror=mirror, size=size, status=status))
            print(f"INDEX {mirror:10s} {size:6s} {status}", flush=True)

    rows, groups = [], []
    with (out / "samples.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for size in sizes:
            for level in levels:
                for mirror in mirrors:
                    key = mirror, size
                    if key not in selected:
                        continue
                    url, digest = selected[key]
                    start = time.monotonic()
                    with ThreadPoolExecutor(max_workers=level) as pool:
                        future_map = {
                            pool.submit(download, mirror, size, level, n, url, digest, args.timeout): n
                            for n in range(1, args.requests + 1)
                        }
                        samples = [future.result() for future in as_completed(future_map)]
                    elapsed = time.monotonic() - start
                    for sample in samples:
                        writer.writerow(sample)
                        rows.append(sample)
                    f.flush()
                    success = [v for v in samples if v["ok"]]
                    good_bytes = sum(v["bytes"] for v in success)
                    summary = dict(
                        mirror=mirror, size=size, concurrency=level,
                        attempted=len(samples), passed=len(success),
                        failed=len(samples) - len(success),
                        success_rate=round(len(success) / len(samples) * 100, 2),
                        p90_all_s=round(p90([v["total_s"] for v in samples]), 3),
                        median_success_s=round(statistics.median([v["total_s"] for v in success]), 3) if success else None,
                        median_success_mib_s=round(statistics.median([v["mib_s"] for v in success]), 3) if success else None,
                        aggregate_good_mib_s=round(good_bytes / elapsed / 1048576, 3),
                        wall_s=round(elapsed, 3),
                    )
                    groups.append(summary)
                    print(f"{size:6s} c={level} {mirror:10s} {summary['passed']}/{summary['attempted']} "
                          f"P90={summary['p90_all_s']}s good={summary['aggregate_good_mib_s']} MiB/s",
                          flush=True)

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "perspective": "runner-direct-egress",
        "cluster": os.environ.get("BENCH_CLUSTER", "unspecified"),
        "runner": os.environ.get("RUNNER_NAME", ""),
        "configuration": dict(mirrors=mirrors, sizes=sizes, concurrency=levels,
                              requests=args.requests, timeout_s=args.timeout),
        "index_probes": probes,
        "groups": groups,
    }
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    markdown = ["## PyPI 镜像质量（Runner 直连，不经过 Squid）", "",
                "| 镜像 | Wheel | 并发 | 成功 | P90 全请求 | 成功中位速率 | 有效总吞吐 |",
                "|---|---|---:|---:|---:|---:|---:|"]
    for g in groups:
        median = g["median_success_mib_s"]
        markdown.append(f"| {g['mirror']} | {g['size']} | {g['concurrency']} | "
                        f"{g['passed']}/{g['attempted']} | {g['p90_all_s']}s | "
                        f"{median if median is not None else 'N/A'} MiB/s | "
                        f"{g['aggregate_good_mib_s']} MiB/s |")
    for item in probes:
        if item["status"] != "ready":
            markdown.append(f"\n- 未验证：{item['mirror']}/{item['size']}: {item['status']}")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as stream:
            stream.write("\n".join(markdown) + "\n")
    print("\n".join(markdown))
    return 0  # External mirror failures are measurements, not runner E2E failures.


if __name__ == "__main__":
    raise SystemExit(main())

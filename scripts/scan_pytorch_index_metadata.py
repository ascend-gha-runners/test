#!/usr/bin/env python3
"""
E2E Comprehensive PyTorch Index PEP 658 Metadata Scanner
Scans ALL packages in PyTorch Simple Index (proxied via nginx-pypi-cache or upstream)
to detect hash mismatches, corrupted metadata trailers (e.g., appended Checksum-SHA256),
and stale cache entries across all HTTP encoding variants.
"""

import argparse
import concurrent.futures
import email
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Dict, List, Optional, Tuple


def log(msg: str, level: str = "INFO"):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scan PyTorch Simple Index for PEP 658 metadata integrity"
    )
    parser.add_argument(
        "--index-url",
        default="http://cache-service.nginx-pypi-cache.svc.cluster.local/whl/cpu",
        help="PyTorch simple repository root index URL",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=16,
        help="Number of concurrent scanner threads (default: 16)",
    )
    parser.add_argument(
        "--sample-per-pkg",
        type=int,
        default=5,
        help="Max number of wheels with metadata to sample per package (default: 5, 0 = all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan ALL wheels with metadata for all packages (no sampling limit)",
    )
    parser.add_argument(
        "--package",
        default="",
        help="Optional: comma-separated list of packages to scan (default: all packages)",
    )
    parser.add_argument(
        "--check-encodings",
        action="store_true",
        help="Check multiple Accept-Encoding headers (default, gzip) to detect stale cache",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP request timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to output results in JSON format",
    )
    return parser.parse_args()


class MetadataVerifier:
    def __init__(self, base_url: str, timeout: int = 20, check_encodings: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.check_encodings = check_encodings
        parsed = urllib.parse.urlparse(self.base_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.encodings = (
            ["", "gzip"] if check_encodings else [""]
        )

    def fetch_url(
        self, url: str, accept_encoding: str = ""
    ) -> Tuple[int, bytes, Dict[str, str], Optional[str]]:
        headers = {
            "User-Agent": "pip/24.0 (ascend-ci-pep658-full-scanner)",
        }
        if accept_encoding:
            headers["Accept-Encoding"] = accept_encoding

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.getheaders()}

                # Auto decompress if compressed
                ce = resp_headers.get("content-encoding", "").lower()
                if "gzip" in ce or data.startswith(b"\x1f\x8b"):
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                elif "deflate" in ce:
                    try:
                        data = zlib.decompress(data)
                    except Exception:
                        try:
                            data = zlib.decompress(data, -zlib.MAX_WBITS)
                        except Exception:
                            pass

                return resp.status, data, resp_headers, None
        except urllib.error.HTTPError as e:
            return e.code, b"", {}, str(e)
        except Exception as e:
            return 0, b"", {}, str(e)

    def discover_packages(self) -> List[str]:
        root_url = self.base_url + "/"
        log(f"Discovering packages from root index: {root_url}")
        status, data, headers, err = self.fetch_url(root_url)
        if status != 200:
            raise RuntimeError(f"Failed to fetch root index {root_url}: status={status}, err={err}")

        html_text = data.decode("utf-8", errors="ignore")
        links = re.findall(r"""href=["']([^"']+)["']""", html_text, re.IGNORECASE)
        packages = []
        for l in links:
            p = l.strip("/").split("#")[0].split("?")[0]
            # Ignore absolute urls, parent navigation, or empty
            if not p or p.startswith("..") or p.startswith("http://") or p.startswith("https://"):
                continue
            if "/" in p:
                continue
            packages.append(p)

        packages = sorted(list(set(packages)))
        log(f"Discovered {len(packages)} packages in index")
        return packages

    def scan_package(
        self, package: str, sample_limit: int = 0
    ) -> Tuple[str, List[Dict], List[str]]:
        pkg_url = f"{self.base_url}/{package}/"
        status, data, headers, err = self.fetch_url(pkg_url)
        if status != 200:
            return package, [], [f"Failed to fetch package page: status={status}, error={err}"]

        html_text = data.decode("utf-8", errors="ignore")

        # Parse anchor tags for wheel releases
        link_pattern = re.compile(
            r"""<a\s+([^>]*?)href=["']([^"']+)["']([^>]*?)>([^<]*)</a>""",
            re.IGNORECASE,
        )

        wheels = []
        for match in link_pattern.finditer(html_text):
            attrs_before, href, attrs_after, text = match.groups()
            all_attrs = f"{attrs_before} {attrs_after}"

            # Only examine wheel files
            raw_href = href.split("#")[0].split("?")[0]
            if not raw_href.endswith(".whl"):
                continue

            # Look for PEP 658 core-metadata attribute
            meta_match = re.search(
                r"""data-(?:dist-info-metadata|core-metadata)=["']([^"']+)["']""",
                all_attrs,
                re.IGNORECASE,
            )
            if not meta_match:
                continue

            meta_attr = meta_match.group(1).strip()
            expected_hash = None
            if meta_attr.startswith("sha256="):
                expected_hash = meta_attr.split("sha256=")[1].split(",")[0].strip()

            # Ensure metadata request is routed through target index origin so nginx cache is exercised
            clean_href = href.split("#")[0].split("?")[0]
            if "/whl/" in clean_href:
                whl_path = clean_href[clean_href.find("/whl/"):]
                clean_wheel_url = f"{self.origin}{whl_path}"
            elif clean_href.startswith("http://") or clean_href.startswith("https://"):
                clean_wheel_url = clean_href
            elif clean_href.startswith("/"):
                clean_wheel_url = f"{self.origin}{clean_href}"
            else:
                clean_wheel_url = urllib.parse.urljoin(pkg_url, clean_href)

            metadata_url = f"{clean_wheel_url}.metadata"
            wheel_filename = os.path.basename(clean_wheel_url)

            wheels.append({
                "wheel": wheel_filename,
                "wheel_url": clean_wheel_url,
                "metadata_url": metadata_url,
                "expected_hash": expected_hash,
            })

        # Apply sampling limit (take latest N wheels, wheels typically sorted chronologically)
        if sample_limit > 0 and len(wheels) > sample_limit:
            sampled_wheels = wheels[-sample_limit:]
        else:
            sampled_wheels = wheels

        return package, sampled_wheels, []

    def verify_single_metadata(self, item: Dict) -> Dict:
        metadata_url = item["metadata_url"]
        wheel = item["wheel"]
        expected_hash = item["expected_hash"]
        errors = []
        metadata_content_sample = ""
        cached_status = ""

        for enc in self.encodings:
            status, data, headers, err = self.fetch_url(metadata_url, accept_encoding=enc)
            if status != 200:
                errors.append(
                    f"HTTP {status} fetching metadata (encoding={enc or 'none'}): {err}"
                )
                continue

            cached_status = headers.get("x-pypi-cache", headers.get("cf-cache-status", "N/A"))
            computed_sha = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="ignore")
            metadata_content_sample = text[:200].replace("\n", " ")

            # 1. SHA-256 Checksum Verification
            if expected_hash and computed_sha.lower() != expected_hash.lower():
                errors.append(
                    f"Hash mismatch (encoding={enc or 'none'}): expected {expected_hash}, got {computed_sha}"
                )

            # 2. Corrupted Trailer Detection (pytorch/pytorch#197552)
            if "Checksum-SHA256:" in text:
                errors.append(
                    f"Corrupted metadata trailer (encoding={enc or 'none'}): contains appended 'Checksum-SHA256:' line"
                )

            # 3. RFC 822 Format & Required Headers Check
            if "Metadata-Version:" not in text:
                errors.append(
                    f"Invalid metadata (encoding={enc or 'none'}): missing 'Metadata-Version:' header"
                )

            try:
                msg = email.message_from_string(text)
                if not msg.get("Metadata-Version") or not msg.get("Name"):
                    errors.append(
                        f"RFC 822 format violation: missing Metadata-Version or Name in parsed headers"
                    )
            except Exception as e:
                errors.append(f"RFC 822 email parsing error: {e}")

        return {
            "wheel": wheel,
            "metadata_url": metadata_url,
            "expected_hash": expected_hash,
            "cache_status": cached_status,
            "passed": len(errors) == 0,
            "errors": errors,
            "sample": metadata_content_sample,
        }


def main():
    args = parse_args()
    log("=" * 70)
    log("PyTorch Simple Index PEP 658 Comprehensive Metadata Scanner")
    log(f"Index URL: {args.index_url}")
    log(f"Concurrency: {args.concurrency}")
    log(f"Check Encodings: {args.check_encodings}")
    sample_limit = 0 if args.all else args.sample_per_pkg
    log(f"Sample Limit per Package: {'ALL' if sample_limit == 0 else sample_limit}")
    log("=" * 70)

    start_time = time.time()
    verifier = MetadataVerifier(
        base_url=args.index_url,
        timeout=args.timeout,
        check_encodings=args.check_encodings,
    )

    if args.package:
        packages = [p.strip() for p in args.package.split(",") if p.strip()]
        log(f"Scanning specified packages: {packages}")
    else:
        try:
            packages = verifier.discover_packages()
        except Exception as e:
            log(f"Failed to discover packages: {e}", "ERROR")
            sys.exit(2)

    total_pkgs = len(packages)
    all_wheels_to_verify = []
    pkgs_with_metadata = 0
    package_scan_errors = []

    log(f"Discovering wheel metadata targets across {total_pkgs} packages (workers={args.concurrency})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_to_pkg = {
            executor.submit(verifier.scan_package, p, sample_limit): p for p in packages
        }
        for future in concurrent.futures.as_completed(future_to_pkg):
            pkg, wheels, errs = future.result()
            if errs:
                package_scan_errors.extend([f"[{pkg}] {e}" for e in errs])
            if wheels:
                pkgs_with_metadata += 1
                for w in wheels:
                    w["package"] = pkg
                    all_wheels_to_verify.append(w)

    log("Package discovery complete:")
    log(f"  - Total Packages in Index: {total_pkgs}")
    log(f"  - Packages Advertising PEP 658 Metadata: {pkgs_with_metadata}")
    log(f"  - Total Wheel Metadata Targets Selected: {len(all_wheels_to_verify)}")

    if package_scan_errors:
        log(f"Warning: {len(package_scan_errors)} errors encountered during package index fetch:", "WARN")
        for err in package_scan_errors[:5]:
            log(f"  {err}", "WARN")

    if not all_wheels_to_verify:
        log("No PEP 658 metadata files found to verify.", "WARN")
        sys.exit(0)

    log(f"Verifying {len(all_wheels_to_verify)} metadata files concurrently...")
    results = []
    failed_items = []
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_to_item = {
            executor.submit(verifier.verify_single_metadata, item): item
            for item in all_wheels_to_verify
        }
        for future in concurrent.futures.as_completed(future_to_item):
            res = future.result()
            results.append(res)
            completed += 1
            if not res["passed"]:
                failed_items.append(res)
                log(f"[FAIL] {res['wheel']}: {'; '.join(res['errors'])}", "ERROR")
            elif completed % 25 == 0 or completed == len(all_wheels_to_verify):
                log(f"Progress: {completed}/{len(all_wheels_to_verify)} verified ({len(failed_items)} failed)")

    elapsed = time.time() - start_time
    log("=" * 70)
    log("SCAN SUMMARY REPORT")
    log("=" * 70)
    log(f"Elapsed Time: {elapsed:.2f}s")
    log(f"Total Packages Scanned: {total_pkgs}")
    log(f"Packages with PEP 658 Metadata: {pkgs_with_metadata}")
    log(f"Total Metadata Files Checked: {len(results)}")
    log(f"Passed: {len(results) - len(failed_items)}")
    log(f"Failed / Corrupted: {len(failed_items)}")

    if args.output_json:
        try:
            report_data = {
                "index_url": args.index_url,
                "timestamp": time.time(),
                "elapsed_seconds": elapsed,
                "total_packages": total_pkgs,
                "packages_with_metadata": pkgs_with_metadata,
                "total_checked": len(results),
                "passed": len(results) - len(failed_items),
                "failed": len(failed_items),
                "failures": failed_items,
            }
            with open(args.output_json, "w", encoding="utf-8") as fh:
                json.dump(report_data, fh, indent=2)
            log(f"JSON report saved to {args.output_json}")
        except Exception as e:
            log(f"Failed to write JSON output: {e}", "WARN")

    step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            status_icon = "✅ PASS" if len(failed_items) == 0 else "❌ FAIL"
            summary_lines = [
                f"### PyTorch Index PEP 658 Metadata Scan Report - {status_icon}",
                "",
                f"- **Index URL**: `{args.index_url}`",
                f"- **Total Packages Scanned**: `{total_pkgs}`",
                f"- **Packages with PEP 658 Metadata**: `{pkgs_with_metadata}`",
                f"- **Total Metadata Files Checked**: `{len(results)}`",
                f"- **Passed**: `{len(results) - len(failed_items)}`",
                f"- **Failed / Corrupted**: `{len(failed_items)}`",
                f"- **Elapsed Time**: `{elapsed:.2f}s`",
                "",
            ]
            if failed_items:
                summary_lines.extend([
                    "#### 损坏 / 异常详情",
                    "| Package | Wheel | Errors |",
                    "|---|---|---|",
                ])
                for f in failed_items:
                    pkg = f.get("package", "unknown")
                    wheel = f.get("wheel", "")
                    errs = "<br>".join(f.get("errors", []))
                    summary_lines.append(f"| {pkg} | {wheel} | {errs} |")
                summary_lines.append("")

            with open(step_summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            log(f"Failed to write GITHUB_STEP_SUMMARY: {e}", "WARN")

    if failed_items:
        log("-" * 70)
        log(f"FAILURE DETAILS ({len(failed_items)} items):", "ERROR")
        for f in failed_items:
            log(f"  • {f['wheel']} ({f['metadata_url']})", "ERROR")
            for err in f["errors"]:
                log(f"      - {err}", "ERROR")
        log("-" * 70)
        sys.exit(1)
    else:
        log(">>> ALL METADATA FILES PASSED VERIFICATION (No hash mismatches, no corruption) <<<")
        sys.exit(0)


if __name__ == "__main__":
    main()

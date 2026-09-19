#!/usr/bin/env python3
"""
PEP 658 Metadata Check & Integrity Verification Script
Checks PyTorch package simple index for PEP 658 metadata integrity:
1. Queries simple index HTML for package release links.
2. Extracts core-metadata hashes if advertised.
3. Fetches .metadata endpoint directly.
4. Validates metadata format (RFC 822, Metadata-Version).
5. Verifies SHA256 checksum against index hash (detecting HashMismatch issues like pytorch/pytorch#197552).
6. Inspects HTTP response headers (X-Cache-Tier, X-Pypi-Cache, Server, Content-Type).
"""

import argparse
import hashlib
import html
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Test PEP 658 Metadata Integrity")
    parser.add_argument(
        "--index-url",
        default="http://cache-service.nginx-pypi-cache.svc.cluster.local/whl/cpu",
        help="PyTorch simple repository index URL",
    )
    parser.add_argument(
        "--package",
        default="torch",
        help="Package name to verify in simple index",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=5,
        help="Number of wheel packages to sample and test",
    )
    parser.add_argument(
        "--test-pip-dry-run",
        action="store_true",
        help="Also execute pip install --dry-run check",
    )
    return parser.parse_args()


def get_system_info():
    log(f"System: {platform.system()} {platform.release()} ({platform.machine()})")
    log(f"Python: {platform.python_version()} at {sys.executable}")
    try:
        pip_ver = subprocess.check_output(
            [sys.executable, "-m", "pip", "--version"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        log(f"Pip: {pip_ver}")
    except Exception as e:
        log(f"Pip version check failed: {e}", "WARN")


def test_pep658_metadata(index_url, package, sample_count):
    base = index_url.rstrip("/")
    pkg_url = f"{base}/{package}/"
    log(f"Fetching index: {pkg_url}")

    req = urllib.request.Request(
        pkg_url,
        headers={"User-Agent": "pip/24.0 (ascend-ci-e2e-pep658-test)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            headers = dict(resp.getheaders())
            status = resp.status
    except Exception as e:
        log(f"Failed to fetch package index {pkg_url}: {e}", "ERROR")
        return False

    server = headers.get("server", "-")
    tier = headers.get("x-cache-tier", headers.get("X-Cache-Tier", "none"))
    pypi_cache = headers.get("x-pypi-cache", headers.get("X-Pypi-Cache", "none"))
    log(f"Index status: {status}, Server: {server}, X-Pypi-Cache: {pypi_cache}, X-Cache-Tier: {tier}")

    # Parse anchor tags
    link_pattern = re.compile(
        r"""<a\s+([^>]*?)href=["']([^"']+)["']([^>]*?)>([^<]*)</a>""",
        re.IGNORECASE,
    )

    wheels = []
    for match in link_pattern.finditer(content):
        attrs_before, href, attrs_after, text = match.groups()
        all_attrs = f"{attrs_before} {attrs_after}"

        if not href.endswith(".whl") and ".whl#" not in href:
            continue

        # Extract sha256 from URL fragment if present (#sha256=...)
        frag_sha256 = None
        if "#" in href:
            parts = href.split("#", 1)
            raw_url = parts[0]
            frag = parts[1]
            if frag.startswith("sha256="):
                frag_sha256 = frag[len("sha256=") :]
        else:
            raw_url = href

        # Extract core metadata hash if present (data-core-metadata="sha256=...")
        core_meta_match = re.search(
            r"""data-core-metadata=["']sha256=([a-f0-9]+)["']""",
            all_attrs,
            re.IGNORECASE,
        )
        core_meta_sha256 = core_meta_match.group(1) if core_meta_match else None

        # Build absolute wheel URL
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            full_whl_url = raw_url
        elif raw_url.startswith("/"):
            # absolute path from root
            prefix = base.split("/", 3)[0] + "//" + base.split("/")[2]
            full_whl_url = f"{prefix}{raw_url}"
        else:
            full_whl_url = f"{pkg_url}{raw_url}"

        wheels.append({
            "href": href,
            "url": full_whl_url,
            "filename": text.strip() or os.path.basename(raw_url),
            "fragment_sha256": frag_sha256,
            "core_meta_sha256": core_meta_sha256,
        })

    log(f"Found {len(wheels)} total wheel links for {package}")
    if not wheels:
        log("No wheels found in package index!", "ERROR")
        return False

    # Sample latest wheels
    samples = wheels[-sample_count:]
    log(f"Testing {len(samples)} sample wheels for PEP 658 metadata integrity...")

    passed = 0
    failed = 0

    for idx, whl in enumerate(samples, 1):
        whl_name = whl["filename"]
        meta_url = f"{whl['url']}.metadata"
        log(f"\n[{idx}/{len(samples)}] Checking metadata: {whl_name}")
        log(f"  Metadata URL: {meta_url}")

        m_req = urllib.request.Request(
            meta_url,
            headers={"User-Agent": "pip/24.0 (ascend-ci-e2e-pep658-test)"},
        )

        try:
            with urllib.request.urlopen(m_req, timeout=20) as m_resp:
                meta_bytes = m_resp.read()
                m_headers = dict(m_resp.getheaders())
                m_status = m_resp.status
        except urllib.error.HTTPError as he:
            if he.code == 404:
                # Some repositories do not host PEP 658 metadata for older releases
                log(f"  [WARN] Metadata returned HTTP 404 (not hosted for this wheel)", "WARN")
                continue
            else:
                log(f"  [FAIL] Metadata fetch error HTTP {he.code}: {he.reason}", "ERROR")
                failed += 1
                continue
        except Exception as e:
            log(f"  [FAIL] Metadata network error: {e}", "ERROR")
            failed += 1
            continue

        actual_sha256 = hashlib.sha256(meta_bytes).hexdigest()
        meta_text = meta_bytes.decode("utf-8", errors="ignore")
        size = len(meta_bytes)
        tier = m_headers.get("x-cache-tier", m_headers.get("X-Cache-Tier", "none"))
        pypi_cache = m_headers.get("x-pypi-cache", m_headers.get("X-Pypi-Cache", "none"))

        log(f"  Status: {m_status}, Size: {size} bytes, SHA256: {actual_sha256}")
        log(f"  X-Pypi-Cache: {pypi_cache}, X-Cache-Tier: {tier}, Content-Type: {m_headers.get('content-type', '-')}")

        # Check basic RFC 822 / Metadata-Version format
        if "Metadata-Version:" in meta_text or "Name:" in meta_text:
            log("  [PASS] Metadata RFC 822 format valid")
        else:
            log("  [FAIL] Metadata body does not contain valid Python package metadata headers!", "ERROR")
            log(f"  Preview: {meta_text[:200]}...")
            failed += 1
            continue

        # Check hash match if index provided data-core-metadata
        expected_meta_hash = whl["core_meta_sha256"]
        if expected_meta_hash:
            if actual_sha256.lower() == expected_meta_hash.lower():
                log(f"  [PASS] PEP 658 Checksum Matches! (sha256={actual_sha256})")
            else:
                log(
                    f"  [FAIL] PEP 658 Checksum Mismatch! "
                    f"Index expected: {expected_meta_hash}, Actual downloaded: {actual_sha256}",
                    "ERROR",
                )
                failed += 1
                continue
        else:
            log("  [INFO] Index did not specify data-core-metadata hash; payload successfully fetched and verified")

        passed += 1

    log(f"\nMetadata check summary: {passed} passed, {failed} failed")
    return failed == 0


def test_pip_resolution(index_url, package):
    log("\nTesting pip install --dry-run resolution...")
    # PyTorch 的 whl/cpu 页面极其庞大（上千个历史版本），不指定版本且 --no-cache-dir 时，
    # pip 会对所有版本进行回溯解析，耗时可能超过 3-5 分钟。
    # 我们指定一个确定的 cpu 版本（如 torch==2.4.0 或 torch）并加上 --timeout 60 和宽松的 subprocess timeout
    target_spec = package if "==" in package else f"{package}==2.4.0"
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--no-cache-dir",
        "--timeout",
        "30",
        "--index-url",
        index_url,
    ]
    # 若为 http 内部地址，追加 --trusted-host
    if "://" in index_url:
        host = index_url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
        cmd.extend(["--trusted-host", host])
    cmd.append(target_spec)
    log(f"Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            log("Pip dry-run succeeded without hash mismatch!")
            return True
        else:
            log(f"Pip dry-run failed with code {proc.returncode}:", "ERROR")
            print(proc.stderr)
            # Check specifically for HashMismatch
            if "HashMismatch" in proc.stderr or "DO NOT MATCH THE HASHES" in proc.stderr:
                log("CRITICAL: Detected HashMismatch during pip resolution!", "ERROR")
            return False
    except Exception as e:
        log(f"Pip dry-run execution error: {e}", "ERROR")
        return False


def main():
    args = parse_args()
    get_system_info()

    success = test_pep658_metadata(
        args.index_url,
        args.package,
        args.sample_count,
    )

    if args.test_pip_dry_run:
        pip_ok = test_pip_resolution(args.index_url, args.package)
        success = success and pip_ok

    if success:
        log("\n>>> ALL PEP 658 METADATA INTEGRITY CHECKS PASSED <<<")
        sys.exit(0)
    else:
        log("\n>>> PEP 658 METADATA INTEGRITY CHECKS FAILED <<<", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()

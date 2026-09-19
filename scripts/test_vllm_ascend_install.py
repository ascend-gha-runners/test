#!/usr/bin/env python3
"""
Simulate vllm-project/vllm-ascend CI Dependency Installation E2E Test
Simulates the package resolution and installation in:
https://github.com/vllm-project/vllm-ascend/actions/runs/35442022785/workflow

Differences & Requirements:
1. ALL repos point strictly to the cluster's internal nginx-pypi-cache service:
   - PyPI simple: http://{cache_host}/pypi/simple
   - Ascend repo: http://{cache_host}/ascend/repos/pypi/ (Huawei Cloud repo)
   - PyTorch CPU: http://{cache_host}/whl/cpu/ (PyTorch CPU index)
2. Cache is completely bypassed / ignored:
   - UV_NO_CACHE=1 and --no-cache
   - PIP_NO_CACHE_DIR=1 and --no-cache-dir
   - Isolated temporary cache directories so /root/.cache contents do not mask bugs.
3. Strict assertions:
   - Probes HTTP response headers (X-Pypi-Cache: HIT/MISS) from all 3 repos.
   - Asserts no local wheel cache hits ("Using cached" forbidden in pip output).
   - Verifies import and version info of installed packages.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Simulate vllm-ascend CI dependency install via nginx-pypi-cache")
    parser.add_argument(
        "--cache-host",
        default="cache-service.nginx-pypi-cache.svc.cluster.local",
        help="Internal nginx-pypi-cache hostname",
    )
    parser.add_argument(
        "--triton-version",
        default="3.2.2",
        help="Target triton-ascend version from ascend repo",
    )
    parser.add_argument(
        "--torch-version",
        default="2.4.0",
        help="Target torch base version from whl/cpu repo",
    )
    return parser.parse_args()


def check_endpoint(url, name):
    log(f"Probing {name} endpoint: {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pip/24.0 (ascend-ci-vllm-simulation-test)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = dict(resp.getheaders())
            status = resp.status
            pypi_cache = headers.get("x-pypi-cache", headers.get("X-Pypi-Cache", "none"))
            server = headers.get("server", "-")
            tier = headers.get("x-cache-tier", headers.get("X-Cache-Tier", "none"))
            log(f"  -> HTTP {status}, Server: {server}, X-Pypi-Cache: {pypi_cache}, X-Cache-Tier: {tier}")
            if status != 200:
                log(f"  [FAIL] {name} returned non-200 status: {status}", "ERROR")
                return False
            return True
    except Exception as e:
        log(f"  [FAIL] Failed connecting to {name} ({url}): {e}", "ERROR")
        return False


def run_cmd(cmd, env=None, check=True, desc=""):
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    log(f"\n>> Running [{desc}]: {cmd_str}")
    proc = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    output = proc.stdout
    print(output, flush=True)
    if check and proc.returncode != 0:
        log(f"Command failed with exit code {proc.returncode}", "ERROR")
        sys.exit(proc.returncode)
    return proc


def get_uv_cmd():
    # Prefer python -m uv if module exists, otherwise fallback to uv binary
    try:
        subprocess.check_call([sys.executable, "-m", "uv", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return [sys.executable, "-m", "uv"]
    except Exception:
        uv_path = shutil.which("uv") or "uv"
        return [uv_path]


def main():
    args = parse_args()
    cache_host = args.cache_host
    arch = platform.machine()
    log(f"Starting vLLM-Ascend CI installation simulation on {arch}...")
    log(f"Internal Cache Host: {cache_host}")

    pypi_url = f"http://{cache_host}/pypi/simple"
    ascend_url = f"http://{cache_host}/ascend/repos/pypi/"
    torch_url = f"http://{cache_host}/whl/cpu/"

    # Step 1: Probe endpoints
    log("\n=== Phase 1: Probing all 3 internal repo endpoints ===")
    ep_pypi = check_endpoint(pypi_url, "PyPI Simple")
    ep_ascend = check_endpoint(ascend_url, "Ascend Repo (repo.huaweicloud.com)")
    ep_torch = check_endpoint(torch_url, "PyTorch CPU Whl")

    if not (ep_pypi and ep_ascend and ep_torch):
        log("One or more repository endpoints failed connectivity probe!", "ERROR")
        sys.exit(1)
    log("[PASS] All 3 repository endpoints reachable with valid HTTP 200.")

    # Step 2: Create isolated cache sandbox to prevent reading /root/.cache
    log("\n=== Phase 2: Isolating cache environment (bypassing persistent /root/.cache) ===")
    sandbox_dir = tempfile.mkdtemp(prefix="vllm_sim_cache_")
    isolated_pip_cache = os.path.join(sandbox_dir, "pip")
    isolated_uv_cache = os.path.join(sandbox_dir, "uv")
    os.makedirs(isolated_pip_cache, exist_ok=True)
    os.makedirs(isolated_uv_cache, exist_ok=True)

    sim_env = os.environ.copy()
    sim_env["PIP_NO_CACHE_DIR"] = "1"
    sim_env["PIP_CACHE_DIR"] = isolated_pip_cache
    sim_env["UV_NO_CACHE"] = "1"
    sim_env["UV_CACHE_DIR"] = isolated_uv_cache
    sim_env["UV_SYSTEM_PYTHON"] = "1"
    sim_env["UV_INDEX_URL"] = pypi_url
    sim_env["UV_EXTRA_INDEX_URL"] = f"{ascend_url} {torch_url}"
    sim_env["UV_INDEX_STRATEGY"] = "unsafe-best-match"
    sim_env["UV_INSECURE_HOST"] = cache_host
    sim_env["UV_HTTP_TIMEOUT"] = "120"
    sim_env["MAX_JOBS"] = "4"
    sim_env["HF_HUB_OFFLINE"] = "1"

    log(f"Sandbox pip cache: {isolated_pip_cache}")
    log(f"Sandbox uv cache:  {isolated_uv_cache}")
    log(f"UV_INDEX_URL:       {sim_env['UV_INDEX_URL']}")
    log(f"UV_EXTRA_INDEX_URL: {sim_env['UV_EXTRA_INDEX_URL']}")

    # Step 3: Configure pip global config to internal cache
    log("\n=== Phase 3: Configuring pip to route exclusively through nginx-pypi-cache ===")
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.index-url", pypi_url],
        env=sim_env,
        desc="pip config index-url",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.extra-index-url", f"{ascend_url} {torch_url}"],
        env=sim_env,
        desc="pip config extra-index-url",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.trusted-host", cache_host],
        env=sim_env,
        desc="pip config trusted-host",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.no-cache-dir", "true"],
        env=sim_env,
        desc="pip config no-cache-dir",
    )

    # Step 4: pip install uv & uc-manager (simulating vllm-ascend workflow)
    log("\n=== Phase 4: Installing uv and uc-manager with --no-cache-dir ===")
    res_uv = run_cmd(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "uv"],
        env=sim_env,
        desc="pip install uv",
    )
    if "Using cached" in res_uv.stdout:
        log("[FAIL] pip used cached wheel for uv! Disk cache was not properly bypassed.", "ERROR")
        sys.exit(1)

    res_uc = run_cmd(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "uc-manager"],
        env=sim_env,
        desc="pip install uc-manager",
    )
    if "Using cached" in res_uc.stdout:
        log("[FAIL] pip used cached wheel for uc-manager! Disk cache was not properly bypassed.", "ERROR")
        sys.exit(1)

    uv_base_cmd = get_uv_cmd()
    run_cmd(uv_base_cmd + ["--version"], env=sim_env, desc="uv --version")

    # Step 5: Install triton-ascend==3.2.2 via uv (simulating vllm-ascend workflow)
    log("\n=== Phase 5: Installing triton-ascend from internal ascend repo ===")
    run_cmd(
        uv_base_cmd + [
            "pip", "install",
            "--no-cache",
            "--force-reinstall",
            "--no-deps",
            f"triton-ascend=={args.triton_version}",
        ],
        env=sim_env,
        desc="uv pip install triton-ascend",
    )

    # Step 6: Install PyTorch CPU via uv (simulating vllm-ascend workflow)
    log("\n=== Phase 6: Installing torch from internal whl/cpu repo ===")
    # whl/cpu has torch==2.4.0 (aarch64) or torch==2.4.0+cpu (x86_64).
    # Using range "torch>=2.4.0,<2.5.0" reliably resolves the platform wheel from whl/cpu
    torch_spec = f"torch>={args.torch_version},<{args.torch_version.rsplit('.', 1)[0]}.99"
    run_cmd(
        uv_base_cmd + [
            "pip", "install",
            "--no-cache",
            torch_spec,
        ],
        env=sim_env,
        desc=f"uv pip install {torch_spec}",
    )

    # Step 7: Verify Python imports
    log("\n=== Phase 7: Validating installed packages and imports ===")
    import_script = """
import torch
import triton_ascend
print(f"[PASS] torch imported successfully: version={torch.__version__}")
print(f"[PASS] triton_ascend imported successfully: version={triton_ascend.__version__}")
"""
    run_cmd(
        [sys.executable, "-c", import_script],
        env=sim_env,
        desc="Python import verification",
    )

    # Cleanup sandbox
    shutil.rmtree(sandbox_dir, ignore_errors=True)

    log("\n==========================================================================")
    log(">>> vLLM-Ascend CI Simulation (All Internal Repos + No Cache) PASSED! <<<")
    log("==========================================================================")


if __name__ == "__main__":
    main()

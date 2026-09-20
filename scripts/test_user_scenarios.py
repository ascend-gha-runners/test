#!/usr/bin/env python3
"""
Real End-User Scenario Test Suite for Ascend GHA Runners
Modeled directly on vllm-project/vllm-ascend CI workflows and Dockerfiles:
- .github/workflows/_selected_tests.yaml
- .github/workflows/_build_csrc_cache.yaml
- .github/workflows/_schedule_image_build.yaml
- Dockerfile / Dockerfile.nightly.a2

Constraints:
- Strictly NO bare curl probes as pass/fail criteria.
- Tests MUST execute actual package managers, toolchains, compilers, and dependencies.
- Enforce --no-cache-dir / UV_NO_CACHE=1 to prevent local disk cache from masking issues.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time


def log(msg: str, level: str = "INFO"):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{timestamp}] [{level}]"
    print(f"{prefix} {msg}", flush=True)


def run_cmd(cmd, env=None, check=True, desc="", cwd=None):
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    log(f">> Running [{desc}]: {cmd_str}")
    proc = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=cwd,
    )
    output = proc.stdout.strip()
    if output:
        # Print with indentation
        for line in output.splitlines():
            print(f"   | {line}", flush=True)

    if check and proc.returncode != 0:
        log(f"Command failed with exit code {proc.returncode} for step [{desc}]", "ERROR")
        raise RuntimeError(f"Step '{desc}' failed with code {proc.returncode}")
    return proc


def detect_os():
    """Detect OS distribution: ubuntu, openeuler, etc."""
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release") as f:
            content = f.read().lower()
            if "openeuler" in content:
                return "openeuler"
            elif "ubuntu" in content or "debian" in content:
                return "ubuntu"
            elif "centos" in content or "rhel" in content or "fedora" in content:
                return "openeuler"
    return "unknown"


# ==============================================================================
# Scenario 1: OS Package Manager (APT on Ubuntu, YUM/DNF on openEuler)
# ==============================================================================
def test_os_package_manager(cache_host: str):
    os_type = detect_os()
    log(f"=== Scenario 1: OS Package Manager Verification (Detected: {os_type}) ===")

    if os_type == "ubuntu":
        log(f"Configuring Ubuntu APT mirror to http://{cache_host}:8081 ...")
        # Exact pattern from vllm-ascend:
        # sed -Ei 's@(ports|archive).ubuntu.com@cache-service.nginx-pypi-cache.svc.cluster.local:8081@g' /etc/apt/sources.list
        run_cmd(
            f"sed -Ei 's@(ports|archive).ubuntu.com@{cache_host}:8081@g' /etc/apt/sources.list",
            desc="Rewrite /etc/apt/sources.list to port 8081",
        )
        run_cmd("apt-get update -y", desc="apt-get update via 8081")
        run_cmd("apt-get install -y --no-install-recommends zstd", desc="apt-get install zstd via 8081")
        run_cmd(["zstd", "--version"], desc="Verify installed zstd binary")
        log("[PASS] Ubuntu APT package installation via port 8081 succeeded.")

    elif os_type == "openeuler":
        log(f"Configuring openEuler YUM/DNF mirror to http://{cache_host}:8083 ...")
        # Exact pattern from vllm-ascend _build_csrc_cache.yaml:
        # sed -Ei 's@https?://[^/]+/(openeuler|centos|fedora)@http://cache-service...:8083/\1@g' /etc/yum.repos.d/*.repo
        run_cmd(
            f"sed -Ei 's@https?://[^/]+/(openeuler|centos|fedora)@http://{cache_host}:8083/\\1@g' /etc/yum.repos.d/*.repo",
            desc="Rewrite /etc/yum.repos.d/*.repo to port 8083",
        )
        run_cmd("dnf clean all", desc="dnf clean all")
        run_cmd("dnf makecache", desc="dnf makecache via 8083")
        run_cmd("dnf install -y zstd", desc="dnf install zstd via 8083")
        run_cmd(["zstd", "--version"], desc="Verify installed zstd binary")
        log("[PASS] openEuler YUM/DNF package installation via port 8083 succeeded.")

    else:
        log(f"[WARN] Unsupported OS type '{os_type}' for package manager test; skipping.", "WARN")


# ==============================================================================
# Scenario 2: PyPI + UV Real Installation (vLLM-Ascend core CI pattern)
# ==============================================================================
def test_pypi_and_uv_install(cache_host: str, triton_ver: str, torch_ver: str):
    log("=== Scenario 2: Realistic Python / UV Installation (vLLM-Ascend Core) ===")

    pypi_url = f"http://{cache_host}/pypi/simple"
    ascend_url = f"http://{cache_host}/ascend/repos/pypi"
    torch_url = f"http://{cache_host}/whl/cpu/"

    # Isolate local pip/uv caches completely
    sandbox_dir = tempfile.mkdtemp(prefix="vllm_user_sim_")
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

    # 1. Configure pip global config (exact vLLM CI pattern)
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.index-url", pypi_url],
        env=sim_env,
        desc="pip config set global.index-url",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.extra-index-url", f"{ascend_url} {torch_url}"],
        env=sim_env,
        desc="pip config set global.extra-index-url",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.trusted-host", cache_host],
        env=sim_env,
        desc="pip config set global.trusted-host",
    )
    run_cmd(
        [sys.executable, "-m", "pip", "config", "set", "global.no-cache-dir", "true"],
        env=sim_env,
        desc="pip config set global.no-cache-dir",
    )

    # 2. Install uv and uc-manager with pip
    res_uv = run_cmd(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "uv", "uc-manager"],
        env=sim_env,
        desc="pip install uv uc-manager (--no-cache-dir)",
    )
    if "Using cached" in res_uv.stdout:
        raise RuntimeError("pip used cached wheel! Disk cache was not properly bypassed.")

    # 3. Determine uv executable
    try:
        subprocess.check_call([sys.executable, "-m", "uv", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uv_cmd = [sys.executable, "-m", "uv"]
    except Exception:
        uv_cmd = [shutil.which("uv") or "uv"]

    run_cmd(uv_cmd + ["--version"], env=sim_env, desc="uv version check")

    # 4. Install triton-ascend from internal ascend repo via uv
    run_cmd(
        uv_cmd + [
            "pip", "install",
            "--no-cache",
            "--force-reinstall",
            "--no-deps",
            f"triton-ascend=={triton_ver}",
        ],
        env=sim_env,
        desc=f"uv pip install triton-ascend=={triton_ver} (internal ascend repo)",
    )

    # 5. Install torch from internal whl/cpu repo via uv
    torch_spec = f"torch>={torch_ver},<{torch_ver.rsplit('.', 1)[0]}.99"
    run_cmd(
        uv_cmd + [
            "pip", "install",
            "--no-cache",
            torch_spec,
        ],
        env=sim_env,
        desc=f"uv pip install {torch_spec} (internal whl/cpu repo)",
    )

    # 6. Real Python runtime import validation
    import_verify = """
import torch
print(f"torch imported successfully: version={torch.__version__}")

try:
    import triton
    print(f"triton imported successfully: version={getattr(triton, '__version__', 'unknown')}")
except Exception as e:
    print(f"triton runtime note: {e}")

import uc_manager
print("uc_manager imported successfully")
"""
    run_cmd([sys.executable, "-c", import_verify], env=sim_env, desc="Python runtime package imports")

    shutil.rmtree(sandbox_dir, ignore_errors=True)
    log("[PASS] Realistic PyPI + UV package installation and imports succeeded.")


# ==============================================================================
# Scenario 3: Rustup & Cargo with crates.io Sparse Index (Ports 8082 & 8085)
# ==============================================================================
def test_rust_toolchain_and_cargo_build(cache_host: str):
    log("=== Scenario 3: Rustup Toolchain (8082) & Cargo Build with Sparse Index (8085) ===")

    rustup_server = f"http://{cache_host}:8082/rustup"
    rustup_update = f"http://{cache_host}:8082/rustup/rustup"
    crates_index = f"http://{cache_host}:8085/index/"

    rust_env = os.environ.copy()
    rust_env["RUSTUP_DIST_SERVER"] = rustup_server
    rust_env["RUSTUP_UPDATE_ROOT"] = rustup_update

    cargo_home = os.path.expanduser("~/.cargo")
    os.makedirs(cargo_home, exist_ok=True)
    rust_env["CARGO_HOME"] = cargo_home
    rust_env["PATH"] = f"{cargo_home}/bin:" + rust_env.get("PATH", "")

    # 1. Bootstrap rustup from internal mirror (pattern from vllm-ascend/Dockerfile)
    installer_path = "/tmp/rustup-init.sh"
    run_cmd(
        f"curl -fsSL '{rustup_update}/rustup-init.sh' -o {installer_path}",
        env=rust_env,
        desc="Download rustup-init.sh from internal port 8082",
    )
    # Strip hardcoded https requirement since internal mirror is http
    run_cmd(
        f"sed -i \"s/--proto '=https'//g; s/--https-only//g\" {installer_path}",
        desc="Adjust rustup-init.sh for internal http mirror",
    )
    # Install minimal toolchain
    run_cmd(
        f"sh {installer_path} -y --default-toolchain minimal --profile minimal --no-modify-path",
        env=rust_env,
        desc="Install minimal Rust toolchain via internal 8082 mirror",
    )
    if os.path.exists(installer_path):
        os.remove(installer_path)

    run_cmd(["rustc", "--version"], env=rust_env, desc="rustc --version")
    run_cmd(["cargo", "--version"], env=rust_env, desc="cargo --version")

    # 2. Configure Cargo to use crates.io sparse index mirror on port 8085
    cargo_config = os.path.join(cargo_home, "config.toml")
    config_content = f"""[source.crates-io]
replace-with = "mirror"

[source.mirror]
registry = "sparse+{crates_index}"
"""
    with open(cargo_config, "w") as f:
        f.write(config_content)
    log(f"Configured {cargo_config} with sparse index on {crates_index}")

    # 3. Real user build: create a cargo project, add real dependency (serde), and compile!
    test_project_dir = tempfile.mkdtemp(prefix="vllm_cargo_test_")
    try:
        run_cmd(
            ["cargo", "init", "--bin", "vllm_rust_verify"],
            cwd=test_project_dir,
            env=rust_env,
            desc="cargo init --bin vllm_rust_verify",
        )
        # Add serde dependency (fetched through 8085 sparse index)
        run_cmd(
            ["cargo", "add", "serde", "--vers", "1.0.197"],
            cwd=test_project_dir,
            env=rust_env,
            desc="cargo add serde via 8085 sparse index",
        )
        # Modify main.rs to use serde to guarantee code compilation against the crate
        main_rs = os.path.join(test_project_dir, "src", "main.rs")
        with open(main_rs, "w") as f:
            f.write("""use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize, Debug)]
struct TestPayload {
    service: String,
    status: u32,
}

fn main() {
    let p = TestPayload {
        service: "nginx-pypi-cache-crates".to_string(),
        status: 200,
    };
    println!("[PASS] Rust binary executed with serde: {:?}", p);
}
""")
        # Build binary (downloads crate payload & compiles)
        run_cmd(
            ["cargo", "build"],
            cwd=test_project_dir,
            env=rust_env,
            desc="cargo build (fetching crate from 8085 and compiling)",
        )
        # Execute the built binary
        bin_path = os.path.join(test_project_dir, "target", "debug", "vllm_rust_verify")
        res = run_cmd([bin_path], env=rust_env, desc="Execute compiled Rust binary")
        if "[PASS]" not in res.stdout:
            raise RuntimeError(f"Rust binary output unexpected: {res.stdout}")

        log("[PASS] Rust toolchain install (8082) & Cargo sparse build (8085) succeeded.")
    finally:
        shutil.rmtree(test_project_dir, ignore_errors=True)


# ==============================================================================
# Scenario 4: Git Proxy / CDN
# ==============================================================================
def test_git_proxy():
    log("=== Scenario 4: Git Proxy & GitHub Mirror Verification ===")
    proxy_prefix = "https://gh-proxy.test.osinfra.cn/"

    # Configure insteadOf (pattern from vllm-ascend CI)
    run_cmd(
        f"git config --global url.\"{proxy_prefix}https://github.com/\".insteadOf https://github.com/",
        desc="Configure git insteadOf gh-proxy",
    )

    test_clone_dir = tempfile.mkdtemp(prefix="git_proxy_test_")
    try:
        run_cmd(
            ["git", "clone", "--depth", "1", "https://github.com/ascend-gha-runners/test.git", test_clone_dir],
            desc="git clone via gh-proxy",
        )
        run_cmd(["git", "log", "-1", "--oneline"], cwd=test_clone_dir, desc="git log verify")
        log("[PASS] Git clone via internal proxy succeeded.")
    finally:
        shutil.rmtree(test_clone_dir, ignore_errors=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Real End-User Scenario Test Suite for Ascend CI Runners")
    parser.add_argument(
        "--cache-host",
        default=os.environ.get("CACHE_HOST", "cache-service.nginx-pypi-cache.svc.cluster.local"),
        help="Internal cache service domain/host",
    )
    parser.add_argument(
        "--triton-version",
        default="3.2.2",
        help="Target triton-ascend package version",
    )
    parser.add_argument(
        "--torch-version",
        default="2.4.0",
        help="Target torch package version",
    )
    parser.add_argument(
        "--scenarios",
        default="all",
        help="Comma-separated scenarios: os_pkg,pypi_uv,rust,git, or all",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    cache_host = args.cache_host
    arch = platform.machine()
    log(f"Starting Realistic End-User CI Test Suite on {arch} ({detect_os()})...")
    log(f"Cache Host: {cache_host}")

    selected_scenarios = [s.strip() for s in args.scenarios.split(",")]
    run_all = "all" in selected_scenarios

    failures = []

    # 1. OS Package Manager
    if run_all or "os_pkg" in selected_scenarios:
        try:
            test_os_package_manager(cache_host)
        except Exception as e:
            log(f"[FAIL] OS Package Manager scenario failed: {e}", "ERROR")
            failures.append("os_package_manager")

    # 2. PyPI + UV
    if run_all or "pypi_uv" in selected_scenarios:
        try:
            test_pypi_and_uv_install(cache_host, args.triton_version, args.torch_version)
        except Exception as e:
            log(f"[FAIL] PyPI + UV scenario failed: {e}", "ERROR")
            failures.append("pypi_uv")

    # 3. Rustup & Cargo
    if run_all or "rust" in selected_scenarios:
        try:
            test_rust_toolchain_and_cargo_build(cache_host)
        except Exception as e:
            log(f"[FAIL] Rustup & Cargo scenario failed: {e}", "ERROR")
            failures.append("rust_cargo")

    # 4. Git Proxy
    if run_all or "git" in selected_scenarios:
        try:
            test_git_proxy()
        except Exception as e:
            log(f"[FAIL] Git proxy scenario failed: {e}", "ERROR")
            failures.append("git_proxy")

    log("\n==========================================================================")
    if failures:
        log(f">>> TEST SUITE FAILED with {len(failures)} failed scenario(s): {failures} <<<", "ERROR")
        sys.exit(1)
    else:
        log(">>> ALL REALISTIC END-USER SCENARIOS PASSED SUCCESSFULLY! <<<")
    log("==========================================================================")


if __name__ == "__main__":
    main()

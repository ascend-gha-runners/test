#!/usr/bin/env python3
"""
Generate and Update E2E Test Report Data for GitHub Pages
Fetches GitHub Actions workflow runs and jobs, maintains historical data incrementally,
and aggregates metrics by date (day) and test case.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch GHA runs and generate E2E test report data"
    )
    parser.add_argument(
        "--repo",
        default="ascend-gha-runners/test",
        help="GitHub repository in owner/repo format",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub Personal Access Token or GITHUB_TOKEN",
    )
    parser.add_argument(
        "--test-cases-config",
        default=".github/config/test_cases.json",
        help="Path to test_cases.json catalog",
    )
    parser.add_argument(
        "--history-file",
        default="data/history.json",
        help="Path to historical runs database file",
    )
    parser.add_argument(
        "--output-dir",
        default="site",
        help="Directory to write output JSON and web files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=60,
        help="Maximum number of recent runs to fetch from GitHub API",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrency workers for fetching jobs",
    )
    parser.add_argument(
        "--full-fetch-jobs",
        action="store_true",
        help="Force re-fetching jobs for completed runs even if cached in history",
    )
    return parser.parse_args()


def get_default_token():
    # 1. From env
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        return tok
    # 2. Try gh auth token
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        t = proc.stdout.strip()
        if t:
            return t
    except Exception:
        pass
    return ""


def load_test_cases_catalog(config_path):
    catalog = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for tc in data.get("test_cases", []):
                    wf = tc.get("workflow", "")
                    wf_base = os.path.basename(wf).replace(".yml", "").replace(".yaml", "")
                    tc["workflow_base"] = wf_base
                    catalog[tc["id"]] = tc
                    if wf_base:
                        catalog[wf_base] = tc
        except Exception as e:
            print(f"[WARN] Failed to parse {config_path}: {e}", file=sys.stderr)
    return catalog


def github_api_get(url, token=None):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Ascend-Test-Report-Generator",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code} for {url}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ERROR] Request failed for {url}: {e}", file=sys.stderr)
        return None


def fetch_runs_via_gh(repo, limit=60):
    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--limit",
        str(limit),
        "--json",
        "databaseId,name,workflowName,workflowDatabaseId,conclusion,status,createdAt,updatedAt,url,event,headBranch,headSha",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except Exception as e:
        print(f"[WARN] gh run list failed: {e}", file=sys.stderr)
        return None


def fetch_jobs_for_run(repo, run_id, token=""):
    """
    Fetch jobs using HTTP API with token, fallback to gh cli with retry.
    """
    if token:
        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
        data = github_api_get(url, token)
        if data and "jobs" in data:
            return data["jobs"]

    for attempt in range(2):
        cmd = [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repo,
            "--json",
            "jobs",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            res = json.loads(proc.stdout)
            return res.get("jobs", [])
        except Exception as e:
            if attempt == 0:
                time.sleep(1)
            else:
                print(f"[WARN] gh run view {run_id} failed after retry: {e}", file=sys.stderr)
    return []


def parse_iso_datetime(dt_str):
    if not dt_str:
        return None
    clean_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(clean_str)
    except Exception:
        return None


def extract_cluster_from_job_name(job_name):
    if not job_name:
        return "default"
    
    m = re.search(r"\(([^)]+)\)", job_name)
    if m:
        candidate = m.group(1).strip()
        parts = [p.strip() for p in candidate.split(",")]
        for p in parts:
            if re.search(r"(gy|hk|wlcb|sz|cd|dg|xa|tj|sh|bj|wh|fs|su)-?[0-9]+", p, re.I):
                return p
        return candidate

    m2 = re.search(r"([a-zA-Z]+-?[0-9]+)", job_name)
    if m2:
        return m2.group(1)

    return "all"


def get_test_case_info(workflow_name, catalog):
    wf_clean = workflow_name.replace(".yml", "").replace(".yaml", "").strip()
    if wf_clean in catalog:
        tc = catalog[wf_clean]
        return {
            "test_case_id": tc.get("id", wf_clean),
            "title": tc.get("name", wf_clean),
            "category": tc.get("category", "e2e"),
            "doc_url": tc.get("feature_doc", ""),
            "assertions": tc.get("assertions", {}),
        }
    
    friendly_titles = {
        "e2e-cluster-runners": "全集群 Runner 双标签调度与算力识别",
        "e2e-cross-cluster-pep658": "PyTorch PEP 658 元数据完整性抽检",
        "e2e-feature-apt-cache": "Ubuntu APT 镜像缓存服务验证 (8081)",
        "e2e-feature-crates-cache": "crates.io 缓存服务验证 (8085)",
        "e2e-feature-pypi-cache": "PyPI 缓存服务验证 (80)",
        "e2e-feature-rustup-cache": "Rustup 工具链缓存服务验证 (8082)",
        "e2e-feature-yum-cache": "openEuler YUM/DNF 镜像缓存服务验证 (8083)",
        "e2e-gy006-a2-runner-smoke": "GY-006 集群 A2-Runner 基础烟测",
        "e2e-gy006-nginx-cache": "GY-006 集群 Nginx 缓存综合验证",
        "e2e-hk001-a2-1-smoke": "HK-001 集群 A2-1 烟测与基础环境连通性",
        "e2e-scan-pytorch-metadata": "PyTorch 元数据全量扫描验证",
        "e2e-user-scenarios": "真实生产用户组合全链路场景验证",
    }

    return {
        "test_case_id": wf_clean,
        "title": friendly_titles.get(wf_clean, wf_clean),
        "category": "e2e-test",
        "doc_url": "https://ascend-gha-runners.github.io/docs/feature/",
        "assertions": {},
    }


def main():
    args = parse_args()
    if not args.token:
        args.token = get_default_token()

    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(os.path.dirname(args.history_file) if os.path.dirname(args.history_file) else ".", exist_ok=True)

    catalog = load_test_cases_catalog(args.test_cases_config)
    print(f"[INFO] Loaded {len(catalog)} test case definitions from catalog")

    # Load existing history
    history_runs = {}
    if os.path.exists(args.history_file):
        try:
            with open(args.history_file, "r", encoding="utf-8") as f:
                raw_hist = json.load(f)
                if isinstance(raw_hist, dict) and "runs" in raw_hist:
                    for r in raw_hist["runs"]:
                        history_runs[str(r["id"])] = r
                elif isinstance(raw_hist, list):
                    for r in raw_hist:
                        history_runs[str(r["id"])] = r
            print(f"[INFO] Loaded {len(history_runs)} existing historical runs from {args.history_file}")
        except Exception as e:
            print(f"[WARN] Failed to load history file: {e}", file=sys.stderr)

    # Fetch recent runs
    raw_runs = None
    try:
        raw_runs = fetch_runs_via_gh(args.repo, args.limit)
    except Exception:
        raw_runs = None

    if not raw_runs and args.token:
        print("[INFO] Fallback to GitHub REST API for runs...")
        api_url = f"https://api.github.com/repos/{args.repo}/actions/runs?per_page={args.limit}"
        resp_data = github_api_get(api_url, args.token)
        if resp_data and "workflow_runs" in resp_data:
            raw_runs = []
            for item in resp_data["workflow_runs"]:
                raw_runs.append({
                    "databaseId": item.get("id"),
                    "name": item.get("name"),
                    "workflowName": item.get("name"),
                    "workflowDatabaseId": item.get("workflow_id"),
                    "conclusion": item.get("conclusion"),
                    "status": item.get("status"),
                    "createdAt": item.get("created_at"),
                    "updatedAt": item.get("updated_at"),
                    "url": item.get("html_url"),
                    "event": item.get("event"),
                    "headBranch": item.get("head_branch"),
                    "headSha": item.get("head_sha"),
                })

    if not raw_runs:
        print("[WARN] No workflow runs retrieved from GitHub API.", file=sys.stderr)
        if not history_runs:
            print("[ERROR] No historical data either. Exiting.", file=sys.stderr)
            sys.exit(1)
        raw_runs = []

    print(f"[INFO] Evaluating {len(raw_runs)} fetched runs against cache...")

    # Identify runs needing jobs
    runs_needing_jobs = []
    for r in raw_runs:
        run_id_str = str(r["databaseId"])
        cached = history_runs.get(run_id_str)
        needs_jobs = False
        if not cached:
            needs_jobs = True
        elif args.full_fetch_jobs:
            needs_jobs = True
        elif cached.get("status") != "completed" and r.get("status") == "completed":
            needs_jobs = True
        elif "jobs" not in cached or not cached["jobs"]:
            needs_jobs = True

        if needs_jobs and r.get("status") == "completed":
            runs_needing_jobs.append(r)

    print(f"[INFO] {len(runs_needing_jobs)} runs require job details fetching (concurrency: {args.workers})")

    # Fetch jobs concurrently
    fetched_jobs_map = {}
    if runs_needing_jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_run = {
                executor.submit(fetch_jobs_for_run, args.repo, r["databaseId"], args.token): r["databaseId"]
                for r in runs_needing_jobs
            }
            for future in concurrent.futures.as_completed(future_to_run):
                rid = future_to_run[future]
                try:
                    jobs_data = future.result()
                    fetched_jobs_map[rid] = jobs_data
                except Exception as exc:
                    print(f"[WARN] Run {rid} jobs fetch generated an exception: {exc}")
                    fetched_jobs_map[rid] = []

    # Merge into history
    for r in raw_runs:
        run_id = r["databaseId"]
        run_id_str = str(run_id)
        cached = history_runs.get(run_id_str)

        if run_id in fetched_jobs_map:
            raw_job_list = fetched_jobs_map[run_id]
            jobs_list = []
            for j in raw_job_list:
                j_start = parse_iso_datetime(j.get("startedAt") or j.get("started_at"))
                j_end = parse_iso_datetime(j.get("completedAt") or j.get("completed_at"))
                j_dur = int((j_end - j_start).total_seconds()) if (j_start and j_end) else 0

                failed_step = None
                steps_data = []
                for s in j.get("steps", []):
                    s_conc = s.get("conclusion")
                    if s_conc in ("failure", "timed_out") and not failed_step:
                        failed_step = s.get("name")
                    steps_data.append({
                        "name": s.get("name"),
                        "conclusion": s_conc,
                        "number": s.get("number"),
                    })

                cluster = extract_cluster_from_job_name(j.get("name", ""))
                jobs_list.append({
                    "id": j.get("databaseId") or j.get("id"),
                    "name": j.get("name"),
                    "cluster": cluster,
                    "status": j.get("status"),
                    "conclusion": j.get("conclusion"),
                    "started_at": j.get("startedAt") or j.get("started_at"),
                    "completed_at": j.get("completedAt") or j.get("completed_at"),
                    "duration_seconds": max(0, j_dur),
                    "url": j.get("url") or j.get("html_url"),
                    "failed_step": failed_step,
                    "steps": steps_data,
                })
        elif cached:
            jobs_list = cached.get("jobs", [])
        else:
            jobs_list = []

        dt_start = parse_iso_datetime(r.get("createdAt"))
        dt_end = parse_iso_datetime(r.get("updatedAt"))
        duration_seconds = int((dt_end - dt_start).total_seconds()) if (dt_start and dt_end) else 0

        date_utc = dt_start.strftime("%Y-%m-%d") if dt_start else "unknown"
        if dt_start:
            dt_beijing = dt_start + datetime.timedelta(hours=8)
            date_beijing = dt_beijing.strftime("%Y-%m-%d")
        else:
            date_beijing = date_utc

        wf_name = r.get("workflowName") or r.get("name") or "unknown"
        tc_info = get_test_case_info(wf_name, catalog)

        jobs_passed = sum(1 for j in jobs_list if j.get("conclusion") == "success")
        jobs_failed = sum(1 for j in jobs_list if j.get("conclusion") in ("failure", "timed_out"))

        record = {
            "id": r["databaseId"],
            "name": r.get("name"),
            "workflow_name": wf_name,
            "test_case_id": tc_info["test_case_id"],
            "title": tc_info["title"],
            "category": tc_info["category"],
            "doc_url": tc_info["doc_url"],
            "assertions": tc_info["assertions"],
            "status": r.get("status"),
            "conclusion": r.get("conclusion") or "in_progress",
            "created_at": r.get("createdAt"),
            "updated_at": r.get("updatedAt"),
            "duration_seconds": max(0, duration_seconds),
            "event": r.get("event"),
            "branch": r.get("headBranch"),
            "sha": (r.get("headSha") or "")[:8],
            "url": r.get("url"),
            "date": date_beijing,
            "date_utc": date_utc,
            "date_beijing": date_beijing,
            "jobs_count": len(jobs_list),
            "jobs_passed": jobs_passed,
            "jobs_failed": jobs_failed,
            "jobs": jobs_list,
        }
        history_runs[run_id_str] = record

    print(f"[INFO] History synchronized. Total tracked runs: {len(history_runs)}")

    # Sort all runs descending by created_at
    all_runs = list(history_runs.values())
    all_runs.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    # Save to history file
    with open(args.history_file, "w", encoding="utf-8") as f:
        json.dump({"runs": all_runs}, f, indent=2, ensure_ascii=False)
    print(f"[INFO] Saved history to {args.history_file}")

    # Build Aggregations
    total_runs = len(all_runs)
    completed_runs = [r for r in all_runs if r.get("status") == "completed"]
    passed_runs = [r for r in completed_runs if r.get("conclusion") == "success"]
    failed_runs = [r for r in completed_runs if r.get("conclusion") in ("failure", "timed_out")]
    pass_rate = round((len(passed_runs) / len(completed_runs) * 100), 1) if completed_runs else 100.0

    # 1. By Date
    by_date = {}
    for r in all_runs:
        d = r.get("date_beijing") or "unknown"
        if d not in by_date:
            by_date[d] = {
                "date": d,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "in_progress": 0,
                "durations": [],
                "runs": [],
            }
        item = by_date[d]
        item["total"] += 1
        item["runs"].append(r)
        if r.get("status") != "completed":
            item["in_progress"] += 1
        elif r.get("conclusion") == "success":
            item["passed"] += 1
        else:
            item["failed"] += 1
        if r.get("duration_seconds"):
            item["durations"].append(r["duration_seconds"])

    daily_trend = []
    for d in sorted(by_date.keys(), reverse=True):
        item = by_date[d]
        comp = item["passed"] + item["failed"]
        pr = round(item["passed"] / comp * 100, 1) if comp > 0 else (100.0 if item["passed"] > 0 else 0.0)
        avg_dur = int(sum(item["durations"]) / len(item["durations"])) if item["durations"] else 0
        item["summary"] = {
            "date": d,
            "total": item["total"],
            "passed": item["passed"],
            "failed": item["failed"],
            "in_progress": item["in_progress"],
            "pass_rate": pr,
            "avg_duration_sec": avg_dur,
        }
        del item["durations"]
        daily_trend.append(item["summary"])

    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")
    today_stats = by_date.get(today_str, {}).get("summary", {
        "date": today_str,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "in_progress": 0,
        "pass_rate": 100.0,
        "avg_duration_sec": 0,
    })

    # 2. By Test Case
    by_test_case = {}
    for r in all_runs:
        cid = r.get("test_case_id") or r.get("workflow_name")
        if cid not in by_test_case:
            by_test_case[cid] = {
                "id": cid,
                "name": r.get("title", cid),
                "workflow": r.get("workflow_name"),
                "category": r.get("category", "e2e"),
                "doc_url": r.get("doc_url", ""),
                "assertions": r.get("assertions", {}),
                "total": 0,
                "passed": 0,
                "failed": 0,
                "durations": [],
                "latest_conclusion": None,
                "latest_run_at": None,
                "latest_url": None,
                "history_by_date": {},
                "recent_runs": [],
            }
        tc_entry = by_test_case[cid]
        tc_entry["total"] += 1
        conc = r.get("conclusion")
        if conc == "success":
            tc_entry["passed"] += 1
        elif conc in ("failure", "timed_out"):
            tc_entry["failed"] += 1
        
        if r.get("duration_seconds"):
            tc_entry["durations"].append(r["duration_seconds"])

        if not tc_entry["latest_run_at"] or (r.get("created_at") or "") > tc_entry["latest_run_at"]:
            tc_entry["latest_run_at"] = r.get("created_at")
            tc_entry["latest_conclusion"] = conc
            tc_entry["latest_url"] = r.get("url")

        d = r.get("date_beijing")
        if d:
            if d not in tc_entry["history_by_date"]:
                tc_entry["history_by_date"][d] = {
                    "conclusion": conc,
                    "status": r.get("status"),
                    "run_id": r.get("id"),
                    "url": r.get("url"),
                    "duration_seconds": r.get("duration_seconds"),
                    "created_at": r.get("created_at"),
                }
            elif conc == "failure":
                tc_entry["history_by_date"][d]["conclusion"] = "failure"

        if len(tc_entry["recent_runs"]) < 10:
            tc_entry["recent_runs"].append({
                "id": r.get("id"),
                "conclusion": conc,
                "status": r.get("status"),
                "date": d,
                "duration_seconds": r.get("duration_seconds"),
                "url": r.get("url"),
                "event": r.get("event"),
                "branch": r.get("branch"),
                "created_at": r.get("created_at"),
                "jobs_count": r.get("jobs_count"),
                "jobs_failed": r.get("jobs_failed"),
            })

    test_cases_list = []
    for cid, tc_entry in by_test_case.items():
        comp = tc_entry["passed"] + tc_entry["failed"]
        tc_entry["pass_rate"] = round(tc_entry["passed"] / comp * 100, 1) if comp > 0 else 100.0
        tc_entry["avg_duration_sec"] = int(sum(tc_entry["durations"]) / len(tc_entry["durations"])) if tc_entry["durations"] else 0
        del tc_entry["durations"]
        test_cases_list.append(tc_entry)

    test_cases_list.sort(key=lambda x: (x.get("category"), x.get("id")))

    # 3. Matrix
    all_dates_sorted = sorted(by_date.keys(), reverse=True)
    matrix_dates = all_dates_sorted[:14]
    matrix_data = []
    for tc in test_cases_list:
        row = {
            "id": tc["id"],
            "name": tc["name"],
            "category": tc["category"],
            "statuses": {},
        }
        for d in matrix_dates:
            d_info = tc["history_by_date"].get(d)
            if d_info:
                row["statuses"][d] = {
                    "conclusion": d_info.get("conclusion"),
                    "run_id": d_info.get("run_id"),
                    "url": d_info.get("url"),
                }
            else:
                row["statuses"][d] = None
        matrix_data.append(row)

    # 4. Recent Failures
    recent_failures = []
    for r in all_runs:
        if r.get("conclusion") in ("failure", "timed_out"):
            recent_failures.append({
                "id": r["id"],
                "workflow_name": r.get("workflow_name"),
                "title": r.get("title"),
                "date": r.get("date_beijing"),
                "created_at": r.get("created_at"),
                "url": r.get("url"),
                "branch": r.get("branch"),
                "event": r.get("event"),
                "failed_jobs": [
                    {
                        "name": j.get("name"),
                        "cluster": j.get("cluster"),
                        "failed_step": j.get("failed_step"),
                        "url": j.get("url"),
                    }
                    for j in r.get("jobs", [])
                    if j.get("conclusion") in ("failure", "timed_out")
                ],
            })
            if len(recent_failures) >= 15:
                break

    report_data = {
        "meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "generated_at_beijing": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            "repo": args.repo,
            "total_runs": total_runs,
            "completed_runs": len(completed_runs),
            "passed_runs": len(passed_runs),
            "failed_runs": len(failed_runs),
            "overall_pass_rate": pass_rate,
            "total_test_cases": len(test_cases_list),
        },
        "today": today_stats,
        "daily_trend": daily_trend,
        "dates_list": all_dates_sorted,
        "by_date": by_date,
        "by_test_case": by_test_case,
        "test_cases_list": test_cases_list,
        "matrix": {
            "dates": matrix_dates,
            "rows": matrix_data,
        },
        "recent_failures": recent_failures,
    }

    data_dir = os.path.join(args.output_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    report_json_path = os.path.join(data_dir, "report_data.json")
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False)
    print(f"[SUCCESS] Wrote report data to {report_json_path}")

    out_history_path = os.path.join(data_dir, "history.json")
    with open(out_history_path, "w", encoding="utf-8") as f:
        json.dump({"runs": all_runs}, f, ensure_ascii=False)
    print(f"[SUCCESS] Wrote full history to {out_history_path}")


if __name__ == "__main__":
    main()

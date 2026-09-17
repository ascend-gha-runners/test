# test

ascend-gha-runners 的 runner e2e 测试仓库。

**开发新用例前必读 [AGENTS.md](AGENTS.md)**(断言分级、确定性断言模式、已踩坑清单)。

## E2E 套件（贵阳006 组织级 A2 runner）

基础设施变更（[ascend-ci-deployment](https://github.com/opensourceways/ascend-ci-deployment)）合入前，先在本仓库 dispatch 对应 e2e workflow 验证，全绿后再合入。

| Workflow | 覆盖 | 目标 runner |
|---|---|---|
| `e2e-gy006-a2-runner-smoke` | label 调度 / aarch64 / NPU 卡数(1/2/4/8) / CPU·内存配额 / git-cdn / 共享缓存挂载 | `linux-aarch64-a2-{1,2,4,8}` + `gy-006` |
| `e2e-gy006-nginx-cache` | X-Pypi-Cache 命中 / 经缓存 pip install / 404 回源确定性断言 / crates.io 缓存(8085) / 大 wheel 冷热对比 / yum 探针 | `linux-aarch64-a2-1` + `gy-006` |

### 运行方式

```bash
gh workflow run e2e-gy006-a2-runner-smoke.yml --repo ascend-gha-runners/test
gh workflow run e2e-gy006-nginx-cache.yml   --repo ascend-gha-runners/test
# 或在 GitHub Actions 页面手动 Run workflow
```

### 说明

- NPU 卡数与 X-Pypi-Cache 命中为**硬断言**；CPU/内存配额断言阈值 90%（容忍 cgroup 取整）；yum 探针为信息项（continue-on-error）。
- 5xx fallback 的故障注入验证在 ascend-ci-deployment 仓库单测（`tests/test-nginx-pypi-cache.sh`）+ nginx-pypi-cache-test 实例，线上只验 404 真实回源。
- a2-8 需要 8 卡整节点，Pending 超过 90 分钟说明该规格资源不足，请到 ascend-ci-deployment 调整配额（参考 vllm-omni `npu-8` 用 256Gi 的先例）。

### 历史 workflow

- `test_npu.yaml` / `test-action-path.yml` / `test_secret_upload.yml`：早期手工测试，保留。

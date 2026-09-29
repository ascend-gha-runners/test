# 0002 — PyTorch CPU 用例仅限 amd64 架构测试并在 arm64 优雅跳过

- 状态：已接受 (2026-09-26)
- 决策人：CI/基础设施测试组
- 关联：PR [#23](https://github.com/ascend-gha-runners/test/pull/23), [`AGENTS.md`](../../AGENTS.md), [`e2e-feature-pypi-cache.yml`](../../.github/workflows/e2e-feature-pypi-cache.yml), [`e2e-cross-cluster-pep658.yml`](../../.github/workflows/e2e-cross-cluster-pep658.yml), [`e2e-scan-pytorch-metadata.yml`](../../.github/workflows/e2e-scan-pytorch-metadata.yml)

## 背景

在各集群测试 PyTorch Wheels 缓存（`/whl/cpu` 路径）与 PEP 658 元数据时，出现针对 ARM64 / AArch64 架构节点（如 `gy-001`, `sh-001`, `sz-lab`, `wlcb-001` 等）测试报错失败的情况。

经根因排查：
- 官方 PyTorch 维护团队在上游 `https://download.pytorch.org/whl/cpu/` 路径下，**仅构建并发布了 `x86_64` (Linux) 与 `win_amd64` (Windows) 的 CPU 版预编译 wheel**；
- 官方从未在该源下提供 Linux `aarch64` 的预编译轮子。因此在 ARM64 节点上执行 `pip/uv install torch --index-url .../whl/cpu` 必然报 `No matching distribution found`。

## 决策

**严格遵循 `AGENTS.md`“绝不把环境差异伪装成用例失败”的断言分级原则：针对 PyTorch 依赖测试，全面跳过 arm64 / aarch64 架构，仅在各集群的 amd64 节点上调度执行：**

1. **矩阵调度级过滤 (Matrix Setup Level)**：
   - 在 PyTorch 专项工作流（如 `e2e-scan-pytorch-metadata.yml` 与 `e2e-cross-cluster-pep658.yml`）中，`matrix-setup` 阶段强制过滤 `arch in ("amd64", "x86_64")`；
   - 覆盖全网所有具备 amd64 算力的 9 个集群（`gy-006`, `gy-003`, `gy-004`, `cn12-001`, `gy-005`, `sh-002`, `hk-001`, `aiframework`, `mind-third-ci`）；
   - 针对纯 arm64 的 4 个集群（`gy-001`, `sh-001`, `sz-lab`, `wlcb-001`），增加优雅跳过判定（当 `count == 0` 时跳过 matrix 执行，流程置绿通过）。
2. **步骤级架构保护 (Step Level Guard)**：
   - 在平台特性通用工作流（`e2e-feature-pypi-cache.yml`）的步骤 5 中，检测宿主架构；若为 `arm64` / `aarch64`，直接输出提示信息并以 `exit 0` 优雅退出；
   - 步骤 6 导入自检中，在 arm64 节点跳过 `import torch` 与张量计算，仅检验其他通用依赖（`uv`, `uc-manager`, `triton-ascend`）。
3. **测试脚本防护**：
   - 在 `scripts/pep658_check.py` 等底层脚本中增加架构感知，对 arm64 节点安全跳过 pip 解析测试。

## 理由与后果

- **区分被测主体**：本测试仓库的职责是检验**集群内网网络、Nginx 缓存代理、DNS 与存储**，而不是断言上游 PyTorch 社区是否支持 aarch64 CPU wheel。强行在不支持的架构上跑只会制造无意义的假红警报；
- **全绿保障**：确保全集群调度在真实反映集群网络与代理能力的同时，不因上游生态缺失阻碍 PR 合入大盘。

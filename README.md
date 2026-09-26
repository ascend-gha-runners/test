# ascend-gha-runners/test

本仓库承载 **Ascend NPU Runner 与集群基础设施的端到端 (E2E) 测试套件**。
任何 [ascend-ci-deployment](https://github.com/opensourceways/ascend-ci-deployment) 的基础设施变更（Runner 部署、Nginx 缓存代理、调度器、存储挂载等）**合入前必须先在本仓库 dispatch 对应 E2E 验证全绿后再合入**。

> **规范必读**：
> - **📊 测试质量大盘 (GitHub Pages)**：[**https://ascend-gha-runners.github.io/test/**](https://ascend-gha-runners.github.io/test/)（每 1 个测试完成自动刷新，按天与测试用例维度展示全集群运行健康度）；
> - **测试意图与验收基准**：查看 [**`docs/test-cases.md`**](docs/test-cases.md)（所有用例的规格说明、输入输出、断言分级与容灾标准）；
> - **用例元数据清单**：查看 [**`.github/config/test_cases.json`**](.github/config/test_cases.json)（机器可读配置与执行引擎映射）；
> - **开发规范与避坑指南**：查看 [**`AGENTS.md`**](AGENTS.md)（断言分级、确定性回源断言模式、已踩坑清单）。

---

## 一、三层测试解耦架构

本仓库采用“规约（意图）- 引擎（逻辑）- 编排（策略）”三层解耦设计：

1. **规约层（Specification）**：在 [`docs/test-cases.md`](docs/test-cases.md) 与 [`.github/config/test_cases.json`](.github/config/test_cases.json) 固化测试目的与预期行为契约，涵盖硬断言、软断言与探针；
2. **编排与执行层（Orchestration & Execution）**：在 `.github/workflows/` 中根据 [`.github/config/runners.json`](.github/config/runners.json) 动态派生覆盖全量 13 个集群的测试矩阵，各特性工作流直接集成基础功能协议验证与真实用户视角的生产构建场景。

---

## 二、平台特性专属用例 (Platform Features)

对应官方文档 [Platform Features](https://ascend-gha-runners.github.io/docs/feature/)，每个特性均有独立 E2E 测试用例，采用真实项目（如 `vllm-project/vllm-ascend`）的实际使用方式，并支持跨全 13 集群矩阵调度：

| 用例 ID | 特性 | 关联工作流 | 代理端口 / 协议 | 覆盖范围 / 关键断言 |
|---|---|---|---|---|
| `TC-FEAT-PYPI` | **PyPI Cache** | [`e2e-feature-pypi-cache.yml`](.github/workflows/e2e-feature-pypi-cache.yml) | Port 80 (HTTP) | 基础连通+用户全流程：pip/uv 多源配置、triton-ascend 安装、`/whl/cpu` PyTorch 安装 (amd64)、运行时 import 验证、404 回源 |
| `TC-FEAT-APT` | **APT Cache** | [`e2e-feature-apt-cache.yml`](.github/workflows/e2e-feature-apt-cache.yml) | Port 8081 (HTTP) | 基础连通+用户全流程：Ubuntu 真实 `apt-get update` & 安装构建依赖 `git`/`zstd`/`gcc`/`cmake` |
| `TC-FEAT-RUSTUP` | **Rust / rustup** | [`e2e-feature-rustup-cache.yml`](.github/workflows/e2e-feature-rustup-cache.yml) | Port 8082 (HTTP) | 基础连通+用户全流程：rustup 极简稳定工具链真实下载安装与 `rustc`/`cargo` 可执行验证 |
| `TC-FEAT-YUM` | **YUM / DNF Cache** | [`e2e-feature-yum-cache.yml`](.github/workflows/e2e-feature-yum-cache.yml) | Port 8083 (HTTP) | 基础连通+用户全流程：openEuler 真实 `dnf/yum makecache` & 安装构建依赖 `git`/`zstd` |
| `TC-FEAT-CRATES` | **crates.io Cache**| [`e2e-feature-crates-cache.yml`](.github/workflows/e2e-feature-crates-cache.yml) | Port 8085 (HTTP) | 基础连通+用户全流程：Cargo sparse 镜像、真实工程拉取 `anyhow` 编译执行、MISS→HIT 缓存头 |

---

## 三、集群调度与专项测试

| 用例 ID | 目标 | 关联工作流 | 调度与覆盖 |
|---|---|---|---|
| `TC-SCHED-DUAL-LABEL` | **全集群双标签与算力自检** | [`e2e-cluster-runners.yml`](.github/workflows/e2e-cluster-runners.yml) | 覆盖全部 13 集群 `[型号, 区域]` 双标签调度与芯片驱动自检 |
| `TC-PEP658-METADATA` | **PyTorch 元数据 PEP 658 完整性** | [`e2e-cross-cluster-pep658.yml`](.github/workflows/e2e-cross-cluster-pep658.yml) | 校验 Simple Index 元数据校验和，针对 pytorch/pytorch#197552 兜底 |

---

## 四、执行方式

### 1. GitHub Actions 触发 (远程调度)

```bash
# 平台各特性独立验证
gh workflow run e2e-feature-pypi-cache.yml   --repo ascend-gha-runners/test
gh workflow run e2e-feature-apt-cache.yml    --repo ascend-gha-runners/test
gh workflow run e2e-feature-rustup-cache.yml --repo ascend-gha-runners/test
gh workflow run e2e-feature-yum-cache.yml    --repo ascend-gha-runners/test
gh workflow run e2e-feature-crates-cache.yml --repo ascend-gha-runners/test

# 全集群 Runner 联通性与双标签验证
gh workflow run e2e-cluster-runners.yml      --repo ascend-gha-runners/test

# 烟测与全链路用例
gh workflow run e2e-gy006-a2-runner-smoke.yml --repo ascend-gha-runners/test
gh workflow run e2e-gy006-nginx-cache.yml   --repo ascend-gha-runners/test
gh run watch <run-id> --repo ascend-gha-runners/test
```

### 2. 双重视角设计规范 (Dual-Perspective Design)

本仓库各平台特性工作流均遵循**“基础功能验证 + 用户视角场景测试”**双重闭环设计：
- **【基础功能】**：覆盖端点连通性探测、服务响应头断言（MISS/HIT）、协议级 404 回退机制及多源容灾降级；
- **【用户视角场景】**：完全模拟真实大型 AI 工程（如 `vllm-project/vllm-ascend`）的生产 CI 环境，配置真实多源镜像，严格强制 `--no-cache-dir` 绕过本地缓存，真实安装工具链、下载编译核心依赖（如 `triton-ascend`、`PyTorch`、`anyhow` 等）并运行 Python/Rust 计算程序进行运行时验证。

## 五、测试质量大盘 (Test Quality Dashboard)

本仓库提供由 GitHub Pages 承载的自动化测试质量看板：
- **访问地址**：[https://ascend-gha-runners.github.io/test/](https://ascend-gha-runners.github.io/test/)
- **自动刷新机制**：由 [`.github/workflows/update-report-pages.yml`](.github/workflows/update-report-pages.yml) 监听所有 `e2e-*` 验证工作流，**只要任意 1 个测试完成（无论是定时 schedule 还是手动 dispatch），便即刻自动刷新大盘**。
- **展示维度**：
  1. **按天展示 (Daily Timeline)**：展示每日通过率走势、单日运行总览、当日所有用例及细分子 Job（集群）的执行流水与耗时；
  2. **按测试用例展示 (Test Cases)**：关联 [`.github/config/test_cases.json`](.github/config/test_cases.json) 规格，展示平台特性用例的硬断言、历史通过率及历史表现；
  3. **矩阵全景大盘 (Health Matrix)**：用例 × 日期的二维状态热力网格；
  4. **异常与失败诊断 (Failures)**：聚焦最近报错的 Job、集群、失败 Step 与 Actions 排查入口。

---

## 六、历史 workflow

`test_npu.yaml` / `test-action-path.yml` / `test_secret_upload.yml` 为早期手工测试，保留作参考，新用例不要模仿其结构。

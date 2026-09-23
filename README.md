# ascend-gha-runners/test

本仓库承载 **Ascend NPU Runner 与集群基础设施的端到端 (E2E) 测试套件**。
任何 [ascend-ci-deployment](https://github.com/opensourceways/ascend-ci-deployment) 的基础设施变更（Runner 部署、Nginx 缓存代理、调度器、存储挂载等）**合入前必须先在本仓库 dispatch 对应 E2E 验证全绿后再合入**。

> **规范必读**：
> - **测试意图与验收基准**：查看 [**`docs/test-cases.md`**](docs/test-cases.md)（所有用例的规格说明、输入输出、断言分级与容灾标准）；
> - **用例元数据清单**：查看 [**`.github/config/test_cases.json`**](.github/config/test_cases.json)（机器可读配置与执行引擎映射）；
> - **开发规范与避坑指南**：查看 [**`AGENTS.md`**](AGENTS.md)（断言分级、确定性回源断言模式、已踩坑清单）。

---

## 一、三层测试解耦架构

本仓库采用“规约（意图）- 引擎（逻辑）- 编排（策略）”三层解耦设计：

1. **规约层（Specification）**：在 [`docs/test-cases.md`](docs/test-cases.md) 与 [`.github/config/test_cases.json`](.github/config/test_cases.json) 固化测试目的与预期行为契约，长期稳定；
2. **引擎层（Execution Engine）**：在 [`scripts/test_user_scenarios.py`](scripts/test_user_scenarios.py) 提供纯 Python 实现，支持脱离 GitHub Actions 在本地容器独立复现；
3. **编排层（Dynamic Policy）**：在 `.github/workflows/` 中根据 [`.github/config/runners.json`](.github/config/runners.json) 动态派生覆盖全量 13 个集群的测试矩阵。

---

## 二、平台特性专属用例 (Platform Features)

对应官方文档 [Platform Features](https://ascend-gha-runners.github.io/docs/feature/)，每个特性均有独立 E2E 测试用例，采用真实项目（如 `vllm-project/vllm-ascend`）的实际使用方式，并支持跨全 13 集群矩阵调度：

| 用例 ID | 特性 | 关联工作流 | 代理端口 / 协议 | 覆盖范围 / 关键断言 |
|---|---|---|---|---|
| `TC-FEAT-PYPI` | **PyPI Cache** | [`e2e-feature-pypi-cache.yml`](.github/workflows/e2e-feature-pypi-cache.yml) | Port 80 (HTTP) | pip/uv 真实安装、`/whl/cpu` 真实安装 PyTorch、404 回源至 pypi.org |
| `TC-FEAT-APT` | **APT Cache** | [`e2e-feature-apt-cache.yml`](.github/workflows/e2e-feature-apt-cache.yml) | Port 8081 (HTTP) | Ubuntu 真实 `apt-get update` & 安装构建依赖 `git`/`zstd`/`gcc`/`cmake` |
| `TC-FEAT-RUSTUP` | **Rust / rustup** | [`e2e-feature-rustup-cache.yml`](.github/workflows/e2e-feature-rustup-cache.yml) | Port 8082 (HTTP) | rustup 极简稳定工具链真实下载安装与 `rustc`/`cargo` 可执行验证 |
| `TC-FEAT-YUM` | **YUM / DNF Cache** | [`e2e-feature-yum-cache.yml`](.github/workflows/e2e-feature-yum-cache.yml) | Port 8083 (HTTP) | openEuler 真实 `dnf/yum makecache` & 安装构建依赖 `git`/`zstd` |
| `TC-FEAT-CRATES` | **crates.io Cache**| [`e2e-feature-crates-cache.yml`](.github/workflows/e2e-feature-crates-cache.yml) | Port 8085 (HTTP) | Cargo sparse 镜像、真实工程拉取 `anyhow` 依赖并编译执行、MISS→HIT 缓存头 |

---

## 三、集群调度与专项测试

| 用例 ID | 目标 | 关联工作流 | 调度与覆盖 |
|---|---|---|---|
| `TC-SCHED-DUAL-LABEL` | **全集群双标签与算力自检** | [`e2e-cluster-runners.yml`](.github/workflows/e2e-cluster-runners.yml) | 覆盖全部 13 集群 `[型号, 区域]` 双标签调度与芯片驱动自检 |
| `TC-PEP658-METADATA` | **PyTorch 元数据 PEP 658 完整性** | [`e2e-cross-cluster-pep658.yml`](.github/workflows/e2e-cross-cluster-pep658.yml) | 校验 Simple Index 元数据校验和，针对 pytorch/pytorch#197552 兜底 |
| `TC-E2E-USER-SCENARIOS` | **生产端到端全链路场景** | [`e2e-user-scenarios.yml`](.github/workflows/e2e-user-scenarios.yml) | 串联 OS 包管理器、Python+uv、Rust 编译与 Git 代理的真实构建流水线 |

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

### 2. 容器与本地调试复现 (脱离 GHA 上下文)

```bash
# 查看所有固化的用例清单
python3 scripts/test_user_scenarios.py --list-cases

# 单独执行指定用例
python3 scripts/test_user_scenarios.py --case TC-FEAT-PYPI
python3 scripts/test_user_scenarios.py --case TC-FEAT-APT
python3 scripts/test_user_scenarios.py --case TC-FEAT-RUSTUP
python3 scripts/test_user_scenarios.py --case TC-FEAT-YUM
python3 scripts/test_user_scenarios.py --case TC-FEAT-CRATES

# 执行全量真实场景
python3 scripts/test_user_scenarios.py --scenarios all
```

---

## 五、历史 workflow

`test_npu.yaml` / `test-action-path.yml` / `test_secret_upload.yml` 为早期手工测试，保留作参考，新用例不要模仿其结构。

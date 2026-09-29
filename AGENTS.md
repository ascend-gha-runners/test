# AGENTS.md — ascend-gha-runners/test 架构地图与开发指导

本仓库承载 **昇腾 GHA Runner 与集群基础设施的端到端 (E2E) 测试**。
任何 [ascend-ci-deployment](https://github.com/opensourceways/ascend-ci-deployment) 的基础设施变更（Runner 镜像、Nginx 缓存、调度标签、共享存储等）**合入前必须先在此处 dispatch 对应 E2E，全绿后方可合入**。

---

## 1. 仓库导航地图 (Repository Index Map)

为支持 Agent **渐进式加载 (Progressive Loading)**，本仓库信息遵循“全局索引地图 -> 专项规约与决策 -> 底层配置与实现”的三层结构。请按当前任务的关注点跳转查阅：

| 模块 / 路径 | 核心定位与职责 | 何时需要读取 |
|---|---|---|
| [`docs/adr/`](docs/adr/) | **架构决策记录 (ADR)**<br>• 决策索引: [`docs/adr/README.md`](docs/adr/README.md)<br>• 写作约束: [`docs/adr/AGENTS.md`](docs/adr/AGENTS.md) | **方案变化、选型变更、新增机制时必读**（合入前必须提交对应 ADR） |
| [`docs/test-cases.md`](docs/test-cases.md) | **测试用例规约详情 (Specification)**<br>• 元数据清单: [`.github/config/test_cases.json`](.github/config/test_cases.json) | 需要了解具体用例的目的、生产调用方式、三级断言标准时 |
| [`.github/config/runners.json`](.github/config/runners.json) | **13 集群 Runner 拓扑与标签清单** | 调试调度策略、派生测试矩阵、配置双标签 `[型号, 集群名]` 时 |
| [`.github/workflows/`](.github/workflows/) | **E2E 编排工作流清单** | 编写、调试或修改具体 GitHub Actions 流程时 |
| [`scripts/`](scripts/) | **测试支撑与看板数据生成脚本** | 修改 PEP 658 校验器、元数据扫描器或测试质量大盘生成逻辑时 |

---

## 2. 任务快速路由 (Task Routing)

Agent 在执行不同任务时，请依照以下路径逐步加载上下文：

### 场景 A：方案变化 / 技术选型 / 新增机制
- **核心约束**：**严禁未经 ADR 直接合入重大结构与策略变更**。
- **行动指引**：
  1. 阅读 [`docs/adr/AGENTS.md`](docs/adr/AGENTS.md) 明确 ADR 格式与生命周期；
  2. 查阅 [`docs/adr/README.md`](docs/adr/README.md) 了解现有决策（如 [ADR-0001 双重视角闭环](docs/adr/0001-平台特性用例采用基础功能与用户场景双重视角闭环.md) 与 [ADR-0002 PyTorch 架构跳过](docs/adr/0002-pytorch-cpu用例仅限amd64架构测试并在arm64优雅跳过.md)）；
  3. 撰写 `docs/adr/NNNN-<标题>.md`，随 PR 一并提交。

### 场景 B：新增或修改平台特性用例 (Platform Features)
- **核心约束**：**采用“双重视角闭环”，严禁将基础功能与真实用户场景割裂为独立用例**（参见 [ADR-0001](docs/adr/0001-平台特性用例采用基础功能与用户场景双重视角闭环.md)）。
- **行动指引**：
  1. 查阅 [`docs/test-cases.md`](docs/test-cases.md) 获取特性规约；
  2. 工作流命名为 `e2e-feature-<能力>.yml`；
  3. 工作流内必须显式组织两类步骤：
     - `【基础功能】`：端口连通、响应头（MISS/HIT）、404 回退（`X-Cache-Tier`）、容灾降级；
     - `【用户视角场景】`：真实工程镜像源配置、强制 `--no-cache-dir` 绕过本地缓存、真实编译/安装专有核心依赖、Python/Rust 运行时自检。
  4. 同步登记到 [`.github/config/test_cases.json`](.github/config/test_cases.json)。

### 场景 C：跨集群调度与架构适配
- **行动指引**：
  1. 查阅 [`.github/config/runners.json`](.github/config/runners.json) 获取集群 Runner 的 `labels` 与 `arch`（amd64 / aarch64）；
  2. **PyTorch 用例铁律**：PyTorch `/whl/cpu` 仅支持 amd64，在 arm64 节点必须优雅跳过（参见 [ADR-0002](docs/adr/0002-pytorch-cpu用例仅限amd64架构测试并在arm64优雅跳过.md)），严禁因上游缺失而误报挂掉；
  3. 调度目标标签规范：`runs-on: ["<能力标签>", "<集群标签>"]`（AND 逻辑钉死目标集群）。

---

## 3. 核心设计原则与断言标准

### 两层测试分工体系
- **本仓库 (E2E)**：真实集群上端到端验证（调度、算力、缓存链路、网络、存储）；
- **上游仓库 (单测)**：[`ascend-ci-deployment`](https://github.com/opensourceways/ascend-ci-deployment) 验证静态配置语义（Nginx 回退链、ArgoCD lint），CI 每次 PR 自动运行。

### 断言分级标准 (必须显式声明)
1. **硬断言 (exit 1 阻断)**：核心能力本身（如 NPU 卡数、缓存头存在、依赖安装成功、二进制正确运行）；
2. **软断言 (阈值容差 / WARN)**：有合理波动的指标（如 CPU/内存配额达到 90%、首次 MISS 二次 HIT）；
3. **信息项 (`continue-on-error` 或 WARN-skip)**：探针类检查。**绝不把环境差异伪装成用例失败**。

### 确定性断言模式
验证缓存代理时，不得仅依赖 HTTP 状态码，必须断言关键响应头：
- `X-Cache-Tier`: 回源层级（`huaweicloud` / `ustc` / `nju` / `pypi-org` / `crates-official`）；
- `X-Crates-Cache` / `X-Rustup-Cache` / `X-Pypi-Cache`: 缓存命中状态；
- 头带 `always` 规则：在虚构资源（如 404）上依然会注入，构成了确定性的“回退链路存活”证明。

---

## 4. 关键避坑基线 (Gotchas)

编写或审查 Workflow 脚本前必须核对以下关键陷阱：
1. **容器内缺少 `hostname` 命令**：CANN 基础镜像无 `hostname`，获取节点名必须用 `$(cat /etc/hostname 2>/dev/null || uname -n)`；
2. **共享存储并发写冲突**：共享 SFS 目录下的测试文件必须按 pod 唯一（推荐 `${GITHUB_RUN_ID}-$$`），禁止固定文件名；
3. **国内网络直连 GitHub 超时**：IDC 容器内检出或拉取代码时，必须注入 `gh-proxy.test.osinfra.cn` 镜像代理；
4. **Schedule 定时触发无 inputs 参数**：定时任务中 `${{ inputs.xxx }}` 会渲染为空字符串，引用必须配置 fallback 环境变量（如 `${CACHE_HOST}`）；
5. **Upstream 配置未同步时走降级**：若上游 PR 已合入但 ArgoCD 尚未同步至集群，E2E 应当输出 Warning 降级跳过（注明 PR 编号），不得粗暴判负。

---

## 5. 工作流清单与运行入口

### 平台特性测试工作流 (Platform Features)
| 工作流 | 代理端口 / 协议 | 覆盖范围 / 关键断言 |
|---|---|---|
| [`e2e-feature-pypi-cache.yml`](.github/workflows/e2e-feature-pypi-cache.yml) | Port 80 (HTTP) | pip/uv 多源配置、triton-ascend 安装、PyTorch (amd64)、运行时 import、404 回源 |
| [`e2e-feature-apt-cache.yml`](.github/workflows/e2e-feature-apt-cache.yml) | Port 8081 (HTTP) | Ubuntu 真实 `apt-get update` & 安装构建依赖 `git`/`zstd`/`gcc`/`cmake` |
| [`e2e-feature-rustup-cache.yml`](.github/workflows/e2e-feature-rustup-cache.yml) | Port 8082 (HTTP) | rustup 极简稳定工具链真实下载安装与 `rustc`/`cargo` 可执行验证 |
| [`e2e-feature-yum-cache.yml`](.github/workflows/e2e-feature-yum-cache.yml) | Port 8083 (HTTP) | openEuler 真实 `dnf/yum makecache` & 安装构建依赖 `git`/`zstd` |
| [`e2e-feature-crates-cache.yml`](.github/workflows/e2e-feature-crates-cache.yml) | Port 8085 (HTTP) | Cargo sparse 镜像、真实工程拉取 `anyhow` 依赖并编译执行、MISS→HIT 缓存头 |

### 常用运行命令
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

# hk-001 A2(gy-001/wlcb-001) 的 nginx 真实客户场景（同一份 steps 用 matrix 跑两个 runner）
gh workflow run e2e-hk001-a2-1-nginx-cache.yml --repo ascend-gha-runners/test -f target=all
gh run watch <run-id> --repo ascend-gha-runners/test

# hk-001 / gy-001 / wlcb-001 三 runner 的 modelscope 模型缓存一致性
# （以 hk-001 为基线，基线≥10GiB 的 model 缺失即硬断言失败）
gh workflow run e2e-hk001-model-sync.yml --repo ascend-gha-runners/test -f target=all
```

> 说明：`e2e-hk001-model-sync.yml` 并发采集三个 runner 的 `~/.cache/modelscope/hub`
> 清单（三者挂的是各自集群独立 SFS Turbo，见
> `manifests/liqo/provider-*/ascend-gha-runners-hk-001/pvc.yaml`），再由 ubuntu job
> 以 hk-001 为基线比出 gy-001 / wlcb-001 缺失的模型（基线 <10GiB 直接忽略）。
> 扫描脚本 `scripts/scan_model_inventory.py`、比对脚本 `scripts/compare_model_inventory.py`。

> 说明：`e2e-hk001-a2-1-nginx-cache.yml` 用 `matrix`（gy001/wlcb001）复用同一份 steps。
> `wlcb001` 的 cache 回源不稳定（nginx error log `connect() failed (110: Connection
> timed out) while connecting to upstream`），matrix 项设 `degrade=true`：回源类场景
> 失败时 WARN 跳过（不阻塞），本地断言仍硬断言；`gy001` 一律硬断言。

### 测试质量看板 (GitHub Pages)
- **看板访问**：[https://ascend-gha-runners.github.io/test/](https://ascend-gha-runners.github.io/test/)
- **数据源与刷新**：由 [`update-report-pages.yml`](.github/workflows/update-report-pages.yml) 监听各 E2E 工作流完成事件，数据持久化保存在 `gh-pages` 分支的 `data/history.json`。

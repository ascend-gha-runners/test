# AGENTS.md — ascend-gha-runners/test 开发指导

本仓库承载 **runner / 集群基础设施的端到端(e2e)测试**。任何
[ascend-ci-deployment](https://github.com/opensourceways/ascend-ci-deployment)
的基础设施变更(runner、nginx cache、调度、存储等)**合入前必须先在这里
dispatch 对应 e2e,全绿后再合入**。

## 两层测试体系

| 层 | 位置 | 验证什么 |
|---|---|---|
| **e2e(本仓库)** | `.github/workflows/e2e-*.yml` | 真实集群上端到端:调度、算力、缓存链路、网络 |
| **单测(ascend-ci-deployment)** | `tests/`(pytest + shell) | 配置文件语义(nginx conf 回退链、YAML 规范、ArgoCD lint),CI 每次 PR 自动跑 |

e2e 发现的问题先定位根因,配置类修复落到 ascend-ci-deployment(单测同步补),
再回到本仓库回归。

## 新增 e2e 用例规范

### 命名与触发
- 文件名:`e2e-<集群>-<能力>.yml`,如 `e2e-gy006-nginx-cache.yml`
- 触发:`on: workflow_dispatch`(新文件必须先进 main 才能被 dispatch;
  main 无分支保护,小改动可直接推,正式变更走 PR)
- 只测明确目标,一个 workflow 一类能力,steps 内按断言分组

### 目标 runner 写法
```yaml
runs-on: ["<能力标签>", "gy-006"]   # 多标签 AND 匹配;gy-006 用于钉死集群
timeout-minutes: 90                  # 资源 Pending 时兜底,8 卡等稀缺资源适当放宽
container:
  image: swr.cn-southwest-2.myhuaweicloud.com/base_image/ascend-ci/cann:8.2.rc1.alpha003-910b-openeuler22.03-py3.11
```

### 断言分级(必须显式声明)
1. **硬断言**(exit 1):能力本身,如 NPU 卡数、缓存头存在、回退层级
2. **软断言**(阈值):有合理波动的值,如 CPU/内存配额按 90% 断言,并在
   WARN 时说明原因
3. **信息项**(`continue-on-error: true` 或 WARN-skip):探针类,如 yum 源
   格式差异;**绝不把环境差异伪装成用例失败**

### 确定性断言模式(缓存/代理类用例的标准做法)
客户端无法从状态码区分"哪个上游服务了请求",必须用响应头:
- `X-Cache-Tier`:pypi/crates 回源层级(huaweicloud/ustc/nju/pypi-org/crates-official)
- `X-Crates-Cache` / `X-Rustup-Cache` / `X-Pypi-Cache`:MISS→HIT 命中验证
- 头带 `always`,**最终 404/302 上也会出现**——bogus 资源 + 头 = 确定性回退证明
- 头未部署时(上游 PR 未合入):**WARN 跳过并在输出里注明 PR 编号**,不要 FAIL

### 已踩过的坑(写用例前先读)
1. **共享存储写测的文件名必须按 pod 唯一**(`$(hostname)` 不可用——CANN 镜像
   没有 hostname 命令;用 `${GITHUB_RUN_ID}-$$`):多 job 共享同一 SFS 目录,
   固定名会互相 touch/rm 踩踏
2. **CANN 镜像缺常用命令**:`hostname` 不存在;依赖前先 `command -v` 验证
3. **响应头命名避免撞 CDN**:Fastly 会透传自己的 `X-Served-By`,用
   `X-Cache-Tier` 这类带命名空间的名字
4. **sub_filter 类断言要求响应带正确 Content-Type**(application/json),
   mock/上游缺该头时 sub_filter 不生效
5. **python 内联在 bash 单引号里时不能用单引号字符**(转义或改双引号)
6. cargo sparse 协议要求 cargo ≥ 1.68

### 与 ascend-ci-deployment 的联动
- 基础设施 PR 描述里注明"合入前需跑 e2e:xxx"
- e2e 中引用上游 PR 编号(如 `ascend-ci-deployment#1672`)说明依赖关系
- 上游功能合入但未同步到集群(ArgoCD 延迟/静态 checksum)时,e2e 走降级
  而非失败,等部署完成后自然转硬断言

## 平台特性测试用例 (Platform Features)

对应官方文档 [Platform Features](https://ascend-gha-runners.github.io/docs/feature/)，每个特性均有独立 E2E 测试用例，采用真实项目（如 `vllm-project/vllm-ascend`）的实际使用方式，并基于 `.github/config/runners.json` 支持全 13 集群矩阵调度：

| 特性 | 工作流文件 | 代理端口 / 协议 | 覆盖范围 / 关键断言 |
|---|---|---|---|
| **PyPI Cache** | `e2e-feature-pypi-cache.yml` | Port 80 (HTTP) | pip/uv 真实安装、`/whl/cpu` 真实安装 PyTorch、404 回源至 pypi.org |
| **APT Cache** | `e2e-feature-apt-cache.yml` | Port 8081 (HTTP) | Ubuntu 真实 `apt-get update` & 安装构建依赖 `git`/`zstd`/`gcc`/`cmake` |
| **Rust / rustup** | `e2e-feature-rustup-cache.yml` | Port 8082 (HTTP) | rustup 极简稳定工具链真实下载安装与 `rustc`/`cargo` 可执行验证 |
| **YUM / DNF Cache** | `e2e-feature-yum-cache.yml` | Port 8083 (HTTP) | openEuler 真实 `dnf/yum makecache` & 安装构建依赖 `git`/`zstd` |
| **crates.io Cache**| `e2e-feature-crates-cache.yml` | Port 8085 (HTTP) | Cargo sparse 镜像、真实工程拉取 `anyhow` 依赖并编译执行、MISS→HIT 缓存头 |

## 运行方式

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

# 手动触发测试报告页面数据刷新 (日常无需执行，测试完成后自动触发)
gh workflow run update-report-pages.yml     --repo ascend-gha-runners/test
```

## 测试质量报告看板 (GitHub Pages)
- 看板访问: https://ascend-gha-runners.github.io/test/
- 触发方式: 任意 `e2e-*` 工作流 completed 时由 `update-report-pages.yml` 自动触发。
- 数据持久化: 增量测试流水保存在 `gh-pages` 分支的 `data/history.json`，不会因 CI 日志过期而丢失。

## 历史 workflow
`test_npu.yaml` / `test-action-path.yml` / `test_secret_upload.yml` 为早期
手工测试,保留作参考,新用例不要模仿其结构。


# ARC Controller 升级 SOP

## 0. 术语表

本文使用下表中的术语。每个术语在全文中含义唯一。

| 术语 | 含义 |
| --- | --- |
| Controller | GitHub Actions Runner Controller 的控制器组件。Deployment 名称格式：`<release>-gha-rs-controller` |
| Listener | 每个 Runner Scale Set 对应一个的事件监听 Pod，由 Controller 创建 |
| Runner Scale Set | 一组 runner 对应的 Helm release。示例：`linux-aarch64-a2-1-gy006` |
| 包装 chart | 配置仓库中包含上游依赖 chart 的目录。示例：`manifests/arc-controller-0.15.0` |
| 配置仓库 | `opensourceways/ascend-ci-deployment`（GitOps 仓库） |
| NJU 镜像 | `ghcr.nju.edu.cn`。上游 `ghcr.io` 的 pull-through 镜像 |
| SWR | 华为云镜像仓库。本文使用 `swr.cn-southwest-2.myhuaweicloud.com` |
| 基线 | 升级前采集的 Listener 名称与状态清单。验收时用作对照 |
| 金丝雀 | 每批升级的第一个集群。承担验证任务 |

## 1. 目的

本文规定 Controller 与 Listener 的升级操作。

所有操作通过 GitOps 执行：变更进入配置仓库，ArgoCD 完成同步。

此流程给出三个保证：
- 每个变更经评审后合入。
- 每个变更对应一个 commit，可追溯。
- 回退操作是 revert，不在集群上手工修改。

## 2. 适用范围

本文适用于所有安装 ARC 的集群。

每批升级按此顺序执行：
1. 金丝雀：1 个集群。
2. 灰度：2~3 个集群。
3. 全量：其余集群。

规则：上一批验收全部通过后，才开始下一批。禁止一次升级全部集群。

### 2.1 占位符

执行命令前，用下表的值替换占位符：

| 占位符 | 含义 | 获取方式 |
| --- | --- | --- |
| `<KUBECONFIG>` | 目标集群的 kubeconfig 文件路径 | 集群清单 |
| `<TARGET_VERSION>` | 目标版本。示例：`0.15.0` | 升级计划 |
| `<RELEASE>` | Controller 的 Helm release 名。当前值为 `arc` | ArgoCD Application 的 `spec.source.helm.releaseName` 字段 |
| `<CONTROLLER_NAMESPACE>` | Controller 的命名空间。当前值为 `arc-systems` | ArgoCD Application 的 `spec.destination.namespace` 字段 |

## 3. 版本关联关系

升级前阅读本节。本节回答一个问题：Controller 版本变化时，哪些对象跟随变化。

| 对象 | 是否跟随 Controller 版本 | 说明 |
| --- | --- | --- |
| Controller Deployment（`<release>-gha-rs-controller`） | 是 | 镜像由包装 chart 的 values 控制 |
| Listener | 是（自动） | Controller 按 Runner Scale Set 的 `listenerTemplate` 创建。`listenerTemplate` 未指定 image 时，Listener 镜像 = Controller 镜像 |
| Controller chart 自带的 RBAC 与 Service | 是（随 chart） | 不需要单独操作 |
| CRD（`actions.github.com/*` 共 4 个） | 需单独确认 | 随 chart 的 `crds/` 目录分发。ArgoCD 随 chart 同步应用 |
| Runner Scale Set（`gha-runner-scale-set` chart） | 否 | 版本独立。要求：Runner Scale Set 版本 = Controller 版本 |
| Runner 镜像（runner pod 使用） | 否 | 由 Runner Scale Set 的 values 显式指定 |

规则：
- Runner Scale Set 的 `listenerTemplate` 未指定 image：升级 Controller 时，Listener 自动跟随。不需要修改 Listener。
- Runner Scale Set 的 `listenerTemplate` 指定了 image：在同一 PR 内修改该 image，或删除该 image 声明。否则 Listener 不跟随，验收 §4.2 失败。
- 保持 Runner Scale Set 版本 = Controller 版本。混用版本时，先获得评审记录。

### 3.1 镜像与 chart 来源

集群节点不从官方 `ghcr.io` 拉取镜像。升级前，按下表确认每个对象的来源可用。

| 镜像 | 来源 | 升级前动作 |
| --- | --- | --- |
| Controller / Listener（标准路径） | NJU 镜像：`ghcr.nju.edu.cn/actions/gha-runner-scale-set-controller:<tag>` | 执行 `docker manifest inspect ghcr.nju.edu.cn/actions/gha-runner-scale-set-controller:<tag>`。输出包含 manifest = 可用 |
| Controller / Listener（例外路径） | SWR：`.../modelfoundry/gha-runner-scale-set-controller:0.14.201` | 仅适用于 0.14.x 补丁版本升级。升级到 0.15.0+：使用标准路径。原因见下文决策记录 |
| Runner 基础镜像 | SWR：`.../base_image/actions/actions-runner:<ver>`，由配置仓库的 `sync-images.yml` 每日同步 | 在该 workflow 的 `matrix.runner_version` 中加入目标版本。合入后确认 SWR 出现该 tag |
| Runner hooks 镜像 | SWR：`.../modelfoundry/runner-containers-hooks:<tag>` | 与 Controller 升级无关。仅 runner 镜像变更时操作 |
| 业务 AI 镜像 | SWR：`.../base_image/ascend-ci/<project>/...`，由 `ascend-gha-runners/sync-tools` 每小时同步 | 无操作 |

事实说明（每条一个事实）：
- NJU 是上游的 pull-through 镜像。上游发布后，NJU 自动获得该版本。存在同步延迟。
- 配置仓库 `manifests/arc-controller-*/values.yaml` 的默认 repository 是 NJU。未覆盖 repository 的集群直接使用 NJU。此类集群示例：hk001、huabei-003、hb-003-verl。
- `modelfoundry` 镜像是定制构建，无自动同步链路。原因见历史注记。
- 决策记录（2026-10-18）：`0.14.201` 为 liqo 适配定制构建。其改动不需要进入 0.15.0+。升级到 0.15.0+ 时使用 NJU 上游镜像；仅升级 0.14.x 补丁版本时需要 modelfoundry 构建。

注意：`sync-images.yml` 的 `sync-helm` job 已被注释（PR opensourceways/ascend-ci-deployment#240）。其目标路径 `base_image/actions/gha-runner-scale-set-controller` 从未生效。2026-10-18 探测结果：cn-southwest-2 与 cn-east-4 两个 region 均不存在该路径。不要使用该路径。

Helm chart 的拉取发生在 ArgoCD 与 CI 侧，不经过集群节点。

Helm chart 依赖源规则：
- 标准源：NJU 镜像 `oci://ghcr.nju.edu.cn/actions/actions-runner-controller-charts`。新增 chart 与版本升级一律使用此源。
- 回退源：官方 `oci://ghcr.io/actions/actions-runner-controller-charts`。仅 NJU 未同步目标版本且升级紧急时使用。使用时在 PR 描述记录原因。
- 升级前确认目标版本在所选源上可得（NJU 存在同步延迟）。验证命令：`docker manifest inspect ghcr.nju.edu.cn/actions/actions-runner-controller-charts/<chart 名>:<version>`。示例：`.../gha-runner-scale-set:0.15.0`（Runner Scale Set chart）、`.../gha-runner-scale-set-controller:0.15.0`（Controller chart）。两个制品的 0.15.0 版本已验证可得（2026-10-18）。

### 3.2 0.15.0 行为变化（0.14.x → 0.15.0+ 升级适用）

首次升级到 0.15.0 时，按下表核对。

依据：官方 changelog（release `gha-runner-scale-set-0.15.0`，2026-10-01）、CRD schema 逐字段对比（0.14.2 vs 0.15.0）、gy006 金丝雀验证（opensourceways/ascend-ci-deployment#2030）、arc-karmada-test 运行报告。

| 变化（上游 PR） | 行为 | 需要的操作 |
| --- | --- | --- |
| 原地升级取代旧升级策略（#4516，breaking） | Runner spec 变更不再立即重建 Listener 与 EphemeralRunnerSet。顺序变为：关停 Runner Scale Set → 等待空闲 runner 退出 → 滚动更新。忙碌 runner 不被打断 | 无需配置。Controller values 中旧的 `flags.updateStrategy` 会被忽略，删除它 |
| Runner 计数改走 metrics（#4557/#4583） | AutoscalingRunnerSet 与 EphemeralRunnerSet 的 status 计数字段被移除。kubectl printer columns 被移除 | 看板或告警读取 CR status 计数时，改为读取 controller metrics。`metrics:` 对象的配置方式不变 |
| Listener 漂移检测改为直接比较 Pod（#4635/#4680/#4695） | Listener Pod 的比较对象从 hash 变为 Pod 本身 | 验收 §4.2 时增加观察：listener 无重建循环（日志无持续 patch） |
| chart 渲染期校验注解与标签（#4637/#4639/#4640/#4679） | values 中的注解值必须为字符串。裸整数或布尔导致渲染失败 | 升级前核对 Runner Scale Set values 的 annotations。gy006 已核对：全部合规 |
| controller flag 变更（#4670） | `runnerMaxConcurrentReconciles` 与 `updateStrategy` 被移除。替代物为 per-controller 默认值（2/2/2/4） | 删除包装 chart values 中的这两个 flag。gy006 已删除 |
| CRD schema | EphemeralRunner、EphemeralRunnerSet、AutoscalingRunnerSet 的 spec 与 status 无字段移除。`ephemeralrunners` 的 `.status.phase` 与 `ephemeralrunnersets` 的 `.spec.ephemeralRunnerSpec` 保持兼容 | zombie-runner-cleanup 与 monitoring cronjob 不受影响。CRD 由 ArgoCD 随 chart 应用（ServerSideApply） |

## 4. 标准流程（四步）

```
1. 前置确认：确认版本。采集 Listener 基线。完成检查清单。
2. 修改：在配置仓库建分支、改文件、执行本地检查、提 PR。
3. 同步：PR 合入后，ArgoCD 自动同步。
4. 验收：逐项核对。全部通过后填写 §7 记录表。
```

### 步骤 1：前置确认

#### 1.1 确认版本

记录下表两个值：

| 项 | 获取方式 |
| --- | --- |
| 目标版本 | 升级计划。填入 `<TARGET_VERSION>` |
| 当前 Controller 版本 | 执行下方命令，记录输出 |

```sh
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get deploy <release>-gha-rs-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

#### 1.2 采集 Listener 基线

执行下列命令。将输出保存到文件 `baseline-<CLUSTER>-<YYYYMMDD>.txt`。此文件是验收 §4.2 的对照输入。

```sh
# Listener 清单
kubectl --kubeconfig=<KUBECONFIG> get autoscalinglisteners -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name

# Listener Pod：名称、就绪状态、镜像
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image \
  | grep -- '-listener'
```

检查基线输出。发现以下情况时，先处理再升级：
- 存在无对应 Runner Scale Set 的 Listener（孤儿）。记录它，验收时排除。
- 存在 READY=false 的 Listener Pod。记录原因。

#### 1.3 前置检查清单

逐项执行并勾选。任何一项不通过，停止升级。

- [ ] 确认 Controller / Listener 镜像可用：执行 3.1 的 `docker manifest inspect` 命令。输出包含 manifest 即通过。
- [ ] 确认 Runner 基础镜像可用（仅 runner 镜像变更时）：`sync-images.yml` 的 matrix 包含目标版本，且 SWR 出现对应 tag。
- [ ] 确认 chart 可得（仅 chart 版本变更时）：所选依赖源包含目标版本。
- [ ] 确认包装 chart 存在：`ls manifests/arc-controller-<TARGET_VERSION>/Chart.yaml` 输出该文件。
- [ ] 对比 CRD：对比当前版本与目标版本的 `crds/` 目录。规则：无字段删除即通过。有字段删除时，评审后决定。
- [ ] 核对 RBAC：对比目标版本 chart 生成的 RBAC。新增权限时，写入 PR 描述，等待评审。
- [ ] 对齐 Runner Scale Set 版本：目标集群全部 Runner Scale Set chart 的依赖版本 = 目标版本。
- [ ] 核对 0.15.0 行为变化（首次跨越 0.15.0 时）：完成 3.2 的三项操作——删除被移除的 flag；核对 values 注解值全部为字符串；确认无监控读取已移除的 status 计数字段。
- [ ] 确认集群无运行中作业。原因见 6.5。
- [ ] 记录回退点：保存配置仓库当前 main 分支的 commit SHA。

### 步骤 2：修改（提 PR）

在配置仓库操作。禁止直接操作集群。

#### 2.1 修改 Controller 版本

按版本控制方式选择一种：
- 方式 A（chart 路径控制）：修改 Application 的 `spec.source.path`，指向目标版本目录。
- 方式 B（镜像 tag 控制）：修改 values 中的 `image.repository` 与 `image.tag`。

Listener 镜像固定的 Runner Scale Set：优先删除 `listenerTemplate` 中的 image 声明，使 Listener 跟随 Controller 镜像。必须保留时，将 image 改为目标版本。

示例（方式 A）：

```yaml
spec:
  source:
    path: manifests/arc-controller-<TARGET_VERSION>   # 原为上一版本目录
```

#### 2.2 对齐 Runner Scale Set 版本

修改每个 Runner Scale Set 的 `Chart.yaml` 依赖版本：

```yaml
dependencies:
  - name: gha-runner-scale-set
    version: <TARGET_VERSION>
```

仓库提交 `Chart.lock` 时，重新生成或删除该文件。过期的 `Chart.lock` 会使 Helm dependency build 失败。

#### 2.3 校验与合入

按顺序执行：
1. 运行 yamllint。零 error 通过。
2. 运行 Application 静态检查。检查项：`spec.source.path` 存在、`prune: true`、`metadata.name` 与文件名对应。
3. 提 PR。PR 描述包含：变更范围、依据、回滚方式。
4. CI 通过，评审通过后合入。

### 步骤 3：等待同步

1. PR 合入后，ArgoCD 开始自动同步。预期 5 分钟内完成。
2. 执行：

```sh
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> rollout status deploy/<release>-gha-rs-controller --timeout=300s
```

输出包含 `successfully rolled out` = Controller 更新完成。

3. 10 分钟后 Application 未达到 `Synced / Healthy`：检查 ArgoCD 事件与 controller 日志，定位原因。不要重复手动触发 sync。

### 步骤 4：验收

逐项核对。全部通过后填写 §7 记录表。

#### 4.1 Controller

```sh
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get deploy <release>-gha-rs-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods | grep <release>-gha-rs-controller
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> logs deploy/<release>-gha-rs-controller --tail=200 \
  | grep -iE 'error|panic|forbidden'
```

判定标准：
- [ ] Pod 状态 `READY 1/1`，无 CrashLoop。
- [ ] 镜像输出 = `<TARGET_VERSION>` 对应的镜像。
- [ ] 日志命令输出为空（无 error / panic / forbidden）。

#### 4.2 Listener（对照 §1.2 基线）

```sh
kubectl --kubeconfig=<KUBECONFIG> get autoscalinglisteners -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image \
  | grep -- '-listener'
```

判定标准：
- [ ] Listener 名称与数量 = 基线（已排除的孤儿除外）。
- [ ] 每个 Listener `READY=true` 且 Running。
- [ ] 每个 Listener 镜像 = `<TARGET_VERSION>` 对应的镜像。
- [ ] 首次升级到 0.15.0 时：观察 10 分钟，listener 无重建循环（logs 无持续 patch）。

#### 4.3 功能冒烟

- [ ] 触发一个最小 workflow。预期：runner pod 创建，作业完成。
- [ ] gy006 集群：执行 `e2e-gy006-a2-runner-smoke.yml`。

#### 4.4 关联资源

- [ ] CRD 版本 = 目标版本（仅跨 chart 版本升级时）。核对命令：`kubectl get crd | grep actions.github.com`，对比 CRD 的 creationTimestamp 是否更新。
- [ ] 新增 RBAC 与 PR 描述一致。

## 5. 回滚

按顺序执行：
1. 在配置仓库对升级 commit 执行 `git revert <commit> --no-edit`。
2. 通过 PR 合入 revert。默认分支禁止直接 push。
3. ArgoCD 同步后，controller 回到上一版本。预期 5 分钟内完成。
4. 对回滚结果执行 §4.1 与 §4.2 验收。

注意：revert 不回退 CRD。CRD 已升级时，先评估旧版本 controller 与新 CRD 的兼容性，再决定是否手动回退 CRD。

## 6. 风险与注意事项

每条按“风险 → 条件 → 对策”描述。

1. **版本不一致**。条件：Runner Scale Set 版本 ≠ Controller 版本。对策：获得评审记录后再升级。
2. **Listener 镜像固定**。条件：`listenerTemplate` 指定了 image。对策：同一 PR 内删除或修改该 image。否则验收 §4.2 失败。
3. **CRD 升级**。条件：跨 chart 版本升级。对策：ArgoCD 随 chart 应用 CRD；验收 §4.4 核对。手动修改过 CRD 时，先评估兼容性。
4. **RBAC 变更**。条件：目标版本附带新增权限。对策：写入 PR 描述，评审通过后升级。
5. **升级窗口的行为**。
   - 0.14.x：升级瞬间暂停新 runner 注册。对策：确认集群无运行中作业后升级。
   - 0.15.0+：原地升级语义（#4516，见 3.2）。空闲 runner 退出后滚动，忙碌 runner 不被打断。对策：避开业务高峰。
6. **分批**。禁止一次升级全部集群。每批验收通过后进入下一批。

## 7. 记录表

| 集群 | 升级前版本 | 目标版本 | Controller 就绪 | Listener 全起 | 冒烟 | 回滚 | 执行人 | 时间 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |

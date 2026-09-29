# ARC Controller 升级 SOP

## 1. 目的

规范各集群 GitHub Actions Runner Controller（ARC，`gha-runner-scale-set`）的 Controller / Listener 升级操作，统一走 GitOps 流程，做到可评审、可追溯、可快速回退。

## 2. 适用范围

所有使用 ARC 的集群。单次变更建议按「金丝雀（1 个非关键集群）→ 灰度（2~3 个）→ 全量」分批推进，每批验收通过后再进入下一批。

以下命令中的占位符在实例化时替换：

- `<KUBECONFIG>`：目标集群 kubeconfig
- `<TARGET_VERSION>`：目标版本，如 `0.14.2`
- `<RELEASE>`：Controller 的 Helm release 名（通常为 `arc`）
- `<CONTROLLER_NAMESPACE>`：Controller 命名空间（通常为 `arc-systems`）

## 3. 版本关联关系

理解清楚「什么随 Controller 版本变化」是避免漏升级 / 误升级的前提：

| 对象 | 说明 | 是否随 Controller 版本 |
| --- | --- | --- |
| Controller Deployment | `<release>-gha-rs-controller` | 是 |
| Listener | 由 Controller 运行时按 Runner Scale Set 的 `listenerTemplate` 创建；未显式指定镜像时，**镜像 = Controller 镜像** | 是（自动） |
| Controller chart 自带 RBAC / Service | 随 chart | 随 chart |
| CRD（`actions.github.com/*`） | 随 chart `crds/` | 需单独确认是否更新 |
| Runner Scale Set（`gha-runner-scale-set` chart） | 独立版本 | 否 |
| Runner 镜像 | 独立 | 否 |

结论：

- 只要集群内 Runner Scale Set 的 `listenerTemplate` 未硬编码镜像，**升级 Controller 会同时把 Listener 升级到同一版本**，无需单独改 Listener。
- 若某集群显式钉死了 Listener 镜像，必须一并处理，否则 Listener 不会跟随。
- 版本一致性：仓库规范要求 Runner Scale Set 版本与 Controller 版本一致；本 SOP 将其作为独立校验项。

## 4. 标准四步流程

```
1. 前置确认  ── 版本 + 集群当前 running 的 listener 基线
2. 提 PR     ── 修改 Controller（Listener 随动）版本
3. 等同步    ── ArgoCD 自动 sync
4. 验收      ── Controller running + 原 listener 全部正常拉起
```

### 步骤 1：前置确认

#### 1.1 确认版本

| 项 | 值 |
| --- | --- |
| 目标版本 | `<TARGET_VERSION>` |
| 当前 Controller 版本 | 由集群现状采集（见下） |

#### 1.2 采集 Listener 基线（升级前，务必存档）

```sh
# Controller 现状
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get deploy <release>-gha-rs-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

# Listener 基线：名称、状态、镜像
kubectl --kubeconfig=<KUBECONFIG> get autoscalinglisteners -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image \
  | grep -- '-listener'
```

将 Listener 名称清单存档，作为步骤 4 的对照基线。

#### 1.3 前置检查清单

- [ ] 目标镜像 `<TARGET_VERSION>` 已同步到所用镜像仓库，且支持集群所需架构（amd64 / aarch64）
- [ ] 目标 chart / manifest 已存在于配置仓库
- [ ] 已确认 CRD 从当前版本到目标版本无破坏性变更
- [ ] 已确认升级附带的 RBAC / 其他资源变化（新增权限需评审接受）
- [ ] 已确认 Runner Scale Set 版本是否需要同步对齐
- [ ] 目标集群无大批 CI 任务在执行，已通知使用方
- [ ] 已记录回退点（当前主干 commit）

### 步骤 2：提 PR

在配置仓库（GitOps 仓库）中修改，**不直接操作集群**。

#### 2.1 修改 Controller 版本

- 若版本由 chart 路径切换控制：修改对应 Application 的 `spec.source.path` 指向目标版本目录。
- 若版本由值控制：修改 `image.tag` 或依赖版本。
- Listener：`listenerTemplate` 未硬编码镜像时无需改动；有硬编码时一并去钉死或对齐目标版本。

示例（chart 路径切换）：

```yaml
spec:
  source:
    path: manifests/arc-controller-<TARGET_VERSION>   # 原为上一版本目录
```

#### 2.2 Runner Scale Set 版本对齐（如适用）

对需要对齐的 Runner Scale Set，修改其 `Chart.yaml` 中的依赖版本：

```yaml
dependencies:
  - name: gha-runner-scale-set
    version: <TARGET_VERSION>
```

改动后按仓库约定更新依赖锁文件（若仓库提交 `Chart.lock`）。

#### 2.3 校验与提交

- YAML 格式检查（yamllint 等）
- Application 静态检查：`spec.source.path` 存在、前缀合法、`prune: true`、`metadata.name` 与文件名对应
- 提 PR → CI 通过 → reviewer 合入主干

### 步骤 3：等待同步

- GitOps（如 ArgoCD）在合入后自动同步（通常数分钟）。
- 确认对应 Application 变为 `Synced / Healthy` 后进入验收。

### 步骤 4：验收（逐项勾选，全部通过才算完成）

#### 4.1 Controller

```sh
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get deploy <release>-gha-rs-controller \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods | grep <release>-gha-rs-controller
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> logs deploy/<release>-gha-rs-controller --tail=200 | grep -iE 'error|panic|reconcile'
```

- [ ] Controller Pod `READY 1/1`、无 CrashLoop
- [ ] Controller 镜像为目标版本 `<TARGET_VERSION>`
- [ ] Controller 日志无持续 `error` / `panic` / `forbidden`

#### 4.2 Listener（对照 1.2 基线）

```sh
kubectl --kubeconfig=<KUBECONFIG> get autoscalinglisteners -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name
kubectl --kubeconfig=<KUBECONFIG> -n <CONTROLLER_NAMESPACE> get pods \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image \
  | grep -- '-listener'
```

- [ ] 基线中的 Listener 名称、数量全部一致（无缺失、无多余）
- [ ] 每个 Listener 均 `READY=true` 且 Running
- [ ] 每个 Listener 镜像为目标版本 `<TARGET_VERSION>`

#### 4.3 功能冒烟

- [ ] 触发一个最小 workflow，runner 成功拉起并完成

#### 4.4 关联资源

- [ ] CRD（`actions.github.com/*`）已更新到目标版本对应版本（或确认无需更新）
- [ ] 升级附带的新增 RBAC / 资源符合预期

## 5. 回滚

GitOps 回滚以 revert 为准：

```sh
git revert <本批 commit> --no-edit && git push origin HEAD:main
```

- 同步回上一版本（通常数分钟）。
- Controller 单副本 + `updateStrategy: immediate` 时，切换瞬间 Listener 会重建，暂停新 runner 注册。
- 若手动改过 CRD，需另行评估回退（CRD 变更属高风险，通常不可简单回滚）。

## 6. 风险与注意事项

1. **版本一致性**：Runner Scale Set 与 Controller 版本应保持一致，混用需评审确认兼容性。
2. **Listener 硬编码镜像**：若 Runner Scale Set 显式钉死 Listener 镜像，Controller 升级不会带动 Listener，必须一并处理。
3. **CRD 升级**：Helm / ArgoCD 对 chart `crds/` 的升级行为需实测确认；必要时手动 apply。
4. **RBAC 新增**：升级可能附带新增集群级权限，需安全评审。
5. **短中断**：单副本 Controller 升级瞬间暂停新 runner 注册，建议安排低峰。
6. 每批升级间隔观察，禁止一次性全量。

## 7. 记录表

| 集群 | 升级前版本 | 目标版本 | Controller 就绪 | Listener 全起 | 冒烟 | 回滚 | 执行人 | 时间 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |

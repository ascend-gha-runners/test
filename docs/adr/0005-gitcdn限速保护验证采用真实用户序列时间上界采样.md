# 0005 — git-cdn 限速保护验证采用真实用户序列时间上界采样

- 状态：提议中
- 关联：`../test-cases.md`（TC-FEAT-GITCDN），`../../.github/workflows/e2e-feature-git-cdn.yml`
- 背景：[opensourceways/backlog#2885](https://github.com/opensourceways/backlog/issues/2885)、vllm-project/vllm-ascend run 41602861604、ascend-ci-deployment `422793a8c`

## 背景与决策

incident 中 actions/checkout 的按 SHA fetch 在 git-cdn 限速流上悬挂约 4 小时（客户端 gitconfig
显式禁用了低速中断：`lowSpeedLimit=0, lowSpeedTime=999999`），修复落在 git-cdn 服务端
（回源 gitconfig 注入 `http.lowSpeed*`，限速流 60s 快失败并交给既有 backoff 重试）。该故障的
触发条件是上游 gh-proxy/github 的随机限速，属于低概率随机事件，且生产 git-cdn 与上游链路
不允许从测试侧做故障注入（沿用 ADR-0004"不主动故障注入生产"的边界）。

据此决策：

1. **不做生产端故障注入**：不修改 git-cdn 配置、不模拟限速上游；E2E 只验证真实用户行为。
2. **以真实用户 git 序列复现 incident 操作形态**：ls-remote → 全量 clone（对标
   fetch-depth: 0）→ 每轮取最新分支 SHA 后按 SHA fetch。最新 SHA 大概率不在 git-cdn
   pack 缓存内，会触发回源，即被测保护路径；这是不注入故障前提下能达到的最强确定性。
3. **以"时间上界 + 故障签名"作硬断言**：每条 git 命令套 `timeout` 上界（超时 exit 124 即判负），
   并匹配 incident 实际出现过的故障签名（`Could not resolve HEAD to a revision` 等）。修复前
   该场景为小时级悬挂，上界秒级/分钟级即可区分两种形态，不需要感知 git-cdn 内部实现。
4. **故障率结论由统计而非单次运行给出**：工作流每天 4 个时段（覆盖业务高峰）× 13 集群定时采样，
   每集群多轮迭代；单次全绿不证明修复生效，"随机限速故障率接近 0"由看板跨运行聚合的长期趋势承担。

## 证据与范围

- 集群覆盖口径：runners.json 登记的全部集群（每集群择一 Runner，优先 CPU；同集群 cpu/npu
  共享同一套集群级 git-cdn 实例，择一即可全覆盖）。被测端点固定为集群内 DNS
  `git-cdn-service.git-cdn.svc.cluster.local:8000`，sh-001/sh-002 未部署的 gitcode 实例
  （`git-cdn-gitcode`）与 gy-001/wlcb-001 的 ExternalName 别名一律记探针，不判负、不推断缺失。
- 流量路径证据链：用例以 GIT_CONFIG_GLOBAL 注入与生产 configmap postStart 完全一致的
  `https://github.com/` -> git-cdn insteadOf 重写并自检生效（硬）、clone 日志含 git-cdn 302 规范化
  重定向（硬）、info/refs smart 协议 advertisement（硬）——三者共同证明采样确实经过 git-cdn，
  避免直连上游时"全绿但没测到"的假阳性。不依赖 ambient global config：k8s 容器模式作业 HOME
  未必是 /root（runner 镜像普遍为 /github/home，重写只落在 /root/.gitconfig），ambient 重写是否
  生效因 fleet 而异，仅记探针（首次全量 dispatch 13/13 集群因此失败后修正）。
- 软阈值（单次 fetch-by-SHA 耗时，默认 120s）只承担趋势观察：冷回源大 pack 与缓存命中之间存在
  数量级波动，超过软阈值仅 WARN，不伪装成硬失败。
- 定时采样以 CANN 容器（root / HOME=/root，与 vllm-ascend CI 同款镜像）在 Runner Pod 内执行：
  pod template 的 postStart 钩子向 /root/.gitconfig 追加的 git-cdn 重写在容器内即生产等价生效；
  客户端重写目标与 incident 一致，GIT_CONFIG_GLOBAL 注入仍保留为跨 fleet 确定性兜底（不指定
  镜像时作业落到 HOME=/home/runner 的 runner 镜像，ambient 注入不可见——首次全量 dispatch 即踩中）。
  不在本轮引入 ArgoCD revision 门禁或部署等待作为验收前提。
- 需要验证 git-cdn 配置渲染正确性时，由 ascend-ci-deployment 的守护单测
  （`tests/test_gitcdn_lowspeed_gitconfig.py`）承担；本仓库不重复断言服务端内部配置。

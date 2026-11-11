# 0008 — 真实 actions/checkout 形态验证 git-cdn 检出慢速卡死

- 状态：提议中
- 关联：`../test-cases.md`（TC-FEAT-GITCDN-CHECKOUT），`../../.github/workflows/e2e-feature-git-cdn-checkout.yml`，[ADR-0005](0005-gitcdn限速保护验证采用真实用户序列时间上界采样.md)
- 背景：[opensourceways/backlog#2885](https://github.com/opensourceways/backlog/issues/2885)、vllm-project/vllm-ascend run 42972775223（job 130366214428）、incident run 41602861604

## 背景与决策

incident 的实际调用者是 vllm-ascend CI 里的 actions/checkout：按 commit SHA 全量 fetch 在 git-cdn
限速流上悬挂约 4 小时。ADR-0005 用手写 git 命令序列复刻操作形态，但 checkout action 自身的
客户端行为（内部 fetch 命令序列、ref 解析、submodule 递归、workspace 处理）与手写序列仍存在差距；
"checkout 经 git-cdn 的真实效果"需要以**真实 action 调用形态**验证。参考形态取自
vllm-ascend `_selected_tests.yaml` 的 cpu-ut job（run 42972775223 实测：vllm 检出 12s、
vllm-ascend 检出 45s）。

据此决策新增机制 TC-FEAT-GITCDN-CHECKOUT：

1. **完全对标生产 checkout 形态**：`actions/checkout@v7` 双检出——vllm 主仓
   （repository + ref + path，默认浅 fetch）+ vllm-ascend（`ref=<commit SHA>` + `fetch-depth: 0`
   + `submodules: recursive`）。按 SHA 全量 fetch 即 incident 悬挂形态；submodule（catlass）
   来自 gitcode.com 直连不经 git-cdn，与生产行为一致。
2. **轮次可控 + 仅手动触发**：dispatch 参数 iterations（1-5）动态生成 matrix，每轮一个独立 job、
   每轮冷检出（self-hosted runner 默认不清 workspace，轮开始强制清空，保证轮间形态一致）；
   固定 hk-001 的 `linux-amd64-cpu-2` 双标签（runners.json 硬校验，不得编造标签）。
3. **判定语义（判负卡死，不判负限速）**：判负对象是"卡死"而非"限速"——单轮总耗时超上界（默认 900s，
   dispatch 可调 120-1800）判 `timeout_hang`；checkout 失败判 `checkout_failed`；轮被工作流级
   40min 上界强杀（artifact 缺失）判 `MISSING`，三者均判负。有界慢速（超软阈值 300s 但在上界内）
   仅 WARN 记趋势。job 级 40min 硬上界 + step 级超时保证作业永不悬挂。
4. **分项计时对标生产基线**：每轮分别记录 vllm / vllm-ascend 检出耗时（生产实测 12s / 45s），
   report 汇总输出轮次耗时表 + ASCII 条形 + 与 incident 基线（小时级）对比。

## 证据与范围

- 流量路径证据链与 ADR-0005 一致：GIT_CONFIG_GLOBAL 注入与生产 configmap postStart 完全一致的
  insteadOf 重写并自检生效（硬），setup 步真实 git 客户端 ls-remote 含 git-cdn 302 重定向证据
  （硬）。CANN 容器（root / HOME=/root）下 pod postStart 的 ambient 注入亦可生效，仅记探针。
- 单集群手动触发定位为**人工验证入口**（ADR-0005 的 13 集群定时采样 + 看板统计承担长期收敛；
  本用例提供"指定 ref、指定轮次、单集群深查"的能力，如 incident 复盘/修复灰度验证）。
- MISSING 轮的处理：轮 job 被强杀时其 result artifact 缺失，report 按"卡死判负"处理——
  与"作业永不悬挂"的工作流级上界自洽。
- 客户端视角限制与 ADR-0005 一致：git-cdn 内部重试与上游请求次数不可直接观测，慢速卡死的判定以
  耗时上界形态给出。

# 0007 — git-cdn 限速快失败重试语义采用并发客户端时间线有界采样

- 状态：提议中
- 关联：`../test-cases.md`（TC-FEAT-GITCDN-FAILFAST），`../../.github/workflows/e2e-feature-git-cdn-failfast.yml`，[ADR-0005](0005-gitcdn限速保护验证采用真实用户序列时间上界采样.md)，[ADR-0006](0006-受控限速上游验证git传输自保护语义.md)
- 背景：[opensourceways/backlog#2885](https://github.com/opensourceways/backlog/issues/2885)、ascend-ci-deployment `422793a8c`

## 背景与决策

ADR-0005 用例的判定语义是"无签名全绿"：任何限速故障签名都判负。该语义在**正常时段健康采样**上是对的，
但对 backlog#2885 的验收语义（"限速时快失败重试，而不是卡住"）存在盲区——如果限速真的发生且被
git-cdn 正确吸收（回源 git 子进程 ~60s 中止 → repo_cache backoff 重试 → 有界失败或自愈成功），
ADR-0005 用例只会看到一个带签名的失败并判负，无法区分"限速被自愈"与"卡死悬挂"两种形态。
生产上游限速不可注入（ADR-0004/0005 边界），客户端只能观测自己一侧的行为序列。

据此决策新增机制 TC-FEAT-GITCDN-FAILFAST：

1. **判负对象是"卡死"而非"限速"**：操作被外层 timeout 强杀（exit 124，悬挂形态）或 exit≠0 且
   无限速签名（异常错误）才判负；exit≠0 带限速签名 = 限速被快失败 + backoff 重试吸收，
   是机制按设计工作的证据，记录不判负。单操作上界默认 480s，必须大于生产最坏重试链
   （≈ (BACKOFF_COUNT+1)×lowSpeedTime + 指数退避 ≈ 376s），否则"重试耗尽后返回错误"会被
   误判为悬挂强杀。
2. **并发采样提高撞限速概率**：dispatch 可调并发流数（默认 1，上限 3），每流独立 fetch 目录循环
   "ls-remote → fetch main → fetch-by-SHA"。多 job 并发拉代码是正常用户形态，不属于故障注入；
   与 ADR-0005 串行采样互补，本次触发的上游回源密度更高。
3. **客户端时间线可视化**：每操作记录（流， 操作， 开始偏移， 耗时， exit， 签名），
   Step Summary 输出时间线表 + ASCII 耗时条 + 判定分类统计。慢速但成功的操作按
   `≈duration/lowSpeedTime` 折算疑似重试次数（信息项）。观测只走客户端时间线，
   不采集 git-cdn /metrics（服务端指标观测留作后续迭代，避免依赖服务端端点可达性）。
4. **限速事件的长期结论仍由统计收敛**：单次运行遇不到限速（低概率随机事件）时输出
   "全部操作有界完成"，不算失败；"限速时快失败重试"的证据由定时采样（每天 1 次 × 13 集群）
   在看板跨运行聚合。

## 证据与范围

- 与既有两用例的分工：ADR-0005（TC-FEAT-GITCDN）验证"正常时段无签名全绿 + 时间上界"；
  ADR-0006（TC-FEAT-LOWPEED）在实验室 fixture 确定性验证 git lowSpeed 机制语义；本用例验证
  "限速发生时的形态是有界自愈而非悬挂"。三者共同覆盖 issue #2885 的验收语义。
- 流量路径证据链与 ADR-0005 一致：GIT_CONFIG_GLOBAL 注入与生产 postStart 完全一致的 insteadOf
  重写并自检生效（硬），clone 日志含 git-cdn 302 重定向证据（硬），ls-remote 真实 git 客户端
  连通（硬）。gitcode 实例与 ambient 重写探针已由 ADR-0005 用例普查，本用例不重复。
- 客户端视角的固有限制：git-cdn 内部重试次数无法直接观测，"疑似重试"由耗时形态推断
  （有界失败带签名 / 慢速成功），明确标注为推断证据而非直接计数；"很多次上游请求"的
  直接观测需服务端 /metrics，超出本用例范围（见决策 3 的后续迭代说明）。
- 判定分类口径：`normal`（快速成功）/ `fail_fast`（带签名有界失败 = 快失败吸收）/
  `recovered`（≥lowSpeedTime 慢速成功 = 疑似重试自愈）/ `HANG`（强杀，判负）/
  `UNEXPECTED`（无签名失败或路径证据缺失，判负）。violation 条目一律引用真实操作 label，
  report 按操作关联判定，保证时间线与分类一致。
- 不修改生产 git-cdn 配置、不向上游加压超过正常用户并发形态、不做服务端指标依赖；
  dispatch 参数（并发/轮次/上界）钳制保证作业在 60min 硬上界内永不悬挂。

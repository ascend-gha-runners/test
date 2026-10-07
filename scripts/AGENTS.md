# scripts/ — 独立执行工具

修改测试引擎前读取 `../docs/test-cases.md`；变更架构或执行策略时读取
`../docs/adr/README.md` 与相关 ADR。编排、Runner 调度和安装架构过滤由工作流负责。

## HTTP wheel 完整性引擎

- `wheel_integrity_check.py` 使用标准库，通过显式索引与精确版本 pin 校验样本；
  wheel 下载只做字节 SHA-256 校验，不安装、不导入，因此不按宿主架构过滤。
- 跟随索引发布的相对、根相对及绝对链接；legacy 和 origin 路径均可使用，
  origin 路径链接不是前置条件。保留 query，去除 fragment 后下载。
  E2E 使用 `--require-index-origin`：所选 wheel/metadata URL、索引最终 URL
  及每跳重定向须与请求的样本索引 scheme/netloc 相同（含端口）；跨源输出
  `fail`，重定向在请求目标前拒绝。独立 CLI 默认仍允许跨源。
- wheel hash 来自索引 `#sha256`；metadata hash 来自广告属性。
  缺失或不支持的 hash 输出 `not_verified`，不能包装成完整性通过。
  未广告的 metadata 字段为 `not_verified`（reason: `not advertised`），
  不请求 sidecar，也不阻断 wheel 校验；广告但无 hash 会阻断成功。
- stdout 是 JSON；退出码见 CLI `--help`。HTTP 错误、大小超限和 hash 不符为 `fail`。
  请求按 socket timeout 与流式字节上限约束，编排方另设总 job timeout。
- 结果仅代表显式配置的样本，不代表全部 wheel、全部依赖、解析/安装成功或运行时正确。
  保持旧 PEP 658 工具接口独立，新增引擎不会自动修正旧工具的判定语义。

## 本地验证

修改引擎后执行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_wheel_integrity_check.py' -v
```

fixture 仅使用 loopback HTTP 服务及 mock 网络错误。新增边界测试保持确定性；
不以生产访问、远程 dispatch 或实际大 wheel 下载代替本地测试。

# E2E 工作流

修改特性用例先读 `../../docs/test-cases.md` 与 `../../docs/adr/README.md`；每种生态在同一 workflow 内保留基础功能与真实用户场景，遵守根目录的三级断言。

- 真实下载使用空的客户端缓存/隔离环境；预装包、已有构建产物和失败被忽略不能算通过。
- 新增 Go 用例仅人工选择一个集群；现有工作流保留既有 schedule。本轮不新增发布、ArgoCD revision 门禁或自动 E2E dispatch。
- HTTP 状态用 curl 的 `%{http_code}` 判定；回源层级另作观察。客户端备用源/离线重建与 nginx 上游回退分别报告。
- `pass` / `fail` / `not_applicable` / `not_verified` 分开呈现；探针与缓存命中波动保持软断言。没有层级证据不能打印“回退通过”。
- PyTorch CPU 安装继续遵守 ADR-0002；文件 HTTP hash 抽样不依赖运行架构，人工执行时可开启，声明样本而不是全量保证。
- crates 默认仅运行审查过的 gy-006 8085 入口；其他集群为未验证，不推断线上端口缺失。hk-ci 与 hk-001 不同，缺少组织 Runner 证据时不得编造标签。

本地：先运行 `python3 -m unittest discover -s scripts -p 'test_*integrity*.py'`，再对修改的 YAML 做解析、actionlint 和内嵌 shell 语法检查。真实集群运行会创建负载，需另行确认目标及授权。

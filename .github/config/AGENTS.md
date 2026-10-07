# 测试配置

`runners.json` 只记录已有 Runner 配置与双标签，不表示在线健康状态；新增集群必须有组织级 Runner 标签与实际落点证据，hk-ci 不得复用 hk-001。

修改 `test_cases.json` 同步 `../../docs/test-cases.md`：登记 workflow、端口、客户端、硬/软/探针断言和覆盖边界。安装与文件抽样完整性分开描述，不能把 metadata hash 等同于 wheel hash 或把部分样本称为全量依赖校验。

校验 JSON 可解析，登记 workflow 路径必须存在；保留既有字段以兼容看板生成器。

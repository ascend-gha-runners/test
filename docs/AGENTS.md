# 测试规约文档

`test-cases.md` 定义用户能力与三级断言；`.github/config/test_cases.json` 登记可执行入口。修改任一处同步另一处，并核对实际 workflow/script，不将历史运行状态写成长期保证。

新增机制或调度策略按 `adr/AGENTS.md` 新增 ADR 并更新索引；已接受决策按原边界保持，未评审的新决策标记提议中。

验收表必须区分已验证、失败、不适用、未验证；每条 hash 校验说明对象是 metadata、wheel 抽样还是客户端解析到的完整依赖安装。真实回退证据与客户端容灾分开，不以 404 单独证明 nginx 上游 5xx/507 回退。

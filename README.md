# 推广申请处理 Agent

![CI](https://github.com/lszlovelhl/promotion_agent/actions/workflows/test.yml/badge.svg)

基于 **Master-Sub Agent 架构**的飞书群机器人，自动化处理运营团队的抖加 / 垫付推广申请：**提取 → 校验 → 人工确认 → 写表 → 汇报** 全链路，无需人工录入。

## 解决的问题

运营团队推广申请人工录入易错、跨表难对账：
- 手动录入金额 / 笔数容易出错，对账要来回翻表格
- 申请文本口语化、格式不固定，纯规则解析覆盖率低

## 核心架构

```
用户在飞书群 @机器人 发送申请文本
          ↓
┌─────────────────┐
│   Master Agent  │   主协调者：任务调度、流程编排、状态管理
└─────────┬───────┘
          ↓
┌─────────┴───────┐
│ 1. Extractor    │   信息提取：规则正则优先 + LLM 兜底双层提取
├─────────────────┤
│ 2. Validator    │   全维度校验：金额/笔数/日期/档位/笔误/重复申请
├─────────────────┤
│ 3. LarkWriter   │   按博主匹配，自动更新多维表格字段
├─────────────────┤
│ 4. Reporter     │   金额汇总、异常简报、负责人日报
└─────────────────┘
```

**为什么用 Master-Sub Agent？** 企业级 Agent 必须职责分离——提取、校验、写入、汇报每个环节独立，可测试、可替换、可观测；主 Agent 负责任务编排，子 Agent 是专业执行者。

**为什么不全用大模型？** LLM 会幻觉，金额计算可能出错。固定格式优先用规则正则（快、准、零成本），LLM 只做口语化兜底；所有硬校验用规则，保证结果可解释。

## 功能特性

- **双层提取**：规则正则优先（100% 准确）+ LLM 兜底（口语化文本），字段名容错映射兼容模型输出漂移
- **全维度校验**：金额一致性、笔数一致性、日期逻辑、抖加档位、笔误检测（编辑距离 ≤ 2）、重复申请——实测发现「声称 7 笔、明细仅 5 条」「打款日期早于申请日期」等异常并自动标红
- **人工确认闭环**：模板确认卡片，支持修改意见输入，边看边改；确认写入 / 驳回，审核结果分级
- **消息幂等防重**：MD5 指纹拦截重复提交（pending / confirmed 两态），待确认超 30 分钟自动释放，重复消息不重复调用 LLM
- **Web 监管后台**：仪表盘（总金额 / 总笔数 / 覆盖博主数）、申请记录、系统配置、机器人一键重启
- **准确率量化**：评测脚本基于真实确认记录，规则与 LLM 双模式 P / R / F1 均 100%

## 快速开始

```bash
git clone https://github.com/lszlovelhl/promotion_agent.git
cd promotion_agent

# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（复制模板，填写飞书应用 / 表格 / LLM 接入点）
cp config.example.json data/config.json

# 3. 启动（自动连接飞书长连接 + Web 后台）
python app.py
```

后台地址 `http://localhost:8889`，在「系统配置」页填写飞书机器人 app_id / app_secret、多维表格 app_token / table_id、LLM API Key 后保存，机器人自动连接飞书。

> 详细使用见 [说明书.md](说明书.md)

## 技术栈

| 层 | 技术 |
|---|---|
| Agent 架构 | Master-Sub（提取 / 校验 / 写入 / 汇报四子 Agent） |
| 机器人 | 飞书长连接（无需公网 IP）+ 模板确认卡片 |
| LLM | 火山方舟（OpenAI 兼容协议），规则失败时兜底 |
| 后端 | FastAPI + uvicorn（Web 监管后台） |
| 存储 | JSON 持久化：records.json（历史）+ processed_messages.json（幂等指纹） |
| 工程化 | Docker / docker-compose / GitHub Actions CI（26 项单元测试） |

## 测试

```bash
python -m unittest discover tests -v   # 26 项全部通过
```

覆盖：提取规则与 LLM 字段容错、校验逻辑、幂等三态流转（pending / confirmed / rejected）、过期释放、持久化。

## Docker

```bash
docker compose up -d   # 端口 8889，data/ 数据卷持久化
```

## 面试讲解要点

- **卡片回调必须异步处理**：3 秒内返回响应，否则飞书超时
- **卡片更新走 cardkit 接口**：普通消息接口不支持更新
- **人工确认是数据安全兜底**：入库前必须确认，异常自动标记，减少人工检查成本

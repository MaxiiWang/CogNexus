# CogNexus 🌐

**[English](README.md) | 中文**

**分布式认知枢纽 - 让 AI Agent 的能力流通起来**

连接 Human、Character、Simulation，用 ATP 驱动知识交换。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**🌍 在线体验：[https://wielding.ai](https://wielding.ai)**

---

> ⚠️ **Vibe Coding | Work in Progress | Experimental**
> 
> 本项目采用 Vibe Coding 方式开发——由 AI Agent 主导编码，人类负责方向把控和验收。
> 
> **这是一个实验性项目，仍在积极开发中：**
> - 代码可能存在 bug 和不完善之处
> - API 和数据结构可能随时变更
> - 部分功能尚未经过充分测试
> 
> **免责声明：** 本项目按"原样"提供，不提供任何明示或暗示的保证。使用本项目的风险由用户自行承担。作者不对因使用本项目而导致的任何数据丢失、系统故障或其他损失负责。

---

## ✨ 特性

- **Agent 市场** - 发布和发现 AI Agent，浏览其能力和定价
- **Token 交易** - 使用 ATP（平台积分）购买 Agent 访问权限
- **多类型 Agent** - Human（真人知识库）、Character（角色）、Simulation（模拟）
- **认知模拟** - 蒙特卡洛认知模拟引擎，多轮环境注入与叙事立场提取
- **智能招募** - 自动为模拟场景招募合适的 Agent 参与者
- **全站国际化** - 中/英双语界面，包括文档页
- **访问控制** - Token 权限分级（完整访问/问答/浏览）
- **健康监控** - 自动检测 Agent 在线状态
- **Dashboard** - 统一管理 Agent 发布和 Simulation 运行

---

## 🎯 什么是 CogNexus？

CogNexus 是一个 **Agent 能力交换平台**：

```
┌─────────────┐     发布 Agent      ┌─────────────┐
│   Agent     │ ──────────────────→ │  CogNexus   │
│   Owner     │ ←────────────────── │   Market    │
└─────────────┘     获得 ATP        └─────────────┘
                                          │
                                          │ 发现 & 购买
                                          ↓
                                    ┌─────────────┐
                                    │    User     │
                                    │   (买家)    │
                                    └─────────────┘
```

**ATP（Agent Trade Points）** 是平台内部积分：
- 用于购买 Agent Token
- 无现实货币价值
- 仅作为平台内交换媒介

---

## 🧪 Simulation 系统

CogNexus 内置认知模拟引擎，支持：

- **蒙特卡洛认知模拟** - 通过多次采样探索 Agent 的认知空间
- **多轮环境注入** - 在模拟过程中动态注入外部条件
- **叙事立场提取** - 分析 Agent 在不同情境下的叙事倾向
- **Simulation React** - Agent 对模拟场景的付费交互机制
- **LLM 配置** - 支持多种 LLM 预设（含豆包模型）

### 模拟流程

1. 创建 Simulation 场景，配置环境参数
2. 智能招募系统自动匹配适合的 Agent
3. 执行多轮模拟，采集 Agent 响应
4. 蒙特卡洛引擎分析认知分布
5. 提取叙事立场和关键洞察

---

## 🚀 快速开始

### 方式一：让你的 Agent 自主安装

```
请帮我部署这个项目：https://github.com/MaxiiWang/CogNexus

阅读 README.md 和 SETUP.md，
完成数据库初始化和服务启动。
```

### 方式二：手动安装

```bash
git clone https://github.com/MaxiiWang/CogNexus.git
cd CogNexus
chmod +x setup.sh
./setup.sh
```

详细步骤参见 [SETUP.md](SETUP.md)。

### 验证安装

```bash
curl http://localhost:8080/api/stats
```

---

## 📖 API 文档

### 公开接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/stats` | GET | 平台统计 |
| `/api/agents` | GET | Agent 列表 |
| `/api/agents/{id}` | GET | Agent 详情 |

### 认证接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/register` | POST | 用户注册 |
| `/api/auth/login` | POST | 用户登录 |
| `/api/auth/me` | GET | 当前用户信息 |

### Agent 管理（需登录）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agents` | POST | 发布 Agent |
| `/api/agents/{id}` | PUT | 更新 Agent |
| `/api/agents/{id}` | DELETE | 删除 Agent |
| `/api/agents/{id}/tokens` | GET | 查看 Token |
| `/api/agents/{id}/tokens` | POST | 添加 Token |
| `/api/agents/{id}/purchase` | POST | 购买 Token |

### Agent 集成接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agents/probe` | POST | 探测 Agent URL |
| `/api/agents/health-check` | POST | 批量健康检查 |

### Simulation 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/simulations` | GET | 模拟列表 |
| `/api/simulations` | POST | 创建模拟 |
| `/api/simulations/{id}` | PUT | 更新模拟 |
| `/api/simulations/{id}` | DELETE | 删除模拟 |

---

## 🔗 与 Cogmate 集成

CogNexus 设计为与 [Cogmate](https://github.com/MaxiiWang/Cogmate) 配合使用：

1. **Cogmate** 提供知识管理能力（存储/检索/图谱）
2. **CogNexus** 提供能力交换市场（发布/发现/交易/模拟）

### Agent 发布流程

```bash
# 1. 在 Cogmate 中生成 Token
cd Cogmate
./cogmate visual --duration 15d --scope qa_public --count 20

# 2. 在 CogNexus 发布 Agent，添加 Token
# 通过 Web 界面或 API
```

也可以直接在 Cogmate 的管理弹窗中使用 CogNexus Tab 一键发布。

### Token 验证

CogNexus 添加 Token 时会调用 Agent 的验证接口：

```
GET {agent_url}/api/hub/token/validate?token=xxx
```

---

## 📁 项目结构

```
CogNexus/
├── README.md             # English README
├── README.zh-CN.md       # 中文 README（本文件）
├── SETUP.md              # 安装指南
├── requirements.txt      # Python 依赖
├── setup.sh              # 安装脚本
├── start.sh              # 启动脚本
├── .env.example          # 环境变量模板
│
├── api/                  # 后端
│   ├── main.py           # FastAPI 应用
│   ├── auth.py           # 认证逻辑
│   ├── database.py       # 数据库操作
│   ├── models.py         # 数据模型
│   ├── simulation.py     # 模拟引擎
│   ├── simulation_routes.py # 模拟路由
│   └── monte_carlo.py    # 蒙特卡洛引擎
│
├── frontend/             # 前端
│   ├── index.html        # 首页
│   ├── marketplace.html  # 市场页
│   ├── dashboard.html    # 仪表盘
│   ├── simulation.html   # 模拟页面
│   ├── docs.html         # 文档页
│   └── css/
│       └── theme.css     # 设计系统
│
└── data/                 # 数据目录（gitignore）
    └── hub.db            # SQLite 数据库
```

---

## ⚙️ 配置

### 环境变量

```bash
cp .env.example .env
```

```bash
# 服务配置
HUB_HOST=0.0.0.0
HUB_PORT=8080

# JWT 密钥（请修改！）
JWT_SECRET=your-secret-key-change-this

# 数据库路径
DATABASE_PATH=data/hub.db

# 新用户初始 ATP
INITIAL_ATP=100
```

---

## 🎨 设计系统

前端使用统一的设计令牌系统（`frontend/css/theme.css`）：

- **配色** - 暖琥珀金主色，深色背景
- **字体** - Playfair Display + Inter + JetBrains Mono
- **组件** - 玻璃拟态卡片、响应式布局
- **动画** - 渐入效果、悬浮交互
- **国际化** - 全站中/英切换（62+ 翻译 key）

与 [Cogmate Visual](https://github.com/MaxiiWang/Cogmate) 保持视觉统一。

---

## 🛣️ Roadmap

- [x] **Phase A1** - 基础市场（用户/Agent/Token CRUD）
- [x] **Phase A2** - Token 验证集成
- [x] **Phase A3** - 多角色发布流程
- [x] **Phase B1** - Simulation 前端 + LLM 集成
- [x] **Phase B2** - 多轮环境注入 + 叙事立场提取
- [x] **Phase B3** - 蒙特卡洛认知模拟引擎
- [x] **Phase C1** - 全站国际化 (中/英)
- [ ] **Phase C2** - Character Agent 深度支持
- [ ] **Phase D1** - ATP 充值/提现

---

## 📝 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

## 🙏 致谢

- [Cogmate](https://github.com/MaxiiWang/Cogmate) - 知识管理系统
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [OpenClaw](https://github.com/openclaw/openclaw) - Agent 运行时

---

**让认知能力自由流通。** 🌐

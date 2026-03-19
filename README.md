# CogNexus 🌐

**English | [中文](README.zh-CN.md)**

**Distributed Cognitive Hub - Let AI Agent Capabilities Flow**

Connect Human, Character, and Simulation agents. Drive knowledge exchange with ATP.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**🌍 Live Demo: [https://wielding.ai](https://wielding.ai)**

---

> ⚠️ **Vibe Coding | Work in Progress | Experimental**
> 
> This project is developed using Vibe Coding — AI Agents lead the coding, humans steer direction and review.
> 
> **This is an experimental project under active development:**
> - Code may contain bugs and rough edges
> - APIs and data structures may change at any time
> - Some features have not been thoroughly tested
> 
> **Disclaimer:** This project is provided "as-is" without warranty of any kind. Use at your own risk. The author is not responsible for any data loss, system failures, or other damages resulting from the use of this project.

---

## ✨ Features

- **Agent Marketplace** - Publish and discover AI Agents, browse capabilities and pricing
- **Token Trading** - Use ATP (Agent Trade Points) to purchase Agent access
- **Multi-Type Agents** - Human (personal knowledge), Character (personas), Simulation (cognitive models)
- **Cognitive Simulation** - Monte Carlo cognitive simulation engine with multi-round environment injection and narrative stance extraction
- **Smart Recruiting** - Automatically recruit suitable Agent participants for simulation scenarios
- **Full i18n** - Chinese/English bilingual interface including documentation
- **Access Control** - Tiered token permissions (full / Q&A / browse)
- **Health Monitoring** - Auto-detect Agent online status
- **Dashboard** - Unified management for Agent publishing and Simulation runs

---

## 🎯 What is CogNexus?

CogNexus is an **Agent capability exchange platform**:

```
┌─────────────┐   Publish Agent     ┌─────────────┐
│   Agent     │ ──────────────────→ │  CogNexus   │
│   Owner     │ ←────────────────── │   Market    │
└─────────────┘     Earn ATP        └─────────────┘
                                          │
                                          │ Discover & Purchase
                                          ↓
                                    ┌─────────────┐
                                    │    User     │
                                    │   (Buyer)   │
                                    └─────────────┘
```

**ATP (Agent Trade Points)** is the platform's internal currency:
- Used to purchase Agent Tokens
- No real-world monetary value
- Serves only as an in-platform exchange medium

---

## 🧪 Simulation System

CogNexus includes a built-in cognitive simulation engine:

- **Monte Carlo Cognitive Simulation** - Explore Agent cognitive space through multi-sample analysis
- **Multi-Round Environment Injection** - Dynamically inject external conditions during simulation
- **Narrative Stance Extraction** - Analyze Agent narrative tendencies across different scenarios
- **Simulation React** - Paid interaction mechanism for Agents responding to simulation scenarios
- **LLM Configuration** - Support for multiple LLM presets (including Doubao models)

### Simulation Workflow

1. Create a simulation scenario with environment parameters
2. Smart recruiting system auto-matches suitable Agents
3. Execute multi-round simulation, collect Agent responses
4. Monte Carlo engine analyzes cognitive distribution
5. Extract narrative stances and key insights

---

## 🚀 Quick Start

### Option 1: Let Your Agent Install It

```
Please deploy this project: https://github.com/MaxiiWang/CogNexus

Read README.md and SETUP.md,
complete database initialization and service startup.
```

### Option 2: Manual Installation

```bash
git clone https://github.com/MaxiiWang/CogNexus.git
cd CogNexus
chmod +x setup.sh
./setup.sh
```

See [SETUP.md](SETUP.md) for detailed steps.

### Verify Installation

```bash
curl http://localhost:8080/api/stats
```

---

## 📖 API Reference

### Public Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stats` | GET | Platform statistics |
| `/api/agents` | GET | Agent list |
| `/api/agents/{id}` | GET | Agent details |

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/register` | POST | User registration |
| `/api/auth/login` | POST | User login |
| `/api/auth/me` | GET | Current user info |

### Agent Management (Auth Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents` | POST | Publish Agent |
| `/api/agents/{id}` | PUT | Update Agent |
| `/api/agents/{id}` | DELETE | Delete Agent |
| `/api/agents/{id}/tokens` | GET | View Tokens |
| `/api/agents/{id}/tokens` | POST | Add Token |
| `/api/agents/{id}/purchase` | POST | Purchase Token |

### Agent Integration

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/probe` | POST | Probe Agent URL |
| `/api/agents/health-check` | POST | Batch health check |

### Simulation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/simulations` | GET | List simulations |
| `/api/simulations` | POST | Create simulation |
| `/api/simulations/{id}` | PUT | Update simulation |
| `/api/simulations/{id}` | DELETE | Delete simulation |

---

## 🔗 Cogmate Integration

CogNexus is designed to work with [Cogmate](https://github.com/MaxiiWang/Cogmate):

1. **Cogmate** provides knowledge management capabilities (storage / retrieval / graph)
2. **CogNexus** provides the capability exchange market (publish / discover / trade / simulate)

### Agent Publishing Flow

```bash
# 1. Generate tokens in Cogmate
cd Cogmate
./cogmate visual --duration 15d --scope qa_public --count 20

# 2. Publish Agent on CogNexus, add tokens
# Via web interface or API
```

You can also publish directly from Cogmate's management modal via the CogNexus tab.

### Token Validation

When adding tokens, CogNexus calls the Agent's validation endpoint:

```
GET {agent_url}/api/hub/token/validate?token=xxx
```

---

## 📁 Project Structure

```
CogNexus/
├── README.md             # English README (this file)
├── README.zh-CN.md       # 中文 README
├── SETUP.md              # Setup guide
├── requirements.txt      # Python dependencies
├── setup.sh              # Setup script
├── start.sh              # Start script
├── .env.example          # Environment variable template
│
├── api/                  # Backend
│   ├── main.py           # FastAPI application
│   ├── auth.py           # Authentication
│   ├── database.py       # Database operations
│   ├── models.py         # Data models
│   ├── simulation.py     # Simulation engine
│   ├── simulation_routes.py # Simulation routes
│   └── monte_carlo.py    # Monte Carlo engine
│
├── frontend/             # Frontend
│   ├── index.html        # Home page
│   ├── marketplace.html  # Marketplace
│   ├── dashboard.html    # Dashboard
│   ├── simulation.html   # Simulation page
│   ├── docs.html         # Documentation
│   └── css/
│       └── theme.css     # Design system
│
└── data/                 # Data directory (gitignored)
    └── hub.db            # SQLite database
```

---

## ⚙️ Configuration

### Environment Variables

```bash
cp .env.example .env
```

```bash
# Server config
HUB_HOST=0.0.0.0
HUB_PORT=8080

# JWT secret (change this!)
JWT_SECRET=your-secret-key-change-this

# Database path
DATABASE_PATH=data/hub.db

# Initial ATP for new users
INITIAL_ATP=100
```

---

## 🎨 Design System

The frontend uses a unified design token system (`frontend/css/theme.css`):

- **Colors** - Warm amber-gold primary, dark background
- **Typography** - Playfair Display + Inter + JetBrains Mono
- **Components** - Glassmorphism cards, responsive layout
- **Animations** - Fade-in effects, hover interactions
- **i18n** - Full site Chinese/English toggle (62+ translation keys)

Visually unified with [Cogmate Visual](https://github.com/MaxiiWang/Cogmate).

---

## 🛣️ Roadmap

- [x] **Phase A1** - Core marketplace (User / Agent / Token CRUD)
- [x] **Phase A2** - Token validation integration
- [x] **Phase A3** - Multi-profile publishing flow
- [x] **Phase B1** - Simulation frontend + LLM integration
- [x] **Phase B2** - Multi-round environment injection + narrative stance extraction
- [x] **Phase B3** - Monte Carlo cognitive simulation engine
- [x] **Phase C1** - Full site i18n (Chinese / English)
- [ ] **Phase C2** - Deep Character Agent support
- [ ] **Phase D1** - ATP deposit / withdrawal

---

## 📝 License

MIT License - See [LICENSE](LICENSE)

---

## 🙏 Acknowledgments

- [Cogmate](https://github.com/MaxiiWang/Cogmate) - Personal knowledge management system
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [OpenClaw](https://github.com/openclaw/openclaw) - Agent runtime

---

**Let cognitive capabilities flow freely.** 🌐

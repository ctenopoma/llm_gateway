# 📘 Antigravity LLM Gateway

**A Robust Enterprise-Grade LLM API Gateway with Rate Limiting, Load Balancing, and Billing Management**

![Version](https://img.shields.io/badge/version-2.4.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-green.svg)
![License](https://img.shields.io/badge/license-MIT-gray.svg)

---

## 📋 Overview

Antigravity LLM Gateway is an enterprise-ready API gateway for managing access to Large Language Model (LLM) services. It provides:

- **Multi-Model Support**: Manage multiple LLM providers and endpoints
- **API Key Authentication**: Secure request validation with key-based access control
- **Rate Limiting & Budgeting**: Control costs with token and request limits per API key
- **Load Balancing**: Distribute requests across multiple model endpoints
- **Request/Response Logging**: Full audit trail of all API usage
- **Health Checks**: Automatic endpoint monitoring and failover
- **Admin Dashboard**: Web UI for API key management and billing visualization
- **Internal App Integration**: Support for internal applications via shared secrets
- **Docker Ready**: Pre-configured for containerized deployment

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 13+
- Redis 7+
- Docker & Docker Compose (optional)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd llm_gateway
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your database and API settings
   ```

4. **Initialize the database**
   ```bash
   python create_missing_tables.py
   ```

5. **Run the application**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

### Docker Setup

```bash
docker-compose up -d
```

This will start the Gateway, PostgreSQL, Redis, and Nginx services.

---

## 🏗️ Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Nginx (Reverse Proxy)                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                   FastAPI Application                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────┐  │
│  │  GatewayMiddle  │  │  Load Balancer  │  │ Admin REST │  │
│  │  ware           │  │                 │  │   API      │  │
│  └─────────────────┘  └─────────────────┘  └────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
    ┌──────────────────┼──────────────────┬──────────────────┐
    │                  │                  │                  │
┌───▼────┐      ┌──────▼──────┐   ┌──────▼──────┐    ┌──────▼────┐
│ Redis  │      │ PostgreSQL  │   │ LiteLLM     │    │ Upstream  │
│(Cache) │      │(Storage)    │   │ (LLM Layer) │    │ Endpoints │
└────────┘      └─────────────┘   └─────────────┘    └───────────┘
```

### Directory Structure

```
app/
├── main.py                 # FastAPI entry point
├── config.py              # Configuration management
├── database.py            # Database connection & utilities
├── exceptions.py          # Custom exceptions
├── redis_client.py        # Redis connection handler
├── models/
│   └── schemas.py         # Pydantic models
├── middleware/
│   └── gateway.py         # Request authentication & validation
├── routers/
│   ├── admin.py          # Admin dashboard APIs
│   ├── apps.py           # App registration APIs
│   ├── chat.py           # Chat completion endpoints
│   └── management.py     # Model & endpoint management
├── services/
│   ├── api_key.py        # API key validation
│   ├── budget.py         # Budget enforcement
│   ├── context_validation.py # Input validation
│   ├── error_sanitizer.py # Error response handling
│   ├── health_check.py    # Endpoint health monitoring
│   ├── load_balancer.py   # Load balancing logic
│   └── usage_log.py       # Request logging
└── admin/
    ├── static/           # CSS/JS assets
    └── templates/        # HTML templates
```

---

## 🔐 Authentication

The Gateway supports two authentication methods:

### 1. API Key Authentication

For external applications:

```bash
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api-key>" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 2. Shared Secret (Internal Apps)

For trusted internal applications:

```bash
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Secret: <shared-secret>" \
  -H "X-App-Id: <app-id>" \
  -H "X-User-Oid: <user-id>" \
  -d '{...}'
```

---

## 📊 Admin Dashboard

Access the admin panel at: `http://localhost:8000/admin/`

**Features:**
- Create and manage API keys
- Monitor API key usage and costs
- View usage logs and audit trails
- Manage app registrations
- Configure model endpoints

---

## ⚙️ Configuration

Key environment variables:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost/llm_gateway

# Redis
REDIS_URL=redis://localhost:6379/0

# Gateway Settings
GATEWAY_SHARED_SECRET=your-shared-secret
LOG_RETENTION_DAYS=90
RATE_LIMIT_REQUESTS_PER_MINUTE=60

# LLM Settings
DEFAULT_MODEL=gpt-4
LLM_API_KEY=your-api-key
```

See `app/config.py` for all available settings.

---

## 🧪 Testing

Run the test suite:

```bash
pytest tests/ -v
```

Run specific test modules:

```bash
pytest tests/test_api_key.py -v
pytest tests/test_budget.py -v
pytest tests/test_context_validation.py -v
```

---

## 📝 API Documentation

Once the application is running, view the interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## 📚 Key Features

### Rate Limiting
- Per-API-key request and token limits
- Configurable time windows
- Automatic overflow handling

### Budget Management
- Monthly cost tracking per API key
- Soft and hard limits
- Real-time budget monitoring

### Load Balancing
- Round-robin distribution
- Health-based routing
- Automatic failover

### Logging & Auditing
- All requests logged with full context
- Usage analytics
- Audit trail for compliance

### Health Monitoring
- Periodic endpoint health checks
- Automatic circuit breaking
- Health status reporting

---

## 🔧 Troubleshooting

### Database Connection Issues

```bash
python diagnose_keys.py
python list_tables.py
```

### Health Check Problems

Check the `/health` endpoint:

```bash
curl http://localhost:8000/health
```

### Debug Mode

Enable debug logging:

```env
LOG_LEVEL=DEBUG
```

---

## 📖 Documentation

- [SPECIFICATION.md](SPECIFICATION.md) - Complete technical specification
- [TUTORIAL.md](TUTORIAL.md) - Step-by-step usage guide

---

## 🤝 Contributing

1. Create a feature branch
2. Commit your changes
3. Push to the branch
4. Submit a pull request

---

## 📄 License

This project is licensed under the MIT License.

---

## 📞 Support

For issues, questions, or feedback, please open an issue on the repository.

---

**Happy Gateway Building! 🚀**

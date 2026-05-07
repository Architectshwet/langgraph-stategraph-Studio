# Seagate Agent Assistant 🚀
### State-of-the-Art Multi-Agent Orchestration with LangGraph & Intent Locking

The Seagate Agent Assistant is a high-performance, enterprise-grade operational intelligence platform designed to automate complex Seagate manufacturing and infrastructure workflows. It leverages a sophisticated **Supervisor Router** architecture with persistent **Intent Locking** to provide a robust, deterministic, and highly responsive user experience.

---

## 🏛️ Technical Architecture

The platform's intelligence is distributed across a stateful, multi-node graph designed for reliability and strict procedural compliance:

- **Multi-Agent Supervisor Routing**: A centralized **Router Node** acts as the system's "Front Door," classifying user intent and enforcing domain guardrails. It delegates specialized tasks to independent, tool-bound agents.
- **Session-Based Intent Locking**: To ensure continuity in multi-step workflows, the system implements an **Intent Locking mechanism**. Once a specialist (DMR or SSL) is engaged, the session is "locked" to that specialist, bypassing the router for subsequent messages until the task is completed or the session expires.
- **Isolated Specialist Memory**: Each specialist node maintains its own independent history (`partial_normal_dmr_history`, `ssl_history`) within the global `RouterState`. This prevents context pollution and improves the LLM's reasoning accuracy for complex, sequential tasks.
- **Transactional Workflow Orchestration**: Designed as a state machine, the system enforces strict sequences (e.g., `collect_info` -> `triage` -> `resolve`), eliminating LLM hallucinations in sensitive database operations.

---

## 💻 Full-Stack Technology Suite

- **Orchestration**: `LangGraph v0.2+` for cyclic, stateful multi-agent graphs.
- **Agentic Framework**: `LangChain` for tool-binding, advanced prompt engineering, and LLM abstraction.
- **Core API**: `FastAPI` with an asynchronous lifecycle (`Lifespan`) for efficient resource management (DB pools, LLM clients).
- **Communication Protocols**:
  - **Real-Time WebSockets**: Low-latency, bi-directional communication for instantaneous UI updates.
  - **SSE (Server-Sent Events)**: Asynchronous progress streaming from specialist agents.
- **Persistence & State Management**:
  - **PostgreSQL**: Transactional storage for conversation history, operational logs, and business state.
  - **Postgres Checkpointer**: Durable, cross-session persistence for LangGraph states.
  - **Redis Integration**: High-performance cross-thread memory and distributed session store.
- **Security & Validation**:
  - **Robust JWT Authentication**: Secure, token-based access control for both API and WebSocket connections.
  - **Pydantic V2**: Strict data validation and schema enforcement across all system boundaries.
- **Observability**: **Langfuse** integration for end-to-end tracing, LLM cost tracking, and performance monitoring.
- **Infrastructure**: Containerized deployment with **Docker** and **Docker Compose**.

---

## ✨ Key Technical Achievements

- **Zero-Latency UI Responsiveness**: Database append operations for conversation history are offloaded to `asyncio` background tasks, allowing the UI input to unlock the moment the agent's response is delivered.
- **Dynamic Prompt Injection**: Context-aware prompts are dynamically generated with real-time metadata (e.g., current date, session context) to ensure accurate reasoning.
- **Scalable Resource Pooling**: Implementation of `psycopg_pool` for efficient managed database connections, ensuring stability under high operational load.
- **Custom Mermaid Visualization**: Automated generation of the current multi-agent architecture's state graph (`seagate_stategraph.png`).

---

## 🛠️ Quick Start

### 1. Environment Setup
Configure your `.env` file with the following critical parameters:
- `OPENAI_API_KEY`
- `POSTGRES_URL`
- `REDIS_URL`
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`

### 2. Launching the System

#### Option A: Docker (Recommended)
```bash
docker-compose -f docker-compose.dev.yml up --build
```

#### Option B: Local UV Development
```bash
# Initialize environment
uv sync

# Run the server
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Architecture Visualization
Retrieve the latest state graph from the running container:
```bash
docker cp wafer-agent-dev:/app/seagate_stategraph.png .
```

---

## 📖 Operational Workflows

- **DMR Management**: "Execute partial release for DMR-X7002"
- **SSL Lifecycle**: "Generate a CSR for my Windows IIS server"
- **Infrastructure Support**: "Raise a ticket for certificate renewal"
- **Security Guardrails**: Automated refusal of unrelated general-knowledge or non-Seagate queries.

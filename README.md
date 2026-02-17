# CI Triage Bot

Automatic triage system for vLLM nightly/daily CI builds. Uses Claude Code to intelligently analyze job logs, categorize failures, and match them to historical issues.

## Features

- **Automatic Build Sync**: Fetches builds from Buildkite CI
- **Intelligent Triage**: Uses Claude Code to analyze job logs and categorize failures
- **Pattern Matching**: Matches new failures to historical ones for suggested issues
- **GitHub Integration**: Create and link issues directly from the dashboard
- **Slack Notifications**: Optional alerts for new failures

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- `gh` CLI (GitHub CLI) - authenticated
- `bk` CLI (Buildkite CLI) - authenticated
- Claude Code CLI (optional, for AI-powered triage)

### Local Development

1. **Install Python dependencies**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

2. **Install frontend dependencies**:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

3. **Initialize database**:
   ```bash
   mkdir -p data
   # Database is auto-created on first run
   ```

4. **Run backend**:
   ```bash
   source .venv/bin/activate
   uvicorn app.main:app --reload
   ```

5. **Run frontend** (separate terminal):
   ```bash
   cd frontend
   npm run dev
   ```

6. **Open**: http://localhost:5173

### Docker Deployment

1. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

2. **Run with Docker Compose**:
   ```bash
   docker-compose up -d
   ```

3. **Open**: http://localhost:8000

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite database URL | `sqlite+aiosqlite:///./data/ci-bot.db` |
| `BUILDKITE_API_TOKEN` | Buildkite API token (optional) | - |
| `SLACK_WEBHOOK_URL` | Slack webhook for notifications | - |
| `CLAUDE_CODE_USE_VERTEX` | Use Vertex AI for Claude | `1` |
| `CLOUD_ML_REGION` | Vertex AI region | `us-east5` |
| `ANTHROPIC_VERTEX_PROJECT_ID` | Vertex AI project | `itpc-gcp-ai-eng-claude` |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/builds` | List builds |
| GET | `/api/builds/{number}` | Get build details |
| POST | `/api/builds/sync` | Sync builds from Buildkite |
| GET | `/api/builds/dashboard/summary` | Dashboard stats |
| GET | `/api/triages/failures/{id}/suggestions` | Get similar issues |
| POST | `/api/issues/failures/{id}/create` | Create GitHub issue |
| POST | `/api/issues/failures/{id}/link` | Link existing issue |
| POST | `/api/jobs/{id}/retry` | Retry failed job |

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Buildkite CI  │     │   GitHub API    │     │   Scheduler     │
│   (bk CLI)      │     │   (gh CLI)      │     │   (APScheduler) │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │     FastAPI Backend     │
                    │                         │
                    │  ┌─────────────────┐    │
                    │  │ Claude Service  │    │
                    │  │ (AI Triage)     │    │
                    │  └─────────────────┘    │
                    │                         │
                    │  ┌─────────────────┐    │
                    │  │ SQLite Database │    │
                    │  └─────────────────┘    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    React Frontend       │
                    │    (Tailwind CSS)       │
                    └─────────────────────────┘
```

## Triage Categories

- **infra**: Docker issues, network errors, HuggingFace 502, NCCL timeout, GPU errors
- **test**: AssertionError, accuracy regression, CUDA errors in tests, import errors

## License

Internal use only.

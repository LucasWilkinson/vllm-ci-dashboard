# CI Triage Bot

CI triage dashboard for vLLM nightly/daily builds.

## Terminology

### Buildkite Concepts
- **Build**: A Buildkite CI run (e.g., #51461). Has state: passed, failed, failing, running, scheduled
- **Job**: A Buildkite step within a build (e.g., "LoRA TP (Distributed)"). Jobs can pass, fail, or soft_fail. One job runs one or more test files
- **Soft Fail**: A job that failed but was marked as allowed to fail - should be excluded from failure counts

### Test Hierarchy
- **Test File**: A Python test module (e.g., `tests/lora/test_olmoe_tp.py`). One job may run multiple test files
- **Test**: A test function within a file (e.g., `test_olmoe_lora`). Full path: `tests/lora/test_olmoe_tp.py::test_olmoe_lora`
- **Test Case**: A parameterized instance of a test (e.g., `test_olmoe_lora[param1-param2]`). Multiple cases can fail within the same test

### Triage Concepts
- **Failure**: A triaged failed job. Has category (infra/test), error_message
- **Failing Test**: The specific test(s) that failed within a job. Can be a single test or a list if multiple tests failed
- **Nightly/Daily Build**: Full CI runs on main branch, identified by message containing "Full CI run - nightly" or "Full CI run - daily"
- **Current Issue**: A test failure from latest nightly/daily OR any main commit since then that hasn't passed on main since. Real-time view of ongoing broken tests.
- **Flaky**: A failure manually marked as flaky via `KnownFailure.is_flaky`
- **Resolved by PR**: A failure marked as fixed by a specific PR number, removing it from Current Issues

## Architecture

- **Backend**: FastAPI + SQLAlchemy + SQLite
- **Frontend**: React + TypeScript + Tailwind CSS
- **Triage**: Claude Sonnet analyzes job logs to categorize failures
- **KnownFailure**: Groups related failures, tracks lifecycle (open/resolved), links to GitHub issues

## Key Files

### Backend API
- `app/api/builds.py` - Build list, current issues endpoint
- `app/api/jobs.py` - Job details endpoint
- `app/api/triages.py` - Failure details, retriage
- `app/api/issues.py` - GitHub issues integration

### Services
- `app/services/triage.py` - Orchestrates triage using Claude
- `app/services/claude.py` - Claude CLI wrapper for log analysis
- `app/services/buildkite.py` - Buildkite API client
- `app/services/github.py` - GitHub API client
- `app/services/triage_status.py` - Triage status tracking
- `app/services/slack.py` - Slack notifications

### Models
- `app/models/build.py` - Build and Job SQLAlchemy models
- `app/models/failure.py` - Failure SQLAlchemy model
- `app/models/github.py` - GitHub issue model

### Frontend
- `frontend/src/components/Dashboard.tsx` - Main dashboard UI
- `frontend/src/components/BuildDetail.tsx` - Individual build view
- `frontend/src/components/Layout.tsx` - App layout

### Scripts
- `scripts/seed_from_buildkite.py` - Seed database by syncing real builds from Buildkite

## Data Flow

1. Sync builds from Buildkite API
2. For failed jobs, fetch logs and analyze with Claude
3. Store failure with category, type, message; assign to KnownFailure
4. Display in dashboard with expandable failure details

## Commands

```bash
# Dev server
source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend dev
cd frontend && npm run dev

# Frontend build
cd frontend && npm run build && cp -r dist/* ../static/

# Database migrations
source .venv/bin/activate && alembic upgrade head

# Run tests
source .venv/bin/activate && pytest

# Seed database from Buildkite (real builds)
source .venv/bin/activate && python scripts/seed_from_buildkite.py

# Linting
source .venv/bin/activate && ruff check app/
```

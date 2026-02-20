from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite+aiosqlite:///./data/ci-bot.db"

    github_repo: str = "vllm-project/vllm"
    github_webhook_secret: str | None = None
    buildkite_org: str = "vllm"
    buildkite_pipeline: str = "ci"
    buildkite_api_token: str | None = None
    buildkite_webhook_token: str | None = None
    buildkite_test_suite: str = "ci-1"

    claude_code_use_vertex: bool = True
    cloud_ml_region: str = "us-east5"
    anthropic_vertex_project_id: str = "itpc-gcp-ai-eng-claude"


settings = Settings()

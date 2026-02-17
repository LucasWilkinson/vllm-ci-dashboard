"""Seed with full CI runs only."""
import asyncio
from datetime import datetime, timedelta, UTC

from sqlalchemy import text

from app.database import async_session_maker, engine, Base
from app.models import Build, Job, Failure, GitHubIssue, ErrorSignature


async def seed_data():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        now = datetime.now(UTC)

        # Build 1: Recent nightly - failed with NCCL timeout
        build1 = Build(
            buildkite_build_number=54321,
            build_type="nightly",
            state="failed",
            commit_sha="a1b2c3d4e5f6",
            branch="main",
            message="Nightly full CI",
            web_url="https://buildkite.com/vllm/ci/builds/54321",
            triage_status="completed",
            created_at=now - timedelta(hours=2),
            synced_at=now,
        )
        session.add(build1)
        await session.flush()

        job1_1 = Job(
            build_id=build1.id,
            buildkite_job_id="job-54321-001",
            name="test-distributed-tp4",
            state="failed",
            step_key="test_distributed_tp4",
            web_url="https://buildkite.com/vllm/ci/builds/54321#001",
        )
        job1_2 = Job(
            build_id=build1.id,
            buildkite_job_id="job-54321-002",
            name="test-models-gpt2",
            state="passed",
            step_key="test_models_gpt2",
            web_url="https://buildkite.com/vllm/ci/builds/54321#002",
        )
        job1_3 = Job(
            build_id=build1.id,
            buildkite_job_id="job-54321-003",
            name="test-samplers",
            state="passed",
            step_key="test_samplers",
            web_url="https://buildkite.com/vllm/ci/builds/54321#003",
        )
        session.add_all([job1_1, job1_2, job1_3])
        await session.flush()

        failure1 = Failure(
            job_id=job1_1.id,
            failure_category="infra",
            failure_type="nccl_timeout",
            error_signature="NCCLTimeout:distributed_tp4",
            error_message="NCCL timeout during TP=4 initialization. Collective operation timed out after 300s.",
            root_cause="Network fabric congestion between GPU nodes causing NCCL collective timeouts.",
            is_flaky=True,
            log_excerpt="""[2026-02-12 18:23:45] Initializing distributed with TP=4
[2026-02-12 18:23:46] Setting up NCCL communicator...
[2026-02-12 18:28:46] ERROR: NCCL timeout after 300 seconds
[2026-02-12 18:28:46] RuntimeError: NCCL communicator was aborted
[2026-02-12 18:28:47] Test FAILED""",
        )
        session.add(failure1)

        # Build 2: Yesterday nightly - passed
        build2 = Build(
            buildkite_build_number=54320,
            build_type="nightly",
            state="passed",
            commit_sha="f6e5d4c3b2a1",
            branch="main",
            message="Nightly full CI",
            web_url="https://buildkite.com/vllm/ci/builds/54320",
            triage_status="completed",
            created_at=now - timedelta(hours=26),
            synced_at=now,
        )
        session.add(build2)
        await session.flush()

        job2_1 = Job(
            build_id=build2.id,
            buildkite_job_id="job-54320-001",
            name="test-distributed-tp4",
            state="passed",
            step_key="test_distributed_tp4",
            web_url="https://buildkite.com/vllm/ci/builds/54320#001",
        )
        job2_2 = Job(
            build_id=build2.id,
            buildkite_job_id="job-54320-002",
            name="test-models-gpt2",
            state="passed",
            step_key="test_models_gpt2",
            web_url="https://buildkite.com/vllm/ci/builds/54320#002",
        )
        session.add_all([job2_1, job2_2])

        # Build 3: Two days ago - failed with assertion error
        build3 = Build(
            buildkite_build_number=54319,
            build_type="nightly",
            state="failed",
            commit_sha="1a2b3c4d5e6f",
            branch="main",
            message="Nightly full CI",
            web_url="https://buildkite.com/vllm/ci/builds/54319",
            triage_status="completed",
            created_at=now - timedelta(hours=50),
            synced_at=now,
        )
        session.add(build3)
        await session.flush()

        job3_1 = Job(
            build_id=build3.id,
            buildkite_job_id="job-54319-001",
            name="test-attention-backends",
            state="failed",
            step_key="test_attention",
            web_url="https://buildkite.com/vllm/ci/builds/54319#001",
        )
        job3_2 = Job(
            build_id=build3.id,
            buildkite_job_id="job-54319-002",
            name="test-distributed-tp2",
            state="passed",
            step_key="test_distributed_tp2",
            web_url="https://buildkite.com/vllm/ci/builds/54319#002",
        )
        session.add_all([job3_1, job3_2])
        await session.flush()

        failure3 = Failure(
            job_id=job3_1.id,
            failure_category="test",
            failure_type="assertion_error",
            error_signature="AssertionError:flash_attn_output",
            error_message="Flash attention output mismatch: relative error 0.015 > tolerance 0.01",
            root_cause="Numerical precision regression in flash attention kernel after recent commit.",
            is_flaky=False,
            log_excerpt="""[2026-02-10 20:15:22] Running test_flash_attention_v2_fp16...
[2026-02-10 20:15:24] Comparing outputs...
[2026-02-10 20:15:24] AssertionError: Output mismatch
  Max relative error: 0.015
  Tolerance: 0.01
  at tests/kernels/test_attention.py:234
[2026-02-10 20:15:24] FAILED""",
        )
        session.add(failure3)

        # Build 4: Three days ago - failed with docker issue
        build4 = Build(
            buildkite_build_number=54318,
            build_type="nightly",
            state="failed",
            commit_sha="6f5e4d3c2b1a",
            branch="main",
            message="Nightly full CI",
            web_url="https://buildkite.com/vllm/ci/builds/54318",
            triage_status="pending",
            created_at=now - timedelta(hours=74),
            synced_at=now,
        )
        session.add(build4)
        await session.flush()

        job4_1 = Job(
            build_id=build4.id,
            buildkite_job_id="job-54318-001",
            name="build-docker-image",
            state="failed",
            step_key="docker_build",
            web_url="https://buildkite.com/vllm/ci/builds/54318#001",
        )
        session.add(job4_1)
        await session.flush()

        failure4 = Failure(
            job_id=job4_1.id,
            failure_category="infra",
            failure_type="docker_pull_timeout",
            error_signature="DockerError:ghcr_timeout",
            error_message="Docker pull from ghcr.io timed out after 300s",
            root_cause="GitHub Container Registry rate limiting or network connectivity issues.",
            is_flaky=True,
            log_excerpt="""[2026-02-09 18:12:33] Pulling ghcr.io/vllm-project/vllm-ci:base...
[2026-02-09 18:17:33] ERROR: context deadline exceeded
[2026-02-09 18:17:33] docker: Error response from daemon: Get https://ghcr.io/v2/: net/http: request canceled
[2026-02-09 18:17:34] Build FAILED""",
        )
        session.add(failure4)

        # GitHub issues
        issue1 = GitHubIssue(
            github_issue_number=15234,
            title="[CI] NCCL timeout in TP=4 distributed tests",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/15234",
        )
        issue2 = GitHubIssue(
            github_issue_number=15198,
            title="[CI] Flash attention numerical precision regression",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/15198",
        )
        issue3 = GitHubIssue(
            github_issue_number=14876,
            title="[CI] Docker pull timeouts from ghcr.io",
            state="closed",
            github_issue_url="https://github.com/vllm-project/vllm/issues/14876",
        )
        session.add_all([issue1, issue2, issue3])
        await session.flush()

        # Error signatures
        sig1 = ErrorSignature(
            signature_hash="NCCLTimeout:distributed_tp4",
            occurrence_count=8,
            associated_issue_id=issue1.id,
        )
        sig2 = ErrorSignature(
            signature_hash="AssertionError:flash_attn_output",
            occurrence_count=3,
            associated_issue_id=issue2.id,
        )
        sig3 = ErrorSignature(
            signature_hash="DockerError:ghcr_timeout",
            occurrence_count=12,
            associated_issue_id=issue3.id,
        )
        session.add_all([sig1, sig2, sig3])

        await session.commit()
        print("Seeded 4 full CI run builds:")
        print("  #54321 - failed (NCCL timeout)")
        print("  #54320 - passed")
        print("  #54319 - failed (assertion error)")
        print("  #54318 - failed (docker timeout)")


if __name__ == "__main__":
    asyncio.run(seed_data())

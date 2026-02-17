"""Seed mock data for local testing."""
import asyncio
from datetime import datetime, timedelta

from app.database import async_session_maker, init_db
from app.models import Build, Job, Failure, GitHubIssue, ErrorSignature


async def seed_data():
    await init_db()

    async with async_session_maker() as session:
        now = datetime.utcnow()

        build1 = Build(
            buildkite_build_number=12345,
            build_type="nightly",
            state="failed",
            commit_sha="abc123def456",
            branch="main",
            message="[nightly] Run full test suite",
            web_url="https://buildkite.com/vllm/ci/builds/12345",
            triage_status="completed",
            created_at=now - timedelta(hours=2),
            synced_at=now,
        )
        session.add(build1)
        await session.flush()

        job1 = Job(
            build_id=build1.id,
            buildkite_job_id="job-001-abc",
            name="test-distributed-tp2",
            state="failed",
            step_key="test_distributed_tp2",
            web_url="https://buildkite.com/vllm/ci/builds/12345#job-001",
        )
        session.add(job1)

        job2 = Job(
            build_id=build1.id,
            buildkite_job_id="job-002-def",
            name="test-model-runner",
            state="passed",
            step_key="test_model_runner",
            web_url="https://buildkite.com/vllm/ci/builds/12345#job-002",
        )
        session.add(job2)

        job3 = Job(
            build_id=build1.id,
            buildkite_job_id="job-003-ghi",
            name="test-attention-backend",
            state="failed",
            step_key="test_attention",
            web_url="https://buildkite.com/vllm/ci/builds/12345#job-003",
        )
        session.add(job3)
        await session.flush()

        failure1 = Failure(
            job_id=job1.id,
            failure_category="infra",
            failure_type="nccl_timeout",
            error_signature="NCCLTimeout:distributed_init",
            error_message="NCCL timeout during distributed initialization. Workers failed to sync within 300s.",
            root_cause="GPU communication issue, possibly due to network congestion or faulty InfiniBand connection.",
            is_flaky=True,
            log_excerpt="""[2024-01-15 10:23:45] Starting distributed test with TP=2
[2024-01-15 10:23:46] Initializing NCCL communicator...
[2024-01-15 10:28:46] ERROR: NCCL timeout after 300 seconds
[2024-01-15 10:28:46] torch.distributed.DistBackendError: NCCL error in: ../torch/lib/c10d/ProcessGroupNCCL.cpp:123
[2024-01-15 10:28:47] Test failed with exit code 1""",
        )
        session.add(failure1)

        failure2 = Failure(
            job_id=job3.id,
            failure_category="test",
            failure_type="assertion_error",
            error_signature="AssertionError:attention_output_mismatch",
            error_message="Attention output mismatch: expected shape (32, 64, 128), got (32, 64, 127)",
            root_cause="Off-by-one error in attention computation output dimensions.",
            is_flaky=False,
            log_excerpt="""[2024-01-15 10:15:22] Running test_flash_attention_v2...
[2024-01-15 10:15:23] Input shape: (32, 64, 128)
[2024-01-15 10:15:24] AssertionError: Attention output mismatch
  Expected: (32, 64, 128)
  Got: (32, 64, 127)
  at tests/test_attention.py:145
[2024-01-15 10:15:24] FAILED test_flash_attention_v2""",
        )
        session.add(failure2)
        await session.flush()

        build2 = Build(
            buildkite_build_number=12344,
            build_type="daily",
            state="passed",
            commit_sha="def789ghi012",
            branch="main",
            message="[daily] Benchmark run",
            web_url="https://buildkite.com/vllm/ci/builds/12344",
            triage_status="completed",
            created_at=now - timedelta(hours=6),
            synced_at=now,
        )
        session.add(build2)

        build3 = Build(
            buildkite_build_number=12343,
            build_type="nightly",
            state="failed",
            commit_sha="ghi345jkl678",
            branch="main",
            message="[nightly] Full CI run",
            web_url="https://buildkite.com/vllm/ci/builds/12343",
            triage_status="pending",
            created_at=now - timedelta(hours=26),
            synced_at=now,
        )
        session.add(build3)
        await session.flush()

        job4 = Job(
            build_id=build3.id,
            buildkite_job_id="job-004-jkl",
            name="test-docker-build",
            state="failed",
            step_key="docker_build",
            web_url="https://buildkite.com/vllm/ci/builds/12343#job-004",
        )
        session.add(job4)
        await session.flush()

        failure3 = Failure(
            job_id=job4.id,
            failure_category="infra",
            failure_type="docker_pull_failed",
            error_signature="DockerError:pull_timeout",
            error_message="Docker pull failed: connection timeout to ghcr.io after 120s",
            root_cause="Network issue connecting to GitHub Container Registry.",
            is_flaky=True,
            log_excerpt="""[2024-01-14 08:12:33] Pulling image ghcr.io/vllm-project/vllm:base...
[2024-01-14 08:14:33] ERROR: pull timeout after 120s
[2024-01-14 08:14:33] docker: Error response from daemon: Get https://ghcr.io/v2/: net/http: request canceled""",
        )
        session.add(failure3)

        issue1 = GitHubIssue(
            github_issue_number=9876,
            title="[CI] NCCL timeout in distributed tests",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/9876",
        )
        session.add(issue1)

        issue2 = GitHubIssue(
            github_issue_number=9543,
            title="[CI] Docker pull failures from ghcr.io",
            state="closed",
            github_issue_url="https://github.com/vllm-project/vllm/issues/9543",
        )
        session.add(issue2)
        await session.flush()

        sig1 = ErrorSignature(
            signature_hash="NCCLTimeout:distributed_init",
            occurrence_count=5,
            associated_issue_id=issue1.id,
        )
        session.add(sig1)

        sig2 = ErrorSignature(
            signature_hash="DockerError:pull_timeout",
            occurrence_count=3,
            associated_issue_id=issue2.id,
        )
        session.add(sig2)

        await session.commit()
        print("Mock data seeded successfully!")
        print(f"  - 3 builds")
        print(f"  - 4 jobs (2 failed)")
        print(f"  - 3 failures")
        print(f"  - 2 GitHub issues")
        print(f"  - 2 error signatures")


if __name__ == "__main__":
    asyncio.run(seed_data())

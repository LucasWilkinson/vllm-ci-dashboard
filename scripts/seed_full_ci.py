"""Seed with realistic full CI runs - nightly and daily with many jobs."""
import asyncio
from datetime import datetime, timedelta, UTC

from app.database import async_session_maker, engine, Base
from app.models import Build, Job, Failure, GitHubIssue, ErrorSignature
from app.models.github import FailureIssueLink

COMMON_JOBS = [
    "build-docker-image",
    "lint-and-format",
    "mypy-check",
    "test-basic-correctness-1",
    "test-basic-correctness-2",
    "test-core-models",
    "test-distributed-tp2",
    "test-distributed-tp4",
    "test-distributed-pp2",
    "test-samplers",
    "test-attention-backends",
    "test-quantization-awq",
    "test-quantization-gptq",
    "test-speculative-decoding",
    "test-lora",
    "test-prefix-caching",
    "test-chunked-prefill",
    "test-vision-language",
    "test-encoder-decoder",
    "test-tool-calling",
    "test-structured-output",
    "test-async-engine",
    "test-openai-api",
    "test-embeddings",
    "test-pooling",
    "docs-build",
]


async def seed_data():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        now = datetime.now(UTC)

        # === Build 1: Latest nightly - failed ===
        # Using real vLLM CI build numbers for working links
        build1 = Build(
            buildkite_build_number=51385,
            build_type="nightly",
            state="failed",
            commit_sha="f120bd42d3",
            branch="main",
            message="[Kernel] Support Flashinfer trtllm fused MoE non gated FP8 & NVFP4",
            web_url="https://buildkite.com/vllm/ci/builds/51385",
            triage_status="completed",
            created_at=now - timedelta(hours=2),
            synced_at=now,
        )
        session.add(build1)
        await session.flush()

        failed_jobs_1 = {"test-distributed-tp4", "test-attention-backends"}
        jobs_build1 = {}
        for i, job_name in enumerate(COMMON_JOBS):
            state = "failed" if job_name in failed_jobs_1 else "passed"
            job = Job(
                build_id=build1.id,
                buildkite_job_id=f"job-51385-{i:03d}",
                name=job_name,
                state=state,
                step_key=job_name.replace("-", "_"),
                web_url=f"https://buildkite.com/vllm/ci/builds/51385",
            )
            session.add(job)
            jobs_build1[job_name] = job
        await session.flush()

        failure1 = Failure(
            job_id=jobs_build1["test-distributed-tp4"].id,
            failure_category="infra",
            failure_type="nccl_timeout",
            error_signature="NCCLTimeout:distributed_tp4",
            error_message="NCCL timeout during TP=4 initialization. Collective operation timed out after 300s.",
            root_cause="Network fabric congestion between GPU nodes.",
            is_flaky=True,
            log_excerpt="""[2026-02-12 18:23:45] Initializing distributed with TP=4
[2026-02-12 18:23:46] Setting up NCCL communicator...
[2026-02-12 18:28:46] ERROR: NCCL timeout after 300 seconds
[2026-02-12 18:28:46] RuntimeError: NCCL communicator was aborted
[2026-02-12 18:28:47] Test FAILED""",
        )
        session.add(failure1)

        failure1b = Failure(
            job_id=jobs_build1["test-attention-backends"].id,
            failure_category="test",
            failure_type="assertion_error",
            error_signature="AssertionError:flash_attn_output",
            error_message="Flash attention output mismatch: relative error 0.015 > tolerance 0.01",
            root_cause="Numerical precision issue in flash attention kernel.",
            is_flaky=False,
            log_excerpt="""[2026-02-12 18:15:22] Running test_flash_attention_v2_fp16...
[2026-02-12 18:15:24] AssertionError: Output mismatch
  Max relative error: 0.015, Tolerance: 0.01
[2026-02-12 18:15:24] FAILED""",
        )
        session.add(failure1b)

        # === Build 2: Latest daily - passed ===
        build2 = Build(
            buildkite_build_number=51383,
            build_type="daily",
            state="passed",
            commit_sha="dbf9d0d174",
            branch="main",
            message="Merge remote-tracking branch 'origin/main' into akaratza_fix_voxtral",
            web_url="https://buildkite.com/vllm/ci/builds/51383",
            triage_status="completed",
            created_at=now - timedelta(hours=8),
            synced_at=now,
        )
        session.add(build2)
        await session.flush()

        for i, job_name in enumerate(COMMON_JOBS):
            job = Job(
                build_id=build2.id,
                buildkite_job_id=f"job-51383-{i:03d}",
                name=job_name,
                state="passed",
                step_key=job_name.replace("-", "_"),
                web_url=f"https://buildkite.com/vllm/ci/builds/51383",
            )
            session.add(job)

        # === Build 3: Yesterday nightly - failed ===
        build3 = Build(
            buildkite_build_number=51380,
            build_type="nightly",
            state="failed",
            commit_sha="a1b2c3d4e5",
            branch="main",
            message="Nightly full CI",
            web_url="https://buildkite.com/vllm/ci/builds/51380",
            triage_status="completed",
            created_at=now - timedelta(hours=26),
            synced_at=now,
        )
        session.add(build3)
        await session.flush()

        failed_jobs_3 = {"test-quantization-awq", "test-vision-language", "test-lora"}
        jobs_build3 = {}
        for i, job_name in enumerate(COMMON_JOBS):
            state = "failed" if job_name in failed_jobs_3 else "passed"
            job = Job(
                build_id=build3.id,
                buildkite_job_id=f"job-51380-{i:03d}",
                name=job_name,
                state=state,
                step_key=job_name.replace("-", "_"),
                web_url=f"https://buildkite.com/vllm/ci/builds/51380",
            )
            session.add(job)
            jobs_build3[job_name] = job
        await session.flush()

        failure3a = Failure(
            job_id=jobs_build3["test-quantization-awq"].id,
            failure_category="test",
            failure_type="import_error",
            error_signature="ImportError:autoawq",
            error_message="ModuleNotFoundError: No module named 'autoawq'",
            root_cause="AutoAWQ package not installed in test environment.",
            is_flaky=False,
            log_excerpt="""[2026-02-11 18:05:12] Running AWQ quantization tests...
[2026-02-11 18:05:13] ModuleNotFoundError: No module named 'autoawq'
[2026-02-11 18:05:13] FAILED test_awq_quantization""",
        )
        session.add(failure3a)

        failure3b = Failure(
            job_id=jobs_build3["test-vision-language"].id,
            failure_category="infra",
            failure_type="hf_download_error",
            error_signature="HFError:model_download_502",
            error_message="HuggingFace Hub returned 502 Bad Gateway when downloading model weights",
            root_cause="HuggingFace Hub temporary outage.",
            is_flaky=True,
            log_excerpt="""[2026-02-11 18:22:45] Downloading llava-hf/llava-1.5-7b-hf...
[2026-02-11 18:22:48] HTTPError: 502 Server Error: Bad Gateway
[2026-02-11 18:22:48] huggingface_hub.utils.HfHubHTTPError""",
        )
        session.add(failure3b)

        failure3c = Failure(
            job_id=jobs_build3["test-lora"].id,
            failure_category="test",
            failure_type="cuda_error",
            error_signature="CUDAError:illegal_memory_access",
            error_message="CUDA error: an illegal memory access was encountered",
            root_cause="Memory corruption in LoRA weight application.",
            is_flaky=False,
            log_excerpt="""[2026-02-11 18:35:12] Running test_lora_llama...
[2026-02-11 18:35:45] RuntimeError: CUDA error: an illegal memory access
[2026-02-11 18:35:45] FAILED""",
        )
        session.add(failure3c)

        # === Build 4: Yesterday daily - failed ===
        build4 = Build(
            buildkite_build_number=51375,
            build_type="daily",
            state="failed",
            commit_sha="e5f6a1b2c3d4",
            branch="main",
            message="Daily full CI",
            web_url="https://buildkite.com/vllm/ci/builds/51375",
            triage_status="pending",
            created_at=now - timedelta(hours=32),
            synced_at=now,
        )
        session.add(build4)
        await session.flush()

        failed_jobs_4 = {"build-docker-image"}
        jobs_build4 = {}
        for i, job_name in enumerate(COMMON_JOBS):
            state = "failed" if job_name in failed_jobs_4 else "passed"
            job = Job(
                build_id=build4.id,
                buildkite_job_id=f"job-51375-{i:03d}",
                name=job_name,
                state=state,
                step_key=job_name.replace("-", "_"),
                web_url=f"https://buildkite.com/vllm/ci/builds/51375",
            )
            session.add(job)
            jobs_build4[job_name] = job
        await session.flush()

        failure4 = Failure(
            job_id=jobs_build4["build-docker-image"].id,
            failure_category="infra",
            failure_type="docker_pull_timeout",
            error_signature="DockerError:ghcr_timeout",
            error_message="Docker pull from ghcr.io timed out after 300s",
            root_cause="GitHub Container Registry rate limiting.",
            is_flaky=True,
            log_excerpt="""[2026-02-11 10:12:33] Pulling ghcr.io/vllm-project/vllm-ci:base...
[2026-02-11 10:17:33] ERROR: context deadline exceeded
[2026-02-11 10:17:33] docker: Error response from daemon: net/http: request canceled""",
        )
        session.add(failure4)

        # GitHub issues - created_at set AFTER builds to simulate being filed after CI failure
        issue1 = GitHubIssue(
            github_issue_number=15234,
            title="[CI] NCCL timeout in TP=4 distributed tests",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/15234",
            created_at=now - timedelta(hours=1),  # Created after build 1
        )
        issue2 = GitHubIssue(
            github_issue_number=15198,
            title="[CI] Flash attention numerical precision regression",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/15198",
            created_at=now - timedelta(hours=1),  # Created after build 1
        )
        issue3 = GitHubIssue(
            github_issue_number=14876,
            title="[CI] Docker pull timeouts from ghcr.io",
            state="closed",
            github_issue_url="https://github.com/vllm-project/vllm/issues/14876",
            created_at=now - timedelta(hours=30),  # Created after build 4
        )
        issue4 = GitHubIssue(
            github_issue_number=15301,
            title="[CI] HuggingFace Hub 502 errors in VLM tests",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/15301",
            created_at=now - timedelta(hours=24),  # Created after build 3
        )
        session.add_all([issue1, issue2, issue3, issue4])
        await session.flush()

        # Link failures to issues
        links = [
            FailureIssueLink(failure_id=failure1.id, github_issue_id=issue1.id, link_type="created"),
            FailureIssueLink(failure_id=failure1b.id, github_issue_id=issue2.id, link_type="created"),
            FailureIssueLink(failure_id=failure3b.id, github_issue_id=issue4.id, link_type="associated"),
            FailureIssueLink(failure_id=failure4.id, github_issue_id=issue3.id, link_type="associated"),
        ]
        session.add_all(links)

        # Error signatures
        sigs = [
            ErrorSignature(signature_hash="NCCLTimeout:distributed_tp4", occurrence_count=8, associated_issue_id=issue1.id),
            ErrorSignature(signature_hash="AssertionError:flash_attn_output", occurrence_count=3, associated_issue_id=issue2.id),
            ErrorSignature(signature_hash="DockerError:ghcr_timeout", occurrence_count=12, associated_issue_id=issue3.id),
            ErrorSignature(signature_hash="HFError:model_download_502", occurrence_count=5, associated_issue_id=issue4.id),
        ]
        session.add_all(sigs)

        await session.commit()

        print("Seeded 4 full CI builds with realistic job counts:")
        print(f"  #51385 (nightly) - failed - {len(COMMON_JOBS)} jobs, 2 failures")
        print(f"  #51383 (daily)   - passed - {len(COMMON_JOBS)} jobs")
        print(f"  #51380 (nightly) - failed - {len(COMMON_JOBS)} jobs, 3 failures")
        print(f"  #51375 (daily)   - failed - {len(COMMON_JOBS)} jobs, 1 failure")


if __name__ == "__main__":
    asyncio.run(seed_data())

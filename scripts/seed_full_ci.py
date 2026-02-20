"""Seed with realistic full CI runs - nightly and daily with many jobs."""
import asyncio
from datetime import datetime, timedelta, UTC

from app.database import async_session_maker, engine, Base
from app.models import Build, Job, Failure, KnownFailure, GitHubIssue, ErrorSignature
from app.models.github import FailureIssueLink

# (step_key, display_name) pairs
COMMON_JOBS = [
    ("build-docker-image", "Build Docker Image"),
    ("lint-and-format", "Lint & Format Check"),
    ("mypy-check", "MyPy Type Check"),
    ("test-basic-correctness-1", "Basic Correctness 1/2"),
    ("test-basic-correctness-2", "Basic Correctness 2/2"),
    ("test-core-models", "Core Models"),
    ("test-distributed-tp2", "Distributed TP=2"),
    ("test-distributed-tp4", "Distributed TP=4"),
    ("test-distributed-pp2", "Distributed PP=2"),
    ("test-samplers", "Samplers"),
    ("test-attention-backends", "Attention Backends"),
    ("test-quantization-awq", "Quantization AWQ"),
    ("test-quantization-gptq", "Quantization GPTQ"),
    ("test-speculative-decoding", "Speculative Decoding"),
    ("test-lora", "LoRA"),
    ("test-prefix-caching", "Prefix Caching"),
    ("test-chunked-prefill", "Chunked Prefill"),
    ("test-vision-language", "Vision-Language Models"),
    ("test-encoder-decoder", "Encoder-Decoder"),
    ("test-tool-calling", "Tool Calling"),
    ("test-structured-output", "Structured Output"),
    ("test-async-engine", "Async Engine"),
    ("test-openai-api", "OpenAI API Compatibility"),
    ("test-embeddings", "Embeddings"),
    ("test-pooling", "Pooling"),
    ("docs-build", "Documentation Build"),
]


async def seed_data():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        now = datetime.now(UTC)

        # === Build 1: Latest nightly - failed ===
        build1 = Build(
            buildkite_build_number=51385,
            build_type="nightly",
            state="failed",
            commit_sha="4a8e2f1b9c3d7e6a5f0b2c4d8e1a3f5b7c9d0e2a",
            branch="main",
            message="Full CI run - nightly",
            web_url="https://buildkite.com/vllm/ci/builds/51385",
            triage_status="completed",
            created_at=now - timedelta(hours=2),
            synced_at=now,
        )
        session.add(build1)
        await session.flush()

        failed_jobs_1 = {"test-distributed-tp4", "test-attention-backends"}
        jobs_build1 = {}
        for i, (step_key, display_name) in enumerate(COMMON_JOBS):
            state = "failed" if step_key in failed_jobs_1 else "passed"
            job = Job(
                build_id=build1.id,
                buildkite_job_id=f"job-51385-{i:03d}",
                name=display_name,
                state=state,
                step_key=step_key,
                web_url=f"https://buildkite.com/vllm/ci/builds/51385#job-51385-{i:03d}",
            )
            session.add(job)
            jobs_build1[step_key] = job
        await session.flush()

        failure1 = Failure(
            job_id=jobs_build1["test-distributed-tp4"].id,
            failure_category="infra",
            failure_type="nccl_timeout",
            failing_test="distributed/test_comm_ops.py::test_allreduce",
            error_signature="test_comm_ops.py::test_allreduce:RuntimeError:NCCL_timeout_300s",
            error_message="NCCL timeout during TP=4 initialization. Collective operation timed out after 300s.",
            root_cause="Network fabric congestion between GPU nodes.",
            is_flaky=True,
            log_excerpt="""1234\t[2026-02-12 18:23:45] Initializing distributed with TP=4
1235\t[2026-02-12 18:23:46] Setting up NCCL communicator...
1236\t[2026-02-12 18:28:46] ERROR: NCCL timeout after 300 seconds
1237\t[2026-02-12 18:28:46] RuntimeError: NCCL communicator was aborted
1238\t[2026-02-12 18:28:47] Test FAILED""",
        )
        session.add(failure1)

        failure1b = Failure(
            job_id=jobs_build1["test-attention-backends"].id,
            failure_category="test",
            failure_type="assertion_error",
            failing_test="attention/test_flash_attn.py::test_flash_attention_v2_fp16",
            error_signature="test_flash_attn.py::test_flash_attention_v2_fp16:AssertionError:relative_error_0.015",
            error_message="Flash attention output mismatch: relative error 0.015 > tolerance 0.01",
            root_cause="Numerical precision issue in flash attention kernel.",
            is_flaky=False,
            log_excerpt="""450\tRunning test_flash_attention_v2_fp16...
451\tAssertionError: Output mismatch
452\t  Max relative error: 0.015, Tolerance: 0.01
453\tFAILED attention/test_flash_attn.py::test_flash_attention_v2_fp16""",
        )
        session.add(failure1b)

        # === Build 2: Latest daily - passed ===
        build2 = Build(
            buildkite_build_number=51383,
            build_type="daily",
            state="passed",
            commit_sha="7b3c9e1d4a6f8e2b5c0d7a3e9f1b4c6d8a0e2f4b",
            branch="main",
            message="Full CI run - daily",
            web_url="https://buildkite.com/vllm/ci/builds/51383",
            triage_status="completed",
            created_at=now - timedelta(hours=8),
            synced_at=now,
        )
        session.add(build2)
        await session.flush()

        for i, (step_key, display_name) in enumerate(COMMON_JOBS):
            job = Job(
                build_id=build2.id,
                buildkite_job_id=f"job-51383-{i:03d}",
                name=display_name,
                state="passed",
                step_key=step_key,
                web_url=f"https://buildkite.com/vllm/ci/builds/51383#job-51383-{i:03d}",
            )
            session.add(job)

        # === Build 3: Yesterday nightly - failed ===
        build3 = Build(
            buildkite_build_number=51380,
            build_type="nightly",
            state="failed",
            commit_sha="2d5e8a1c4b7f3e9d6a0c3f5b8e1a4d7c9b2e0f3a",
            branch="main",
            message="Full CI run - nightly",
            web_url="https://buildkite.com/vllm/ci/builds/51380",
            triage_status="completed",
            created_at=now - timedelta(hours=26),
            synced_at=now,
        )
        session.add(build3)
        await session.flush()

        failed_jobs_3 = {"test-quantization-awq", "test-vision-language", "test-lora"}
        jobs_build3 = {}
        for i, (step_key, display_name) in enumerate(COMMON_JOBS):
            state = "failed" if step_key in failed_jobs_3 else "passed"
            job = Job(
                build_id=build3.id,
                buildkite_job_id=f"job-51380-{i:03d}",
                name=display_name,
                state=state,
                step_key=step_key,
                web_url=f"https://buildkite.com/vllm/ci/builds/51380#job-51380-{i:03d}",
            )
            session.add(job)
            jobs_build3[step_key] = job
        await session.flush()

        failure3a = Failure(
            job_id=jobs_build3["test-quantization-awq"].id,
            failure_category="test",
            failure_type="import_error",
            failing_test="quantization/test_awq.py::test_awq_quantization",
            error_signature="test_awq.py::test_awq_quantization:ModuleNotFoundError:autoawq",
            error_message="ModuleNotFoundError: No module named 'autoawq'",
            root_cause="AutoAWQ package not installed in test environment.",
            is_flaky=False,
            log_excerpt="""100\tRunning AWQ quantization tests...
101\tModuleNotFoundError: No module named 'autoawq'
102\tFAILED quantization/test_awq.py::test_awq_quantization""",
        )
        session.add(failure3a)

        failure3b = Failure(
            job_id=jobs_build3["test-vision-language"].id,
            failure_category="infra",
            failure_type="hf_download_error",
            failing_test="models/test_llava.py::test_llava_generation",
            error_signature="test_llava.py::test_llava_generation:HfHubHTTPError:502",
            error_message="HuggingFace Hub returned 502 Bad Gateway when downloading model weights",
            root_cause="HuggingFace Hub temporary outage.",
            is_flaky=True,
            log_excerpt="""200\tDownloading llava-hf/llava-1.5-7b-hf...
201\tHTTPError: 502 Server Error: Bad Gateway
202\thuggingface_hub.utils.HfHubHTTPError""",
        )
        session.add(failure3b)

        failure3c = Failure(
            job_id=jobs_build3["test-lora"].id,
            failure_category="test",
            failure_type="cuda_error",
            failing_test="lora/test_lora_llama.py::test_lora_weight_application",
            error_signature="test_lora_llama.py::test_lora_weight_application:RuntimeError:CUDA_illegal_memory",
            error_message="CUDA error: an illegal memory access was encountered",
            root_cause="Memory corruption in LoRA weight application.",
            is_flaky=False,
            log_excerpt="""350\tRunning test_lora_llama...
351\tRuntimeError: CUDA error: an illegal memory access
352\tFAILED lora/test_lora_llama.py::test_lora_weight_application""",
        )
        session.add(failure3c)

        # === Build 4: Yesterday daily - failed ===
        build4 = Build(
            buildkite_build_number=51375,
            build_type="daily",
            state="failed",
            commit_sha="9c1d3e5f7a2b4c6d8e0a1b3c5d7e9f0a2b4c6d8e",
            branch="main",
            message="Full CI run - daily",
            web_url="https://buildkite.com/vllm/ci/builds/51375",
            triage_status="pending",
            created_at=now - timedelta(hours=32),
            synced_at=now,
        )
        session.add(build4)
        await session.flush()

        failed_jobs_4 = {"build-docker-image"}
        jobs_build4 = {}
        for i, (step_key, display_name) in enumerate(COMMON_JOBS):
            state = "failed" if step_key in failed_jobs_4 else "passed"
            job = Job(
                build_id=build4.id,
                buildkite_job_id=f"job-51375-{i:03d}",
                name=display_name,
                state=state,
                step_key=step_key,
                web_url=f"https://buildkite.com/vllm/ci/builds/51375#job-51375-{i:03d}",
            )
            session.add(job)
            jobs_build4[step_key] = job
        await session.flush()

        failure4 = Failure(
            job_id=jobs_build4["build-docker-image"].id,
            failure_category="infra",
            failure_type="docker_pull_timeout",
            error_signature="DockerError:ghcr_timeout",
            error_message="Docker pull from ghcr.io timed out after 300s",
            root_cause="GitHub Container Registry rate limiting.",
            is_flaky=True,
            log_excerpt="""50\tPulling ghcr.io/vllm-project/vllm-ci:base...
51\tERROR: context deadline exceeded
52\tdocker: Error response from daemon: net/http: request canceled""",
        )
        session.add(failure4)

        # GitHub issues - real vllm issues
        issue_nccl = GitHubIssue(
            github_issue_number=6042,
            title="[Bug]: call for stack trace for \"Watchdog caught collective operation timeout\"",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/6042",
            created_at=now - timedelta(hours=1),
        )
        issue_cuda = GitHubIssue(
            github_issue_number=22662,
            title="[CI Failure]: V1 Test",
            state="open",
            github_issue_url="https://github.com/vllm-project/vllm/issues/22662",
            created_at=now - timedelta(hours=24),
        )
        session.add_all([issue_nccl, issue_cuda])
        await session.flush()

        # Link failures to issues
        links = [
            FailureIssueLink(failure_id=failure1.id, github_issue_id=issue_nccl.id, link_type="associated"),
            FailureIssueLink(failure_id=failure3c.id, github_issue_id=issue_cuda.id, link_type="associated"),
        ]
        session.add_all(links)

        # Error signatures
        sigs = [
            ErrorSignature(signature_hash="test_comm_ops.py::test_allreduce:RuntimeError:NCCL_timeout_300s", occurrence_count=8, associated_issue_id=issue_nccl.id),
            ErrorSignature(signature_hash="test_flash_attn.py::test_flash_attention_v2_fp16:AssertionError:relative_error_0.015", occurrence_count=3),
            ErrorSignature(signature_hash="DockerError:ghcr_timeout", occurrence_count=12),
            ErrorSignature(signature_hash="test_llava.py::test_llava_generation:HfHubHTTPError:502", occurrence_count=5),
            ErrorSignature(signature_hash="test_lora_llama.py::test_lora_weight_application:RuntimeError:CUDA_illegal_memory", occurrence_count=4, associated_issue_id=issue_cuda.id),
        ]
        session.add_all(sigs)
        await session.flush()

        # === KnownFailures — persistent tracked failure patterns ===
        kf_nccl = KnownFailure(
            title="NCCL timeout in TP=4 distributed tests",
            match_prompt="test_comm_ops.py::test_allreduce:RuntimeError:NCCL_timeout_300s",
            category="infra",
            status="open",
            is_flaky=True,
            first_seen_build_id=build3.id,
            last_seen_build_id=build1.id,
            created_at=now - timedelta(hours=25),
        )
        kf_flash = KnownFailure(
            title="Flash attention numerical precision regression",
            match_prompt="test_flash_attn.py::test_flash_attention_v2_fp16:AssertionError:relative_error_0.015",
            category="test",
            status="open",
            is_flaky=False,
            first_seen_build_id=build1.id,
            last_seen_build_id=build1.id,
            created_at=now - timedelta(hours=1),
        )
        kf_docker = KnownFailure(
            title="Docker pull timeouts from ghcr.io",
            match_prompt="DockerError:ghcr_timeout",
            category="infra",
            status="resolved",
            is_flaky=True,
            resolved_by_pr=None,
            first_seen_build_id=build4.id,
            last_seen_build_id=build4.id,
            created_at=now - timedelta(hours=31),
            resolved_at=now - timedelta(hours=20),
        )
        kf_hf = KnownFailure(
            title="HuggingFace Hub 502 errors in VLM tests",
            match_prompt="test_llava.py::test_llava_generation:HfHubHTTPError:502",
            category="infra",
            status="open",
            is_flaky=True,
            first_seen_build_id=build3.id,
            last_seen_build_id=build3.id,
            created_at=now - timedelta(hours=25),
        )
        kf_awq = KnownFailure(
            title="AutoAWQ module not found in test environment",
            match_prompt="test_awq.py::test_awq_quantization:ModuleNotFoundError:autoawq",
            category="test",
            status="open",
            is_flaky=False,
            first_seen_build_id=build3.id,
            last_seen_build_id=build3.id,
            created_at=now - timedelta(hours=25),
        )
        kf_cuda = KnownFailure(
            title="CUDA illegal memory access in LoRA tests",
            match_prompt="test_lora_llama.py::test_lora_weight_application:RuntimeError:CUDA_illegal_memory",
            category="test",
            status="open",
            is_flaky=False,
            first_seen_build_id=build3.id,
            last_seen_build_id=build3.id,
            created_at=now - timedelta(hours=25),
        )
        session.add_all([kf_nccl, kf_flash, kf_docker, kf_hf, kf_awq, kf_cuda])
        await session.flush()

        # Link failures to their KnownFailures
        failure1.known_failure_id = kf_nccl.id
        failure1b.known_failure_id = kf_flash.id
        failure3a.known_failure_id = kf_awq.id
        failure3b.known_failure_id = kf_hf.id
        failure3c.known_failure_id = kf_cuda.id
        failure4.known_failure_id = kf_docker.id

        await session.commit()

        print("Seeded 4 full CI builds with realistic job counts:")
        print(f"  #51385 (nightly) - failed - {len(COMMON_JOBS)} jobs, 2 failures")
        print(f"  #51383 (daily)   - passed - {len(COMMON_JOBS)} jobs")
        print(f"  #51380 (nightly) - failed - {len(COMMON_JOBS)} jobs, 3 failures")
        print(f"  #51375 (daily)   - failed - {len(COMMON_JOBS)} jobs, 1 failure")
        print("  6 KnownFailures (5 open, 1 resolved)")


if __name__ == "__main__":
    asyncio.run(seed_data())

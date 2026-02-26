import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

TRIAGE_PROMPT = """Analyze this CI job log from vLLM and identify test failures.

NOTE: Pre-execution infrastructure failures (agent lost, pod eviction, etc.) are already
handled before this analysis runs. You are only seeing jobs where the process actually
started — focus on understanding what failed and why.

CONTEXT:
vLLM is a distributed GPU inference engine. Tests run multiprocess workers (GPU ranks).
When a worker crashes, errors cascade through MANY layers: GPU error -> worker dies ->
engine init fails -> server exits -> test assertion fails. Between multiprocessing and
torch.compile, there can be MANY cascading exceptions wrapping the original error.

CRITICAL — TRACE EXCEPTION CHAINS:
You MUST trace through the ENTIRE exception chain to find the DEEPEST root cause. The
first traceback you see may itself be a wrapper. Look for:
- "During handling of the above exception, another exception occurred"
- "The above exception was the direct cause of the following exception"
- Subprocess error logs: "(EngineCore_DP0 pid=XXXXX) ERROR ... File ..."
- Re-raised exceptions in multiprocess workers

The DEEPEST exception in the chain is the root cause. For example,
"assert not inplace or not disable_inplace()" in fused_moe.py is more useful than
"Engine core initialization failed" which wraps it 3 layers up.

WRAPPER ERRORS TO SKIP (trace through to find the error they wrap):
- "Engine core initialization failed"
- "Server exited unexpectedly"
- "Failed to start engine", "Worker failed to start"
- "process group initialization failed"
- "See root cause above", "command exited with status", "Failed core proc"
These are NEVER the root cause. Always look for the deeper error they wrap.

TORCH.COMPILE / DYNAMO ERRORS:
torch._dynamo.exc.Observed*Error (ObservedAssertionErrorError, ObservedAttributeError, etc.)
are wrappers around errors that occurred inside a torch.compile'd function. The exception
class name alone is NOT informative. To find the actual root cause:
- Look for "Developer debug context:" lines — these contain the REAL error message
  (e.g., "'ZeroExpertFusedMoE' object has no attribute 'select_experts'")
- Look for subprocess output from "(EngineCore_DP0 pid=...)" which often contains
  the full traceback before torch._dynamo wraps it
- The error_message should describe the ACTUAL underlying error, not the Observed*Error wrapper
- Example: error_message should be "AttributeError: 'ZeroExpertFusedMoE' object has no
  attribute 'select_experts'" NOT "torch._dynamo.exc.ObservedAttributeError: raised exception"

TEST TYPES:
Most vLLM CI jobs run pytest, but some run custom scripts (bash, standalone Python for
benchmarks/accuracy evals). For pytest jobs, look for "PASSED", "FAILED", "ERROR",
traceback blocks, and the pytest summary at the end. For non-pytest jobs, look for exit
codes, assertion messages in stdout, and script-specific error output.

vLLM CI may run multiple test files sequentially in the same job. Each distinct test
failure should be a separate entry in the output array. Check file paths in tracebacks
to separate failures from different tests.

LOG ANALYSIS STRATEGY:
1. Read the LAST 50 lines for the pytest summary (pass/fail counts, test names)
2. Grep for "FAILED " to find which tests failed
3. Grep for "Traceback" to locate all error tracebacks
4. Read around error tracebacks to understand each failure
5. For wrapper errors, grep EARLIER for the actual root cause
6. For torch._dynamo Observed*Error, grep for "Developer debug context:"
7. Grep for "E   " (pytest assertion lines) to find assertion failures

KNOWN FAILURES (existing tracked issues):
{known_failures_context}

LOG FILE TO ANALYZE:
{log_file}

{tc_context}

For each failure, assign it to either:
1. An existing known failure (by known_failure_id) if the root cause matches
2. A new known failure (provide title, summary, match_prompt, category) if genuinely new

Return ONLY valid JSON array (no markdown). Each object has:
- known_failure_id: <int or null> — set if this matches an existing known failure
- new_known_failure: {{"title": "...", "summary": "...", "match_prompt": "...", "category": "infra" or "test"}} or null
- failing_test: Test path (e.g., "v1/test_foo.py::test_bar"). Null for infra issues.
- error_message: THE DEEPEST ROOT CAUSE error — trace through ALL exception chains
- root_cause: 1-2 sentence explanation
- log_line_start: Starting line number for the root cause traceback
- log_line_end: Ending line number. Pick a 20-40 line range showing the deepest error.
  For wrapper errors, point to the ACTUAL root cause. For torch._dynamo errors, include
  the "Developer debug context:" line.

WRITING match_prompt (for new_known_failure):
- Describe the ROOT CAUSE error pattern, NOT wrapper errors
- Include: root cause error type, originating file/module, key error message fragments
- Exclude: wrapper errors (Observed*Error, "Engine core initialization failed", etc.),
  specific parameter values, timestamps, memory addresses, rank numbers
- DO NOT name specific test files UNLESS the failure is truly test-specific (e.g., an
  accuracy regression in a specific test). For infra issues, timeouts, OOM, NCCL errors,
  etc., describe the ERROR PATTERN, not which tests happened to be running.
- Example GOOD: "torch.OutOfMemoryError during model weight loading in linear.py, CUDA
  out of memory when allocating weight tensors for large models"
- Example BAD: "torch._dynamo.exc.ObservedAssertionErrorError with wrapper message"
  (describes the wrapper, not the root cause)
- Example BAD: "signal: terminated during pytest startup for test_block_fp8.py"
  (names specific test files for a timeout). Instead: "Job timeout during pytest
  initialization, cancellation signal before tests execute"
- Goal: match all failures from the same root cause, even across different tests

GROUPING:
Multiple test cases that fail with the same root cause = ONE known failure. The question
is: would a single code fix resolve all of them? Each test case is still a separate entry
in the output array, but they all reference the same new_known_failure title.

CATEGORIES:
- "infra": Docker issues, network/HTTP errors, NCCL errors, OSError (resource unavailable),
  platform unsupported features, OOM during model weight loading (GPU too small for model),
  timeouts where no test started (setup/docker/init)
- "test": AssertionError, accuracy failures, ValueError in test code, OOM AFTER weight
  loading (profiling/KV cache/forward pass — likely code regression increased memory),
  CUDA re-initialization errors (vLLM is multiprocess — driver communication bugs are
  real), timeouts where a test was running and hung (compilation stuck, deadlock)

OOM CATEGORIZATION:
- OOM during weight loading / model initialization → "infra" (GPU too small for model)
- OOM after weights are loaded (profiling, KV cache, forward pass) → "test" (code regression)

TIMEOUTS/CANCELLATIONS:
- "Received cancellation signal" or "signal: terminated" means the job timed out
- If no tests started (timeout during setup/init/docker) → "infra", failing_test = null
- If a test was running and appears to have hung → "test", set failing_test to last test
- Do NOT reference specific test files in match_prompt for timeouts — describe the pattern
"""


def _build_tc_context_text(tc_executions: list[dict] | None) -> str:
    """Build Test Engine context string for prompts."""
    if not tc_executions:
        return ""

    lines = [
        "TEST ENGINE DATA (from Buildkite Test Analytics):",
        "The following tests FAILED according to the test runner. Use this to focus",
        "your analysis — you know exactly which tests failed. Search the raw log for",
        "the full execution output of each failed test (subprocess logs, CUDA errors,",
        "worker messages). The tracebacks below may be truncated; the raw log has the",
        "full output.",
        "",
    ]
    for i, ex in enumerate(tc_executions, 1):
        test_name = ex.get("test_name", "unknown")
        location = ex.get("location", "")
        failure_reason = ex.get("failure_reason", "")
        # Truncate failure_reason for prompt (it may already be truncated at 1024 chars)
        if len(failure_reason) > 500:
            failure_reason = failure_reason[:500] + "... [truncated — check raw log for full traceback]"
        lines.append(f"  Failed test {i}: {test_name}")
        if location:
            lines.append(f"    Location: {location}")
        if failure_reason:
            lines.append(f"    Traceback hint:\n      {failure_reason}")
        lines.append("")

    return "\n".join(lines)


def _build_kf_context_text(known_failures_context: list[dict] | None) -> str:
    """Build known failures context string for prompts."""
    if not known_failures_context:
        return "  (none - all failures are new)"

    kf_lines = []
    for kf in known_failures_context:
        parts = [f"ID={kf['id']}"]
        parts.append(kf['title'])
        if kf.get('summary'):
            parts.append(f"summary={kf['summary']}")
        if kf.get('match_prompt'):
            parts.append(f"match_when={kf['match_prompt']}")
        parts.append(f"category={kf.get('category', 'unknown')}")
        if kf.get('affected_jobs'):
            parts.append(f"jobs={','.join(kf['affected_jobs'])}")
        if kf.get('affected_tests'):
            parts.append(f"tests={','.join(kf['affected_tests'][:5])}")
        kf_lines.append("  " + " | ".join(parts))
    return "\n".join(kf_lines)


async def _run_claude_agentic(prompt: str, allowed_tools: str = "Read,Grep",
                              max_turns: int = 20, timeout: int = 300) -> tuple[str | None, str | None]:
    """Run Claude in agentic mode with tool access. Returns (result_text, session_id)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--output-format", "json",
        "--model", "claude-sonnet-4-5",
        "--max-turns", str(max_turns),
        "--allowedTools", allowed_tools,
        "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("Claude agentic command timed out")
        return None, None

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        logger.error(f"Claude agentic command failed: {error_msg}")
        return None, None

    raw = stdout.decode()

    # Handle JSONL: claude CLI may output multiple JSON lines, take the last one
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    if len(lines) > 1:
        raw = lines[-1]

    try:
        response = json.loads(raw)
        session_id = response.get("session_id")
        subtype = response.get("subtype", "")

        if subtype == "error_max_turns":
            logger.warning(f"Claude hit max turns limit (session={session_id})")
            result = response.get("result")
            if result:
                return result, session_id
            return None, session_id

        result = response.get("result")
        if result is None:
            logger.warning(f"Claude response missing 'result' field (subtype={subtype})")
            return None, session_id

        return result, session_id
    except json.JSONDecodeError:
        # If we can't parse the wrapper, return the raw text (non-JSON output)
        logger.warning(f"Claude output not valid JSON, returning raw text ({len(raw)} chars)")
        return raw, None


async def resume_claude_session(session_id: str, prompt: str,
                                max_turns: int = 10, timeout: int = 120) -> str | None:
    """Resume an existing Claude session with a follow-up prompt."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--output-format", "json",
        "--resume", session_id,
        "--max-turns", str(max_turns),
        "--allowedTools", "Read,Grep",
        "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("Claude resume timed out")
        return None

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        logger.error(f"Claude resume failed: {error_msg}")
        return None

    raw = stdout.decode()
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    if len(lines) > 1:
        raw = lines[-1]

    try:
        response = json.loads(raw)
        return response.get("result", raw)
    except json.JSONDecodeError:
        return raw


def _parse_claude_response(result_text: str) -> list[dict] | None:
    """Parse Claude's response text into a list of analysis dicts.

    Returns None if parsing fails entirely.
    """
    if not result_text or not result_text.strip():
        logger.warning("Empty Claude response text")
        return None

    try:
        # Try to find a JSON array
        array_match = re.search(r'\[[\s\S]*\]', result_text)
        if array_match:
            parsed = json.loads(array_match.group())
            if isinstance(parsed, list):
                return parsed

        # Fall back to single object - wrap in array
        json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
        if json_match:
            return [json.loads(json_match.group())]

        parsed = json.loads(result_text)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError as e:
        preview = result_text[:200].replace('\n', '\\n')
        logger.error(f"Failed to parse Claude response: {e} | preview: {preview}")
        return None


async def analyze_failure_with_claude(
    log_file: str,
    known_failures_context: list[dict] | None = None,
    tc_executions: list[dict] | None = None,
    max_retries: int = 2,
) -> tuple[list[dict], str | None]:
    """Analyze a single job's log file using Claude in agentic mode.

    Args:
        log_file: Path to the raw log file on disk.
        known_failures_context: List of known failure dicts for matching.
        tc_executions: Optional Test Engine failed executions for this job.
            When provided, Claude knows exactly which tests failed and can
            focus on finding the full execution output in the raw log.
        max_retries: Number of retry attempts after initial failure (default 2).

    Returns:
        Tuple of (list of analysis dicts, session_id for resumption).
    """
    kf_text = _build_kf_context_text(known_failures_context)
    tc_text = _build_tc_context_text(tc_executions)

    prompt = TRIAGE_PROMPT.format(
        known_failures_context=kf_text,
        log_file=log_file,
        tc_context=tc_text,
    )

    last_session_id = None

    for attempt in range(1 + max_retries):
        if attempt > 0:
            delay = 5 * attempt  # 5s, 10s backoff
            logger.info(f"Retrying Claude analysis for {log_file} (attempt {attempt + 1}/{1 + max_retries}, waiting {delay}s)")
            await asyncio.sleep(delay)

        result_text, session_id = await _run_claude_agentic(prompt, max_turns=25, timeout=300)
        if session_id:
            last_session_id = session_id

        if not result_text:
            logger.warning(f"Claude returned no result for {log_file} (attempt {attempt + 1}/{1 + max_retries})")
            continue

        parsed = _parse_claude_response(result_text)
        if parsed is not None:
            return parsed, last_session_id

        # JSON parse failed — if we have a session, try resuming with a simpler prompt
        if session_id:
            logger.info(f"Attempting to recover via session resume after parse failure (attempt {attempt + 1})")
            retry_text = await resume_claude_session(
                session_id,
                "Your previous response was not valid JSON. Return ONLY a valid JSON array of failure objects, no markdown or explanation.",
                max_turns=3,
                timeout=60,
            )
            if retry_text:
                parsed = _parse_claude_response(retry_text)
                if parsed is not None:
                    return parsed, session_id

    logger.error(f"Claude analysis failed after {1 + max_retries} attempts for {log_file}")
    return [_fallback_analysis(log_file)], last_session_id


DEDUP_PROMPT = """You are deduplicating known failure records from a CI triage system.

Multiple CI jobs were analyzed in parallel, and each analysis independently created
"known failure" records. Some of these may describe the SAME underlying issue and
should be merged.

Two known failures should be merged if they describe the same root cause bug — i.e.,
a single code fix would resolve both. Different test names or jobs are fine as long
as the underlying error pattern is the same.

Do NOT merge failures that happen to share an error type but have different root causes.
For example, two different AssertionErrors in different modules are separate issues.

KNOWN FAILURES TO EVALUATE:
{kf_list}

Return ONLY valid JSON. Format:
{{
  "merge_groups": [
    {{
      "keep_id": <int>,
      "merge_ids": [<int>, ...],
      "reason": "<brief explanation>"
    }}
  ]
}}

- Each group says: keep the KF with keep_id, merge the others (merge_ids) into it.
- keep_id should be the lowest ID in the group.
- If NO merges are needed (all are distinct issues), return {{"merge_groups": []}}.
- Only group failures you are confident share the same root cause.
"""


async def deduplicate_known_failures(
    kf_descriptions: list[dict],
) -> list[dict]:
    """Ask Claude to identify duplicate KnownFailures that should be merged.

    Args:
        kf_descriptions: List of {id, title, summary, match_prompt, category} dicts.

    Returns:
        List of merge group dicts: {keep_id, merge_ids, reason}.
        Empty list if no merges needed.
    """
    kf_lines = []
    for kf in kf_descriptions:
        parts = [f"ID={kf['id']}"]
        parts.append(f"title={kf['title']}")
        if kf.get("summary"):
            parts.append(f"summary={kf['summary']}")
        if kf.get("match_prompt"):
            parts.append(f"match_prompt={kf['match_prompt']}")
        parts.append(f"category={kf.get('category', 'unknown')}")
        kf_lines.append("  " + " | ".join(parts))

    prompt = DEDUP_PROMPT.format(kf_list="\n".join(kf_lines))

    # No tools needed — pure reasoning over structured data
    result_text, _ = await _run_claude_agentic(
        prompt, allowed_tools="", max_turns=1, timeout=60,
    )
    if not result_text:
        return []

    try:
        json_match = re.search(r'\{[\s\S]*"merge_groups"[\s\S]*\}', result_text)
        if json_match:
            parsed = json.loads(json_match.group())
            return parsed.get("merge_groups", [])

        parsed = json.loads(result_text)
        return parsed.get("merge_groups", [])
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse dedup response: {e}")
        return []


def _fallback_analysis(log_file: str) -> dict:
    """Minimal fallback when Claude analysis fails entirely."""
    return {
        "category": "test",
        "error_message": f"Claude analysis failed for {log_file}",
        "root_cause": "Log analysis inconclusive",
    }

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

TRIAGE_PROMPT = """Analyze this CI job log from vLLM and categorize failures.

CRITICAL - FIND THE ROOT CAUSE:
The log is organized into sections labeled "FIRST ERROR" (usually the root cause), "FINAL ERROR" (cascading failures), and "FINAL OUTPUT" (test summary).

**ALWAYS prioritize errors from "FIRST ERROR" section** - these are the actual root causes. Errors in later sections like "RuntimeError: Engine core initialization failed" or "Server exited unexpectedly" are WRAPPER ERRORS caused by the first error.

Return an ARRAY of failure objects - one per DISTINCT root cause.

Return ONLY valid JSON array (no markdown). Each object has:
- category: "infra" or "test"
- failing_test: Test path (e.g., "v1/test_foo.py::test_bar"). Null for infra issues.
- error_signature: Format "test_file::test_name:ErrorType:specific_detail" - must be unique to this failure
- error_message: THE ACTUAL ROOT CAUSE (quote from FIRST ERROR section, not wrapper errors)
- root_cause: 1-2 sentence explanation
- is_flaky: boolean

WRAPPER ERRORS TO SKIP (use earlier error instead):
- "Engine core initialization failed"
- "Server exited unexpectedly"
- "See root cause above"
- "command exited with status"
- "Failed core proc"

Categories:
- "infra": Docker issues, network/HTTP errors, NCCL errors, timeouts, OSError (resource unavailable), platform unsupported features
- "test": AssertionError, accuracy failures, OOM/memory errors (could be test bug), ValueError in test code

Examples:
[{{"category": "infra", "failing_test": null, "error_signature": "infra:OSError:resource_temporarily_unavailable", "error_message": "OSError: [Errno 11] Resource temporarily unavailable", "root_cause": "System resource exhaustion", "is_flaky": true}}]

[{{"category": "test", "failing_test": "test_voxtral.py::test_online_serving", "error_signature": "test_voxtral.py::test_online_serving:ValueError:gpu_memory", "error_message": "ValueError: Free memory (1.5 GiB) < required (4.0 GiB) on cuda:0", "root_cause": "Test requires more GPU memory than available - may need model/batch size adjustment", "is_flaky": false}}]

Log content:
{log_content}
"""


def _extract_relevant_log_sections(log_content: str, max_size: int = 60000) -> str:
    """Extract relevant error sections from the log, not just the last N chars.

    This finds error/exception lines and includes context around them,
    ensuring we capture root cause errors that may occur earlier in the log.
    """
    lines = log_content.split('\n')

    # Wrapper errors that are NOT informative (skip these when finding "first error")
    wrapper_patterns = [
        'Engine core initialization failed',
        'Server exited unexpectedly',
        'See root cause above',
        'command exited with status',
        'Failed core proc',
        'subprocess.*failed',
    ]

    def is_wrapper_error(line: str) -> bool:
        return any(p in line for p in wrapper_patterns)

    # Patterns that indicate REAL root cause errors (not pytest output)
    real_error_patterns = [
        'ValueError:',
        'RuntimeError:',
        'AssertionError:',
        'KeyError:',
        'TypeError:',
        'ImportError:',
        'ModuleNotFoundError:',
        'FileNotFoundError:',
        'ConnectionError:',
        'TimeoutError:',
        'MemoryError:',
        'OutOfMemoryError',
        'CUDA error',
        'NCCL error',
        'HTTPError:',
        'raise ValueError',
        'raise RuntimeError',
        'raise AssertionError',
        'Traceback (most recent call last)',
    ]

    def is_real_error(line: str) -> bool:
        return any(p in line for p in real_error_patterns)

    # Find all lines with real errors/exceptions (not wrappers, not just "FAILED" output)
    error_indices = []
    for i, line in enumerate(lines):
        if is_real_error(line) and not is_wrapper_error(line):
            error_indices.append(i)

    # Also find wrapper errors separately (to show as context)
    wrapper_indices = []
    for i, line in enumerate(lines):
        if is_wrapper_error(line):
            wrapper_indices.append(i)

    if not error_indices and not wrapper_indices:
        # No errors found, just return last portion
        return log_content[-max_size:] if len(log_content) > max_size else log_content

    sections = []

    # First REAL error section (the root cause) - get substantial context
    if error_indices:
        first_idx = error_indices[0]
        start = max(0, first_idx - 5)  # Some context before
        end = min(len(lines), first_idx + 50)  # Lots of context after to capture full traceback
        sections.append(('FIRST ERROR (ROOT CAUSE)', lines[start:end]))

    # If there's a gap, show middle errors too
    if len(error_indices) > 2:
        mid_idx = error_indices[len(error_indices) // 2]
        if mid_idx - error_indices[0] > 50:
            start = max(0, mid_idx - 5)
            end = min(len(lines), mid_idx + 15)
            sections.append(('MIDDLE ERROR', lines[start:end]))

    # Final error section (cascading failures / wrapper errors)
    if wrapper_indices:
        last_wrapper_idx = wrapper_indices[-1]
        if not error_indices or last_wrapper_idx - error_indices[0] > 30:
            start = max(0, last_wrapper_idx - 10)
            end = min(len(lines), last_wrapper_idx + 10)
            sections.append(('FINAL ERROR (wrapper/cascading)', lines[start:end]))
    elif len(error_indices) > 1:
        last_idx = error_indices[-1]
        if last_idx - error_indices[0] > 30:
            start = max(0, last_idx - 10)
            end = min(len(lines), last_idx + 10)
            sections.append(('FINAL ERROR', lines[start:end]))

    # Final output (pytest summary, last 40 lines)
    sections.append(('FINAL OUTPUT', lines[-40:]))

    # Combine sections
    result_parts = []
    for label, section_lines in sections:
        result_parts.append(f"--- {label} ---\n" + '\n'.join(section_lines))

    result = '\n\n'.join(result_parts)

    # If still too long, truncate but keep structure
    if len(result) > max_size:
        result = result[:max_size] + '\n... (truncated)'

    return result


async def analyze_failure_with_claude(log_content: str) -> dict:
    # Extract relevant sections instead of just truncating from end
    relevant_log = _extract_relevant_log_sections(log_content)

    prompt = TRIAGE_PROMPT.format(log_content=relevant_log)

    # Remove CLAUDECODE env var to allow running Claude inside Claude Code sessions
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--output-format", "json",
        "--model", "claude-sonnet-4-5",
        "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("Claude command timed out")
        return [_fallback_analysis(relevant_log)]

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        logger.error(f"Claude command failed: {error_msg}")
        return [_fallback_analysis(relevant_log)]

    try:
        response = json.loads(stdout.decode())
        result_text = response.get("result", "")

        # Try to find a JSON array first (new format)
        array_match = re.search(r'\[[\s\S]*\]', result_text)
        if array_match:
            parsed = json.loads(array_match.group())
            if isinstance(parsed, list):
                return parsed

        # Fall back to single object (old format) - wrap in array
        json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
        if json_match:
            return [json.loads(json_match.group())]

        parsed = json.loads(result_text)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response: {e}")
        return [_fallback_analysis(relevant_log)]


def _clean_log_line(line: str) -> str:
    """Remove ANSI codes, buildkite timestamps, and other noise."""
    # Remove ANSI escape codes
    clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
    clean = re.sub(r'\x1b_[^\x07]*\x07', '', clean)
    # Remove buildkite timestamps like [2026-02-12T23:12:25Z]
    clean = re.sub(r'^\s*\[[\d\-T:Z]+\]\s*', '', clean)
    # Remove buildkite timing markers like _bk;t=1234567890
    clean = re.sub(r'_bk;t=\d+\s*', '', clean)
    # Remove pytest markers like [rank0]:
    clean = re.sub(r'^\s*\[rank\d+\]:\s*', '', clean)
    # Remove leading E markers from pytest
    clean = re.sub(r'^\s*E\s+', '', clean)
    return clean.strip()


def _extract_error_line(log_content: str) -> str:
    """Extract the actual root cause error line from the log.

    For multiprocess systems like vLLM, we need to find the ORIGINAL error,
    not wrapper errors like 'Server exited unexpectedly'.
    """
    lines = log_content.split('\n')

    # Wrapper errors to SKIP - these are not informative
    wrapper_patterns = [
        r'Server exited unexpectedly',
        r'Engine core initialization failed',
        r'See root cause above',
        r'command exited with status',
        r'subprocess.*failed',
        r'worker.*died',
        r'process.*terminated',
    ]

    # High-priority informative errors (search for these FIRST)
    priority_patterns = [
        (r'ValueError:?\s*Free memory on device.+', 'gpu_memory'),
        (r'OutOfMemoryError.+', 'oom'),
        (r'CUDA out of memory.+', 'oom'),
        (r'AssertionError:?\s*.+accuracy.+', 'accuracy'),
        (r'AssertionError:?\s*.+<\s*[\d.]+', 'assertion_value'),
        (r'requests\.exceptions\.HTTPError:?\s*\d{3}.+', 'http'),
        (r'\d{3} Client Error:.+', 'http'),
        (r'\d{3} Server Error:.+', 'http'),
        (r'ModuleNotFoundError:?\s*.+', 'import'),
        (r'ImportError:?\s*.+', 'import'),
        (r'FileNotFoundError:?\s*.+', 'file'),
        (r'ConnectionError:?\s*.+', 'connection'),
        (r'TimeoutError:?\s*.+', 'timeout'),
    ]

    # Standard error patterns (lower priority)
    standard_patterns = [
        (r'AssertionError:?\s*(.+)', 'AssertionError'),
        (r'ValueError:?\s*(.+)', 'ValueError'),
        (r'RuntimeError:?\s*(.+)', 'RuntimeError'),
        (r'KeyError:?\s*(.+)', 'KeyError'),
        (r'TypeError:?\s*(.+)', 'TypeError'),
        (r'CUDA.*error:?\s*(.+)', 'CUDAError'),
    ]

    def is_wrapper_error(text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in wrapper_patterns)

    # First pass: look for high-priority informative errors
    for line in lines:
        clean_line = _clean_log_line(line)
        if is_wrapper_error(clean_line):
            continue
        for pattern, _ in priority_patterns:
            match = re.search(pattern, clean_line, re.IGNORECASE)
            if match:
                return match.group(0).strip()[:300]

    # Second pass: look for standard errors (skip wrapper errors)
    for line in lines:
        clean_line = _clean_log_line(line)
        if is_wrapper_error(clean_line):
            continue
        for pattern, _ in standard_patterns:
            match = re.search(pattern, clean_line, re.IGNORECASE)
            if match:
                return match.group(0).strip()[:300]

    # Fallback: find any line with "Error:" or "FAILED"
    for line in reversed(lines):
        if 'Error:' in line or 'FAILED' in line:
            clean_line = _clean_log_line(line)
            if clean_line and len(clean_line) > 10:
                return clean_line[:300]

    return "Error details not found in log"


def _fallback_analysis(log_content: str) -> dict:
    log_lower = log_content.lower()
    error_message = _extract_error_line(log_content)

    if any(kw in log_lower for kw in ["docker", "pull", "image not found", "manifest"]):
        return {
            "category": "infra",
            "error_signature": "infra:DockerError:image_or_container",
            "error_message": error_message,
            "root_cause": "Docker image or container issue",
            "is_flaky": False,
        }

    if "httperror" in log_lower or "403" in log_lower or "502" in log_lower or "connection" in log_lower:
        return {
            "category": "infra",
            "error_signature": "infra:HTTPError:network_failure",
            "error_message": error_message,
            "root_cause": "Network or HTTP request failure",
            "is_flaky": True,
        }

    if any(kw in log_lower for kw in ["nccl", "cuda error", "device not found"]):
        return {
            "category": "infra",
            "error_signature": "infra:GPUError:device_issue",
            "error_message": error_message,
            "root_cause": "GPU infrastructure issue",
            "is_flaky": True,
        }

    if "oserror" in log_lower and ("resource temporarily unavailable" in log_lower or "errno 11" in log_lower):
        return {
            "category": "infra",
            "error_signature": "infra:OSError:resource_temporarily_unavailable",
            "error_message": error_message,
            "root_cause": "System resource exhaustion",
            "is_flaky": True,
        }

    if any(kw in log_lower for kw in ["timeout", "timed out"]):
        return {
            "category": "infra",
            "error_signature": "infra:Timeout:process_hung",
            "error_message": error_message,
            "root_cause": "Possible infrastructure slowdown or hanging process",
            "is_flaky": True,
        }

    if any(kw in log_lower for kw in ["assertionerror", "assert ", "failed assertion", "accuracy too low"]):
        return {
            "category": "test",
            "error_signature": "unknown_test:AssertionError:fallback",
            "error_message": error_message,
            "root_cause": "Test validation failure",
            "is_flaky": False,
        }

    if any(kw in log_lower for kw in ["importerror", "modulenotfounderror", "no module named"]):
        return {
            "category": "test",
            "error_signature": "unknown_test:ImportError:missing_module",
            "error_message": error_message,
            "root_cause": "Missing dependency or import issue",
            "is_flaky": False,
        }

    if "error" in log_lower or "failed" in log_lower:
        return {
            "category": "test",
            "error_signature": "unknown_test:Error:fallback",
            "error_message": error_message,
            "root_cause": "Unable to determine root cause",
            "is_flaky": False,
        }

    return {
        "category": "test",
        "error_signature": "unknown:fallback_analysis",
        "error_message": error_message,
        "root_cause": "Log analysis inconclusive",
        "is_flaky": False,
    }

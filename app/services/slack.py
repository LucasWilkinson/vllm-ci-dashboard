import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def notify_new_failures(build_number: int, failures: list[dict], build_url: str | None = None):
    if not settings.slack_webhook_url:
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"CI Build #{build_number} Failed"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{len(failures)} failure(s) detected*"
            }
        },
    ]

    if build_url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{build_url}|View Build>"}
        })

    for failure in failures[:5]:
        emoji = ":wrench:" if failure.get("category") == "infra" else ":x:"
        job_name = failure.get("job_name", "Unknown job")
        error_msg = failure.get("error_message", "No error message")[:100]
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} `{job_name}`: {error_msg}"
            }
        })

    if len(failures) > 5:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_...and {len(failures) - 5} more failures_"
            }
        })

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.slack_webhook_url,
                json={"blocks": blocks},
                timeout=10.0
            )
            response.raise_for_status()
            logger.info(f"Sent Slack notification for build #{build_number}")
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {e}")

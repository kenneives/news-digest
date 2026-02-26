"""
Podcast Script Generator

Generates a two-host conversational podcast script from the daily news digest
using a local LLM via OpenAI-compatible endpoint (Ollama or LM Studio).

Hosts:
  - Alex (male): Enthusiastic about tech breakthroughs
  - Sam (female): Analytical, asks good questions

Requirements:
  - Ollama running locally (default: http://localhost:11434)
  - A model installed (e.g., qwen2.5:14b): ollama pull qwen2.5:14b
  - Test: curl http://localhost:11434/v1/models
"""

import os
import re
import smtplib
import ssl
import time
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)


def extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML digest content using BeautifulSoup."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style"]):
        element.decompose()

    text = soup.get_text(separator="\n", strip=True)

    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# Acceptable parameter sizes for podcast script generation (in billions).
# Ordered by preference: 14b is the sweet spot, 30b if available, 8b as minimum.
PREFERRED_SIZES_B = [14, 30, 8]

# Size strings Ollama uses in model names and parameter_size fields
_SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)[bB]")


def _parse_size_b(text: str) -> float | None:
    """Extract parameter size in billions from a string like '14b' or '14.8B'."""
    m = _SIZE_PATTERN.search(text)
    return float(m.group(1)) if m else None


def _model_family(name: str) -> str:
    """Extract the base family from a model name, e.g. 'qwen' from 'qwen2.5:14b'."""
    # Strip tag (everything after ':')
    base = name.split(":")[0]
    # Strip trailing version numbers: "qwen2.5" -> "qwen", "llama3.1" -> "llama"
    return re.sub(r"[\d.]+$", "", base).rstrip("-")


def _model_version(name: str) -> tuple:
    """Extract a sortable version tuple from a model name.

    'qwen2.5:14b' -> (2, 5), 'llama3.1:8b' -> (3, 1), 'mistral:7b' -> (0,)
    Higher versions are considered newer/better.
    """
    base = name.split(":")[0]
    nums = re.findall(r"\d+", base)
    return tuple(int(n) for n in nums) if nums else (0,)


def _list_ollama_models(llm_url: str) -> list[dict] | None:
    """Query Ollama for all locally available models. Returns None if unreachable."""
    try:
        resp = requests.get(f"{llm_url}/api/tags", timeout=10)
        resp.raise_for_status()
        return resp.json().get("models", [])
    except (requests.ConnectionError, requests.Timeout):
        logger.warning("Ollama not reachable at %s for model discovery", llm_url)
        return None
    except requests.HTTPError:
        logger.warning("Ollama /api/tags returned %s", resp.status_code)
        return None


def _notify_model_size_change(
    selected_model: str,
    selected_size: int,
    configured_model: str,
    configured_size: int,
) -> None:
    """Send an email notification when the auto-selected model leaves the 14b range."""
    sender_email = os.getenv("GMAIL_ADDRESS")
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    recipient_str = os.getenv("RECIPIENT_EMAIL")

    if not all([sender_email, app_password, recipient_str]):
        logger.warning("Cannot send model-change notification — missing email config")
        return

    recipients = [r.strip() for r in recipient_str.split(",") if r.strip()]
    direction = "larger" if selected_size > configured_size else "smaller"
    now = datetime.now()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"Podcast Model Changed: {configured_model} -> {selected_model} ({direction})"
    )
    msg["From"] = f"News Digest <{sender_email}>"
    msg["To"] = ", ".join(recipients)

    plain = (
        f"Podcast model auto-selection changed size on {now:%Y-%m-%d at %H:%M}.\n\n"
        f"Configured: {configured_model} (~{configured_size}B)\n"
        f"Selected:   {selected_model} (~{selected_size}B)\n\n"
        f"The selected model is {direction} than the configured baseline.\n"
        f"If this is unexpected, check which models your Ollama instance has:\n"
        f"  curl http://localhost:11434/api/tags\n\n"
        f"To pin a specific model, set LOCAL_LLM_MODEL in your .env file."
    )

    html = f"""\
<!DOCTYPE html>
<html>
<head><style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
    .change-box {{ background: {"#fff7e6" if direction == "smaller" else "#e6f7ff"};
                   border: 1px solid {"#ffa940" if direction == "smaller" else "#1890ff"};
                   border-radius: 5px; padding: 15px; margin: 20px 0; }}
    .model {{ font-family: monospace; font-size: 1.1em; }}
    .arrow {{ font-size: 1.3em; margin: 0 8px; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }}
</style></head>
<body>
    <h2>Podcast Model Size Change</h2>
    <p>On <strong>{now:%A, %B %d at %H:%M}</strong>, the podcast generator auto-selected
       a <strong>{direction}</strong> model than your configured baseline.</p>
    <div class="change-box">
        <span class="model">{configured_model}</span>
        <span class="arrow">&rarr;</span>
        <span class="model">{selected_model}</span>
        <br><small>~{configured_size}B &rarr; ~{selected_size}B</small>
    </div>
    <p>This happened because the auto-selection found a newer version in a different
       size bucket. If this is unexpected, check your Ollama models:</p>
    <pre>curl http://localhost:11434/api/tags</pre>
    <p>To pin a specific model, set <code>LOCAL_LLM_MODEL</code> in your <code>.env</code> file.</p>
    <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
    <p style="color: #666; font-size: 0.85em;">Automated notification from News Digest podcast pipeline.</p>
</body></html>"""

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipients, msg.as_string())
        logger.info("Model-change notification sent to %s", ", ".join(recipients))
        print(f"  Model size change notification sent ({configured_size}B -> {selected_size}B)")
    except Exception as exc:
        logger.error("Failed to send model-change notification: %s", exc)


def _pull_model(llm_url: str, model_name: str) -> None:
    """Pull a model from the Ollama registry."""
    logger.info("Pulling model '%s' from Ollama registry...", model_name)
    print(f"  Pulling model '{model_name}' — this may take a while...")
    try:
        pull_resp = requests.post(
            f"{llm_url}/api/pull",
            json={"name": model_name, "stream": False},
            timeout=900,
        )
        pull_resp.raise_for_status()
        logger.info("Successfully pulled model '%s'", model_name)
    except Exception as exc:
        logger.error("Failed to pull model '%s': %s", model_name, exc)
        raise RuntimeError(
            f"Required model '{model_name}' could not be pulled. "
            f"Install manually with: ollama pull {model_name}"
        ) from exc


def _select_best_model(llm_url: str, configured_model: str) -> str:
    """Pick the best available model from the same family in the 8b-30b range.

    Logic:
      1. Query Ollama for all local models.
      2. Filter to the same family as the configured model (e.g. 'qwen').
      3. Keep only models whose parameter size rounds to 8b, 14b, or 30b.
      4. Among matches, prefer: highest version first, then size by preference order.
      5. If no suitable model is found locally, pull the configured 14b default.

    Returns:
        The model name to use for generation.
    """
    models = _list_ollama_models(llm_url)
    if models is None:
        # Ollama not reachable — fall back to configured model and let retry loop handle it
        logger.warning("Cannot discover models — falling back to configured: %s", configured_model)
        return configured_model

    target_family = _model_family(configured_model)
    all_names = [m["name"] for m in models]
    logger.info("Ollama has %d model(s): %s", len(all_names), ", ".join(sorted(all_names)))

    # Build candidates: (model_name, size_b, version_tuple)
    candidates = []
    for m in models:
        name = m["name"]
        if _model_family(name) != target_family:
            continue

        # Try to get size from Ollama's details first, fall back to parsing the name
        size_b = None
        details = m.get("details", {})
        param_size = details.get("parameter_size", "")
        if param_size:
            size_b = _parse_size_b(param_size)
        if size_b is None:
            size_b = _parse_size_b(name)
        if size_b is None:
            continue

        # Round to nearest bucket: 8, 14, or 30
        rounded = min(PREFERRED_SIZES_B, key=lambda s: abs(s - size_b))
        # Only accept if within reasonable range of a bucket (±5b)
        if abs(rounded - size_b) > 5:
            continue

        candidates.append((name, rounded, _model_version(name)))

    if candidates:
        # Sort: highest version first, then by size preference order.
        # Reorder size preferences so the configured model's bucket comes first,
        # preventing daily size-change notifications when both sizes are available.
        configured_size_b = _parse_size_b(configured_model)
        if configured_size_b is not None:
            configured_bucket = min(PREFERRED_SIZES_B, key=lambda s: abs(s - configured_size_b))
            preferred = [configured_bucket] + [s for s in PREFERRED_SIZES_B if s != configured_bucket]
        else:
            preferred = PREFERRED_SIZES_B
        size_rank = {s: i for i, s in enumerate(preferred)}
        candidates.sort(key=lambda c: (c[2], -size_rank.get(c[1], 99)), reverse=True)
        best_name, best_size, best_ver = candidates[0]
        if best_name != configured_model:
            logger.info(
                "Auto-selected model '%s' (%dB, version %s) over configured '%s'",
                best_name, best_size, ".".join(map(str, best_ver)), configured_model,
            )
            print(f"  Auto-selected model: {best_name} (latest available in {target_family} family)")

            # Notify if the size bucket changed (e.g. 14b -> 30b or 14b -> 8b)
            configured_size = _parse_size_b(configured_model)
            if configured_size is not None:
                configured_bucket = min(PREFERRED_SIZES_B, key=lambda s: abs(s - configured_size))
                if best_size != configured_bucket:
                    _notify_model_size_change(
                        best_name, best_size, configured_model, configured_bucket,
                    )
        else:
            logger.info("Configured model '%s' is the best available", configured_model)
        return best_name

    # No suitable model in the family — pull the configured 14b default
    logger.warning(
        "No %s model in 8b/14b/30b range found locally (available: %s). "
        "Pulling configured default '%s'...",
        target_family,
        ", ".join(sorted(all_names)) or "(none)",
        configured_model,
    )
    _pull_model(llm_url, configured_model)
    return configured_model


def generate_podcast_script(digest_text: str, test_mode: bool = False) -> str:
    """Generate a two-host podcast script from digest text via local LLM.

    Args:
        digest_text: Plain-text version of the daily news digest.
        test_mode: If True, truncate input and target a ~2-minute script.

    Returns:
        Formatted script with ``Alex:`` / ``Sam:`` speaker labels.
    """
    llm_url = os.getenv("LOCAL_LLM_URL", "http://localhost:11434")
    configured_model = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:14b")
    api_url = f"{llm_url}/v1/chat/completions"

    # Discover the best available model in the same family (8b/14b/30b range)
    llm_model = _select_best_model(llm_url, configured_model)

    # Truncate digest text to fit within context window
    # Rough estimate: 1 token ≈ 4 characters. Reserve ~2000 tokens for system prompt + output.
    max_chars = 40000 if not test_mode else 4000
    if len(digest_text) > max_chars:
        digest_text = digest_text[:max_chars] + "\n\n[Content truncated for length]"
        print(f"  Digest text truncated to {max_chars} characters")

    duration_target = "about 2 minutes" if test_mode else "15-20 minutes"

    system_prompt = f"""You are a podcast script writer. Write a natural, engaging conversation \
between two hosts discussing today's tech news digest.

HOSTS:
- Alex (male): Enthusiastic about tech breakthroughs, gets excited about new developments, \
uses vivid analogies, sometimes makes pop-culture references.
- Sam (female): Analytical and thoughtful, asks probing follow-up questions, connects dots \
between stories, brings the business/practical perspective.

RULES:
1. Open with a brief, energetic intro where both hosts greet the audience.
2. Cover the most interesting stories from the digest naturally — do NOT just read headlines.
3. Use natural transitions between topics ("Speaking of AI...", "That reminds me of...", etc.).
4. Maximum 2-3 consecutive lines from the same speaker before the other responds.
5. Include genuine reactions: surprise, humor, skepticism, excitement.
6. End with a quick recap of the top takeaway and a sign-off.
7. Target length: {duration_target} of spoken audio (roughly 150 words per minute).
8. Do NOT include stage directions, sound effects, or parenthetical notes.
9. Each line MUST start with exactly "Alex:" or "Sam:" followed by a space and their dialogue.
10. Keep individual lines to 1-3 sentences for natural pacing.

OUTPUT FORMAT:
Return ONLY the script. Each line must begin with the speaker label.
Example:
Alex: Hey everyone, welcome back to the Daily Digest!
Sam: Great to be here. We've got some fascinating stories today.
Alex: Let's dive right in..."""

    user_prompt = f"""Here is today's news digest. Write the podcast script based on this content:

{digest_text}"""

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 4096 if not test_mode else 1024,
        "stream": False,
    }

    # Include model identifier for just-in-time loading
    if llm_model:
        payload["model"] = llm_model

    # Retry logic: Ollama may need time to load the model on first request,
    # or another process (e.g. trading bot) may be swapping models.
    max_retries = 5
    base_delay = 15  # seconds
    retryable_statuses = {400, 404, 409, 500, 503}
    for attempt in range(1, max_retries + 1):
        retry_delay = base_delay * (2 ** (attempt - 1))  # exponential backoff
        print(f"  Calling local LLM at {api_url}... (attempt {attempt}/{max_retries})")
        try:
            response = requests.post(api_url, json=payload, timeout=600)
        except requests.ConnectionError:
            if attempt < max_retries:
                print(f"  Local LLM not reachable, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            raise

        if response.status_code == 200:
            break

        if response.status_code in retryable_statuses and attempt < max_retries:
            reason = response.text[:200] if response.text else "(no body)"
            print(f"  Local LLM returned {response.status_code} ({reason}), "
                  f"retrying in {retry_delay}s...")
            # If model went missing (another process removed it), re-discover
            if response.status_code in (404, 400) and "not found" in response.text.lower():
                print(f"  Model '{llm_model}' appears to have been removed — re-discovering...")
                llm_model = _select_best_model(llm_url, configured_model)
                payload["model"] = llm_model
            time.sleep(retry_delay)
            continue

        print(f"  Local LLM error {response.status_code}: {response.text}")
        response.raise_for_status()

    result = response.json()
    script = result["choices"][0]["message"]["content"].strip()

    return script


def parse_script(script: str) -> list[tuple[str, str]]:
    """Parse a podcast script into speaker/dialogue segments.

    Args:
        script: Raw script text with ``Alex:`` and ``Sam:`` labels.

    Returns:
        List of (speaker, dialogue) tuples.
    """
    segments = []
    current_speaker = None
    current_lines = []

    for line in script.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Check for speaker label at start of line
        match = re.match(r"^(Alex|Sam)\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            # Save previous segment
            if current_speaker and current_lines:
                segments.append((current_speaker, " ".join(current_lines)))

            current_speaker = match.group(1).capitalize()
            current_lines = [match.group(2).strip()]
        elif current_speaker:
            # Continuation of current speaker's dialogue
            current_lines.append(line)

    # Don't forget the last segment
    if current_speaker and current_lines:
        segments.append((current_speaker, " ".join(current_lines)))

    if not segments:
        raise ValueError("Could not parse any speaker segments from the script. "
                         "Expected lines starting with 'Alex:' or 'Sam:'.")

    return segments

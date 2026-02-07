"""
Podcast Script Generator

Generates a two-host conversational podcast script from the daily news digest
using LM Studio's local LLM API (OpenAI-compatible endpoint).

Hosts:
  - Alex (male): Enthusiastic about tech breakthroughs
  - Sam (female): Analytical, asks good questions

Requirements:
  - LM Studio running locally with a loaded model (e.g., Llama 3.1 8B Instruct Q4_K_M)
  - Start the local server in LM Studio's "Local Server" tab
  - Default endpoint: http://localhost:1234/v1/chat/completions
  - Test: curl http://localhost:1234/v1/models
"""

import os
import re
import time

from bs4 import BeautifulSoup
import requests


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


def generate_podcast_script(digest_text: str, test_mode: bool = False) -> str:
    """Generate a two-host podcast script from digest text via LM Studio.

    Args:
        digest_text: Plain-text version of the daily news digest.
        test_mode: If True, truncate input and target a ~2-minute script.

    Returns:
        Formatted script with ``Alex:`` / ``Sam:`` speaker labels.
    """
    lm_studio_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234")
    lm_studio_model = os.getenv("LM_STUDIO_MODEL", "")
    api_url = f"{lm_studio_url}/v1/chat/completions"

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

    # Include model identifier so LM Studio can auto-load it (just-in-time loading)
    if lm_studio_model:
        payload["model"] = lm_studio_model

    # Retry logic: LM Studio may need time to load the model on first request
    max_retries = 3
    retry_delay = 30  # seconds — model loading can take a while
    for attempt in range(1, max_retries + 1):
        print(f"  Calling LM Studio at {api_url}... (attempt {attempt}/{max_retries})")
        try:
            response = requests.post(api_url, json=payload, timeout=600)
        except requests.ConnectionError:
            if attempt < max_retries:
                print(f"  LM Studio not reachable, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            raise

        if response.status_code == 200:
            break

        # 400 typically means model not loaded yet; 503 means server busy loading
        if response.status_code in (400, 503) and attempt < max_retries:
            print(f"  LM Studio returned {response.status_code} (model may be loading), "
                  f"retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            continue

        print(f"  LM Studio error {response.status_code}: {response.text}")
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

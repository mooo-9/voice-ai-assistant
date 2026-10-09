"""
Screen vision tool — captures the screen and sends it to Claude vision API
to answer a question about what's visible.

Unlike capture_screenshot (which does OCR text extraction only), this tool
understands visual layout, graphs, icons, images, and UI structure.
"""

import os

# Load .env in case this module is imported before main.py runs dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def analyze_screen(question: str = "What do you see on screen?") -> str:
    """Use Claude vision to answer a question about what Mo is looking at.

    The picture is the window Mo was in before El Fager came up, not the whole
    monitor: that had the Cockpit on top of whatever he meant."""
    try:
        import anthropic

        from tools import screen_tool

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "[Screen analysis failed: ANTHROPIC_API_KEY not set in .env]"

        img_b64, path = screen_tool.capture_for_mo()
        if not img_b64:
            return "[Screen analysis failed: couldn't capture the screen]"
        screen_tool.delete_temp_screenshot(path)

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": question},
                ],
            }],
        )
        return resp.content[0].text

    except Exception as e:
        return f"[Screen analysis failed: {e}]"

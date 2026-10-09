import base64
import os
import shutil
import tempfile

# Vision API cost note: a 1280px screenshot is roughly 1000-1500 input tokens.
# At claude-sonnet-4-6 pricing this is ~$0.003-0.005 per screenshot call.
# Resize is intentional — do not remove it.

# Point pytesseract at the Windows installer path if tesseract isn't on PATH
_TESSERACT_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if shutil.which("tesseract") is None and os.path.exists(_TESSERACT_WIN):
    try:
        import pytesseract as _pt
        _pt.pytesseract.tesseract_cmd = _TESSERACT_WIN
    except ImportError:
        pass

tesseract_available = (
    shutil.which("tesseract") is not None
    or os.path.exists(_TESSERACT_WIN)
)


def capture_screenshot() -> "tuple[str, str] | tuple[None, None]":
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            pil_img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

        max_width = 1280
        if pil_img.width > max_width:
            ratio = max_width / pil_img.width
            pil_img = pil_img.resize(
                (max_width, int(pil_img.height * ratio)), Image.LANCZOS
            )

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        pil_img.save(tmp.name, "PNG")

        with open(tmp.name, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        return b64, tmp.name
    except Exception as e:
        print(f"[El Fager] Screenshot capture failed: {e}")
        return None, None


def _window_exists(hwnd: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.IsWindow(hwnd))


def capture_window(hwnd: int) -> "tuple[str, str] | tuple[None, None]":
    """One window as it is drawn, even while El Fager covers it.

    (None, None) when it is closed, minimised (nothing is drawn) or comes back
    all black, so the caller can fall back to the whole screen.
    """
    try:
        from PIL import Image

        from tools.whatsapp_desktop import _capture

        if not _window_exists(hwnd):
            return None, None
        shot = _capture(hwnd)
        if shot is None:
            return None, None
        pil_img = shot[0]
        if pil_img.getbbox() is None:
            return None, None

        max_width = 1280
        if pil_img.width > max_width:
            ratio = max_width / pil_img.width
            pil_img = pil_img.resize(
                (max_width, int(pil_img.height * ratio)), Image.LANCZOS
            )

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        pil_img.save(tmp.name, "PNG")

        with open(tmp.name, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        return b64, tmp.name
    except Exception as e:
        print(f"[El Fager] Window capture failed: {e}")
        return None, None


def capture_for_mo() -> "tuple[str, str] | tuple[None, None]":
    """The picture for "what's this" and analyze_screen: the window Mo was in
    before El Fager covered it, or the whole screen when that window can't be
    drawn."""
    from core import focus_context

    seen = focus_context.last()
    if seen and seen.get("hwnd"):
        b64, path = capture_window(seen["hwnd"])
        if b64:
            return b64, path
    return capture_screenshot()


def delete_temp_screenshot(path: str) -> None:
    try:
        os.unlink(path)
    except Exception:
        pass


def ocr_screenshot() -> str:
    if not tesseract_available:
        return "[OCR not available — install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki]"
    try:
        import mss
        import pytesseract
        from PIL import Image
        if os.path.exists(_TESSERACT_WIN):
            pytesseract.pytesseract.tesseract_cmd = _TESSERACT_WIN

        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            pil_img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

        gray = pil_img.convert("L")
        text = pytesseract.image_to_string(gray, lang="ara+eng")
        text = " ".join(text.split())
        if len(text) > 3000:
            text = text[:3000] + "\n[... truncated]"
        return text if text.strip() else "[No text found on screen]"
    except Exception as e:
        return f"[OCR failed: {e}]"

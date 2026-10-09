"""No test may type or click on Mo's machine. A WhatsApp test pressed a real
Enter into whatever window was in front on every suite run; tests/conftest.py
now blocks key and mouse synthesis suite-wide.

Each check looks before it calls: if the guard were missing, calling the real
function to prove it would itself type on Mo's screen."""
import pytest


def _assert_blocked(fn):
    assert "_no_real_input" in fn.__qualname__, f"{fn} is the real function"
    with pytest.raises(RuntimeError, match="off in tests"):
        fn("enter")


@pytest.mark.parametrize("name", ["press_and_release", "send", "write", "press", "release"])
def test_keyboard_cannot_press_keys(name):
    import keyboard
    _assert_blocked(getattr(keyboard, name))


@pytest.mark.parametrize("name", ["press", "hotkey", "write", "typewrite", "click",
                                  "doubleClick", "rightClick", "moveTo", "moveRel",
                                  "scroll", "keyDown", "keyUp", "dragTo",
                                  "mouseDown", "mouseUp"])
def test_pyautogui_cannot_type_or_click(name):
    import pyautogui
    _assert_blocked(getattr(pyautogui, name))

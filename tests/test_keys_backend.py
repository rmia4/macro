import ctypes

import pytest

import input_backend as ib
import keys


def test_scancodes_and_extended():
    assert keys.NAME_TO_SCAN["w"] == (0x11, False)
    assert keys.NAME_TO_SCAN["left"] == (0x4B, True)
    assert keys.NAME_TO_SCAN["ctrl_r"] == (0x1D, True)
    assert keys.NAME_TO_SCAN["ctrl"] == (0x1D, False)
    assert keys.NAME_TO_SCAN["delete"] == (0x53, True)
    assert keys.NAME_TO_SCAN["num_dec"] == (0x53, False)


def test_vk_names_resolve_to_scancodes():
    for name in keys.VK_TO_NAME.values():
        assert keys.is_known(name), name


def test_name_from_pynput_duck_typing():
    class K:
        vk = 0xA3
    assert keys.name_from_pynput(K()) == "ctrl_r"

    class C:
        vk = None
        char = "Q"
    assert keys.name_from_pynput(C()) == "q"


def test_key_input_flags():
    up = ib.key_input("right", up=True)
    assert up.type == ib.INPUT_KEYBOARD
    assert up.ki.wScan == 0x4D and up.ki.wVk == 0
    assert up.ki.dwFlags == (ib.KEYEVENTF_SCANCODE | ib.KEYEVENTF_EXTENDEDKEY | ib.KEYEVENTF_KEYUP)
    down = ib.key_input("a", up=False)
    assert down.ki.dwFlags == ib.KEYEVENTF_SCANCODE


@pytest.mark.skipif(not ib.IS_WINDOWS, reason="Windows ABI(long=4바이트)에서만 유효")
def test_input_struct_size():
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(ib.INPUT) == expected


def test_normalize_abs():
    assert ib.normalize_abs(0, 0, 0, 0, 1920, 1080) == (0, 0)
    assert ib.normalize_abs(1919, 1079, 0, 0, 1920, 1080) == (65535, 65535)
    assert ib.normalize_abs(-1920, 0, -1920, 0, 3840, 1080) == (0, 0)


def test_negative_wheel_data_masked():
    inp = ib.mouse_input(0, 0, -120, ib.MOUSEEVENTF_WHEEL)
    assert inp.mi.mouseData == (-120) & 0xFFFFFFFF

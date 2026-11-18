"""hearing_bot: voice typing and AI text tools on one key, Right Alt.

Hold Right Alt and talk. When you let go, your words are typed where the cursor is.
Tap a letter while holding Right Alt to do something else instead:

    Right Alt + T, keep holding and talk   speak any language, the English translation is typed
    Right Alt + N, keep holding and talk   save what you say to voice_notes.md in Documents
    Right Alt + R, keep holding and talk   rewrite the selected text the way you say ("make it formal")
    Right Alt + F                          fix the spelling and grammar of the selected text
    Right Alt + E                          explain the selected text (an error, code, anything) in a popup

Needs the GROQ_API_KEY environment variable. Problems are written to hearing_bot.log.
"""

import ctypes
import logging
import queue
import struct
import threading
import time
import winsound
from array import array
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import sounddevice as sd

import groq_api

# ---- Settings you can change ----
HOTKEY_VK = 0xA5        # key to hold: 0xA5 Right Alt, 0xA3 Right Ctrl, 0x14 Caps Lock, 0x91 Scroll Lock
ACTION_KEYS = {         # letter tapped while holding the hotkey -> what happens when you let go
    ord("T"): "translate",
    ord("N"): "note",
    ord("R"): "rewrite",
    ord("F"): "fix",
    ord("E"): "explain",
}
LANGUAGE = "en"         # language you dictate in; None = let Whisper detect it (e.g. for Hindi)
NOTES_FILE = None       # None = voice_notes.md in your Documents folder
MIN_SECONDS = 0.5       # ignore recordings shorter than this
MAX_SECONDS = 300       # stop recording after 5 minutes
TAIL_SECONDS = 0.25     # keep recording this long after you let go, so the last word isn't cut
SILENCE_PEAK = 500      # skip recordings whose loudest sample is below this (max 32767)

FIX_PROMPT = (
    "Correct the spelling, grammar and punctuation of the text the user sends. Keep its meaning, tone, "
    "language and line breaks. If it is already correct, return it unchanged. Reply with only the "
    "corrected text, without explanations, quotes or code fences."
)
REWRITE_PROMPT = (
    "The user sends an instruction and a text. Rewrite the text by following the instruction. Reply with "
    "only the rewritten text, without explanations, quotes or code fences."
)
EXPLAIN_PROMPT = (
    "Explain the text the user sends to a software developer in 2 to 5 short sentences of plain English. "
    "If it is an error message, give the likely cause and how to fix it. If it is code, say what it does. "
    "The answer is shown in a plain popup, so don't use markdown, backticks, bullet points or headings."
)

SAMPLE_RATE = 16000     # Whisper works on 16 kHz mono audio
LOG_FILE = Path(__file__).with_name("hearing_bot.log")

log = logging.getLogger("hearing_bot")

# ---- Windows API ----
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10
VK_CONTROL, VK_LCONTROL, VK_RMENU = 0x11, 0xA2, 0xA5
ALTGR_CTRL_SCANCODE = 0x21D  # the extra Left Ctrl that AltGr layouts (like India) send with Right Alt
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
CSIDL_PERSONAL = 5  # the Documents folder
ERROR_ALREADY_EXISTS = 183
MB_ICONERROR, MB_ICONINFORMATION = 0x10, 0x40
MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # unused, but INPUT must be as big as its largest member
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
shell32.SHGetFolderPathW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD,
                                     wintypes.LPWSTR)


# ---- Recording ----
class Recorder:
    """Records 16 kHz mono 16-bit audio from the default microphone."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._size = 0

    def start(self):
        self._chunks, self._size = [], 0
        self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                         callback=self._on_audio)
        self._stream.start()

    def _on_audio(self, data, frames, time_info, status):
        if self._size < MAX_SECONDS * SAMPLE_RATE * 2:
            chunk = bytes(data)
            self._chunks.append(chunk)
            self._size += len(chunk)

    def stop(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return b""
        time.sleep(TAIL_SECONDS)
        stream.stop()
        stream.close()
        return b"".join(self._chunks)


# ---- Sending keys and using the clipboard ----
def send_input(inputs):
    if user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)) != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())


def type_text(text):
    """Type text into the focused window as Unicode key presses (the clipboard is not touched)."""
    utf16 = text.encode("utf-16-le")
    units = struct.unpack(f"<{len(utf16) // 2}H", utf16)
    inputs = (INPUT * (2 * len(units)))()
    for i, unit in enumerate(units):
        for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            event = inputs[2 * i + j]
            event.type = INPUT_KEYBOARD
            event.ki.wScan = unit
            event.ki.dwFlags = flags
    send_input(inputs)


def press_ctrl(vk):
    """Press Ctrl + a key, e.g. press_ctrl(ord("C")) for Ctrl+C."""
    keys = ((VK_CONTROL, 0), (vk, 0), (vk, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP))
    inputs = (INPUT * len(keys))()
    for event, (key, flags) in zip(inputs, keys):
        event.type = INPUT_KEYBOARD
        event.ki.wVk = key
        event.ki.wScan = user32.MapVirtualKeyW(key, 0)
        event.ki.dwFlags = flags
    send_input(inputs)


class Clipboard:
    """Opens the clipboard. A hidden window is the owner, because SetClipboardData needs one."""

    def __enter__(self):
        self.hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        for _ in range(40):  # another app may have it open for a moment
            if user32.OpenClipboard(self.hwnd):
                return self
            time.sleep(0.025)
        user32.DestroyWindow(self.hwnd)
        raise RuntimeError("The clipboard is busy")

    def __exit__(self, *exc_info):
        user32.CloseClipboard()
        user32.DestroyWindow(self.hwnd)

    def get_text(self):
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        try:
            return ctypes.wstring_at(kernel32.GlobalLock(handle))
        finally:
            kernel32.GlobalUnlock(handle)

    def set_text(self, text):
        data = ctypes.create_unicode_buffer(text)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
        ctypes.memmove(kernel32.GlobalLock(handle), data, ctypes.sizeof(data))
        kernel32.GlobalUnlock(handle)
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())


def copy_selection():
    """Return the text selected in the focused app (copied with Ctrl+C), or None if nothing is selected.
    The text that was on the clipboard before is put back."""
    with Clipboard() as clipboard:
        saved = clipboard.get_text()
    before = user32.GetClipboardSequenceNumber()
    press_ctrl(ord("C"))
    deadline = time.monotonic() + 1
    while user32.GetClipboardSequenceNumber() == before:
        if time.monotonic() > deadline:
            return None  # the app copied nothing, so nothing was selected
        time.sleep(0.02)
    with Clipboard() as clipboard:
        selected = clipboard.get_text()
        if saved is not None:
            clipboard.set_text(saved)
    return selected


def paste_text(text):
    """Replace the selection with text by pasting it, then put the old clipboard text back."""
    if not text:
        raise RuntimeError("The model returned no text")
    with Clipboard() as clipboard:
        saved = clipboard.get_text()
        clipboard.set_text(text)
    press_ctrl(ord("V"))
    time.sleep(0.5)  # let the app read the clipboard before it is restored
    if saved is not None:
        with Clipboard() as clipboard:
            clipboard.set_text(saved)


def documents_folder():
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buf)
    return Path(buf.value)


# ---- Actions (run after the hotkey is released) ----
def wait_for_release():
    """Wait while the hotkey is held for the next recording, so output doesn't mix with it."""
    while key_down:
        time.sleep(0.05)


def heard(pcm, translate=False):
    """What was said in the recording, or "" if it was too short or silent."""
    if len(pcm) < MIN_SECONDS * SAMPLE_RATE * 2:
        return ""
    samples = array("h", pcm)
    if max(max(samples), -min(samples)) < SILENCE_PEAK:
        log.info("Skipped a silent recording")
        return ""
    text = groq_api.speech_to_text(pcm, SAMPLE_RATE, language=LANGUAGE, translate=translate)
    return " ".join(text.split())


def selected_text():
    wait_for_release()
    text = copy_selection()
    if not text or not text.strip():
        log.info("Nothing was selected")
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return None
    return text


def dictate(pcm):
    text = heard(pcm)
    if text:
        wait_for_release()
        type_text(text + " ")


def translate(pcm):
    text = heard(pcm, translate=True)
    if text:
        wait_for_release()
        type_text(text + " ")


def note(pcm):
    text = heard(pcm)
    if text:
        path = Path(NOTES_FILE) if NOTES_FILE else documents_folder() / "voice_notes.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- **{datetime.now():%Y-%m-%d %H:%M}** {text}\n")
        winsound.MessageBeep(winsound.MB_OK)


def rewrite(pcm):
    instruction = heard(pcm)
    if not instruction:
        return
    text = selected_text()
    if text:
        paste_text(groq_api.chat(REWRITE_PROMPT, f"Instruction: {instruction}\n\nText:\n{text}"))


def fix(pcm):
    text = selected_text()
    if text:
        paste_text(groq_api.chat(FIX_PROMPT, text))


def explain(pcm):
    text = selected_text()
    if text:
        answer = groq_api.chat(EXPLAIN_PROMPT, text)
        flags = MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        threading.Thread(target=user32.MessageBoxW, args=(None, answer, "hearing_bot: explain", flags),
                         daemon=True).start()


ACTIONS = {"dictate": dictate, "translate": translate, "note": note,
           "rewrite": rewrite, "fix": fix, "explain": explain}

# ---- Workers ----
key_events = queue.Queue()  # "start" when the hotkey is pressed, the action name when it is released
jobs = queue.Queue()        # (action, recorded audio) waiting to be processed, in order


def record_loop():
    recorder = Recorder()
    while True:
        event = key_events.get()
        try:
            if event == "start":
                recorder.start()
            else:
                jobs.put((event, recorder.stop()))
        except Exception:
            log.exception("Recording failed")
            winsound.MessageBeep(MB_ICONERROR)


def job_loop():
    while True:
        action, pcm = jobs.get()
        try:
            ACTIONS[action](pcm)
            log.info("%s finished (%.1f s of audio)", action, len(pcm) / (SAMPLE_RATE * 2))
        except Exception:
            log.exception("%s failed", action)
            winsound.MessageBeep(MB_ICONERROR)


# ---- Keyboard hook ----
key_down = False    # is the hotkey held right now?
action = "dictate"  # what to do when the hotkey is released
swallowed = set()   # letter keys whose key-down was hidden from apps


@HOOKPROC
def on_key(n_code, w_param, l_param):
    """Turn the hotkey (and letters tapped while holding it) into events and hide them from other apps."""
    global key_down, action
    if n_code == 0:  # HC_ACTION
        kb = KBDLLHOOKSTRUCT.from_address(l_param)
        vk, pressed = kb.vkCode, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if not kb.flags & LLKHF_INJECTED:
            if vk == HOTKEY_VK:
                if pressed and not key_down:  # ignore auto-repeat while the key is held
                    key_down, action = True, "dictate"
                    key_events.put("start")
                elif not pressed and key_down:
                    key_down = False
                    key_events.put(action)
                return 1
            if vk in ACTION_KEYS:
                if pressed and key_down:
                    action = ACTION_KEYS[vk]
                    swallowed.add(vk)
                    return 1
                if not pressed and vk in swallowed:
                    swallowed.discard(vk)
                    return 1
        if HOTKEY_VK == VK_RMENU and vk == VK_LCONTROL and kb.scanCode == ALTGR_CTRL_SCANCODE:
            return 1
    return user32.CallNextHookEx(None, n_code, w_param, l_param)


def main():
    ctypes.set_last_error(0)
    kernel32.CreateMutexW(None, False, "Local\\hearing_bot")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return  # another copy is already running
    if not groq_api.api_key():
        user32.MessageBoxW(None, "No Groq API key found. Run install.cmd in the hearing_bot folder to add one.",
                           "hearing_bot", MB_ICONERROR)
        return
    threading.Thread(target=record_loop, daemon=True).start()
    threading.Thread(target=job_loop, daemon=True).start()
    if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, on_key, kernel32.GetModuleHandleW(None), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    log.info("Running. Hold the hotkey to talk.")
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        main()
    except Exception:
        log.exception("hearing_bot stopped")
        raise

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)


def _dark_menus() -> None:
    """Dark native menus (the tray menu) on Windows 10 1903+ / 11.

    uxtheme exports SetPreferredAppMode (#135) and FlushMenuThemes (#136) by
    ordinal only; on older systems the lookup fails and menus stay light.
    """
    try:
        import ctypes

        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme[135](2)  # ForceDark
        uxtheme[136]()
    except Exception:
        LOGGER.debug("Dark menus unavailable", exc_info=True)


class WindowsTray:
    WM_TRAYICON = 0x0400 + 20
    ID_OPEN = 1001
    ID_PAUSE = 1002
    ID_DRAFT = 1003
    ID_AUTO = 1004
    ID_LEARN = 1005
    ID_EXIT = 1006

    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback
        self.thread: Optional[threading.Thread] = None
        self.hwnd = 0
        self._ready = threading.Event()
        self._custom_icon = 0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="TrayIcon", daemon=True)
        self.thread.start()
        self._ready.wait(timeout=3)

    def stop(self) -> None:
        if not self.hwnd:
            return
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore

            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            LOGGER.exception("Unable to close tray icon")

    def notify(self, title: str, message: str) -> None:
        if not self.hwnd:
            return
        try:
            import win32gui  # type: ignore

            flags = win32gui.NIF_INFO
            info_flag = getattr(win32gui, "NIIF_INFO", 1)
            data = (self.hwnd, 0, flags, 0, 0, "Ares Assistant", message, 3000, title, info_flag)
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, data)
        except Exception:
            LOGGER.exception("Tray notification failed")

    def _run(self) -> None:
        _dark_menus()
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
            import win32gui  # type: ignore

            class_name = "AresAssistantTrayWindow"
            message_map = {
                win32con.WM_DESTROY: self._on_destroy,
                win32con.WM_COMMAND: self._on_command,
                self.WM_TRAYICON: self._on_tray_event,
            }
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = win32api.GetModuleHandle(None)
            window_class.lpszClassName = class_name
            window_class.lpfnWndProc = message_map
            try:
                atom = win32gui.RegisterClass(window_class)
            except Exception:
                atom = class_name
            self.hwnd = win32gui.CreateWindow(
                atom,
                "Ares Assistant Tray",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                window_class.hInstance,
                None,
            )
            try:
                size = win32api.GetSystemMetrics(win32con.SM_CXSMICON)
                icon = win32gui.LoadImage(0, str(Path(__file__).with_name('assets')/'app.ico'),
                                         win32con.IMAGE_ICON, size, size, win32con.LR_LOADFROMFILE)
                self._custom_icon = icon
            except Exception:
                LOGGER.exception('Unable to load Ares tray icon')
                icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            notify_data = (
                self.hwnd,
                0,
                win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                self.WM_TRAYICON,
                icon,
                "Ares Assistant",
            )
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, notify_data)
            self._ready.set()
            win32gui.PumpMessages()
        except Exception:
            LOGGER.exception("Tray icon failed")
            self._ready.set()

    def _on_destroy(self, hwnd, message, wparam, lparam):
        try:
            import win32gui  # type: ignore

            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
            if self._custom_icon:
                win32gui.DestroyIcon(self._custom_icon)
                self._custom_icon = 0
            win32gui.PostQuitMessage(0)
        except Exception:
            pass
        self.hwnd = 0
        return 0

    def _on_command(self, hwnd, message, wparam, lparam):
        command_id = wparam & 0xFFFF
        mapping = {
            self.ID_OPEN: "show",
            self.ID_PAUSE: "toggle_pause",
            self.ID_DRAFT: "mode_draft",
            self.ID_AUTO: "mode_auto",
            self.ID_LEARN: "mode_learn",
            self.ID_EXIT: "exit",
        }
        command = mapping.get(command_id)
        if command:
            self.callback(command)
        return 0

    def _on_tray_event(self, hwnd, message, wparam, lparam):
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
            import win32gui  # type: ignore

            if lparam == win32con.WM_LBUTTONDBLCLK:
                self.callback("show")
                return 0
            if lparam != win32con.WM_RBUTTONUP:
                return 0
            menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_OPEN, "Открыть")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_PAUSE, "Пауза / продолжить")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_DRAFT, "Режим: черновик")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_AUTO, "Режим: автоответ")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_LEARN, "Режим: обучение")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_EXIT, "Выход")
            x, y = win32api.GetCursorPos()
            win32gui.SetForegroundWindow(hwnd)
            win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, x, y, 0, hwnd, None)
            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
            win32gui.DestroyMenu(menu)
        except Exception:
            LOGGER.exception("Tray menu failed")
        return 0

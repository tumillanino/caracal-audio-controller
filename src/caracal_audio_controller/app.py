#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSystemTrayIcon,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    print("caracal-audio-controller requires PySide6. Install python3-pyside6.", file=sys.stderr)
    raise SystemExit(1) from exc


APP_NAME = "Caracal Audio Controller"
ACCENT = "#f6c177"
BG = "#181616"
PANEL = "#1f1e1c"
TEXT = "#dcd7ba"
MUTED = "#9cabca"
INFO = "#7fb4ca"
SUCCESS = "#a6da95"
DANGER = "#e46876"
YABRIDGE_SCAN_INTERVAL_MS = 60000
YABRIDGE_INITIAL_SCAN_MS = 3500


@dataclass(frozen=True)
class AudioAction:
    key: str
    title: str
    command: tuple[str, ...]
    description: str
    terminal: bool = True


ACTIONS = {
    "update-audio": AudioAction(
        key="update-audio",
        title="Sync Windows VSTs",
        command=("ujust", "update-audio"),
        description="Run yabridgectl sync for Windows VST3 plugins in the current Wine prefix.",
    ),
    "route-plugins": AudioAction(
        key="route-plugins",
        title="Route System Plugins",
        command=("ujust", "route-plugins"),
        description="Copy packaged system plugins into user scan directories.",
    ),
    "restart-pipewire": AudioAction(
        key="restart-pipewire",
        title="Restart PipeWire",
        command=("ujust", "restart-pipewire"),
        description="Restart the user PipeWire service.",
        terminal=False,
    ),
    "virtual-create": AudioAction(
        key="virtual-create",
        title="Create Virtual Channels",
        command=("ujust", "setup-virtual-channels", "create"),
        description="Create DAW, Monitoring, Recording, and System virtual sinks.",
    ),
    "virtual-remove": AudioAction(
        key="virtual-remove",
        title="Remove Virtual Channels",
        command=("ujust", "setup-virtual-channels", "remove"),
        description="Remove the Caracal virtual channel PipeWire config.",
    ),
    "toggle-bt-mic": AudioAction(
        key="toggle-bt-mic",
        title="Bluetooth Headset Toggle",
        command=("ujust", "toggle-bt-mic"),
        description="Enable or disable the Bluetooth headset profile mitigation.",
    ),
    "upgrade": AudioAction(
        key="upgrade",
        title="Update Caracal OS",
        command=("ujust", "upgrade"),
        description="Update to latest version of Caracal OS (reboot required)",
    ),
}


class StatusBus(QObject):
    log = Signal(str)
    direct_finished = Signal(str, int, str)


class StatusWindow(QMainWindow):
    def __init__(self, controller: "AudioController") -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(620, 440)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("hero")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 18, 18, 18)
        header_layout.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(controller.icon.pixmap(56, 56))
        header_layout.addWidget(icon)

        title_box = QVBoxLayout()
        eyebrow = QLabel("Caracal OS")
        eyebrow.setObjectName("eyebrow")
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        subtitle = QLabel("Tray controls for common audio maintenance tasks.")
        subtitle.setObjectName("muted")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        layout.addWidget(header)

        quick_row = QHBoxLayout()
        for key in ("update-audio", "route-plugins", "restart-pipewire", "upgrade"):
            action = ACTIONS[key]
            button = QPushButton(action.title)
            button.clicked.connect(lambda checked=False, action_key=key: controller.run_action(action_key))
            quick_row.addWidget(button)
        layout.addLayout(quick_row)

        self.status = QLabel("Idle. Left-click the tray icon to sync Windows VSTs.")
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Activity appears here.")
        layout.addWidget(self.log, 1)

        self.setCentralWidget(root)
        self.setStyleSheet(build_stylesheet())

    def append_log(self, message: str) -> None:
        self.log.append(message)
        self.status.setText(message)


class AudioController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.app = QApplication.instance()
        if self.app is None:
            raise RuntimeError("QApplication must exist before AudioController")

        self.bus = StatusBus()
        self.bus.log.connect(self._append_log)
        self.bus.direct_finished.connect(self._direct_finished)
        self.processes: list[QProcess] = []
        self.normal_icon = load_icon("normal")
        self.notification_icon = load_icon("notification")
        self.icon = self.normal_icon
        self.yabridge_pending = False
        self.yabridge_notified = False
        self.window = StatusWindow(self)
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("Caracal Audio Controller")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._tray_activated)
        self.yabridge_timer = QTimer(self)
        self.yabridge_timer.setInterval(YABRIDGE_SCAN_INTERVAL_MS)
        self.yabridge_timer.timeout.connect(self.scan_yabridge_state)

    def start(self) -> int:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, APP_NAME, "No system tray is available in this session.")
            return 1
        self.tray.show()
        self.tray.showMessage(APP_NAME, "Ready. Left-click to sync Windows VSTs.", QSystemTrayIcon.Information, 3500)
        QTimer.singleShot(YABRIDGE_INITIAL_SCAN_MS, self.scan_yabridge_state)
        self.yabridge_timer.start()
        return self.app.exec()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        for key in ("update-audio", "route-plugins", "restart-pipewire"):
            action = ACTIONS[key]
            item = QAction(action.title, menu)
            item.setToolTip(action.description)
            item.triggered.connect(lambda checked=False, action_key=key: self.run_action(action_key))
            menu.addAction(item)

        menu.addSeparator()
        create_item = QAction(ACTIONS["virtual-create"].title, menu)
        create_item.triggered.connect(lambda: self.run_action("virtual-create"))
        menu.addAction(create_item)

        remove_item = QAction(ACTIONS["virtual-remove"].title, menu)
        remove_item.triggered.connect(lambda: self.run_action("virtual-remove"))
        menu.addAction(remove_item)

        bt_item = QAction(ACTIONS["toggle-bt-mic"].title, menu)
        bt_item.triggered.connect(lambda: self.run_action("toggle-bt-mic"))
        menu.addAction(bt_item)

        menu.addSeparator()
        installer_item = QAction("Open Software Installer", menu)
        installer_item.triggered.connect(self.open_software_installer)
        menu.addAction(installer_item)

        status_item = QAction("Show Status", menu)
        status_item.triggered.connect(self.show_status)
        menu.addAction(status_item)

        scan_item = QAction("Check Yabridge Sync State", menu)
        scan_item.triggered.connect(self.scan_yabridge_state)
        menu.addAction(scan_item)

        upgrade_item = QAction("Upgrade Caracal OS", menu)
        scan_item.triggered.connect(lambda: self.run_action("upgrade"))
        menu.addAction(upgrade_item)

        menu.addSeparator()
        quit_item = QAction("Quit", menu)
        quit_item.triggered.connect(self.app.quit)
        menu.addAction(quit_item)
        return menu

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.run_action("update-audio")
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_status()

    def run_action(self, key: str) -> None:
        action = ACTIONS[key]
        self._append_log(f"Starting: {action.title}")
        if action.terminal:
            self._run_in_terminal(action)
        else:
            self._run_direct(action)

    def show_status(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def open_software_installer(self) -> None:
        command = shutil.which("caracal-software-installer-gui") or shutil.which("caracal-software-installer")
        if command is None:
            self._append_log("Caracal Software Installer is not installed.")
            self.tray.showMessage(APP_NAME, "Caracal Software Installer is not installed.", QSystemTrayIcon.Warning, 4000)
            return
        subprocess.Popen([command], start_new_session=True)
        self._append_log("Opened Caracal Software Installer.")

    def _run_in_terminal(self, action: AudioAction) -> None:
        terminal = find_terminal()
        if terminal is None:
            self._append_log("No supported terminal emulator was found.")
            self.tray.showMessage(APP_NAME, "No supported terminal emulator was found.", QSystemTrayIcon.Critical, 5000)
            return

        command_text = " ".join(shlex.quote(part) for part in action.command)
        script = "\n".join(
            [
                "printf '\\nCaracal Audio Controller\\n========================\\n\\n'",
                f"echo {shlex.quote(action.title)}",
                "echo",
                command_text,
                "status=$?",
                "echo",
                "if [ \"$status\" -eq 0 ]; then",
                "  echo 'Action completed successfully.'",
                "else",
                "  echo \"Action failed with exit code $status.\"",
                "fi",
                "echo",
                "read -r -n 1 -s -p 'Press any key to close this window...'",
                "echo",
                "exit \"$status\"",
            ]
        )

        args = terminal_args(terminal, script)
        process = QProcess(self)
        process.setProgram(terminal)
        process.setArguments(args)
        process.setWorkingDirectory(str(Path.home()))
        process.finished.connect(
            lambda code, status, proc=process, key=action.key: self._terminal_finished(proc, key, code)
        )
        self.processes.append(process)
        process.start()
        if not process.waitForStarted(3000):
            self._append_log(f"Could not launch {terminal}.")
            self.tray.showMessage(APP_NAME, f"Could not launch {terminal}.", QSystemTrayIcon.Critical, 5000)
            self.processes.remove(process)
            return

        self._append_log(f"Launched terminal for: {' '.join(action.command)}")
        self.tray.showMessage(APP_NAME, f"Running {action.title}.", QSystemTrayIcon.Information, 3000)

    def _run_direct(self, action: AudioAction) -> None:
        process = QProcess(self)
        process.setProgram(action.command[0])
        process.setArguments(list(action.command[1:]))
        process.setWorkingDirectory(str(Path.home()))
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(lambda proc=process: self._read_process(proc))
        process.finished.connect(lambda code, status, proc=process, key=action.key: self._process_finished(proc, key, code))
        self.processes.append(process)
        process.start()
        if not process.waitForStarted(3000):
            self._append_log(f"Could not start: {' '.join(action.command)}")
            self.tray.showMessage(APP_NAME, f"Could not start {action.title}.", QSystemTrayIcon.Critical, 5000)
            self.processes.remove(process)

    def _read_process(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardOutput()).decode(errors="replace").strip()
        if text:
            self._append_log(text)

    def _process_finished(self, process: QProcess, key: str, exit_code: int) -> None:
        self._read_process(process)
        if process in self.processes:
            self.processes.remove(process)
        self.bus.direct_finished.emit(key, exit_code, ACTIONS[key].title)

    def _terminal_finished(self, process: QProcess, key: str, exit_code: int) -> None:
        if process in self.processes:
            self.processes.remove(process)

        if key == "update-audio" and exit_code == 0:
            self.mark_yabridge_synced()
            self.scan_yabridge_state(show_clear_message=True)
            return

        if exit_code != 0:
            title = ACTIONS[key].title
            self._append_log(f"{title} terminal exited with code {exit_code}.")

    def _direct_finished(self, key: str, exit_code: int, title: str) -> None:
        if exit_code == 0:
            message = f"{title} completed."
            icon = QSystemTrayIcon.Information
        else:
            message = f"{title} failed with exit code {exit_code}."
            icon = QSystemTrayIcon.Critical
        self._append_log(message)
        self.tray.showMessage(APP_NAME, message, icon, 4500)

    def scan_yabridge_state(self, show_clear_message: bool = False) -> None:
        fingerprint, item_count = yabridge_fingerprint()
        saved = read_yabridge_fingerprint()
        pending = item_count > 0 and fingerprint != saved
        self.set_yabridge_pending(pending, item_count, show_clear_message)

    def set_yabridge_pending(self, pending: bool, item_count: int, show_clear_message: bool = False) -> None:
        previous = self.yabridge_pending
        self.yabridge_pending = pending
        if pending:
            self.tray.setIcon(self.notification_icon)
            self.tray.setToolTip("Caracal Audio Controller - Windows VSTs need yabridge sync")
            message = f"{item_count} Windows VST item(s) detected. Run Sync Windows VSTs."
            if not previous:
                self._append_log(message)
            if not self.yabridge_notified:
                self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.Warning, 7000)
                self.yabridge_notified = True
        else:
            self.tray.setIcon(self.normal_icon)
            self.tray.setToolTip("Caracal Audio Controller")
            self.yabridge_notified = False
            if previous or show_clear_message:
                self._append_log("Yabridge sync state is current.")

    def mark_yabridge_synced(self) -> None:
        fingerprint, _item_count = yabridge_fingerprint()
        state_file = yabridge_state_file()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(fingerprint + "\n", encoding="utf-8")
        self._append_log("Saved yabridge sync snapshot.")

    def _append_log(self, message: str) -> None:
        self.window.append_log(message)


def find_terminal() -> str | None:
    preferred = [os.environ.get("TERMINAL", "").strip()]
    preferred.extend(
        [
            "alacritty",
            "ghostty",
            "konsole",
            "gnome-terminal",
            "ptyxis",
            "kgx",
            "kitty",
            "wezterm",
            "xfce4-terminal",
            "mate-terminal",
            "x-terminal-emulator",
            "xterm",
        ]
    )
    for candidate in preferred:
        if candidate and shutil.which(candidate):
            return candidate
    return None


def terminal_args(terminal: str, script: str) -> list[str]:
    name = Path(terminal).name
    home = str(Path.home())
    if name == "alacritty":
        return ["--working-directory", home, "-T", APP_NAME, "-e", "bash", "-lc", script]
    if name == {"konsole", "ghostty"}:
        return ["--workdir", home, "-e", "bash", "-lc", script]
    if name in {"gnome-terminal", "ptyxis", "mate-terminal"}:
        return ["--working-directory", home, "--", "bash", "-lc", script]
    if name == "kgx":
        return ["--working-directory", home, "bash", "-lc", script]
    if name == "kitty":
        return ["--directory", home, "bash", "-lc", script]
    if name == "wezterm":
        return ["start", "--cwd", home, "--", "bash", "-lc", script]
    if name == "xfce4-terminal":
        return ["--working-directory=" + home, "--command", "bash -lc " + shlex.quote(script)]
    if name == "xterm":
        return ["-T", APP_NAME, "-e", "bash", "-lc", script]
    return ["-e", "bash", "-lc", script]


def load_icon(kind: str = "normal") -> QIcon:
    for path in bundled_icon_paths(kind):
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon

    for path in (
        f"/usr/share/caracal-audio-controller/assets/{icon_filename(kind, 'white')}",
        f"/usr/share/caracal-audio-controller/assets/{icon_filename(kind, 'black')}",
        "/usr/share/caracal-audio-controller/assets/icon-white.svg",
        "/usr/share/pixmaps/caracal-audio-controller.svg",
        "/usr/share/pixmaps/caracal-software-installer.svg",
        "/usr/share/caracal-software-installer/assets/images/caracal.svg",
    ):
        if Path(path).exists():
            icon = QIcon(path)
            if not icon.isNull():
                return icon

    icon = QIcon.fromTheme("distributor-logo")
    if not icon.isNull():
        return icon

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(6, 6, 52, 52)
    painter.setPen(QColor(BG))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "C")
    painter.end()
    return QIcon(pixmap)


def bundled_icon_paths(kind: str) -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[2] / "assets" / icon_filename(kind, "white"),
        here.parents[2] / "assets" / icon_filename(kind, "black"),
        here.parents[2] / "assets" / "icon-white.svg",
        here.parents[2] / "assets" / "icon-black.svg",
    ]


def icon_filename(kind: str, color: str) -> str:
    if kind == "notification":
        return f"icon-{color}-notification.svg"
    return f"icon-{color}.svg"


def yabridge_vst3_dir() -> Path:
    return Path.home() / ".wine" / "drive_c" / "Program Files" / "Common Files" / "VST3"


def yabridge_state_file() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "caracal-audio-controller" / "yabridge-vst3.fingerprint"


def read_yabridge_fingerprint() -> str:
    try:
        return yabridge_state_file().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def yabridge_fingerprint() -> tuple[str, int]:
    root = yabridge_vst3_dir()
    if not root.exists():
        return "", 0

    hasher = hashlib.sha256()
    item_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).casefold()):
        if should_ignore_yabridge_path(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue

        relative = str(path.relative_to(root))
        if path.is_dir():
            if path.suffix.lower() == ".vst3":
                item_count += 1
            hasher.update(f"D:{relative}:{stat.st_mtime_ns}".encode())
        elif path.is_file():
            item_count += 1
            hasher.update(f"F:{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode())

    return hasher.hexdigest(), item_count


def should_ignore_yabridge_path(path: Path) -> bool:
    name = path.name
    return name in {"desktop.ini", ".DS_Store"} or name.endswith(".tmp")


def build_stylesheet() -> str:
    return f"""
        QMainWindow, QWidget {{
            background: {BG};
            color: {TEXT};
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 14px;
        }}
        QFrame#hero {{
            background: {PANEL};
            border: 1px solid rgba(138, 164, 176, 51);
            border-radius: 16px;
        }}
        QLabel#eyebrow {{
            color: {ACCENT};
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        QLabel#title {{
            color: {TEXT};
            font-size: 24px;
            font-weight: 700;
        }}
        QLabel#muted {{
            color: {MUTED};
        }}
        QPushButton {{
            min-height: 40px;
            padding: 0 14px;
            border-radius: 14px;
            border: 1px solid rgba(126, 156, 216, 86);
            background: rgba(126, 156, 216, 31);
            color: {TEXT};
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {INFO};
            background: rgba(127, 180, 202, 46);
        }}
        QTextEdit {{
            background: rgba(17, 17, 27, 150);
            color: {TEXT};
            border: 1px solid rgba(255, 255, 255, 16);
            border-radius: 14px;
            padding: 12px;
            font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
        }}
        QMenu {{
            background: {PANEL};
            color: {TEXT};
            border: 1px solid rgba(138, 164, 176, 51);
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 22px;
            border-radius: 8px;
        }}
        QMenu::item:selected {{
            background: rgba(127, 180, 202, 46);
        }}
    """


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    controller = AudioController()
    return controller.start()


if __name__ == "__main__":
    raise SystemExit(main())

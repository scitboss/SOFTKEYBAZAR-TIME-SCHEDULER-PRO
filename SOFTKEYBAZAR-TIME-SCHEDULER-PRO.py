# softkeybazar_time_scheduler.py
# SOFTKEYBAZAR TIME SCHEDULER PRO
# Develop by SOFTKEYBAZAR  |  www.softkeybazar.com  |  www.scitboss.com
# Facebook: facebook.com/cybercrackervai
#
# A lightweight Windows Time Scheduler (Shutdown / Restart / Sleep / RTC Wake).
# Standard library only. Single file. PyInstaller ready.

import os
import sys
import json
import time
import datetime
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
import calendar

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
APP_NAME = "SOFTKEYBAZAR TIME SCHEDULER PRO"
APP_VERSION = "1.0.0"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".softkeybazar_tsp")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
IS_WINDOWS = os.name == "nt"

DEFAULT_CONFIG = {
    "theme": "dark",
    "minimize_on_startup": True,
    "minimize_on_close": True,
    "start_with_windows": False,
    "auto_load_schedule": True,
    "notifications": {
        "before_5min": True,
        "before_1min": True,
        "before_10sec": True,
        "restart": True,
        "sleep": True,
        "activated": True,
        "cancelled": True,
    },
    "schedule": None,
}

# Colors
C_BG = "#0f1420"
C_CARD = "#1a2032"
C_CARD2 = "#232a40"
C_ACCENT = "#4f7cff"
C_ACCENT2 = "#8b5cf6"
C_TEXT = "#e6e9f2"
C_SUBTEXT = "#9aa4bf"
C_GREEN = "#22c55e"
C_RED = "#ef4444"
C_ORANGE = "#f59e0b"

# ----------------------------------------------------------------------------
# Config helpers
# ----------------------------------------------------------------------------
def ensure_config_dir():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception:
        pass

def load_config():
    ensure_config_dir()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            cfg.update(data)
            if "notifications" in data and isinstance(data["notifications"], dict):
                cfg["notifications"].update(data["notifications"])
            return cfg
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(cfg):
    ensure_config_dir()
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# ----------------------------------------------------------------------------
# Windows command helpers
# ----------------------------------------------------------------------------
def run_hidden(cmd):
    """Run a command without showing a console window."""
    try:
        creationflags = 0
        if IS_WINDOWS:
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        subprocess.Popen(cmd, shell=False, creationflags=creationflags)
        return True
    except Exception as e:
        print("Command failed:", cmd, e)
        return False

def do_shutdown(force=False):
    if not IS_WINDOWS:
        return False
    args = ["shutdown", "/s", "/t", "0"]
    if force:
        args.append("/f")
    return run_hidden(args)

def do_restart(force=False):
    if not IS_WINDOWS:
        return False
    args = ["shutdown", "/r", "/t", "0"]
    if force:
        args.append("/f")
    return run_hidden(args)

def do_sleep():
    if not IS_WINDOWS:
        return False
    # Use rundll32 to enter sleep
    return run_hidden(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])

def abort_shutdown():
    if not IS_WINDOWS:
        return False
    return run_hidden(["shutdown", "/a"])

def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def check_rtc_wake_support():
    """Best-effort check whether the system supports RTC wake alarms."""
    if not IS_WINDOWS:
        return False, "Not running on Windows."
    try:
        # powercfg -devicequery wake_armed lists devices armed to wake
        creationflags = 0x08000000
        out = subprocess.check_output(
            ["powercfg", "-devicequery", "wake_armed"],
            creationflags=creationflags, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
        # We just check powercfg exists and returns something
        return True, "RTC wake query succeeded. Configure the alarm in BIOS/UEFI RTC Wake."
    except Exception as e:
        return False, "Unable to query RTC wake devices: %s" % e

# ----------------------------------------------------------------------------
# Start with Windows helper
# ----------------------------------------------------------------------------
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def get_app_command():
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    return '"%s" "%s"' % (sys.executable, os.path.abspath(__file__))

def set_start_with_windows(enabled):
    if not IS_WINDOWS:
        return False, "Not on Windows."
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, "SoftkeybazarTSP", 0, winreg.REG_SZ, get_app_command())
        else:
            try:
                winreg.DeleteValue(key, "SoftkeybazarTSP")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True, "OK"
    except Exception as e:
        return False, str(e)

# ----------------------------------------------------------------------------
# Scheduler Engine
# ----------------------------------------------------------------------------
class SchedulerEngine:
    """
    Runs in a background thread. Checks the active schedule every second.
    Fires notifications and actions.
    """
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.schedule = None  # dict with keys: action, target (iso), repeat, enabled, notified keys
        self._stop = threading.Event()
        self.thread = None
        self.last_fire_key = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def set_schedule(self, schedule):
        with self.lock:
            self.schedule = schedule
            self.last_fire_key = None

    def get_schedule(self):
        with self.lock:
            return json.loads(json.dumps(self.schedule)) if self.schedule else None

    def clear_schedule(self):
        with self.lock:
            self.schedule = None
            self.last_fire_key = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print("Scheduler tick error:", e)
            self._stop.wait(1.0)

    def _tick(self):
        with self.lock:
            sched = self.schedule
        if not sched or not sched.get("enabled", True):
            return
        try:
            target = datetime.datetime.fromisoformat(sched["target"])
        except Exception:
            return
        now = datetime.datetime.now()
        remaining = (target - now).total_seconds()

        if remaining <= 0 and self.last_fire_key != sched["target"]:
            self.last_fire_key = sched["target"]
            self._fire(sched)
            return

        cfg = self.app.config.get("notifications", {})
        action = sched.get("action")

        # Notifications (only relevant for shutdown/restart/sleep)
        if action in ("shutdown", "restart", "sleep"):
            if cfg.get("before_5min") and 299 < remaining <= 300:
                self._notify("%s in 5 minutes" % action.title(),
                             "Your computer will %s in 5 minutes. Please save your work." % action)
            if cfg.get("before_1min") and 59 < remaining <= 60:
                self._notify("%s in 1 minute" % action.title(),
                             "Your computer will %s in 1 minute." % action)
            if cfg.get("before_10sec") and 9 < remaining <= 10:
                self._notify("%s in 10 seconds" % action.title(),
                             "Your computer will %s in 10 seconds!" % action)

    def _notify(self, title, msg):
        try:
            self.app.root.after(0, lambda: self.app.show_toast(title, msg))
        except Exception:
            pass

    def _fire(self, sched):
        action = sched.get("action")
        repeat = sched.get("repeat", "once")
        target = sched.get("target")

        # Show a toast
        self._notify("Executing", "Executing scheduled %s now." % action)

        # Execute
        if action == "shutdown":
            do_shutdown(force=False)
        elif action == "restart":
            do_restart(force=False)
        elif action == "sleep":
            do_sleep()
        elif action == "poweron":
            # Cannot actually power on; show guidance
            self._notify("Power ON", "RTC wake alarm time reached. System cannot power on from Python; configure BIOS/UEFI RTC Wake.")

        # Handle repeat
        if repeat in ("daily", "weekly") and target:
            try:
                dt = datetime.datetime.fromisoformat(target)
                if repeat == "daily":
                    dt = dt + datetime.timedelta(days=1)
                else:
                    dt = dt + datetime.timedelta(days=7)
                with self.lock:
                    if self.schedule and self.schedule.get("target") == target:
                        self.schedule["target"] = dt.isoformat()
                        self.schedule["notified"] = []
                        self.last_fire_key = None
                self.app.persist_schedule()
                self.app.refresh_dashboard()
            except Exception as e:
                print("Repeat reschedule error:", e)
        else:
            # one-time: clear
            with self.lock:
                if self.schedule and self.schedule.get("target") == target:
                    self.schedule["enabled"] = False
            self.app.persist_schedule()
            self.app.refresh_dashboard()


# ----------------------------------------------------------------------------
# Main Application
# ----------------------------------------------------------------------------
class TimeSchedulerApp:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.engine = SchedulerEngine(self)

        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.root.minsize(900, 660)
        self.root.configure(bg=C_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        try:
            self.root.iconbitmap(default="")  # no icon by default
        except Exception:
            pass

        self._build_styles()
        self._build_ui()
        self._load_saved_schedule()

        # Start clock update
        self._update_clock()
        # Start scheduler engine
        self.engine.start()

        # Toast holder
        self.toast_win = None

        # Minimize on startup (optional)
        if self.config.get("minimize_on_startup", False):
            self.root.after(300, self.minimize_window)

    # ---------------- Styling ----------------
    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=C_CARD, foreground=C_SUBTEXT,
                        padding=[16, 8], font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", C_ACCENT)],
                  foreground=[("selected", "white")])
        style.configure("TFrame", background=C_BG)
        style.configure("Card.TFrame", background=C_CARD)
        style.configure("TLabel", background=C_BG, foreground=C_TEXT,
                        font=("Segoe UI", 10))
        style.configure("Sub.TLabel", background=C_BG, foreground=C_SUBTEXT,
                        font=("Segoe UI", 9))
        style.configure("H1.TLabel", background=C_BG, foreground=C_TEXT,
                        font=("Segoe UI", 22, "bold"))
        style.configure("H2.TLabel", background=C_BG, foreground=C_TEXT,
                        font=("Segoe UI", 13, "bold"))
        style.configure("Card.TLabel", background=C_CARD, foreground=C_TEXT,
                        font=("Segoe UI", 10))
        style.configure("CardSub.TLabel", background=C_CARD, foreground=C_SUBTEXT,
                        font=("Segoe UI", 9))
        style.configure("Clock.TLabel", background=C_CARD, foreground=C_TEXT,
                        font=("Consolas", 34, "bold"))
        style.configure("Big.TLabel", background=C_CARD, foreground=C_ACCENT,
                        font=("Segoe UI", 16, "bold"))
        style.configure("TButton", font=("Segoe UI", 10, "bold"),
                        padding=[10, 6], borderwidth=0)
        style.configure("Accent.TButton", background=C_ACCENT, foreground="white")
        style.map("Accent.TButton",
                  background=[("active", "#3b66e0")])
        style.configure("Danger.TButton", background=C_RED, foreground="white")
        style.map("Danger.TButton",
                  background=[("active", "#cc3333")])
        style.configure("Success.TButton", background=C_GREEN, foreground="white")
        style.map("Success.TButton",
                  background=[("active", "#16a34a")])
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT,
                        font=("Segoe UI", 10))
        style.configure("TCombobox", fieldbackground=C_CARD2, background=C_CARD2,
                        foreground=C_TEXT, arrowcolor=C_TEXT)
        style.configure("TSpinbox", fieldbackground=C_CARD2, foreground=C_TEXT,
                        background=C_CARD2, arrowcolor=C_TEXT)
        style.configure("TEntry", fieldbackground=C_CARD2, foreground=C_TEXT,
                        insertcolor=C_TEXT)

    def _card(self, parent, **kw):
        f = tk.Frame(parent, bg=C_CARD, bd=0, highlightthickness=0)
        return f

    def _build_ui(self):
        # Header bar
        header = tk.Frame(self.root, bg=C_BG, height=70)
        header.pack(side="top", fill="x", padx=20, pady=(16, 0))

        title = tk.Label(header, text="SOFTKEYBAZAR  TIME SCHEDULER PRO",
                         bg=C_BG, fg=C_TEXT, font=("Segoe UI", 16, "bold"))
        title.pack(side="left")

        btn_frame = tk.Frame(header, bg=C_BG)
        btn_frame.pack(side="right")
        self._round_button(btn_frame, "—", self.minimize_window, w=40).pack(side="left", padx=4)
        self._round_button(btn_frame, "✕", self.on_close, w=40, color=C_RED).pack(side="left", padx=4)

        # Main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(10, 10))

        self.tab_dash = tk.Frame(self.notebook, bg=C_BG)
        self.tab_sched = tk.Frame(self.notebook, bg=C_BG)
        self.tab_power = tk.Frame(self.notebook, bg=C_BG)
        self.tab_settings = tk.Frame(self.notebook, bg=C_BG)
        self.tab_about = tk.Frame(self.notebook, bg=C_BG)

        self.notebook.add(self.tab_dash, text="  Dashboard  ")
        self.notebook.add(self.tab_sched, text="  Scheduler  ")
        self.notebook.add(self.tab_power, text="  Power ON  ")
        self.notebook.add(self.tab_settings, text="  Settings  ")
        self.notebook.add(self.tab_about, text="  About  ")

        self._build_dashboard()
        self._build_scheduler()
        self._build_poweron()
        self._build_settings()
        self._build_about()

        # Footer
        footer = tk.Frame(self.root, bg=C_BG)
        footer.pack(side="bottom", fill="x", padx=20, pady=(0, 10))
        tk.Label(footer, text="Develop by SOFTKEYBAZAR  |  www.softkeybazar.com  |  www.scitboss.com  |  facebook.com/cybercrackervai",
                 bg=C_BG, fg=C_SUBTEXT, font=("Segoe UI", 8)).pack(side="left")

    def _round_button(self, parent, text, command, w=100, color=C_ACCENT):
        btn = tk.Button(parent, text=text, command=command, bg=color, fg="white",
                        activebackground=color, activeforeground="white",
                        relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                        width=w // 8 if w >= 80 else 2, cursor="hand2")
        return btn

    # ---------------- Dashboard ----------------
    def _build_dashboard(self):
        wrap = tk.Frame(self.tab_dash, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        # Clock card
        clock_card = self._card(wrap)
        clock_card.pack(fill="x", pady=(0, 12), ipady=10)
        tk.Label(clock_card, text="CURRENT TIME", bg=C_CARD, fg=C_SUBTEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(12, 0))
        self.lbl_clock = tk.Label(clock_card, text="--:--:--", bg=C_CARD, fg=C_TEXT,
                                  font=("Consolas", 34, "bold"))
        self.lbl_clock.pack(anchor="w", padx=18)
        self.lbl_date = tk.Label(clock_card, text="", bg=C_CARD, fg=C_SUBTEXT,
                                 font=("Segoe UI", 10))
        self.lbl_date.pack(anchor="w", padx=18, pady=(0, 12))

        # Grid of status cards
        grid = tk.Frame(wrap, bg=C_BG)
        grid.pack(fill="both", expand=True)

        self.card_sched = self._status_card(grid, "NEXT SCHEDULE", "No active schedule", 0, 0)
        self.card_remain = self._status_card(grid, "TIME REMAINING", "00 : 00 : 00", 0, 1)
        self.card_power = self._status_card(grid, "COMPUTER POWER STATUS", "Running", 1, 0)
        self.card_active = self._status_card(grid, "ACTIVE SCHEDULE STATUS", "Idle", 1, 1)

        # Quick action buttons
        actions = tk.Frame(wrap, bg=C_BG)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="Cancel Active Schedule", style="Danger.TButton",
                   command=self.cancel_schedule).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Refresh", style="Accent.TButton",
                   command=self.refresh_dashboard).pack(side="left")

    def _status_card(self, parent, title, value, row, col):
        card = self._card(parent)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6, ipady=14)
        parent.grid_rowconfigure(row, weight=1)
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(card, text=title, bg=C_CARD, fg=C_SUBTEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        lbl = tk.Label(card, text=value, bg=C_CARD, fg=C_TEXT,
                       font=("Segoe UI", 14, "bold"), wraplength=380, justify="left")
        lbl.pack(anchor="w", padx=16, pady=(2, 12))
        return lbl

    # ---------------- Scheduler tab ----------------
    def _build_scheduler(self):
        wrap = tk.Frame(self.tab_sched, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        card = self._card(wrap)
        card.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(card, text="CREATE A SCHEDULE", bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=4,
                                                    sticky="w", padx=16, pady=(12, 6))

        # Action
        tk.Label(card, text="Action:", bg=C_CARD, fg=C_SUBTEXT).grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.var_action = tk.StringVar(value="shutdown")
        action_cb = ttk.Combobox(card, textvariable=self.var_action, state="readonly",
                                 values=["shutdown", "restart", "sleep"])
        action_cb.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        # Repeat
        tk.Label(card, text="Repeat:", bg=C_CARD, fg=C_SUBTEXT).grid(row=1, column=2, sticky="w", padx=16, pady=4)
        self.var_repeat = tk.StringVar(value="once")
        repeat_cb = ttk.Combobox(card, textvariable=self.var_repeat, state="readonly",
                                 values=["once", "daily", "weekly"])
        repeat_cb.grid(row=1, column=3, sticky="w", padx=4, pady=4)

        # Date
        tk.Label(card, text="Date:", bg=C_CARD, fg=C_SUBTEXT).grid(row=2, column=0, sticky="w", padx=16, pady=4)
        date_frame = tk.Frame(card, bg=C_CARD)
        date_frame.grid(row=2, column=1, columnspan=3, sticky="w", padx=4, pady=4)
        now = datetime.datetime.now()
        self.var_year = tk.StringVar(value=str(now.year))
        self.var_month = tk.StringVar(value=str(now.month))
        self.var_day = tk.StringVar(value=str(now.day))
        ttk.Spinbox(date_frame, from_=2024, to=2100, textvariable=self.var_year, width=6).pack(side="left", padx=2)
        ttk.Spinbox(date_frame, from_=1, to=12, textvariable=self.var_month, width=4).pack(side="left", padx=2)
        ttk.Spinbox(date_frame, from_=1, to=31, textvariable=self.var_day, width=4).pack(side="left", padx=2)

        # Time
        tk.Label(card, text="Time:", bg=C_CARD, fg=C_SUBTEXT).grid(row=3, column=0, sticky="w", padx=16, pady=4)
        time_frame = tk.Frame(card, bg=C_CARD)
        time_frame.grid(row=3, column=1, columnspan=3, sticky="w", padx=4, pady=4)
        self.var_hour = tk.StringVar(value="%02d" % now.hour)
        self.var_min = tk.StringVar(value="%02d" % now.minute)
        ttk.Spinbox(time_frame, from_=0, to=23, textvariable=self.var_hour, width=4, format="%02.0f").pack(side="left", padx=2)
        tk.Label(time_frame, text=":", bg=C_CARD, fg=C_TEXT).pack(side="left")
        ttk.Spinbox(time_frame, from_=0, to=59, textvariable=self.var_min, width=4, format="%02.0f").pack(side="left", padx=2)

        # Force?
        self.var_force = tk.BooleanVar(value=False)
        ttk.Checkbutton(card, text="Force close apps (not recommended)", variable=self.var_force).grid(
            row=4, column=1, columnspan=3, sticky="w", padx=4, pady=4)

        # Buttons
        btn_frame = tk.Frame(card, bg=C_CARD)
        btn_frame.grid(row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))
        ttk.Button(btn_frame, text="Schedule", style="Accent.TButton",
                   command=self.create_schedule).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel Active", style="Danger.TButton",
                   command=self.cancel_schedule).pack(side="left")

        # Status card
        status_card = self._card(wrap)
        status_card.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(status_card, text="ACTIVE SCHEDULE", bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        self.lbl_active_sched = tk.Label(status_card, text="No active schedule.",
                                         bg=C_CARD, fg=C_SUBTEXT, font=("Segoe UI", 10),
                                         justify="left", anchor="w")
        self.lbl_active_sched.pack(anchor="w", padx=16, pady=(0, 12))

        # Info
        info = tk.Label(wrap, text="Note: The scheduler runs only while this application is running. "
                                   "Enable 'Start with Windows' in Settings for reliability.",
                        bg=C_BG, fg=C_SUBTEXT, font=("Segoe UI", 9), justify="left")
        info.pack(anchor="w", padx=6)

    # ---------------- Power ON tab ----------------
    def _build_poweron(self):
        wrap = tk.Frame(self.tab_power, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        card = self._card(wrap)
        card.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(card, text="AUTOMATIC COMPUTER POWER ON (RTC WAKE ALARM)",
                 bg=C_CARD, fg=C_TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 6))

        info_text = (
            "Python cannot power on a completely powered-off computer. Automatic power-on "
            "requires hardware support: RTC Wake Alarm in your motherboard BIOS/UEFI.\n\n"
            "Steps to enable RTC Wake Alarm:\n"
            "1. Restart your computer and enter BIOS/UEFI (usually Del, F2, or F10).\n"
            "2. Find 'Power Management', 'APM', or 'RTC Wake' settings.\n"
            "3. Enable 'Wake on RTC' / 'RTC Alarm' / 'Resume by Alarm'.\n"
            "4. Set the desired date and time (usually local time).\n"
            "5. Save and exit.\n\n"
            "After configuring, the computer will power on at the scheduled time even when off.\n"
            "This application can help you check support and store your desired time."
        )
        tk.Label(card, text=info_text, bg=C_CARD, fg=C_SUBTEXT, font=("Segoe UI", 9),
                 justify="left", anchor="w", wraplength=840).pack(anchor="w", padx=16, pady=(0, 12))

        # Support check
        btn_frame = tk.Frame(card, bg=C_CARD)
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))
        ttk.Button(btn_frame, text="Check RTC Wake Support", style="Accent.TButton",
                   command=self.check_rtc).pack(side="left", padx=(0, 8))
        self.lbl_rtc_status = tk.Label(card, text="Status: Not checked.",
                                       bg=C_CARD, fg=C_SUBTEXT, font=("Segoe UI", 10))
        self.lbl_rtc_status.pack(anchor="w", padx=16, pady=(0, 12))

        # Store desired time
        card2 = self._card(wrap)
        card2.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(card2, text="STORE DESIRED POWER-ON TIME", bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 6))
        frame = tk.Frame(card2, bg=C_CARD)
        frame.pack(anchor="w", padx=16, pady=(0, 8))
        now = datetime.datetime.now()
        self.var_p_year = tk.StringVar(value=str(now.year))
        self.var_p_month = tk.StringVar(value=str(now.month))
        self.var_p_day = tk.StringVar(value=str(now.day))
        self.var_p_hour = tk.StringVar(value="%02d" % now.hour)
        self.var_p_min = tk.StringVar(value="%02d" % now.minute)
        ttk.Spinbox(frame, from_=2024, to=2100, textvariable=self.var_p_year, width=6).pack(side="left", padx=2)
        ttk.Spinbox(frame, from_=1, to=12, textvariable=self.var_p_month, width=4).pack(side="left", padx=2)
        ttk.Spinbox(frame, from_=1, to=31, textvariable=self.var_p_day, width=4).pack(side="left", padx=2)
        tk.Label(frame, text="   ", bg=C_CARD).pack(side="left")
        ttk.Spinbox(frame, from_=0, to=23, textvariable=self.var_p_hour, width=4, format="%02.0f").pack(side="left", padx=2)
        tk.Label(frame, text=":", bg=C_CARD, fg=C_TEXT).pack(side="left")
        ttk.Spinbox(frame, from_=0, to=59, textvariable=self.var_p_min, width=4, format="%02.0f").pack(side="left", padx=2)

        ttk.Button(card2, text="Save Power-On Time", style="Accent.TButton",
                   command=self.save_poweron_time).pack(anchor="w", padx=16, pady=(0, 12))
        self.lbl_poweron_saved = tk.Label(card2, text="", bg=C_CARD, fg=C_GREEN,
                                          font=("Segoe UI", 9))
        self.lbl_poweron_saved.pack(anchor="w", padx=16, pady=(0, 8))

    def check_rtc(self):
        ok, msg = check_rtc_wake_support()
        if ok:
            self.lbl_rtc_status.config(text="Status: " + msg, fg=C_GREEN)
            messagebox.showinfo("RTC Wake", msg + "\n\nFollow the BIOS/UEFI instructions in this tab.")
        else:
            self.lbl_rtc_status.config(text="Status: " + msg, fg=C_ORANGE)
            messagebox.showwarning("RTC Wake", msg + "\n\nConfigure RTC Wake Alarm manually in BIOS/UEFI.")

    def save_poweron_time(self):
        try:
            y = int(self.var_p_year.get()); mo = int(self.var_p_month.get()); d = int(self.var_p_day.get())
            h = int(self.var_p_hour.get()); mi = int(self.var_p_min.get())
            dt = datetime.datetime(y, mo, d, h, mi)
        except Exception as e:
            messagebox.showerror("Invalid time", "Please enter a valid date and time.\n\n%s" % e)
            return
        sched = {
            "action": "poweron",
            "target": dt.isoformat(),
            "repeat": "once",
            "enabled": True,
            "created": datetime.datetime.now().isoformat(),
        }
        self.engine.set_schedule(sched)
        self.persist_schedule()
        self.lbl_poweron_saved.config(text="Saved desired power-on time: %s\nConfigure your BIOS RTC alarm to match." % dt.strftime("%Y-%m-%d %H:%M"))
        self.refresh_dashboard()
        messagebox.showinfo("Power ON", "Saved. Remember: Python cannot power on the PC. "
                                        "Configure RTC Wake Alarm in BIOS/UEFI.")

    # ---------------- Settings tab ----------------
    def _build_settings(self):
        wrap = tk.Frame(self.tab_settings, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        card = self._card(wrap)
        card.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(card, text="GENERAL SETTINGS", bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 6))

        self.var_min_start = tk.BooleanVar(value=self.config.get("minimize_on_startup", True))
        self.var_min_close = tk.BooleanVar(value=self.config.get("minimize_on_close", True))
        self.var_start_win = tk.BooleanVar(value=self.config.get("start_with_windows", False))
        self.var_autoload = tk.BooleanVar(value=self.config.get("auto_load_schedule", True))

        ttk.Checkbutton(card, text="Minimize on startup", variable=self.var_min_start).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card, text="Minimize on close (instead of exit)", variable=self.var_min_close).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card, text="Start with Windows", variable=self.var_start_win).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card, text="Auto-load previous schedule on start", variable=self.var_autoload).pack(anchor="w", padx=16, pady=(2, 10))

        # Notifications
        card2 = self._card(wrap)
        card2.pack(fill="x", pady=(0, 12), ipady=8)
        tk.Label(card2, text="NOTIFICATIONS", bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 6))
        n = self.config.get("notifications", {})
        self.var_n5 = tk.BooleanVar(value=n.get("before_5min", True))
        self.var_n1 = tk.BooleanVar(value=n.get("before_1min", True))
        self.var_n10 = tk.BooleanVar(value=n.get("before_10sec", True))
        self.var_nact = tk.BooleanVar(value=n.get("activated", True))
        self.var_ncan = tk.BooleanVar(value=n.get("cancelled", True))
        ttk.Checkbutton(card2, text="Notify 5 minutes before action", variable=self.var_n5).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card2, text="Notify 1 minute before action", variable=self.var_n1).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card2, text="Notify 10 seconds before action", variable=self.var_n10).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card2, text="Notify on schedule activated", variable=self.var_nact).pack(anchor="w", padx=16, pady=2)
        ttk.Checkbutton(card2, text="Notify on schedule cancelled", variable=self.var_ncan).pack(anchor="w", padx=16, pady=(2, 10))

        # Buttons
        btn_frame = tk.Frame(wrap, bg=C_BG)
        btn_frame.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_frame, text="Save Settings", style="Accent.TButton",
                   command=self.save_settings).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Open Config Folder", command=self.open_config_folder).pack(side="left")

    def save_settings(self):
        self.config["minimize_on_startup"] = bool(self.var_min_start.get())
        self.config["minimize_on_close"] = bool(self.var_min_close.get())
        self.config["auto_load_schedule"] = bool(self.var_autoload.get())
        self.config["notifications"] = {
            "before_5min": bool(self.var_n5.get()),
            "before_1min": bool(self.var_n1.get()),
            "before_10sec": bool(self.var_n10.get()),
            "activated": bool(self.var_nact.get()),
            "cancelled": bool(self.var_ncan.get()),
            "restart": True,
            "sleep": True,
        }
        # start with windows
        want_start = bool(self.var_start_win.get())
        if want_start != self.config.get("start_with_windows", False):
            ok, msg = set_start_with_windows(want_start)
            if not ok:
                messagebox.showwarning("Start with Windows", "Could not update startup entry:\n%s" % msg)
            else:
                self.config["start_with_windows"] = want_start
        save_config(self.config)
        messagebox.showinfo("Settings", "Settings saved.")

    def open_config_folder(self):
        try:
            if IS_WINDOWS:
                os.startfile(CONFIG_DIR)
            else:
                subprocess.Popen(["xdg-open", CONFIG_DIR])
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---------------- About tab ----------------
    def _build_about(self):
        wrap = tk.Frame(self.tab_about, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        card = self._card(wrap)
        card.pack(fill="both", expand=True, ipady=10)
        txt = (
            "SOFTKEYBAZAR TIME SCHEDULER PRO\n"
            "Version %s\n\n"
            "A lightweight Windows scheduler for Shutdown, Restart, Sleep,\n"
            "and RTC Wake Alarm guidance.\n\n"
            "Develop by SOFTKEYBAZAR\n"
            "www.softkeybazar.com\n"
            "www.scitboss.com\n"
            "facebook.com/cybercrackervai\n\n"
            "Safety:\n"
            "- Never deletes files\n"
            "- Never modifies personal documents\n"
            "- Does not force shutdown by default\n"
            "- Always warns you to save your work\n\n"
            "Important:\n"
            "The scheduler runs while this application is running.\n"
            "For reliable scheduling even after closing, enable 'Start with Windows'.\n\n"
            "Python cannot power on a fully powered-off PC without hardware\n"
            "RTC Wake support configured in BIOS/UEFI."
        ) % APP_VERSION
        tk.Label(card, text=txt, bg=C_CARD, fg=C_TEXT, justify="left",
                 anchor="w", font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=18)

    # ---------------- Clock & dashboard refresh ----------------
    def _update_clock(self):
        now = datetime.datetime.now()
        self.lbl_clock.config(text=now.strftime("%I:%M:%S %p"))
        self.lbl_date.config(text=now.strftime("%A, %d %B %Y"))
        self.refresh_dashboard()
        self.root.after(1000, self._update_clock)

    def refresh_dashboard(self):
        sched = self.engine.get_schedule()
        if not sched or not sched.get("enabled", True):
            self.card_sched.config(text="No active schedule")
            self.card_remain.config(text="00 : 00 : 00")
            self.card_active.config(text="Idle", fg=C_SUBTEXT)
            self.lbl_active_sched.config(text="No active schedule.", fg=C_SUBTEXT)
            return
        try:
            target = datetime.datetime.fromisoformat(sched["target"])
        except Exception:
            return
        action = sched.get("action", "?")
        repeat = sched.get("repeat", "once")
        self.card_sched.config(text="%s at %s (%s)" % (action.title(),
                              target.strftime("%I:%M %p"), repeat))
        now = datetime.datetime.now()
        remaining = int((target - now).total_seconds())
        if remaining < 0:
            remaining = 0
        h = remaining // 3600
        m = (remaining % 3600) // 60
        s = remaining % 60
        self.card_remain.config(text="%02d : %02d : %02d" % (h, m, s))
        self.card_active.config(text="ACTIVE", fg=C_GREEN)
        self.lbl_active_sched.config(
            text="Action: %s\nTarget: %s\nRepeat: %s\nRemaining: %02d:%02d:%02d"
                 % (action.title(), target.strftime("%Y-%m-%d %I:%M %p"), repeat, h, m, s),
            fg=C_TEXT)

    # ---------------- Scheduling actions ----------------
    def create_schedule(self):
        try:
            y = int(self.var_year.get()); mo = int(self.var_month.get()); d = int(self.var_day.get())
            h = int(self.var_hour.get()); mi = int(self.var_min.get())
            target = datetime.datetime(y, mo, d, h, mi, 0)
        except Exception as e:
            messagebox.showerror("Invalid date/time", "Please enter a valid date and time.\n\n%s" % e)
            return
        now = datetime.datetime.now()
        if target <= now:
            messagebox.showerror("Invalid time", "The scheduled time must be in the future.")
            return
        action = self.var_action.get()
        repeat = self.var_repeat.get()
        if action not in ("shutdown", "restart", "sleep"):
            messagebox.showerror("Invalid action", "Unknown action.")
            return
        if not messagebox.askyesno("Confirm schedule",
                                   "Schedule %s at %s (%s)?\n\nPlease save your work before the scheduled time."
                                   % (action.title(), target.strftime("%Y-%m-%d %I:%M %p"), repeat)):
            return
        sched = {
            "action": action,
            "target": target.isoformat(),
            "repeat": repeat,
            "enabled": True,
            "created": now.isoformat(),
            "force": bool(self.var_force.get()),
        }
        self.engine.set_schedule(sched)
        self.persist_schedule()
        self.refresh_dashboard()
        if self.config.get("notifications", {}).get("activated", True):
            self.show_toast("Schedule activated",
                            "%s at %s (%s)" % (action.title(), target.strftime("%I:%M %p"), repeat))
        messagebox.showinfo("Scheduled", "Schedule created successfully.")

    def cancel_schedule(self):
        sched = self.engine.get_schedule()
        if not sched:
            messagebox.showinfo("Cancel", "There is no active schedule.")
            return
        if not messagebox.askyesno("Cancel schedule", "Cancel the active %s schedule?" % sched.get("action", "")):
            return
        self.engine.clear_schedule()
        self.persist_schedule()
        self.refresh_dashboard()
        if self.config.get("notifications", {}).get("cancelled", True):
            self.show_toast("Schedule cancelled", "The active schedule has been cancelled.")
        messagebox.showinfo("Cancelled", "Schedule cancelled.")

    def persist_schedule(self):
        try:
            sched = self.engine.get_schedule()
            self.config["schedule"] = sched
            save_config(self.config)
        except Exception:
            pass

    def _load_saved_schedule(self):
        if not self.config.get("auto_load_schedule", True):
            return
        sched = self.config.get("schedule")
        if not sched:
            return
        try:
            target = datetime.datetime.fromisoformat(sched["target"])
        except Exception:
            return
        if target <= datetime.datetime.now() and sched.get("repeat") == "once":
            # expired one-time
            self.config["schedule"] = None
            save_config(self.config)
            return
        self.engine.set_schedule(sched)
        self.refresh_dashboard()

    # ---------------- Toast notifications ----------------
    def show_toast(self, title, msg):
        try:
            if self.toast_win is not None and self.toast_win.winfo_exists():
                self.toast_win.destroy()
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.configure(bg=C_ACCENT)
            w.attributes("-topmost", True)
            w.geometry("+%d+%d" % (self.root.winfo_screenwidth() - 420, 40))
            frame = tk.Frame(w, bg=C_CARD, padx=14, pady=10)
            frame.pack(padx=2, pady=2, fill="both", expand=True)
            tk.Label(frame, text=title, bg=C_CARD, fg=C_TEXT,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(frame, text=msg, bg=C_CARD, fg=C_SUBTEXT,
                     font=("Segoe UI", 9), wraplength=360, justify="left").pack(anchor="w")
            self.toast_win = w
            self.root.after(5000, lambda: (w.destroy() if w.winfo_exists() else None))
        except Exception:
            pass

    # ---------------- Window control ----------------
    def minimize_window(self):
        try:
            self.root.iconify()
        except Exception:
            pass

    def on_close(self):
        if self.config.get("minimize_on_close", True):
            if messagebox.askyesno("Close", "Minimize to taskbar instead of exiting?\n\nYes = Minimize, No = Exit"):
                self.minimize_window()
                return
        if messagebox.askyesno("Exit", "Exit %s?\n\nActive schedules will stop running." % APP_NAME):
            try:
                self.engine.stop()
                save_config(self.config)
            except Exception:
                pass
            self.root.destroy()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def main():
    if IS_WINDOWS:
        try:
            # DPI awareness for crisp UI
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    app = TimeSchedulerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

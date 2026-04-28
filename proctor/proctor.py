"""
CPEEN 2026 – Proctor Agent
Rulează pe calculatorul elevului în timpul examenului.
Verifică la fiecare 5 secunde dacă sunt deschise aplicații nepermise.
Prima abatere → descalificare imediată și ireversibilă în Firestore.
"""

import json
import os
import sys
import time
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

import requests
import psutil

try:
    import win32gui
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# ── Firebase ──────────────────────────────────────────────────────────────────

FIREBASE_PROJECT = "cpeen2026"
FIREBASE_API_KEY = "AIzaSyDXO5QS3N4Mb2uG_BQLSyk6a6_8dlY4Evo"
FS_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
    f"/databases/(default)/documents"
)

CHECK_INTERVAL = 5  # secunde între verificări

# ── Clasificare ferestre ──────────────────────────────────────────────────────

# Procesele browser-elor cunoscute
BROWSERS = {
    "chrome", "msedge", "firefox", "opera", "brave", "vivaldi",
    "iexplore", "chromium", "edge", "waterfox", "pale moon", "basilisk",
}

# Procese sistem Windows – ferestrele lor sunt mereu permise
SYSTEM_PROCS = {
    "dwm", "svchost", "csrss", "wininit", "winlogon", "lsass", "services",
    "spoolsv", "taskhostw", "sihost", "fontdrvhost", "runtimebroker",
    "applicationframehost", "shellhost", "ctfmon", "conhost", "textinputhost",
    "searchapp", "searchui", "lockapp", "lockscreenhost", "systemsettings",
    "settingssynchostexe", "securityhealthservice", "audiodg", "wudfhost",
    "wlanext", "msmpeng", "trustedinstaller", "ntoskrnl",
}

# Cuvinte cheie care confirmă că browser-ul afișează site-ul examenului
EXAM_TITLE_KW = ["cpeen 2026", "cpeen2026", "cpeen", "localhost", "127.0.0.1"]

# PID-ul propriului proces – ferestrele noastre sunt mereu permise
OWN_PID = os.getpid()


def classify(hwnd):
    """
    Returnează (allowed: bool, reason: str) pentru un HWND vizibil.
    """
    try:
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        return True, "no_title"

    if not title or len(title.strip()) < 3:
        return True, "empty_title"

    t = title.lower().strip()

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return True, "pid_err"

    # Fereastră a propriului nostru proces → mereu permisă
    if pid == OWN_PID:
        return True, "own_window"

    try:
        proc_raw = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True, "proc_gone"

    p = proc_raw.lower().replace(".exe", "").strip()

    # Windows Explorer: permis doar ca shell (bara de activități, desktop)
    # O fereastră Explorer cu titlu real = File Explorer deschis = interzis
    if p == "explorer":
        if not t or len(t) < 3 or t in ("program manager", "desktop"):
            return True, "shell"
        return False, f"file_explorer:{title[:70]}"

    # Procese sistem → permise
    if p in SYSTEM_PROCS:
        return True, f"system:{p}"

    # Proces browser: permis DOAR dacă afișează site-ul examenului
    if p in BROWSERS:
        if any(kw in t for kw in EXAM_TITLE_KW):
            return True, "exam_browser"
        return False, f"browser_forbidden:{title[:80]}"

    # Orice altceva → interzis
    return False, f"forbidden:{p}:{title[:70]}"


def scan_violations():
    """
    Returnează lista de (title, proc, reason) pentru ferestrele vizibile interzise.
    """
    if not HAS_WIN32:
        return []

    found = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        allowed, reason = classify(hwnd)
        if not allowed:
            try:
                title = win32gui.GetWindowText(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    proc = psutil.Process(pid).name()
                except Exception:
                    proc = "?"
                found.append((title, proc, reason))
            except Exception:
                pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass

    return found


# ── Firestore helpers ─────────────────────────────────────────────────────────

def _fs_val(v):
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: _fs_val(vv) for k, vv in v.items()}}}
    return {"nullValue": None}


def fs_write(collection, doc_id, data):
    url = f"{FS_BASE}/{collection}/{doc_id}"
    body = {"fields": {k: _fs_val(v) for k, v in data.items()}}
    try:
        r = requests.patch(
            url, json=body,
            params={"key": FIREBASE_API_KEY},
            timeout=8,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def fs_verify_code(code):
    """
    Verifică dacă codul există în colecția de participanți.
    Returnează True dacă valid; True și dacă Firestore e inaccesibil (offline).
    """
    url = f"{FS_BASE}/cpeen/participants"
    try:
        r = requests.get(url, params={"key": FIREBASE_API_KEY}, timeout=8)
        if r.status_code != 200:
            return True
        fields = r.json().get("fields", {})
        items_json = fields.get("items_json", {}).get("stringValue", "")
        if not items_json:
            return True
        participants = json.loads(items_json)
        return any(
            p.get("accessCode", "").upper() == code.upper()
            for p in participants
        )
    except Exception:
        return True


# ── Monitor core ──────────────────────────────────────────────────────────────

class ProctorMonitor:
    """Rulează într-un thread de fundal, verifică la fiecare CHECK_INTERVAL secunde."""

    def __init__(self, code: str):
        self.code = code.upper().strip()
        self.running = False
        self.disqualified = False
        self.check_count = 0

        self._on_ok = None    # callback()
        self._on_disq = None  # callback(reason: str)

    def on_ok(self, cb):
        self._on_ok = cb

    def on_disq(self, cb):
        self._on_disq = cb

    def _write_heartbeat(self, status: str, info: str):
        data = {
            "participantCode": self.code,
            "ts": int(time.time() * 1000),
            "tsStr": datetime.now().isoformat(),
            "checkCount": self.check_count,
            "status": status,
            "info": info[:200],
            "disqualified": self.disqualified,
        }
        # Un singur document per participant, suprascris la fiecare heartbeat
        threading.Thread(
            target=fs_write,
            args=("proctorLogs", self.code, data),
            daemon=True,
        ).start()

    def _write_disqualified(self, reason: str):
        """Scrie în proctorFlags/{code} — admin-ul citește de acolo."""
        data = {
            "participantCode": self.code,
            "ts": int(time.time() * 1000),
            "tsStr": datetime.now().isoformat(),
            "reason": reason[:300],
            "disqualified": True,
        }
        # Scriere sincronă cu reîncercări — trebuie să reușească
        for _ in range(4):
            if fs_write("proctorFlags", self.code, data):
                return
            time.sleep(2)

    def _loop(self):
        self.running = True
        while self.running and not self.disqualified:
            time.sleep(CHECK_INTERVAL)
            if not self.running:
                break

            self.check_count += 1
            violations = scan_violations()

            if violations:
                title, proc, reason = violations[0]
                self.disqualified = True
                self._write_heartbeat("disqualified", reason)
                self._write_disqualified(reason)
                self.running = False
                if self._on_disq:
                    self._on_disq(f"{proc}: {title[:60]}")
                break
            else:
                self._write_heartbeat("ok", "all_clear")
                if self._on_ok:
                    self._on_ok()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False


# ── GUI ───────────────────────────────────────────────────────────────────────

C_DARK  = "#1F3864"
C_MID   = "#2E75B6"
C_GREEN = "#1D6027"
C_RED   = "#C0392B"
C_BG    = "#f0f4f8"
C_WHITE = "#ffffff"


class ProctorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CPEEN 2026 – Proctor")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.monitor: ProctorMonitor = None
        self.monitoring_active = False

        self._show_login()

    # ── helpers ────────────────────────────────────────────────────────────

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()

    # ── Screen 1: Login ────────────────────────────────────────────────────

    def _show_login(self):
        self._clear()
        self.monitoring_active = False
        self.root.configure(bg=C_BG)
        self.root.geometry("400x340")

        tk.Label(
            self.root, text="CPEEN 2026",
            font=("Georgia", 20, "bold"), fg=C_DARK, bg=C_BG,
        ).pack(pady=(28, 4))
        tk.Label(
            self.root, text="Agent de monitorizare examen",
            font=("Arial", 10), fg="#666", bg=C_BG,
        ).pack()

        frm = tk.Frame(self.root, bg=C_BG)
        frm.pack(pady=20, padx=40, fill="x")

        tk.Label(
            frm, text="Cod de acces:", font=("Arial", 10, "bold"),
            fg=C_DARK, bg=C_BG, anchor="w",
        ).pack(fill="x")

        self._code_var = tk.StringVar()
        entry = tk.Entry(
            frm, textvariable=self._code_var,
            font=("Courier New", 22), justify="center",
            bg=C_WHITE, relief="solid", bd=1,
        )
        entry.pack(fill="x", ipady=8, pady=(4, 14))
        entry.bind("<Return>", lambda _: self._do_login())
        entry.focus_set()

        def _fmt(*_):
            v = self._code_var.get().upper().replace(" ", "")
            if len(v) > 8:
                v = v[:8]
            self._code_var.set(v)
        self._code_var.trace_add("write", _fmt)

        self._err_lbl = tk.Label(frm, text="", fg=C_RED, font=("Arial", 9), bg=C_BG)
        self._err_lbl.pack(pady=(0, 6))

        tk.Button(
            frm, text="Pornește monitorizarea →",
            command=self._do_login,
            bg=C_MID, fg=C_WHITE, activebackground=C_DARK, activeforeground=C_WHITE,
            font=("Arial", 12, "bold"), relief="flat",
            padx=10, pady=10, cursor="hand2",
        ).pack(fill="x")

        tk.Label(
            self.root,
            text="Prin apăsarea butonului confirmi acordul de monitorizare\n"
                 "în conformitate cu regulamentul CPEEN 2026.",
            font=("Arial", 8), fg="#aaa", bg=C_BG,
            wraplength=360, justify="center",
        ).pack(pady=10)

    def _do_login(self):
        code = self._code_var.get().strip()
        if len(code) != 8:
            self._err_lbl.config(text="Codul de acces trebuie să aibă exact 8 caractere.")
            return
        self._err_lbl.config(text="Se verifică codul…")
        self.root.update()
        threading.Thread(
            target=self._verify_and_precheck, args=(code,), daemon=True
        ).start()

    def _verify_and_precheck(self, code):
        valid = fs_verify_code(code)
        if not valid:
            self.root.after(0, lambda: self._err_lbl.config(
                text="Cod invalid. Verificați codul și contactați administratorul."
            ))
            return
        violations = scan_violations()
        self.root.after(0, lambda: self._show_precheck(code, violations))

    # ── Screen 2: Pre-check ────────────────────────────────────────────────

    def _show_precheck(self, code, violations):
        self._clear()
        self.root.configure(bg=C_BG)

        if violations:
            self.root.geometry("460x400")
            tk.Label(
                self.root, text="⚠  Închide aplicațiile nepermise",
                font=("Arial", 13, "bold"), fg="#7a4f00", bg="#fff3cd",
                padx=12, pady=10,
            ).pack(fill="x")
            tk.Label(
                self.root,
                text="Sunt deschise ferestre nepermise. Închide-le înainte de a începe:",
                font=("Arial", 10), fg="#555", bg=C_BG, wraplength=420,
            ).pack(pady=(12, 4), padx=16, anchor="w")

            box = tk.Frame(self.root, bg=C_WHITE, relief="solid", bd=1)
            box.pack(fill="x", padx=16, pady=4)
            for title, proc, _ in violations[:8]:
                row = f"  •  {proc}  —  {title[:55]}"
                tk.Label(
                    box, text=row,
                    font=("Courier New", 9), fg=C_RED, bg=C_WHITE,
                    anchor="w", pady=3,
                ).pack(fill="x")

            tk.Label(
                self.root,
                text="Închide toate aplicațiile de mai sus, apoi apasă 'Reverificați'.",
                font=("Arial", 9), fg="#666", bg=C_BG, wraplength=420,
            ).pack(pady=(8, 4), padx=16)

            btn_row = tk.Frame(self.root, bg=C_BG)
            btn_row.pack(pady=8)
            tk.Button(
                btn_row, text="↻  Reverificați",
                command=lambda: self._reverify(code),
                bg=C_MID, fg=C_WHITE, font=("Arial", 10, "bold"),
                relief="flat", padx=12, pady=6, cursor="hand2",
            ).pack(side="left", padx=6)
            tk.Button(
                btn_row, text="← Înapoi",
                command=self._show_login,
                bg="#ddd", fg="#555", font=("Arial", 10),
                relief="flat", padx=12, pady=6, cursor="hand2",
            ).pack(side="left", padx=6)

        else:
            self.root.geometry("400x280")
            tk.Label(
                self.root, text="✓  Pregătit pentru monitorizare",
                font=("Arial", 13, "bold"), fg=C_GREEN, bg="#e8f5e9",
                padx=12, pady=10,
            ).pack(fill="x")
            tk.Label(
                self.root,
                text=f"Participant: {code}",
                font=("Courier New", 14, "bold"), fg=C_DARK, bg=C_BG,
            ).pack(pady=(20, 4))
            tk.Label(
                self.root,
                text=f"Nu sunt detectate aplicații interzise.\n"
                     f"Verificare la fiecare {CHECK_INTERVAL} secunde.\n"
                     f"Prima abatere → descalificare automată.",
                font=("Arial", 10), fg="#555", bg=C_BG,
                justify="center",
            ).pack(pady=8)
            tk.Button(
                self.root, text="Începe examenul →",
                command=lambda: self._start_monitoring(code),
                bg=C_GREEN, fg=C_WHITE, activebackground="#155220",
                font=("Arial", 13, "bold"), relief="flat",
                padx=16, pady=10, cursor="hand2",
            ).pack(pady=14)

    def _reverify(self, code):
        self._show_precheck(code, scan_violations())

    # ── Screen 3: Monitoring ────────────────────────────────────────────────

    def _start_monitoring(self, code):
        self.monitor = ProctorMonitor(code)
        self.monitor.on_ok(self._on_ok)
        self.monitor.on_disq(self._on_disq)
        self.monitor.start()
        self.monitoring_active = True
        self._show_monitor(code)

    def _show_monitor(self, code):
        self._clear()
        self.root.configure(bg=C_BG)
        self.root.geometry("360x260")

        hdr = tk.Frame(self.root, bg=C_GREEN)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="●  MONITORIZARE ACTIVĂ",
            font=("Arial", 11, "bold"), fg=C_WHITE, bg=C_GREEN, pady=10,
        ).pack()

        card = tk.Frame(self.root, bg=C_WHITE, padx=16, pady=10)
        card.pack(fill="x", padx=16, pady=12)
        tk.Label(
            card, text=f"Participant: {code}",
            font=("Courier New", 13, "bold"), fg=C_DARK, bg=C_WHITE,
        ).pack(anchor="w")
        tk.Label(
            card, text=f"Verificare la fiecare {CHECK_INTERVAL} secunde",
            font=("Arial", 9), fg="#888", bg=C_WHITE,
        ).pack(anchor="w")

        self._status_var = tk.StringVar(value="Se inițializează…")
        self._status_lbl = tk.Label(
            self.root, textvariable=self._status_var,
            font=("Arial", 10, "bold"), fg=C_MID, bg=C_BG,
        )
        self._status_lbl.pack(pady=(0, 2))

        self._info_var = tk.StringVar(value="Verificări: 0  |  Ora: —")
        tk.Label(
            self.root, textvariable=self._info_var,
            font=("Arial", 9), fg="#aaa", bg=C_BG,
        ).pack()

        tk.Label(
            self.root, text="Nu închide această fereastră în timpul examenului.",
            font=("Arial", 8), fg="#ccc", bg=C_BG,
        ).pack(pady=(10, 2))

        tk.Button(
            self.root, text="Finalizează sesiunea",
            command=self._confirm_stop,
            bg="#e0e4e8", fg="#555", relief="flat",
            font=("Arial", 9), padx=8, pady=4, cursor="hand2",
        ).pack()

        self._tick()

    def _tick(self):
        if self.monitor and not self.monitor.disqualified:
            self._info_var.set(
                f"Verificări: {self.monitor.check_count}  |  "
                f"Ora: {datetime.now().strftime('%H:%M:%S')}"
            )
        self.root.after(1000, self._tick)

    def _on_ok(self):
        self.root.after(0, lambda: (
            self._status_var.set("✓  Status: OK — nicio abatere detectată"),
            self._status_lbl.config(fg=C_GREEN),
        ))

    def _on_disq(self, reason):
        self.monitoring_active = False
        self.root.after(0, lambda: self._show_disqualified(reason))

    # ── Screen 4: Disqualified ─────────────────────────────────────────────

    def _show_disqualified(self, reason):
        self._clear()
        self.root.configure(bg=C_RED)
        self.root.geometry("440x320")

        tk.Label(
            self.root, text="DESCALIFICAT",
            font=("Arial", 32, "bold"), fg=C_WHITE, bg=C_RED,
        ).pack(pady=(36, 6))

        tk.Label(
            self.root, text="Activitate nepermisă detectată și raportată automat.",
            font=("Arial", 10), fg=C_WHITE, bg=C_RED, wraplength=400,
        ).pack()

        cause = reason.split(":")[-1].strip()[:70] if ":" in reason else reason[:70]
        tk.Label(
            self.root, text=f"Motiv: {cause}",
            font=("Arial", 9), fg="#ffaaaa", bg=C_RED,
            wraplength=400, justify="center",
        ).pack(pady=6)

        tk.Label(
            self.root,
            text="Administratorul a fost notificat automat.\n"
                 "Această decizie este înregistrată și IREVERSIBILĂ.",
            font=("Arial", 10, "bold"), fg=C_WHITE, bg=C_RED,
            wraplength=400, justify="center",
        ).pack(pady=12)

        tk.Button(
            self.root, text="Închide",
            command=self.root.destroy,
            bg="#8B2020", fg=C_WHITE, activebackground="#6a1515",
            relief="flat", font=("Arial", 10), padx=16, pady=6, cursor="hand2",
        ).pack(pady=4)

    # ── Window management ──────────────────────────────────────────────────

    def _confirm_stop(self):
        if messagebox.askyesno(
            "Finalizare sesiune",
            "Ești sigur că vrei să oprești monitorizarea?\n"
            "Sesiunea ta va fi marcată ca terminată.",
            parent=self.root,
        ):
            self._do_stop()

    def _on_close(self):
        if self.monitoring_active:
            messagebox.showwarning(
                "Monitorizare activă",
                "Nu poți închide aplicația în timpul examenului.\n"
                "Folosește butonul 'Finalizează sesiunea'.",
                parent=self.root,
            )
        else:
            self._do_stop()

    def _do_stop(self):
        if self.monitor:
            self.monitor.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ProctorApp()
    app.run()

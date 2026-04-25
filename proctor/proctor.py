"""
CPEEN 2026 – Proctor Agent
Agent de monitorizare examen. Se distribuie elevilor ca proctor.exe.
Monitorizează ferestrele active și raportează la Firestore.
"""

import os
import sys
import time
import json
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from collections import deque

import requests
import psutil

try:
    import win32gui
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("[WARN] pywin32 not installed – window detection disabled.")

# ── Firebase config (sync cu js/config.js) ───────────────────────────────────
FIREBASE_PROJECT = "cpeen2026"
FIREBASE_API_KEY = "AIzaSyDXO5QS3N4Mb2uG_BQLSyk6a6_8dlY4Evo"
FS_BASE = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}/databases/(default)/documents"

HEARTBEAT_SEC  = 10   # trimite date la server la fiecare N secunde
MONITOR_SEC    = 1    # verifică fereastra activă la fiecare N secunde
SUSP_THRESHOLD = 5    # comutări suspecte până la descalificare automată

# Cuvinte cheie care indică fereastră suspectă (traduse/AI/social)
SUSPICIOUS_KW = [
    "google translate", "deepl", "chat gpt", "chatgpt", "openai", "bard", "gemini",
    "grammarly", "quizlet", "chegg", "brainly", "socratic",
    "facebook", "instagram", "telegram", "whatsapp", "discord",
    "tiktok", "youtube", "netflix", "twitch",
    "notepad", "word", "notepad++", "sublime", "vscode",
]

# Cuvinte cheie care indică fereastra examenului (nu se sancționează)
EXAM_KW = ["cpeen", "englishgrammarchallenge", "examen", "exam", "localhost", "127.0.0.1"]


# ── Firestore REST helpers ────────────────────────────────────────────────────

def _fs_val(v):
    """Convertește o valoare Python în format Firestore."""
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_fs_val(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: _fs_val(vv) for k, vv in v.items()}}}
    return {"nullValue": None}


def fs_write(collection, doc_id, data: dict, merge=False):
    """Scrie un document în Firestore (create sau update)."""
    url = f"{FS_BASE}/{collection}/{doc_id}"
    params = {"key": FIREBASE_API_KEY}
    if merge:
        # updateMask permite scriere parțială
        fields_str = ",".join(data.keys())
        params["updateMask.fieldPaths"] = list(data.keys())

    body = {"fields": {k: _fs_val(v) for k, v in data.items()}}
    try:
        r = requests.patch(url, json=body, params=params, timeout=6)
        return r.status_code in (200, 201)
    except requests.RequestException:
        return False


def fs_read(collection, doc_id):
    """Citește un document din Firestore. Returnează dict sau None."""
    url = f"{FS_BASE}/{collection}/{doc_id}"
    try:
        r = requests.get(url, params={"key": FIREBASE_API_KEY}, timeout=6)
        if r.status_code != 200:
            return None
        doc = r.json()
        fields = doc.get("fields", {})
        # Dezasamblare simplă (nur stringValue / integerValue / booleanValue)
        result = {}
        for k, v in fields.items():
            if "stringValue" in v:
                result[k] = v["stringValue"]
            elif "integerValue" in v:
                result[k] = int(v["integerValue"])
            elif "booleanValue" in v:
                result[k] = v["booleanValue"]
        return result
    except requests.RequestException:
        return None


def verify_participant(code):
    """Verifică dacă codul există în participants (cpeen/participants).
    Returnează True/False – simplu check (nu descarcăm toată lista)."""
    doc = fs_read("cpeen", "participants")
    if not doc:
        return True  # offline → permitem continuarea
    # participants e stocat ca stringValue cu JSON
    raw = doc.get("data", "") or doc.get("participants", "")
    if not raw:
        return True
    try:
        participants = json.loads(raw)
        return any(p.get("accessCode", "").upper() == code.upper() for p in participants)
    except Exception:
        return True


# ── Window monitor ────────────────────────────────────────────────────────────

def get_active_window():
    """Returnează (titlu, proces) ale ferestrei active."""
    if not HAS_WIN32:
        return "unknown", "unknown"
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            proc_name = ""
        return title, proc_name
    except Exception:
        return "", ""


def is_suspicious(title: str, proc: str) -> bool:
    t = title.lower()
    p = proc.lower()
    return any(kw in t or kw in p for kw in SUSPICIOUS_KW)


def is_exam_window(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in EXAM_KW)


# ── Proctor Agent core ────────────────────────────────────────────────────────

class ProctorAgent:
    def __init__(self, participant_code: str, session_id: str):
        self.code       = participant_code.upper().strip()
        self.session_id = session_id
        self.running    = False

        self.events: deque = deque(maxlen=500)
        self.last_window   = ""
        self.last_heartbeat = time.time()
        self.switch_count   = 0
        self.suspicious_count = 0
        self.disqualified   = False

        self._warn_cb   = None   # callback(title, proc, is_susp)
        self._disq_cb   = None   # callback()

    # ── callbacks ──────────────────────────────────────────────────────────

    def on_warn(self, cb):   self._warn_cb = cb
    def on_disq(self, cb):   self._disq_cb = cb

    # ── heartbeat ──────────────────────────────────────────────────────────

    def _send_heartbeat(self, current_window, current_proc):
        events_snapshot = list(self.events)
        self.events.clear()

        payload = {
            "participantCode": self.code,
            "sessionId":       self.session_id,
            "ts":              int(time.time() * 1000),
            "tsStr":           datetime.now().isoformat(),
            "switchCount":     self.switch_count,
            "suspiciousCount": self.suspicious_count,
            "currentWindow":   current_window[:200],
            "currentProcess":  current_proc[:100],
            "events":          json.dumps(events_snapshot[-50:]),  # ultimele 50
            "disqualified":    self.disqualified,
        }
        doc_id = f"{self.session_id}_hb_{int(time.time())}"
        ok = fs_write("proctorLogs", doc_id, payload)
        if not ok:
            # Repune evenimentele în coadă dacă trimiterea a eșuat
            self.events.extendleft(reversed(events_snapshot))

    def _flag_disqualified(self):
        self.disqualified = True
        data = {
            "participantCode":  self.code,
            "sessionId":        self.session_id,
            "ts":               int(time.time() * 1000),
            "tsStr":            datetime.now().isoformat(),
            "reason":           "Comutări suspecte excesive",
            "switchCount":      self.switch_count,
            "suspiciousCount":  self.suspicious_count,
            "disqualified":     True,
        }
        # Scriem în proctorFlags/{code} – admin-ul citește de acolo
        fs_write("proctorFlags", self.code, data)

    # ── monitor loop (rulat pe thread separat) ────────────────────────────

    def _loop(self):
        self.running = True
        while self.running:
            title, proc = get_active_window()
            now = time.time()

            # Detectăm comutare fereastră
            if title and title != self.last_window:
                if self.last_window:
                    susp = is_suspicious(title, proc)
                    etype = "SUSPICIOUS_SWITCH" if susp else "APP_SWITCH"
                    if susp:
                        self.suspicious_count += 1
                    self.switch_count += 1
                    self.events.append({
                        "type":    etype,
                        "ts":      int(now * 1000),
                        "window":  title[:200],
                        "process": proc[:100],
                    })
                    if self._warn_cb:
                        self._warn_cb(title, proc, susp)
                self.last_window = title

            # Heartbeat
            if now - self.last_heartbeat >= HEARTBEAT_SEC:
                threading.Thread(
                    target=self._send_heartbeat,
                    args=(title, proc),
                    daemon=True
                ).start()
                self.last_heartbeat = now

                # Descalificare automată
                if not self.disqualified and self.suspicious_count >= SUSP_THRESHOLD:
                    threading.Thread(target=self._flag_disqualified, daemon=True).start()
                    if self._disq_cb:
                        self._disq_cb()

            time.sleep(MONITOR_SEC)

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self):
        self.running = False


# ── GUI ───────────────────────────────────────────────────────────────────────

class ProctorUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CPEEN 2026 – Proctor")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.agent: ProctorAgent = None
        self._build_login()

    # ── Login screen ──────────────────────────────────────────────────────

    def _build_login(self):
        self._clear()
        self.root.geometry("380x280")

        tk.Label(self.root, text="CPEEN 2026", font=("Georgia", 16, "bold"),
                 fg="#1F3864").pack(pady=(24, 4))
        tk.Label(self.root, text="Agent de monitorizare examen",
                 font=("Arial", 10), fg="#555").pack()

        frm = tk.Frame(self.root)
        frm.pack(pady=20, padx=30, fill="x")

        tk.Label(frm, text="Cod de acces:", anchor="w").pack(fill="x")
        self._code_var = tk.StringVar()
        self._code_entry = tk.Entry(frm, textvariable=self._code_var,
                                    font=("Courier", 16), justify="center",
                                    width=12, bg="#f0f4f8")
        self._code_entry.pack(fill="x", ipady=6, pady=4)
        self._code_entry.bind("<Return>", lambda _: self._do_login())

        tk.Label(frm, text="ID sesiune (din email/admin):", anchor="w").pack(fill="x", pady=(8, 0))
        self._sid_var = tk.StringVar()
        tk.Entry(frm, textvariable=self._sid_var, width=24).pack(fill="x", ipady=4, pady=4)

        self._err_lbl = tk.Label(frm, text="", fg="red", font=("Arial", 9))
        self._err_lbl.pack()

        btn = tk.Button(frm, text="Pornește monitorizarea →",
                        command=self._do_login,
                        bg="#2E75B6", fg="white",
                        font=("Arial", 11, "bold"), relief="flat",
                        padx=10, pady=8)
        btn.pack(fill="x", pady=(4, 0))

        tk.Label(self.root,
                 text="Această aplicație monitorizează activitatea în timpul examenului.\n"
                      "Prin apăsarea butonului, confirmi că ești de acord.",
                 font=("Arial", 8), fg="#999", wraplength=340, justify="center").pack(pady=8)

    def _do_login(self):
        code = self._code_var.get().strip().upper()
        sid  = self._sid_var.get().strip()
        if len(code) < 4:
            self._err_lbl.config(text="Cod de acces prea scurt.")
            return
        if not sid:
            self._err_lbl.config(text="Introduceți ID-ul sesiunii.")
            return

        self._err_lbl.config(text="Se verifică…")
        self.root.update()

        self.agent = ProctorAgent(code, sid)
        self.agent.on_warn(self._on_warn)
        self.agent.on_disq(self._on_disqualified)
        self.agent.start()
        self._build_monitor(code, sid)

    # ── Monitor screen ────────────────────────────────────────────────────

    def _build_monitor(self, code, sid):
        self._clear()
        self.root.geometry("340x220")

        tk.Label(self.root, text="● MONITORIZARE ACTIVĂ",
                 font=("Arial", 11, "bold"), fg="#1D6027").pack(pady=(16, 4))

        info = tk.Frame(self.root, bg="#f0f4f8", padx=10, pady=8)
        info.pack(fill="x", padx=20)
        tk.Label(info, text=f"Participant: {code}", bg="#f0f4f8",
                 font=("Courier", 11, "bold")).pack(anchor="w")
        tk.Label(info, text=f"Sesiune: {sid}", bg="#f0f4f8",
                 font=("Arial", 9), fg="#555").pack(anchor="w")

        stats = tk.Frame(self.root)
        stats.pack(pady=10)
        self._switch_var   = tk.StringVar(value="Comutări: 0")
        self._susp_var     = tk.StringVar(value="Suspecte: 0")
        self._status_var   = tk.StringVar(value="OK")
        tk.Label(stats, textvariable=self._switch_var, font=("Arial", 10)).grid(row=0, column=0, padx=16)
        tk.Label(stats, textvariable=self._susp_var,  font=("Arial", 10)).grid(row=0, column=1, padx=16)
        self._status_lbl = tk.Label(stats, textvariable=self._status_var,
                                    font=("Arial", 10, "bold"), fg="#1D6027")
        self._status_lbl.grid(row=0, column=2, padx=16)

        self._hb_var = tk.StringVar(value="Heartbeat: —")
        tk.Label(self.root, textvariable=self._hb_var, font=("Arial", 8), fg="#aaa").pack()

        tk.Label(self.root,
                 text="Nu închide această fereastră în timpul examenului.",
                 font=("Arial", 8), fg="#888").pack(pady=4)

        tk.Button(self.root, text="Finalizează sesiunea",
                  command=self._confirm_stop,
                  bg="#C0392B", fg="white", relief="flat",
                  font=("Arial", 9), padx=8, pady=4).pack(pady=4)

        self._poll_stats()

    def _poll_stats(self):
        if self.agent:
            self._switch_var.set(f"Comutări: {self.agent.switch_count}")
            self._susp_var.set(f"Suspecte: {self.agent.suspicious_count}")
            self._hb_var.set(f"Ultimul heartbeat: {datetime.now().strftime('%H:%M:%S')}")
            if self.agent.disqualified:
                self._status_var.set("DESCALIFICAT")
                self._status_lbl.config(fg="#C0392B")
        self.root.after(2000, self._poll_stats)

    # ── Warning popup ─────────────────────────────────────────────────────

    def _on_warn(self, title, proc, is_susp):
        if not is_susp:
            return  # comutare normală, nu avertizăm
        # Rulăm pe thread principal (tkinter nu e thread-safe)
        self.root.after(0, lambda: self._show_warning(title))

    def _show_warning(self, title):
        messagebox.showwarning(
            "Avertisment – CPEEN Proctor",
            f"Activitate suspectă detectată!\n\nFereastră: {title[:80]}\n\n"
            f"Această activitate a fost înregistrată și raportată.\n"
            f"Continuați să comutați la alte aplicații riscați descalificarea.",
            parent=self.root
        )

    def _on_disqualified(self):
        self.root.after(0, self._show_disqualified)

    def _show_disqualified(self):
        messagebox.showerror(
            "DESCALIFICAT – CPEEN Proctor",
            "Ai fost marcat(ă) ca DESCALIFICAT(Ă) din cauza activităților suspecte repetate.\n\n"
            "Administratorul a fost notificat automat.",
            parent=self.root
        )

    # ── Close / stop ──────────────────────────────────────────────────────

    def _confirm_stop(self):
        if messagebox.askyesno("Finalizare", "Ești sigur(ă) că vrei să oprești monitorizarea?"):
            self._do_stop()

    def _on_close(self):
        if self.agent and self.agent.running:
            messagebox.showwarning(
                "Nu poți închide",
                "Aplicația Proctor nu poate fi închisă în timpul examenului.\n"
                "Utilizează butonul 'Finalizează sesiunea'.",
                parent=self.root
            )
        else:
            self._do_stop()

    def _do_stop(self):
        if self.agent:
            self.agent.stop()
        self.root.destroy()

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()

    def run(self):
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ui = ProctorUI()
    ui.run()

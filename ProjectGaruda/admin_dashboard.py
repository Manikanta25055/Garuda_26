# admin_dashboard.py
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

class AdminDashboard(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        self.current_tab = None
        self.build_ui()

    def build_ui(self):
        # Create a menubar for navigation.
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)
        admin_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Admin Tools", menu=admin_menu)
        admin_menu.add_command(label="Code Management", command=lambda: self.switch_tab("code"))
        admin_menu.add_command(label="System & User Management", command=lambda: self.switch_tab("system"))
        admin_menu.add_command(label="AI & Narada Customization", command=lambda: self.switch_tab("ai"))
        admin_menu.add_command(label="Mode Customization", command=lambda: self.switch_tab("mode"))
        
        # Container for tab frames.
        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.tabs = {}
        self.tabs["code"] = self.build_code_management_tab()
        self.tabs["system"] = self.build_system_user_management_tab()
        self.tabs["ai"] = self.build_ai_narada_customization_tab()
        self.tabs["mode"] = self.build_mode_customization_tab()
        
        self.switch_tab("code")

    def switch_tab(self, tab_name):
        if self.current_tab:
            self.current_tab.pack_forget()
        self.current_tab = self.tabs.get(tab_name)
        if self.current_tab:
            self.current_tab.pack(fill="both", expand=True)

    def build_code_management_tab(self):
        frame = ttk.Frame(self.container)
        ttk.Label(frame, text="Code Management & Customization", font=("Helvetica", 16)).pack(pady=10)
        # A scrolled text widget for live code editing.
        self.code_editor = scrolledtext.ScrolledText(frame, wrap="word", width=100, height=20)
        try:
            with open("current_source.py", "r") as f:
                code = f.read()
        except Exception:
            code = "# Source code not available."
        self.code_editor.insert("1.0", code)
        self.code_editor.pack(padx=10, pady=10)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save Changes", command=self.save_code).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Run Code", command=self.run_code).grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="Backup Code", command=self.backup_code).grid(row=0, column=2, padx=5)
        ttk.Button(btn_frame, text="Restore Code", command=self.restore_code).grid(row=0, column=3, padx=5)
        return frame

    def save_code(self):
        code = self.code_editor.get("1.0", "end")
        try:
            with open("current_source.py", "w") as f:
                f.write(code)
            messagebox.showinfo("Success", "Code saved successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Error saving code: {e}")

    def run_code(self):
        messagebox.showinfo("Run Code", "Restarting system with updated code...")
        print("[ADMIN] Run Code triggered.")

    def backup_code(self):
        try:
            with open("current_source.py", "r") as src, open("backup_source.py", "w") as dest:
                dest.write(src.read())
            messagebox.showinfo("Backup", "Code backup completed.")
        except Exception as e:
            messagebox.showerror("Error", f"Error backing up code: {e}")

    def restore_code(self):
        try:
            with open("backup_source.py", "r") as src, open("current_source.py", "w") as dest:
                dest.write(src.read())
            with open("backup_source.py", "r") as src:
                code = src.read()
            self.code_editor.delete("1.0", "end")
            self.code_editor.insert("1.0", code)
            messagebox.showinfo("Restore", "Code restored from backup.")
        except Exception as e:
            messagebox.showerror("Error", f"Error restoring code: {e}")

    def build_system_user_management_tab(self):
        frame = ttk.Frame(self.container)
        ttk.Label(frame, text="System & User Management", font=("Helvetica", 16)).pack(pady=10)
        user_frame = ttk.Frame(frame)
        user_frame.pack(pady=10, fill="x", padx=10)
        ttk.Label(user_frame, text="Registered Users:").grid(row=0, column=0, sticky="w")
        self.user_listbox = tk.Listbox(user_frame, height=5)
        self.user_listbox.grid(row=1, column=0, sticky="ew")
        for u in ["user (role: user)", "admin (role: admin)", "moderator (role: moderator)"]:
            self.user_listbox.insert(tk.END, u)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Add User", command=lambda: messagebox.showinfo("Info", "Add User functionality")).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Edit User", command=lambda: messagebox.showinfo("Info", "Edit User functionality")).grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="Delete User", command=lambda: messagebox.showinfo("Info", "Delete User functionality")).grid(row=0, column=2, padx=5)
        ttk.Label(frame, text="Real-Time System Logs & Monitoring:", font=("Helvetica", 12)).pack(pady=10)
        self.sys_log = scrolledtext.ScrolledText(frame, wrap="word", width=80, height=10)
        self.sys_log.insert("1.0", "System logs will appear here...\nCPU: 45%\nRAM: 60%\nDisk: 70%")
        self.sys_log.configure(state="disabled")
        self.sys_log.pack(padx=10, pady=10)
        return frame

    def build_ai_narada_customization_tab(self):
        frame = ttk.Frame(self.container)
        ttk.Label(frame, text="AI & Narada Customization", font=("Helvetica", 16)).pack(pady=10)
        cmd_frame = ttk.Frame(frame)
        cmd_frame.pack(pady=10, fill="x", padx=10)
        ttk.Label(cmd_frame, text="Custom Voice Commands:").grid(row=0, column=0, sticky="w")
        self.cmd_listbox = tk.Listbox(cmd_frame, height=5)
        self.cmd_listbox.grid(row=1, column=0, sticky="ew")
        for cmd in ["'Turn on lights' -> Activate Light Mode", "'Start recording' -> Begin video capture"]:
            self.cmd_listbox.insert(tk.END, cmd)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Add Command", command=lambda: messagebox.showinfo("Info", "Add Command functionality")).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Delete Command", command=lambda: messagebox.showinfo("Info", "Delete Command functionality")).grid(row=0, column=1, padx=5)
        wake_frame = ttk.Frame(frame)
        wake_frame.pack(pady=10, fill="x", padx=10)
        ttk.Label(wake_frame, text="Custom Wake Word:").grid(row=0, column=0, sticky="w")
        self.entry_wakeword = ttk.Entry(wake_frame)
        self.entry_wakeword.grid(row=0, column=1, padx=5)
        ttk.Button(wake_frame, text="Set Wake Word", command=lambda: messagebox.showinfo("Info", f"Wake word set to '{self.entry_wakeword.get().strip()}'")).grid(row=0, column=2, padx=5)
        return frame

    def build_mode_customization_tab(self):
        frame = ttk.Frame(self.container)
        ttk.Label(frame, text="Mode Customization & Advanced Controls", font=("Helvetica", 16)).pack(pady=10)
        mode_frame = ttk.Frame(frame)
        mode_frame.pack(pady=10, fill="x", padx=10)
        ttk.Label(mode_frame, text="Create New Mode:").grid(row=0, column=0, sticky="w")
        self.entry_new_mode = ttk.Entry(mode_frame)
        self.entry_new_mode.grid(row=0, column=1, padx=5)
        ttk.Button(mode_frame, text="Create Mode", command=lambda: messagebox.showinfo("Info", f"Mode '{self.entry_new_mode.get().strip()}' created")).grid(row=0, column=2, padx=5)
        ttk.Label(frame, text="Existing Modes:").pack(pady=5)
        self.mode_listbox = tk.Listbox(frame, height=5)
        for mode in ["DND Mode", "Idle Mode", "Night Mode"]:
            self.mode_listbox.insert(tk.END, mode)
        self.mode_listbox.pack(padx=10, pady=5, fill="x")
        email_frame = ttk.Frame(frame)
        email_frame.pack(pady=10, fill="x", padx=10)
        ttk.Label(email_frame, text="Email Recipient:").grid(row=0, column=0, sticky="w")
        self.entry_email = ttk.Entry(email_frame)
        self.entry_email.grid(row=0, column=1, padx=5)
        self.entry_email.insert(0, "vishwatejdonkeshwar@gmail.com")
        ttk.Label(email_frame, text="Cooldown (sec):").grid(row=1, column=0, sticky="w")
        self.entry_cooldown = ttk.Entry(email_frame)
        self.entry_cooldown.grid(row=1, column=1, padx=5)
        self.entry_cooldown.insert(0, "60")
        ttk.Button(email_frame, text="Save Email Settings", command=lambda: messagebox.showinfo("Info", "Email settings updated")).grid(row=2, column=0, columnspan=2, pady=5)
        ttk.Button(frame, text="Configure Automated Night Report", command=lambda: messagebox.showinfo("Info", "Night report settings updated")).pack(pady=5)
        return frame


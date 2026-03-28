# login_module.py
import tkinter as tk
from tkinter import ttk, messagebox
import random
import smtplib
from email.mime.text import MIMEText

def send_otp_email(otp):
    """
    Sends an OTP to the admin email using SMTP.
    (Make sure your Gmail account allows SMTP access and use an app password if needed.)
    """
    try:
        msg = MIMEText(f"Your OTP for Garuda Admin Login is: {otp}")
        msg['Subject'] = "OTP for Garuda Admin Login"
        msg['From'] = "mgonugondlamanikanta@gmail.com"
        msg['To'] = "mgonugondlamanikanta@gmail.com"
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login("mgonugondlamanikanta@gmail.com", "nhxc zjtl azxm iixw")
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print("Failed to send OTP email:", e)
        messagebox.showerror("Error", "Failed to send OTP email. Please check your SMTP settings.")

class LoginPage(tk.Frame):
    def __init__(self, master, on_login_success):
        """
        on_login_success: Callback that receives a role string ("admin" or "user")
        """
        super().__init__(master)
        self.master = master
        self.on_login_success = on_login_success
        self.selected_role = None
        self.otp = None
        self.build_selection_screen()

    def build_selection_screen(self):
        for widget in self.winfo_children():
            widget.destroy()
        title = ttk.Label(self, text="Select Your Profile", font=("Helvetica", 16))
        title.pack(pady=20)
        container = ttk.Frame(self)
        container.pack(pady=10)
        # Admin avatar
        self.admin_canvas = tk.Canvas(container, width=100, height=100, highlightthickness=0)
        self.admin_canvas.create_oval(10, 10, 90, 90, fill="lightblue")
        self.admin_canvas.create_text(50, 50, text="Admin", font=("Helvetica", 12))
        self.admin_canvas.grid(row=0, column=0, padx=20)
        self.admin_canvas.bind("<Button-1>", lambda e: self.select_role("admin"))
        # User avatar
        self.user_canvas = tk.Canvas(container, width=100, height=100, highlightthickness=0)
        self.user_canvas.create_oval(10, 10, 90, 90, fill="lightgreen")
        self.user_canvas.create_text(50, 50, text="User", font=("Helvetica", 12))
        self.user_canvas.grid(row=0, column=1, padx=20)
        self.user_canvas.bind("<Button-1>", lambda e: self.select_role("user"))

    def select_role(self, role):
        self.selected_role = role
        self.build_login_form()

    def build_login_form(self):
        for widget in self.winfo_children():
            widget.destroy()
        header = ttk.Label(self, text=f"Login as {self.selected_role.capitalize()}", font=("Helvetica", 16))
        header.pack(pady=20)
        form = ttk.Frame(self)
        form.pack(pady=10)
        ttk.Label(form, text="Username:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.entry_username = ttk.Entry(form)
        self.entry_username.grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(form, text="Password:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.entry_password = ttk.Entry(form, show="*")
        self.entry_password.grid(row=1, column=1, padx=5, pady=5)
        login_btn = ttk.Button(self, text="Login", command=self.verify_credentials)
        login_btn.pack(pady=10)
        back_btn = ttk.Button(self, text="Back", command=self.build_selection_screen)
        back_btn.pack()

    def verify_credentials(self):
        username = self.entry_username.get().strip()
        password = self.entry_password.get().strip()
        if self.selected_role == "admin":
            if username == "admin" and password == "root":
                self.otp = str(random.randint(100000, 999999))
                send_otp_email(self.otp)
                self.build_otp_form()
            else:
                messagebox.showerror("Error", "Invalid admin credentials.")
        else:
            if username == "user" and password == "user":
                self.on_login_success("user")
            else:
                messagebox.showerror("Error", "Invalid user credentials.")

    def build_otp_form(self):
        for widget in self.winfo_children():
            widget.destroy()
        header = ttk.Label(self, text="Enter OTP", font=("Helvetica", 16))
        header.pack(pady=20)
        note = ttk.Label(self, text="An OTP has been sent to mgonugondlamanikanta@gmail.com")
        note.pack(pady=5)
        self.entry_otp = ttk.Entry(self)
        self.entry_otp.pack(pady=10)
        verify_btn = ttk.Button(self, text="Verify OTP", command=self.verify_otp)
        verify_btn.pack(pady=10)
        back_btn = ttk.Button(self, text="Back", command=self.build_login_form)
        back_btn.pack()

    def verify_otp(self):
        entered = self.entry_otp.get().strip()
        if entered == self.otp:
            self.on_login_success("admin")
        else:
            messagebox.showerror("Error", "Invalid OTP. Please try again.")


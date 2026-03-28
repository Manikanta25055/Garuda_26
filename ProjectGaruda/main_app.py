# main_app.py
import tkinter as tk
from login_module import LoginPage
from admin_dashboard import AdminDashboard
from user_dashboard import UserDashboard

try:
    import garuda_pipeline as _garuda_pipeline
    _HAS_PIPELINE = True
except ImportError:
    _HAS_PIPELINE = False  # standalone UI mode without hardware pipeline

class MainApplication(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Garuda Security System")
        self.geometry("900x700")
        self.current_frame = None
        self.show_login_page()
        if _HAS_PIPELINE:
            _garuda_pipeline.start_pipeline()  # Start the pipeline

    def show_login_page(self):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = LoginPage(self, self.on_login_success)
        self.current_frame.pack(fill="both", expand=True)

    def on_login_success(self, role):
        self.current_frame.destroy()
        if role == "admin":
            self.show_admin_dashboard()
        else:
            self.show_user_dashboard()

    def show_admin_dashboard(self):
        self.current_frame = AdminDashboard(self)
        self.current_frame.pack(fill="both", expand=True)

    def show_user_dashboard(self):
        self.current_frame = UserDashboard(self)
        self.current_frame.pack(fill="both", expand=True)

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()


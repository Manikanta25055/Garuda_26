# common.py
import datetime

# Global mode flags
MODE_DND = False         # Disables LED and buzzer alerts
MODE_EMAIL_OFF = False   # Disables email notifications
MODE_IDLE = False        # Disables all alerts (LED, buzzer, email)
MODE_NIGHT = False       # High priority mode: alerts last 10 sec instead of 5

# Log file names
SCISSORS_LOG_FILE = "danger_sightings.txt"
NIGHT_MODE_LOG_FILE = "night_mode_findings.txt"

# Global log lists
system_updates_log = []    # For system events and updates
voice_assistant_log = []   # For Narada’s log messages (e.g. “Listening…”, recognized phrases)
voice_responses = []       # For Narada’s response messages

def log_system_update(message):
    """Append a message to the system updates log with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_updates_log.append(f"[{timestamp}] {message}")

def append_voice_log(message):
    """Append a message to the Narada log with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    voice_assistant_log.append(f"[{timestamp}] {message}")

def append_voice_response(message):
    """Append a message to the Narada responses with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    voice_responses.append(f"[{timestamp}] {message}")

def update_text_widget(widget, new_text):
    """
    Update a Tkinter Text widget with new_text while preserving its scroll position.
    """
    try:
        current_view = widget.yview()
    except Exception:
        current_view = (0.0, 1.0)
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("end", new_text)
    if current_view[1] >= 0.99:
        widget.see("end")
    else:
        widget.yview_moveto(current_view[0])
    widget.configure(state="disabled")


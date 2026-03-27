import logging
import tkinter as tk
from config.config import config

logger = logging.getLogger(__name__)

status_var = None
status_label = None # Added global status label
waveform_timer = None

def initialize_status_var(main_frame):
    global status_var, status_label
    status_var = tk.StringVar()
    status_label = tk.Label(
        main_frame,
        textvariable=status_var,
        fg='white',
        bg='#0E1118',
        font=('Helvetica', 13),
        wraplength=200,
        justify='center'
    )
    status_label.grid(row=1, column=0, sticky='nsew', padx=10, pady=5)
    main_frame.grid_rowconfigure(1, weight=1)  # Allow the status label to expand vertically
    status_var.set("Press record to start recording.✨")

def update_status(message):
    """Updates the status bar with the given message."""
    global status_var, status_label
    if status_var is None or not isinstance(status_var, tk.StringVar):
        logger.debug("Re-initializing status_var.")
        if config.root: # Added condition if config.root is initialized, then do the below
            if config.root.winfo_exists(): # Verify the root exists
                initialize_status_var(config.main_frame) # Pass main_frame as argument
            else:
                logger.error("config.root is not active. Unable to update status.")
                return
        else:
            logger.info("[status] %s", message)
            return # Exit if config.root is not initialized (web mode)

    if status_var is not None:
        status_var.set(message)
    else:
        logger.info("Status update: %s", message)  # Fallback if status_var is not initialized

    if config.root:
        if config.root.winfo_exists():
            config.root.update() # Force GUI to update immediately


def simulate_waveform(canvas):
    canvas.delete("waveform")  # Clear existing waveform
    canvas_width = canvas.winfo_width()
    canvas_height = canvas.winfo_height()

    if canvas_width <= 1:
        canvas.after(100, lambda: simulate_waveform(canvas))
        return

    start_x = canvas_width // 8
    end_x = canvas_width * 7 // 8
    waveform_width = end_x - start_x
    center_y = canvas_height / 2

    # Lazy import to avoid circular dependency (recorder imports from ui.utils)
    import audio.recorder as recorder
    with recorder._chunk_lock:
        chunk = recorder.latest_audio_chunk

    if chunk is None or len(chunk) == 0:
        canvas.create_line(start_x, center_y, end_x, center_y, fill="yellow", width=1, tags="waveform")
        return

    samples = chunk.flatten()
    num_points = max(1, int(waveform_width / 2))
    step = max(1, len(samples) // num_points)
    amplitudes = [abs(float(samples[i])) for i in range(0, len(samples), step)][:num_points]

    max_val = max(amplitudes) if amplitudes and max(amplitudes) > 0 else 1
    max_height = center_y * 0.9

    for i, amp in enumerate(amplitudes):
        x = start_x + int(i * waveform_width / num_points)
        height = int((amp / max_val) * max_height)
        canvas.create_line(x, int(center_y - height), x, int(center_y + height),
                           fill="yellow", width=1, tags="waveform")

def draw_straight_line(canvas):
    canvas.delete("waveform")  # Clear any existing waveform
    canvas_width = canvas.winfo_width()
    canvas_height = canvas.winfo_height()
    
    if canvas_width > 1:  # Ensure the canvas has been drawn
        # Calculate the center of the canvas
        center_x = canvas_width / 2
        # Draw a line from 1/4 of the width to 3/4 of the width
        start_x = center_x - (canvas_width / 4)
        end_x = center_x + (canvas_width / 4)
        canvas.create_line(start_x, canvas_height/2, end_x, canvas_height/2, fill="yellow", width=1, tags="waveform")
    else:
        # If the canvas hasn't been drawn yet, schedule the drawing for later
        canvas.after(100, lambda: draw_straight_line(canvas))

def start_waveform_simulation(canvas, root):
    global waveform_timer
    simulate_waveform(canvas)
    waveform_timer = root.after(100, start_waveform_simulation, canvas, root)  # Continuously update the waveform

def stop_waveform_simulation(canvas):
    global waveform_timer
    if waveform_timer is not None:
        canvas.after_cancel(waveform_timer)
        waveform_timer = None

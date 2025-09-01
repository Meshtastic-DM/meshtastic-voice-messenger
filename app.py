# app.py
import tkinter as tk
from tkinter_interface import MeshtasticVoiceMessenger

def main():
    root = tk.Tk()
    app = MeshtasticVoiceMessenger(root)
    root.mainloop()

if __name__ == "__main__":
    main()

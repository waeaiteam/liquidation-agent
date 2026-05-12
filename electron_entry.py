"""PyInstaller entrypoint - re-runs app.py with __main__ semantics so the
Flask `if __name__ == "__main__"` block still executes inside the frozen exe.
"""
import runpy

runpy.run_module("app", run_name="__main__")

"""PyInstaller entry shim for halbot tray."""
from tray.tray import main

if __name__ == "__main__":
    raise SystemExit(main())

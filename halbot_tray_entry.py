"""PyInstaller entry shim for halbot tray."""
import sys

if __name__ == "__main__":
    if "--dashboard" in sys.argv:
        from dashboard.app import main
        raise SystemExit(main())
    from tray.tray import main
    raise SystemExit(main())

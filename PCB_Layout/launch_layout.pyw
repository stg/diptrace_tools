"""Desktop launcher with startup errors visible on every platform."""
import sys
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from Shared.platform_helpers import show_startup_error

if __name__ == '__main__':
    try:
        from layout_viewer import main
        main()
    except Exception:
        message = traceback.format_exc()
        Path(__file__).with_suffix('.error.log').write_text(message,encoding='utf-8')
        show_startup_error('PCB Layout startup error',message)

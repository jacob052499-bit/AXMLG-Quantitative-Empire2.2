"""The distributable is always the permanent customer edition."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name('package-local.py')),run_name='__main__')

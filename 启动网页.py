"""使用当前项目解释器启动 Streamlit（等价于 python -m streamlit run app.py）。"""

from pathlib import Path
import subprocess
import sys


project_dir = Path(__file__).resolve().parent
raise SystemExit(
    subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(project_dir / "app.py")],
        cwd=project_dir,
    )
)

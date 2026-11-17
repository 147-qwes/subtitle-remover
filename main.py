"""程序入口。

启动图形界面：   python main.py
命令行模式：     python main.py --cli "D:/video" --mode hard --style crop_bottom
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional


def _ensure_std_streams(force: bool = False) -> Optional[str]:
    """窗口版 exe（PyInstaller --windowed）没有控制台，stdout / stderr 是 None。

    命令行模式下若不接管，任何 print 都会直接抛异常。这里改为把输出写进
    exe 同目录的 subtitle-remover-cli.log，跑完看这个文件即可。
    force=True 时（已确认是打包后的 exe）无条件接管，因为这时控制台输出
    反正是不可见的。
    """
    if not force and sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
        log_path = os.path.join(base, "subtitle-remover-cli.log")
        handle = open(log_path, "w", encoding="utf-8", buffering=1)
    except Exception:
        log_path = None

        class _Null:
            def write(self, *_args) -> None:
                pass

            def flush(self) -> None:
                pass

        sys.stdout = _Null()  # type: ignore[assignment]
        sys.stderr = _Null()  # type: ignore[assignment]
        return None
    sys.stdout = handle
    sys.stderr = handle
    return log_path


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in args or "--nogui" in args:
        args = [a for a in args if a not in ("--cli", "--nogui")]
        _ensure_std_streams(force=getattr(sys, "frozen", False))
        from subremover.cli import main as cli_main

        return cli_main(args)

    from subremover.app import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

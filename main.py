"""程序入口。

启动图形界面：   python main.py
命令行模式：     python main.py --cli "D:/video" --mode hard --style crop_bottom
"""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in args or "--nogui" in args:
        args = [a for a in args if a not in ("--cli", "--nogui")]
        from subremover.cli import main as cli_main

        return cli_main(args)

    from subremover.app import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

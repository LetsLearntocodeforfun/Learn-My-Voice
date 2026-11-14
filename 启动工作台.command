#!/bin/bash
# Double-click to start the studio. 双击启动脚本工作台。
cd "$(dirname "$0")" || exit 1

if ! xcode-select -p >/dev/null 2>&1; then
  echo "第一次使用需要安装苹果的命令行工具（免费，几分钟）。"
  echo "请在弹出的窗口里点「安装」，装好后再双击一次本文件。"
  echo "First run needs Apple's Command Line Tools. Click Install in the dialog, then open this file again."
  xcode-select --install
  read -r -p "按回车键关闭 / Press Return to close "
  exit 1
fi

exec /usr/bin/python3 -m studio.server

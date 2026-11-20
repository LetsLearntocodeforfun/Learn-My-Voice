#!/bin/bash
# Double-click to start Learn My Voice. Same as 启动工作台.command.
cd "$(dirname "$0")" || exit 1

if ! xcode-select -p >/dev/null 2>&1; then
  echo "First run needs Apple's Command Line Tools (free, a few minutes)."
  echo "Click Install in the dialog, then open this file again."
  echo "第一次使用需要安装苹果的命令行工具。请在弹出的窗口里点「安装」，装好后再双击一次本文件。"
  xcode-select --install
  read -r -p "Press Return to close / 按回车键关闭 "
  exit 1
fi

exec /usr/bin/python3 -X utf8 -B -E -s -m studio.server "$@"

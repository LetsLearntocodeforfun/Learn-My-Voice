#!/bin/sh
# Start Learn My Voice on Linux, or from a terminal on any Unix:  sh start-learn-my-voice.sh
# 在 Linux 上启动脚本工作台：sh start-learn-my-voice.sh
cd "$(dirname "$0")" || exit 1

for python in python3 python; do
  if command -v "$python" >/dev/null 2>&1 &&
     "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec "$python" -m studio.server "$@"
  fi
done

echo "Python 3.9 or newer is needed and was not found."
echo "Install it with your system's package manager (on Debian or Ubuntu: sudo apt install python3), then run this again."
echo "需要 Python 3.9 或更新的版本，但没有找到。请用系统的软件包管理器安装（Debian、Ubuntu：sudo apt install python3），然后再运行一次。"
exit 1

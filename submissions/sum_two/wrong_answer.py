import sys

# 错因：把加法写成了减法
a, b = map(int, sys.stdin.read().split())
print(a - b)

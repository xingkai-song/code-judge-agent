import sys

a, b = map(int, sys.stdin.read().split())
# 错因：除数为零
print(a // (b - b))

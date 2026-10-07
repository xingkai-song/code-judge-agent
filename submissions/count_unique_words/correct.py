import sys

words = sys.stdin.read().split()
print(len({w.lower() for w in words}))

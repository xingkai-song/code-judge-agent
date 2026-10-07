import sys

# 错因：没有忽略大小写，Hello 与 hello 被算成两个词
words = sys.stdin.read().split()
print(len(set(words)))

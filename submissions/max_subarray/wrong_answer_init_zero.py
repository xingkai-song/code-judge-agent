import sys

# 错因：把答案初始化成 0，全负数时子段和应为负数，这里会输出 0
data = sys.stdin.read().split()
n = int(data[0])
arr = list(map(int, data[1 : 1 + n]))

best = 0
cur = 0
for x in arr:
    cur = max(0, cur + x)
    best = max(best, cur)
print(best)

import sys


def main() -> None:
    data = sys.stdin.read().split()
    n = int(data[0])
    arr = list(map(int, data[1 : 1 + n]))
    best = cur = arr[0]
    for x in arr[1:]:
        cur = max(x, cur + x)
        best = max(best, cur)
    print(best)


main()

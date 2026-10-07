#include <iostream>

int main() {
    // 错因：用 int 存结果，两个 2e9 相加会溢出
    int a = 0, b = 0;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}

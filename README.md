# 编程题诊断 Agent

一个以**判题沙箱为 ground truth** 的编程题诊断 Agent：学生提交代码 → 自动判题 →
定位错因 → 生成**可验证**的讲解 → 推荐针对性练习 → 跟踪掌握度。

核心原则：**所有 ground truth 由验证器提供，不依赖人工标注。**

---

## 当前进度

> 诚实标注：项目还在开发中，目前完成的是判题核心（Day 1–2）。

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 判题核心（语言 / 沙箱 / 比对 / 判定） | ✅ 完成 | 47 个单测通过 |
| 本地沙箱 | ✅ 完成 | 资源限制 + 并发读管道 + 超时杀进程组 |
| Docker 沙箱 | ✅ 完成（待联调） | 已实现，需本机 Docker daemon 运行 |
| 题库导入（HumanEval + MBPP，591 道） | ✅ 完成 | 参考解 100% 通过自检 |
| 变异测试（错误注入，自动造错因标签） | ✅ 完成 | 11 个算子，2987 条带标签样本 |
| 错因诊断 + 讲解生成 | ⬜ 待做 | 第 2 周 |
| 讲解可信性验证（反向注入） | ⬜ 待做 | 第 2 周 |
| 评测集与指标看板 | ⬜ 待做 | 复用 `../data-agent` 的评测平台 |

---

## 快速开始

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"

# 端到端演示：把 submissions/ 下的代码全部判一遍
./.venv/bin/python scripts/demo.py            # 自动选沙箱
./.venv/bin/python scripts/demo.py local      # 强制本地沙箱
./.venv/bin/python scripts/demo.py docker     # 强制 Docker 沙箱

# 单元测试
./.venv/bin/python -m pytest

# 导入公开数据集（首次或想更新题库时执行）
./.venv/bin/python scripts/import_datasets.py

# 基线自检：参考解必须 100% 通过，否则先修 harness
./.venv/bin/python scripts/run_baseline.py --jobs 4

# 生成带错因标签的变异数据集
./.venv/bin/python scripts/mutate.py --jobs 6
```

### 题库规模与自检结果

| 数据集 | 题目 | 用例 | 参考解通过率 | 耗时 |
| --- | --- | --- | --- | --- |
| HumanEval | 164 | 1,187 | **100%** | 3.7s |
| MBPP (sanitized) | 427 | 1,324 | **100%** | 10.4s |
| 合计 | **591** | **2,511** | **100%** | 14s（4 并发） |

参考解全过不是「顺便跑出来的」，而是**判题器正确性的证明**：如果参考解都过不了，
问题一定在 harness（驱动脚本、断言切分、判题逻辑），而不在数据。
这件事在 Day 3 真的发生了两次，见下面的踩坑 6 和 7。

题目清单与参考解分别存放在 `problems/*.jsonl` 和 `solutions/*.jsonl`
（一行一条，避免上千个小文件把目录撑爆）。原始数据集缓存在 `data/raw/`，
可用导入脚本随时重建。

### 变异数据集（错因标签的来源）

错因标签不靠人工标注，而是**构造出来的**：往参考解里注入已知类型的错误，
注入的是什么错，标签就是什么错；再用判题机确认「这样改确实会失败」。

| 算子 | 错因标签 | 候选 | **有效样本** | 等价变异率 |
| --- | --- | --- | --- | --- |
| off_by_one_minus | 差一错误（偏小） | 688 | **586** | 14.8% |
| off_by_one_plus | 差一错误（偏大） | 688 | **576** | 16.3% |
| arithmetic_operator | 算术运算符误用 | 542 | **497** | 8.3% |
| condition_negation | 条件判断取反 | 382 | **378** | 1.1% |
| comparison_operator | 比较符误用 | 376 | **367** | 2.4% |
| argument_order | 参数顺序错误 | 265 | **216** | 18.5% |
| return_value | 返回值错误 | 132 | **120** | 9.1% |
| boundary_condition | 边界条件错误 | 184 | **90** | **51.1%** |
| boolean_operator | 逻辑运算符误用 | 85 | **77** | 9.4% |
| initial_value | 初始值错误 | 81 | **71** | 12.3% |
| loop_control | 循环控制错误 | 20 | **9** | 55.0% |
| **合计** | | **3,443** | **2,987** | **13.2%** |

覆盖 512 道题，判定分布 WA 2511 / RE 418 / TLE 58，全量生成耗时 94 秒。

**等价变异率是个有价值的副产品。** 它指的是「注入错误之后，测试用例仍然全部通过」
的比例——说明这个改动在当前测试下不改变行为，或者测试根本没覆盖到那处分支。
它衡量的是**测试用例对错误的检出能力**。

这里的结果很说明问题：`边界条件错误` 的等价变异率高达 **51.1%**，而
`比较符误用` 只有 2.4%。也就是说，**把 `<` 改成 `<=` 有一半的概率不会被官方测试发现**，
而把 `==` 改成 `!=` 几乎立刻被抓。这正好印证了学界对 HumanEval / MBPP 测试用例
覆盖不足的批评：单点测试多，边界测试少。

演示输出（真实运行结果）：

```
题目                  提交                          语言    判定  用例    编译ms   执行ms
sum_two             correct.py                  python  AC    3/3        0      99
sum_two             correct.cpp                 cpp     AC    3/3      264       6
sum_two             overflow.cpp                cpp     WA    2/3      263       5
sum_two             wrong_answer.py             python  WA    0/3        0     100
sum_two             runtime_error.py            python  RE    0/3        0      99
sum_two             timeout.py                  python  TLE   0/1        0    2023
sum_two             compile_error.cpp           cpp     CE    -        240       0
max_subarray        wrong_answer_init_zero.py   python  WA    3/4        0     133
count_unique_words  case_sensitive.py           python  WA    2/3        0     108
```

注意 `overflow.cpp` 是 **2/3 通过**——它只在「结果超过 32 位 int」那条边界用例上
挂了。判题器默认不提前退出，把每条用例的结果全留下来，这正是诊断层需要的输入。

---

## 架构

```
                    ┌──────────────────────────────┐
                    │        CodeVerifier          │  判题器：组装下面几层
                    └───────┬──────────────┬───────┘
                            │              │
              ┌─────────────▼───┐     ┌────▼─────────────┐
              │   languages     │     │     compare      │
              │ 源码名/编译/运行 │     │ exact/token/float│
              └─────────┬───────┘     └──────────────────┘
                        │
              ┌─────────▼──────────────────────────┐
              │            Sandbox                 │  同一接口，两种实现
              ├────────────────┬───────────────────┤
              │  LocalSandbox  │  DockerSandbox    │
              │  子进程+rlimit │  容器+cgroup      │
              └────────────────┴───────────────────┘
                        │
              ┌─────────▼───────────┐
              │  _proc.run_process  │  并发读管道 / 看门狗 / rusage
              └─────────────────────┘
```

分层原则：**沙箱只负责「安全地跑一个命令，如实报回资源消耗」，不做任何对错判断**；
判定逻辑全部在 verifier。换沙箱不影响判题语义，加语言不用改判题器。

### 两种沙箱的能力差异

| 能力 | LocalSandbox | DockerSandbox |
| --- | --- | --- |
| 墙钟超时 | ✅ 看门狗 + 杀进程组 | ✅ 同左，并额外 `docker kill` 容器 |
| CPU 时间限制 | ✅ `RLIMIT_CPU` | ✅ `--cpus` + `RLIMIT_CPU` |
| 内存上限 | ❌ macOS 上做不到（见踩坑 2） | ✅ `--memory`，cgroup 强制 |
| 网络隔离 | ❌ | ✅ `--network none` |
| fork 炸弹防护 | ❌ | ✅ `--pids-limit`（按 cgroup） |
| 峰值内存上报 | ✅ `wait4` 精确到该子进程 | ✅ |
| 适用场景 | 开发、单测、跑可信代码 | 跑不可信代码 |

---

## 踩坑记录（面试可直接讲）

这八个问题都是实测撞出来的，现象、原因、修法都有。

### 1. 先 `wait()` 再读输出 → 整个判题卡死

**现象**：跑一个会打印大量内容的提交，判题进程直接挂住不动。

**原因**：管道的缓冲区只有 64KB。子进程写满之后阻塞在 `write` 上，等着有人读；
而父进程在 `os.wait4` 上等子进程退出。双方互等，死锁。

**修法**：开**两个读线程**并发消费 stdout/stderr，并且读取时就带上限，
超过上限的部分丢弃并标记 `output_truncated`——既解决死锁，也顺手实现了输出超限判定。

### 2. macOS 上 `setrlimit(RLIMIT_AS)` 无法限制内存

**现象**：想给子进程设 256MB 内存上限，结果 `preexec_fn` 直接抛异常，
连 `python3 -c "print(1)"` 都起不来。

**原因**：macOS 的 `setrlimit(RLIMIT_AS)` 在新上限低于**当前已用虚拟内存**时
返回 `EINVAL`；而 Python 进程的虚拟地址空间本来就远大于这个值。实测把上限设到
4GB 依然失败。

**修法**：本地沙箱放弃强制内存上限，只**如实上报峰值**（`wait4` 的 `ru_maxrss`），
并在 `Sandbox.enforces_memory` 里如实声明能力；真正的内存限制交给容器 cgroup。
**结论是「能力要如实声明」，而不是假装限制住了。**

### 3. 用 `RLIMIT_NPROC` 防 fork 炸弹，把 C++ 编译搞挂了

**现象**：加上进程数限制之后，所有 C++ 提交都报编译失败：
`clang++: error: unable to execute command: posix_spawn failed: Resource temporarily unavailable`。
编译耗时只有 14ms——根本没到编译器真正干活就挂了。

**原因**：`RLIMIT_NPROC` 统计的是**整个用户**的进程总数，不是这个进程树。
机器上本来就跑着几百个进程，阈值一设成 128，子进程再 fork 就必然失败，
而 clang 需要派生 cc1、ld 等子进程。

**修法**：本地沙箱去掉 `RLIMIT_NPROC`，防 fork 炸弹改用容器的 `--pids-limit`
（按 cgroup 统计，语义才对）。**「按用户统计」和「按进程树统计」是两种语义，
用错了会误伤。**

### 4. 编译产物用相对路径，检测可执行文件时查错了目录

**现象**：C++ 编译成功（264ms），但运行阶段报 `命令不存在：./main`，判定变成 IE。

**原因**：判断可执行文件是否存在时用了 `os.path.exists("./main")`，
这是相对**父进程当前目录**解析的，而不是沙箱工作目录。

**修法**：相对路径（`./main`、含分隔符的路径）一律相对 `request.workdir` 解析。

### 5. 单位陷阱：`ru_maxrss` 在 macOS 是字节，在 Linux 是 KB

**现象**：一个只打印一行的 Python 程序，内存上报 11714560 "KB"（11GB）。

**原因**：macOS 的 `ru_maxrss` 单位是字节，Linux 是 KB，代码里换算方向写反了。

**修法**：按平台除 1024 或 1，统一成 KB。修正后实测：空跑 ~9MB，
分配 200MB 的程序上报 208MB，符合预期。

### 6. 把 `check()` 拆成多条独立进程，共享状态丢了

**现象**：HumanEval 164 道题里有 4 道参考解跑不过，报
`NameError: name 'rng' is not defined`。

**原因**：一开始为了「精确归因到哪条断言挂了」，我把 `check()` 函数体里的每条
语句拆成独立进程执行。但函数体里的语句是**共享局部变量**的——前面定义了
`rng`，后面的断言才用它，换进程后这个名字就不存在了。

**修法**：改成**一个进程按顺序跑完所有语句、逐条记录结果**，状态自然保留，
顺带把进程数从 7 倍降到 1 倍，单题耗时从 154ms 降到 23ms。
归因精度没有损失——驱动每跑完一条就落盘一次结果。

### 7. 模板里的双花括号：生成的代码把字典写成了集合

**现象**：591 道题的参考解**全部失败**，报 `TypeError: unhashable type: 'dict'`。

**原因**：驱动模板原本用 `str.format` 拼装，字典字面量写了 `{{"k": v}}` 转义；
后来改用 `str.replace` 替换占位符，双花括号就不再被转义了，生成的代码变成
「集合里装一个字典」——字典不可哈希，直接 TypeError。

**修法**：改成单花括号，并补了一个测试 `ast.parse(build_driver(...))`
——**生成代码的正确性不能靠肉眼，只能靠拿去编译一次**。
这个测试在后续改动里立刻拦住过一次同类问题。

### 8. 同一个工具函数用在了两个语义不同的地方

**现象**：变异引擎对简单的单行函数能产出变异体，但对真实的多语句函数
**一道题都产不出来**——3443 个候选变成 0，而且不报任何错，静默全部丢弃。

**原因**：我写了一个 `_unparse()`，里面用 `" ".join(text.split())` 把换行压成空格，
本意是给报告生成「单行摘要」。但它同时被拿去生成**变异体源码**了，于是
```
def f(n):
    total = 0
    return total + n
```
被压成 `def f(n): total = 0 return total + n` —— 这不是合法 Python，
后面的语法检查把每一个变异体都判为无效并静默跳过。
单行函数（`def f(n): return n + 1`）恰好压完仍然合法，所以小测试全过，
真实代码全挂。

**修法**：拆成两个函数——`_render_source()` 保留换行用于生成可执行代码，
`_render_inline()` 压成单行用于展示。并补了一条断言「变异体必须含换行」的测试。
**教训：展示用的格式化和执行用的生成，绝不能共用一个函数。**

---

## 目录结构

```
code-judge-agent/
├── src/code_judge/
│   ├── types.py           判定结果模型（Verdict / TestCase / Problem / RunReport）
│   ├── compare.py         输出比对（精确 / 分词 / 浮点容差）
│   ├── languages.py       语言注册表，加语言不用改判题器
│   ├── problems.py        题目 JSON 读写
│   ├── references.py      参考解存取
│   ├── datasets.py        HumanEval / MBPP 下载与格式转换
│   ├── assert_driver.py   函数补全题的测试驱动生成
│   ├── verifier.py        判题器 + verdict 分类
│   ├── mutation/
│   │   ├── operators.py   11 个变异算子（错误类型定义）
│   │   ├── engine.py      单点变异体生成
│   │   └── dataset.py     带标签样本的存取
│   └── sandbox/
│       ├── base.py        沙箱接口与能力声明
│       ├── _proc.py       公共机制：并发读管道 / 看门狗 / rusage
│       ├── local.py       本地子进程沙箱
│       └── docker.py      容器沙箱
├── problems/samples/      三道示例题
├── submissions/           11 份演示提交，覆盖 AC/WA/TLE/RE/CE
├── scripts/demo.py        端到端演示
├── scripts/import_datasets.py  数据集下载与转换
├── scripts/run_baseline.py     参考解基线自检
├── scripts/mutate.py           变异数据集生成
├── eval/datasets/         2987 条带错因标签的样本
├── problems/              题库（samples/ 三道示例 + 数据集 JSONL）
├── solutions/             参考解，变异测试的输入
└── tests/                 47 个单测
```

---

## 与项目 B（`../data-agent`）的关系

两个项目共用一套「验证器优先」的方法论，但负责的能力不同：

| | 项目 B：`data-agent` | 项目 D：`code-judge-agent`（本仓库） |
| --- | --- | --- |
| 场景 | 数据分析工作流 | 编程题诊断与陪练 |
| 核心验证器 | 结果集精确比对（SQL 执行结果） | 判题沙箱（编译 + 测试用例） |
| ground truth 来源 | 标准 SQL + 合成数据集 | 题目自带测试用例 + 变异测试 |
| 已建成的基础设施 | 评测执行器、LLM-as-judge、OTel 轨迹、幂等键、CI 门禁 | 判题核心（本仓库） |

**复用方式**：本项目的评测集构建完成之后，把错因诊断的评测接入 `data-agent`
已有的评测执行器与报告格式，两个项目共享同一套「跑评测 → 出报告 → CI 门禁」的流程。

---

## 下一步

| 阶段 | 任务 |
| --- | --- |
| ~~Day 3~~ | ~~接入 HumanEval / MBPP~~ ✅ 已完成，591 道题，参考解 100% 通过 |
| ~~Day 4–5~~ | ~~实现变异算子，跑出错因带标签数据~~ ✅ 已完成，11 个算子 / 2987 条样本 |
| Day 6 | **找 3 名真实用户**（刷题/准备的同门），确认真实需求 |
| Day 7 | 跑通「判题 → 错因 → 讲解」最小闭环，定义指标基线 |

Docker 沙箱的实现已经就绪，本机启动 Docker Desktop 后即可用
`./.venv/bin/python scripts/demo.py docker` 验证容器路径。

后续可选：CodeContests（约 1.3 万题，自带测试用例）可以进一步把题库推到万级，
导入逻辑已经抽象好，加一个 `DatasetSpec` 即可。

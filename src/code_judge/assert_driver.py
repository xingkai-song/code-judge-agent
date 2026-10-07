"""函数补全题的测试驱动生成。

HumanEval / MBPP 这类数据集不是标准输入输出型的：提交只需要实现指定函数，
数据集给的是若干条断言。

## 为什么是「一个进程跑完所有断言」而不是「一条断言一个进程」

最初的实现是每条断言单独起一个进程，好处是归因精确。但实测 HumanEval 有 4 道题
直接挂了，报 ``NameError: name 'rng' is not defined``——因为 ``check()`` 函数体里的
语句**共享局部变量**（前面的语句定义了变量，后面的断言才用它），拆成独立进程后
这个状态就丢了。

所以正确做法是：**在同一个进程里按顺序执行所有语句**，逐条记录结果。
这样既保住了共享状态，又比原来少起 N 倍进程。

## 结果怎么传回来

不用 stdout——选手代码可能往 stdout 打印任何东西，会污染结果。
驱动把每条断言的结果**写进一个 JSON 文件**（工作目录下的 ``_assert_results.json``），
每执行完一条就落盘一次，这样即使进程被超时杀掉，之前的结果也能拿到。

## 级联失败

第一条断言挂了之后，后面的语句往往也会跟着挂（变量没定义、状态被改坏）。
这类失败信息量很低，所以驱动会给它们打上 ``cascaded`` 标记，
下游统计错因时可以把它们过滤掉，避免把一道题的错因算成十几个。
"""

from __future__ import annotations

from dataclasses import dataclass

RESULT_FILENAME = "_assert_results.json"

EXIT_MISSING_ENTRY = 44
EXIT_IMPORT_FAILED = 45


@dataclass(frozen=True)
class CaseOutcome:
    """一条断言在驱动里的执行结果。"""

    case_id: str
    status: str  # pass / assert / error
    message: str = ""
    cascaded: bool = False


_TEMPLATE = '''\
"""自动生成的测试驱动，请勿手工修改。"""
import importlib.util as _iu
import json as _json
import sys as _sys
import traceback as _tb

_SOURCE = __SOURCE__
_ENTRY = __ENTRY__
_RESULT_PATH = __RESULT_PATH__

_CASE_RESULTS = []
_FAILED = False


def _emit():
    try:
        with open(_RESULT_PATH, "w", encoding="utf-8") as _fh:
            _json.dump(_CASE_RESULTS, _fh, ensure_ascii=False)
    except OSError:
        pass


def _fill_all(status, message):
    for _cid in __ALL_CASE_IDS__:
        _CASE_RESULTS.append(
            {"case_id": _cid, "status": status, "message": message, "cascaded": False}
        )
    _emit()


_spec = _iu.spec_from_file_location("_solution", _SOURCE)
_mod = _iu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_mod)
except BaseException:
    _tb.print_exc()
    _fill_all("error", "导入解答失败")
    _sys.exit(__EXIT_IMPORT_FAILED__)

# 把解答里定义的名字注入本模块：MBPP 的断言直接按函数名调用，不注入会报 NameError
globals().update({_k: _v for _k, _v in vars(_mod).items() if not _k.startswith("__")})

try:
    candidate = getattr(_mod, _ENTRY)
except AttributeError:
    _fill_all("error", "找不到入口函数：__ENTRY_TEXT__")
    _sys.exit(__EXIT_MISSING_ENTRY__)

__CASE_BLOCKS__

_emit()
'''

_CASE_BLOCK = '''
_prev_failed = _FAILED
try:
{body}
    _CASE_RESULTS.append({"case_id": "{case_id}", "status": "pass", "message": "", "cascaded": _prev_failed})
except AssertionError as _exc:
    _FAILED = True
    _CASE_RESULTS.append({"case_id": "{case_id}", "status": "assert", "message": str(_exc)[:300], "cascaded": _prev_failed})
except BaseException as _exc:
    _FAILED = True
    _CASE_RESULTS.append(
        {"case_id": "{case_id}", "status": "error",
         "message": ("%s: %s" % (type(_exc).__name__, _exc))[:300], "cascaded": _prev_failed}
    )
_emit()
'''


def build_driver(
    source_name: str,
    entry_point: str,
    case_sources: list[tuple[str, str]],
    result_path: str = RESULT_FILENAME,
) -> str:
    """生成驱动脚本。

    :param source_name: 提交源文件名（相对路径，相对运行目录解析，
                        本地沙箱和容器沙箱因此可以共用同一个脚本）
    :param entry_point: 要求的入口函数名
    :param case_sources: ``[(case_id, 该用例的语句源码)]``
    :param result_path: 结果 JSON 的落盘路径
    """
    case_ids = [cid for cid, _ in case_sources]
    blocks = "".join(
        _CASE_BLOCK.replace("{case_id}", cid).replace("{body}", _indent(src, 4))
        for cid, src in case_sources
    )
    return (
        _TEMPLATE.replace("__SOURCE__", repr(source_name))
        .replace("__ENTRY_TEXT__", entry_point)
        .replace("__ENTRY__", repr(entry_point))
        .replace("__RESULT_PATH__", repr(result_path))
        .replace("__ALL_CASE_IDS__", repr(case_ids))
        .replace("__EXIT_MISSING_ENTRY__", str(EXIT_MISSING_ENTRY))
        .replace("__EXIT_IMPORT_FAILED__", str(EXIT_IMPORT_FAILED))
        .replace("__CASE_BLOCKS__", blocks.rstrip("\n"))
    )


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    lines = text.strip("\n").splitlines() or ["pass"]
    return "\n".join(pad + line if line.strip() else "" for line in lines)

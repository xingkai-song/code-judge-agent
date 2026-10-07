"""判题器：把语言配置、沙箱执行、输出比对组装成一次完整判定。

判定优先级（和主流评测机一致）：

    编译失败 CE  →  运行异常 RE / 超时 TLE / 内存 MLE / 输出超限 OLE  →  答案错误 WA  →  通过 AC

多条用例的汇总取severity 最高的一条；默认**不提前退出**，把每条用例的结果都
留下来——诊断层需要知道「到底哪几条挂了、错在哪」，这比早点结束有价值。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Protocol

from code_judge.compare import compare
from code_judge.languages import Language, get_language
from code_judge.sandbox.base import ExecLimits, ExecRequest, ExecResult, Sandbox
from code_judge.sandbox.docker import DockerSandbox
from code_judge.sandbox.local import LocalSandbox
import json

from code_judge.assert_driver import RESULT_FILENAME, build_driver
from code_judge.types import CaseResult, Problem, ProblemKind, RunReport, Verdict

DEFAULT_WORKDIR_ROOT = "/tmp/code_judge_work"

#: 被认为是「内存超限」的退出信号
_OOM_SIGNALS = {"SIGKILL", "SIGSEGV", "SIGABRT", "SIGBUS"}
#: 被认为是「超时」的退出信号
_TLE_SIGNALS = {"SIGXCPU", "SIGALRM"}

#: 命中这些判定就提前结束剩余用例。
#: WA 和 RE 不在其中——诊断层需要看到「哪几条挂了、分别错在哪」的完整模式；
#: 而 TLE / MLE / OLE 属于资源级问题，后面的用例只会重复同样的结果，
#: 继续跑纯粹是浪费时间（一个 2 秒的超时提交会被放大三倍）。
_EARLY_STOP_VERDICTS = {Verdict.TLE, Verdict.MLE, Verdict.OLE, Verdict.IE}

#: 只有 Python 的提交支持函数补全题（HumanEval / MBPP 都是 Python）
_ASSERT_LANGUAGES = {"python"}


class Verifier(Protocol):
    """判题器接口。后续的 MutationVerifier / ClaimVerifier 实现同一套签名。"""

    name: str

    def verify(self, source: str, problem: Problem, language: str = "python") -> RunReport:
        ...


class CodeVerifier:
    """用题目自带测试用例判定一份提交。"""

    name = "code"

    def __init__(
        self,
        sandbox: Sandbox | None = None,
        workdir_root: str = DEFAULT_WORKDIR_ROOT,
        keep_workdir: bool = False,
        stop_on_first_failure: bool = False,
        compile_time_ms: int = 20_000,
    ) -> None:
        self.sandbox = sandbox or LocalSandbox()
        self.workdir_root = workdir_root
        self.keep_workdir = keep_workdir
        self.stop_on_first_failure = stop_on_first_failure
        self.compile_time_ms = compile_time_ms
        self._docker_cache: dict[str, DockerSandbox] = {}

    # -- 对外入口 -------------------------------------------------------------

    def verify(self, source: str, problem: Problem, language: str = "python") -> RunReport:
        lang = get_language(language)
        if problem.kind is ProblemKind.ASSERT and lang.key not in _ASSERT_LANGUAGES:
            raise ValueError(
                f"函数补全题 {problem.id} 目前只支持 {'/'.join(sorted(_ASSERT_LANGUAGES))}，"
                f"收到 {lang.key}"
            )
        sandbox = self._sandbox_for(lang)

        Path(self.workdir_root).mkdir(parents=True, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix=f"{problem.id}-", dir=self.workdir_root)
        try:
            (Path(workdir) / lang.source_name).write_text(source, encoding="utf-8")
            report = self._run_all(sandbox, lang, problem, workdir)
            report.sandbox = sandbox.name
            return report
        finally:
            if not self.keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)
            else:
                pass

    def verify_file(self, path: str | Path, problem: Problem, language: str = "python") -> RunReport:
        return self.verify(Path(path).read_text(encoding="utf-8"), problem, language)

    # -- 内部实现 -------------------------------------------------------------

    def _sandbox_for(self, lang: Language) -> Sandbox:
        """Docker 沙箱要按语言换镜像，本地沙箱直接复用。"""
        if not isinstance(self.sandbox, DockerSandbox):
            return self.sandbox
        image = lang.docker_image or self.sandbox.image
        if image == self.sandbox.image:
            return self.sandbox
        if image not in self._docker_cache:
            self._docker_cache[image] = DockerSandbox(
                image=image, docker_bin=self.sandbox.docker_bin
            )
        return self._docker_cache[image]

    def _run_all(
        self, sandbox: Sandbox, lang: Language, problem: Problem, workdir: str
    ) -> RunReport:
        report = RunReport(verdict=Verdict.IE, language=lang.key, workdir=workdir)

        if lang.needs_compile:
            compile_result = self._compile(sandbox, lang, problem, workdir)
            report.compile_ms = compile_result.time_ms
            if compile_result.exit_code != 0 or compile_result.timed_out:
                report.verdict = Verdict.CE
                report.compile_error = (
                    compile_result.stderr or compile_result.stdout or compile_result.error
                )
                return report

        limits = ExecLimits(
            time_ms=problem.time_limit_ms,
            memory_mb=problem.memory_limit_mb,
        )

        if problem.kind is ProblemKind.ASSERT:
            # 函数补全题的所有断言在同一个进程里顺序执行（见 assert_driver 的说明），
            # 所以整题只跑一次，结果从驱动落盘的 JSON 里读。
            report.cases.extend(
                self._run_assert_problem(sandbox, lang, problem, workdir, limits)
            )
        else:
            for case in problem.cases:
                result = sandbox.exec(
                    ExecRequest(
                        argv=lang.run_cmd,
                        stdin=case.stdin,
                        workdir=workdir,
                        limits=limits,
                        label=case.id,
                    )
                )
                case_result = self._judge_case(case, result, problem, limits)
                report.cases.append(case_result)
                if self.stop_on_first_failure and not case_result.verdict.is_accepted:
                    break
                if case_result.verdict in _EARLY_STOP_VERDICTS:
                    break

        report.verdict = self._overall_verdict(report.cases)
        return report

    def _run_assert_problem(
        self, sandbox: Sandbox, lang: Language, problem: Problem, workdir: str, limits: ExecLimits
    ) -> list[CaseResult]:
        driver_name = "driver.py"
        driver = build_driver(
            lang.source_name,
            problem.entry_point,
            [(c.id, c.assert_code) for c in problem.cases],
            RESULT_FILENAME,
        )
        (Path(workdir) / driver_name).write_text(driver, encoding="utf-8")

        result = sandbox.exec(
            ExecRequest(
                argv=("python3", driver_name),
                stdin="",
                workdir=workdir,
                limits=limits,
                label=f"assert:{problem.id}",
            )
        )
        recorded = _read_assert_results(Path(workdir) / RESULT_FILENAME)
        return _merge_assert_results(problem, result, limits, recorded)

    def _compile(
        self, sandbox: Sandbox, lang: Language, problem: Problem, workdir: str
    ) -> ExecResult:
        assert lang.compile_cmd is not None
        return sandbox.exec(
            ExecRequest(
                argv=lang.compile_cmd,
                stdin="",
                workdir=workdir,
                limits=ExecLimits(
                    time_ms=self.compile_time_ms,
                    memory_mb=max(problem.memory_limit_mb, 512),
                    max_output_bytes=256_000,
                ),
                label="compile",
            )
        )

    def _judge_case(
        self, case, result: ExecResult, problem: Problem, limits: ExecLimits
    ) -> CaseResult:
        """标准输入输出型题目：先看执行是否异常，再比答案。"""
        verdict, detail = _classify(result, limits)
        if verdict is Verdict.AC:
            ok, why = compare(
                result.stdout,
                case.expected_stdout,
                checker=problem.checker,
                tolerance=problem.float_tolerance,
            )
            if not ok:
                verdict, detail = Verdict.WA, why
        return CaseResult(
            case_id=case.id,
            verdict=verdict,
            expected_stdout=case.expected_stdout,
            stdout=result.stdout,
            stderr=result.stderr,
            time_ms=result.time_ms,
            cpu_ms=result.cpu_ms,
            memory_kb=result.memory_kb,
            exit_code=result.exit_code,
            signal_name=result.signal_name,
            detail=detail,
        )

    @staticmethod
    def _overall_verdict(cases: list[CaseResult]) -> Verdict:
        if not cases:
            return Verdict.IE
        return max((c.verdict for c in cases), key=lambda v: v.rank)


def _classify(result: ExecResult, limits: ExecLimits) -> tuple[Verdict, str]:
    """把一个执行结果翻译成判定，AC 表示「没有异常，可以去比答案了」。"""
    if result.error:
        return Verdict.IE, result.error
    if result.timed_out or result.signal_name in _TLE_SIGNALS:
        return Verdict.TLE, f"超出时间限制 {limits.time_ms} ms"
    if result.output_truncated:
        return Verdict.OLE, f"输出超过 {limits.max_output_bytes} 字节"

    near_memory_ceiling = result.memory_kb >= limits.memory_mb * 1024 * 0.9
    if result.signal_name in _OOM_SIGNALS and near_memory_ceiling:
        return Verdict.MLE, f"内存超限（峰值 {result.memory_kb} KB）"
    if "MemoryError" in result.stderr or "OutOfMemory" in result.stderr:
        return Verdict.MLE, f"内存超限（峰值 {result.memory_kb} KB）"
    if result.signal_name:
        return Verdict.RE, f"异常退出：{result.signal_name}"
    if result.exit_code not in (0, None):
        tail = _stderr_tail(result.stderr)
        detail = f"非零退出码 {result.exit_code}"
        if tail:
            detail += f"：{tail}"
        return Verdict.RE, detail
    return Verdict.AC, ""


def _read_assert_results(path: Path) -> dict[str, dict]:
    """读取驱动落盘的断言结果。文件不存在或损坏时返回空字典，
    交给调用方用执行结果（超时 / 崩溃）兜底。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict] = {}
    for item in raw:
        if isinstance(item, dict) and "case_id" in item:
            out[str(item["case_id"])] = item
    return out


def _merge_assert_results(
    problem: Problem,
    result: ExecResult,
    limits: ExecLimits,
    recorded: dict[str, dict],
) -> list[CaseResult]:
    """把「驱动记录到的断言结果」和「进程层面的执行结果」合成最终判定。

    驱动是被超时杀掉时，后面的断言还没来得及跑，这些用例就用进程级判定兜底
    （超时就是 TLE），而已落盘的断言结果仍然保留——这样超时也不会丢掉诊断信息。
    """
    fallback, fallback_detail = _classify(result, limits)
    if not recorded and fallback is Verdict.AC:
        # 进程正常退出却没产出结果，说明驱动本身有问题，不能算选手通过
        fallback, fallback_detail = Verdict.IE, "驱动未产出断言结果"

    cases: list[CaseResult] = []
    for case in problem.cases:
        record = recorded.get(case.id)
        if record is None:
            cases.append(
                CaseResult(
                    case_id=case.id,
                    verdict=fallback,
                    detail=fallback_detail,
                    time_ms=result.time_ms,
                    memory_kb=result.memory_kb,
                    exit_code=result.exit_code,
                    signal_name=result.signal_name,
                )
            )
            continue
        cases.append(
            CaseResult(
                case_id=case.id,
                verdict=_assert_status_to_verdict(record.get("status", "")),
                detail=_assert_detail(record),
                stderr=result.stderr if record.get("status") != "pass" else "",
                time_ms=result.time_ms,
                memory_kb=result.memory_kb,
                exit_code=result.exit_code,
                signal_name=result.signal_name,
                cascaded=bool(record.get("cascaded", False)),
            )
        )
    return cases


def _assert_status_to_verdict(status: str) -> Verdict:
    if status == "pass":
        return Verdict.AC
    if status == "assert":
        return Verdict.WA
    return Verdict.RE


def _assert_detail(record: dict) -> str:
    status = record.get("status", "")
    message = str(record.get("message", "")).strip()
    if status == "pass":
        return ""
    prefix = "断言失败" if status == "assert" else "运行出错"
    return f"{prefix}：{message}" if message else prefix


def _stderr_tail(stderr: str, limit: int = 160) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-1].strip()
    return tail if len(tail) <= limit else tail[:limit] + "…"

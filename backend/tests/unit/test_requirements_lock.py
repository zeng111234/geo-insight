"""部署锁文件的一致性测试。

守的是"服务器照 requirements.lock.txt 装完，能跑起来的东西和本机验证过的是同一套"。
断言来自部署需求，不是从 lock 文件反推：
  - 直接依赖必须齐全（尤其 playwright——漏了它服务器发布直接不可用）；
  - 每行必须是精确 pin（否则不叫 lock）；
  - 每个传递依赖都要在（pip 装齐不是靠运气）；
  - runtime 里不能混进测试依赖；
  - lock 与宽松声明 requirements.txt 不能打架（lock 版本必须满足 >= 约束）。
"""

import re
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

BACKEND = Path(__file__).resolve().parents[2]
LOCK = BACKEND / "requirements.lock.txt"
LOOSE = BACKEND / "requirements.txt"

DIRECT = {"starlette", "uvicorn", "playwright"}
# 这三个直接依赖的传递闭包（Python 3.12 / Linux 实际会装的）
TRANSITIVE = {
    "anyio",
    "typing-extensions",
    "click",
    "h11",
    "pyee",
    "greenlet",
    "idna",
    "colorama",
}
TEST_ONLY = {"pytest", "pytest-cov", "pytest-benchmark", "httpx"}


def _lines(path: Path) -> list[str]:
    return [
        line
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.split("#", 1)[0].strip())
    ]


def pins(path: Path) -> dict[str, str]:
    """宽松解析成 {包名: 版本}，容忍 >= 这类写法——
    精确性由 test_every_line_is_an_exact_pin 单独守。这样改坏一处比较符时，
    只有那一条红，其余断言还能继续给出"哪些依赖齐全"的真实结论。
    """
    result = {}
    for line in _lines(path):
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*(?:==|>=|~=|<=|>|<)\s*([A-Za-z0-9][A-Za-z0-9.*+!-]*)", line)
        if match:
            result[match.group(1).lower().replace("_", "-")] = match.group(2)
    return result


def _parse_pin(line: str) -> dict[str, str]:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*==\s*([A-Za-z0-9][A-Za-z0-9.*+!-]*)", line)
    if not match:
        raise AssertionError(f"lock 里这行不是精确 pin：{line!r}")
    return {match.group(1).lower().replace("_", "-"): match.group(2)}


class RequirementsLockTest(unittest.TestCase):
    def test_direct_dependencies_present(self):
        missing = DIRECT - pins(LOCK).keys()
        self.assertEqual(missing, set(), f"lock 少了直接依赖：{missing}")

    def test_every_line_is_an_exact_pin(self):
        # 逐行调用 _parse_pin，任何 >= / ~= / 裸包名都会抛 AssertionError
        for line in _lines(LOCK):
            _parse_pin(line)

    def test_transitive_closure_present(self):
        missing = TRANSITIVE - pins(LOCK).keys()
        self.assertEqual(missing, set(), f"lock 少了传递依赖，服务器 pip 会漏装：{missing}")

    def test_no_test_dependencies_in_runtime_lock(self):
        overlap = TEST_ONLY & pins(LOCK).keys()
        self.assertEqual(overlap, set(), f"运行时 lock 混进了测试依赖：{overlap}")

    def test_lock_versions_satisfy_loose_declaration(self):
        locked = pins(LOCK)
        for raw in LOOSE.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            req = Requirement(line)
            name = req.name.lower().replace("_", "-")
            self.assertIn(name, locked, f"宽松声明里的 {name} 没被 lock 覆盖")
            self.assertTrue(
                locked[name] in req.specifier,
                f"lock 的 {name}=={locked[name]} 不满足 requirements.txt 的 {req.specifier}",
            )

    def test_locked_versions_are_valid(self):
        for name, version in pins(LOCK).items():
            try:
                Version(version)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{name} 的版本号 {version!r} 不合法：{exc}")


if __name__ == "__main__":
    unittest.main()

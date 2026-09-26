"""Dockerfile 校验：在没有容器运行时的情况下，验证镜像布局是否可用。

背景：本机没有 docker/podman，镜像从未构建过。为了不让 Dockerfile 变成
"写了但没人验证"的摆设，这里做两件事：

  1. 静态校验：解析 Dockerfile 的 COPY 指令，确认每个源路径真实存在；
     确认 ENTRYPOINT/CMD 指向的文件在镜像内可达。
  2. 动态校验：按 COPY 清单把文件复制到临时目录，**在该目录内**用与镜像
     相同的命令跑一次 `main.py --mock`，验证 import 能解析、入口能跑通。

用法：
    python scripts/verify_dockerfile.py

注意：动态校验使用 --mock 与独立配置文件，不需要任何外部凭据。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"

# 镜像内有意义、应当存在的路径（缺失即视为布局缺陷）
REQUIRED_IN_IMAGE = [
    "shared/models.py",
    "collector/github.py",
    "generator/formatter.py",
    "notifier/email.py",
    "main.py",
    # 规范与护栏：书里第 8 章要求护栏在运行时可拦截，镜像内必须带着 specs/
    "specs/proposal.md",
    "specs/design.md",
    "specs/tasks.md",
    "specs/contracts/api-spec.yaml",
    "scripts/input_guard.py",
    "scripts/output_guard.py",
    "CLAUDE.md",
]

COPY_RE = re.compile(r"^COPY\s+(?:--\S+\s+)*(?P<srcs>.+?)\s+(?P<dest>\S+)\s*$")


def parse_copies(text: str) -> list[tuple[list[str], str]]:
    copies: list[tuple[list[str], str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = COPY_RE.match(line)
        if not m:
            continue
        srcs = [s for s in m.group("srcs").split() if not s.startswith("--")]
        copies.append((srcs, m.group("dest")))
    return copies


def check_static() -> list[str]:
    """返回问题列表；空列表表示通过。"""
    problems: list[str] = []
    if not DOCKERFILE.exists():
        return [f"找不到 {DOCKERFILE}"]

    text = DOCKERFILE.read_text(encoding="utf-8")
    copies = parse_copies(text)
    if not copies:
        problems.append("Dockerfile 里没有解析到任何 COPY 指令")

    # 1) COPY 源路径必须真实存在
    for srcs, dest in copies:
        for src in srcs:
            if src in (".", "./"):
                continue
            if not (ROOT / src).exists():
                problems.append(f"COPY 源不存在：{src}（目标 {dest}）")

    # 2) 必需文件必须被 COPY 进镜像
    declared = {s.rstrip("/") for srcs, _ in copies for s in srcs}
    for required in REQUIRED_IN_IMAGE:
        top = required.split("/")[0]
        # 文件被显式 COPY，或其所在目录被整体 COPY
        if required in declared or top in declared:
            continue
        problems.append(f"镜像内缺少必需文件：{required}（既未单独 COPY，也未 COPY 其目录 {top}/）")

    # 3) ENTRYPOINT / CMD 指向的文件必须存在
    entry = re.search(r"^ENTRYPOINT\s+(.*)$", text, flags=re.MULTILINE)
    cmd = re.search(r"^CMD\s+(.*)$", text, flags=re.MULTILINE)
    if entry and "main.py" in entry.group(1) and "main.py" not in declared:
        problems.append("ENTRYPOINT 调用 main.py，但 Dockerfile 没有 COPY main.py")

    if cmd:
        args = re.findall(r'"([^"]+)"', cmd.group(1))
        for flag, expect_next in (("--config", True),):
            if flag in args:
                idx = args.index(flag)
                if expect_next and idx + 1 < len(args):
                    cfg = args[idx + 1]
                    # 镜像内只有 example 或 COPY 进来的配置文件
                    if cfg not in declared and f"{cfg}.example" in declared:
                        # 允许：部署时挂载真实配置；但必须在文件里说明
                        if "VOLUME" not in text and "挂载" not in text:
                            problems.append(
                                f"CMD 使用 {cfg}，但镜像内只有 {cfg}.example 且未声明挂载方式"
                            )
    return problems


def check_dynamic() -> tuple[bool, str]:
    """按 COPY 清单搭建镜像布局并在其中运行 --mock。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    copies = parse_copies(text)
    staging = ROOT / "build_image_check"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    try:
        for srcs, dest in copies:
            for src in srcs:
                source = ROOT / src
                if not source.exists():
                    continue
                rel = Path(dest)
                if str(dest).endswith("/") or source.is_dir():
                    target_dir = staging / rel
                    target_dir.mkdir(parents=True, exist_ok=True)
                    if source.is_dir():
                        for child in source.iterdir():
                            if child.is_dir():
                                shutil.copytree(
                                    child, target_dir / child.name, dirs_exist_ok=True
                                )
                            else:
                                shutil.copy2(child, target_dir / child.name)
                    else:
                        shutil.copy2(source, target_dir / source.name)
                else:
                    (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, staging / rel)

        # 镜像内不含真实 config.yaml（它被 .gitignore 忽略），所以由部署方挂载。
        # 校验时按 example 生成一份最小配置，等价于"挂载了配置文件"。
        example = staging / "config.yaml.example"
        mock_config = staging / "config.mock.yaml"
        if example.exists():
            mock_config.write_text(
                example.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:
            return False, "镜像内没有 config.yaml.example，无法生成校验配置"

        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--config",
                "config.mock.yaml",
                "--date",
                "2026-08-20",
                "--mock",
            ],
            cwd=staging,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + (result.stderr or "")
        ok = result.returncode == 0 and "日报生成完成" in output
        return ok, output.strip().splitlines()[-1] if output.strip() else "(无输出)"
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _force_utf8_console() -> None:
    """Windows 控制台默认 GBK，✓ / ✗ 会触发 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def main() -> int:
    _force_utf8_console()
    print("=" * 66)
    print("Dockerfile 静态校验")
    print("=" * 66)
    problems = check_static()
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
    else:
        print("  ✓ COPY 源全部存在，必需文件齐备，ENTRYPOINT/CMD 可达")

    print()
    print("=" * 66)
    print("镜像布局动态校验（按 COPY 清单搭建后运行 main.py --mock）")
    print("=" * 66)
    ok, info = check_dynamic()
    print(f"  {'✓' if ok else '✗'} {info}")

    if problems or not ok:
        print()
        print("校验未通过。")
        return 1
    print()
    print("校验通过：镜像布局可运行。")
    print("注：本机无容器运行时，未执行真正的 docker build。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

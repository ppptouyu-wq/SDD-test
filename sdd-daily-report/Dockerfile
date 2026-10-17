# 部署环境：公司内网服务器，Docker 容器化部署（proposal.md §2.3 技术约束）
#
# v1.12 改为多阶段构建（specs/design.md §7.2）：
#   阶段 1（builder）只负责把运行期依赖装进 /opt/venv，一个可以整体搬走的目录；
#   阶段 2（runtime）只搬运 venv + 代码 + 规范，不带 pip 缓存、不带构建痕迹。
# 为什么要分阶段：单阶段镜像会把 pip 的下载缓存、编译中间产物一起留在层里，
# 镜像体积白白翻倍，而这些东西运行时一个都用不到。
#
# 镜像里**没有**：`.git`（会让工程进展板块降级为中性空态，见 §7.3）、
# `data/`（本地 SQLite）、`.env` / `config.yaml`（凭据，由部署方挂载）。
# 这些排除规则写在 .dockerignore 里，构建上下文就已经不含它们了。

FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# 只装运行期依赖：dev extra（pytest 等）不进运行镜像
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install \
        "httpx>=0.27" "PyYAML>=6.0" "Jinja2>=3.1"

# ---------------------------------------------------------------------------
# 阶段 2：runtime —— 只带能跑起来的最小集合
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PATH="/opt/venv/bin:${PATH}"

# 非 root：容器内进程不以 uid 0 运行（design.md §7.2、proposal.md §3.1 v1.12）
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

WORKDIR /app

# 依赖整体从 builder 搬过来，不重装、不带构建缓存
COPY --from=builder /opt/venv /opt/venv

# ---------- 代码（采集层 / 生成层 / 推送层 / 共享层 / 编排入口）----------
COPY shared/ ./shared/
COPY collector/ ./collector/
COPY generator/ ./generator/
COPY notifier/ ./notifier/
COPY main.py ./

# ---------- 规范（第一手工件，运行期需要可读）----------
# 镜像内必须带 specs/：第 8 章的入护栏要据此判断"是否越界实现"
# （proposal.md §2.2 的排除项），缺了护栏就形同虚设。
COPY specs/ ./specs/

# ---------- 工程化配套：护栏脚本 + AI 上下文锚点 ----------
COPY scripts/ ./scripts/
COPY CLAUDE.md ./
COPY .claude/ ./.claude/

# ---------- 配置模板 ----------
# 镜像内不放真实 config.yaml（含成员映射与环境相关参数，且被 .gitignore 忽略）。
# 部署时挂载：-v /opt/sdd/config.yaml:/app/config.yaml:ro
COPY config.yaml.example ./config.yaml.example

# 日报历史用本地 SQLite（ADR-002），挂载出来以便持久化
VOLUME ["/app/data"]

# 运行期要往 /app/data 写库；COPY 进来的文件属主是 root，统一交给 app，
# 否则非 root 用户第一次写库就会 PermissionError。
RUN mkdir -p /app/data && chown -R app:app /app

USER app

# 镜像自检：四层模块都能 import、入口可达。刻意**不依赖外部配置**
# （镜像里没有 config.yaml，`main.py --check` 需要挂载后才成立），
# 这样健康检查在任何部署形态下都给出同样可信的答案。
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import main, shared.models, collector.github, generator.template, notifier.email"]

# 运行方式：基于 Cron 的定时任务，不需要常驻后台服务（proposal.md §2.3）
# 容器内默认执行一次，由宿主 cron 或 docker run 触发。
# 首次部署先跑健康检查：
#   docker run --rm -e GITHUB_TOKEN -e LARK_APP_ID -e LARK_APP_SECRET -e SMTP_PASSWORD \
#     -v /opt/sdd/config.yaml:/app/config.yaml:ro sdd-daily-report --check
ENTRYPOINT ["python", "main.py"]
CMD ["--config", "config.yaml"]

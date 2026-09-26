# 部署环境：公司内网服务器，Docker 容器化部署（proposal.md §2.3 技术约束）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ---------- 依赖 ----------
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "httpx>=0.27" "PyYAML>=6.0" "Jinja2>=3.1"

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

# 运行方式：基于 Cron 的定时任务，不需要常驻后台服务（proposal.md §2.3）
# 容器内默认执行一次，由宿主 cron 或 docker run 触发。
# 首次部署先跑健康检查：
#   docker run --rm -e GITHUB_TOKEN -e LARK_APP_ID -e LARK_APP_SECRET -e SMTP_PASSWORD \
#     -v /opt/sdd/config.yaml:/app/config.yaml:ro sdd-daily-report --check
ENTRYPOINT ["python", "main.py"]
CMD ["--config", "config.yaml"]

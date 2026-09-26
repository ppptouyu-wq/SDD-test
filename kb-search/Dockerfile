# 部署：Kubernetes（proposal.md §2.3 技术约束）
# 搜索服务无状态、可水平扩容；向量库（Qdrant）作为独立服务部署。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ---------- 依赖 ----------
COPY pyproject.toml ./
# 只装服务运行所需：核心依赖 + HTTP 服务层 + 真实向量库
RUN pip install --no-cache-dir "httpx>=0.27" "fastapi>=0.110" "uvicorn>=0.29" \
    && pip install --no-cache-dir "qdrant-client>=1.9" || \
    echo "qdrant-client 安装失败，将无法使用真实向量库（本地内存实现仍可用）"

# ---------- 规范（第一手工件，运行期可读）----------
COPY specs/ ./specs/
COPY CLAUDE.md ./

# ---------- 代码 ----------
COPY kb_search/ ./kb_search/

# ---------- 配置模板 ----------
# 真实凭据一律走环境变量，不烧进镜像（design.md §6.2）
COPY .env.example ./.env.example

# 默认以演示模式启动（Mock 飞书 + 本地向量化 + 内存向量库），
# 便于部署后立刻验证服务可用；生产环境传入 --serve 之外的启动参数覆盖。
EXPOSE 8000

# 健康检查：容器编排可据此判断实例是否就绪
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "kb_search.api:create_demo_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

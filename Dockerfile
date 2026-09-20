# 在线 Demo / 自托管的容器镜像。
#
# 这个镜像跑的是**演示模式**（`streamlit_app.py`）：首次启动自动生成一份全部虚构的
# 演示数据，访问者看到完整界面，但不会出现任何人的真实简历。
#
# 用法：
#   docker build -t advisor-fit-demo .
#   docker run --rm -p 8501:8501 advisor-fit-demo
#   浏览器打开 http://localhost:8501
#
# 想跑"真实使用"而不是演示：把 CMD 换成 app.py，并把你的 data/ 与 .env 挂进来，
# 例如：docker run --rm -p 8501:8501 -v "$PWD/data:/app/data" --env-file .env advisor-fit-demo \
#         streamlit run app.py --server.port=8501 --server.address=0.0.0.0

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖单独一层：改代码不会让依赖层失效
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"

CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY survivor ./survivor
COPY config ./config
COPY ["Survivor keeper log 2025.xlsx", "./"]
CMD ["python", "-m", "survivor.catalog"]

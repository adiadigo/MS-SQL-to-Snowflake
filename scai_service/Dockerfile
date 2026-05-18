FROM ubuntu:22.04

ARG SCAI_DOWNLOAD_URL
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fSL "${SCAI_DOWNLOAD_URL}" -o /usr/local/bin/scai \
    && chmod +x /usr/local/bin/scai

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

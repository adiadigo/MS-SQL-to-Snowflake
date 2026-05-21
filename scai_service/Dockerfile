FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/root/.local/bin:/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip curl bash expect \
    libicu70 libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Install scai CLI
RUN curl -fsSL https://snowconvert.snowflake.com/storage/linux/prod/cli/install.sh | bash

# Accept terms using expect to handle the interactive prompt
COPY accept_terms.exp /tmp/accept_terms.exp
RUN expect /tmp/accept_terms.exp

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

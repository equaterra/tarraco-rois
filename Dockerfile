FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENTRYPOINT ["/bin/bash", "-c"]
CMD ["echo 'Run scripts from /app/scripts' && bash"]

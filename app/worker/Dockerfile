FROM python:3.12-slim
WORKDIR /worker
COPY app/worker /worker
RUN pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir .
CMD ["python", "-m", "ai_interviewer_worker.main"]

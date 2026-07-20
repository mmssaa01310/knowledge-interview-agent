FROM python:3.12-slim
WORKDIR /service
COPY app/api /service
RUN pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir --no-build-isolation .
CMD ["uvicorn", "ai_interviewer_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

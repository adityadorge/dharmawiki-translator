FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dwtranslate ./dwtranslate
COPY config.example.yaml ./config.yaml
ENV DWT_CONFIG=/app/config.yaml DWT_STATE_PATH=/tmp/state.sqlite
ENTRYPOINT ["python", "-m", "dwtranslate"]
# default: refresh Hindi drafts for all pages, then publish reviewed ones
CMD ["translate", "--lang", "hi", "--all"]

import json
import logging

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"level": record.levelname, "event": record.getMessage()}, ensure_ascii=False)

def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("legal")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)

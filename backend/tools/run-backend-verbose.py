"""Dev-only: run the API with root logging on so app.* / apscheduler.* INFO
lines actually show up (uvicorn's default config leaves the root logger bare).
"""

import logging

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")

FROM mcr.microsoft.com/devcontainers/python:3.12

# Install system dependencies if any are needed
# RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
#     && apt-get -y install --no-install-recommends <your-package-list-here>

# Install Home Assistant and common testing tools
RUN pip3 install --no-cache-dir \
    homeassistant \
    pytest \
    pytest-homeassistant-custom-component \
    pytest-asyncio \
    pytest-cov \
    ruff \
    aiohttp \
    pytz

# Set the working directory
WORKDIR /workspaces/ha_realtime_trains_api

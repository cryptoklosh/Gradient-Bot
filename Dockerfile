FROM browserless/chrome:latest

USER root
# Install Python and system dependencies
RUN apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
    git

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=True

# Run the bot
ENTRYPOINT ["python3", "bot.py"]

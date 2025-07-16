# Use slim Python image
FROM python:3.10-slim

# Install aria2
RUN apt-get update && \
    apt-get install -y aria2 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create a working directory
WORKDIR /bot

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY . .

# Create download folder
RUN mkdir -p /downloads

# Expose download path as a volume
VOLUME ["/downloads"]

# Run the bot
CMD ["python", "bot.py"]

#!/bin/bash
# Deploy Voice AI Engine on AWS Lightsail (Ubuntu)
# Run this once on a fresh Lightsail instance

set -e

echo "=== Installing system dependencies ==="
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv nginx certbot python3-certbot-nginx

echo "=== Creating app directory ==="
sudo mkdir -p /opt/voice-ai-engine
sudo chown ubuntu:ubuntu /opt/voice-ai-engine

echo "=== Copy files ==="
cp -r . /opt/voice-ai-engine/
cd /opt/voice-ai-engine

echo "=== Creating virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt

echo "=== Setting up environment ==="
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Edit /opt/voice-ai-engine/.env with your API keys before starting!"
fi

echo "=== Creating systemd service ==="
sudo tee /etc/systemd/system/voice-ai.service > /dev/null <<EOF
[Unit]
Description=Voice AI Engine
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/voice-ai-engine
EnvironmentFile=/opt/voice-ai-engine/.env
ExecStart=/opt/voice-ai-engine/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable voice-ai

echo "=== Setting up Nginx reverse proxy ==="
sudo tee /etc/nginx/sites-available/voice-ai > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 3600;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/voice-ai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit /opt/voice-ai-engine/.env with your API keys"
echo "2. sudo systemctl start voice-ai"
echo "3. sudo systemctl status voice-ai"
echo "4. Check logs: journalctl -u voice-ai -f"
echo "5. Point your Bandwidth number webhook to: http://YOUR_IP/bandwidth/incoming"

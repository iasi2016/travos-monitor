#!/bin/bash
set -e

echo "=== Instalare Travos Monitor ==="

sudo apt update
sudo apt install -y python3 python3-venv python3-pip

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Instalare terminata."
echo "Pornire:"
echo "  source venv/bin/activate"
echo "  python main.py"

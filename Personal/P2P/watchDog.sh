#!/bin/bash

while true; do

cd /root/Polynomials/Personal/P2P

git pull

docker compose up -d --build
# 打印时间
echo date: $(date)
sleep 60


done
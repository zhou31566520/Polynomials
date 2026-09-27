#!/bin/bash

while true; do

cd /root/Polynomials/Personal/P2P

git pull

docker compose up -d --build
sleep 60

# 打印时间
echo date: $(date)

done
#!/bin/bash
cd /root/EchoServe
rm -rf web/dist
mkdir -p web
tar xzf /root/echoseve_dist.tar.gz -C web
echo "Uploaded at $(date)"

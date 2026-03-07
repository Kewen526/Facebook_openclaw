#!/bin/bash
# ========================================
# Mihomo (Clash Meta) 代理部署脚本
# 用途：在阿里云 ECS 上安装代理，让 Chrome 能访问 Facebook
# 只监听本地端口，不影响服务器其他程序
# ========================================

set -e

echo "📦 [1/5] 下载 mihomo..."
cd /tmp
curl -L -o mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.0/mihomo-linux-amd64-v1.19.0.gz" --max-time 120
gunzip -f mihomo.gz
chmod +x mihomo
mv mihomo /usr/local/bin/mihomo
echo "✅ mihomo 版本: $(/usr/local/bin/mihomo -v)"

echo "📝 [2/5] 写入配置文件..."
mkdir -p /etc/mihomo
cat > /etc/mihomo/config.yaml << 'CONFIGEOF'
mode: rule
mixed-port: 7897
socks-port: 7898
port: 7899
allow-lan: false
log-level: info
secret: ''
external-controller: 127.0.0.1:9097
geodata-mode: false
geo-auto-update: false
dns:
  enable: true
  use-hosts: true
  use-system-hosts: true
  listen: 0.0.0.0:1053
  ipv6: false
  enhanced-mode: redir-host
  nameserver:
  - 223.5.5.5
  - 119.29.29.29
  proxy-server-nameserver:
  - https://1.12.12.12/dns-query
  - https://223.5.5.5/dns-query
tun:
  enable: false
proxies:
- name: daily-recommend-1-taiwan
  type: trojan
  server: cm.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: daily-recommend-2-hongkong
  type: trojan
  server: zz2.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: daily-recommend-3-hongkong
  type: trojan
  server: cu.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: daily-recommend-4-taiwan
  type: trojan
  server: zz6.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: hongkong
  type: trojan
  server: zz5.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: taiwan
  type: trojan
  server: hinet.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: singapore
  type: trojan
  server: sgp.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: singapore2
  type: trojan
  server: sgp2.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: japan
  type: trojan
  server: softbank.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: japan2
  type: trojan
  server: jp2.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: usa
  type: trojan
  server: usa1.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: usa2
  type: trojan
  server: usa2.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: usa3
  type: trojan
  server: usa3.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: uk
  type: trojan
  server: vnt.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: korea
  type: trojan
  server: sk.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: korea2
  type: trojan
  server: kr2.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
- name: canada
  type: trojan
  server: ca.shso.xyz
  port: 10051
  password: 430c720813c89de5521d51be842a746a
  udp: true
  skip-cert-verify: false
  sni: apis.99912345.xyz
proxy-groups:
- name: Proxy
  type: select
  proxies:
  - daily-recommend-1-taiwan
  - daily-recommend-2-hongkong
  - daily-recommend-3-hongkong
  - daily-recommend-4-taiwan
  - hongkong
  - taiwan
  - singapore
  - singapore2
  - japan
  - japan2
  - usa
  - usa2
  - usa3
  - uk
  - korea
  - korea2
  - canada
rules:
- DOMAIN-SUFFIX,cn,DIRECT
- DOMAIN-KEYWORD,baidu,DIRECT
- DOMAIN-KEYWORD,alibaba,DIRECT
- DOMAIN-KEYWORD,aliyun,DIRECT
- DOMAIN-KEYWORD,taobao,DIRECT
- DOMAIN-KEYWORD,tencent,DIRECT
- DOMAIN-KEYWORD,qq.com,DIRECT
- DOMAIN-KEYWORD,weixin,DIRECT
- DOMAIN-KEYWORD,bilibili,DIRECT
- DOMAIN-SUFFIX,1688.com,DIRECT
- IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
- IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
- IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
- IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
- GEOIP,CN,DIRECT
- MATCH,Proxy
CONFIGEOF
echo "✅ 配置已写入 /etc/mihomo/config.yaml"

echo "🚀 [3/5] 启动 mihomo..."
pkill mihomo 2>/dev/null || true
sleep 1
nohup /usr/local/bin/mihomo -d /etc/mihomo > /var/log/mihomo.log 2>&1 &
sleep 3

echo "🔍 [4/5] 检查启动状态..."
if grep -q "listening" /var/log/mihomo.log; then
    echo "✅ mihomo 启动成功!"
    grep "listening" /var/log/mihomo.log
else
    echo "❌ 启动失败，查看日志:"
    cat /var/log/mihomo.log
    exit 1
fi

echo "🌐 [5/5] 测试代理连接..."
# 测试 HTTP 代理
result=$(curl -x http://127.0.0.1:7899 -o /dev/null -w "%{http_code}" -s https://www.google.com --max-time 15 2>&1)
if [ "$result" = "200" ] || [ "$result" = "301" ] || [ "$result" = "302" ]; then
    echo "✅ 代理测试成功! Google 返回 HTTP $result"
else
    echo "⚠️  Google 返回 HTTP $result，尝试切换节点..."
    # 切换到香港节点
    curl -s -X PUT http://127.0.0.1:9097/proxies/Proxy -d '{"name":"hongkong"}'
    sleep 2
    result2=$(curl -x http://127.0.0.1:7899 -o /dev/null -w "%{http_code}" -s https://www.google.com --max-time 15 2>&1)
    echo "   切换到香港节点后: HTTP $result2"
    if [ "$result2" = "000" ]; then
        echo "⚠️  所有节点都不通，请检查代理账号是否有效"
    fi
fi

echo ""
echo "========================================="
echo "  部署完成！"
echo "  HTTP 代理: http://127.0.0.1:7899"
echo "  SOCKS5 代理: socks5://127.0.0.1:7898"
echo "  混合代理: 127.0.0.1:7897"
echo "  管理 API: http://127.0.0.1:9097"
echo ""
echo "  切换节点示例:"
echo "  curl -X PUT http://127.0.0.1:9097/proxies/Proxy -d '{\"name\":\"usa\"}'"
echo ""
echo "  查看可用节点:"
echo "  curl -s http://127.0.0.1:9097/proxies/Proxy | python3 -m json.tool"
echo ""
echo "  server.py 已配置自动使用 socks5://127.0.0.1:7898"
echo "  如需更改代理地址，设置环境变量: export BROWSER_PROXY=socks5://IP:PORT"
echo "========================================="

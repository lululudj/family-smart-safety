#!/bin/bash
# tello-autolink —— 塔台对 Tello 热点的自动跟随
# 行为（每 10 秒一轮）：
#   1. 扫描发现 TELLO-* 热点 → 若 wlan0 不在 Tello 网段，立即连接
#   2. 已连 Tello 但无人机失联（连续 3 次 ping 不通 192.168.10.1）→ 断开重连
#   3. Tello 热点消失 → 断开死链，回落家里 WiFi
#   4. Tello 和家网都不在 → 保持 wlan0 空闲等待
# 家网兜底由 eth0 承担（塔台主链路走有线），此脚本只管 wlan0 的 Tello 跟随。

POLL=10
GATEWAY=192.168.10.1
HOME_SSID=CU-A696-5G
TELLO_PROFILE=TELLO-5896C7
fail=0

while true; do
    TSSID=$(nmcli -t -f SSID dev wifi list 2>/dev/null | grep '^TELLO-' | head -n1)
    CUR=$(ip -4 addr show wlan0 2>/dev/null | grep -o '192\.168\.10\.[0-9]*' | head -n1)

    if [ -n "$TSSID" ]; then
        # Tello 在场
        if [ -z "$CUR" ]; then
            # wlan0 不在 Tello 网段 → 连接（优先已有配置，没有就新建）
            nmcli con up id "$TELLO_PROFILE" ifname wlan0 >/dev/null 2>&1 || nmcli dev wifi connect "$TSSID" ifname wlan0 >/dev/null 2>&1
            fail=0
        else
            # 已连接，检查无人机是否真的活着
            if ping -c 1 -W 2 "$GATEWAY" >/dev/null 2>&1; then
                fail=0
            else
                fail=$((fail+1))
                if [ "$fail" -ge 3 ]; then
                    # 连续 30 秒失联 → 断开，下一轮重新连接
                    nmcli dev disconnect wlan0 >/dev/null 2>&1
                    fail=0
                fi
            fi
        fi
    else
        # Tello 不在场
        if [ -n "$CUR" ]; then
            # 还挂着 Tello 网段的死 IP → 断开
            nmcli dev disconnect wlan0 >/dev/null 2>&1
        fi
        # wlan0 完全没 IP → 回落家里 WiFi（如有）
        if ! ip -4 addr show wlan0 2>/dev/null | grep -q 'inet '; then
            nmcli con up id "$HOME_SSID" >/dev/null 2>&1
        fi
    fi
    sleep "$POLL"
done



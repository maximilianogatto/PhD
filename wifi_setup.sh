#!/bin/bash
# ifae-ip.sh — activa/desactiva la IP fija 172.16.100.55 en la interfaz Wi-Fi
# Uso: ifae-ip.sh on   -> configura IP fija (modo "DHCP con dirección manual")
#      ifae-ip.sh off  -> vuelve a DHCP automático
#
# Requiere que exista una regla NOPASSWD en sudoers para estos dos comandos
# exactos (ver instrucciones de instalación).
 
SERVICE="Wi-Fi"          # nombre del servicio de red (verificar con: networksetup -listallnetworkservices)
IP="172.16.100.55"
LOG="/tmp/ifae-ip.log"

{ echo "== $(date) =="; whoami; id; echo "HOME=$HOME"; echo "TTY=$(tty 2>&1)"; } >> /tmp/shortcuts-debug.log 2>&1

case "$1" in
  on)
    if sudo -n /usr/sbin/networksetup -setmanualwithdhcprouter "$SERVICE" "$IP"; then
      echo "$(date '+%F %T') - IP fija $IP activada en $SERVICE" >> "$LOG"
    else
      echo "$(date '+%F %T') - ERROR activando IP fija (revisar sudoers)" >> "$LOG"
      exit 1
    fi
    ;;
  off)
    if sudo -n /usr/sbin/networksetup -setdhcp "$SERVICE"; then
      echo "$(date '+%F %T') - IP automática (DHCP) restaurada en $SERVICE" >> "$LOG"
    else
      echo "$(date '+%F %T') - ERROR restaurando DHCP (revisar sudoers)" >> "$LOG"
      exit 1
    fi
    ;;
  *)
    echo "Uso: $0 on|off"
    exit 1
    ;;
esac
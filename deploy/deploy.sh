#!/usr/bin/env bash
# Despliega (o actualiza) la demo pública en un VPS Linux con Docker.
#
#   DOMAIN=demo.tudominio.com ./deploy.sh     # primera vez y cada actualización
#   ./deploy.sh --check                        # solo muestra recursos y requisitos
#   ./deploy.sh --stop                         # detiene la demo
#
# Requisitos: Docker con el plugin compose, puertos 80 y 443 abiertos, y un registro DNS tipo A
# del dominio apuntando a la IP de este servidor.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*"; exit 1; }

check_resources() {
  say "Recursos del servidor"
  ok "CPU: $(nproc) núcleos"
  ok "RAM: $(free -h | awk '/^Mem:/ {print $2 " total, " $7 " disponible"}')"
  ok "Disco libre: $(df -h . | awk 'NR==2 {print $4}')"
  if command -v nvidia-smi >/dev/null 2>&1; then
    ok "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
  else
    ok "GPU: no detectada (la demo liviana no la necesita)"
  fi
  local avail_mb
  avail_mb=$(free -m | awk '/^Mem:/ {print $7}')
  if [ "$avail_mb" -lt 400 ]; then
    warn "Menos de 400 MB de RAM disponible: la demo puede quedarse corta de memoria."
  fi

  say "Requisitos"
  command -v docker >/dev/null 2>&1 || fail "Docker no está instalado. Instálalo con: curl -fsSL https://get.docker.com | sh"
  docker compose version >/dev/null 2>&1 || fail "Falta el plugin 'docker compose'."
  ok "Docker $(docker --version | awk '{print $3}' | tr -d ,) con compose"
  for port in 80 443; do
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$"; then
      if docker ps --format '{{.Names}}' | grep -q '^banking-demo-caddy$'; then
        ok "Puerto ${port}: en uso por la demo (actualización)"
      else
        warn "Puerto ${port} ocupado por otro servicio. Caddy necesita 80 y 443 libres."
        warn "Si ya tienes Nginx u otro proxy, apúntalo a este servidor en el puerto que elijas (ver docs/DEPLOY.md)."
      fi
    fi
  done
}

case "${1:-}" in
  --check) check_resources; exit 0 ;;
  --stop)  say "Deteniendo la demo"; docker compose down; ok "Detenida"; exit 0 ;;
esac

[ -n "${DOMAIN:-}" ] || fail "Indica el dominio: DOMAIN=demo.tudominio.com ./deploy.sh"
check_resources

say "Verificando DNS de ${DOMAIN}"
server_ip=$(curl -fsS4 https://api.ipify.org || true)
dns_ip=$(getent ahostsv4 "${DOMAIN}" | awk 'NR==1 {print $1}' || true)
if [ -n "$server_ip" ] && [ "$server_ip" = "$dns_ip" ]; then
  ok "${DOMAIN} → ${dns_ip} (este servidor)"
else
  warn "${DOMAIN} apunta a '${dns_ip:-nada}' y este servidor es '${server_ip:-desconocido}'."
  warn "Sin el DNS correcto Caddy no podrá obtener el certificado HTTPS (puede tardar unos minutos en propagarse)."
fi

say "Descargando la última imagen"
DOMAIN="$DOMAIN" docker compose pull

say "Levantando demo + Caddy"
DOMAIN="$DOMAIN" docker compose up -d

say "Esperando a que la demo responda"
for _ in $(seq 1 30); do
  if docker exec banking-demo python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')" >/dev/null 2>&1; then
    ok "Demo OK"
    break
  fi
  sleep 2
done

echo
printf '\033[32mDemo publicada: https://%s\033[0m\n' "$DOMAIN"
echo "  La primera visita puede tardar unos segundos mientras Caddy obtiene el certificado."
echo "  Logs:        docker compose logs -f"
echo "  Actualizar:  DOMAIN=${DOMAIN} ./deploy.sh"
echo "  Detener:     ./deploy.sh --stop"

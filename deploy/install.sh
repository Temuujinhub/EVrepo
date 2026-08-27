#!/usr/bin/env bash
# EV Hub — шинэ сервер дээр НЭГ УДААГИЙН суулгац (Ubuntu/Debian).
# WSS сервер (172.16.100.32) дээр root-оор:
#     git clone https://github.com/Temuujinhub/EVrepo.git /opt/evhub-src
#     cd /opt/evhub-src && bash deploy/install.sh
#
# Юу хийдэг:
#   1. Багцууд: python3-venv, postgresql, nginx, git
#   2. evhub системийн хэрэглэгч + /opt/evhub (git working copy)
#   3. Postgres: evhub DB + хэрэглэгч (санамсаргүй нууц үг)
#   4. venv + requirements
#   5. .env (нууц утгууд автоматаар generate)
#   6. systemd: evhub.service + autodeploy timer
#   7. nginx: 8080 → 8100
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Temuujinhub/EVrepo.git}"
APP_DIR=/opt/evhub

if [[ $EUID -ne 0 ]]; then echo "root-оор ажиллуулна уу"; exit 1; fi

echo "── 1/7 багцууд ──"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql nginx git curl

echo "── 2/7 хэрэглэгч + код ──"
id evhub &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin evhub
if [[ ! -d $APP_DIR/.git ]]; then
    # install.sh-ийг clone дотроос ажиллуулсан бол түүнийгээ ашиглана
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [[ -d $SRC_DIR/.git && $SRC_DIR != $APP_DIR ]]; then
        cp -r "$SRC_DIR" $APP_DIR
    else
        git clone "$REPO_URL" $APP_DIR
    fi
fi
cd $APP_DIR
git config --global --add safe.directory $APP_DIR || true

echo "── 3/7 Postgres ──"
DB_PASS="$(openssl rand -hex 16)"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='evhub'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE ROLE evhub LOGIN PASSWORD '$DB_PASS'"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='evhub'" | grep -q 1 || \
    sudo -u postgres createdb -O evhub evhub

echo "── 4/7 venv ──"
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

echo "── 5/7 .env ──"
if [[ ! -f .env ]]; then
    PROV_PASS="$(openssl rand -hex 12)"
    INT_KEY="$(openssl rand -hex 24)"
    ENC_KEY="$(venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    sed -e "s|^EVHUB_DATABASE_URL=.*|EVHUB_DATABASE_URL=postgresql://evhub:$DB_PASS@127.0.0.1:5432/evhub|" \
        -e "s|^EVHUB_PROVISION_PASSWORD=.*|EVHUB_PROVISION_PASSWORD=$PROV_PASS|" \
        -e "s|^EVHUB_INTERNAL_API_KEY=.*|EVHUB_INTERNAL_API_KEY=$INT_KEY|" \
        -e "s|^EVHUB_SECRET_ENC_KEY=.*|EVHUB_SECRET_ENC_KEY=$ENC_KEY|" \
        deploy/env.example > .env
    chmod 600 .env
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║  .env үүслээ — доорх утгуудыг ТЭМДЭГЛЭЖ АВНА УУ:             ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo "  Цэнэглэгчийн provision нууц үг : $PROV_PASS"
    echo "  Internal API түлхүүр (core-д)  : $INT_KEY"
    echo "  (core-ийн .env: PARKING_EVHUB_API_KEY=$INT_KEY)"
    echo "  EVHUB_CORE_URL / EVHUB_CORE_API_KEY-г .env дээр ГАРААР бөглөнө."
    echo ""
else
    echo "  .env аль хэдийн байна — хөндөхгүй"
fi
chown -R evhub:evhub $APP_DIR

echo "── 6/7 systemd ──"
cp deploy/evhub.service /etc/systemd/system/
cp deploy/evhub-autodeploy.service /etc/systemd/system/
cp deploy/evhub-autodeploy.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now evhub
systemctl enable --now evhub-autodeploy.timer

echo "── 7/7 nginx ──"
cp deploy/nginx-evhub.conf /etc/nginx/sites-available/evhub
ln -sf /etc/nginx/sites-available/evhub /etc/nginx/sites-enabled/evhub
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "✅ Дууслаа. Шалгах:"
echo "   curl http://127.0.0.1:8080/healthz"
echo "   systemctl status evhub"
echo "Цэнэглэгчийн HMI-ийн Server url:"
echo "   ws://202.21.117.180:8080/ocpp/1.6/{cp_id}"

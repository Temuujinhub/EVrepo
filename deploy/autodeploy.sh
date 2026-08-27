#!/usr/bin/env bash
# Pull-суурьт autodeploy (PARKING-ийн deploy/autodeploy.sh-ийн загвар).
#
# ЯАГААД pull: энэ сервер NAT-ийн ард (гаднаас SSH орох боломжгүй, гадаад
# 202.21.117.180:8080 нь зөвхөн OCPP порт) тул GitHub Actions push-deploy
# хийж чадахгүй. Оронд нь сервер өөрөө 2 минут тутам GitHub-аас татна.
#
# Гол дүрэм (EV_CHARGING_PLAN.md §4.2): hub restart нь цэнэглэгчийн холболт
# таслах тул ЗӨВХӨН hub/ эсвэл requirements.txt өөрчлөгдсөн үед л restart.
set -euo pipefail

APP_DIR=/opt/evhub
cd $APP_DIR

OLD_REV=$(git rev-parse HEAD)
git fetch origin main --quiet
NEW_REV=$(git rev-parse origin/main)
[[ "$OLD_REV" == "$NEW_REV" ]] && exit 0

echo "autodeploy: $OLD_REV → $NEW_REV"
git reset --hard origin/main --quiet
chown -R evhub:evhub $APP_DIR

CHANGED=$(git diff --name-only "$OLD_REV" "$NEW_REV" || true)

if echo "$CHANGED" | grep -q "^requirements.txt"; then
    venv/bin/pip install -r requirements.txt -q
fi

if echo "$CHANGED" | grep -qE "^(hub/|requirements.txt)"; then
    echo "autodeploy: hub өөрчлөгдсөн — restart"
    systemctl restart evhub
else
    echo "autodeploy: hub хөндөгдөөгүй — restart АЛГАСАВ (цэнэглэгчийн холболт хадгалагдана)"
fi

if echo "$CHANGED" | grep -q "^deploy/nginx-evhub.conf"; then
    cp deploy/nginx-evhub.conf /etc/nginx/sites-available/evhub
    nginx -t && systemctl reload nginx
fi
if echo "$CHANGED" | grep -q "^deploy/evhub.service"; then
    cp deploy/evhub.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl restart evhub
fi

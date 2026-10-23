#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
#  نظام غياب الطلبة (8.iajaward.org) من الهاتف (Termux) — احتياط عند انقطاع الكمبيوتر
#  يعمل بجانب موقع الجائزة (~/iaj) دون تعارض: منفذ 8008 ونفق مستقل «sas-standby»
#
#   bash ~/sas/standby/phone.sh setup        # مرة واحدة: المتطلبات
#   bash ~/sas/standby/phone.sh configure    # مرة واحدة: الرموز (من إعدادات IAJ تلقائياً) + الأزرار SAS
#   bash ~/sas/standby/phone.sh update-code  # آخر نسخة من GitHub (أو من Drive)
#   bash ~/sas/standby/phone.sh takeover     # ★ الطوارئ: أحدث بيانات + تشغيل + تحويل 8.iajaward.org للهاتف
#   bash ~/sas/standby/phone.sh stop         # إنهاء الطوارئ: إرجاع للكمبيوتر أو رفع بيانات الهاتف
#   bash ~/sas/standby/phone.sh server       # إرجاع 8.iajaward.org للكمبيوتر فوراً (بدون نقل بيانات)
#   bash ~/sas/standby/phone.sh status       # أين يعمل الآن
#   bash ~/sas/standby/phone.sh backup       # رفع بيانات الهاتف إلى Drive
#   bash ~/sas/standby/phone.sh try          # تجربة على الهاتف فقط: http://127.0.0.1:8008
#
#  القفل واللغة مشتركان مع أزرار IAJ (IAJ 0 / IAJ 9).
# ================================================================
set -e
SITE="$HOME/sas"
LOCAL_BK="$HOME/sas-backups"
REMOTE="gdrive:IAJ-backups"
[ -f "$HOME/.iaj_standby" ] && . "$HOME/.iaj_standby"
SAS_REMOTE="${SAS_REMOTE:-$REMOTE/sas}"
[ -f "$HOME/.sas_standby" ] && . "$HOME/.sas_standby"
TOKEN_FILE="$HOME/.sas_tunnel_token"
export PYTHONUTF8=1
LANG_UI="${LANG_UI:-ar}"
export IAJ_LANG="$LANG_UI"
T() { if [ "$LANG_UI" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
say() { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*"; exit 1; }
guard() {
  if [ -f "$HOME/.iaj_lock" ] && [ -z "$IAJ_NOGUARD" ]; then
    die "$(T "🔒 الأزرار الحساسة مقفلة — اضغط «IAJ 0 - قفل أو فتح الأزرار» لفتحها" "🔒 Sensitive buttons are locked - press IAJ 0 to unlock")"
  fi
}
need_code() { [ -f "$SITE/serve.py" ] || die "$(T "الكود غير موجود — شغّل: bash phone.sh update-code" "Code not found - run: bash phone.sh update-code")"; }
# إيقاف نفق هذا النظام فقط (لا يلمس نفق موقع الجائزة)
kill_my_tunnel() {
  [ -s "$TOKEN_FILE" ] || return 0
  local key; key=$(head -c 40 "$TOKEN_FILE")
  for p in $(pgrep -x cloudflared 2>/dev/null); do
    tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -qF "$key" && kill "$p" 2>/dev/null || true
  done
}
other_takeover() { pgrep -f "(iaj|rastkt|venues|attendance)/standby/phone.sh takeover" >/dev/null 2>&1; }

ensure_env() {
  if [ ! -f "$SITE/config.env" ]; then
    KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    printf 'SECRET_KEY=%s\nBASE_URL=https://8.iajaward.org\nPORT=8008\nTHREADS=4\nTRUST_PROXY=1\nDISABLE_REMINDERS=1\n' "$KEY" > "$SITE/config.env"
  fi
  # رمز Cloudflare: نفس رمز موقع الجائزة على هذا الهاتف
  if ! grep -q '^CF_API_TOKEN=.' "$SITE/config.env" && grep -q '^CF_API_TOKEN=.' "$HOME/iaj/.env" 2>/dev/null; then
    grep '^CF_API_TOKEN=' "$HOME/iaj/.env" >> "$SITE/config.env"
  fi
  chmod 600 "$SITE/config.env" 2>/dev/null || true
}

case "$1" in
  setup)
    say "$(T "تثبيت المتطلبات" "Installing requirements")"
    pkg install -y python rclone cloudflared unzip >/dev/null
    pkg install -y python-cryptography >/dev/null 2>&1 || true
    pip install -r "$SITE/requirements.txt" tzdata
    say "$(T "تم. الخطوة التالية: bash ~/sas/standby/phone.sh configure" "Done. Next: bash ~/sas/standby/phone.sh configure")"
    ;;

  update-code)
    if [ -s "$HOME/.iaj_github_token" ] && [ -f "$SITE/standby/gh_update.py" ]; then
      say "$(T "تنزيل الكود من GitHub" "Downloading code from GitHub")"
      if python "$SITE/standby/gh_update.py"; then
        say "$(T "تم تحديث الكود" "Code updated")"; exit 0
      fi
      say "$(T "تعذّر GitHub (تأكد أن رمز GitHub يشمل مستودع SAS) — أحاول من Drive" "GitHub failed (make sure the GitHub token includes the SAS repo) - trying Drive")"
    fi
    mkdir -p "$LOCAL_BK/code"
    rclone copy "$SAS_REMOTE/code" "$LOCAL_BK/code" --progress
    [ -f "$LOCAL_BK/code/sas-code.zip" ] || die "$(T "لم أجد sas-code.zip في Drive — شغّل backup_now.bat على الكمبيوتر" "sas-code.zip not in Drive - run backup_now.bat on the PC")"
    unzip -oq "$LOCAL_BK/code/sas-code.zip" -d "$HOME"
    sed -i 's/\r$//' "$SITE"/standby/*.sh 2>/dev/null || true
    say "$(T "تم تحديث الكود من Drive" "Code updated from Drive")"
    ;;

  restore)
    need_code; ensure_env
    say "$(T "تنزيل أحدث بيانات غياب الطلبة من Drive" "Downloading the latest sas data from Drive")"
    mkdir -p "$LOCAL_BK/db"
    if ! rclone lsf "$SAS_REMOTE/db" --include "*.db.gz" 2>/dev/null | grep -q . && ! ls "$LOCAL_BK/db"/*.db.gz >/dev/null 2>&1; then
      echo
      echo "$(T "  ✖ لا توجد أي نسخة احتياطية لنظام غياب الطلبة في Drive ($SAS_REMOTE/db)." "  ✖ There is no sas backup in Drive ($SAS_REMOTE/db).")"
      echo "$(T "    الكمبيوتر لم يرفع نسخة بعد. عند تشغيله: شغّل 3_autostart.bat كمسؤول (نسخة كل ساعة) ثم backup_now.bat" "    The PC has not uploaded one yet. When it is on: run 3_autostart.bat as admin (hourly backup), then backup_now.bat")"
      echo "$(T "    وتأكد أن مجلد النسخ BACKUP_DIR في config.env داخل Google Drive: ...\\IAJ-backups\\sas" "    and that BACKUP_DIR in config.env is inside Google Drive: ...\\IAJ-backups\\sas")"
      die "$(T "لم يتغيّر شيء — الموقع لم يُحوَّل" "Nothing changed - the site was not switched")"
    fi
    rclone copy "$SAS_REMOTE/db" "$LOCAL_BK/db" --include "*.db.gz" --max-age 30d --progress || true
    cd "$SITE" && python ops/restore.py --latest "$LOCAL_BK" --yes
    ;;

  try)
    need_code; ensure_env
    say "$(T "النظام يعمل على الهاتف فقط: http://127.0.0.1:8008 — للإيقاف Ctrl+C" "Running on the phone only: http://127.0.0.1:8008 - Ctrl+C to stop")"
    cd "$SITE" && DISABLE_SCHEDULER=1 python "$SITE/serve.py"
    ;;

  configure)
    need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/config.env" || {
      read -r -s -p "$(T "  الصق رمز Cloudflare API ثم Enter: " "  Paste the Cloudflare API token, then Enter: ")" TK; echo
      [ -n "$TK" ] && echo "CF_API_TOKEN=$TK" >> "$SITE/config.env"; }
    say "$(T "جلب رمز نفق الهاتف sas-standby من Cloudflare (يُحفظ ولا يظهر)" "Fetching the phone tunnel token from Cloudflare (saved, not shown)")"
    (cd "$SITE" && python ops/cf.py token > "$TOKEN_FILE.tmp") && [ -s "$TOKEN_FILE.tmp" ] && mv "$TOKEN_FILE.tmp" "$TOKEN_FILE" && chmod 600 "$TOKEN_FILE" \
      || { rm -f "$TOKEN_FILE.tmp"; die "$(T "تعذّر — شغّل أولاً على الكمبيوتر 1_setup.bat (ينشئ النفق)" "Failed - run 1_setup.bat on the PC first (it creates the tunnel)")"; }
    mkdir -p "$HOME/.shortcuts"
    mk() { printf '#!/data/data/com.termux/files/usr/bin/bash\n%s\necho; read -r -p "Enter ↵" _\n' "$2" > "$HOME/.shortcuts/$1"; chmod +x "$HOME/.shortcuts/$1"; }
    mk "SAS 1 - تشغيل طوارئ غياب الطلبة"  "bash ~/sas/standby/phone.sh takeover"
    mk "SAS 1 - إنهاء طوارئ غياب الطلبة"  "bash ~/sas/standby/phone.sh stop"
    mk "SAS 2 - إرجاع غياب الطلبة للكمبيوتر" "bash ~/sas/standby/phone.sh server"
    mk "SAS 3 - أين يعمل نظام غياب الطلبة" "bash ~/sas/standby/phone.sh status"
    mk "SAS 4 - تحديث كود غياب الطلبة" "bash ~/sas/standby/phone.sh update-code && bash ~/sas/standby/phone.sh configure"
    mk "SAS 5 - نسخ بيانات غياب الطلبة إلى Drive" "bash ~/sas/standby/phone.sh backup"
        say "$(T "تم. حدّث أداة Termux:Widget لتظهر أزرار SAS" "Done. Refresh the Termux:Widget to see the SAS buttons")"
    cd "$SITE" && python ops/cf.py status || true
    ;;

  backup)
    guard; need_code
    cd "$SITE"
    say "$(T "رفع بيانات الهاتف إلى Drive" "Uploading the phone data to Drive")"
    python ops/backup.py --dir "$LOCAL_BK" --tag phone --no-code --no-uploads
    rclone copy "$LOCAL_BK/db" "$SAS_REMOTE/db" --include "*-phone.db.gz" --max-age 1d --progress
    say "$(T "تم. على الكمبيوتر: back_to_server.bat" "Done. On the PC: back_to_server.bat")"
    ;;

  takeover)
    guard; need_code; ensure_env
    grep -q '^CF_API_TOKEN=.' "$SITE/config.env" || die "$(T "شغّل configure أولاً" "Run configure first")"
    [ -s "$TOKEN_FILE" ] || die "$(T "شغّل configure أولاً (رمز النفق)" "Run configure first (tunnel token)")"
    if (cd "$SITE" && python ops/cf.py server-up 2>/dev/null); then
      echo
      echo "$(T "  ✋ الكمبيوتر يعمل ونظام غياب الطلبة سليم — لا حاجة للطوارئ." "  ✋ The PC is up and the sas system is fine - no need for emergency mode.")"
      read -r -p "$(T "  للتشغيل رغم ذلك اكتب FORCE، أو Enter للإلغاء: " "  To run anyway type FORCE, or Enter to cancel: ")" ANS
      [ "$ANS" = "FORCE" ] || die "$(T "تم الإلغاء" "Cancelled")"
    fi
    pkill -f "$SITE/serve.py" 2>/dev/null || true
    kill_my_tunnel; sleep 1
    bash "$0" restore
    cd "$SITE"
    termux-wake-lock 2>/dev/null || true
    say "$(T "تشغيل نظام غياب الطلبة" "Starting the sas system")"
    python "$SITE/serve.py" > "$HOME/sas-serve.log" 2>&1 &
    WEB=$!
    OK=0
    for i in $(seq 1 45); do
      kill -0 $WEB 2>/dev/null || break
      python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8008/',timeout=2)" 2>/dev/null && { OK=1; break; }
      sleep 2
    done
    if [ $OK != 1 ]; then
      kill $WEB 2>/dev/null; tail -20 "$HOME/sas-serve.log"
      die "$(T "لم يعمل على الهاتف — لم يُحوَّل شيء" "Did not start on the phone - nothing was switched")"
    fi
    say "$(T "تشغيل نفق الهاتف" "Starting the phone tunnel")"
    cloudflared tunnel --no-autoupdate run --token "$(cat "$TOKEN_FILE")" > "$HOME/sas-cloudflared.log" 2>&1 &
    CF=$!
    FP0=$(python ops/fingerprint.py 2>/dev/null | tail -1)
    DONE=0
    finish() {
      [ $DONE = 1 ] && return; DONE=1
      kill $WEB $CF 2>/dev/null
      say "$(T "إيقاف نظام غياب الطلبة على الهاتف" "Stopping the sas system on the phone")"
      FP1=$(python ops/fingerprint.py 2>/dev/null | tail -1)
      if [ "$FP0" = "$FP1" ]; then
        if python ops/cf.py server; then
          python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('server',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
          rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$SAS_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
        else
          say "$(T "الكمبيوتر غير متصل — 8.iajaward.org سيبقى متوقفاً حتى يعود" "The PC is offline - 8.iajaward.org stays down until it is back")"
        fi
      else
        IAJ_NOGUARD=1 bash "$0" backup || true
        echo
        echo "$(T "  ⚠ سُجّلت بيانات على الهاتف — على الكمبيوتر شغّل: back_to_server.bat" "  ⚠ Data was recorded on the phone - on the PC run: back_to_server.bat")"
      fi
      other_takeover || termux-wake-unlock 2>/dev/null || true
    }
    trap finish EXIT INT TERM
    say "$(T "تحويل 8.iajaward.org إلى الهاتف" "Switching 8.iajaward.org to the phone")"
    if python ops/cf.py phone; then
      python -c "import sys;sys.path.insert(0,'ops');from common import stamp;print('phone',stamp())" > "$LOCAL_BK/db/ACTIVE_SITE.txt"
      rclone copyto "$LOCAL_BK/db/ACTIVE_SITE.txt" "$SAS_REMOTE/db/ACTIVE_SITE.txt" 2>/dev/null || true
    else
      die "$(T "لم يتم التحويل" "Switch failed")"
    fi
    say "$(T "✅ الهاتف يشغّل 8.iajaward.org — لا تغلق النافذة. للإنهاء: زر «SAS 1 - إنهاء» أو Ctrl+C" "✅ The phone serves 8.iajaward.org - keep this window. To finish: SAS 1 - Stop, or Ctrl+C")"
    while kill -0 $WEB 2>/dev/null && kill -0 $CF 2>/dev/null; do sleep 5; done
    say "$(T "توقف النظام أو النفق على الهاتف" "The system or tunnel stopped on the phone")"
    exit 0
    ;;

  stop)
    PID=$(pgrep -f "sas/standby/phone.sh takeover" | grep -v "^$$\$" | head -1)
    if [ -z "$PID" ]; then
      say "$(T "طوارئ غياب الطلبة ليست قيد التشغيل" "SAS emergency mode is not running")"
    else
      say "$(T "إنهاء الطوارئ… (قد يستغرق دقيقة)" "Stopping emergency mode... (may take a minute)")"
      kill -INT "$PID" 2>/dev/null || true
      for i in $(seq 1 45); do kill -0 "$PID" 2>/dev/null || break; sleep 2; done
    fi
    need_code; cd "$SITE" && python ops/cf.py status 2>/dev/null || true
    ;;

  server)
    guard; need_code
    read -r -p "$(T "  إرجاع 8.iajaward.org للكمبيوتر بدون نقل بيانات — اكتب YES: " "  Move 8.iajaward.org back to the PC without moving data - type YES: ")" OK
    [ "$OK" = "YES" ] || die "$(T "تم الإلغاء" "Cancelled")"
    cd "$SITE" && python ops/cf.py server
    ;;

  status)
    need_code; cd "$SITE" && python ops/cf.py status
    ;;

  *)
    sed -n '2,18p' "$0"
    ;;
esac

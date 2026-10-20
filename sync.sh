#!/bin/sh
set -u

CONFIG_PATH="${CONFIG_PATH:-/config.json}"
LIVE_AUTHORIZE="${LIVE_AUTHORIZE:-/radius-live/mods-config/files/authorize}"
FREERADIUS_CONTAINER="${FREERADIUS_CONTAINER:?FREERADIUS_CONTAINER must be set}"
RESYNC_INTERVAL="${RESYNC_INTERVAL:-3600}"
DEPLOY_DELAY="${DEPLOY_DELAY:-0}"
STAGING_DIR="/staging"
STAGING_AUTHORIZE="$STAGING_DIR/mods-config/files/authorize"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

docker_api() {
    # docker_api METHOD PATH [JSON_BODY]
    curl -sSf --unix-socket /var/run/docker.sock -X "$1" \
        -H 'Content-Type: application/json' ${3:+-d "$3"} "http://localhost$2"
}

reload_freeradius() {
    # SIGHUP makes freeradius re-read the files module (authorize) without
    # stopping the server, which also avoids breaking the keepalived container
    # that shares its network namespace.
    #
    # The signal comes from a kill run inside the container, not from Docker's
    # kill endpoint: Docker marks a container as manually stopped whenever that
    # endpoint signals it, whatever the signal, and from then on it ignores the
    # restart policy, so a crashed or daemon-restarted freeradius stays down.
    exec_id=$(docker_api POST "/containers/$FREERADIUS_CONTAINER/exec" \
        '{"Cmd":["sh","-c","kill -HUP 1"]}' \
        | sed -n 's/.*"Id":"\([0-9a-f]*\)".*/\1/p')
    if [ -n "$exec_id" ] \
        && docker_api POST "/exec/$exec_id/start" '{"Detach":false}' >/dev/null \
        && docker_api GET "/exec/$exec_id/json" | grep -q '"ExitCode":0[,}]'; then
        log "sent SIGHUP to $FREERADIUS_CONTAINER"
    else
        log "ERROR: failed to signal $FREERADIUS_CONTAINER"
        return 1
    fi
}

deploy() {
    if [ ! -s "$STAGING_AUTHORIZE" ]; then
        log "ERROR: scraper produced no authorize file; keeping current config"
        return 1
    fi
    # a generated file with zero MAC entries almost certainly means the scrape
    # went wrong; never deploy it
    if ! grep -q '^[0-9A-F]\{12\}' "$STAGING_AUTHORIZE"; then
        log "ERROR: generated authorize has no MAC entries; keeping current config"
        return 1
    fi
    if cmp -s "$STAGING_AUTHORIZE" "$LIVE_AUTHORIZE"; then
        log "no change"
        return 0
    fi
    # comments embed volatile fields (last_ip); when only comments differ,
    # refresh the live file for human readers but don't reload freeradius
    if [ "$(grep -v '^#' "$STAGING_AUTHORIZE")" = "$(grep -v '^#' "$LIVE_AUTHORIZE" 2>/dev/null)" ]; then
        cp "$STAGING_AUTHORIZE" "$LIVE_AUTHORIZE"
        log "comment-only change; skipping reload"
        return 0
    fi
    # the secondary sets DEPLOY_DELAY so both freeradius instances never
    # reload at the same moment
    if [ "$DEPLOY_DELAY" -gt 0 ]; then
        log "waiting ${DEPLOY_DELAY}s before deploying"
        sleep "$DEPLOY_DELAY"
    fi
    cp "$STAGING_AUTHORIZE" "$LIVE_AUTHORIZE"
    log "deployed updated authorize"
    reload_freeradius
}

sync_once() {
    rm -rf "$STAGING_DIR"
    if ! python /usr/src/app/unifi_freeradius_sync.py -c "$CONFIG_PATH" -o "$STAGING_DIR"; then
        log "ERROR: scraper failed; keeping current config"
        return 1
    fi
    deploy
}

case "${1:-}" in
    # validate and deploy whatever is in staging; used as the watcher's
    # --on-change hook
    --deploy)
        deploy
        exit $?
        ;;
    # manual trigger: docker exec <container> ./sync.sh --once
    --once)
        sync_once
        exit $?
        ;;
esac

# default mode: the scraper holds a websocket to the UniFi controller and
# regenerates staging whenever a client note or network changes (plus a
# RESYNC_INTERVAL fallback), invoking --deploy after each regeneration; if it
# dies, restart it after a delay
while true; do
    rm -rf "$STAGING_DIR"
    python /usr/src/app/unifi_freeradius_sync.py --watch \
        -c "$CONFIG_PATH" -o "$STAGING_DIR" \
        --resync "$RESYNC_INTERVAL" \
        --on-change "/usr/src/app/sync.sh --deploy"
    log "watcher exited; restarting in 30s"
    sleep 30
done

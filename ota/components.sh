# shellcheck shell=bash
# The server components: what each ships (PATHS, read by scripts/deploy.sh) and how it installs (CMD, run as root from
# the root of an extracted release tree with FRIDAY_VERSION_LINE="<describe> <sha>" set). Sourced by scripts/deploy.sh
# and by the VM agent's OTA installer (agents/server/friday_server_agent/ota/server_install.py), so a manual deploy and
# an OTA install run exactly the same thing. FRIDAY_NO_RESTART=1 asks an installer that would restart the voice to
# leave it to the caller.
friday_component() {
  case "$1" in
    server-agent)  PATHS="agents scripts/vault.py vault";          CMD="bash agents/server/install.sh" ;;
    server)        PATHS="server voice_gateway/local_config.py";   CMD="bash server/install.sh" ;;
    voice-gateway) PATHS="voice_gateway scripts/lib";              CMD="bash voice_gateway/install.sh" ;;
    brain-skills)  PATHS="brain";                                  CMD="bash brain/skills/install.sh" ;;
    wwt)           PATHS="tools/wakeword/training tools/wakeword/wwt"; CMD="bash tools/wakeword/wwt/install.sh" ;;
    container)     PATHS="server/image";                           CMD="bash server/image/install.sh" ;;
    *) return 1 ;;
  esac
}

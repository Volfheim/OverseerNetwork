"""Generic observation targets and operation policy. Inventory lives in config."""

DIAGNOSTIC_TARGETS = {
    "openai": ["chatgpt.com", "auth.openai.com", "challenges.cloudflare.com"],
    "google": ["www.google.com", "gemini.google.com"],
    "youtube": ["www.youtube.com", "i.ytimg.com"],
    "russian": ["ya.ru", "ozon.ru"],
}


def services_for(server):
    return list(server.services)


def restart_block_reason(server, item):
    if server.type != "ssh":
        return "local_read_only"
    if server.protected or server.environment == "production":
        return "protected_workload"
    protected = {s.removesuffix(".service") for s in server.protected_targets}
    if item.target.removesuffix(".service") in protected | {
        "ssh", "sshd", "docker", "containerd", "networking", "NetworkManager", "systemd-networkd",
    }:
        return "protected_service"
    if server.control_mode != "operate" or not item.allow_restart:
        return "observe_only"
    if not server.strict_host_key:
        return "host_verification_required"
    return None

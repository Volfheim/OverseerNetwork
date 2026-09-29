import asyncio
import copy
import ipaddress
import json
import math
import re
import socket
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen


def public_ip(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError("Geolocation requires a public IP address")
    return str(address)


def resolve_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host):
            raise ValueError("Expected an IP address or hostname") from None
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        for item in sorted(addresses, key=lambda item: item[0] != socket.AF_INET):
            try:
                return public_ip(item[4][0])
            except ValueError:
                continue
        raise ValueError("Hostname has no public address")
    return public_ip(host)


def lookup_geo(host=None):
    target = resolve_ip(host.strip()) if host and host.strip() else ""
    fields = "success,message,ip,country,city,latitude,longitude,connection.isp,timezone.id"
    request = Request(f"https://ipwho.is/{quote(target, safe='')}?fields={fields}",
                      headers={"User-Agent": "OverseerNetwork/1.2", "Accept": "application/json"})
    with urlopen(request, timeout=8) as response:
        payload = json.loads(response.read(65536))
    if payload.get("success") is not True:
        raise ValueError("IP geolocation provider could not resolve this address")
    address = public_ip(payload.get("ip", ""))
    if target and address != target:
        raise ValueError("Geolocation response does not match the requested IP")
    try:
        lat, lon = float(payload["latitude"]), float(payload["longitude"])
    except (TypeError, KeyError, ValueError):
        raise ValueError("Geolocation response has no valid coordinates") from None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Geolocation coordinates are outside valid bounds")
    return {"host": address, "ip": address, "coordinates": [lat, lon],
            "city": payload.get("city"), "country": payload.get("country"),
            "provider": payload.get("connection", {}).get("isp"),
            "timezone": payload.get("timezone", {}).get("id"), "geo_provider": "ipwho.is"}


class GeoLocator:
    """Runtime observations, separate from inventory and SSH connection addresses."""

    def __init__(self, lookup=None, clock=None):
        self.lookup = lookup or lookup_geo
        self.clock = clock or time.monotonic
        self.cache = {}
        self.locks = {}
        self.semaphore = asyncio.Semaphore(3)

    @staticmethod
    def key(node):
        return (node.type, None if node.type == "local" else node.host)

    @staticmethod
    def ttl(node):
        return 300 if node.type == "local" else 21600

    def network_snapshot(self, node):
        source = "egress-ip" if node.type == "local" else "server-ip"
        if node.type != "local" and not node.auto_geo:
            return {"source": "manual", "status": "ready", "coordinates": list(node.coordinates),
                    "city": node.city, "country": node.country, "provider": node.provider, "checked_at": None}
        entry = self.cache.get(self.key(node))
        if not entry:
            return {"source": source, "status": "pending", "coordinates": None, "checked_at": None}
        result = copy.deepcopy(entry["result"])
        if result["status"] == "ready" and self.clock() - entry["time"] >= self.ttl(node):
            result["status"] = "stale"
        return result

    def snapshot(self, node):
        network = self.network_snapshot(node)
        if node.type != "local":
            return network
        located = node.physical_location is not None
        return {"source": "physical", "status": "ready" if located else "needs-location",
                "coordinates": list(node.physical_location) if located else None,
                "city": node.city if located else None, "country": node.country if located else None,
                "provider": node.provider, "checked_at": None, "network": network}

    async def locate(self, node, refresh=False):
        if node.type != "local" and not node.auto_geo:
            return self.snapshot(node)
        key = self.key(node)
        async with self.locks.setdefault(key, asyncio.Lock()):
            entry = self.cache.get(key)
            if entry:
                age = self.clock() - entry["time"]
                ttl = 60 if refresh or entry["result"]["status"] == "unavailable" else self.ttl(node)
                if age < ttl:
                    return self.snapshot(node)
            source = "egress-ip" if node.type == "local" else "server-ip"
            async with self.semaphore:
                try:
                    observation = await asyncio.wait_for(asyncio.to_thread(self.lookup, key[1]), timeout=12)
                    result = {**observation, "source": source, "status": "ready"}
                except Exception:
                    result = {"source": source, "status": "unavailable", "coordinates": None}
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            self.cache[key] = {"time": self.clock(), "result": result}
            return self.snapshot(node)

    def public_node(self, server_id, node):
        result = node.public_dict(server_id)
        observation = self.snapshot(node)
        result["geo"] = observation
        for key in ("coordinates", "city", "country", "provider"):
            result[key] = observation.get(key)
        return result

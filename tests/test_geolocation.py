import asyncio
import io
import json
import unittest
from unittest.mock import patch

from app.config import ServerConfig
from app.geolocation import GeoLocator, lookup_geo


def response(**extra):
    return io.BytesIO(json.dumps({"success": True, "ip": "8.8.8.8", "latitude": 37.4,
        "longitude": -122.1, "city": "Fixture city", "country": "Fixture country",
        "connection": {"isp": "Fixture ISP"}, "timezone": {"id": "Etc/UTC"}, **extra}).encode())


class LookupTests(unittest.TestCase):
    def test_lookup_uses_https_and_the_servers_address_not_requester(self):
        with patch("app.geolocation.urlopen", return_value=response()) as fetch:
            result = lookup_geo("8.8.8.8")
        self.assertEqual(result["coordinates"], [37.4, -122.1])
        self.assertEqual(result["ip"], "8.8.8.8")
        self.assertTrue(fetch.call_args.args[0].full_url.startswith("https://ipwho.is/8.8.8.8?"))

    def test_pc_uses_own_egress_when_no_host_is_provided(self):
        with patch("app.geolocation.urlopen", return_value=response()) as fetch:
            result = lookup_geo(None)
        self.assertEqual(result["city"], "Fixture city")
        self.assertTrue(fetch.call_args.args[0].full_url.startswith("https://ipwho.is/?"))

    def test_missing_invalid_or_wrong_ip_result_never_becomes_a_map_position(self):
        for data in [{"latitude": None}, {"longitude": 181}, {"latitude": "NaN"},
                     {"success": False}, {"ip": "1.1.1.1"}]:
            with self.subTest(data=data), patch("app.geolocation.urlopen", return_value=response(**data)):
                with self.assertRaises(ValueError):
                    lookup_geo("8.8.8.8")

    def test_private_hosts_are_not_sent_to_a_public_geo_provider(self):
        with patch("app.geolocation.urlopen") as fetch:
            for host in ["127.0.0.1", "192.168.1.2", "::1", "http://localhost/"]:
                with self.subTest(host=host), self.assertRaises(ValueError):
                    lookup_geo(host)
        fetch.assert_not_called()


class LocatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000.0
        self.calls = []
        self.lookup_failed = False
        self.address = "8.8.8.8"
        def query(host):
            self.calls.append(host)
            if self.lookup_failed:
                raise OSError("offline")
            return {"host": host or self.address, "ip": host or self.address, "coordinates": [51.5, -0.12],
                    "city": "London", "country": "United Kingdom", "provider": "Fixture ISP"}
        self.locator = GeoLocator(lookup=query, clock=lambda: self.now)
        self.pc = ServerConfig(name="Remote", type="ssh", host="8.8.8.8", username="tester", auto_geo=True,
                               coordinates=[55.7558, 37.6173], city="Moscow")

    async def test_configured_moscow_is_not_used_while_auto_geo_is_pending(self):
        node = self.locator.public_node("pc", self.pc)
        self.assertIsNone(node["coordinates"])
        self.assertIsNone(node["city"])
        self.assertEqual(node["geo"]["status"], "pending")
        await self.locator.locate(self.pc)
        node = self.locator.public_node("pc", self.pc)
        self.assertEqual(node["coordinates"], [51.5, -0.12])
        self.assertEqual(node["geo"]["source"], "server-ip")
        self.assertEqual(self.pc.city, "Moscow")  # Observation must not rewrite configuration.

    async def test_concurrent_requests_share_lookup_and_pc_refreshes_after_ttl(self):
        self.pc = self.pc.model_copy(update={"type": "local"})
        results = await asyncio.gather(*(self.locator.locate(self.pc) for _ in range(5)))
        self.assertTrue(all(item["network"]["ip"] == "8.8.8.8" for item in results))
        self.assertEqual(self.calls, [None])
        self.address = "1.1.1.1"
        self.now += 301
        self.assertEqual((await self.locator.locate(self.pc))["network"]["ip"], "1.1.1.1")

    async def test_failure_does_not_restore_old_city_or_zero_coordinates(self):
        self.lookup_failed = True
        result = await self.locator.locate(self.pc)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(self.locator.public_node("pc", self.pc)["coordinates"])
        await self.locator.locate(self.pc)
        self.assertEqual(len(self.calls), 1)

    async def test_expired_location_is_marked_stale_until_refresh(self):
        await self.locator.locate(self.pc)
        self.now += 21601
        self.assertEqual(self.locator.public_node("pc", self.pc)["geo"]["status"], "stale")

    async def test_physical_pc_location_does_not_follow_vpn_or_disappear_offline(self):
        pc = ServerConfig(name="PC", type="local", physical_location=[51.5, -0.12], city="London")
        result = await self.locator.locate(pc)
        self.assertEqual(result["source"], "physical")
        self.assertEqual(result["coordinates"], [51.5, -0.12])
        self.assertEqual(result["network"]["ip"], "8.8.8.8")
        self.lookup_failed = True
        self.now += 301
        result = await self.locator.locate(pc)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["coordinates"], [51.5, -0.12])
        self.assertEqual(result["network"]["status"], "unavailable")

    async def test_unconfigured_pc_does_not_use_old_moscow_or_vpn_as_physical_location(self):
        pc = self.pc.model_copy(update={"type": "local"})
        result = await self.locator.locate(pc)
        self.assertEqual(result["status"], "needs-location")
        self.assertIsNone(result["coordinates"])
        self.assertEqual(result["network"]["ip"], "8.8.8.8")

    def test_physical_location_rejects_invalid_coordinates(self):
        for point in [[91, 0], [0, 181], [0], [float("nan"), 0]]:
            with self.subTest(point=point), self.assertRaises(ValueError):
                ServerConfig(name="PC", type="local", physical_location=point)

    async def test_remote_address_change_invalidates_cache(self):
        node = ServerConfig(name="Remote", host="8.8.8.8", username="tester")
        self.assertEqual((await self.locator.locate(node))["source"], "server-ip")
        changed = node.model_copy(update={"host": "1.1.1.1"})
        self.assertIsNone(self.locator.public_node("remote", changed)["coordinates"])
        self.assertEqual((await self.locator.locate(changed))["ip"], "1.1.1.1")

    async def test_manual_coordinates_are_kept_and_do_not_trigger_lookup(self):
        manual = self.pc.model_copy(update={"auto_geo": False})
        result = await self.locator.locate(manual)
        self.assertEqual(result["source"], "manual")
        self.assertEqual(result["coordinates"], [55.7558, 37.6173])
        self.assertEqual(self.calls, [])

    async def test_forced_refresh_has_a_minimum_interval(self):
        await self.locator.locate(self.pc)
        await self.locator.locate(self.pc, refresh=True)
        self.assertEqual(len(self.calls), 1)
        self.now += 61
        await self.locator.locate(self.pc, refresh=True)
        self.assertEqual(len(self.calls), 2)

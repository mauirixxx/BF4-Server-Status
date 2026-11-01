import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timezone
from server_geography import parse_battlelog_geography

A='28773abe-e620-4d36-9512-c6f4b128f0ad'
B='86fd73d2-c37e-45a4-bac2-474b93cf7c56'

class GeographyParserTests(unittest.TestCase):
    def test_server_values(self):
        payload=f'"guid":"{A}","region":1,"pingSite":"iad","country":"us"'
        self.assertEqual(parse_battlelog_geography(payload,A)['country_code'],'us')
        self.assertEqual(parse_battlelog_geography(payload,A)['ping_site'],'iad')
    def test_cross_server_contamination_rejected(self):
        payload=f'"guid":"{A}","foo":1}},"other":{{"guid":"{B}","pingSite":"ams","country":"de"'
        with self.assertRaises(ValueError):parse_battlelog_geography(payload,A)
    def test_missing_guid_rejected(self):
        with self.assertRaises(ValueError):parse_battlelog_geography('"country":"us"',A)
    def test_missing_geo_rejected(self):
        with self.assertRaises(ValueError):parse_battlelog_geography(f'"guid":"{A}"',A)

class EnrichmentTests(unittest.TestCase):
    def test_enrich_one(self):
        from geography_worker import enrich_one
        server=SimpleNamespace(server_guid=A,platform='PC',battlelog_url=None,country_code=None,ping_site=None,region=None,location=None,geo_source=None,geo_checked_at=None,geo_updated_at=None,geo_next_check_at=None)
        response=SimpleNamespace(text=f'"guid":"{A}","region":1,"pingSite":"iad","country":"us"',raise_for_status=lambda:None)
        with patch('geography_worker.requests.get',return_value=response) as get:
            result=enrich_one(None,server)
        self.assertEqual(result['country_code'],'us')
        self.assertEqual(server.geo_source,'battlelog_server_page')
        self.assertEqual(server.geo_next_check_at.tzinfo,timezone.utc)
        self.assertEqual(get.call_count,1)

if __name__=='__main__':unittest.main()

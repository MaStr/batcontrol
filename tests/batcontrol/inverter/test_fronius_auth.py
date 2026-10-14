"""Tests for Fronius inverter authentication functionality."""
import unittest
import hashlib
from packaging import version

from batcontrol.inverter.fronius import hash_utf8, get_api_config, FroniusApiConfig
from batcontrol.value_utils import mask_sensitive_headers


class TestFroniusAuthentication(unittest.TestCase):
    """Test authentication algorithm selection based on firmware version."""

    def test_hash_utf8_md5_default(self):
        """Test that hash_utf8 defaults to MD5."""
        test_string = "test_string"
        result = hash_utf8(test_string)
        expected = hashlib.md5(test_string.encode('utf-8')).hexdigest()
        self.assertEqual(result, expected)

    def test_hash_utf8_md5_explicit(self):
        """Test hash_utf8 with explicit MD5 algorithm."""
        test_string = "test_string"
        result = hash_utf8(test_string, "MD5")
        expected = hashlib.md5(test_string.encode('utf-8')).hexdigest()
        self.assertEqual(result, expected)

    def test_hash_utf8_sha256(self):
        """Test hash_utf8 with SHA256 algorithm."""
        test_string = "test_string"
        result = hash_utf8(test_string, "SHA256")
        expected = hashlib.sha256(test_string.encode('utf-8')).hexdigest()
        self.assertEqual(result, expected)

    def test_hash_utf8_case_insensitive(self):
        """Test that algorithm parameter is case insensitive."""
        test_string = "test_string"
        result_upper = hash_utf8(test_string, "SHA256")
        result_lower = hash_utf8(test_string, "sha256")
        result_mixed = hash_utf8(test_string, "Sha256")
        self.assertEqual(result_upper, result_lower)
        self.assertEqual(result_upper, result_mixed)

    def test_api_config_version_before_1_38_6_1_uses_md5(self):
        """Test that firmware versions before 1.38.6-1 use MD5."""
        test_versions = ["1.35.0", "1.36.0", "1.37.0", "1.38.5"]
        for v in test_versions:
            with self.subTest(version=v):
                parsed_version = version.parse(v)
                config = get_api_config(parsed_version)
                self.assertEqual(config.auth_algorithm, "MD5")

    def test_api_config_version_1_38_6_1_and_later_uses_sha256(self):
        """Test that firmware version 1.38.6-1 and later use SHA256."""
        test_versions = ["1.38.6-1", "1.38.7", "1.39.0", "2.0.0"]
        for v in test_versions:
            with self.subTest(version=v):
                parsed_version = version.parse(v)
                config = get_api_config(parsed_version)
                self.assertEqual(config.auth_algorithm, "SHA256")

    def test_api_config_boundary_version(self):
        """Test the exact boundary version 1.38.6-1."""
        # Version just before the boundary should use MD5
        version_before = version.parse("1.38.5")
        config_before = get_api_config(version_before)
        self.assertEqual(config_before.auth_algorithm, "MD5")
        
        # The boundary version should use SHA256
        boundary_version = version.parse("1.38.6-1")
        config_boundary = get_api_config(boundary_version)
        self.assertEqual(config_boundary.auth_algorithm, "SHA256")

    def test_hash_utf8_with_bytes_input(self):
        """Test hash_utf8 with bytes input."""
        test_bytes = b"test_bytes"
        result_md5 = hash_utf8(test_bytes, "MD5")
        result_sha256 = hash_utf8(test_bytes, "SHA256")
        
        expected_md5 = hashlib.md5(test_bytes).hexdigest()
        expected_sha256 = hashlib.sha256(test_bytes).hexdigest()
        
        self.assertEqual(result_md5, expected_md5)
        self.assertEqual(result_sha256, expected_sha256)


if __name__ == '__main__':
    unittest.main()


class TestAuthHeaderLogging(unittest.TestCase):
    """The digest Authorization header must not be logged in full.

    Code scanning alert py/clear-text-logging-sensitive-data: the header is
    built from the inverter password (password -> a1 -> ha1 -> response) and
    the auth debug logger used to receive it unchanged. The digest is
    replayable for as long as the server nonce is valid.
    """

    def setUp(self):
        self.password = 'testpass'
        self.user = 'customer'
        realm = 'Webinterface area'
        ha1 = hash_utf8(f'{self.user}:{realm}:{self.password}', 'SHA256')
        ha2 = hash_utf8('GET:/commands/Login', 'SHA256')
        self.respdig = hash_utf8(f'{ha1}:nonce:00000001:cnonce:auth:{ha2}', 'SHA256')
        self.header = (
            f'Digest username="{self.user}", realm="{realm}", nonce="nonce", '
            f'uri="/commands/Login", algorithm="SHA256", qop=auth, nc=00000001, '
            f'cnonce="cnonce", response="{self.respdig}"'
        )

    def test_password_derived_digest_is_not_logged(self):
        logged = mask_sensitive_headers({'Authorization': self.header})

        self.assertNotIn(self.respdig, logged['Authorization'])
        self.assertNotIn(self.password, logged['Authorization'])

    def test_header_stays_recognisable(self):
        logged = mask_sensitive_headers({'Authorization': self.header})

        self.assertEqual(
            logged['Authorization'], f'Dig...{self.header[-3:]}')

    def test_non_sensitive_headers_are_kept(self):
        headers = {'Authorization': self.header, 'Content-Type': 'application/json'}

        logged = mask_sensitive_headers(headers)

        self.assertEqual(logged['Content-Type'], 'application/json')

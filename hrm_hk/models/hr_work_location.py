import hashlib
import hmac
import logging
import math
import re
import secrets
import time

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

QR_WINDOW = 90  # seconds — the QR code rotates every 90s

# Patterns to pull lat,lng out of a pasted Google Maps link.
_LATLNG_RES = [
    re.compile(r'@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)'),           # /maps/@22.33,114.19,17z
    re.compile(r'!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)'),       # place ...!3dLAT!4dLNG
    re.compile(r'[?&](?:q|ll|daddr)=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)'),
    re.compile(r'/(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)'),           # generic .../LAT,LNG
]


class HrWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    hk_gmap_url = fields.Char(string='Google Maps Link',
                              help='Paste a Google Maps link; the coordinates are extracted automatically.')
    hk_latitude = fields.Float(string='Latitude', digits=(10, 7))
    hk_longitude = fields.Float(string='Longitude', digits=(10, 7))
    hk_geofence_m = fields.Integer(
        string='Allowed radius (m)', default=200,
        help='A QR clock-in is accepted only within this many metres of the site. 0 = no geofence.')
    hk_qr_secret = fields.Char(readonly=True, copy=False, groups='hr.group_hr_manager')
    hk_monitor_token = fields.Char(string='Monitor Token', readonly=True, copy=False,
                                   groups='hr.group_hr_user')

    @api.onchange('hk_gmap_url')
    def _onchange_hk_gmap_url(self):
        lat, lng = self._parse_gmap(self.hk_gmap_url)
        if lat is not None:
            self.hk_latitude = lat
            self.hk_longitude = lng

    @api.model
    def _parse_gmap(self, url):
        """Extract (lat, lng) from a Google Maps URL; resolves short links."""
        if not url:
            return (None, None)
        u = url.strip()
        if 'goo.gl' in u or 'maps.app' in u:
            try:
                import requests
                u = requests.get(u, timeout=6, allow_redirects=True).url
            except Exception:  # noqa: BLE001
                _logger.warning('hrm_hk: could not resolve short maps link')
        for rx in _LATLNG_RES:
            m = rx.search(u)
            if m:
                return (float(m.group(1)), float(m.group(2)))
        return (None, None)

    # --- rotating QR token -------------------------------------------------
    def _ensure_qr_keys(self):
        for loc in self:
            vals = {}
            if not loc.hk_qr_secret:
                vals['hk_qr_secret'] = secrets.token_hex(16)
            if not loc.hk_monitor_token:
                vals['hk_monitor_token'] = secrets.token_urlsafe(24)
            if vals:
                loc.sudo().write(vals)

    def _qr_code(self, window=None):
        """30-digit code derived from the site secret and the 90s time window."""
        self.ensure_one()
        self._ensure_qr_keys()
        if window is None:
            window = int(time.time() // QR_WINDOW)
        digest = hmac.new(self.sudo().hk_qr_secret.encode(),
                          str(window).encode(), hashlib.sha256).hexdigest()
        return str(int(digest, 16) % (10 ** 30)).zfill(30)

    def _qr_payload(self):
        self.ensure_one()
        now = int(time.time())
        return {
            'site_id': self.id,
            'site': self.name,
            'code': self._qr_code(now // QR_WINDOW),
            'expires_in': QR_WINDOW - (now % QR_WINDOW),
            'window_seconds': QR_WINDOW,
        }

    @api.model
    def _verify_qr(self, site_id, code):
        """Return the site if the code is valid for the current or previous window."""
        Loc = self.sudo()
        loc = Loc.browse(int(site_id)).exists() if site_id else Loc.browse()
        if not loc or not code:
            return Loc.browse()
        window = int(time.time() // QR_WINDOW)
        return loc if code in (loc._qr_code(window), loc._qr_code(window - 1)) else Loc.browse()

    def _within_geofence(self, lat, lng):
        self.ensure_one()
        if not (self.hk_latitude and self.hk_geofence_m):
            return True  # no geofence configured
        R = 6371000.0
        p1, p2 = math.radians(self.hk_latitude), math.radians(lat)
        dphi = math.radians(lat - self.hk_latitude)
        dlmb = math.radians(lng - self.hk_longitude)
        a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
        dist = 2 * R * math.asin(math.sqrt(a))
        return dist <= self.hk_geofence_m

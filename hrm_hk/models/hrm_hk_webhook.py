import hashlib
import hmac
import json
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# event name -> boolean subscription field on the webhook record
EVENT_FIELDS = {
    'leave.approved': 'trig_leave_approved',
    'leave.refused': 'trig_leave_refused',
    'payslip.posted': 'trig_payslip_posted',
    'shift.published': 'trig_shift_published',
    'leave.requested': 'trig_leave_requested',
    'absence.detected': 'trig_absence_detected',
}


class HrmHkWebhook(models.Model):
    _name = 'hrm.hk.webhook'
    _description = 'HRM Outbound Webhook'

    name = fields.Char(required=True)
    url = fields.Char(string='Target URL', required=True)
    secret = fields.Char(
        string='Signing Secret', required=True,
        help='HMAC-SHA256 secret. The signature is sent in the '
             'X-HRM-Signature header so the web app can verify authenticity.',
    )
    active = fields.Boolean(default=True)
    trig_leave_approved = fields.Boolean(string='Leave Approved', default=True)
    trig_leave_refused = fields.Boolean(string='Leave Refused', default=True)
    trig_payslip_posted = fields.Boolean(string='Payslip Posted', default=True)
    trig_shift_published = fields.Boolean(string='Shift Published', default=True)
    trig_leave_requested = fields.Boolean(string='Leave Requested', default=True)
    trig_absence_detected = fields.Boolean(string='Absence Detected', default=True)
    last_delivery = fields.Datetime(string='Last Delivery', readonly=True)
    last_status = fields.Char(string='Last Status', readonly=True)

    def _dispatch(self, event, payload):
        """Queue a signed POST to every active webhook subscribed to `event`.

        Runs after the transaction commits so we never notify on a rollback.
        `payload` is a JSON-serialisable list/dict.
        """
        field = EVENT_FIELDS.get(event)
        if not field:
            return
        hooks = self.sudo().search([('active', '=', True), (field, '=', True)])
        if not hooks:
            return
        body = json.dumps({'event': event, 'data': payload}, default=str)
        for hook in hooks:
            hook_id = hook.id
            self.env.cr.postcommit.add(
                lambda h=hook_id, b=body, e=event: self._deliver(h, b, e)
            )

    def _deliver(self, hook_id, body, event):
        if requests is None:
            _logger.warning('hrm_hk: python-requests not installed, webhook skipped')
            return
        hook = self.browse(hook_id).exists()
        if not hook:
            return
        signature = hmac.new(
            hook.secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        status = 'error'
        try:
            resp = requests.post(
                hook.url,
                data=body,
                headers={
                    'Content-Type': 'application/json',
                    'X-HRM-Event': event,
                    'X-HRM-Signature': f'sha256={signature}',
                },
                timeout=10,
            )
            status = str(resp.status_code)
        except Exception as exc:  # noqa: BLE001 - webhook must never break payroll
            _logger.warning('hrm_hk webhook %s failed: %s', hook.name, exc)
            status = f'error: {exc}'
        hook.sudo().write({'last_delivery': fields.Datetime.now(), 'last_status': status})

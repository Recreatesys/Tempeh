import base64
import functools
import json
import logging
from datetime import timedelta

from odoo import http, fields, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.tools.image import image_process

_logger = logging.getLogger(__name__)

from .auth import jwt_decode, _json


def hrm_auth(func):
    """Authenticate the Bearer access token and resolve the caller's employee.

    Runs as sudo but every handler must scope its queries to
    ``request.hrm_employee`` — ownership is enforced by the token, not by ORM
    record rules (employees are portal users without HR model ACLs).
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kw):
        header = request.httprequest.headers.get('Authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        payload = jwt_decode(request.env, token)
        if not payload or payload.get('typ') != 'access':
            return _json({'error': 'unauthorized'}, 401)
        employee = request.env['hr.employee'].sudo().browse(payload.get('eid')).exists()
        if not employee:
            return _json({'error': 'no_employee'}, 403)
        # Employees are portal users with no HR ACLs, and an auth='none' route
        # leaves env.user empty (breaking ORM code that calls env.user.has_group,
        # and blanking group-restricted fields in QWeb report rendering). Run the
        # request as an internal HR admin that HAS the HR/payroll groups, so both
        # ORM logic and report rendering behave. Ownership is enforced by the
        # token's employee id, not by record rules.
        request.update_env(user=SUPERUSER_ID)  # make env usable first
        admin = request.env.ref('base.user_admin', raise_if_not_found=False)
        run_uid = admin.id if (admin and admin.active and not admin.share) else SUPERUSER_ID
        request.update_env(user=run_uid)
        request.hrm_employee = employee.with_user(run_uid)
        return func(self, *args, **kw)
    return wrapper


def hrm_service(func):
    """Authenticate a server-to-server call via the X-API-Key header
    (hrm.hk.api.key). Used by the Next.js server for push fan-out."""
    @functools.wraps(func)
    def wrapper(self, *args, **kw):
        raw = request.httprequest.headers.get('X-API-Key', '')
        key = request.env['hrm.hk.api.key'].sudo()._verify(raw)
        if not key:
            return _json({'error': 'unauthorized'}, 401)
        request.update_env(user=SUPERUSER_ID)
        return func(self, *args, **kw)
    return wrapper


def _body():
    try:
        return json.loads(request.httprequest.data or b'{}')
    except ValueError:
        return {}


class HrmHkApiController(http.Controller):

    # --- profile -----------------------------------------------------------
    @http.route('/api/hrm/v1/me', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def me(self, **kw):
        return _json(request.hrm_employee._hrm_hk_self_data())

    # Nothing is directly writable through this endpoint any more — see
    # _PROFILE_NO_ESS and _PROFILE_ADDRESS_FIELDS below. Kept as an explicit
    # empty set (rather than removed) so the shape of this endpoint stays
    # obvious and any field added back here is a deliberate, reviewed choice.
    _PROFILE_WRITABLE = set()
    _PROFILE_SENSITIVE = {'bank_account_id'}  # routed through HR approval (pre-existing shape)

    # Portlets the customer's spec lists as having NO ESS exposure at all
    # (Phone Info, Email Info, Emergency Contact — see hrm_hk_ess_request.py's
    # PORTLET_SELECTION, which deliberately excludes them too). These fields
    # must never be employee-writable, directly or via an approval workflow.
    _PROFILE_NO_ESS = {
        'mobile_phone': 'phone_info',
        'private_phone': 'phone_info',
        'private_email': 'email_info',
        'emergency_contact': 'emergency_contact',
        'emergency_phone': 'emergency_contact',
    }
    # Address portlet now has HR-approval routing via hrm.hk.ess.request
    # (portlet_type='address') — these can no longer be written directly.
    _PROFILE_ADDRESS_FIELDS = {'private_street', 'private_city', 'private_zip'}

    @http.route('/api/hrm/v1/profile', type='http', auth='none', methods=['PATCH'],
                csrf=False, save_session=False)
    @hrm_auth
    def update_profile(self, **kw):
        emp = request.hrm_employee
        body = _body()
        # Fail loudly on any now-forbidden field instead of silently dropping
        # it — a silent drop would look like a successful save to the app.
        errors = []
        for field in sorted(set(body) & set(self._PROFILE_NO_ESS)):
            errors.append({
                'field': field,
                'reason': 'no_ess_exposure',
                'detail': 'This field has no employee self-service exposure per the '
                          '"%s" portlet and can never be changed from the app.'
                          % self._PROFILE_NO_ESS[field],
            })
        for field in sorted(set(body) & self._PROFILE_ADDRESS_FIELDS):
            errors.append({
                'field': field,
                'reason': 'requires_change_request',
                'detail': "Address changes now require HR approval. Submit "
                          "POST /api/hrm/v1/ess_requests with portlet_type='address' "
                          "and an attachment instead.",
                'portlet_type': 'address',
            })
        if errors:
            return _json({'error': 'forbidden_fields', 'fields': errors}, 422)
        vals = {k: v for k, v in body.items() if k in self._PROFILE_WRITABLE}
        if vals:
            emp.sudo().write(vals)
        sensitive = {k: v for k, v in body.items() if k in self._PROFILE_SENSITIVE}
        if sensitive:
            emp.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                summary='Employee requested bank / sensitive detail change',
                note=json.dumps(sensitive),
                user_id=emp.parent_id.user_id.id or request.env.ref('base.user_admin').id,
            )
        return _json({'ok': True, 'updated': list(vals.keys()),
                      'pending_approval': list(sensitive.keys())})

    # --- leave -------------------------------------------------------------
    @http.route('/api/hrm/v1/leave_types', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def leave_types(self, **kw):
        types = request.env['hr.leave.type'].sudo().search([('active', '=', True)])
        return _json([{
            'id': t.id,
            'name': t.name,
            'statutory': t.hk_statutory,
            'requires_allocation': t.requires_allocation,
            'requires_proof': t.hk_requires_proof,
        } for t in types])

    @http.route('/api/hrm/v1/leaves', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_leaves(self, **kw):
        emp = request.hrm_employee
        leaves = request.env['hr.leave'].sudo().search(
            [('employee_id', '=', emp.id)], limit=100, order='request_date_from desc')
        allocations = request.env['hr.leave.allocation'].sudo().search([
            ('employee_id', '=', emp.id), ('state', '=', 'validate')])
        balances = {}
        for alloc in allocations:
            t = alloc.holiday_status_id
            balances.setdefault(t.name, 0.0)
            balances[t.name] += alloc.number_of_days
        return _json({
            'balances': balances,
            'leaves': [{
                'id': lv.id,
                'type': lv.holiday_status_id.name,
                'date_from': str(lv.request_date_from) if lv.request_date_from else None,
                'date_to': str(lv.request_date_to) if lv.request_date_to else None,
                'days': lv.number_of_days,
                'state': lv.state,
            } for lv in leaves],
        })

    @http.route('/api/hrm/v1/leaves', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_auth
    def create_leave(self, **kw):
        emp = request.hrm_employee
        body = _body()
        type_id = body.get('holiday_status_id')
        date_from = body.get('date_from')
        date_to = body.get('date_to')
        if not (type_id and date_from and date_to):
            return _json({'error': 'missing_fields',
                          'required': ['holiday_status_id', 'date_from', 'date_to']}, 400)
        leave_type = request.env['hr.leave.type'].sudo().browse(int(type_id)).exists()
        if not leave_type:
            return _json({'error': 'invalid_type'}, 400)
        # Types flagged hk_requires_proof (e.g. sickness) demand a supporting
        # document — reject up front so no half-formed request is created.
        proof = body.get('proof')
        if leave_type.hk_requires_proof and not (proof and proof.get('data')):
            return _json({'error': 'proof_required',
                          'leave_type': leave_type.name}, 422)
        # Validate a raster-image proof BEFORE creating anything: the Documents
        # module computes a thumbnail via image_process at flush time and only
        # catches UserError/TypeError, so a corrupt photo (OSError) would 500 the
        # request. Run the same call now and reject cleanly. PDFs and webp are
        # thumbnailed client-side by Documents, so they need no server check.
        _pmime = (proof or {}).get('mimetype') or ''
        if proof and proof.get('data') and _pmime.startswith('image/') and _pmime != 'image/webp':
            try:
                image_process(base64.b64decode(proof['data'].split(',')[-1]),
                              size=(200, 140), crop='center')
            except Exception:  # noqa: BLE001 - unreadable/corrupt image
                return _json({'error': 'proof_invalid',
                              'leave_type': leave_type.name}, 422)
        # Employees are portal users (no HR ACLs), so create as sudo with an
        # explicit employee_id. hr.leave lands in 'confirm' on create (v19).
        try:
            leave = request.env['hr.leave'].sudo().with_context(
                default_employee_id=emp.id).create({
                'employee_id': emp.id,
                'holiday_status_id': int(type_id),
                'request_date_from': date_from,
                'request_date_to': date_to,
                'name': body.get('reason') or 'Leave request',
            })
        except Exception as exc:  # noqa: BLE001 - surface validation to the app
            _logger.exception('hrm_hk leave create failed')
            return _json({'error': 'create_failed', 'detail': str(exc)}, 400)
        # supporting document (doctor's note etc.) — attach to the leave and
        # post it to the chatter so the approving manager can see it. Images
        # were validated above, so this is safe; PDFs and other files attach
        # directly.
        if proof and proof.get('data'):
            try:
                att = request.env['ir.attachment'].sudo().create({
                    'name': proof.get('filename') or 'medical-proof',
                    'datas': proof['data'].split(',')[-1],
                    'res_model': 'hr.leave', 'res_id': leave.id,
                    'mimetype': proof.get('mimetype') or 'application/octet-stream',
                })
                leave.sudo().message_post(
                    body='Supporting document uploaded via the employee app.',
                    attachment_ids=[att.id])
            except Exception:  # noqa: BLE001 - keep the required document or reject
                _logger.warning('hrm_hk: proof attach failed for leave %s', leave.id)
                leave.unlink()
                return _json({'error': 'proof_invalid', 'leave_type': leave_type.name}, 422)
        # notify the manager (web-app notification + optional webhook)
        if emp.parent_id:
            request.env['hrm.hk.notification'].sudo()._notify(
                emp.parent_id, 'leave_request',
                name='%s requested %s' % (emp.name, leave.holiday_status_id.name),
                body='%s → %s (%s day(s))' % (
                    leave.request_date_from, leave.request_date_to, leave.number_of_days),
                about=emp, res_model='hr.leave', res_id=leave.id,
                webhook_event='leave.requested')
        return _json({'ok': True, 'id': leave.id, 'state': leave.state}, 201)

    @http.route('/api/hrm/v1/leaves/<int:leave_id>/cancel', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def cancel_leave(self, leave_id, **kw):
        emp = request.hrm_employee
        leave = request.env['hr.leave'].sudo().browse(leave_id).exists()
        if not leave or leave.employee_id.id != emp.id:
            return _json({'error': 'not_found'}, 404)
        # Pending request -> delete it. Validated & still cancellable -> user-cancel.
        if leave.state in ('draft', 'confirm'):
            leave.unlink()
            return _json({'ok': True, 'state': 'deleted'})
        if leave.can_cancel:
            leave._action_user_cancel('Cancelled from web app')
            return _json({'ok': True, 'state': leave.state})
        return _json({'error': 'not_cancellable', 'state': leave.state}, 409)

    # --- ESS change requests ------------------------------------------------
    @http.route('/api/hrm/v1/ess_requests/portlets', type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False)
    @hrm_auth
    def ess_request_portlets(self, **kw):
        """Which portlets the app should expose as employee-initiated change
        requests, and whether each needs a supporting attachment. Portlets with
        no ESS at all (per the customer spec) simply have no config row here,
        so the app never lists them as editable."""
        configs = request.env['hrm.hk.ess.portlet.config'].sudo().search(
            [('active', '=', True)], order='sequence')
        return _json([{
            'portlet_type': c.portlet_type,
            'attachment_required': c.attachment_required,
        } for c in configs])

    @http.route('/api/hrm/v1/ess_requests', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_ess_requests(self, **kw):
        emp = request.hrm_employee
        recs = request.env['hrm.hk.ess.request'].sudo().search(
            [('employee_id', '=', emp.id)], limit=100, order='create_date desc')
        return _json(recs._ess_payload())

    @http.route('/api/hrm/v1/ess_requests/<int:request_id>', type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False)
    @hrm_auth
    def get_ess_request(self, request_id, **kw):
        emp = request.hrm_employee
        rec = request.env['hrm.hk.ess.request'].sudo().browse(request_id).exists()
        if not rec or rec.employee_id.id != emp.id:
            return _json({'error': 'not_found'}, 404)
        return _json(rec._ess_payload()[0])

    @http.route('/api/hrm/v1/ess_requests', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_auth
    def create_ess_request(self, **kw):
        """Create a change request and submit it in the same call — the app has
        no use for a request that sits unsubmitted, and the attachment
        requirement is enforced by action_submit()."""
        emp = request.hrm_employee
        body = _body()
        portlet_type = body.get('portlet_type')
        proposed_values = body.get('proposed_values')
        allowed = dict(request.env['hrm.hk.ess.request']._fields['portlet_type'].selection)
        if portlet_type not in allowed:
            return _json({'error': 'invalid_portlet_type', 'allowed': list(allowed)}, 400)
        if not proposed_values or not isinstance(proposed_values, dict):
            return _json({'error': 'missing_fields',
                          'required': ['portlet_type', 'proposed_values']}, 400)
        try:
            rec = request.env['hrm.hk.ess.request'].sudo().create({
                'employee_id': emp.id,
                'portlet_type': portlet_type,
                'proposed_values': proposed_values,
                'reason': body.get('reason'),
            })
        except Exception as exc:  # noqa: BLE001
            _logger.exception('hrm_hk ess_request create failed')
            return _json({'error': 'create_failed', 'detail': str(exc)}, 400)
        # supporting attachment(s) — accept either a single 'attachment' object
        # or an 'attachments' list, same shape as the leave 'proof' upload.
        attachments = body.get('attachments') or (
            [body['attachment']] if body.get('attachment') else [])
        for att in attachments:
            if not att.get('data'):
                continue
            mimetype = att.get('mimetype') or ''
            if mimetype.startswith('image/') and mimetype != 'image/webp':
                try:
                    image_process(base64.b64decode(att['data'].split(',')[-1]),
                                  size=(200, 140), crop='center')
                except Exception:  # noqa: BLE001 - unreadable/corrupt image
                    rec.unlink()
                    return _json({'error': 'attachment_invalid'}, 422)
            try:
                request.env['ir.attachment'].sudo().create({
                    'name': att.get('filename') or 'supporting-document',
                    'datas': att['data'].split(',')[-1],
                    'res_model': 'hrm.hk.ess.request', 'res_id': rec.id,
                    'mimetype': mimetype or 'application/octet-stream',
                })
            except Exception:  # noqa: BLE001
                rec.unlink()
                return _json({'error': 'attachment_invalid'}, 422)
        try:
            rec.action_submit()
        except UserError as exc:
            rec.unlink()
            return _json({'error': 'submit_failed', 'detail': str(exc)}, 422)
        return _json({'ok': True, 'id': rec.id, 'state': rec.state}, 201)

    # --- expenses ----------------------------------------------------------
    @http.route('/api/hrm/v1/expenses/categories', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def expense_categories(self, **kw):
        prods = request.env['product.product'].sudo().search(
            [('can_be_expensed', '=', True)], limit=100)
        return _json([{'id': p.id, 'name': p.display_name} for p in prods])

    @http.route('/api/hrm/v1/expenses', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_expenses(self, **kw):
        emp = request.hrm_employee
        exps = request.env['hr.expense'].sudo().search(
            [('employee_id', '=', emp.id)], limit=100, order='date desc')
        return _json([self._expense_dict(e) for e in exps])

    @http.route('/api/hrm/v1/expenses', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_auth
    def create_expense(self, **kw):
        emp = request.hrm_employee
        body = _body()
        if not (body.get('product_id') and body.get('total_amount')):
            return _json({'error': 'missing_fields',
                          'required': ['product_id', 'total_amount']}, 400)
        try:
            expense = request.env['hr.expense'].sudo().create({
                'name': body.get('name') or 'Expense',
                'employee_id': emp.id,
                'product_id': int(body['product_id']),
                'total_amount': float(body['total_amount']),
                'date': body.get('date') or fields.Date.today(),
                'description': body.get('description') or '',
                'payment_mode': 'own_account',
            })
        except Exception as exc:  # noqa: BLE001
            _logger.exception('hrm_hk expense create failed')
            return _json({'error': 'create_failed', 'detail': str(exc)}, 400)
        # receipt (best-effort — a bad file must not lose the expense)
        receipt = body.get('receipt')
        if receipt and receipt.get('data'):
            try:
                request.env['ir.attachment'].sudo().create({
                    'name': receipt.get('filename') or 'receipt',
                    'datas': receipt['data'].split(',')[-1],
                    'res_model': 'hr.expense', 'res_id': expense.id,
                    'mimetype': receipt.get('mimetype') or 'application/octet-stream',
                })
            except Exception:  # noqa: BLE001
                _logger.warning('hrm_hk: could not attach receipt to expense %s', expense.id)
        # submit for approval (best-effort). In v18 approval runs through an
        # hr.expense.sheet that wraps the expense line(s).
        if body.get('submit', True):
            try:
                sheet = request.env['hr.expense.sheet'].sudo().create({
                    'name': expense.name,
                    'employee_id': emp.id,
                    'expense_line_ids': [(6, 0, expense.ids)],
                })
                sheet.action_submit_sheet()
            except Exception as exc:  # noqa: BLE001
                _logger.warning('hrm_hk: expense %s submit failed: %s', expense.id, exc)
        # notify manager
        if emp.parent_id:
            request.env['hrm.hk.notification'].sudo()._notify(
                emp.parent_id, 'info',
                name='%s submitted an expense' % emp.name,
                body='%s: %s' % (expense.name, expense.total_amount),
                about=emp, res_model='hr.expense', res_id=expense.id,
                webhook_event='leave.requested')
        return _json({'ok': True, 'id': expense.id, 'state': expense.state}, 201)

    @http.route('/api/hrm/v1/team/expenses', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team_expenses(self, **kw):
        ids = self._managed().ids
        if not ids:
            return _json([])
        domain = [('employee_id', 'in', ids)]
        if request.httprequest.args.get('pending') == '1':
            domain.append(('state', '=', 'submitted'))
        exps = request.env['hr.expense'].sudo().search(
            domain, limit=200, order='date desc')
        return _json([self._expense_dict(e, with_emp=True) for e in exps])

    @http.route('/api/hrm/v1/team/expenses/<int:exp_id>/approve', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def approve_expense(self, exp_id, **kw):
        exp = self._team_expense(exp_id)
        if not exp:
            return _json({'error': 'not_found'}, 404)
        if not exp.sheet_id:
            return _json({'error': 'not_submitted'}, 400)
        exp.sheet_id.sudo()._do_approve()
        request.env['hrm.hk.notification'].sudo()._notify(
            exp.employee_id, 'leave_result',
            name='Your expense "%s" was approved' % exp.name,
            about=request.hrm_employee, res_model='hr.expense', res_id=exp.id)
        return _json({'ok': True, 'state': exp.state})

    @http.route('/api/hrm/v1/team/expenses/<int:exp_id>/reject', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def reject_expense(self, exp_id, **kw):
        exp = self._team_expense(exp_id)
        if not exp:
            return _json({'error': 'not_found'}, 404)
        reason = _body().get('reason') or 'Rejected'
        if not exp.sheet_id:
            return _json({'error': 'not_submitted'}, 400)
        exp.sheet_id.sudo()._do_refuse(reason)
        request.env['hrm.hk.notification'].sudo()._notify(
            exp.employee_id, 'leave_result',
            name='Your expense "%s" was rejected' % exp.name,
            body=reason, about=request.hrm_employee, res_model='hr.expense', res_id=exp.id)
        return _json({'ok': True, 'state': exp.state})

    def _team_expense(self, exp_id):
        ids = self._managed().ids
        exp = request.env['hr.expense'].sudo().browse(exp_id).exists()
        if not exp or exp.employee_id.id not in ids:
            return None
        return exp

    def _expense_dict(self, e, with_emp=False):
        d = {
            'id': e.id, 'name': e.name,
            'category': e.product_id.display_name,
            'total_amount': e.total_amount,
            'date': str(e.date) if e.date else None,
            'state': e.state,
            'description': e.description or None,
            'has_receipt': bool(e.attachment_ids),
        }
        if with_emp:
            d['employee'] = e.employee_id.name
            d['employee_id'] = e.employee_id.id
        return d

    # --- notices / announcements ------------------------------------------
    @http.route('/api/hrm/v1/notices', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def notices(self, **kw):
        recs = request.env['hrm.hk.notice'].sudo().search([('active', '=', True)], limit=50)
        return _json(recs._notice_payload())

    # --- schedule / shifts -------------------------------------------------
    @http.route('/api/hrm/v1/shifts', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_shifts(self, **kw):
        emp = request.hrm_employee
        shifts = request.env['hrm.hk.shift'].sudo().search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ['published', 'confirmed']),
        ], order='start_datetime asc', limit=200)
        return _json([{
            'id': s.id,
            'site': s.work_location_id.name,
            'shift': s.shift_type_id.hk_display_code or None,
            'shift_name': s.shift_type_id.name or None,
            'start': s.start_datetime and str(s.start_datetime),
            'end': s.end_datetime and str(s.end_datetime),
            'hours': round(s.planned_hours, 2),
            'state': s.state,
            'note': s.note or None,
        } for s in shifts])

    @http.route('/api/hrm/v1/shifts/<int:shift_id>/confirm', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def confirm_shift(self, shift_id, **kw):
        emp = request.hrm_employee
        shift = request.env['hrm.hk.shift'].sudo().browse(shift_id).exists()
        if not shift or shift.employee_id.id != emp.id:
            return _json({'error': 'not_found'}, 404)
        shift.action_confirm()
        return _json({'ok': True, 'state': shift.state})

    # --- manager / team leader --------------------------------------------
    def _managed(self):
        """Recordset of employees the caller manages (empty if not a leader)."""
        return request.hrm_employee._hrm_hk_managed_employees()

    @http.route('/api/hrm/v1/team', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team(self, **kw):
        team = self._managed()
        if not team:
            return _json({'is_manager': False, 'members': []})
        today = fields.Date.today()
        day_start = fields.Datetime.to_datetime(today)
        day_end = day_start + timedelta(days=1)
        Att = request.env['hr.attendance'].sudo()
        Lv = request.env['hr.leave'].sudo()
        Shift = request.env['hrm.hk.shift'].sudo()
        members = []
        for e in team:
            att = Att.search([('employee_id', '=', e.id),
                              ('check_in', '>=', day_start)], order='check_in desc', limit=1)
            on_leave = Lv.search([('employee_id', '=', e.id), ('state', '=', 'validate'),
                                  ('date_from', '<', day_end), ('date_to', '>=', day_start)], limit=1)
            shift = Shift.search([('employee_id', '=', e.id), ('state', 'in', ['published', 'confirmed']),
                                  ('start_datetime', '>=', day_start),
                                  ('start_datetime', '<', day_end)], limit=1)
            if on_leave:
                status = 'on_leave'
            elif att:
                status = 'checked_out' if att.check_out else 'present'
            elif shift:
                status = 'absent'
            else:
                status = 'off'
            members.append({
                'id': e.id, 'name': e.name, 'job_title': e.job_title,
                'site': (shift.work_location_id.name if shift else None),
                'status': status,
                'check_in': str(att.check_in) if att else None,
                'leave_type': on_leave.holiday_status_id.name if on_leave else None,
            })
        return _json({'is_manager': True, 'members': members})

    @http.route('/api/hrm/v1/team/schedule', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team_schedule(self, **kw):
        ids = self._managed().ids
        if not ids:
            return _json([])
        shifts = request.env['hrm.hk.shift'].sudo().search([
            ('employee_id', 'in', ids), ('state', 'in', ['published', 'confirmed']),
        ], order='start_datetime asc', limit=400)
        return _json([{
            'id': s.id, 'employee': s.employee_id.name, 'employee_id': s.employee_id.id,
            'site': s.work_location_id.name,
            'start': str(s.start_datetime) if s.start_datetime else None,
            'end': str(s.end_datetime) if s.end_datetime else None,
            'hours': round(s.planned_hours, 2), 'state': s.state,
        } for s in shifts])

    @http.route('/api/hrm/v1/team/attendance', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team_attendance(self, **kw):
        ids = self._managed().ids
        if not ids:
            return _json([])
        recs = request.env['hr.attendance'].sudo().search(
            [('employee_id', 'in', ids)], order='check_in desc', limit=100)
        return _json([self._attendance_dict(r, with_emp=True) for r in recs])

    @http.route('/api/hrm/v1/team/leaves', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team_leaves(self, **kw):
        ids = self._managed().ids
        if not ids:
            return _json([])
        pending_only = request.httprequest.args.get('pending') == '1'
        domain = [('employee_id', 'in', ids)]
        if pending_only:
            domain.append(('state', 'in', ['confirm', 'validate1']))
        leaves = request.env['hr.leave'].sudo().search(
            domain, order='request_date_from desc', limit=200)
        return _json([{
            'id': lv.id, 'employee': lv.employee_id.name, 'employee_id': lv.employee_id.id,
            'type': lv.holiday_status_id.name,
            'date_from': str(lv.request_date_from) if lv.request_date_from else None,
            'date_to': str(lv.request_date_to) if lv.request_date_to else None,
            'days': lv.number_of_days, 'state': lv.state,
            'reason': lv.name,
        } for lv in leaves])

    def _team_leave(self, leave_id):
        ids = self._managed().ids
        lv = request.env['hr.leave'].sudo().browse(leave_id).exists()
        if not lv or lv.employee_id.id not in ids:
            return None
        return lv

    @http.route('/api/hrm/v1/team/leaves/<int:leave_id>/approve', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def approve_leave(self, leave_id, **kw):
        lv = self._team_leave(leave_id)
        if not lv:
            return _json({'error': 'not_found'}, 404)
        # 'both'-validation leaves need two approvals to reach 'validate'
        for _ in range(2):
            if lv.state in ('confirm', 'validate1'):
                lv.action_approve()
        request.env['hrm.hk.notification'].sudo()._notify(
            lv.employee_id, 'leave_result',
            name='Your %s was approved' % lv.holiday_status_id.name,
            about=request.hrm_employee, res_model='hr.leave', res_id=lv.id)
        return _json({'ok': True, 'state': lv.state})

    @http.route('/api/hrm/v1/team/leaves/<int:leave_id>/reject', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def reject_leave(self, leave_id, **kw):
        lv = self._team_leave(leave_id)
        if not lv:
            return _json({'error': 'not_found'}, 404)
        lv.action_refuse()
        request.env['hrm.hk.notification'].sudo()._notify(
            lv.employee_id, 'leave_result',
            name='Your %s was rejected' % lv.holiday_status_id.name,
            about=request.hrm_employee, res_model='hr.leave', res_id=lv.id)
        return _json({'ok': True, 'state': lv.state})

    # --- HR approval of ESS change requests ---------------------------------
    # Eligibility is per-portlet (Group A / Group B), not org hierarchy, so this
    # does not reuse _managed() — it filters by hrm.hk.ess.request.can_be_decided_by
    # against the caller's own res.users groups.
    @http.route('/api/hrm/v1/team/ess_requests', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def team_ess_requests(self, **kw):
        user = request.hrm_employee.sudo().user_id
        if not user:
            return _json([])
        recs = request.env['hrm.hk.ess.request'].sudo().search(
            [('state', '=', 'submitted')], order='create_date desc', limit=200)
        approvable = recs.filtered(lambda r: r.can_be_decided_by(user))
        return _json(approvable._ess_payload())

    @http.route('/api/hrm/v1/team/ess_requests/<int:request_id>/approve', type='http',
                auth='none', methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def approve_ess_request(self, request_id, **kw):
        # request.hrm_employee.user_id is derived from the JWT's verified
        # 'eid' claim, which the caller cannot forge, unlike a context value
        # — this is the only identity action_approve trusts for eligibility.
        approving_user = request.hrm_employee.sudo().user_id
        if not approving_user:
            return _json({'error': 'forbidden'}, 403)
        rec = request.env['hrm.hk.ess.request'].sudo().browse(request_id).exists()
        if not rec:
            return _json({'error': 'not_found'}, 404)
        body = _body()
        try:
            # Re-scope env.user to the real approving employee (hrm_auth left
            # it on an elevated admin/superuser identity) BEFORE calling
            # action_approve, so the ORM-layer guard in write() checks the
            # genuine approver, not a value we merely asserted.
            rec.with_user(approving_user).action_approve(decision_reason=body.get('reason'))
        except AccessError:
            return _json({'error': 'forbidden'}, 403)
        except UserError as exc:
            return _json({'error': 'approve_failed', 'detail': str(exc)}, 409)
        return _json({'ok': True, 'state': rec.state, 'applied': rec.applied})

    @http.route('/api/hrm/v1/team/ess_requests/<int:request_id>/reject', type='http',
                auth='none', methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def reject_ess_request(self, request_id, **kw):
        approving_user = request.hrm_employee.sudo().user_id
        if not approving_user:
            return _json({'error': 'forbidden'}, 403)
        rec = request.env['hrm.hk.ess.request'].sudo().browse(request_id).exists()
        if not rec:
            return _json({'error': 'not_found'}, 404)
        body = _body()
        try:
            rec.with_user(approving_user).action_reject(decision_reason=body.get('reason'))
        except AccessError:
            return _json({'error': 'forbidden'}, 403)
        except UserError as exc:
            return _json({'error': 'reject_failed', 'detail': str(exc)}, 409)
        return _json({'ok': True, 'state': rec.state})

    # --- notifications -----------------------------------------------------
    @http.route('/api/hrm/v1/notifications', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def notifications(self, **kw):
        emp = request.hrm_employee
        notes = request.env['hrm.hk.notification'].sudo().search(
            [('employee_id', '=', emp.id)], limit=50)
        return _json({
            'unread': sum(1 for n in notes if not n.is_read),
            'items': [{
                'id': n.id, 'category': n.category, 'title': n.name,
                'body': n.body, 'about': n.related_employee_id.name or None,
                'res_model': n.res_model, 'res_id': n.res_id,
                'is_read': n.is_read, 'created': str(n.create_date),
            } for n in notes],
        })

    @http.route('/api/hrm/v1/notifications/read', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def mark_read(self, **kw):
        emp = request.hrm_employee
        body = _body()
        domain = [('employee_id', '=', emp.id), ('is_read', '=', False)]
        if body.get('id'):
            domain.append(('id', '=', int(body['id'])))
        request.env['hrm.hk.notification'].sudo().search(domain).write({'is_read': True})
        return _json({'ok': True})

    @http.route('/api/hrm/v1/attendance/qr_check_in', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def qr_check_in(self, **kw):
        emp = request.hrm_employee
        body = _body()
        site = request.env['hr.work.location']._verify_qr(
            body.get('site_id'), body.get('code'))
        if not site:
            return _json({'error': 'invalid_or_expired_qr'}, 400)
        lat, lng = body.get('latitude'), body.get('longitude')
        if lat is not None and not site._within_geofence(lat, lng):
            return _json({'error': 'outside_site', 'site': site.name}, 403)
        Att = request.env['hr.attendance'].sudo()
        # toggle: clock out if already in, else clock in — both stamped at the site
        if emp.attendance_state == 'checked_in':
            rec = Att.search([('employee_id', '=', emp.id), ('check_out', '=', False)],
                             order='check_in desc', limit=1)
            if rec:
                vals = {'check_out': fields.Datetime.now(), 'hk_site_id': site.id}
                if lat is not None:
                    vals.update(out_latitude=lat, out_longitude=lng)
                rec.write(vals)
                return _json({'ok': True, 'action': 'check_out', 'site': site.name,
                              'worked_hours': rec.worked_hours})
        vals = {'employee_id': emp.id, 'check_in': fields.Datetime.now(), 'hk_site_id': site.id}
        if lat is not None:
            vals.update(in_latitude=lat, in_longitude=lng)
        rec = Att.create(vals)
        return _json({'ok': True, 'action': 'check_in', 'site': site.name,
                      'check_in': str(rec.check_in)})

    @http.route('/api/hrm/v1/site/qr', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    def site_qr(self, **kw):
        """Public, monitor-token-gated: current rotating QR payload for a site's screen."""
        token = request.httprequest.args.get('token')
        if not token:
            return _json({'error': 'missing_token'}, 401)
        site = request.env['hr.work.location'].sudo().search(
            [('hk_monitor_token', '=', token)], limit=1)
        if not site:
            return _json({'error': 'invalid_token'}, 401)
        return _json(site._qr_payload())

    # --- web push subscriptions -------------------------------------------
    @http.route('/api/hrm/v1/push/subscribe', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_auth
    def push_subscribe(self, **kw):
        emp = request.hrm_employee
        body = _body()
        keys = body.get('keys') or {}
        endpoint = body.get('endpoint')
        if not (endpoint and keys.get('p256dh') and keys.get('auth')):
            return _json({'error': 'invalid_subscription'}, 400)
        request.env['hrm.hk.push.subscription']._upsert(
            emp, endpoint, keys['p256dh'], keys['auth'],
            ua=request.httprequest.headers.get('User-Agent'))
        return _json({'ok': True})

    @http.route('/api/hrm/v1/push/unsubscribe', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_auth
    def push_unsubscribe(self, **kw):
        endpoint = _body().get('endpoint')
        if endpoint:
            request.env['hrm.hk.push.subscription'].sudo().search(
                [('endpoint', '=', endpoint)]).unlink()
        return _json({'ok': True})

    @http.route('/api/hrm/v1/push/subscriptions', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_service
    def push_subscriptions(self, **kw):
        emp_id = request.httprequest.args.get('employee_id')
        if not emp_id:
            return _json([])
        subs = request.env['hrm.hk.push.subscription'].sudo().search(
            [('employee_id', '=', int(emp_id)), ('active', '=', True)])
        return _json([{
            'id': s.id, 'endpoint': s.endpoint,
            'keys': {'p256dh': s.p256dh, 'auth': s.auth},
        } for s in subs])

    @http.route('/api/hrm/v1/push/forget', type='http', auth='none', methods=['POST'],
                csrf=False, save_session=False)
    @hrm_service
    def push_forget(self, **kw):
        endpoint = _body().get('endpoint')
        if endpoint:
            request.env['hrm.hk.push.subscription'].sudo().search(
                [('endpoint', '=', endpoint)]).unlink()
        return _json({'ok': True})

    # --- attendance --------------------------------------------------------
    @http.route('/api/hrm/v1/attendance', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_attendance(self, **kw):
        emp = request.hrm_employee
        recs = request.env['hr.attendance'].sudo().search(
            [('employee_id', '=', emp.id)], limit=60, order='check_in desc')
        return _json({
            'attendance_state': emp.attendance_state,
            'records': [self._attendance_dict(r) for r in recs],
        })

    def _attendance_dict(self, r, with_emp=False):
        d = {
            'id': r.id,
            'check_in': str(r.check_in) if r.check_in else None,
            'check_out': str(r.check_out) if r.check_out else None,
            'worked_hours': r.worked_hours,
            'in_gps': [r.in_latitude, r.in_longitude] if r.in_latitude else None,
            'out_gps': [r.out_latitude, r.out_longitude] if r.out_latitude else None,
        }
        if with_emp:
            d['employee'] = r.employee_id.name
            d['employee_id'] = r.employee_id.id
        return d

    @http.route('/api/hrm/v1/attendance/check_in', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def check_in(self, **kw):
        emp = request.hrm_employee
        body = _body()
        if emp.attendance_state == 'checked_in':
            return _json({'error': 'already_checked_in'}, 409)
        vals = {'employee_id': emp.id, 'check_in': fields.Datetime.now()}
        if body.get('latitude') is not None:
            vals['in_latitude'] = body['latitude']
            vals['in_longitude'] = body.get('longitude')
        rec = request.env['hr.attendance'].sudo().create(vals)
        return _json({'ok': True, 'attendance_id': rec.id, 'check_in': str(rec.check_in)})

    @http.route('/api/hrm/v1/attendance/check_out', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    @hrm_auth
    def check_out(self, **kw):
        emp = request.hrm_employee
        body = _body()
        rec = request.env['hr.attendance'].sudo().search(
            [('employee_id', '=', emp.id), ('check_out', '=', False)],
            limit=1, order='check_in desc')
        if not rec:
            return _json({'error': 'not_checked_in'}, 409)
        vals = {'check_out': fields.Datetime.now()}
        if body.get('latitude') is not None:
            vals['out_latitude'] = body['latitude']
            vals['out_longitude'] = body.get('longitude')
        rec.write(vals)
        return _json({'ok': True, 'worked_hours': rec.worked_hours,
                      'check_out': str(rec.check_out)})

    # --- payslips ----------------------------------------------------------
    @http.route('/api/hrm/v1/payslips', type='http', auth='none', methods=['GET'],
                csrf=False, save_session=False)
    @hrm_auth
    def list_payslips(self, **kw):
        emp = request.hrm_employee
        slips = request.env['hr.payslip'].sudo().search(
            [('employee_id', '=', emp.id), ('state', 'in', ['validated', 'paid'])],
            limit=48, order='date_from desc')
        return _json([{
            'id': s.id,
            'name': s.name,
            'date_from': str(s.date_from) if s.date_from else None,
            'date_to': str(s.date_to) if s.date_to else None,
            'net_wage': s.net_wage if 'net_wage' in s._fields else None,
        } for s in slips])

    @http.route('/api/hrm/v1/payslips/<int:slip_id>/pdf', type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False)
    @hrm_auth
    def payslip_pdf(self, slip_id, **kw):
        emp = request.hrm_employee
        slip = request.env['hr.payslip'].sudo().browse(slip_id).exists()
        if not slip or slip.employee_id.id != emp.id:
            return _json({'error': 'not_found'}, 404)
        # Serve the cached PDF (rendered once at posting); render+cache on miss.
        pdf = slip._hrm_hk_render_pdf()
        if not pdf:
            return _json({'error': 'report_unavailable'}, 500)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', f'inline; filename=payslip-{slip.id}.pdf'),
        ])

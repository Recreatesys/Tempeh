import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Portlets in scope for F1 (EC Transactions Sample.xlsx -> Workflows sheet).
# Portlet #6 (Job Information / Transfer - Site / Dept) is F2 scope and is
# deliberately NOT listed here. When F2 lands, add its value here and seed a
# matching hrm.hk.ess.portlet.config row — nothing else in this file needs to
# change, the dispatch tables below are keyed off this selection.
PORTLET_SELECTION = [
    ('personal_info', 'Personal Info (Marital Status)'),   # portlet 1
    ('bank_account', 'Bank Account'),                       # portlet 2
    ('address', 'Address'),                                 # portlet 3
    ('dependent', 'Dependent'),                              # portlet 4
    ('work_permit', 'Work Permit'),                          # portlet 5
    ('qualification', 'Qualification'),                      # portlet 7
    ('experience', 'Experience'),                             # portlet 8
    ('education', 'Education'),                               # portlet 9
]


class HrmHkEssPortletConfig(models.Model):
    """Data-driven approval-eligibility mapping for ESS change-request portlets.

    The customer's spec sheet has open questions on which HR group approves
    which portlet, so this mapping intentionally lives in data (see
    data/hrm_hk_ess_portlet_config_data.xml) instead of being hardcoded in
    Python — it can be corrected from Settings without a code change.
    """
    _name = 'hrm.hk.ess.portlet.config'
    _description = 'ESS Change Request - Portlet Approval Config'
    _order = 'sequence, id'
    _rec_name = 'portlet_type'

    sequence = fields.Integer(default=10)
    portlet_type = fields.Selection(PORTLET_SELECTION, required=True)
    attachment_required = fields.Boolean(
        string='Attachment Required', default=True,
        help='Employee must attach a supporting document before submitting.')
    approver_group_ids = fields.Many2many(
        'res.groups', string='Eligible Approver Groups', required=True,
        help='Any member of any of these groups may approve/reject requests for '
             'this portlet. Leave the default Group A row untouched to keep the '
             "customer's fallback rule: an unassigned portlet routes to Group A.")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('uniq_portlet_type', 'unique(portlet_type)',
         'Only one approval config is allowed per portlet.'),
    ]


class HrmHkEssRequest(models.Model):
    """Employee-initiated change request that requires HR approval before the
    proposed values are written to the real employee/contract/bank record.

    Workflow: draft -> submitted -> approved / rejected.
    Only 'submitted' requests can be decided; approving writes through to the
    real record (see _apply*), rejecting never touches it.
    """
    _name = 'hrm.hk.ess.request'
    _description = 'ESS Change Request'
    _order = 'create_date desc'

    # Fields that define *what* is being requested — once a request leaves
    # draft, none of these may change again through any code path, employee
    # or HR. Editing proposed_values after an attachment has been reviewed
    # would let a submitted (and later approved) request diverge from what
    # HR actually looked at.
    _LOCKED_AFTER_DRAFT = ('employee_id', 'portlet_type', 'proposed_values')

    # Decision/audit fields a caller must never set by hand — only our own
    # action_* methods may, and only from a genuinely superuser-mode write
    # (see write() below). 'applied' is included: it is the honesty flag
    # behind SHOULD-FIX 6 (a request must not be able to claim it was written
    # through when it wasn't), so it needs the same protection as `state`
    # itself — otherwise a write() call that omits 'state' but sets
    # 'applied'/'approver_id' directly would slip past the guard entirely.
    _SYSTEM_MANAGED = ('state', 'approver_id', 'decision_date', 'applied')

    # portlet_type -> write-through method. 'dependent' has no backing model
    # anywhere in Odoo (core or hr_skills) and is intentionally NOT in this
    # table — see _apply_unmanaged, which now handles only that one portlet.
    # 'qualification' and 'experience' write through to hr.resume.line
    # (hr_skills), added as a dependency for this work package.
    _APPLY_METHODS = {
        'personal_info': '_apply_personal_info',
        'bank_account': '_apply_bank_account',
        'address': '_apply_address',
        'work_permit': '_apply_work_permit',
        'education': '_apply_education',
        'qualification': '_apply_qualification',
        'experience': '_apply_experience',
    }

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade', index=True)
    portlet_type = fields.Selection(PORTLET_SELECTION, string='Portlet', required=True)
    config_id = fields.Many2one(
        'hrm.hk.ess.portlet.config', compute='_compute_config_id', store=True,
        string='Approval Config')
    attachment_required = fields.Boolean(related='config_id.attachment_required', store=False)

    proposed_values = fields.Json(
        string='Proposed Values', required=True,
        help='Dict of field: new value, as submitted by the employee app. '
             'Interpreted by portlet_type on approval — see _apply*.')
    current_values = fields.Json(
        string='Current Values (Snapshot)', readonly=True, copy=False,
        help='Snapshot of the real record taken at submit time, for audit / diff display.')
    reason = fields.Text(string='Employee Note')

    attachment_ids = fields.One2many(
        'ir.attachment', 'res_id', string='Supporting Attachment(s)',
        domain=[('res_model', '=', 'hrm.hk.ess.request')])

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='draft', required=True, copy=False, index=True)

    approver_id = fields.Many2one('res.users', string='Decided By', readonly=True, copy=False)
    decision_date = fields.Datetime(readonly=True, copy=False)
    decision_reason = fields.Text(string='HR Decision Note')

    applied = fields.Boolean(
        default=False, copy=False, readonly=True,
        help='True once approval actually wrote the proposed values through to the real '
             "record. False for an approved request means the portlet has no automatic "
             'write-through target yet and HR must apply it by hand — see _apply_unmanaged.')

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('portlet_type', 'employee_id.name')
    def _compute_name(self):
        labels = dict(self._fields['portlet_type'].selection)
        for rec in self:
            rec.name = '%s - %s' % (
                labels.get(rec.portlet_type, rec.portlet_type or '?'),
                rec.employee_id.name or '?',
            )

    @api.depends('portlet_type')
    def _compute_config_id(self):
        Config = self.env['hrm.hk.ess.portlet.config'].sudo()
        for rec in self:
            rec.config_id = Config.search([('portlet_type', '=', rec.portlet_type)], limit=1)

    # --- approver eligibility ------------------------------------------------
    def _eligible_approver_groups(self):
        """Groups allowed to decide this request. Falls back to Group A if a
        portlet has no (or a deleted) config row, per the customer's rule that
        an unassigned portlet routes to Group A by default."""
        self.ensure_one()
        groups = self.config_id.approver_group_ids
        if not groups:
            groups = self.env.ref(
                'hrm_hk.group_hrm_hk_ess_approver_a', raise_if_not_found=False)
        return groups

    def can_be_decided_by(self, user):
        self.ensure_one()
        groups = self._eligible_approver_groups()
        return bool(groups and (groups & user.sudo().groups_id))

    # --- ORM-layer guard (defence in depth) ---------------------------------
    #
    # An earlier version of this guard gated on a context flag
    # (`self.env.context.get('hrm_hk_ess_internal_write')`) and took the
    # approving identity from a second context key. That was bypassable:
    # `context` is a plain dict the caller supplies as the last argument of
    # `execute_kw`, so any holder of `perm_write` (e.g. a Group-B-only
    # `hr.group_hr_user`) could call `write({'state': 'approved'})` with a
    # context claiming to be internal and naming an arbitrary Group-A user as
    # the approver — both checks would pass because they trusted caller-
    # supplied data instead of the authenticated session.
    #
    # This version keys off two properties the caller cannot set via any RPC
    # argument:
    #   * `self.env.su` — true only when Python code on the server explicitly
    #     called `.sudo()`. There is no `execute_kw` parameter for it; it is
    #     not part of `context`. Our own action_* methods below are the only
    #     code in this module that calls `.sudo()` on `_SYSTEM_MANAGED`
    #     fields, so a raw `write({'state': ...})` from anywhere else — RPC,
    #     another module, the web client — always fails this check.
    #   * `self.env.user` — derived from `self.env.uid`, the uid the request
    #     actually authenticated as. `.sudo()` bypasses access checks but
    #     never changes `uid` (see Odoo's `BaseModel.sudo()` docstring), so
    #     `self.env.user` inside a sudo'd write is still the genuine acting
    #     user, not an elevated or forged one. The controller MUST reach
    #     action_approve/action_reject via `rec.with_user(real_user)...` (see
    #     controllers/api_v1.py) so that uid is the real approving employee's
    #     own res.users, not the admin/superuser identity `hrm_auth` runs the
    #     rest of the request under.
    def write(self, vals):
        """Enforce, at the ORM layer, what the UI only enforces cosmetically:

        1. state / approver_id / decision_date / applied can only change from
           a write made in superuser mode (`self.env.su`) — i.e. only from
           inside action_submit / action_approve / action_reject / the
           _apply* write-through they call, which explicitly `.sudo()` this
           write. A plain `write()` with any of those keys always raises,
           whatever the caller's `perm_write` ACL says.
        2. Any write that moves `state` into 'approved' or 'rejected' is
           re-checked against can_be_decided_by(self.env.user) here too — the
           environment's real authenticated uid, not anything the caller can
           pass in — so a Group-B-only approver cannot approve a
           Group-A-only portlet even if some other code path reached this
           write() in superuser mode.
        3. employee_id / portlet_type / proposed_values are immutable once a
           request has left 'draft', for every caller, superuser or not —
           there is no legitimate reason to change what is being requested
           after it has been submitted (and possibly already reviewed).

        This guard is deliberately not backed by narrowing hr.group_hr_user's
        perm_write on ir.model.access.csv: HR/approvers still need direct
        write access for legitimate non-system fields (decision_reason
        annotations, attachment_ids, and HR creating/submitting a draft on an
        employee's behalf from the backend). perm_write is not part of this
        guard's trust boundary any more — the checks above hold regardless of
        it — so narrowing it would only remove those legitimate uses without
        closing any additional gap.
        """
        if set(vals) & set(self._SYSTEM_MANAGED):
            if not self.env.su:
                raise UserError(_(
                    'The status and decision of a change request can only be changed '
                    'via Submit, Approve or Reject.'))
            if vals.get('state') in ('approved', 'rejected'):
                for rec in self:
                    if not rec.can_be_decided_by(self.env.user):
                        raise AccessError(_(
                            'You are not in an approver group for the "%s" portlet.',
                            dict(rec._fields['portlet_type'].selection).get(rec.portlet_type)))
        if set(vals) & set(self._LOCKED_AFTER_DRAFT):
            for rec in self:
                if rec.state != 'draft':
                    raise UserError(_(
                        'This request can no longer be edited once it has left draft; '
                        'reject it and have the employee submit a new one instead.'))
        return super().write(vals)

    # --- state machine ---------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only a draft request can be submitted.'))
            if not rec.proposed_values:
                raise UserError(_('There is nothing to submit — no proposed values.'))
            if rec.attachment_required and not rec.attachment_ids:
                raise UserError(_(
                    'A supporting attachment is required for this portlet before it '
                    'can be submitted.'))
            rec._validate_proposed_values()
            rec.sudo().write({
                'current_values': rec._snapshot_current_values(),
                'state': 'submitted',
            })
            rec._notify_approvers()
        return True

    def action_approve(self, decision_reason=None):
        """The deciding identity is always `self.env.user` — the real
        authenticated uid this recordset's environment carries, never a
        caller-supplied value (there is no `user=` parameter any more; a
        forgeable one is exactly how the previous version of this guard was
        bypassed). Under hrm_auth, env.user defaults to an elevated
        admin/superuser identity, so the controller MUST call this via
        `rec.with_user(real_approving_user).action_approve(...)` — see
        controllers/api_v1.py — to re-scope env.user to the genuine approver
        before this method (and the write() guard it triggers) ever runs."""
        user = self.env.user
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only a submitted request can be approved.'))
            if not rec.can_be_decided_by(user):
                raise AccessError(_(
                    'You are not in an approver group for the "%s" portlet.',
                    dict(rec._fields['portlet_type'].selection).get(rec.portlet_type)))
            rec._apply()
            rec.sudo().write({
                'state': 'approved',
                'approver_id': user.id,
                'decision_date': fields.Datetime.now(),
                'decision_reason': decision_reason,
            })
            self.env['hrm.hk.notification'].sudo()._notify(
                rec.employee_id, 'info',
                name=_('Your change request was approved'),
                body=rec.name,
                about=rec.employee_id, res_model='hrm.hk.ess.request', res_id=rec.id)
        return True

    def action_reject(self, decision_reason=None):
        """See action_approve for why the deciding identity is always
        self.env.user, and why the controller must call this via with_user()."""
        user = self.env.user
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only a submitted request can be rejected.'))
            if not rec.can_be_decided_by(user):
                raise AccessError(_(
                    'You are not in an approver group for the "%s" portlet.',
                    dict(rec._fields['portlet_type'].selection).get(rec.portlet_type)))
            rec.sudo().write({
                'state': 'rejected',
                'approver_id': user.id,
                'decision_date': fields.Datetime.now(),
                'decision_reason': decision_reason,
            })
            self.env['hrm.hk.notification'].sudo()._notify(
                rec.employee_id, 'info',
                name=_('Your change request was rejected'),
                body=decision_reason or rec.name,
                about=rec.employee_id, res_model='hrm.hk.ess.request', res_id=rec.id)
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(r.state not in ('draft',) for r in self):
            raise UserError(_('Only a draft request can be deleted; reject it instead.'))

    # --- notifications -----------------------------------------------------
    def _notify_approvers(self):
        self.ensure_one()
        groups = self._eligible_approver_groups()
        if not groups:
            _logger.warning(
                'hrm_hk: ESS request %s has no eligible approver group configured', self.id)
            return
        approvers = self.env['hr.employee'].sudo().search(
            [('user_id.groups_id', 'in', groups.ids)])
        for approver in approvers:
            self.env['hrm.hk.notification'].sudo()._notify(
                approver, 'info',
                name=_('%s submitted a change request', self.employee_id.name),
                body=self.name,
                about=self.employee_id, res_model='hrm.hk.ess.request', res_id=self.id)

    # --- snapshot / write-through -------------------------------------------
    def _snapshot_current_values(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        if self.portlet_type == 'personal_info':
            return {'marital': emp.marital}
        if self.portlet_type == 'address':
            return {
                'street': emp.private_street,
                'street2': emp.private_street2,
                'city': emp.private_city,
                'zip': emp.private_zip,
                'state_id': emp.private_state_id.id or None,
                'country_id': emp.private_country_id.id or None,
            }
        if self.portlet_type == 'bank_account':
            primary = emp.primary_bank_account_id
            return {
                'acc_number': primary.acc_number,
                'bank_name': primary.bank_id.name,
            } if primary else {}
        if self.portlet_type == 'work_permit':
            return {
                'hk_visa_type': emp.hk_visa_type,
                'hk_visa_number': emp.hk_visa_number,
                'hk_visa_expiry': str(emp.hk_visa_expiry) if emp.hk_visa_expiry else None,
            }
        if self.portlet_type == 'education':
            return {
                'certificate': emp.certificate,
                'study_field': emp.study_field,
                'study_school': emp.study_school,
            }
        # dependent: no backing model, nothing to snapshot.
        # qualification / experience: these add a *new* hr.resume.line rather
        # than update an existing scalar field, so there is no single "current
        # value" to diff against — nothing to snapshot either.
        return {}

    def _validate_proposed_values(self):
        """Catch, at submit time, proposed values that would blow up the
        write-through with a raw ORM error at approve time — after HR has
        already clicked approve. Currently only 'qualification' and
        'experience' create a new hr.resume.line, whose `name` and
        `date_start` fields are both required.

        `date_start` is required here rather than defaulted: hr.resume.line
        would happily default an omitted date_start to *today* at create
        time, but that is wrong data for these two portlets specifically — a
        qualification obtained in 2015 or a job that started years ago must
        not be silently recorded as starting on the approval date. It also
        closes a DB-constraint gap: hr.resume.line has
        `CHECK (date_start <= date_end OR date_end IS NULL)`; if date_start
        were left to default to today while an employee-supplied date_end
        (e.g. a certificate's earlier issue date) came through, the default
        could easily land *after* date_end and trip that constraint at
        approve time — which Odoo has no friendly message mapping for. The
        employee-app screen for these two portlets doesn't exist yet, so
        requiring the field now costs nothing and avoids designing a UI
        around a default we don't actually want."""
        self.ensure_one()
        if self.portlet_type in ('qualification', 'experience'):
            label = dict(self._fields['portlet_type'].selection).get(self.portlet_type)
            name = (self.proposed_values or {}).get('name')
            if not (isinstance(name, str) and name.strip()):
                raise UserError(_(
                    'A "name" is required in the proposed values for the "%s" portlet.', label))
            date_start = (self.proposed_values or {}).get('date_start')
            if not date_start:
                raise UserError(_(
                    'A "date_start" is required in the proposed values for the "%s" portlet.',
                    label))
            date_end = (self.proposed_values or {}).get('date_end')
            if date_end:
                try:
                    is_reversed = fields.Date.from_string(date_end) < fields.Date.from_string(date_start)
                except ValueError:
                    raise UserError(_('The proposed date_start / date_end could not be parsed.'))
                if is_reversed:
                    raise UserError(_('The end date cannot be before the start date.'))

    def _apply(self):
        self.ensure_one()
        method_name = self._APPLY_METHODS.get(self.portlet_type)
        if method_name:
            getattr(self, method_name)()
            self.sudo().write({'applied': True})
        else:
            self._apply_unmanaged()
            self.sudo().write({'applied': False})

    def _apply_personal_info(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        vals = {k: v for k, v in self.proposed_values.items() if k in ('marital',)}
        if vals:
            emp.write(vals)

    def _apply_address(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        field_map = {
            'street': 'private_street', 'street2': 'private_street2',
            'city': 'private_city', 'zip': 'private_zip',
            'state_id': 'private_state_id', 'country_id': 'private_country_id',
        }
        vals = {
            field_map[k]: v for k, v in self.proposed_values.items() if k in field_map
        }
        if vals:
            emp.write(vals)

    def _apply_bank_account(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        partner = emp.work_contact_id
        if not partner:
            raise UserError(_('Employee has no linked work contact to attach a bank account to.'))
        acc_number = self.proposed_values.get('acc_number')
        if not acc_number:
            raise UserError(_('Proposed bank account is missing an account number.'))
        vals = {'acc_number': acc_number, 'partner_id': partner.id}
        bank_name = self.proposed_values.get('bank_name')
        if bank_name:
            Bank = self.env['res.bank'].sudo()
            bank = Bank.search([('name', '=', bank_name)], limit=1) or Bank.create(
                {'name': bank_name})
            vals['bank_id'] = bank.id
        Account = self.env['res.partner.bank'].sudo()
        record = Account.search(
            [('partner_id', '=', partner.id), ('acc_number', '=', acc_number)], limit=1)
        if record:
            record.write(vals)
        else:
            record = Account.create(vals)
        emp.write({'bank_account_ids': [(4, record.id)]})

    def _apply_work_permit(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        vals = {
            k: v for k, v in self.proposed_values.items()
            if k in ('hk_visa_type', 'hk_visa_number', 'hk_visa_expiry')
        }
        if vals:
            emp.write(vals)

    def _apply_education(self):
        self.ensure_one()
        emp = self.employee_id.sudo()
        vals = {
            k: v for k, v in self.proposed_values.items()
            if k in ('certificate', 'study_field', 'study_school')
        }
        if vals:
            emp.write(vals)

    def _apply_qualification(self):
        """Qualification portlet -> new hr.resume.line under the seeded
        'Education' line type."""
        self.ensure_one()
        line_type = self.env.ref('hr_skills.resume_type_education', raise_if_not_found=False)
        self._apply_resume_line(line_type)

    def _apply_experience(self):
        """Experience portlet -> new hr.resume.line under the seeded 'Other
        Experience' line type."""
        self.ensure_one()
        line_type = self.env.ref('hr_skills.resume_type_experience', raise_if_not_found=False)
        self._apply_resume_line(line_type)

    def _apply_resume_line(self, line_type):
        """Shared write-through for the two hr.resume.line-backed portlets.

        `name` and `date_start` are both required on hr.resume.line and are
        validated at submit time (see _validate_proposed_values) so this
        never raises a raw ORM error — or trips hr.resume.line's
        `date_start <= date_end` DB constraint — at approve time. Both are
        re-checked here defensively in case a record somehow reached
        'submitted' without going through action_submit (e.g. a fixture or a
        future code path). Neither is defaulted on this end: hr.resume.line
        would happily default an omitted date_start to today, but that would
        silently record an employee's actual qualification/employment start
        date as "the day HR approved the request", which is simply wrong
        data — see _validate_proposed_values for the full reasoning.

        Note (v18 port): hr.resume.line in this Odoo version only has
        employee_id/name/date_start/date_end/description/line_type_id — the
        v19 fields this originally also wrote (duration, external_url,
        certificate_file, certificate_filename) do not exist here. duration
        and external_url are folded into `description` instead of being
        silently dropped; there is no attachment field to write a
        certificate/diploma image onto, so the original supporting document
        stays only on the source ess.request's attachment_ids (visible to
        HR from there).
        """
        self.ensure_one()
        emp = self.employee_id.sudo()
        name = (self.proposed_values.get('name') or '').strip()
        if not name:
            raise UserError(_('Proposed values are missing a required "name".'))
        date_start = self.proposed_values.get('date_start')
        if not date_start:
            raise UserError(_('Proposed values are missing a required "date_start".'))
        vals = {
            'employee_id': emp.id,
            'name': name,
            'date_start': date_start,
            'line_type_id': line_type.id if line_type else False,
        }
        date_end = self.proposed_values.get('date_end')
        if date_end:
            vals['date_end'] = date_end
        extra_lines = []
        duration = self.proposed_values.get('duration')
        if duration:
            extra_lines.append(_('Duration: %s', duration))
        external_url = self.proposed_values.get('external_url')
        if external_url:
            extra_lines.append(_('URL: %s', external_url))
        description = self.proposed_values.get('description')
        if description:
            extra_lines.append(description)
        if extra_lines:
            vals['description'] = '<br/>'.join(extra_lines)
        self.env['hr.resume.line'].sudo().create(vals)

    def _apply_unmanaged(self):
        """dependent only: no dependants/dependent model exists anywhere in
        Odoo (core or hr_skills) to write through to. Designing one is
        explicitly deferred to work package H1 (the insurance module):
        requirement R011 needs dependants carrying differing insurance
        premiums, and that shape belongs with the module that will actually
        consume it, not bolted onto hrm_hk speculatively here. Approving
        still records the decision, but the change is logged for HR to apply
        by hand instead of silently pretending it was written through — see
        the module docstring on _APPLY_METHODS."""
        self.ensure_one()
        user = self.env.user
        emp = self.employee_id.sudo()
        label = dict(self._fields['portlet_type'].selection).get(self.portlet_type)
        emp.message_post(body=_(
            'ESS change request #%(id)s (%(portlet)s) was approved but has no automatic '
            'write-through target yet — apply the change to the employee record manually. '
            'Proposed values: %(values)s',
            id=self.id, portlet=label, values=json.dumps(self.proposed_values),
        ))
        emp.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Apply approved ESS change: %s', label),
            note=json.dumps(self.proposed_values),
            user_id=user.id,
        )

    # --- REST payload --------------------------------------------------------
    def _ess_payload(self):
        return [{
            'id': r.id,
            'portlet_type': r.portlet_type,
            'state': r.state,
            'proposed_values': r.proposed_values,
            'reason': r.reason or None,
            'decision_reason': r.decision_reason or None,
            'decision_date': str(r.decision_date) if r.decision_date else None,
            'has_attachment': bool(r.attachment_ids),
            'created': str(r.create_date),
            # 'applied' is only meaningful once state == 'approved': True means
            # the proposed values were written through to the real record;
            # False means the portlet has no automatic write-through target
            # yet and HR still needs to apply the change by hand (see
            # _apply_unmanaged in hrm_hk_ess_request.py). Do not read this as
            # "rejected" or "still pending" — check `state` for that.
            'applied': r.applied,
            'pending_manual': r.state == 'approved' and not r.applied,
        } for r in self]

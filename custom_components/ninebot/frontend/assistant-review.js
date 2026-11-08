/** Local human-review state. Confirmation credentials never leave memory and HA's WS. */
export const ACTIVE = new Set(['approved', 'queued', 'running', 'waiting_budget', 'retry_wait', 'paused']);
export const TERMINAL = new Set(['completed', 'partial', 'cancelled', 'failed', 'expired']);

export class ReviewSession {
  constructor(hass, changed = () => {}) {
    this.hass = hass;
    this.changed = changed;
    this.epoch = 0;
    this.vehicles = [];
    this.tasks = [];
    this.vehicleRef = '';
    this.prepared = null;
    this.error = null;
    this.busy = false;
    this.more = false;
  }
  stop() {
    this.epoch += 1;
    this.prepared = null;
    this.vehicles = [];
    this.tasks = [];
    this.vehicleRef = '';
    this.busy = false;
  }
  async run(call, apply) {
    if (this.busy) return false;
    const epoch = this.epoch;
    this.busy = true;
    this.error = null;
    this.changed();
    try {
      const result = await call();
      if (epoch !== this.epoch) return false;
      apply(result);
      return true;
    } catch (error) {
      if (epoch === this.epoch) {
        this.error = typeof error?.code === 'string' ? error.code : 'retry_later';
        this.prepared = null;
        if (this.error === 'not_authorized') {
          this.tasks = []; this.vehicles = []; this.vehicleRef = ''; this.more = false;
        }
      }
      return false;
    } finally {
      if (epoch === this.epoch) {
        this.busy = false;
        this.changed();
      }
    }
  }
  async load() {
    const ok = await this.run(
      () => this.hass.callWS({ type: 'ninebot/assistant/vehicles' }),
      (result) => {
        if (!Array.isArray(result?.data?.vehicles)) throw new Error('Invalid vehicle response');
        this.vehicles = result.data.vehicles;
        this.more = result.data.more_available === true;
      },
    );
    if (ok && this.vehicles.length === 1) await this.select(this.vehicles[0].vehicle_ref);
  }
  async select(vehicleRef) {
    if (this.busy || (vehicleRef && !this.vehicles.some((v) => v.vehicle_ref === vehicleRef))) return false;
    this.epoch += 1;
    this.vehicleRef = vehicleRef;
    this.tasks = [];
    this.prepared = null;
    if (!vehicleRef) { this.changed(); return true; }
    return this.refresh();
  }
  message(operation, values = {}) {
    return { type: 'ninebot/assistant/review', vehicle_ref: this.vehicleRef, operation, ...values };
  }
  async refresh() {
    if (!this.vehicleRef) return false;
    return this.run(() => this.hass.callWS(this.message('list')), (result) => {
      if (!Array.isArray(result?.tasks)) throw new Error('Invalid task response');
      this.tasks = result.tasks;
      if (this.prepared && !this.tasks.some((t) => t.task_id === this.prepared.task.task_id
        && t.state === 'awaiting_approval' && t.plan_hash === this.prepared.task.plan_hash)) this.prepared = null;
    });
  }
  async prepare(taskId) {
    this.prepared = null;
    return this.run(() => this.hass.callWS(this.message('prepare', { task_id: taskId })), (result) => {
      if (result?.task?.task_id !== taskId || result.task.vehicle_ref !== this.vehicleRef
        || !/^[a-f0-9]{64}$/.test(result.task.plan_hash)
        || !Number.isFinite(Date.parse(result.task.approval_expires_at))
        || typeof result.token !== 'string' || result.token.length < 16 || result.token.length > 128) {
        throw new Error('Invalid confirmation response');
      }
      this.prepared = { task: result.task, token: result.token };
    });
  }
  update(task) {
    this.tasks = this.tasks.map((t) => t.task_id === task.task_id ? task : t);
  }
  async approve() {
    const prepared = this.prepared;
    if (this.busy || !prepared || prepared.task.vehicle_ref !== this.vehicleRef) return false;
    this.prepared = null;
    if (Date.parse(prepared.task.approval_expires_at) <= Date.now()) {
      this.error = 'approval_expired';
      this.changed();
      return false;
    }
    return this.run(() => this.hass.callWS(this.message('approve', {
      task_id: prepared.task.task_id, token: prepared.token, plan_hash: prepared.task.plan_hash,
    })), (result) => this.update(result.task));
  }
  async cancel(taskId) {
    if (this.prepared?.task.task_id === taskId) this.prepared = null;
    return this.run(() => this.hass.callWS(this.message('cancel', { task_id: taskId })), (result) => this.update(result.task));
  }
  get refreshSeconds() {
    if (this.busy || this.prepared) return 0;
    if (this.tasks.some((task) => ACTIVE.has(task.state))) return 10;
    return this.tasks.some((task) => task.state === 'awaiting_approval') ? 30 : 0;
  }
}

export const TEXT = {
  en: {
    title: 'Ninebot Assistant', subtitle: 'History task review · Beta', vehicle: 'Vehicle', choose: 'Select a vehicle',
    refresh: 'Refresh', device: 'Open device', empty: 'No tasks for this vehicle.', select: 'Select a vehicle to review your tasks.',
    review: 'Review', approve: 'Approve and synchronize', cancel: 'Cancel task', dismiss: 'Back',
    confirm: 'Review the scope', notice: 'Approval allows cloud requests for these saved trip identities. Retries count toward the request limit. Existing saved details are reused.',
    dates: 'Dates', zone: 'Time zone', selected: 'Selected details', budget: 'Maximum requests', expires: 'Approval expires',
    saved: 'Saved details', attempts: 'Requests used', checkpoint: 'Last checkpoint', retry: 'Next eligible attempt', unknown: 'Not observed',
    more: 'More vehicles are authorized. Narrow the Assistant vehicle selection in account options.',
    records: 'Selected records', failed: 'Review is unavailable. Check access or try again shortly.',
    states: { awaiting_approval: 'Awaiting your review', approved: 'Approved', queued: 'Queued', running: 'Synchronizing', waiting_budget: 'Waiting for request budget', retry_wait: 'Waiting to retry', paused: 'Paused', completed: 'Completed', partial: 'Partially completed', cancelled: 'Cancelled', failed: 'Failed', expired: 'Expired' },
    errors: { not_authorized: 'Access denied. Check your account and Assistant vehicle selection.', approval_expired: 'This confirmation expired. Request a new plan.', approval_used: 'This task is no longer awaiting approval.', retry_later: 'Temporarily unavailable. Check the task state before retrying.', task_not_found: 'This task is no longer available.' },
    reasons: { budget_exhausted: 'The approved request limit was reached.', deadline_expired: 'The task execution window expired.', scope_changed: 'The saved trip mapping changed. Request a new plan.', not_authorized: 'Task access was revoked.', auth_required: 'The account needs to sign in again.', unavailable: 'The detail could not be verified.', budget_unavailable: 'The approved request budget could not be verified.' },
  },
  zh: {
    title: 'Ninebot Assistant', subtitle: '历史任务审核 · Beta', vehicle: '车辆', choose: '请选择车辆',
    refresh: '刷新', device: '查看设备', empty: '这辆车暂无任务。', select: '请选择车辆，查看你申请的任务。',
    review: '审核', approve: '批准并同步', cancel: '取消任务', dismiss: '返回',
    confirm: '确认任务范围', notice: '批准后将为这些已保存的行程标识请求云端详情。重试计入请求上限，已保存的详情会复用。',
    dates: '日期', zone: '时区', selected: '选中详情数', budget: '请求次数上限', expires: '审批截止时间',
    saved: '已保存详情', attempts: '已使用请求', checkpoint: '最近进度记录', retry: '下次允许尝试时间', unknown: '尚未观察',
    more: '还有更多已授权车辆，请在账号选项中缩小 Assistant 车辆范围。', records: '选中记录',
    failed: '暂时无法审核，请检查访问权限或稍后重试。',
    states: { awaiting_approval: '等待你审核', approved: '已批准', queued: '已排队', running: '正在同步', waiting_budget: '等待请求额度', retry_wait: '等待重试', paused: '已暂停', completed: '已完成', partial: '部分完成', cancelled: '已取消', failed: '未完成', expired: '已过期' },
    errors: { not_authorized: '访问被拒绝，请检查账号与 Assistant 车辆范围。', approval_expired: '本次确认已过期，请重新申请计划。', approval_used: '此任务已不再等待审批。', retry_later: '暂时不可用，请先查看任务状态再重试。', task_not_found: '此任务已不再可用。' },
    reasons: { budget_exhausted: '已达到批准的请求上限。', deadline_expired: '任务执行期限已过。', scope_changed: '本地行程映射已变化，请重新申请计划。', not_authorized: '任务访问权限已撤销。', auth_required: '账号需要重新登录。', unavailable: '无法核验行程详情。', budget_unavailable: '无法核验批准的请求额度。' },
  },
};

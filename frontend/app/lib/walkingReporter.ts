import { ApiError, fetchPatientWalking, publishWalkingEvent } from "./api";
import type { WalkError, WalkingEvent, WalkingView, WalkEventName } from "./integration";

export interface WalkingReport {
  view: WalkingView | null;
  warning: string | null;
  changed: boolean;
  stopping: boolean;
}

export class WalkingReporter {
  private controller = new AbortController();
  private queue: WalkingEvent[] = [];
  private sending = false;
  private sequence = 0;
  private accepted = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private report: WalkingReport = { view: null, warning: null, changed: false, stopping: false };

  constructor(
    private patientId: string,
    private token: string,
    private notify: (report: WalkingReport) => void,
    private invalid: () => void,
  ) {}

  get view() { return this.report.view; }
  get blocked() {
    return this.report.changed || this.report.stopping || this.controller.signal.aborted ||
      (this.view !== null && (this.view.survey_status !== "stored" || ["saved", "stopped"].includes(this.view.status)));
  }

  private update(patch: Partial<WalkingReport>) {
    if (this.controller.signal.aborted) return;
    this.report = { ...this.report, ...patch };
    this.notify(this.report);
  }

  private acceptView(walking: WalkingView) {
    if (this.view && (walking.version < this.view.version ||
      (["saved", "stopped"].includes(this.view.status) && walking.status !== this.view.status))) return;
    this.update({ view: walking, warning: this.queue.length && !["saved", "stopped"].includes(walking.status) ? this.report.warning : null });
  }

  conflict() {
    this.queue = [];
    this.update({ changed: true, warning: "This assessment no longer accepts updates. Load the current assessment or contact your care team." });
  }

  private authError(error: unknown) {
    if (error instanceof ApiError && [401, 403].includes(error.status)) {
      this.invalid();
      this.dispose();
      return true;
    }
    return false;
  }

  async load() {
    try {
      const { walking } = await fetchPatientWalking(this.patientId, this.token, this.controller.signal);
      if (this.controller.signal.aborted) return;
      this.sequence = walking?.last_sequence ?? 0;
      this.update({ view: walking, warning: null });
      this.emit("page_ready");
    } catch (error) {
      if (this.controller.signal.aborted || this.authError(error)) return;
      this.update({ warning: "Walking status is unavailable. You can still analyze a walk; saving must be confirmed by the server." });
    }
    if (!this.controller.signal.aborted) this.schedule();
  }

  private schedule() {
    this.timer = setTimeout(async () => {
      await this.verifyScope();
      if (!this.controller.signal.aborted && !this.report.changed) this.schedule();
    }, 5000);
  }

  async verifyScope(): Promise<boolean> {
    if (this.controller.signal.aborted || this.report.changed) return false;
    try {
      const { walking } = await fetchPatientWalking(this.patientId, this.token, this.controller.signal);
      if (this.controller.signal.aborted) return false;
      if (walking?.call_id !== this.view?.call_id || walking?.attempt_id !== this.view?.attempt_id) {
        this.queue = [];
        this.update({ changed: true, warning: "Your care team updated this assessment. Load the current assessment before recording another walk." });
        return false;
      }
      if (walking && (!this.view || walking.version >= this.view.version)) {
        this.sequence = Math.max(this.sequence, walking.last_sequence);
        this.acceptView(walking);
      }
      return true;
    } catch (error) {
      if (this.controller.signal.aborted || this.authError(error)) return false;
      this.update({ warning: "Walking status could not be checked. Retry when your connection is available." });
      return this.view === null;
    }
  }

  emit(event: WalkEventName, error_code?: WalkError) {
    const view = this.view;
    if (!view || this.blocked || event !== "stopped" && this.accepted + this.queue.length >= 180) return;
    const previous = this.queue.at(-1);
    if (previous?.event === event && previous.error_code === error_code) return;
    this.queue.push({
      event_id: crypto.randomUUID(),
      call_id: view.call_id,
      attempt_id: view.attempt_id,
      sequence: ++this.sequence,
      event,
      ...(error_code ? { error_code } : {}),
    });
    void this.flush();
  }

  async flush() {
    if (this.sending || this.controller.signal.aborted || this.report.changed) return;
    this.sending = true;
    try {
      while (this.queue.length && !this.controller.signal.aborted && !this.report.changed) {
        const body = this.queue[0];
        const { walking } = await publishWalkingEvent(this.patientId, this.token, body, this.controller.signal);
        if (this.controller.signal.aborted || this.report.changed) return;
        if (walking.call_id !== body.call_id || walking.attempt_id !== body.attempt_id) {
          this.update({ changed: true, warning: "Assessment changed. Load the current assessment." });
          return;
        }
        this.queue.shift();
        this.accepted += 1;
        this.sequence = Math.max(this.sequence, walking.last_sequence);
        this.acceptView(walking);
        if (["saved", "stopped"].includes(walking.status)) this.queue = [];
      }
    } catch (error) {
      if (this.controller.signal.aborted || this.authError(error)) return;
      if (error instanceof ApiError && [404, 409].includes(error.status)) {
        this.conflict();
      } else {
        this.update({ warning: "Walking progress could not be sent. Retry progress sync when connected; your walk is not marked saved." });
      }
    } finally {
      this.sending = false;
    }
  }

  async stop() {
    if (!await this.verifyScope() || this.blocked) return;
    this.emit("stopped");
    this.update({ stopping: true });
    await this.flush();
  }

  saved(sessionId: string) {
    if (!this.view || this.controller.signal.aborted || this.report.changed) return;
    this.queue = [];
    this.update({ view: { ...this.view, status: "saved", session_id: sessionId }, warning: null });
  }

  dispose() {
    this.controller.abort();
    clearTimeout(this.timer);
    this.queue = [];
    this.token = "";
  }
}

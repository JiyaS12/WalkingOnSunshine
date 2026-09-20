import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchPatientWalking, publishWalkingEvent } from "./api";
import { deferred, walkingView } from "./integration.test-support";
import { WalkingReporter, type WalkingReport } from "./walkingReporter";

vi.mock("./api", async () => ({
  ...await vi.importActual<typeof import("./api")>("./api"),
  fetchPatientWalking: vi.fn(), publishWalkingEvent: vi.fn(),
}));

let reporter: WalkingReporter;
const invalid = vi.fn();
beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView() });
  vi.mocked(publishWalkingEvent).mockImplementation(async (_pid, _token, event) => ({
    walking: walkingView({ version: 4 + event.sequence, last_sequence: event.sequence, last_event: event.event, status: "page_ready" }),
  }));
  reporter = new WalkingReporter("patient-1", "signed.payload", vi.fn(), invalid);
});
afterEach(() => { reporter.dispose(); vi.useRealTimers(); });

describe("patient walking event contract", () => {
  it("reports page open separately from camera readiness and sends ordered, correlated events", async () => {
    await reporter.load();
    await reporter.flush();
    expect(publishWalkingEvent).toHaveBeenCalledTimes(1);
    expect(vi.mocked(publishWalkingEvent).mock.calls[0][2].event).toBe("page_ready");
    reporter.emit("calibration_started");
    reporter.emit("calibration_completed");
    reporter.emit("capture_started");
    reporter.emit("capture_completed");
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.mocked(publishWalkingEvent).mock.calls.map((args) => args[2].sequence)).toEqual([1, 2, 3, 4, 5]);
    for (const [pid, token, event] of vi.mocked(publishWalkingEvent).mock.calls) {
      expect([pid, token]).toEqual(["patient-1", "signed.payload"]);
      expect(event).toMatchObject({ call_id: "call-1", attempt_id: "attempt-1", event_id: expect.stringMatching(/^[\w-]{16,}$/) });
    }
    expect(reporter.view?.status).not.toBe("saved");
  });

  it("retries an ambiguous event with identical ID, sequence and payload", async () => {
    vi.mocked(publishWalkingEvent).mockRejectedValueOnce(new TypeError("lost response"));
    await reporter.load();
    await vi.advanceTimersByTimeAsync(0);
    await reporter.flush();
    expect(vi.mocked(publishWalkingEvent).mock.calls[1][2]).toEqual(vi.mocked(publishWalkingEvent).mock.calls[0][2]);
  });

  it("keeps a failed-event retry visible after a successful status poll", async () => {
    const notify = vi.fn<(report: WalkingReport) => void>();
    reporter.dispose();
    reporter = new WalkingReporter("patient-1", "signed.payload", notify, invalid);
    vi.mocked(publishWalkingEvent).mockRejectedValue(new TypeError("offline"));
    await reporter.load();
    await vi.advanceTimersByTimeAsync(5000);
    expect(notify.mock.calls.at(-1)?.[0].warning).toMatch(/progress could not be sent/);
  });

  it("prevents new capture while an explicit stop is awaiting server confirmation", async () => {
    await reporter.load();
    await vi.advanceTimersByTimeAsync(0);
    const pending = deferred<Awaited<ReturnType<typeof publishWalkingEvent>>>();
    vi.mocked(publishWalkingEvent).mockReturnValue(pending.promise);
    await reporter.stop();
    expect(reporter.blocked).toBe(true);
    reporter.emit("capture_started");
    expect(vi.mocked(publishWalkingEvent).mock.calls.at(-1)?.[2].event).toBe("stopped");
    expect(reporter.view?.status).not.toBe("saved");
    pending.resolve({ walking: walkingView({ status: "stopped", version: 10 }) });
    await vi.advanceTimersByTimeAsync(0);
    expect(reporter.view?.status).toBe("stopped");
  });

  it("blocks a superseded attempt and never moves old capture events onto it", async () => {
    await reporter.load();
    await vi.advanceTimersByTimeAsync(0);
    vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView({ attempt_id: "new-attempt" }) });
    expect(await reporter.verifyScope()).toBe(false);
    reporter.emit("capture_completed");
    expect(reporter.blocked).toBe(true);
    expect(publishWalkingEvent).toHaveBeenCalledTimes(1);
  });

  it("does not regress a server-confirmed save when an older event response arrives", async () => {
    const pending = deferred<Awaited<ReturnType<typeof publishWalkingEvent>>>();
    vi.mocked(publishWalkingEvent).mockReturnValueOnce(pending.promise);
    await reporter.load();
    reporter.saved("session-1");
    pending.resolve({ walking: walkingView({ version: 9, status: "capturing" }) });
    await vi.advanceTimersByTimeAsync(0);
    expect(reporter.view).toMatchObject({ status: "saved", session_id: "session-1" });
  });

  it("keeps legacy walking usable when the status service is unavailable", async () => {
    vi.mocked(fetchPatientWalking).mockRejectedValue(new ApiError("Unavailable", 503));
    await reporter.load();
    expect(reporter.blocked).toBe(false);
    expect(await reporter.verifyScope()).toBe(true);
    reporter.emit("capture_completed");
    expect(publishWalkingEvent).not.toHaveBeenCalled();
  });

  it("expires access on 401 and cancels all future events/polls", async () => {
    vi.mocked(fetchPatientWalking).mockRejectedValue(new ApiError("Expired", 401));
    await reporter.load();
    expect(invalid).toHaveBeenCalledOnce();
    expect(reporter.blocked).toBe(true);
    await vi.advanceTimersByTimeAsync(15000);
    reporter.emit("capture_started");
    expect(fetchPatientWalking).toHaveBeenCalledTimes(1);
    expect(publishWalkingEvent).not.toHaveBeenCalled();
  });

  it("aborts in-flight reads and ignores their late completion after disposal", async () => {
    const response = deferred<Awaited<ReturnType<typeof fetchPatientWalking>>>();
    vi.mocked(fetchPatientWalking).mockReturnValue(response.promise);
    const load = reporter.load();
    const signal = vi.mocked(fetchPatientWalking).mock.calls[0][2];
    reporter.dispose();
    response.resolve({ walking: walkingView() });
    await load;
    expect(signal?.aborted).toBe(true);
    expect(reporter.view).toBeNull();
    expect(publishWalkingEvent).not.toHaveBeenCalled();
  });
});

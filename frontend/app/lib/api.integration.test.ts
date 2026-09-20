import { afterEach, expect, it, vi } from "vitest";
import { fetchPatientCalls, fetchPatientWalking, publishWalkingEvent, refreshPatientCall, retryPatientSMS, setPatientCondition, startPatientCall } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("uses clinician cookies and JSON mutations without operator/provider credentials", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  await setPatientCondition("patient 1", "stroke");
  await startPatientCall("patient 1", { request_id: "request_0123456789", to_number: "+15555550123", condition_category: "stroke" });
  await fetchPatientCalls("patient 1");
  await refreshPatientCall("patient 1", "call 1");
  await retryPatientSMS("patient 1", "call 1", "retry_01234567890");
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "http://localhost:8000/api/patients/patient%201/condition",
    "http://localhost:8000/api/patients/patient%201/calls",
    "http://localhost:8000/api/patients/patient%201/calls",
    "http://localhost:8000/api/patients/patient%201/calls/call%201/refresh",
    "http://localhost:8000/api/patients/patient%201/calls/call%201/sms-retries",
  ]);
  for (const [, init] of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
    expect(new Headers(init.headers).has("X-Operator-Token")).toBe(false);
    expect(init.cache).toBe("no-store");
  }
  expect(fetchMock.mock.calls.map(([, init]) => init.method ?? "GET")).toEqual(["PATCH", "POST", "GET", "POST", "POST"]);
  expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({ request_id: "retry_01234567890" });
});

it("scopes walking requests to bearer patient access, with abort propagation and no credentials in URLs", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  await fetchPatientWalking("patient 1", "signed.payload", controller.signal);
  const event = { call_id: "call-1", attempt_id: "attempt-1", event_id: "event_01234567890", sequence: 1, event: "page_ready" as const };
  await publishWalkingEvent("patient 1", "signed.payload", event, controller.signal);
  controller.abort();
  for (const [url, init] of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(url).toContain("/api/patient-access/patient%201/walking");
    expect(url).not.toContain("signed.payload");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer signed.payload");
    expect(init).toMatchObject({ credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" });
    expect(init.signal?.aborted).toBe(true);
  }
  expect(fetchMock.mock.calls[1][0]).toContain("/walking/events");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(event);
});
